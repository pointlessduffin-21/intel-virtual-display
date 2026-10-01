"""Intel Virtual Display: native Tkinter window + tray icon. No web view.

One process: Tk on the main thread, pystray on its own thread, and a 1 s ticker thread that runs the
confirmation timeout and the monitor-change keeper (core.tick). Display changes run on short worker threads so
the window never freezes; tray actions are marshalled to the Tk thread through a queue.
"""
import ctypes
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes

import pystray
from PIL import Image, ImageDraw, ImageTk

from . import core
from . import paper as P

APP = "Intel Virtual Display"
VERSION = "2.0.0"
MUTEX_NAME = "Local\\IntelVirtualDisplay.Native"
SHOW_EVENT = "Local\\IntelVirtualDisplay.Native.Show"

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateMutexW.restype = wintypes.HANDLE
_k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
_k32.CreateEventW.restype = wintypes.HANDLE
_k32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_k32.OpenEventW.restype = wintypes.HANDLE
_k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_k32.SetEvent.argtypes = [wintypes.HANDLE]
_k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]


def size_text(w, h):
    return "%d × %d" % (w, h)


def make_icon(s):
    """The app icon in the Paper palette: a dashed vermilion desktop around a solid ink panel."""
    ss = 4
    S = s * ss
    k = S / 64.0
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    lw = max(ss, round(3 * k))
    d.rounded_rectangle((2 * k, 2 * k, 62 * k, 62 * k), radius=14 * k, fill=P._rgba(P.PAPER), outline=P._rgba(P.INK), width=lw)
    d.rounded_rectangle((10 * k, 12 * k, 54 * k, 42 * k), radius=4 * k, outline=P._rgba(P.ACCENT), width=lw)
    d.rounded_rectangle((10 * k, 26 * k, 34 * k, 42 * k), radius=3 * k, fill=P._rgba(P.INK))
    d.line((22 * k, 52 * k, 42 * k, 52 * k), fill=P._rgba(P.INK), width=lw)
    return im.resize((s, s), Image.LANCZOS)


# ---------- Start with Windows: a shortcut in the user's Startup folder ----------
def _startup_dir():
    return os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup")


SHORTCUT = os.path.join(_startup_dir(), APP + ".lnk")
SCRIPT_SHORTCUT = os.path.join(_startup_dir(), "Virtual Resolution.lnk")   # from Install-Startup.ps1


def startup_enabled():
    return os.path.exists(SHORTCUT)


def set_startup(on):
    if not on:
        if os.path.exists(SHORTCUT):
            os.remove(SHORTCUT)
        core.log("Start with Windows: off")
        return
    if getattr(sys, "frozen", False):
        target, args = sys.executable, "--background"
    else:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        target, args = (pyw if os.path.exists(pyw) else sys.executable), '"%s" --background' % os.path.abspath(sys.argv[0])
    env = dict(os.environ, IVD_LNK=SHORTCUT, IVD_TARGET=target, IVD_ARGS=args, IVD_DIR=os.path.dirname(target))
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:IVD_LNK); $s.TargetPath=$env:IVD_TARGET; "
          "$s.Arguments=$env:IVD_ARGS; $s.WorkingDirectory=$env:IVD_DIR; $s.Description='Intel Virtual Display: keeps your desktop resolution'; $s.Save()")
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps], env=env, check=True,
                   creationflags=0x08000000)   # CREATE_NO_WINDOW
    if os.path.exists(SCRIPT_SHORTCUT):
        os.remove(SCRIPT_SHORTCUT)
        core.log("Replaced the PowerShell startup entry with the app's own.")
    core.log("Start with Windows: on")


