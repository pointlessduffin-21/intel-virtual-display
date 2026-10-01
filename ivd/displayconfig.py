"""Windows display-configuration (CCD) API via ctypes.

The technique: Windows "virtual modes". The desktop (source mode) is made larger than the signal sent to the
display (target mode), and Windows' compositor scales the whole desktop into the display's native-size
surface. The GPU driver only ever sees a native-resolution image, so no driver downsampling feature is needed.
"""
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass

QDC_ONLY_ACTIVE_PATHS = 0x2
QDC_VIRTUAL_MODE_AWARE = 0x10
SDC_USE_DATABASE_CURRENT = 0x0F
SDC_USE_SUPPLIED_DISPLAY_CONFIG = 0x20
SDC_VALIDATE = 0x40
SDC_APPLY = 0x80
SDC_SAVE_TO_DATABASE = 0x200
SDC_ALLOW_CHANGES = 0x400
SDC_VIRTUAL_MODE_AWARE = 0x8000
PATH_SUPPORT_VIRTUAL_MODE = 0x8
OUTPUT_INTERNAL = 0x80000000
SCALING_IDENTITY = 1
SCALING_ASPECTRATIOCENTEREDMAX = 4
MODE_SOURCE, MODE_TARGET, MODE_DESKTOP_IMAGE = 1, 2, 3


class LUID(C.Structure):
    _fields_ = [("LowPart", W.DWORD), ("HighPart", W.LONG)]


class RATIONAL(C.Structure):
    _fields_ = [("Numerator", C.c_uint32), ("Denominator", C.c_uint32)]


class SOURCE_INFO(C.Structure):
    _fields_ = [("adapterId", LUID), ("id", C.c_uint32), ("modeInfoIdx", C.c_uint32), ("statusFlags", C.c_uint32)]


class TARGET_INFO(C.Structure):
    _fields_ = [("adapterId", LUID), ("id", C.c_uint32), ("modeInfoIdx", C.c_uint32), ("outputTechnology", C.c_uint32),
                ("rotation", C.c_uint32), ("scaling", C.c_uint32), ("refreshRate", RATIONAL),
                ("scanLineOrdering", C.c_uint32), ("targetAvailable", W.BOOL), ("statusFlags", C.c_uint32)]


class PATH_INFO(C.Structure):
    _fields_ = [("sourceInfo", SOURCE_INFO), ("targetInfo", TARGET_INFO), ("flags", C.c_uint32)]


class VIDEO_SIGNAL(C.Structure):
    _fields_ = [("pixelRate", C.c_uint64), ("hSyncFreq", RATIONAL), ("vSyncFreq", RATIONAL),
                ("activeCx", C.c_uint32), ("activeCy", C.c_uint32), ("totalCx", C.c_uint32), ("totalCy", C.c_uint32),
                ("videoStandard", C.c_uint32), ("scanLineOrdering", C.c_uint32)]


class SOURCE_MODE(C.Structure):
    _fields_ = [("width", C.c_uint32), ("height", C.c_uint32), ("pixelFormat", C.c_uint32), ("x", C.c_int32), ("y", C.c_int32)]


class POINTL(C.Structure):
    _fields_ = [("x", C.c_int32), ("y", C.c_int32)]


class RECTL(C.Structure):
    _fields_ = [("left", C.c_int32), ("top", C.c_int32), ("right", C.c_int32), ("bottom", C.c_int32)]


class DESKTOP_IMAGE(C.Structure):
    _fields_ = [("pathSourceSize", POINTL), ("region", RECTL), ("clip", RECTL)]


class MODE_UNION(C.Union):
    _fields_ = [("target", VIDEO_SIGNAL), ("source", SOURCE_MODE), ("desktop", DESKTOP_IMAGE)]


class MODE_INFO(C.Structure):
    _fields_ = [("infoType", C.c_uint32), ("id", C.c_uint32), ("adapterId", LUID), ("u", MODE_UNION)]


class HEADER(C.Structure):
    _fields_ = [("type", C.c_int32), ("size", C.c_uint32), ("adapterId", LUID), ("id", C.c_uint32)]


class SOURCE_NAME(C.Structure):
    _fields_ = [("header", HEADER), ("gdiName", C.c_wchar * 32)]


class TARGET_NAME(C.Structure):
    _fields_ = [("header", HEADER), ("flags", C.c_uint32), ("outputTechnology", C.c_uint32),
                ("edidManufactureId", C.c_uint16), ("edidProductCodeId", C.c_uint16), ("connectorInstance", C.c_uint32),
                ("friendlyName", C.c_wchar * 64), ("monitorDevicePath", C.c_wchar * 128)]


