"""The Paper design system for Tkinter: tokens, fonts, and a few widgets.

Paper ground, ink type, one vermilion accent; Instrument Serif for titles and numbers, Inter for text,
JetBrains Mono for small uppercase labels. Rounded shapes are rendered with Pillow (supersampled, so edges are
antialiased) and cached, because Tk's canvas draws curves without antialiasing.
"""
import ctypes
import glob
import os
import sys
import tkinter as tk
import tkinter.font as tkfont

from PIL import Image, ImageDraw, ImageTk

PAPER = "#f5f2eb"
PAPER_HI = "#faf8f3"
PAPER_LO = "#ebe6db"
CARD = "#f9f7f1"          # rgba(250,248,243,.82) over paper
INK = "#1c1b18"
INK_2 = "#4f4b43"
INK_3 = "#8a857a"
LINE = "#dbd8d2"          # ink at 12% over paper
LINE_2 = "#c5c2bd"        # ink at 22%
ACCENT = "#d2462f"
OK = "#3d7a57"
WARN = "#c68a17"
DOWN = "#b5afa3"
WARN_BG, WARN_LINE = "#f7f1e6", "#dec181"
ERR_BG, ERR_LINE = "#f8efe9", "#e8a89b"

_scale = 1.0
_fonts = {}
_images = {}


def resource(*parts):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def px(n):
    return int(round(n * _scale))


def setup(root):
    """Load the bundled fonts privately for this process and define the named fonts."""
    global _scale
    _scale = root.winfo_fpixels("1i") / 96.0
    for f in glob.glob(resource("fonts", "*.ttf")):
        ctypes.windll.gdi32.AddFontResourceExW(f, 0x10, 0)   # FR_PRIVATE: visible to this process only
    fams = set(tkfont.families(root))

    def pick(*names):
        return next((n for n in names if n in fams), names[-1])

    serif, sans = pick("Instrument Serif", "Georgia"), pick("Inter", "Segoe UI")
    sans_med, sans_semi = pick("Inter Medium", "Segoe UI Semibold"), pick("Inter SemiBold", "Segoe UI Semibold")
    mono, mono_med = pick("JetBrains Mono", "Consolas"), pick("JetBrains Mono Medium", "JetBrains Mono", "Consolas")
    spec = {
        "display": (serif, 40, "normal", "roman"), "display_em": (serif, 40, "normal", "italic"),
        "title": (serif, 28, "normal", "roman"), "title_em": (serif, 28, "normal", "italic"),
        "kpi": (serif, 26, "normal", "roman"),
        "lead": (sans, 16, "normal", "roman"), "body": (sans, 14, "normal", "roman"), "small": (sans, 13, "normal", "roman"),
        "body_med": (sans_med, 14, "normal", "roman"), "strong": (sans_semi, 14, "normal", "roman"),
        "label": (mono, 10, "normal", "roman"), "mono": (mono, 11, "normal", "roman"), "button": (mono_med, 11, "normal", "roman"),
    }
    for name, (family, size, weight, slant) in spec.items():
        _fonts[name] = tkfont.Font(root, family=family, size=-px(size), weight=weight, slant=slant)


def font(name):
    return _fonts[name]


def _rgba(c, a=255):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (a,)


def _supersampled(w, h, r, fill, outline, width, bg, dash):
    ss = 4
    im = Image.new("RGBA", (w * ss, h * ss), _rgba(bg))
    d = ImageDraw.Draw(im)
    lw = max(1, round(width * ss))
    box = (lw / 2, lw / 2, w * ss - lw / 2 - 1, h * ss - lw / 2 - 1)
    d.rounded_rectangle(box, radius=r * ss, fill=_rgba(fill) if fill else None, outline=_rgba(outline) if outline else None, width=lw)
    if outline and dash:
        # cut gaps along the straight edges
        on, off = dash[0] * ss, dash[1] * ss
        gap = _rgba(fill or bg)
        band = lw * 2
        for length, horizontal in ((w * ss, True), (h * ss, False)):
            pos, end = r * ss, length - r * ss
            while pos + on < end:
                a, b = pos + on, min(pos + on + off, end)
                if horizontal:
                    d.rectangle((a, 0, b, band), fill=gap)
                    d.rectangle((a, h * ss - band, b, h * ss), fill=gap)
                else:
                    d.rectangle((0, a, band, b), fill=gap)
                    d.rectangle((w * ss - band, a, w * ss, b), fill=gap)
                pos += on + off
    return im.resize((w, h), Image.LANCZOS)


