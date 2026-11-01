"""HEIC/HEIF photos (INTEGRATION_PLAN.md section 11, Q4): the size from the `ispe` property of the header, read by
the bounded parser of barcodes.heif_size (no recursion, caps on boxes and bytes, every size checked), fuzzed; the
pixel guard on it; a header it cannot read left to the decoder. Synthetic files only, built here.

  python3 -I -B -m unittest discover -s tests/core -p test_heif.py -v
"""
import os, random, shutil, struct, sys, tempfile, time, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402,F401  (the ported core on sys.path; its log lines to the current stdout)
from support.stub_decoder import StubDecoder  # noqa: E402
import barcodes as bc  # noqa: E402


def box(kind, payload, large=False):
    if large:                                           # a 64-bit size
        return struct.pack(">I4sQ", 1, kind, 16 + len(payload)) + payload
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def full(kind, payload, version=0, flags=0):
    return box(kind, struct.pack(">I", (version << 24) | flags) + payload)


def ispe(w, h):
    return full(b"ispe", struct.pack(">II", w, h))


def heif(sizes=((4032, 3024), (512, 512), (320, 240)), primary=1, assoc=None, brand=b"heic", meta_last=False,
         wide=False, mdat=b"\0" * 64, large_mdat=False, extra_meta=b""):
    """A HEIF file: ftyp, meta (hdlr, pitm, iprp: ipco with one ispe per size, ipma) and mdat. Item i (from 1) is
    associated with property i unless `assoc` says otherwise; `wide`: pitm and ipma version 1, 16-bit indexes."""
    ftyp = box(b"ftyp", brand + struct.pack(">I", 0) + b"mif1" + brand)
    hdlr = full(b"hdlr", struct.pack(">I4s12s", 0, b"pict", b"\0" * 12) + b"\0")
    pitm = full(b"pitm", struct.pack(">I", primary), version=1) if wide else full(b"pitm", struct.pack(">H", primary))
    ipco = box(b"ipco", b"".join(ispe(w, h) for w, h in sizes))
    assoc = assoc or {i: [i] for i in range(1, len(sizes) + 1)}
    entries = b""
    for item, props in assoc.items():
        entries += struct.pack(">I" if wide else ">H", item) + bytes([len(props)])
        entries += b"".join(struct.pack(">H", 0x8000 | p) if wide else bytes([0x80 | p]) for p in props)
    ipma = full(b"ipma", struct.pack(">I", len(assoc)) + entries, version=1 if wide else 0, flags=1 if wide else 0)
    meta = full(b"meta", hdlr + pitm + extra_meta + box(b"iprp", ipco + ipma))
    data = box(b"mdat", mdat, large=large_mdat)
    return ftyp + (data + meta if meta_last else meta + data)


