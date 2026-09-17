// reachy-fps serves a first-person teleoperation page for Reachy Mini.
//
// The binary embeds the web UI and proxies two things to the robot so the
// browser only ever talks to one origin:
//
//	/api/*       -> Reachy Mini daemon (REST + motion/state WebSockets)
//	/signalling  -> GStreamer webrtcsink signalling server
//
// Video and audio flow peer-to-peer between the browser and the robot over
// WebRTC; nothing media-related passes through this process.
package main

import (
	"embed"
	"flag"
	"fmt"
	"io/fs"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"time"
)

//go:embed web
var webFS embed.FS

func main() {
	listen := flag.String("listen", "0.0.0.0:8080", "address to serve the UI on (voice passthrough needs the page on localhost or HTTPS, e.g. via `tailscale serve`)")
	daemon := flag.String("daemon", "http://localhost:8000", "Reachy Mini daemon URL (Wireless: http://reachy-mini.local:8000)")
	signalling := flag.String("signalling", "", "WebRTC signalling URL (default: daemon host, port 8443)")
	openBrowser := flag.Bool("open", os.Getenv("SSH_CONNECTION") == "", "open the UI in the default browser (off by default over SSH)")
	flag.Parse()

	daemonURL, err := parseHTTPURL(*daemon)
	if err != nil {
		log.Fatalf("invalid -daemon: %v", err)
	}
	if *signalling == "" {
		*signalling = "http://" + net.JoinHostPort(daemonURL.Hostname(), "8443")
	}
	signallingURL, err := parseHTTPURL(*signalling)
	if err != nil {
		log.Fatalf("invalid -signalling: %v", err)
	}

	web, err := fs.Sub(webFS, "web")
	if err != nil {
		log.Fatal(err)
	}

	mux := http.NewServeMux()
	mux.Handle("/api/", newProxy(daemonURL, ""))
	mux.Handle("/signalling", newProxy(signallingURL, "/"))
	mux.Handle("/", noCache(http.FileServer(http.FS(web))))

	ln, err := net.Listen("tcp", *listen)
	if err != nil {
		log.Fatalf("listen %s: %v", *listen, err)
	}
	// Browsers treat localhost as a secure context (microphone allowed), so
	// open that locally even when listening on all interfaces.
	host, port, _ := net.SplitHostPort(ln.Addr().String())
	uiURL := "http://" + ln.Addr().String()
	if ip := net.ParseIP(host); ip != nil && (ip.IsLoopback() || ip.IsUnspecified()) {
		uiURL = "http://localhost:" + port
	}

	checkDaemon(daemonURL)
	log.Printf("daemon:     %s", daemonURL)
	log.Printf("signalling: %s", signallingURL)
	log.Printf("UI:         %s (listening on %s)", uiURL, ln.Addr())
	if host != "127.0.0.1" && host != "::1" {
		log.Printf("remote:     voice needs HTTPS off-machine; e.g. `tailscale serve --bg %s`", port)
	}

	if *openBrowser {
		go launchBrowser(uiURL)
	}
	log.Fatal(http.Serve(ln, mux))
}

// parseHTTPURL accepts http(s):// or ws(s):// URLs, or a bare host:port.
func parseHTTPURL(raw string) (*url.URL, error) {
	if !strings.Contains(raw, "://") {
		raw = "http://" + raw
	}
	u, err := url.Parse(raw)
	if err != nil {
		return nil, err
	}
	switch u.Scheme {
	case "ws":
		u.Scheme = "http"
	case "wss":
		u.Scheme = "https"
	case "http", "https":
	default:
		return nil, fmt.Errorf("unsupported scheme %q", u.Scheme)
	}
	if u.Host == "" {
		return nil, fmt.Errorf("missing host in %q", raw)
	}
	return u, nil
}

// newProxy forwards requests (including WebSocket upgrades) to target.
// A non-empty path replaces the request path.
func newProxy(target *url.URL, path string) *httputil.ReverseProxy {
	return &httputil.ReverseProxy{
		Rewrite: func(r *httputil.ProxyRequest) {
			r.SetURL(target)
			if path != "" {
				r.Out.URL.Path = path
				r.Out.URL.RawPath = ""
			}
			// The upstream servers don't know about us; present the
			// request as if it came straight from a local client.
			r.Out.Header.Del("Origin")
		},
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			log.Printf("proxy %s: %v", r.URL.Path, err)
			http.Error(w, "robot unreachable: "+err.Error(), http.StatusBadGateway)
		},
	}
}

func noCache(h http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-cache")
		h.ServeHTTP(w, r)
	})
}

func checkDaemon(u *url.URL) {
	client := http.Client{Timeout: 2 * time.Second}
	resp, err := client.Get(u.JoinPath("/api/daemon/status").String())
	if err != nil {
		log.Printf("warning: daemon not reachable at %s (%v); the UI will keep retrying", u, err)
		return
	}
	resp.Body.Close()
}

func launchBrowser(u string) {
	time.Sleep(300 * time.Millisecond)
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "darwin":
		cmd = exec.Command("open", u)
	case "windows":
		cmd = exec.Command("rundll32", "url.dll,FileProtocolHandler", u)
	default:
		cmd = exec.Command("xdg-open", u)
	}
	if err := cmd.Start(); err != nil {
		log.Printf("could not open browser: %v", err)
	}
}
