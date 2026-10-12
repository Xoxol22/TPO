"""Serve the existing webui-vue build and proxy its API calls to QEMU."""
import gzip
import http.client
import http.server
import mimetypes
import os
import socket
import ssl
from pathlib import Path
from urllib.parse import urlsplit


WEB_ROOT = Path(os.environ.get("WEBUI_ROOT", "/opt/openbmc/webui")).resolve()
BMC = urlsplit(os.environ.get("BMC_URL", "https://127.0.0.1:2443"))
PORT = int(os.environ.get("WEBUI_PORT", "8100"))
PROXY_PREFIXES = ("/redfish", "/login", "/logout", "/kvm", "/download", "/xyz")
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade", "content-length"}


class WebServer(http.server.ThreadingHTTPServer):
    def server_bind(self):
        # Same loopback MSS workaround as the original lab4 fix_loopback.c.
        # Set before listen so browser connections inherit the segment limit.
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_MAXSEG, 1460)
        super().server_bind()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            # Navigation/driver.quit may cancel pending API responses.
            self.close_connection = True

    def do_GET(self):
        if self.path.startswith(PROXY_PREFIXES):
            self.proxy()
        else:
            self.serve_file()

    def serve_file(self):
        relative = urlsplit(self.path).path.lstrip("/") or "index.html"
        candidate = (WEB_ROOT / relative).resolve()
        if not candidate.is_relative_to(WEB_ROOT):
            self.send_error(403)
            return
        if not candidate.is_file():
            self.send_error(404)
            return
        data = candidate.read_bytes()
        compressed = len(data) > 1024 and "gzip" in self.headers.get("Accept-Encoding", "")
        if compressed:
            data = gzip.compress(data)
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        self.end_headers()
        self.wfile.write(data)

    def proxy(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        context = ssl.create_default_context()
        if os.environ.get("BMC_VERIFY_TLS", "1") == "0":
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        connection = http.client.HTTPSConnection(BMC.hostname, BMC.port or 443, context=context, timeout=30)
        headers = {key: value for key, value in self.headers.items()
                   if key.lower() not in HOP_HEADERS | {"host", "accept-encoding"}}
        headers["Host"] = BMC.netloc
        try:
            connection.request(self.command, self.path, body=body or None, headers=headers)
            response = connection.getresponse()
            payload = response.read()
        except (OSError, http.client.HTTPException) as error:
            self.log_error("BMC proxy failed: %s", error)
            self.send_error(502, "OpenBMC backend unavailable")
            return
        finally:
            connection.close()
        self.send_response(response.status)
        for key, value in response.getheaders():
            if key.lower() not in HOP_HEADERS | {"strict-transport-security"}:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_POST = do_PATCH = do_PUT = do_DELETE = proxy


if __name__ == "__main__":
    if not (WEB_ROOT / "index.html").is_file():
        raise SystemExit(f"Missing webui-vue build: {WEB_ROOT}/index.html")
    if BMC.scheme != "https" or not BMC.hostname:
        raise SystemExit("BMC_URL must be an https:// URL")
    server = WebServer(("127.0.0.1", PORT), Handler)
    print(f"Web UI: http://127.0.0.1:{PORT}; backend: {BMC.geturl()}", flush=True)
    server.serve_forever()
