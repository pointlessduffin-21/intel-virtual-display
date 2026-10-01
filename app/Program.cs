using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("Intel Virtual Display")]
[assembly: AssemblyProduct("intel-virtual-display")]
[assembly: AssemblyDescription("Run a Windows desktop larger than your screen")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

namespace IntelVirtualDisplay
{
    // "Start with Windows": a shortcut to this exe (with --background) in the user's Startup folder.
    public static class Startup
    {
        static string ShortcutPath { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Startup), "Intel Virtual Display.lnk"); } }
        // The shortcut Install-Startup.ps1 creates; the app's own entry replaces it.
        static string ScriptShortcutPath { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Startup), "Virtual Resolution.lnk"); } }

        public static bool IsEnabled { get { return File.Exists(ShortcutPath); } }

        public static void Set(bool on)
        {
            if (on)
            {
                var shell = Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));
                dynamic lnk = shell.GetType().InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { ShortcutPath });
                lnk.TargetPath = Application.ExecutablePath;
                lnk.Arguments = "--background";
                lnk.WorkingDirectory = Path.GetDirectoryName(Application.ExecutablePath);
                lnk.Description = "Intel Virtual Display: keeps your desktop resolution";
                lnk.Save();
                if (File.Exists(ScriptShortcutPath)) { File.Delete(ScriptShortcutPath); Core.Log("Replaced the PowerShell startup entry with the app's own."); }
                Core.Log("Start with Windows: on");
            }
            else
            {
                if (File.Exists(ShortcutPath)) File.Delete(ShortcutPath);
                Core.Log("Start with Windows: off");
            }
        }
    }

    static class Program
    {
        static Server server;
        static NotifyIcon tray;
        // Desktop sizes offered as presets, as multiples of the display's native size. On a 1920x1080 panel these
        // are 2560x1440, 3200x1800, 3840x2160, 5120x2880 and 7680x4320.
        public static readonly double[] Factors = { 4.0 / 3, 5.0 / 3, 2, 8.0 / 3, 4 };

        [STAThread]
        static void Main(string[] args)
        {
            bool background = args.Any(a => a.Equals("--background", StringComparison.OrdinalIgnoreCase));
            Vd.SetProcessDpiAwarenessContext(new IntPtr(-4));   // per-monitor DPI aware: physical pixels everywhere
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            Directory.CreateDirectory(Core.DataDir);
            string instanceFile = Path.Combine(Core.DataDir, "instance.txt");
            bool first;
            using (var mutex = new Mutex(true, @"Local\IntelVirtualDisplay", out first))
            {
                if (!first)
                {
                    // Already running (usually in the tray): just open its window.
                    if (!background) { try { OpenUi(File.ReadAllText(instanceFile).Trim()); } catch { } }
                    return;
                }

                Core.Init();
                server = new Server();
                server.Start();
                File.WriteAllText(instanceFile, server.Url);
                Core.Log("App started" + (background ? " in the background" : "") + ".");

                tray = new NotifyIcon { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath), Text = "Intel Virtual Display", Visible = true };
                tray.MouseClick += (s, e) => { if (e.Button == MouseButtons.Left) OpenUi(server.Url); };
                var menu = new ContextMenuStrip();
                menu.Opening += (s, e) => { BuildMenu(menu); e.Cancel = false; };
                menu.Items.Add("Open");   // placeholder so the menu opens; rebuilt on every Opening
                tray.ContextMenuStrip = menu;
                Core.Notify += msg => tray.ShowBalloonTip(4000, "Intel Virtual Display", msg, ToolTipIcon.Info);

                int tick = 0;
                var timer = new System.Windows.Forms.Timer { Interval = 1000 };
                timer.Tick += (s, e) => { tick++; try { Core.Tick(tick); } catch (Exception ex) { Core.Log("Timer error: " + ex.Message); } };
                timer.Start();

                Application.ApplicationExit += (s, e) =>
                {
                    Core.Revert();   // never leave an unconfirmed change behind
                    tray.Visible = false;
                    try { File.Delete(instanceFile); } catch { }
                    Core.Log("App exited.");
                };
                if (!background) OpenUi(server.Url);
                Application.Run();
            }
        }

        static void BuildMenu(ContextMenuStrip menu)
        {
            menu.Items.Clear();
            var open = new ToolStripMenuItem("Open Intel Virtual Display", null, (s, e) => OpenUi(server.Url));
            open.Font = new Font(open.Font, FontStyle.Bold);
            menu.Items.Add(open);
            menu.Items.Add(new ToolStripSeparator());

            var displays = Core.Displays();
            var d = displays.FirstOrDefault(x => x.Internal) ?? displays.FirstOrDefault(x => x.Primary);
            if (d != null)
            {
                menu.Items.Add(new ToolStripMenuItem(d.FriendlyName + " (native " + d.SignalWidth + "x" + d.SignalHeight + ")") { Enabled = false });
                AddSize(menu, d, (int)d.SignalWidth, (int)d.SignalHeight, "Native");
                foreach (var f in Factors) AddSize(menu, d, Even(d.SignalWidth * f), Even(d.SignalHeight * f), null);
                menu.Items.Add(new ToolStripSeparator());
            }
            menu.Items.Add(new ToolStripMenuItem("Keep resolution when monitors change", null, (s, e) => Core.SetKeep(!Core.KeepOnMonitorChange)) { Checked = Core.KeepOnMonitorChange });
            menu.Items.Add(new ToolStripMenuItem("Start with Windows", null, (s, e) => Startup.Set(!Startup.IsEnabled)) { Checked = Startup.IsEnabled });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(new ToolStripMenuItem("Exit", null, (s, e) => Application.Exit()));
        }

        static int Even(double v) { return (int)Math.Round(v / 2) * 2; }

        static void AddSize(ContextMenuStrip menu, Vd.DisplayInfo d, int w, int h, string label)
        {
            string key = Core.Key(d);
            var item = new ToolStripMenuItem(w + " x " + h + (label == null ? "" : "  (" + label + ")"));
            item.Checked = d.DesktopWidth == w && d.DesktopHeight == h;
            item.Click += (s, e) =>
            {
                bool native = w == d.SignalWidth && h == d.SignalHeight;
                string error = Core.Request(key, w, h, !native);   // larger sizes need confirming in the window
                if (error != null) tray.ShowBalloonTip(6000, "Intel Virtual Display", error, ToolTipIcon.Warning);
                else if (!native) OpenUi(server.Url);
            };
            menu.Items.Add(item);
        }

        public static void OpenUi(string url)
        {
            string profile = Path.Combine(Core.DataDir, "window");
            string edge = FindEdge();
            try
            {
                if (edge != null)
                    Process.Start(edge, "--app=\"" + url + "\" --user-data-dir=\"" + profile + "\" --window-size=1180,880 --no-first-run --no-default-browser-check");
                else
                    Process.Start(url);
            }
            catch (Exception e) { Core.Log("Could not open the window: " + e.Message); }
        }

        static string FindEdge()
        {
            foreach (var root in new[] { Registry.CurrentUser, Registry.LocalMachine })
                using (var k = root.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"))
                {
                    var v = k == null ? null : k.GetValue(null) as string;
                    if (!string.IsNullOrEmpty(v) && File.Exists(v)) return v;
                }
            foreach (var pf in new[] { Environment.GetEnvironmentVariable("ProgramFiles(x86)"), Environment.GetEnvironmentVariable("ProgramFiles") })
            {
                if (pf == null) continue;
                string p = Path.Combine(pf, @"Microsoft\Edge\Application\msedge.exe");
                if (File.Exists(p)) return p;
            }
            return null;
        }
    }
}
