"""The decoder client (barcodes.py) against a stub decoder on 127.0.0.1: every row of INTEGRATION_PLAN.md 5.10 (what
a decoder answer becomes), the request it sends, the status and the probe, and the guard-first rule. No network
beyond the loopback.

  python3 -I -B -m unittest discover -s tests/core -p test_barcodes_client.py -v
"""
import contextlib, io, json, os, shutil, socket, sys, tempfile, time, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402,F401  (the ported core on sys.path; its log lines to the current stdout)
from support import stub_decoder  # noqa: E402
from support.stub_decoder import StubDecoder  # noqa: E402
import barcodes as bc  # noqa: E402
import test_flights as tf  # noqa: E402
from PIL import Image  # noqa: E402

BCBP = tf.bcbp()


def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Client(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="bpt-test-client-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.stub = StubDecoder()
        self.addCleanup(self.stub.close)
        saved = bc.DECODER_URL
        bc.configure(self.stub.url)
        self.addCleanup(bc.configure, saved)
        self.png = os.path.join(self.dir, "photo.png")
        Image.new("RGB", (16, 16), "white").save(self.png)
        self.pdf = os.path.join(self.dir, "pass.pdf")
        with open(self.pdf, "wb") as f:
            f.write(b"%PDF-1.4\n%%EOF\n")
        self.output = io.StringIO()
        r = contextlib.redirect_stdout(self.output)
        r.__enter__()
        self.addCleanup(r.__exit__, None, None, None)

    def tearDown(self):
        self.assertNotIn(BCBP, self.output.getvalue())          # nothing decoded in the log

    def unavailable(self, path=None):
        with self.assertRaises(bc.DecoderUnavailable) as e:
            bc.read_barcodes(path or self.png)
        self.assertEqual(bc.status(), "down")
        self.assertIsNotNone(bc.down_since())
        return str(e.exception)

    # ------------------------------------------------ 200
    def test_codes_post_processed_as_today(self):
        self.stub.codes([BCBP, BCBP, "  ", f"  {BCBP}  ", "x" * 600, ""])
        self.assertEqual(bc.read_barcodes(self.png), [BCBP, "x" * 500])     # stripped, cut, no blank, no duplicate
        self.stub.codes([f"code {i}" for i in range(bc.MAX_CODES + 5)])
        self.assertEqual(bc.read_barcodes(self.png), [f"code {i}" for i in range(bc.MAX_CODES)])
        self.assertEqual(bc.status(), "ok")

    def test_no_code_found(self):
        self.stub.codes([])
        self.assertEqual(bc.read_barcodes(self.png), [])

    def test_the_request(self):
        self.stub.codes([BCBP])
        self.assertEqual(bc.read_barcodes(self.pdf), [BCBP])       # a PDF goes as it is: the decoder renders it
        (r,) = self.stub.requests
        with open(self.pdf, "rb") as f:
            self.assertEqual(r["body"], f.read())
        self.assertEqual((r["method"], r["path"], r["query"], r["content_type"]),
                         ("POST", "/v1/decode", {"pages": ["3"], "max_codes": ["50"]}, "application/octet-stream"))

    def test_no_proxy_from_the_environment(self):
        self.stub.codes([BCBP])
        with mock.patch.dict(os.environ, {"http_proxy": f"http://127.0.0.1:{closed_port()}",
                                          "HTTP_PROXY": f"http://127.0.0.1:{closed_port()}"}):
            self.assertEqual(bc.read_barcodes(self.png), [BCBP])
        self.assertEqual(len(self.stub.requests), 1)

    # ------------------------------------------------ the decoder's "not readable": [] (archived, as today)
    def test_not_readable_answers_are_archived(self):
        for code, error in ((422, "unreadable"), (504, "timeout"), (400, "empty_body"), (400, "bad_param"),
                            (413, "too_large"), (415, "unsupported_type")):
            with self.subTest(code=code, error=error):
                self.stub.error(code, error)
                self.assertEqual(bc.read_barcodes(self.png), [])
                self.assertEqual(bc.status(), "ok")
        self.stub.answer(415, raw=b"not json")                     # a 4xx of the input, even without the JSON
        self.assertEqual(bc.read_barcodes(self.png), [])

    def test_too_many_pixels_is_the_guard_text(self):
        self.stub.error(422, "too_many_pixels", width=9000, height=9000)
        with self.assertRaises(ValueError) as e:
            bc.read_barcodes(self.png)
        self.assertIs(type(e.exception), ValueError)
        self.assertEqual(str(e.exception), "immagine di 9000x9000 pixel: troppo grande")

    def test_internal_error_uses_an_attempt(self):
        self.stub.error(500, "internal")
        with self.assertRaises(RuntimeError) as e:
            bc.read_barcodes(self.png)
        self.assertEqual(str(e.exception), "il decodificatore non ha finito la lettura")
        self.assertNotIsInstance(e.exception, bc.DecoderUnavailable)

    # ------------------------------------------------ unavailable: the document waits
    def test_no_url(self):
        bc.configure(None)
        with self.assertRaises(bc.DecoderUnavailable):
            bc.read_barcodes(self.png)
        self.assertEqual((bc.status(), bc.down_since()), ("off", None))
        self.assertEqual(self.stub.requests, [])

    def test_refused(self):
        bc.configure(f"http://127.0.0.1:{closed_port()}")
        self.unavailable()

    def test_dns(self):
        bc.configure("http://bpt-wf-decoder.invalid:8099")
        with mock.patch.object(socket, "getaddrinfo", side_effect=socket.gaierror(-2, "Name or service not known")):
            self.unavailable()

    def test_client_timeout(self):
        self.stub.answer(200, {"barcodes": []}, delay=1.5)
        start = time.monotonic()
        with mock.patch.object(bc, "TIMEOUT_S", 0.3):
            self.unavailable()
        self.assertLess(time.monotonic() - start, 1.4)

    def test_busy_and_answers_that_are_not_the_decoders(self):
        cases = [(503, {"error": {"code": "busy", "message": "busy"}}, None), (503, None, b""),
                 (502, None, b"<html>bad gateway</html>"), (504, None, b"gateway timeout"), (500, None, b"oops"),
                 (404, None, b"not found"), (200, None, b"not json"), (200, None, b'{"barcodes": "x"}'),
                 (200, None, b"[1, 2]"), (302, None, b"")]
        for code, body, raw in cases:
            with self.subTest(code=code, raw=raw):
                bc.configure(self.stub.url)                         # up again
                headers = {"Location": "http://127.0.0.1:1/"} if code == 302 else None
                self.stub.answer(code, body, raw=raw, headers=headers)
                self.unavailable()
        self.assertEqual(sum(r["path"] == "/v1/decode" for r in self.stub.requests), len(cases))   # 302 not followed

    def test_a_long_answer_of_the_decoder_is_read_whole(self):
        """SECURITY-02: the 50 QR codes of 2,900 bytes 0xFF of one 51 KB sheet made the real decoder answer 1,072,930
        bytes; cut at the old 1 MiB that was "decoder down" and the queue stood still. Every 200 the API allows is
        read: 50 codes of 8,192 characters (escaped \\uXXXX) and their bytes_b64."""
        texts = ["ÿ" * 8192] * 49 + [BCBP]
        body = stub_decoder.decode_body(texts)
        for item in body["barcodes"]:
            item["bytes_b64"] = "A" * 10924
        raw = json.dumps(body).encode()
        self.assertGreater(len(raw), 2_900_000)
        self.assertLessEqual(len(raw), bc.MAX_REPLY)
        self.stub.answer(200, raw=raw)
        self.assertEqual(bc.read_barcodes(self.png), ["ÿ" * 500, BCBP])
        self.assertEqual(bc.status(), "ok")

    def test_a_200_over_max_reply_uses_an_attempt(self):
        """SECURITY-02: a 200 longer than MAX_REPLY (only a broken decoder sends one) is this file's failure, like a
        500: RuntimeError, one attempt used, the decoder still "ok" (so the queue goes on), not DecoderUnavailable."""
        big = json.dumps(stub_decoder.decode_body(["x" * 1000] * (bc.MAX_REPLY // 1000 + 10))).encode()
        self.assertGreater(len(big), bc.MAX_REPLY)
        self.stub.answer(200, raw=big)
        with self.assertRaises(RuntimeError) as e:
            bc.read_barcodes(self.png)
        self.assertNotIsInstance(e.exception, bc.DecoderUnavailable)
        self.assertEqual(str(e.exception), "il decodificatore non ha finito la lettura")
        self.assertEqual((bc.status(), bc.down_since()), ("ok", None))

    def test_the_decoders_200_without_a_barcodes_list_uses_an_attempt(self):
        for body in ({"api": 1, "type": "png", "barcodes": "x"}, {"api": 1, "type": "png"}):
            with self.subTest(body=body):
                self.stub.answer(200, body)
                with self.assertRaises(RuntimeError) as e:
                    bc.read_barcodes(self.png)
                self.assertNotIsInstance(e.exception, bc.DecoderUnavailable)
                self.assertEqual(bc.status(), "ok")
        for raw in (b'{"api": true, "barcodes": "x"}', b'{"api": 2, "barcodes": "x"}'):   # not the decoder's
            with self.subTest(raw=raw):
                bc.configure(self.stub.url)
                self.stub.answer(200, raw=raw)
                self.unavailable()

    # ------------------------------------------------ status and probe
    def test_status_down_since_and_recovery(self):
        port = closed_port()
        bc.configure(f"http://127.0.0.1:{port}")
        self.assertEqual((bc.status(), bc.down_since()), ("ok", None))     # not known to be down
        self.unavailable()
        first = bc.down_since()
        self.unavailable()
        self.assertEqual(bc.down_since(), first)                    # since the first failure in a row
        bc.configure(self.stub.url)
        self.stub.codes([])
        self.assertEqual(bc.read_barcodes(self.png), [])
        self.assertEqual((bc.status(), bc.down_since()), ("ok", None))

    def test_probe(self):
        body = bc.probe()
        self.assertEqual((body["ok"], body["name"], bc.status()), (True, "barcode-decoder", "ok"))
        self.assertEqual(bc.last_health(), body)
        self.assertEqual([(r["method"], r["path"]) for r in self.stub.requests], [("GET", "/health")])
        self.stub.health = (500, {"ok": False})
        self.assertIsNone(bc.probe())
        self.assertEqual(bc.status(), "down")
        self.stub.health = (200, {"ok": True})
        self.assertEqual(bc.probe(), {"ok": True})
        self.assertEqual(bc.status(), "ok")
        bc.configure(None)
        self.assertIsNone(bc.probe())
        self.assertEqual(bc.status(), "off")

    def test_invalid_urls_are_refused(self):
        for url in ("ftp://x", "http://", "http://x/?a=1", "http://x/#f", "local-barcode-decoder:8099"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                bc.configure(url)
        bc.configure("http://local-barcode-decoder:8099/")
        self.assertEqual(bc.DECODER_URL, "http://local-barcode-decoder:8099")

    # ------------------------------------------------ the guard first
    def test_a_header_that_cannot_be_read_is_archived_without_a_request(self):
        for name, data in (("text.png", b"not an image"), ("broken.jpg", b"\xff\xd8\xff\xe0broken")):
            path = os.path.join(self.dir, name)
            with open(path, "wb") as f:
                f.write(data)
            self.assertEqual(bc.read_barcodes(path), [])
        self.assertEqual(bc.read_barcodes(os.path.join(self.dir, "missing.png")), [])
        bc.configure(None)                                          # without a decoder too: today's []
        self.assertEqual(bc.read_barcodes(os.path.join(self.dir, "text.png")), [])
        self.assertEqual(self.stub.requests, [])
        self.assertIn("barcodes not read (UnidentifiedImageError)", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