def _nine_slice(w, h, r, fill, outline, width, bg):
    """Antialias only a small corner template, then assemble w x h from its corners, stretched edges and fill.
    Costs the same for a full-width card as for a button."""
    c = r + 2                                  # corner cell size
    t = _supersampled(2 * c + 1, 2 * c + 1, r, fill, outline, width, bg, None)
    im = Image.new("RGBA", (w, h))
    im.paste(t.crop((c, c, c + 1, c + 1)).resize((w, h)), (0, 0))          # centre fill
    im.paste(t.crop((c, 0, c + 1, c)).resize((w - 2 * c, c)), (c, 0))         # top edge
    im.paste(t.crop((c, c + 1, c + 1, 2 * c + 1)).resize((w - 2 * c, c)), (c, h - c))   # bottom
    im.paste(t.crop((0, c, c, c + 1)).resize((c, h - 2 * c)), (0, c))         # left
    im.paste(t.crop((c + 1, c, 2 * c + 1, c + 1)).resize((c, h - 2 * c)), (w - c, c))   # right
    im.paste(t.crop((0, 0, c, c)), (0, 0))
    im.paste(t.crop((c + 1, 0, 2 * c + 1, c)), (w - c, 0))
    im.paste(t.crop((0, c + 1, c, 2 * c + 1)), (0, h - c))
    im.paste(t.crop((c + 1, c + 1, 2 * c + 1, 2 * c + 1)), (w - c, h - c))
    return im