class ADAPTER_NAME(C.Structure):
    _fields_ = [("header", HEADER), ("adapterDevicePath", C.c_wchar * 128)]


assert C.sizeof(PATH_INFO) == 72 and C.sizeof(MODE_INFO) == 64, "unexpected struct layout"

_user32 = C.WinDLL("user32", use_last_error=True)
_user32.GetDisplayConfigBufferSizes.argtypes = [C.c_uint32, C.POINTER(C.c_uint32), C.POINTER(C.c_uint32)]
_user32.QueryDisplayConfig.argtypes = [C.c_uint32, C.POINTER(C.c_uint32), C.c_void_p, C.POINTER(C.c_uint32), C.c_void_p, C.c_void_p]
_user32.SetDisplayConfig.argtypes = [C.c_uint32, C.c_void_p, C.c_uint32, C.c_void_p, C.c_uint32]
_user32.DisplayConfigGetDeviceInfo.argtypes = [C.c_void_p]
for _f in (_user32.GetDisplayConfigBufferSizes, _user32.QueryDisplayConfig, _user32.SetDisplayConfig, _user32.DisplayConfigGetDeviceInfo):
    _f.restype = C.c_long


class Config:
    """An active display configuration: path and mode arrays plus the query flags they came from."""

    def __init__(self, paths, modes, flags):
        self.paths, self.modes, self.flags = paths, modes, flags

    @property
    def virtual(self):
        return bool(self.flags & QDC_VIRTUAL_MODE_AWARE)

    def clone(self):
        return Config(type(self.paths).from_buffer_copy(self.paths), type(self.modes).from_buffer_copy(self.modes), self.flags)

    # In virtual-mode-aware configs indices are packed: source in the high 16 bits; target high 16, desktop image low 16.
    def source_idx(self, pi):
        v = self.paths[pi].sourceInfo.modeInfoIdx
        return v >> 16 if self.virtual else v

    def target_idx(self, pi):
        v = self.paths[pi].targetInfo.modeInfoIdx
        return v >> 16 if self.virtual else v

    def desktop_idx(self, pi):
        if not self.virtual:
            return -1
        i = self.paths[pi].targetInfo.modeInfoIdx & 0xFFFF
        return i if i < len(self.modes) and self.modes[i].infoType == MODE_DESKTOP_IMAGE else -1


def query(virtual_aware=True):
    flags = QDC_ONLY_ACTIVE_PATHS | (QDC_VIRTUAL_MODE_AWARE if virtual_aware else 0)
    for _ in range(3):   # the configuration can change between the two calls; retry
        np_, nm = C.c_uint32(), C.c_uint32()
        r = _user32.GetDisplayConfigBufferSizes(flags, C.byref(np_), C.byref(nm))
        if r:
            raise OSError(r, "GetDisplayConfigBufferSizes failed")
        paths, modes = (PATH_INFO * np_.value)(), (MODE_INFO * nm.value)()
        r = _user32.QueryDisplayConfig(flags, C.byref(np_), paths, C.byref(nm), modes, None)
        if r == 122:   # ERROR_INSUFFICIENT_BUFFER
            continue
        if r:
            raise OSError(r, "QueryDisplayConfig failed")
        return Config((PATH_INFO * np_.value).from_buffer_copy(paths, 0) if np_.value else (PATH_INFO * 0)(),
                      (MODE_INFO * nm.value).from_buffer_copy(modes, 0) if nm.value else (MODE_INFO * 0)(), flags)
    raise OSError(122, "QueryDisplayConfig kept changing")


def query_any():
    try:
        return query(True)
    except OSError:
        return query(False)   # older Windows without the virtual-mode-aware query


_OUTPUTS = {0: "VGA", 4: "DVI", 5: "HDMI", 6: "LVDS", 10: "DisplayPort", 11: "Embedded DisplayPort", 13: "Embedded UDI",
            15: "Miracast", 16: "Indirect (wired)", 17: "Indirect (virtual)", 18: "DisplayPort (USB tunnel)", OUTPUT_INTERNAL: "Internal"}


def _vendor(path):
    p = (path or "").upper()
    for tag, name in (("VEN_8086", "Intel"), ("VEN_10DE", "NVIDIA"), ("VEN_1002", "AMD"), ("VEN_1022", "AMD"), ("VEN_1414", "Microsoft"), ("VEN_5143", "Qualcomm")):
        if tag in p:
            return name
    return "Unknown"