class WithDir(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="bpt-test-heif-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.n = 0

    def write(self, data, name=None):
        self.n += 1
        path = os.path.join(self.dir, name or f"photo{self.n}.heic")
        with open(path, "wb") as f:
            f.write(data)
        return path

    def size(self, data):
        return bc.heif_size(self.write(data))


class HeifSize(WithDir):
    def test_the_primary_image(self):
        self.assertEqual(self.size(heif()), (4032, 3024))
        self.assertEqual(self.size(heif(primary=3)), (320, 240))          # pitm, not the largest
        self.assertEqual(self.size(heif(primary=2, assoc={1: [1], 2: [3, 2]})), (320, 240))   # its first ispe

    def test_brands_versions_and_layouts(self):
        for brand in bc.HEIF_BRANDS:
            with self.subTest(brand=brand):
                self.assertEqual(self.size(heif(brand=brand)), (4032, 3024))
        self.assertEqual(self.size(heif(wide=True, primary=70000, assoc={70000: [1], 2: [2]})), (4032, 3024))
        self.assertEqual(self.size(heif(meta_last=True, mdat=b"\0" * 300_000)), (4032, 3024))
        self.assertEqual(self.size(heif(large_mdat=True, meta_last=True)), (4032, 3024))
        self.assertEqual(self.size(heif(extra_meta=box(b"iinf", b"\0" * 50) + box(b"iloc", b"\0" * 40))), (4032, 3024))

    def test_without_a_usable_primary_the_largest(self):
        self.assertEqual(self.size(heif(primary=9)), (4032, 3024))         # pitm names no associated item
        self.assertEqual(self.size(heif(sizes=((320, 240), (4032, 3024)), assoc={7: [1]}, primary=1)),
                         (4032, 3024))
        self.assertEqual(self.size(heif(sizes=((0, 3024), (640, 480)), primary=1)), (640, 480))   # 0 x h ignored

    def test_not_heif(self):
        self.assertIsNone(self.size(b"\xff\xd8\xff\xe0" + b"0" * 40))
        self.assertIsNone(self.size(heif(brand=b"avif")))
        self.assertIsNone(self.size(b""))
        self.assertIsNone(bc.heif_size(os.path.join(self.dir, "missing.heic")))
        self.assertIsNone(self.size(heif(sizes=())))                          # no ispe at all

    def test_sizes_are_never_trusted(self):
        good = heif()
        meta_at = good.index(b"meta") - 4
        cases = {
            "truncated": good[:meta_at + 40],
            "meta past the end": good[:meta_at] + struct.pack(">I", 1 << 30) + good[meta_at + 4:],
            "meta shorter than its header": good[:meta_at] + struct.pack(">I", 4) + good[meta_at + 4:],
            "64-bit size past the end": good[:meta_at] + struct.pack(">I4sQ", 1, b"meta", 1 << 62) + good[meta_at + 8:],
            "child past its parent": good.replace(b"\x00\x00\x00\x14ispe", b"\x7f\x00\x00\x14ispe", 1),
            "ipma count without entries": heif().replace(b"ipma", b"ipma", 1)[:0] + heif(assoc={1: [1]}).replace(
                b"ipma\x00\x00\x00\x00\x00\x00\x00\x01", b"ipma\x00\x00\x00\x00\xff\xff\xff\xff"),
        }
        for name, data in cases.items():
            with self.subTest(case=name):
                start = time.monotonic()
                self.assertIsNone(self.size(data))
                self.assertLess(time.monotonic() - start, 0.5)

    def test_caps(self):
        many_top = box(b"ftyp", b"heic" + b"\0" * 4) + box(b"free", b"") * (bc._HEIF_MAX_TOP_BOXES + 1) + heif()[24:]
        self.assertIsNone(self.size(many_top))                          # meta only after 65 boxes: not looked for
        big_meta = heif(extra_meta=box(b"free", b"\0" * (bc._HEIF_MAX_META + 1)))
        self.assertIsNone(self.size(big_meta))                          # more than 1 MiB of meta: not read
        crowded = heif(extra_meta=box(b"free", b"") * (min(bc._HEIF_MAX_BOXES, 100_000) + 1))
        self.assertIsNone(self.size(crowded))                           # more than 4096 boxes inside meta
        self.assertEqual(self.size(heif(extra_meta=box(b"free", b"") * 100)), (4032, 3024))

    def test_reads_are_bounded(self):
        # meta after a 20 MB mdat: only box headers are read before it, never the mdat
        path = self.write(heif(meta_last=True, mdat=b"\0" * (20 * 1024 * 1024)))
        real_open, read = open, []

        class Counting:
            def __init__(self, f):
                self.f = f

            def read(self, n=-1):
                b = self.f.read(n)
                read.append(len(b))
                return b

            def __getattr__(self, name):
                return getattr(self.f, name)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.f.close()
        with mock.patch("builtins.open", lambda *a, **k: Counting(real_open(*a, **k))):
            self.assertEqual(bc.heif_size(path), (4032, 3024))
        self.assertLess(sum(read), 4096)

    def test_fuzz(self):
        """Random corruptions of real-shaped files: a size or None, never an exception, always fast."""
        rnd = random.Random(20261031)
        seeds = [heif(), heif(wide=True, primary=2, assoc={1: [1], 2: [2, 3]}), heif(meta_last=True),
                 heif(large_mdat=True, meta_last=True)]
        start, worst = time.monotonic(), 0.0
        for i in range(2500):
            data = bytearray(rnd.choice(seeds))
            for _ in range(rnd.randint(1, 6)):
                kind = rnd.random()
                at = rnd.randrange(len(data))
                if kind < 0.4:
                    data[at] = rnd.randrange(256)                     # a flipped byte
                elif kind < 0.7:
                    data[at:at + 4] = struct.pack(">I", rnd.choice((0, 1, 7, 8, 9, 0xFFFFFFFF, rnd.getrandbits(32))))
                elif kind < 0.85:
                    del data[at:]                                     # truncated
                    if len(data) < 12:
                        data += b"\0" * (12 - len(data))
                else:
                    data[at:at] = os.urandom(rnd.randint(1, 64))       # bytes inserted
            data[4:12] = b"ftypheic"                                   # still looks like a HEIC
            t0 = time.monotonic()
            got = self.size(bytes(data))
            worst = max(worst, time.monotonic() - t0)
            if got is not None:
                self.assertTrue(isinstance(got, tuple) and len(got) == 2 and all(isinstance(x, int) and x > 0
                                                                                for x in got), (i, got))
        self.assertLess(worst, 0.25)
        self.assertLess(time.monotonic() - start, 60)


class Guard(WithDir):
    """The pixel guard on a HEIC, and a header that cannot be read left to the decoder."""

    def setUp(self):
        super().setUp()
        self.stub = StubDecoder()
        self.addCleanup(self.stub.close)
        saved = bc.DECODER_URL
        bc.configure(self.stub.url)
        self.addCleanup(bc.configure, saved)

    def test_too_large_from_ispe_before_any_request(self):
        path = self.write(heif(sizes=((9000, 9000), (320, 240))))
        with self.assertRaises(ValueError) as e:
            bc.read_barcodes(path)
        self.assertEqual(str(e.exception), "immagine di 9000x9000 pixel: troppo grande")
        self.assertEqual(self.stub.requests, [])
        bc.configure(None)                                           # also without a decoder
        with self.assertRaises(ValueError):
            bc.read_barcodes(path)

    def test_an_hevx_photo_has_the_guard_and_goes_to_the_decoder(self):
        """PARITY-04 (FIX-repo-1): hevx is one of Q4's brands: its size from `ispe`, then the decoder."""
        with self.assertRaises(ValueError) as e:
            bc.read_barcodes(self.write(heif(sizes=((9000, 9000),), brand=b"hevx")))
        self.assertEqual(str(e.exception), "immagine di 9000x9000 pixel: troppo grande")
        self.assertEqual(self.stub.requests, [])
        self.stub.codes(["M1ROSSI/MARIO"], kind="heic")
        self.assertEqual(bc.read_barcodes(self.write(heif(brand=b"hevx"))), ["M1ROSSI/MARIO"])
        self.assertEqual(len(self.stub.requests), 1)

    def test_a_heic_within_the_limit_goes_to_the_decoder(self):
        data = heif()
        self.stub.codes(["M1ROSSI/MARIO"], kind="heic")
        self.assertEqual(bc.read_barcodes(self.write(data)), ["M1ROSSI/MARIO"])
        self.assertEqual(self.stub.requests[-1]["body"], data)
        with self.assertRaises(ValueError):
            bc.read_barcodes(self.write(data), max_pixels=4032 * 3024 - 1)

    def test_an_unreadable_header_is_left_to_the_decoder(self):
        data = heif()
        unreadable = data[:data.index(b"meta") + 30]                 # meta cut: no size here
        self.assertIsNone(bc.heif_size(self.write(unreadable)))
        self.stub.error(422, "too_many_pixels", width=12000, height=9000)
        with self.assertRaises(ValueError) as e:
            bc.read_barcodes(self.write(unreadable))
        self.assertEqual(str(e.exception), "immagine di 12000x9000 pixel: troppo grande")   # the same text
        self.assertEqual(len(self.stub.requests), 1)
        self.stub.error(422, "unreadable")
        self.assertEqual(bc.read_barcodes(self.write(unreadable)), [])


if __name__ == "__main__":
    unittest.main()
