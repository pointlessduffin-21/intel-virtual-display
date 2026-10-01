using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace IntelVirtualDisplay
{
    // Minimal HTTP server on 127.0.0.1 for the UI. Static files come from embedded resources; /api/* needs the
    // per-run token (sent in the X-IVD-Token header) and a loopback Host header, so web pages cannot drive it.
    public class Server
    {
        public int Port { get; private set; }
        public string Token { get; private set; }
        public Action OnOpenRequest;
        TcpListener listener;
        static readonly JavaScriptSerializer Json = new JavaScriptSerializer();

        public void Start()
        {
            var bytes = new byte[24]; using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(bytes);
            Token = BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant();
            listener = new TcpListener(IPAddress.Loopback, 0);
            listener.Start();
            Port = ((IPEndPoint)listener.LocalEndpoint).Port;
            var t = new Thread(AcceptLoop) { IsBackground = true, Name = "http" };
            t.Start();
        }

        public string Url { get { return "http://127.0.0.1:" + Port + "/#" + Token; } }

        void AcceptLoop()
        {
            while (true)
            {
                TcpClient c;
                try { c = listener.AcceptTcpClient(); } catch { return; }
                ThreadPool.QueueUserWorkItem(_ => { try { Handle(c); } catch (Exception e) { Core.Log("HTTP error: " + e.Message); } finally { c.Close(); } });
            }
        }

        void Handle(TcpClient client)
        {
            client.ReceiveTimeout = 5000;
            var stream = client.GetStream();
            var head = new MemoryStream();
            int matched = 0;
            while (matched < 4)
            {
                int b = stream.ReadByte();
                if (b < 0) return;
                head.WriteByte((byte)b);
                matched = (b == (matched % 2 == 0 ? '\r' : '\n')) ? matched + 1 : (b == '\r' ? 1 : 0);
                if (head.Length > 32768) return;
            }
            var lines = Encoding.ASCII.GetString(head.ToArray()).Split(new[] { "\r\n" }, StringSplitOptions.None);
            var first = lines[0].Split(' ');
            if (first.Length < 2) return;
            string method = first[0], path = first[1].Split('?')[0];
            var headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (var l in lines.Skip(1)) { int i = l.IndexOf(':'); if (i > 0) headers[l.Substring(0, i).Trim()] = l.Substring(i + 1).Trim(); }

            string host;
            headers.TryGetValue("Host", out host);
            if (host != "127.0.0.1:" + Port && host != "localhost:" + Port) { Send(stream, 421, "text/plain", Encoding.UTF8.GetBytes("Wrong host")); return; }

            string body = "";
            string len;
            if (headers.TryGetValue("Content-Length", out len))
            {
                int n = Math.Min(int.Parse(len), 65536);
                var buf = new byte[n]; int read = 0;
                while (read < n) { int r = stream.Read(buf, read, n - read); if (r <= 0) break; read += r; }
                body = Encoding.UTF8.GetString(buf, 0, read);
            }

            if (path.StartsWith("/api/"))
            {
                string token;
                if (!headers.TryGetValue("X-IVD-Token", out token) || token != Token) { SendJson(stream, 403, new { error = "Forbidden" }); return; }
                if (method != "GET" && method != "POST") { SendJson(stream, 405, new { error = "Method not allowed" }); return; }
                SendJson(stream, 200, Api(method, path, body));
                return;
            }
            if (method != "GET") { Send(stream, 405, "text/plain", Encoding.UTF8.GetBytes("Method not allowed")); return; }
            ServeStatic(stream, path == "/" ? "/index.html" : path);
        }

        object Api(string method, string path, string body)
        {
            var args = string.IsNullOrEmpty(body) ? new Dictionary<string, object>() : Json.Deserialize<Dictionary<string, object>>(body);
            Func<string, object> arg = k => args.ContainsKey(k) ? args[k] : null;
            switch (path)
            {
                case "/api/state": return State();
                case "/api/apply":
                    return Result(Core.Request(Convert.ToString(arg("key")), Convert.ToInt32(arg("width")), Convert.ToInt32(arg("height")), !(arg("confirm") is bool) || (bool)arg("confirm")));
                case "/api/confirm": return Result(Core.Confirm());
                case "/api/revert": Core.Revert(); return Result(null);
                case "/api/settings":
                    if (arg("keep") is bool) Core.SetKeep((bool)arg("keep"));
                    if (arg("startWithWindows") is bool) Startup.Set((bool)arg("startWithWindows"));
                    return Result(null);
                case "/api/open-settings":
                    System.Diagnostics.Process.Start("ms-settings:display"); return Result(null);
                default: return new { error = "Unknown endpoint" };
            }
        }

        static object Result(string error) { return error == null ? (object)new { ok = true } : new { ok = false, error = error }; }

        static object State()
        {
            var displays = Core.Displays().Select(d =>
            {
                var want = Core.Desired(d);
                return new
                {
                    key = Core.Key(d), gdi = d.GdiName, name = d.FriendlyName, output = d.Output, vendor = d.Vendor,
                    builtIn = d.Internal, primary = d.Primary, virtualModes = d.SupportsVirtualMode,
                    desktopWidth = d.DesktopWidth, desktopHeight = d.DesktopHeight,
                    nativeWidth = d.SignalWidth, nativeHeight = d.SignalHeight, refresh = Math.Round(d.RefreshHz),
                    remembered = want == null ? null : new { width = want.Width, height = want.Height }
                };
            }).OrderByDescending(d => d.builtIn).ThenByDescending(d => d.primary).ToList();
            return new
            {
                displays = displays, pending = Core.PendingInfo(), confirmSeconds = Core.ConfirmSeconds,
                settings = new { keep = Core.KeepOnMonitorChange, startWithWindows = Startup.IsEnabled },
                log = Core.RecentLog().Reverse().Take(30).ToArray(),
                version = Assembly.GetExecutingAssembly().GetName().Version.ToString(3)
            };
        }

        static readonly Dictionary<string, string> Types = new Dictionary<string, string>
        {
            { ".html", "text/html; charset=utf-8" }, { ".css", "text/css; charset=utf-8" }, { ".js", "text/javascript; charset=utf-8" },
            { ".woff2", "font/woff2" }, { ".svg", "image/svg+xml" }, { ".ico", "image/x-icon" }, { ".png", "image/png" }
        };

        static void ServeStatic(Stream s, string path)
        {
            string name = "ui" + path.Replace('\\', '/');
            if (name.Contains("..")) { Send(s, 404, "text/plain", Encoding.UTF8.GetBytes("Not found")); return; }
            using (var res = Assembly.GetExecutingAssembly().GetManifestResourceStream(name))
            {
                if (res == null) { Send(s, 404, "text/plain", Encoding.UTF8.GetBytes("Not found")); return; }
                var ms = new MemoryStream(); res.CopyTo(ms);
                string type;
                if (!Types.TryGetValue(Path.GetExtension(name).ToLowerInvariant(), out type)) type = "application/octet-stream";
                Send(s, 200, type, ms.ToArray());
            }
        }

        static void SendJson(Stream s, int status, object o) { Send(s, status, "application/json; charset=utf-8", Encoding.UTF8.GetBytes(Json.Serialize(o))); }

        static void Send(Stream s, int status, string type, byte[] data)
        {
            string reason = status == 200 ? "OK" : status == 403 ? "Forbidden" : status == 404 ? "Not Found" : status == 405 ? "Method Not Allowed" : "Misdirected Request";
            var h = new StringBuilder();
            h.Append("HTTP/1.1 ").Append(status).Append(' ').Append(reason).Append("\r\n");
            h.Append("Content-Type: ").Append(type).Append("\r\n");
            h.Append("Content-Length: ").Append(data.Length).Append("\r\n");
            h.Append("Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\n");
            h.Append("Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'\r\n");
            h.Append("Connection: close\r\n\r\n");
            var hb = Encoding.ASCII.GetBytes(h.ToString());
            s.Write(hb, 0, hb.Length); s.Write(data, 0, data.Length); s.Flush();
        }
    }
}
