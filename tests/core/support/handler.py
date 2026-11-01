"""The standalone's HTTP handler (tracker/engine.py Handler, E:1692-1795) on top of the ported core, for the copied
tests and the L1 harness only: the same routes, the Tailscale-User-Login identity with its Host and Origin checks,
the 403 texts of the tailnet, the Server header and the body reads, with the logic in api.py. In Home Assistant
the views of views.py serve api.py instead (HA's auth, the HA user mapped to a person).

  srv = handler.ThreadingHTTPServer(("127.0.0.1", 0), handler.Handler)    # Handler.cfg = {"users": {login: user}}
"""
import json, re, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: F401  (re-exported for the tests)

import support  # noqa: F401  (the ported core on sys.path)
import api, engine, ha, manual  # noqa: E402

# Allowed hosts: a tailnet name (tailscale serve forwards the node's) or the loopback, with or without a port
_HOST = re.compile(r"(127\.0\.0\.1|localhost|[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net)(?::\d{1,5})?", re.I)
FORBIDDEN_GET = "Non so chi sei: passa dal tailnet."
FORBIDDEN_POST = "Non so chi sei: la Scorciatoia deve passare dal tailnet."


class Handler(BaseHTTPRequestHandler):
    server_version = ha.PROJECT
    sys_version = ""
    cfg = None

    def log_message(self, fmt, *args):
        pass                                        # no headers or addresses in the log: reply() logs

    def reply(self, code, body):
        b = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
        api.log_reply(self.command, self.path, code, body)

    def user(self):
        # no web pages: the Shortcut sends no Origin. A page doing DNS rebinding on 127.0.0.1:<port> can GET
        # without Origin, but with its own name in Host
        if self.headers.get("Origin") or not _HOST.fullmatch(self.headers.get("Host") or ""):
            return None
        return self.cfg["users"].get(self.headers.get("Tailscale-User-Login", ""))

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            return self.reply(*api.health())
        if path in ("/documents", "/flights", "/choices"):
            u = self.user()
            if not u:
                return self.reply(403, {"message": FORBIDDEN_GET})
            return self.reply(*{"/documents": api.documents, "/flights": api.flights, "/choices": api.choices}[path](u))
        self.reply(404, {"message": api.TEXTS["not_found"]})

    def do_POST(self):
        if self.path == "/flight":
            return self.flight_request()
        if self.path != "/upload":
            return self.reply(404, {"message": api.TEXTS["not_found"]})
        u = self.user()
        if not u:
            return self.reply(403, {"message": FORBIDDEN_POST})
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if not 0 < n <= api.MAX_BODY:
            return self.reply(413 if n else 400, {"message": api.TEXTS["empty_or_too_large"]})
        self.reply(*api.upload(self.headers.get("Content-Type"), self.rfile.read(n), u))

    def flight_request(self):
        """POST /flight (api.flight): a JSON body of at most manual.MAX_JSON_BODY bytes, which must arrive whole
        within manual.BODY_TIMEOUT_S seconds (otherwise no reply: the connection is closed)."""
        u = self.user()
        if not u:
            return self.reply(403, {"message": FORBIDDEN_POST})
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if not 0 < n <= manual.MAX_JSON_BODY:
            return self.reply(413 if n else 400, {"message": manual.text("body_size")})
        # one deadline for the whole body, not for each read: a client sending a byte now and then is cut off too
        deadline, body = time.monotonic() + manual.BODY_TIMEOUT_S, b""
        try:
            while len(body) < n:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError
                self.connection.settimeout(left)
                chunk = self.rfile.read1(n - len(body))
                if not chunk:                       # closed before the end: what came is answered, as read(n) did
                    break
                body += chunk
        except OSError as e:                        # TimeoutError: a client that stopped sending
            self.close_connection = True
            engine.log(f"POST /flight: body not received ({type(e).__name__})")
            return
        self.reply(*api.flight(body, u, self.cfg))