def rounded(w, h, r, fill, outline=None, width=1, bg=PAPER, dash=None):
    """A cached antialiased rounded rectangle image (pill when r >= h/2)."""
    w, h = max(1, int(w)), max(1, int(h))
    r = max(0, min(int(r), w // 2, h // 2))
    key = (w, h, r, fill, outline, width, bg, dash)
    img = _images.get(key)
    if img:
        return img
    if dash or w < 2 * r + 6 or h < 2 * r + 6:
        im = _supersampled(w, h, r, fill, outline, width, bg, dash)
    else:
        im = _nine_slice(w, h, r, fill, outline, width, bg)
    img = ImageTk.PhotoImage(im)
    _images[key] = img
    if len(_images) > 400:
        _images.pop(next(iter(_images)))
    return img


def dot_image(color, bg):
    return rounded(px(8), px(8), px(4), color, bg=bg)


class Card(tk.Canvas):
    """A translucent-paper card: rounded hairline frame with an inner frame for content (`.body`)."""

    def __init__(self, master, pad=20, bg=CARD, line=LINE, radius=18, outer=PAPER, **kw):
        super().__init__(master, bg=outer, highlightthickness=0, bd=0, **kw)
        self._pad, self._fill, self._line, self._radius, self._outer = px(pad), bg, line, px(radius), outer
        self.body = tk.Frame(self, bg=bg)
        self._win = self.create_window(self._pad, self._pad, anchor="nw", window=self.body)
        self._bgimg = None
        self.body.bind("<Configure>", self._fit)
        self.bind("<Configure>", self._redraw)

    def _fit(self, _e=None):
        self.configure(height=self.body.winfo_reqheight() + 2 * self._pad)

    def _redraw(self, e):
        w, h = max(e.width, 2 * self._radius + 2), max(e.height, 2 * self._radius + 2)
        self.itemconfigure(self._win, width=w - 2 * self._pad)
        self.delete("bg")
        self._bgimg = rounded(w, h, self._radius, self._fill, self._line, 1, self._outer)
        self.create_image(0, 0, anchor="nw", image=self._bgimg, tags="bg")
        self.tag_lower("bg")


class Pill(tk.Canvas):
    """Pill button. kind: 'outline' | 'ink'. selected=True renders it filled (aria-pressed)."""

    def __init__(self, master, text, command=None, kind="outline", selected=False, bg=CARD, min_width=0, height=34, fnt="button", interactive=True):
        self._font = font(fnt)
        w = max(px(min_width), self._font.measure(text) + px(32))
        h = px(height)
        super().__init__(master, width=w, height=h, bg=bg, highlightthickness=0, bd=0, cursor="hand2" if interactive else "arrow", takefocus=1 if interactive else 0)
        self._text, self._cmd, self._kind, self._selected, self._bg = text, command, kind, selected, bg
        self._hover, self._enabled = False, True
        if not interactive:   # a badge: same shape, no hover or focus
            self._draw()
            return
        for ev, fn in (("<Enter>", lambda e: self._set_hover(True)), ("<Leave>", lambda e: self._set_hover(False)),
                       ("<ButtonRelease-1>", self._click), ("<Return>", self._click), ("<space>", self._click),
                       ("<FocusIn>", lambda e: self._draw()), ("<FocusOut>", lambda e: self._draw())):
            self.bind(ev, fn)
        self._draw()

    def configure_state(self, enabled=None, selected=None, text=None):
        if enabled is not None:
            self._enabled = enabled
        if selected is not None:
            self._selected = selected
        if text is not None:
            self._text = text
        self.configure(cursor="hand2" if self._enabled else "arrow")
        self._draw()

    def _set_hover(self, on):
        self._hover = on
        self._draw()

    def _click(self, _e=None):
        if self._enabled and self._cmd:
            self._cmd()

    def _draw(self):
        w, h = int(self["width"]), int(self["height"])
        filled = self._kind == "ink" or self._selected
        if filled:
            fill, line, fg = ("#000000" if self._hover and self._enabled else INK), INK, PAPER
        else:
            fill, line, fg = self._bg, (INK if self._hover and self._enabled else LINE_2), INK
        if not self._enabled:
            fill, line, fg = (PAPER_LO if filled else self._bg), LINE, INK_3
        focus = self.focus_get() is self
        self.delete("all")
        self._img = rounded(w, h, h // 2, fill, ACCENT if focus else line, 2 if focus else 1, self._bg)
        self.create_image(0, 0, anchor="nw", image=self._img)
        self.create_text(w // 2, h // 2, text=self._text, font=self._font, fill=fg)


class Check(tk.Frame):
    """Checkbox with a paper-styled box and a text label."""

    def __init__(self, master, text, command=None, bg=CARD):
        super().__init__(master, bg=bg, cursor="hand2", takefocus=1)
        self._on, self._cmd, self._bg = False, command, bg
        self.box = tk.Canvas(self, width=px(20), height=px(20), bg=bg, highlightthickness=0, bd=0)
        self.box.pack(side="left", padx=(0, px(10)))
        self.label = tk.Label(self, text=text, font=font("body"), fg=INK, bg=bg, justify="left", anchor="w", wraplength=px(360))
        self.label.pack(side="left", fill="x")
        for w in (self, self.box, self.label):
            w.bind("<ButtonRelease-1>", self._toggle)
        self.bind("<space>", self._toggle)
        self.bind("<FocusIn>", lambda e: self._draw())
        self.bind("<FocusOut>", lambda e: self._draw())
        self._draw()

    def set(self, on):
        if on != self._on:
            self._on = on
            self._draw()

    def _toggle(self, _e=None):
        self._on = not self._on
        self._draw()
        if self._cmd:
            self._cmd(self._on)

    def _draw(self):
        s = px(20)
        self.box.delete("all")
        focus = self.focus_get() is self
        self._img = rounded(s, s, px(5), INK if self._on else PAPER_HI, ACCENT if focus else (INK if self._on else LINE_2), 2 if focus else 1, self._bg)
        self.box.create_image(0, 0, anchor="nw", image=self._img)
        if self._on:
            self.box.create_line(px(5), px(10.5), px(8.5), px(14), px(15), px(6.5), fill=PAPER, width=max(2, px(2)), capstyle="round", joinstyle="round")


class Scroll(tk.Frame):
    """Vertically scrolling container; put content in `.inner`.

    The scrollbar is a thin overlay (an ink-3 pill on the right edge), so showing or hiding it never changes
    the content width. A layout-affecting scrollbar can flip-flop forever when re-wrapped text makes the page
    alternately taller and shorter than the window.
    """

    def __init__(self, master, bg=PAPER):
        super().__init__(master, bg=bg)
        self._bg = bg
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, yscrollincrement=px(24))
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window(0, 0, anchor="nw", window=self.inner)
        self.canvas.pack(fill="both", expand=True)
        self.bar = tk.Canvas(self, width=px(10), bg=bg, highlightthickness=0, bd=0)
        self._drag = None
        self.canvas.configure(yscrollcommand=lambda a, b: self._paint_bar(float(a), float(b)))
        self.inner.bind("<Configure>", self._region)
        self.canvas.bind("<Configure>", self._width)
        self.bar.bind("<ButtonPress-1>", self._press)
        self.bar.bind("<B1-Motion>", self._move)
        self.bind_all("<MouseWheel>", self._wheel, add="+")

    def _width(self, e):
        self.canvas.itemconfigure(self._win, width=e.width)
        self._region()

    def _region(self, _e=None):
        self.canvas.configure(scrollregion=(0, 0, 1, max(self.inner.winfo_reqheight(), 1)))
        a, b = self.canvas.yview()
        self._paint_bar(a, b)

    def _paint_bar(self, a, b):
        if b - a >= 0.999:
            self.bar.place_forget()
            return
        self.bar.place(relx=1.0, x=-px(3), y=0, relheight=1.0, anchor="ne")
        h = max(self.bar.winfo_height(), 1)
        top, bot = int(a * h), max(int(b * h), int(a * h) + px(24))
        self.bar.delete("all")
        self._thumb = rounded(px(6), max(px(8), bot - top), px(3), INK_3, bg=self._bg)
        self.bar.create_image(px(2), top, anchor="nw", image=self._thumb)

    def _press(self, e):
        self._drag = (e.y, self.canvas.yview()[0])

    def _move(self, e):
        if self._drag:
            y0, start = self._drag
            self.canvas.yview_moveto(start + (e.y - y0) / max(self.bar.winfo_height(), 1))

    def _wheel(self, e):
        if self.bar.winfo_ismapped() and str(e.widget).startswith(str(self)):
            self.canvas.yview_scroll(int(-e.delta / 120) * 2, "units")


def label(master, text, bg=CARD, fnt="body", fg=INK, **kw):
    return tk.Label(master, text=text, font=font(fnt), fg=fg, bg=bg, anchor="w", justify="left", **kw)


def eyebrow(master, text, bg=PAPER):
    """Mono uppercase label with the leading hairline dash, as in paper.css."""
    f = tk.Frame(master, bg=bg)
    c = tk.Canvas(f, width=px(22), height=px(10), bg=bg, highlightthickness=0, bd=0)
    c.create_line(0, px(5), px(18), px(5), fill=INK_3)
    c.pack(side="left", padx=(0, px(8)))
    tk.Label(f, text=text.upper(), font=font("label"), fg=INK_3, bg=bg).pack(side="left")
    return f


def mono_label(master, text, bg=CARD, fg=INK_3):
    # JetBrains Mono at small sizes reads as letter-spaced already; uppercase as the kit does.
    return tk.Label(master, text=text.upper(), font=font("label"), fg=fg, bg=bg, anchor="w")
