"""Cosmic Tracker server: serves index.html and relays requests to NASA/IPAC IRSA and CDS.
Run:  python nasa_server.py      then open  http://localhost:8765
Diagnose: http://localhost:8765/test
"""
import http.server, urllib.parse, urllib.request, urllib.error, ssl, os, time, threading
import xml.etree.ElementTree as ET

ALLOWED = ("irsa.ipac.caltech.edu", "cds.unistra.fr")
SIA = "https://irsa.ipac.caltech.edu/SIA"
COLLECTIONS = ["spherex_dr1", "spherex_qr3", "spherex_qr2", "spherex_qr"]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"}
PORT = int(os.environ.get("PORT", 8765))

GATE = threading.Semaphore(4)          # never hit IRSA with more than 4 requests at once
CACHE, CACHE_MAX = {}, 300             # small in-memory cache (url -> (type, bytes))
LOCK = threading.Lock()


def _open(url, timeout):
    req = urllib.request.Request(url, headers=UA)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except Exception as e:
        reason = getattr(e, "reason", None)
        if isinstance(reason, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(e):
            return urllib.request.urlopen(req, timeout=timeout, context=ssl._create_unverified_context())
        raise


def fetch_bytes(url, timeout=60, tries=3):
    """Download with retries and backoff (handles 429/5xx and timeouts). Returns (content_type, data)."""
    with LOCK:
        if url in CACHE:
            return CACHE[url]
    last = None
    for n in range(tries):
        try:
            with GATE:
                with _open(url, timeout) as r:
                    res = (r.headers.get("Content-Type", "application/octet-stream"), r.read())
            with LOCK:
                if len(CACHE) >= CACHE_MAX:
                    CACHE.pop(next(iter(CACHE)))
                CACHE[url] = res
            return res
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504):
                raise
        except Exception as e:
            last = e
        time.sleep(1.5 * (n + 1))
    raise last


def tag(e):
    return e.tag.split("}")[-1]


def run_test():
    out = []
    log = out.append
    try:
        for col in COLLECTIONS:
            url = SIA + "?COLLECTION=" + col + "&POS=circle+269.4521+4.6934+0.0003"
            try:
                ctype, data = fetch_bytes(url, tries=2)
            except Exception as e:
                log("[%s] request failed: %s" % (col, repr(e)[:200]))
                continue
            root = ET.fromstring(data)
            fields = [f.get("name") for f in root.iter() if tag(f) == "FIELD"]
            rows = [t for t in root.iter() if tag(t) == "TR"]
            log("[%s] HTTP ok, %d bytes, %d images" % (col, len(data), len(rows)))
            if rows and "access_url" in fields:
                tds = [td.text or "" for td in rows[0] if tag(td) == "TD"]
                img = tds[fields.index("access_url")].strip()
                cut = img + ("&" if "?" in img else "?") + "center=269.4521,4.6934d&size=0.2"
                _, b = fetch_bytes(cut)
                log("    cutout: %d bytes, starts with %r" % (len(b), b[:6]))
                log("RESULT: everything works with " + col)
                return "\n".join(out)
        log("RESULT: NASA answered, but no usable images. Check the lines above.")
    except Exception as e:
        log("FAILED: " + repr(e)[:500])
    return "\n".join(out)


class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/test":
            body = run_test().encode("utf-8")
            print(body.decode("utf-8"))
            return self._send(200, "text/plain; charset=utf-8", body)
        if u.path == "/api/collections":
            import json
            return self._send(200, "application/json", json.dumps({"collections": COLLECTIONS}).encode())
        if u.path == "/proxy":
            target = urllib.parse.parse_qs(u.query).get("u", [""])[0]
            p = urllib.parse.urlparse(target)
            if p.scheme != "https" or p.hostname not in ALLOWED:
                return self.send_error(403, "host not allowed")
            try:
                ctype, data = fetch_bytes(target)
                return self._send(200, ctype, data)
            except Exception as e:
                msg = repr(e).replace("\n", " ")[:300]
                print("PROXY ERROR:", msg, "| URL:", target[:200])
                return self.send_error(502, msg)
        if u.path == "/":
            self.path = "/index.html"
        super().do_GET()


if __name__ == "__main__":
    print("Cosmic Tracker server. Open: http://localhost:%d  (self-test: /test)" % PORT)
    http.server.ThreadingHTTPServer(("", PORT), H).serve_forever()
