using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

// Win32 display-configuration (CCD) interop used by Set-VirtualResolution.ps1.
//
// The technique: Windows "virtual modes". The desktop (source mode) is made larger than the signal sent to the
// display (target mode), and Windows' compositor scales the whole desktop into the display's native-size surface.
// The GPU driver only ever sees a native-resolution image, so this needs no driver downsampling feature
// (Intel has none; AMD VSR / NVIDIA DSR are optional on those vendors).
public static class Vd {
  [StructLayout(LayoutKind.Sequential)] public struct LUID { public uint Low; public int High; }
  [StructLayout(LayoutKind.Sequential)] public struct RATIONAL { public uint Num, Den; }
  [StructLayout(LayoutKind.Sequential)] public struct SOURCE_INFO { public LUID adapterId; public uint id, modeInfoIdx, statusFlags; }
  [StructLayout(LayoutKind.Sequential)] public struct TARGET_INFO {
    public LUID adapterId; public uint id, modeInfoIdx, outputTechnology, rotation, scaling;
    public RATIONAL refreshRate; public uint scanLineOrdering; public int targetAvailable; public uint statusFlags;
  }
  [StructLayout(LayoutKind.Sequential)] public struct PATH_INFO { public SOURCE_INFO sourceInfo; public TARGET_INFO targetInfo; public uint flags; }
  [StructLayout(LayoutKind.Explicit, Size=64)] public struct MODE_INFO {
    [FieldOffset(0)] public uint infoType;            // 1 = source (desktop), 2 = target (signal), 3 = desktop image
    [FieldOffset(4)] public uint id;
    [FieldOffset(8)] public LUID adapterId;
    // DISPLAYCONFIG_VIDEO_SIGNAL_INFO (target)
    [FieldOffset(16)] public ulong pixelRate;
    [FieldOffset(24)] public RATIONAL hSyncFreq;
    [FieldOffset(32)] public RATIONAL vSyncFreq;
    [FieldOffset(40)] public uint activeCx;
    [FieldOffset(44)] public uint activeCy;
    [FieldOffset(48)] public uint totalCx;
    [FieldOffset(52)] public uint totalCy;
    [FieldOffset(56)] public uint videoStandard;
    [FieldOffset(60)] public uint tScanLineOrdering;
    // DISPLAYCONFIG_SOURCE_MODE
    [FieldOffset(16)] public uint srcWidth;
    [FieldOffset(20)] public uint srcHeight;
    [FieldOffset(24)] public uint srcPixelFormat;
    [FieldOffset(28)] public int srcX;
    [FieldOffset(32)] public int srcY;
    // DISPLAYCONFIG_DESKTOP_IMAGE_INFO
    [FieldOffset(16)] public int pathSourceCx;
    [FieldOffset(20)] public int pathSourceCy;
    [FieldOffset(24)] public int regionL;
    [FieldOffset(28)] public int regionT;
    [FieldOffset(32)] public int regionR;
    [FieldOffset(36)] public int regionB;
    [FieldOffset(40)] public int clipL;
    [FieldOffset(44)] public int clipT;
    [FieldOffset(48)] public int clipR;
    [FieldOffset(52)] public int clipB;
  }
  [StructLayout(LayoutKind.Sequential)] struct HEADER { public uint type, size; public LUID adapterId; public uint id; }
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct SOURCE_NAME {
    public HEADER header; [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string gdiName;
  }
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct TARGET_NAME {
    public HEADER header; public uint flags, outputTechnology; public ushort edidManufactureId, edidProductCodeId; public uint connectorInstance;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=64)] public string friendlyName;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=128)] public string monitorDevicePath;
  }
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct ADAPTER_NAME {
    public HEADER header; [MarshalAs(UnmanagedType.ByValTStr, SizeConst=128)] public string adapterDevicePath;
  }

  public const uint QDC_ONLY_ACTIVE_PATHS = 0x2, QDC_VIRTUAL_MODE_AWARE = 0x10;
  public const uint SDC_USE_DATABASE_CURRENT = 0x0F, SDC_USE_SUPPLIED_DISPLAY_CONFIG = 0x20, SDC_VALIDATE = 0x40,
                    SDC_APPLY = 0x80, SDC_SAVE_TO_DATABASE = 0x200, SDC_ALLOW_CHANGES = 0x400, SDC_VIRTUAL_MODE_AWARE = 0x8000;
  public const uint PATH_SUPPORT_VIRTUAL_MODE = 0x8, OUTPUT_INTERNAL = 0x80000000;
  public const uint SCALING_IDENTITY = 1, SCALING_ASPECTRATIOCENTEREDMAX = 4;

  [DllImport("user32.dll")] static extern int GetDisplayConfigBufferSizes(uint flags, out uint numPaths, out uint numModes);
  [DllImport("user32.dll")] static extern int QueryDisplayConfig(uint flags, ref uint numPaths, [Out] PATH_INFO[] paths, ref uint numModes, [Out] MODE_INFO[] modes, IntPtr topologyId);
  [DllImport("user32.dll")] static extern int SetDisplayConfig(uint numPaths, [In] PATH_INFO[] paths, uint numModes, [In] MODE_INFO[] modes, uint flags);
  [DllImport("user32.dll", EntryPoint="DisplayConfigGetDeviceInfo")] static extern int GetSourceName(ref SOURCE_NAME info);
  [DllImport("user32.dll", EntryPoint="DisplayConfigGetDeviceInfo")] static extern int GetTargetName(ref TARGET_NAME info);
  [DllImport("user32.dll", EntryPoint="DisplayConfigGetDeviceInfo")] static extern int GetAdapterName(ref ADAPTER_NAME info);
  [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr value);

  public class Config {
    public PATH_INFO[] Paths; public MODE_INFO[] Modes; public uint QueryFlags;
    public bool Virtual { get { return (QueryFlags & QDC_VIRTUAL_MODE_AWARE) != 0; } }
    public Config Clone() { return new Config { Paths = (PATH_INFO[])Paths.Clone(), Modes = (MODE_INFO[])Modes.Clone(), QueryFlags = QueryFlags }; }
  }

  public static Config Query(bool virtualAware) {
    uint flags = QDC_ONLY_ACTIVE_PATHS | (virtualAware ? QDC_VIRTUAL_MODE_AWARE : 0);
    uint np, nm;
    int r = GetDisplayConfigBufferSizes(flags, out np, out nm);
    if (r != 0) throw new Exception("GetDisplayConfigBufferSizes failed: " + r);
    var p = new PATH_INFO[np]; var m = new MODE_INFO[nm];
    r = QueryDisplayConfig(flags, ref np, p, ref nm, m, IntPtr.Zero);
    if (r != 0) throw new Exception("QueryDisplayConfig failed: " + r);
    Array.Resize(ref p, (int)np); Array.Resize(ref m, (int)nm);
    return new Config { Paths = p, Modes = m, QueryFlags = flags };
  }

  // In virtual-mode-aware configs mode indices are packed: source in the high 16 bits; target high 16, desktop image low 16.
  public static int SourceIdx(Config c, int pi) { uint v = c.Paths[pi].sourceInfo.modeInfoIdx; return (int)(c.Virtual ? v >> 16 : v); }
  public static int TargetIdx(Config c, int pi) { uint v = c.Paths[pi].targetInfo.modeInfoIdx; return (int)(c.Virtual ? v >> 16 : v); }
  public static int DesktopIdx(Config c, int pi) {
    if (!c.Virtual) return -1;
    int i = (int)(c.Paths[pi].targetInfo.modeInfoIdx & 0xFFFF);
    return (i < c.Modes.Length && c.Modes[i].infoType == 3) ? i : -1;
  }

  public class DisplayInfo {
    public int PathIndex; public string GdiName, FriendlyName, Output, Vendor, AdapterPath;
    public bool Internal, Primary, SupportsVirtualMode;
    public uint DesktopWidth, DesktopHeight, SignalWidth, SignalHeight; public double RefreshHz;
  }

  static string OutputName(uint t) {
    switch (t) {
      case 0: return "VGA"; case 4: return "DVI"; case 5: return "HDMI"; case 6: return "LVDS"; case 10: return "DisplayPort";
      case 11: return "Embedded DisplayPort"; case 13: return "Embedded UDI"; case 18: return "DisplayPort (USB tunnel)"; case 15: return "Miracast"; case 16: return "Indirect (wired)";
      case 17: return "Indirect (virtual)"; case OUTPUT_INTERNAL: return "Internal"; default: return "Other (" + t + ")";
    }
  }
  static string VendorName(string adapterPath) {
    string p = (adapterPath ?? "").ToUpperInvariant();
    if (p.Contains("VEN_8086")) return "Intel";
    if (p.Contains("VEN_10DE")) return "NVIDIA";
    if (p.Contains("VEN_1002") || p.Contains("VEN_1022")) return "AMD";
    if (p.Contains("VEN_1414")) return "Microsoft";
    if (p.Contains("VEN_5143") || p.Contains("QCOM")) return "Qualcomm";
    return "Unknown";
  }

  public static List<DisplayInfo> Describe(Config c) {
    var list = new List<DisplayInfo>();
    for (int i = 0; i < c.Paths.Length; i++) {
      var p = c.Paths[i]; var s = c.Modes[SourceIdx(c, i)]; var t = c.Modes[TargetIdx(c, i)];
      var sn = new SOURCE_NAME(); sn.header.type = 1; sn.header.size = (uint)Marshal.SizeOf(typeof(SOURCE_NAME)); sn.header.adapterId = p.sourceInfo.adapterId; sn.header.id = p.sourceInfo.id;
      var tn = new TARGET_NAME(); tn.header.type = 2; tn.header.size = (uint)Marshal.SizeOf(typeof(TARGET_NAME)); tn.header.adapterId = p.targetInfo.adapterId; tn.header.id = p.targetInfo.id;
      var an = new ADAPTER_NAME(); an.header.type = 4; an.header.size = (uint)Marshal.SizeOf(typeof(ADAPTER_NAME)); an.header.adapterId = p.targetInfo.adapterId;
      GetSourceName(ref sn); GetTargetName(ref tn); GetAdapterName(ref an);
      bool internalPanel = p.targetInfo.outputTechnology == OUTPUT_INTERNAL || p.targetInfo.outputTechnology == 11 || p.targetInfo.outputTechnology == 13 || p.targetInfo.outputTechnology == 6;
      list.Add(new DisplayInfo {
        PathIndex = i, GdiName = sn.gdiName, FriendlyName = string.IsNullOrEmpty(tn.friendlyName) ? (internalPanel ? "Built-in display" : "Display") : tn.friendlyName,
        Output = OutputName(p.targetInfo.outputTechnology), Internal = internalPanel, Primary = s.srcX == 0 && s.srcY == 0,
        AdapterPath = an.adapterDevicePath, Vendor = VendorName(an.adapterDevicePath),
        SupportsVirtualMode = c.Virtual && (p.flags & PATH_SUPPORT_VIRTUAL_MODE) != 0 && DesktopIdx(c, i) >= 0,
        DesktopWidth = s.srcWidth, DesktopHeight = s.srcHeight, SignalWidth = t.activeCx, SignalHeight = t.activeCy,
        RefreshHz = t.vSyncFreq.Den == 0 ? 0 : (double)t.vSyncFreq.Num / t.vSyncFreq.Den
      });
    }
    return list;
  }

  // Virtual mode: desktop of w x h is fitted (aspect ratio preserved, centered) into the display's native-size surface.
  public static Config WithVirtualDesktop(Config c, int pi, uint w, uint h) {
    var r = c.Clone(); int si = SourceIdx(r, pi), di = DesktopIdx(r, pi);
    if (di < 0) throw new Exception("This display path does not expose a desktop image (no virtual mode support).");
    int pw = r.Modes[di].pathSourceCx, ph = r.Modes[di].pathSourceCy;
    double scale = Math.Min((double)pw / w, (double)ph / h);
    int rw = (int)Math.Round(w * scale), rh = (int)Math.Round(h * scale);
    int left = (pw - rw) / 2, top = (ph - rh) / 2;
    r.Modes[si].srcWidth = w; r.Modes[si].srcHeight = h;
    r.Modes[di].regionL = left; r.Modes[di].regionT = top; r.Modes[di].regionR = left + rw; r.Modes[di].regionB = top + rh;
    r.Modes[di].clipL = 0; r.Modes[di].clipT = 0; r.Modes[di].clipR = (int)w; r.Modes[di].clipB = (int)h;
    return r;
  }
  // Fallback for drivers without virtual modes: larger source mode, driver/GPU scaler does the downscale.
  public static Config WithLegacyDesktop(Config c, int pi, uint w, uint h, uint scaling) {
    var r = c.Clone(); int si = SourceIdx(r, pi);
    r.Modes[si].srcWidth = w; r.Modes[si].srcHeight = h; r.Paths[pi].targetInfo.scaling = scaling;
    return r;
  }

  static uint Flags(Config c, uint baseFlags) { return baseFlags | SDC_USE_SUPPLIED_DISPLAY_CONFIG | SDC_ALLOW_CHANGES | (c.Virtual ? SDC_VIRTUAL_MODE_AWARE : 0); }
  public static int Apply(Config c, bool save) { return SetDisplayConfig((uint)c.Paths.Length, c.Paths, (uint)c.Modes.Length, c.Modes, Flags(c, SDC_APPLY | (save ? SDC_SAVE_TO_DATABASE : 0))); }
  public static int Validate(Config c) { return SetDisplayConfig((uint)c.Paths.Length, c.Paths, (uint)c.Modes.Length, c.Modes, Flags(c, SDC_VALIDATE)); }
  // Re-applies the configuration Windows has saved for the connected displays.
  public static int ApplySaved() { return SetDisplayConfig(0, null, 0, null, SDC_APPLY | SDC_USE_DATABASE_CURRENT); }
}