class App:
    def __init__(self, background):
        self.q = queue.Queue()
        self.busy = False
        self.drafts = {}
        self.sig = None
        self.log_sig = None
        self.dialog = None
        self.toast_after = None
        self.hidden_note_shown = False

        self.root = tk.Tk()
        self.root.withdraw()
        P.setup(self.root)
        self.root.title(APP)
        self.root.configure(bg=P.PAPER)
        self._icon = ImageTk.PhotoImage(make_icon(64))
        self.root.iconphoto(True, self._icon)
        self.root.geometry("%dx%d" % (P.px(1080), P.px(860)))
        self.root.minsize(P.px(640), P.px(520))
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self._build()
        self.refresh()
        if not background:
            self.show()
        self.root.after(100, self._drain)
        self.root.after(1000, self._poll)

    # ---------- layout ----------
    def _build(self):
        self.scroll = P.Scroll(self.root)
        self.scroll.pack(fill="both", expand=True)
        page = tk.Frame(self.scroll.inner, bg=P.PAPER)
        page.pack(fill="both", expand=True, padx=P.px(44), pady=(P.px(30), P.px(36)))
        self.page = page

        top = tk.Frame(page, bg=P.PAPER)
        top.pack(fill="x")
        left = tk.Frame(top, bg=P.PAPER)
        left.pack(side="left", fill="x", expand=True)
        P.eyebrow(left, "Intel virtual display").pack(anchor="w")
        title = tk.Frame(left, bg=P.PAPER)
        title.pack(anchor="w", pady=(P.px(6), 0))
        tk.Label(title, text="Desktop ", font=P.font("display"), fg=P.INK, bg=P.PAPER).pack(side="left")
        tk.Label(title, text="resolution", font=P.font("display_em"), fg=P.INK, bg=P.PAPER).pack(side="left")
        P.Pill(top, "Windows display settings", command=lambda: os.startfile("ms-settings:display"), bg=P.PAPER).pack(side="right", anchor="n", pady=(P.px(18), 0))

        self.lead = P.label(page, "Run a desktop larger than your screen. Windows draws it at full size and scales it down, "
                                  "and the display keeps receiving its native signal.", bg=P.PAPER, fnt="lead", fg=P.INK_2)
        self.lead.pack(fill="x", pady=(P.px(10), P.px(24)))
        page.bind("<Configure>", lambda e: self.lead.configure(wraplength=min(e.width, P.px(760))))

        self.cards = tk.Frame(page, bg=P.PAPER)
        self.cards.pack(fill="x")

        lower = tk.Frame(page, bg=P.PAPER)
        lower.pack(fill="x", pady=(P.px(18), 0))
        lower.columnconfigure(0, weight=1, uniform="lower")
        lower.columnconfigure(1, weight=1, uniform="lower")

        sc = P.Card(lower)
        sc.grid(row=0, column=0, sticky="nsew", padx=(0, P.px(9)))
        P.mono_label(sc.body, "Settings").pack(anchor="w", pady=(0, P.px(14)))
        self.keep = P.Check(sc.body, "Keep my resolution when monitors are plugged in or removed", command=self._set_keep)
        self.keep.pack(anchor="w", fill="x")
        hint1 = P.label(sc.body, "Windows saves display settings separately for each combination of monitors. With this on, the app "
                                 "re-applies your size whenever that combination changes.", fnt="small", fg=P.INK_3, wraplength=P.px(400))
        hint1.pack(anchor="w", padx=(P.px(30), 0), pady=(P.px(6), P.px(16)))
        self.startup = P.Check(sc.body, "Start with Windows (runs in the tray)", command=self._set_startup)
        self.startup.pack(anchor="w", fill="x")
        hint2 = P.label(sc.body, "Applies your resolution at sign-in. Closing this window keeps the app running in the tray.",
                        fnt="small", fg=P.INK_3, wraplength=P.px(400))
        hint2.pack(anchor="w", padx=(P.px(30), 0), pady=(P.px(6), 0))

        def wrap_settings(e):
            for w in (self.keep.label, self.startup.label, hint1, hint2):
                w.configure(wraplength=max(P.px(160), e.width - P.px(34)))
        sc.body.bind("<Configure>", wrap_settings, add="+")

        ac = P.Card(lower)
        ac.grid(row=0, column=1, sticky="nsew", padx=(P.px(9), 0))
        P.mono_label(ac.body, "Activity").pack(anchor="w", pady=(0, P.px(14)))
        self.log = tk.Text(ac.body, height=10, wrap="word", font=P.font("mono"), fg=P.INK_2, bg=P.PAPER_HI, relief="flat",
                           highlightthickness=1, highlightbackground=P.LINE, highlightcolor=P.LINE, padx=P.px(12), pady=P.px(10),
                           cursor="arrow", state="disabled")
        self.log.pack(fill="both", expand=True)

        P.label(page, "Version %s  ·  Settings and log in %%LOCALAPPDATA%%\\intel-virtual-display" % VERSION,
                bg=P.PAPER, fnt="label", fg=P.INK_3).pack(anchor="w", pady=(P.px(24), 0))

        self.toast = tk.Label(self.root, font=P.font("small"), fg=P.PAPER, bg=P.INK, padx=P.px(16), pady=P.px(10))

    def _card(self, d, rem, pend):
        c = P.Card(self.cards, pad=22)
        c.pack(fill="x", pady=(0, P.px(18)))
        b = c.body
        b.columnconfigure(0, weight=10, uniform="dc")
        b.columnconfigure(1, weight=11, uniform="dc")

        head = tk.Frame(b, bg=P.CARD)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, P.px(18)))
        tk.Label(head, text=d.name, font=P.font("title"), fg=P.INK, bg=P.CARD).pack(side="left")
        badges = tk.Frame(head, bg=P.CARD)
        badges.pack(side="right")
        for t in ("Built-in" if d.built_in else d.output, d.vendor + " GPU", "Primary" if d.primary else None, d.gdi.replace("\\\\.\\", "")):
            if t:
                P.Pill(badges, t, bg=P.CARD, height=26, fnt="label", interactive=False).pack(side="left", padx=(P.px(6), 0))

        left = tk.Frame(b, bg=P.CARD)
        left.grid(row=1, column=0, sticky="nw", padx=(0, P.px(28)))
        kpis = tk.Frame(left, bg=P.CARD)
        kpis.pack(anchor="w", fill="x")
        native = (d.desktop_w, d.desktop_h) == (d.native_w, d.native_h)
        f = d.desktop_w / d.native_w if d.native_w else 1
        scale = "Native" if native else (("%.2f" % f).rstrip("0").rstrip(".") + "× down" if f > 1 else ("%.2f" % (1 / f)).rstrip("0").rstrip(".") + "× up")
        for col, (lab, val, sub) in enumerate((("Desktop", size_text(d.desktop_w, d.desktop_h), None),
                                               ("Panel signal", size_text(d.native_w, d.native_h), "%d Hz" % round(d.refresh)),
                                               ("Scale", scale, None))):
            k = tk.Frame(kpis, bg=P.CARD)
            k.grid(row=0, column=col, sticky="nw", padx=(0, P.px(26)))
            P.mono_label(k, lab).pack(anchor="w")
            tk.Label(k, text=val, font=P.font("kpi"), fg=P.INK, bg=P.CARD).pack(anchor="w", pady=(P.px(2), 0))
            if sub:
                P.mono_label(k, sub).pack(anchor="w")
        self._diagram(left, d).pack(anchor="w", pady=(P.px(20), 0))
        cap = ("The dashed area is drawn by Windows and shrunk to fit the solid panel." if d.desktop_w > d.native_w else
               "A smaller desktop is enlarged to fill the panel." if d.desktop_w < d.native_w else "Native: one desktop pixel per panel pixel.")
        P.label(left, cap, fnt="small", fg=P.INK_3, wraplength=P.px(380)).pack(anchor="w", pady=(P.px(8), 0))

        right = tk.Frame(b, bg=P.CARD)
        right.grid(row=1, column=1, sticky="new")
        P.mono_label(right, "Desktop size").pack(anchor="w", pady=(0, P.px(10)))
        grid = tk.Frame(right, bg=P.CARD)
        grid.pack(anchor="w")
        for i, (w, h) in enumerate(core.presets(d)):
            current = (w, h) == (d.desktop_w, d.desktop_h)
            text = "Native" if i == 0 else size_text(w, h)
            p = P.Pill(grid, text, command=(lambda w=w, h=h: self.apply(d, w, h)) if not current else None,
                       selected=current, bg=P.CARD, min_width=128)
            p.configure_state(enabled=not self.busy or current)
            p.grid(row=i // 3, column=i % 3, padx=(0, P.px(6)), pady=(0, P.px(6)), sticky="w")

        form = tk.Frame(right, bg=P.CARD)
        form.pack(anchor="w", pady=(P.px(14), 0))
        draft = self.drafts.get(d.key, ("", ""))
        entries = []
        for col, (lab, val, ph) in enumerate((("Width", draft[0], d.native_w * 2), ("Height", draft[1], d.native_h * 2))):
            fr = tk.Frame(form, bg=P.CARD)
            fr.grid(row=0, column=col * 2, sticky="w")
            P.mono_label(fr, lab).pack(anchor="w", pady=(0, P.px(4)))
            box = tk.Frame(fr, bg=P.PAPER_HI, highlightthickness=1, highlightbackground=P.LINE_2, highlightcolor=P.INK)
            box.pack(anchor="w")
            e = tk.Entry(box, width=6, font=P.font("lead"), fg=P.INK, bg=P.PAPER_HI, relief="flat", bd=0, insertbackground=P.INK,
                         highlightthickness=0)
            e.insert(0, val or str(ph))
            if not val:
                e.configure(fg=P.INK_3)
            e.bind("<FocusIn>", lambda ev, e=e, box=box: (e.configure(fg=P.INK), e.select_range(0, "end"), box.configure(highlightbackground=P.INK)))
            e.bind("<FocusOut>", lambda ev, box=box: box.configure(highlightbackground=P.LINE_2))
            e.pack(padx=P.px(14), pady=P.px(10))
            entries.append(e)
            if col == 0:
                tk.Label(form, text="×", font=P.font("lead"), fg=P.INK_3, bg=P.CARD).grid(row=0, column=1, sticky="s", padx=P.px(8), pady=(0, P.px(8)))

        def submit(_e=None):
            try:
                w, h = int(entries[0].get()), int(entries[1].get())
            except ValueError:
                self.show_toast("Enter a width and height, at least 640 × 480.")
                return
            self.drafts[d.key] = (entries[0].get(), entries[1].get())
            self.apply(d, w, h)

        for e in entries:
            e.bind("<Return>", submit)
            e.bind("<KeyRelease>", lambda ev: self.drafts.__setitem__(d.key, (entries[0].get(), entries[1].get())))
        ap = P.Pill(form, "Apply", command=submit, kind="ink", bg=P.CARD)
        ap.configure_state(enabled=not self.busy)
        ap.grid(row=0, column=3, sticky="s", padx=(P.px(10), 0), pady=(0, P.px(2)))

        st = tk.Frame(right, bg=P.CARD)
        st.pack(anchor="w", pady=(P.px(16), 0))
        if pend and pend["key"] == d.key:
            color, text = P.WARN, "Waiting for you to keep or revert %s." % size_text(pend["width"], pend["height"])
        elif rem:
            color, text = P.OK, "Remembered: %s%s" % (size_text(*rem), ", re-applied when monitors change." if core.keep_on_monitor_change() else ".")
        else:
            color, text = P.DOWN, "Using the native resolution."
        self._dots = getattr(self, "_dots", [])
        img = P.dot_image(color, P.CARD)
        self._dots.append(img)
        tk.Label(st, image=img, bg=P.CARD).pack(side="left", padx=(0, P.px(8)))
        P.label(st, text, fnt="body", fg=P.INK_2, wraplength=P.px(420)).pack(side="left")

        warn = None
        if not d.built_in:
            warn = "External display: some drivers (including Intel) refuse desktops larger than native here. If Windows refuses, nothing changes."
        elif not d.virtual_modes:
            warn = "This driver does not report Windows virtual-mode support, so the app will ask the driver to scale instead. It may refuse."
        if warn:
            wc = P.Card(right, pad=12, bg=P.WARN_BG, line=P.WARN_LINE, radius=12, outer=P.CARD)
            wc.pack(fill="x", pady=(P.px(16), 0))
            P.label(wc.body, warn, bg=P.WARN_BG, fnt="small", fg=P.INK_2, wraplength=P.px(400)).pack(anchor="w")

    def _diagram(self, master, d):
        W, H = P.px(340), P.px(150)
        cv = tk.Canvas(master, width=W, height=H + P.px(18), bg=P.CARD, highlightthickness=0, bd=0)
        mw, mh = max(d.desktop_w, d.native_w), max(d.desktop_h, d.native_h)
        s = min((W - 2) / mw, (H - 2) / mh)
        dw, dh, nw, nh = max(8, round(d.desktop_w * s)), max(8, round(d.desktop_h * s)), max(8, round(d.native_w * s)), max(8, round(d.native_h * s))
        cv._imgs = [P.rounded(dw, dh, P.px(6), None, P.ACCENT, 1.2, P.CARD, dash=(P.px(5), P.px(4))),
                    P.rounded(nw, nh, P.px(4), "#ebe8e0", P.INK, 1.2, P.CARD)]
        cv.create_image(0, H - dh, anchor="nw", image=cv._imgs[0])
        cv.create_image(0, H - nh, anchor="nw", image=cv._imgs[1])
        cv.create_text(0, H + P.px(10), anchor="w", font=P.font("label"), fill=P.INK_3,
                       text="desktop %s  ·  panel %s" % (size_text(d.desktop_w, d.desktop_h), size_text(d.native_w, d.native_h)))
        return cv

    # ---------- state ----------
    def refresh(self):
        """Re-render from the current state. Skips (instead of blocking) if a display change holds the lock."""
        if not core.LOCK.acquire(blocking=False):
            return
        try:
            ds = core.dc.displays()
            rems = [core.remembered(d) for d in ds]
            pend = core.pending()
            keep = core.keep_on_monitor_change()
            log = core.recent_log()
        finally:
            core.LOCK.release()
        self.keep.set(keep)
        self.startup.set(startup_enabled())
        sig = repr((ds, rems, pend and pend["key"], keep, self.busy))
        editing = isinstance(self.root.focus_get(), tk.Entry)
        if sig != self.sig and not editing:
            self.sig = sig
            for w in self.cards.winfo_children():
                w.destroy()
            self._dots = []
            if not ds:
                c = P.Card(self.cards)
                c.pack(fill="x")
                P.label(c.body, "No displays found", fnt="title").pack()
            for d, rem in zip(ds, rems):
                self._card(d, rem, pend)
        lsig = "\n".join(log[:40])
        if lsig != self.log_sig:
            self.log_sig = lsig
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            self.log.insert("1.0", lsig)
            self.log.configure(state="disabled")
        self._sync_dialog(pend)

    def _poll(self):
        try:
            self.refresh()
        except Exception as e:
            core.log("UI refresh error: %s" % e)
        self.root.after(1000, self._poll)

    def _drain(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "show":
                    self.show()
                elif kind == "apply":
                    self.apply(msg[1], msg[2], msg[3])
                elif kind == "toast":
                    self.show_toast(msg[1])
                elif kind == "refresh":
                    self.sig = None
                    self.refresh()
                elif kind == "exit":
                    self.quit()
                    return
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    # ---------- actions ----------
    def apply(self, d, w, h):
        if self.busy:
            return
        native = (w, h) == (d.native_w, d.native_h)
        self.busy = True
        self.sig = None
        self.show_toast("Switching %s to %s…" % (d.name, size_text(w, h)))

        def work():
            err = core.request(d.key, w, h, confirm=not native)
            self.busy = False
            self.q.put(("toast", err) if err else ("refresh",))
            if err:
                self.q.put(("refresh",))
        threading.Thread(target=work, daemon=True).start()
        self.refresh()

    def _set_keep(self, on):
        core.set_keep(on)
        self.sig = None
        self.refresh()

    def _set_startup(self, on):
        try:
            set_startup(on)
            self.show_toast("The app will start with Windows." if on else "The app will no longer start with Windows.")
        except Exception as e:
            self.show_toast("Could not change the startup entry: %s" % e)
        self.refresh()

    def show_toast(self, text):
        self.toast.configure(text=text, wraplength=P.px(460))
        self.toast.place(relx=1.0, rely=1.0, x=-P.px(18), y=-P.px(18), anchor="se")
        self.toast.lift()
        if self.toast_after:
            self.root.after_cancel(self.toast_after)
        self.toast_after = self.root.after(5500, self.toast.place_forget)

    # ---------- confirm dialog ----------
    def _sync_dialog(self, pend):
        if not pend:
            if self.dialog:
                self.dialog.destroy()
                self.dialog = None
            return
        if not self.dialog:
            self._open_dialog(pend)
        left = pend["seconds_left"]
        self._dlg_text.configure(text="If you can read this comfortably, keep it. Otherwise it goes back to your previous "
                                      "setting in %d second%s." % (left, "" if left == 1 else "s"))
        bw = int(self._dlg_bar["width"])
        self._dlg_bar.delete("fill")
        frac = left / core.CONFIRM_SECONDS
        if frac > 0:
            self._dlg_bar_img = P.rounded(max(P.px(4), round(bw * frac)), P.px(4), P.px(2), P.INK, bg=P.PAPER_LO)
            self._dlg_bar.create_image(0, 0, anchor="nw", image=self._dlg_bar_img, tags="fill")

    def _open_dialog(self, pend):
        dl = tk.Toplevel(self.root)
        self.dialog = dl
        dl.title("Keep display resolution?")
        dl.configure(bg=P.PAPER_HI)
        dl.resizable(False, False)
        dl.attributes("-topmost", True)
        if self.root.state() == "normal":
            dl.transient(self.root)
        dl.protocol("WM_DELETE_WINDOW", self._revert)
        body = tk.Frame(dl, bg=P.PAPER_HI)
        body.pack(fill="both", expand=True, padx=P.px(26), pady=(P.px(22), P.px(18)))
        P.eyebrow(body, "Display resolution", bg=P.PAPER_HI).pack(anchor="w")
        t = tk.Frame(body, bg=P.PAPER_HI)
        t.pack(anchor="w", pady=(P.px(8), P.px(12)))
        tk.Label(t, text="Keep ", font=P.font("title"), fg=P.INK, bg=P.PAPER_HI).pack(side="left")
        tk.Label(t, text=size_text(pend["width"], pend["height"]), font=P.font("title_em"), fg=P.INK, bg=P.PAPER_HI).pack(side="left")
        tk.Label(t, text="?", font=P.font("title"), fg=P.INK, bg=P.PAPER_HI).pack(side="left")
        self._dlg_text = P.label(body, "", bg=P.PAPER_HI, wraplength=P.px(440))
        self._dlg_text.pack(anchor="w")
        self._dlg_bar = tk.Canvas(body, width=P.px(440), height=P.px(4), bg=P.PAPER_HI, highlightthickness=0, bd=0)
        self._dlg_bar.pack(anchor="w", pady=(P.px(14), P.px(4)))
        self._dlg_track = P.rounded(P.px(440), P.px(4), P.px(2), P.PAPER_LO, bg=P.PAPER_HI)
        self._dlg_bar.create_image(0, 0, anchor="nw", image=self._dlg_track)
        tk.Frame(dl, bg=P.LINE, height=1).pack(fill="x")
        foot = tk.Frame(dl, bg=P.PAPER_HI)
        foot.pack(fill="x", padx=P.px(22), pady=(P.px(14), P.px(18)))
        keep = P.Pill(foot, "Keep changes", command=self._confirm, kind="ink", bg=P.PAPER_HI)
        keep.pack(side="right")
        P.Pill(foot, "Revert", command=self._revert, bg=P.PAPER_HI).pack(side="right", padx=(0, P.px(8)))
        dl.bind("<Return>", lambda e: self._confirm())
        dl.bind("<Escape>", lambda e: self._revert())
        dl.update_idletasks()
        sw, sh = dl.winfo_screenwidth(), dl.winfo_screenheight()
        dl.geometry("+%d+%d" % ((sw - dl.winfo_reqwidth()) // 2, (sh - dl.winfo_reqheight()) // 3))
        dl.deiconify()
        dl.lift()
        dl.focus_force()
        keep.focus_set()

    def _confirm(self):
        def work():
            err = core.confirm()
            self.q.put(("toast", err or "Kept. It will be restored after restarts."))
            self.q.put(("refresh",))
        threading.Thread(target=work, daemon=True).start()

    def _revert(self):
        def work():
            core.revert()
            self.q.put(("toast", "Reverted."))
            self.q.put(("refresh",))
        threading.Thread(target=work, daemon=True).start()

    # ---------- window ----------
    def show(self):
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def hide(self):
        self.root.withdraw()
        if not self.hidden_note_shown and self.tray:
            self.hidden_note_shown = True
            self.tray.notify("Still running in the tray. Right-click the icon for sizes, or Exit.", APP)

    def quit(self):
        core.revert()   # never leave an unconfirmed change behind
        core.log("App exited.")
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    tray = None


def _tray(app):
    def default_display():
        ds = core.displays()
        return next((d for d in ds if d.built_in), None) or next((d for d in ds if d.primary), None)

    def items():
        yield pystray.MenuItem("Open " + APP, lambda: app.q.put(("show",)), default=True)
        yield pystray.Menu.SEPARATOR
        d = default_display()
        if d:
            yield pystray.MenuItem("%s (native %d × %d)" % (d.name, d.native_w, d.native_h), lambda: None, enabled=False)
            for i, (w, h) in enumerate(core.presets(d)):
                yield pystray.MenuItem(("Native  " if i == 0 else "") + size_text(w, h),
                                       (lambda w, h: lambda: app.q.put(("apply", d, w, h)))(w, h),
                                       checked=(lambda w, h: lambda item: (d.desktop_w, d.desktop_h) == (w, h))(w, h), radio=True)
            yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Keep resolution when monitors change", lambda: (core.set_keep(not core.keep_on_monitor_change()), app.q.put(("refresh",))),
                               checked=lambda item: core.keep_on_monitor_change())
        yield pystray.MenuItem("Start with Windows", lambda: (set_startup(not startup_enabled()), app.q.put(("refresh",))),
                               checked=lambda item: startup_enabled())
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Exit", lambda: app.q.put(("exit",)))

    icon = pystray.Icon("intel-virtual-display", make_icon(64), APP, menu=pystray.Menu(lambda: list(items())))
    app.tray = icon
    core.on_notify(lambda m: icon.notify(m, APP))
    threading.Thread(target=icon.run, daemon=True, name="tray").start()

    def refresh_menu():
        last = None
        while True:
            time.sleep(2)
            try:
                with core.LOCK:
                    sig = repr((core.dc.displays(), core.keep_on_monitor_change(), startup_enabled()))
                if sig != last:
                    last = sig
                    icon.update_menu()
            except Exception:
                pass
    threading.Thread(target=refresh_menu, daemon=True, name="tray-menu").start()


def _ticker():
    n = 0
    while True:
        time.sleep(1)
        n += 1
        try:
            core.tick(n)
        except Exception as e:
            core.log("Timer error: %s" % e)


def main():
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # per-monitor DPI aware: physical pixels
    background = "--background" in sys.argv
    mutex = _k32.CreateMutexW(None, True, MUTEX_NAME)
    if ctypes.get_last_error() == 183:   # ERROR_ALREADY_EXISTS: open the running copy's window instead
        if not background:
            ev = _k32.OpenEventW(0x0002, False, SHOW_EVENT)
            if ev:
                _k32.SetEvent(ev)
        return
    show_event = _k32.CreateEventW(None, False, False, SHOW_EVENT)

    core.init()
    core.log("App started%s." % (" in the background" if background else ""))
    app = App(background)
    _tray(app)

    def wait_show():
        while _k32.WaitForSingleObject(show_event, 0xFFFFFFFF) == 0:
            app.q.put(("show",))
    threading.Thread(target=wait_show, daemon=True, name="show").start()
    threading.Thread(target=_ticker, daemon=True, name="ticker").start()
    app.root.mainloop()
    del mutex
