"""Display state and actions: apply / confirm / revert, per-monitor memory, and the monitor-change keeper.

Every public function takes LOCK, so the UI, the tray thread and the ticker never interleave display changes.
Settings use the same file and format as earlier versions (%LOCALAPPDATA%\\intel-virtual-display\\settings.json).
"""
import json
import os
import threading
import time
from collections import deque
from datetime import datetime

from . import displayconfig as dc

CONFIRM_SECONDS = 15
DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "intel-virtual-display")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
LOG_PATH = os.path.join(DATA_DIR, "log.txt")
# Preset desktop sizes as multiples of native: on 1920x1080 -> 2560x1440, 3200x1800, 3840x2160, 5120x2880, 7680x4320.
FACTORS = (4 / 3, 5 / 3, 2, 8 / 3, 4)

LOCK = threading.RLock()
_recent = deque(maxlen=80)
_settings = None
_pending = None          # dict(key, name, width, height, deadline, config)
_listeners = []          # callables(message) for keeper / timeout notifications


def even(v):
    return int(round(v / 2)) * 2


def presets(d):
    return [(d.native_w, d.native_h)] + [(even(d.native_w * f), even(d.native_h * f)) for f in FACTORS]


def on_notify(fn):
    _listeners.append(fn)


def _notify(msg):
    for fn in _listeners:
        try:
            fn(msg)
        except Exception:
            pass


# ---------- log ----------
def log(msg):
    _recent.append(datetime.now().strftime("%H:%M:%S") + "  " + msg)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(datetime.now().isoformat() + " " + msg + "\n")
    except OSError:
        pass


def recent_log():
    return list(reversed(_recent))


# ---------- settings ----------
def _save():
    tmp = SETTINGS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_settings, f, indent=1)
    os.replace(tmp, SETTINGS_PATH)


def init():
    global _settings
    os.makedirs(DATA_DIR, exist_ok=True)
    with LOCK:
        try:
            with open(SETTINGS_PATH, encoding="utf-8-sig") as f:
                _settings = json.load(f)
        except FileNotFoundError:
            _settings = None
        except (OSError, ValueError) as e:
            log("Could not read settings (%s); starting fresh." % e)
            _settings = None
        if not isinstance(_settings, dict):
            # First run: remember any display already running a non-native desktop, so it is kept.
            _settings = {"KeepOnMonitorChange": True, "Displays": {}}
            for d in dc.displays():
                if (d.desktop_w, d.desktop_h) != (d.native_w, d.native_h):
                    _settings["Displays"][d.key] = {"Name": d.name, "Width": d.desktop_w, "Height": d.desktop_h}
            _save()
        _settings.setdefault("KeepOnMonitorChange", True)
        _settings.setdefault("Displays", {})


def keep_on_monitor_change():
    with LOCK:
        return bool(_settings["KeepOnMonitorChange"])


def set_keep(on):
    with LOCK:
        _settings["KeepOnMonitorChange"] = bool(on)
        _save()
    log("Keep resolution when monitors change: " + ("on" if on else "off"))


def remembered(d):
    with LOCK:
        s = _settings["Displays"].get(d.key)
        return (s["Width"], s["Height"]) if s else None


def _remember(d, w, h):
    if (w, h) == (d.native_w, d.native_h):
        _settings["Displays"].pop(d.key, None)
    else:
        _settings["Displays"][d.key] = {"Name": d.name, "Width": w, "Height": h}
    _save()


def displays():
    with LOCK:
        return dc.displays()


def _find(key):
    return next((d for d in dc.displays() if d.key == key), None)


# ---------- applying ----------
def _apply_transient(target, w, h):
    """Applies for this session only. Returns (config, None) on success or (None, error)."""
    attempts = []
    if target.virtual_modes:
        attempts.append(("virtual mode", dc.with_virtual_desktop(dc.query(True), target.path_index, w, h)))
    legacy = dc.query(False)
    li = next((x for x in dc.describe(legacy) if x.gdi == target.gdi), None)
    native = (w, h) == (target.native_w, target.native_h)
    if li:
        attempts.append(("driver scaling", dc.with_legacy_desktop(legacy, li.path_index, w, h,
                                                                  dc.SCALING_IDENTITY if native else dc.SCALING_ASPECTRATIOCENTEREDMAX)))
    clamped = False
    for name, cfg in attempts:
        r = dc.apply(cfg, False)
        if r:
            log("Using %s, %dx%d was rejected (error %d)." % (name, w, h, r))
            continue
        time.sleep(0.7)
        now = next((x for x in dc.describe(dc.query(False)) if x.gdi == target.gdi), None)
        if now and (now.desktop_w, now.desktop_h) == (w, h):
            log("Applied %dx%d on %s using %s." % (w, h, target.name, name))
            return cfg, None
        clamped = True
        log("Using %s, Windows accepted %dx%d but kept %s." % (name, w, h, "%dx%d" % (now.desktop_w, now.desktop_h) if now else "?"))
        dc.apply_saved()
    err = "Windows would not run %d x %d on %s. The display is unchanged." % (w, h, target.name)
    if clamped and w > target.native_w:
        err += " Larger-than-native desktops are not allowed on this display by its driver (on Intel this typically works on built-in panels only)."
    if target.vendor == "AMD":
        err += " AMD Software also offers Virtual Super Resolution."
    if target.vendor == "NVIDIA":
        err += " NVIDIA Control Panel also offers DSR / DLDSR."
    log(err)
    return None, err


