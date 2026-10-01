using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Web.Script.Serialization;

namespace IntelVirtualDisplay
{
    public class DisplaySetting { public string Name; public int Width; public int Height; }

    public class Settings
    {
        public bool KeepOnMonitorChange = true;
        public Dictionary<string, DisplaySetting> Displays = new Dictionary<string, DisplaySetting>();
    }

    public class Pending
    {
        public string Key, Name; public int Width, Height; public DateTime Deadline; public Vd.Config Config;
    }

    // All display state and actions. Every public member takes Gate, so the HTTP threads and the UI-thread timer
    // never interleave display changes.
    public static class Core
    {
        public const int ConfirmSeconds = 15;
        public static readonly string DataDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "intel-virtual-display");
        static readonly string SettingsPath = Path.Combine(DataDir, "settings.json");
        static readonly string LogPath = Path.Combine(DataDir, "log.txt");
        static readonly object Gate = new object();
        static readonly List<string> recent = new List<string>();
        static Settings settings;
        static Pending pending;

        // Raised (on whatever thread made the change) when the keeper or a timeout changed the display.
        public static event Action<string> Notify;

        public static void Init()
        {
            Directory.CreateDirectory(DataDir);
            lock (Gate)
            {
                settings = LoadSettings();
                if (settings == null)
                {
                    // First run: remember any display that is already running a non-native desktop, so it is kept.
                    settings = new Settings();
                    foreach (var d in Displays())
                        if (d.DesktopWidth != d.SignalWidth || d.DesktopHeight != d.SignalHeight)
                            settings.Displays[Key(d)] = new DisplaySetting { Name = d.FriendlyName, Width = (int)d.DesktopWidth, Height = (int)d.DesktopHeight };
                    SaveSettings();
                }
            }
        }

        public static string Key(Vd.DisplayInfo d) { return string.IsNullOrEmpty(d.MonitorPath) ? d.GdiName : d.MonitorPath; }

        public static List<Vd.DisplayInfo> Displays()
        {
            Vd.Config c;
            try { c = Vd.Query(true); } catch { c = Vd.Query(false); }
            return Vd.Describe(c);
        }

        // ---------- log ----------
        public static void Log(string s)
        {
            string line = DateTime.Now.ToString("o") + " " + s;
            lock (recent) { recent.Add(DateTime.Now.ToString("HH:mm:ss") + "  " + s); if (recent.Count > 60) recent.RemoveAt(0); }
            try { File.AppendAllText(LogPath, line + Environment.NewLine); } catch { }
        }
        public static string[] RecentLog() { lock (recent) { return recent.ToArray(); } }

        // ---------- settings ----------
        static Settings LoadSettings()
        {
            try { return File.Exists(SettingsPath) ? new JavaScriptSerializer().Deserialize<Settings>(File.ReadAllText(SettingsPath)) : null; }
            catch (Exception e) { Log("Could not read settings (" + e.Message + "); starting fresh."); return null; }
        }
        static void SaveSettings() { File.WriteAllText(SettingsPath, new JavaScriptSerializer().Serialize(settings)); }

        public static bool KeepOnMonitorChange { get { lock (Gate) { return settings.KeepOnMonitorChange; } } }
        public static void SetKeep(bool on) { lock (Gate) { settings.KeepOnMonitorChange = on; SaveSettings(); } Log("Keep resolution when monitors change: " + (on ? "on" : "off")); }

        static void Remember(Vd.DisplayInfo d, int w, int h)
        {
            if (w == d.SignalWidth && h == d.SignalHeight) settings.Displays.Remove(Key(d));
            else settings.Displays[Key(d)] = new DisplaySetting { Name = d.FriendlyName, Width = w, Height = h };
            SaveSettings();
        }

        // ---------- applying ----------
        // Applies for this session only; returns the configuration that worked, or null with an error.
        static Vd.Config ApplyTransient(Vd.DisplayInfo target, int w, int h, out string error)
        {
            error = null;
            var attempts = new List<KeyValuePair<string, Vd.Config>>();
            if (target.SupportsVirtualMode)
                attempts.Add(new KeyValuePair<string, Vd.Config>("virtual mode", Vd.WithVirtualDesktop(Vd.Query(true), target.PathIndex, (uint)w, (uint)h)));
            var legacy = Vd.Query(false);
            var li = Vd.Describe(legacy).FirstOrDefault(x => x.GdiName == target.GdiName);
            bool native = w == target.SignalWidth && h == target.SignalHeight;
            if (li != null)
                attempts.Add(new KeyValuePair<string, Vd.Config>("driver scaling", Vd.WithLegacyDesktop(legacy, li.PathIndex, (uint)w, (uint)h, native ? Vd.SCALING_IDENTITY : Vd.SCALING_ASPECTRATIOCENTEREDMAX)));

            bool clamped = false;
            foreach (var a in attempts)
            {
                int r = Vd.Apply(a.Value, false);
                if (r != 0) { Log("Using " + a.Key + ", " + w + "x" + h + " was rejected (error " + r + ")."); continue; }
                System.Threading.Thread.Sleep(700);
                var now = Vd.Describe(Vd.Query(false)).FirstOrDefault(x => x.GdiName == target.GdiName);
                if (now != null && now.DesktopWidth == w && now.DesktopHeight == h) { Log("Applied " + w + "x" + h + " on " + target.FriendlyName + " using " + a.Key + "."); return a.Value; }
                clamped = true;
                Log("Using " + a.Key + ", Windows accepted " + w + "x" + h + " but kept " + (now == null ? "?" : now.DesktopWidth + "x" + now.DesktopHeight) + ".");
                Vd.ApplySaved();
            }
            error = "Windows would not run " + w + "x" + h + " on " + target.FriendlyName + ". The display is unchanged.";
            if (clamped && w > target.SignalWidth)
                error += " Larger-than-native desktops are not allowed on this display by its driver (on Intel this typically works on built-in panels only).";
            if (target.Vendor == "AMD") error += " AMD Software also offers Virtual Super Resolution.";
            if (target.Vendor == "NVIDIA") error += " NVIDIA Control Panel also offers DSR / DLDSR.";
            Log(error);
            return null;
        }

