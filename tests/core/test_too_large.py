"""The pixel guard of the decoder client (BC6, INTEGRATION_PLAN.md 7.2 and 5.10): the size comes from the header,
read here before any request, and the error is a plain ValueError, so the engine stores «ValueError: immagine di
WxH pixel: troppo grande». The standalone's TooLarge tests (tracker/test_barcodes.py): test_also_without_a_decoder
kept as it is, the other three against a stub decoder on 127.0.0.1 (no fake zxing in the client).

  python3 -I -B -m unittest discover -s tests/core -p test_too_large.py -v
"""
import contextlib, io, os, shutil, sys, tempfile, unittest, zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402,F401  (the ported core on sys.path; its log lines to the current stdout)
from support.stub_decoder import StubDecoder  # noqa: E402
import barcodes as bc  # noqa: E402
import test_flights as tf  # noqa: E402  (the synthetic BCBP)
from PIL import Image  # noqa: E402

BCBP = tf.bcbp()                    # fictional: ROSSI/MARIO, FR 1234 BLQ-STN


def huge(kind, side=9000):
    """A 16x16 JPEG or PNG whose header says side x side: PIL reads the size, decoding it would fail."""
    buf = io.BytesIO()
    Image.new("RGB", (16, 16)).save(buf, kind)
    b = bytearray(buf.getvalue())
    if kind == "JPEG":
        i = b.index(b"\xff\xc0")                                    # SOF0: height and width after length, precision
        b[i + 5:i + 9] = side.to_bytes(2, "big") * 2
    else:
        b[16:24] = side.to_bytes(4, "big") * 2                      # IHDR, then its CRC
        b[29:33] = zlib.crc32(bytes(b[12:29])).to_bytes(4, "big")
    return bytes(b)


class WithStub(unittest.TestCase):
    """A stub decoder answering with the BCBP, the client pointed at it; no decoder unless a test removes it."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tracker-test-barcodes-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self._path = os.environ.get("PATH", "")
        self.addCleanup(os.environ.__setitem__, "PATH", self._path)
        self.stub = StubDecoder()
        self.addCleanup(self.stub.close)
        self.stub.codes([BCBP])
        saved = bc.DECODER_URL
        bc.configure(self.stub.url)
        self.addCleanup(bc.configure, saved)
        self.png = os.path.join(self.dir, "photo.png")
        Image.new("RGB", (16, 16), "white").save(self.png)

    def write(self, name, data):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def posted(self):
        return [r for r in self.stub.requests if r["method"] == "POST"]


class TooLarge(WithStub):
    """The pixel guard (BC6): the size comes from the header, nothing is sent to the decoder, and the error is a
    plain ValueError, so the engine stores «ValueError: immagine di WxH pixel: troppo grande»."""

    def assert_too_large(self, path, text="immagine di 9000x9000 pixel: troppo grande", **k):
        with self.assertRaises(ValueError) as e:
            bc.read_barcodes(path, **k)
        self.assertIs(type(e.exception), ValueError)
        self.assertEqual(str(e.exception), text)

    def test_rejected_before_decoding(self):
        for kind, name in (("JPEG", "huge.jpg"), ("PNG", "huge.png")):
            path = self.write(name, huge(kind))
            with Image.open(path) as im:
                self.assertEqual(im.size, (9000, 9000))
            self.assert_too_large(path)
        self.assertEqual(self.stub.requests, [])                    # the decoder was never asked

    def test_beyond_the_pil_limit(self):
        # 400 Mpx: Image.open itself refuses the header (as in the old image_b64), still before any request
        with self.assertRaises(Image.DecompressionBombError):
            bc.read_barcodes(self.write("bomb.png", huge("PNG", 20000)))
        self.assertEqual(self.stub.requests, [])

    def test_also_without_a_decoder(self):
        os.environ["PATH"] = self.dir                      # the old image_b64 refused it all the same
        with contextlib.redirect_stdout(io.StringIO()):
            self.assert_too_large(self.write("huge.png", huge("PNG")))

    def test_the_limit(self):
        self.assertEqual(bc.MAX_PIXELS, 50_000_000)
        self.assert_too_large(self.png, "immagine di 16x16 pixel: troppo grande", max_pixels=255)
        self.assertEqual(self.stub.requests, [])
        self.assertEqual(bc.read_barcodes(self.png, max_pixels=256), [BCBP])
        self.assertEqual(bc.read_barcodes(self.write("big.png", huge("PNG", 7000))), [BCBP])    # 49 Mpx: it passes
        self.assertEqual(len(self.posted()), 2)


class WithoutADecoder(unittest.TestCase):
    """test_also_without_a_decoder above runs with the stub configured; here the URL is really unset."""

    def test_the_guard_comes_before_the_missing_decoder(self):
        saved = bc.DECODER_URL
        bc.configure(None)
        self.addCleanup(bc.configure, saved)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "huge.png")
            with open(path, "wb") as f:
                f.write(huge("PNG"))
            with self.assertRaises(ValueError) as e:
                bc.read_barcodes(path)
            self.assertEqual(str(e.exception), "immagine di 9000x9000 pixel: troppo grande")
            small = os.path.join(d, "small.png")
            Image.new("RGB", (16, 16)).save(small)
            with self.assertRaises(bc.DecoderUnavailable):
                bc.read_barcodes(small)


if __name__ == "__main__":
    unittest.main()
