"""A stub of the barcode decoder (API v1, INTEGRATION_PLAN.md 5.9) on an ephemeral 127.0.0.1 port, stdlib only, for
the client tests: every answer is scripted, every request recorded. Nothing is decoded.

  stub = StubDecoder()                  # stub.url: "http://127.0.0.1:<port>"
  stub.codes(["M1ROSSI/MARIO ..."])     # 200 in the API's shape
  stub.error(422, "too_many_pixels", width=9000, height=9000)
  stub.answer(503, raw=b"busy")         # any code, a JSON body or raw bytes, headers, a delay
  stub.requests                         # [{"method", "path", "query", "content_type", "length", "body"}]
  stub.close()
"""
import json, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def decode_body(texts, kind="png"):
    """A 200 body of POST /v1/decode with these texts, in order."""
    return {"api": 1, "type": kind, "width": 16, "height": 16, "pages": None, "truncated": False, "elapsed_ms": 1,
            "barcodes": [{"page": None, "format": "Aztec", "text": t, "bytes_b64": "", "content_type": "Text",
                          "position": "0x0 1x0 1x1 0x1", "orientation": 0, "symbology_identifier": "]z0",
                          "valid": True} for t in texts]}


class StubDecoder:
    def __init__(self):
        self.requests = []
        self.reply = (200, decode_body([]), {}, 0.0)
        self.health = (200, {"ok": True, "api": 1, "name": "barcode-decoder", "version": "1.0.0"})
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                stub.record(self, b"")
                code, body = stub.health
                self.send(code, json.dumps(body).encode() if not isinstance(body, bytes) else body, {})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                stub.record(self, self.rfile.read(n))
                code, body, headers, delay = stub.reply
                if delay:
                    time.sleep(delay)
                self.send(code, body if isinstance(body, bytes) else json.dumps(body).encode(), headers)

            def send(self, code, b, headers):
                try:
                    self.send_response(code)
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(b)))
                    self.end_headers()
                    self.wfile.write(b)
                except OSError:
                    pass                                # the client gave up (its timeout)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def record(self, h, body):
        u = urllib.parse.urlsplit(h.path)
        self.requests.append({"method": h.command, "path": u.path, "query": urllib.parse.parse_qs(u.query),
                              "content_type": h.headers.get("Content-Type"), "length": len(body), "body": body})

    def answer(self, code, body=None, raw=None, headers=None, delay=0.0):
        self.reply = (code, raw if raw is not None else body, headers or {}, delay)

    def codes(self, texts, kind="png"):
        self.answer(200, decode_body(texts, kind))

    def error(self, code, error_code, headers=None, **extra):
        self.answer(code, {"error": {"code": error_code, "message": error_code, **extra}}, headers=headers)

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