        static Vd.DisplayInfo Find(string key) { return Displays().FirstOrDefault(d => Key(d) == key); }

        // confirm = true: apply now, keep only if Confirm() is called within ConfirmSeconds.
        public static string Request(string key, int w, int h, bool confirm)
        {
            if (w < 640 || h < 480 || w > 16384 || h > 16384) return "Choose a size between 640x480 and 16384x16384.";
            lock (Gate)
            {
                if (pending != null) RevertLocked("replaced by a new request");
                var d = Find(key);
                if (d == null) return "That display is no longer connected.";
                string error;
                var cfg = ApplyTransient(d, w, h, out error);
                if (cfg == null) return error;
                if (confirm)
                    pending = new Pending { Key = key, Name = d.FriendlyName, Width = w, Height = h, Config = cfg, Deadline = DateTime.UtcNow.AddSeconds(ConfirmSeconds) };
                else { Vd.Apply(cfg, true); Remember(d, w, h); Log("Saved " + w + "x" + h + " for " + d.FriendlyName + "."); }
                return null;
            }
        }

        public static string Confirm()
        {
            lock (Gate)
            {
                if (pending == null) return "Nothing is waiting for confirmation.";
                int r = Vd.Apply(pending.Config, true);
                var d = Find(pending.Key);
                if (d != null) Remember(d, pending.Width, pending.Height);
                Log("Kept and saved " + pending.Width + "x" + pending.Height + " for " + pending.Name + " (result " + r + ").");
                pending = null;
                return null;
            }
        }

        public static void Revert() { lock (Gate) { if (pending != null) RevertLocked("reverted"); } }
        static void RevertLocked(string why)
        {
            int r = Vd.ApplySaved();
            Log("Change to " + pending.Width + "x" + pending.Height + " " + why + "; restored the saved setting (result " + r + ").");
            pending = null;
        }

        public static object PendingInfo()
        {
            lock (Gate)
            {
                if (pending == null) return null;
                return new { key = pending.Key, width = pending.Width, height = pending.Height,
                             secondsLeft = Math.Max(0, (int)Math.Ceiling((pending.Deadline - DateTime.UtcNow).TotalSeconds)) };
            }
        }
        public static bool HasPending { get { lock (Gate) { return pending != null; } } }

        public static DisplaySetting Desired(Vd.DisplayInfo d) { lock (Gate) { DisplaySetting s; return settings.Displays.TryGetValue(Key(d), out s) ? s : null; } }

        // ---------- timer: confirmation timeout + keeper ----------
        static string lastSignature;
        static int stableTicks;
        static bool enforcePending = true;

        // Called every second on the UI thread.
        public static void Tick(int tick)
        {
            string note = null;
            lock (Gate)
            {
                if (pending != null)
                {
                    if (DateTime.UtcNow > pending.Deadline) { RevertLocked("was not confirmed in " + ConfirmSeconds + " s"); note = "Resolution change reverted (not confirmed)."; }
                }
                else if (tick % 2 == 0) note = KeeperLocked();
            }
            if (note != null && Notify != null) Notify(note);
        }

        // Re-applies remembered resolutions after the set of connected monitors changes (once it has settled for
        // ~4 s), because Windows stores display settings per monitor combination. If the user changes a resolution
        // elsewhere (Windows Settings) while the monitors stay the same, that choice is adopted instead of fought.
        static string KeeperLocked()
        {
            var displays = Displays();
            string sig = string.Join("|", displays.Select(Key).OrderBy(x => x).ToArray());
            if (sig != lastSignature) { lastSignature = sig; stableTicks = 0; enforcePending = true; return null; }
            stableTicks++;
            if (enforcePending)
            {
                if (stableTicks < 2) return null;
                enforcePending = false; stableTicks = 0;
                if (!settings.KeepOnMonitorChange) return null;
                var changed = new List<string>();
                foreach (var d in displays)
                {
                    DisplaySetting want;
                    if (!settings.Displays.TryGetValue(Key(d), out want)) continue;
                    if (d.DesktopWidth == want.Width && d.DesktopHeight == want.Height) continue;
                    string error;
                    var cfg = ApplyTransient(d, want.Width, want.Height, out error);
                    if (cfg != null) { Vd.Apply(cfg, true); changed.Add(want.Width + "x" + want.Height + " on " + d.FriendlyName); }
                }
                if (changed.Count == 0) return null;
                Log("Monitors changed; re-applied " + string.Join(", ", changed) + ".");
                return "Re-applied " + string.Join(", ", changed) + ".";
            }
            if (stableTicks >= 3)
            {
                foreach (var d in displays)
                {
                    DisplaySetting want;
                    bool has = settings.Displays.TryGetValue(Key(d), out want);
                    bool nonNative = d.DesktopWidth != d.SignalWidth || d.DesktopHeight != d.SignalHeight;
                    if (has ? (d.DesktopWidth != want.Width || d.DesktopHeight != want.Height) : nonNative)
                    {
                        Remember(d, (int)d.DesktopWidth, (int)d.DesktopHeight);
                        Log("Noticed " + d.DesktopWidth + "x" + d.DesktopHeight + " on " + d.FriendlyName + " was set outside the app; remembering it.");
                    }
                }
            }
            return null;
        }
    }
}