def request(key, w, h, confirm=True):
    """Apply w x h. With confirm, it is kept only if confirm() is called within CONFIRM_SECONDS. Returns an error or None."""
    global _pending
    if not (640 <= w <= 16384 and 480 <= h <= 16384):
        return "Choose a size between 640 x 480 and 16384 x 16384."
    with LOCK:
        if _pending:
            _revert_locked("replaced by a new request")
        d = _find(key)
        if not d:
            return "That display is no longer connected."
        cfg, err = _apply_transient(d, w, h)
        if not cfg:
            return err
        if confirm:
            _pending = {"key": key, "name": d.name, "width": w, "height": h, "config": cfg, "deadline": time.monotonic() + CONFIRM_SECONDS}
        else:
            dc.apply(cfg, True)
            _remember(d, w, h)
            log("Saved %dx%d for %s." % (w, h, d.name))
        return None


def confirm():
    global _pending
    with LOCK:
        if not _pending:
            return "Nothing is waiting for confirmation."
        r = dc.apply(_pending["config"], True)
        d = _find(_pending["key"])
        if d:
            _remember(d, _pending["width"], _pending["height"])
        log("Kept and saved %dx%d for %s (result %d)." % (_pending["width"], _pending["height"], _pending["name"], r))
        _pending = None
        return None


def revert():
    with LOCK:
        if _pending:
            _revert_locked("reverted")


def _revert_locked(why):
    global _pending
    r = dc.apply_saved()
    log("Change to %dx%d %s; restored the saved setting (result %d)." % (_pending["width"], _pending["height"], why, r))
    _pending = None


def pending():
    with LOCK:
        if not _pending:
            return None
        left = max(0, int(_pending["deadline"] - time.monotonic() + 0.999))
        return {"key": _pending["key"], "name": _pending["name"], "width": _pending["width"], "height": _pending["height"], "seconds_left": left}


# ---------- ticker: confirmation timeout + keeper ----------
_last_sig = None
_stable = 0
_enforce = True


def tick(n):
    """Call once a second. Reverts unconfirmed changes and runs the keeper every 2 s."""
    note = None
    with LOCK:
        if _pending:
            if time.monotonic() > _pending["deadline"]:
                _revert_locked("was not confirmed in %d s" % CONFIRM_SECONDS)
                note = "Resolution change reverted (not confirmed)."
        elif n % 2 == 0:
            note = _keeper_locked()
    if note:
        _notify(note)


def _keeper_locked():
    """Windows stores display settings per monitor combination, so after the set of connected monitors changes
    (and has settled for ~4 s) re-apply remembered sizes. A change made elsewhere (Windows Settings) while the
    monitors stay the same is adopted instead of fought."""
    global _last_sig, _stable, _enforce
    ds = dc.displays()
    sig = "|".join(sorted(d.key for d in ds))
    if sig != _last_sig:
        _last_sig, _stable, _enforce = sig, 0, True
        return None
    _stable += 1
    if _enforce:
        if _stable < 2:
            return None
        _enforce, _stable = False, 0
        if not _settings["KeepOnMonitorChange"]:
            return None
        changed = []
        for d in ds:
            want = _settings["Displays"].get(d.key)
            if not want or (d.desktop_w, d.desktop_h) == (want["Width"], want["Height"]):
                continue
            cfg, _ = _apply_transient(d, want["Width"], want["Height"])
            if cfg:
                dc.apply(cfg, True)
                changed.append("%dx%d on %s" % (want["Width"], want["Height"], d.name))
        if not changed:
            return None
        log("Monitors changed; re-applied " + ", ".join(changed) + ".")
        return "Re-applied " + ", ".join(changed) + "."
    if _stable >= 3:
        for d in ds:
            want = _settings["Displays"].get(d.key)
            current = (d.desktop_w, d.desktop_h)
            if (want and current != (want["Width"], want["Height"])) or (not want and current != (d.native_w, d.native_h)):
                _remember(d, *current)
                log("Noticed %dx%d on %s was set outside the app; remembering it." % (current[0], current[1], d.name))
    return None