@dataclass
class Display:
    path_index: int
    gdi: str
    name: str
    output: str
    vendor: str
    monitor_path: str
    built_in: bool
    primary: bool
    virtual_modes: bool
    desktop_w: int
    desktop_h: int
    native_w: int
    native_h: int
    refresh: float

    @property
    def key(self):
        return self.monitor_path or self.gdi


def describe(cfg):
    out = []
    for i in range(len(cfg.paths)):
        p = cfg.paths[i]
        s = cfg.modes[cfg.source_idx(i)].u.source
        t = cfg.modes[cfg.target_idx(i)].u.target
        sn = SOURCE_NAME(); sn.header.type = 1; sn.header.size = C.sizeof(SOURCE_NAME); sn.header.adapterId = p.sourceInfo.adapterId; sn.header.id = p.sourceInfo.id
        tn = TARGET_NAME(); tn.header.type = 2; tn.header.size = C.sizeof(TARGET_NAME); tn.header.adapterId = p.targetInfo.adapterId; tn.header.id = p.targetInfo.id
        an = ADAPTER_NAME(); an.header.type = 4; an.header.size = C.sizeof(ADAPTER_NAME); an.header.adapterId = p.targetInfo.adapterId
        for info in (sn, tn, an):
            _user32.DisplayConfigGetDeviceInfo(C.byref(info))
        tech = p.targetInfo.outputTechnology
        built_in = tech in (OUTPUT_INTERNAL, 6, 11, 13)
        out.append(Display(
            path_index=i, gdi=sn.gdiName, name=tn.friendlyName or ("Built-in display" if built_in else "Display"),
            output=_OUTPUTS.get(tech, "Other (%d)" % tech), vendor=_vendor(an.adapterDevicePath), monitor_path=tn.monitorDevicePath,
            built_in=built_in, primary=(s.x == 0 and s.y == 0),
            virtual_modes=cfg.virtual and bool(p.flags & PATH_SUPPORT_VIRTUAL_MODE) and cfg.desktop_idx(i) >= 0,
            desktop_w=s.width, desktop_h=s.height, native_w=t.activeCx, native_h=t.activeCy,
            refresh=(t.vSyncFreq.Numerator / t.vSyncFreq.Denominator) if t.vSyncFreq.Denominator else 0.0))
    return out


def displays():
    return describe(query_any())


def with_virtual_desktop(cfg, pi, w, h):
    """Desktop of w x h fitted (aspect ratio kept, centered) into the display's native-size surface."""
    r = cfg.clone()
    si, di = r.source_idx(pi), r.desktop_idx(pi)
    if di < 0:
        raise ValueError("This display path has no desktop image (no virtual-mode support).")
    d = r.modes[di].u.desktop
    pw, ph = d.pathSourceSize.x, d.pathSourceSize.y
    scale = min(pw / w, ph / h)
    rw, rh = round(w * scale), round(h * scale)
    left, top = (pw - rw) // 2, (ph - rh) // 2
    r.modes[si].u.source.width, r.modes[si].u.source.height = w, h
    d.region.left, d.region.top, d.region.right, d.region.bottom = left, top, left + rw, top + rh
    d.clip.left, d.clip.top, d.clip.right, d.clip.bottom = 0, 0, w, h
    return r


def with_legacy_desktop(cfg, pi, w, h, scaling):
    """Fallback for drivers without virtual modes: a larger source mode that the driver/GPU scaler must shrink."""
    r = cfg.clone()
    si = r.source_idx(pi)
    r.modes[si].u.source.width, r.modes[si].u.source.height = w, h
    r.paths[pi].targetInfo.scaling = scaling
    return r


def _set(cfg, flags):
    flags |= SDC_USE_SUPPLIED_DISPLAY_CONFIG | SDC_ALLOW_CHANGES | (SDC_VIRTUAL_MODE_AWARE if cfg.virtual else 0)
    return _user32.SetDisplayConfig(len(cfg.paths), cfg.paths, len(cfg.modes), cfg.modes, flags)


def apply(cfg, save):
    return _set(cfg, SDC_APPLY | (SDC_SAVE_TO_DATABASE if save else 0))


def validate(cfg):
    return _set(cfg, SDC_VALIDATE)


def apply_saved():
    """Re-applies the configuration Windows has saved for the connected displays."""
    return _user32.SetDisplayConfig(0, None, 0, None, SDC_APPLY | SDC_USE_DATABASE_CURRENT)
