"""The worker while the decoder does not answer (INTEGRATION_PLAN.md 3.7 E9, IBC18, IBC19): the photo stays queued
with its attempt given back, the Wallet passes are read meanwhile, the pauses grow (1, 5, 15, 60 minutes) and start
again after a success, and a photo queued for more than 24 hours goes in error with its notification. A decoder
that fails inside uses the attempts as any other error. Through the fake reader, and once through the real client
and a stub decoder.

  python3 -I -B -m unittest discover -s tests/core -p test_decoder_queue.py -v
"""
import datetime, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core_case import CFG, P, USER, CoreCase, eng, te  # noqa: E402
from support.stub_decoder import StubDecoder  # noqa: E402

tf = te.tf
DOWN_24H = "il decodificatore delle foto non risponde da 24 ore"


class Queue(CoreCase):
    def setUp(self):
        super().setUp()
        self.down = True                       # the fake reader raises DecoderUnavailable while this is true
        self.calls = []

    def fake_reader(self, path, max_pixels=None):
        self.calls.append(os.path.basename(path))
        if self.down:
            raise eng.barcodes.DecoderUnavailable("ConnectionRefusedError")
        return list(self.codes)

    def photo(self, color=(1, 2, 3)):
        return eng.save_upload(te.tiny_png(color), "png", USER)[0]

    def resume(self):
        eng._decoder_pause_until = 0.0         # the pause is over

    def test_the_attempt_is_given_back(self):
        doc = self.photo()
        self.assertEqual(eng.work_one(CFG), 0)
        d = self.doc(doc)
        self.assertEqual((d["status"], d["attempts"], d["error"]), ("in_coda", 0, None))
        self.assertIn(f"#{doc}: decoder unavailable (ConnectionRefusedError), retrying in 60 s", self.output.getvalue())
        self.assertGreater(eng._decoder_pause_until, 0)
        for _ in range(5):                     # paused: nothing read, nothing used
            self.assertIsNone(eng.work_one(CFG))
        self.assertEqual((len(self.calls), self.doc(doc)["attempts"]), (1, 0))

    def test_wallet_passes_are_read_meanwhile(self):
        photo = self.photo()
        self.assertEqual(eng.work_one(CFG), 0)                              # paused now
        p = eng.save_upload(tf.pkpass(tf.bcbp()), "pkpass", USER)[0]
        self.assertEqual(eng.work_one(CFG), 0)                              # the pass, newer, read first
        self.assertEqual((self.doc(p)["status"], self.doc(photo)["status"]), ("letto", "in_coda"))
        self.assertIsNone(eng.work_one(CFG))                                # its notification; the photo waits
        self.assertEqual([n["tag"] for n in self.notifications()], [f"{P}-d{p}"])
        self.resume()
        self.down, self.codes = False, []
        self.assertEqual(eng.work_one(CFG), 0)                              # the decoder back: read (archived)
        self.assertEqual((self.doc(photo)["status"], self.doc(photo)["attempts"]), ("archiviato", 1))

    def test_the_pauses_grow_and_start_again(self):
        self.photo()
        waits = []
        for _ in range(6):
            self.resume()
            before = eng.time.monotonic()
            self.assertEqual(eng.work_one(CFG), 0)
            waits.append(round(eng._decoder_pause_until - before))
        self.assertEqual(waits, [60, 300, 900, 3600, 3600, 3600])
        self.resume()
        self.down, self.codes = False, []
        eng.work_one(CFG)                                                   # a success
        self.down = True
        self.photo((4, 5, 6))
        self.resume()
        eng.work_one(CFG)
        self.assertIn("retrying in 60 s", self.output.getvalue().splitlines()[-1])

    def test_after_24_hours_in_error_with_its_notification(self):
        doc = self.photo()
        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24, minutes=1)).astimezone()
        eng.update_document(doc, uploaded_at=old.isoformat(timespec="seconds"))
        recent = self.photo((9, 9, 9))
        self.assertEqual(eng.work_one(CFG), 0)
        d = self.doc(doc)
        self.assertEqual((d["status"], d["error"]), ("errore", DOWN_24H))
        self.assertIn(f"#{doc}: decoder unavailable (ConnectionRefusedError), in error after 24 hours",
                      self.output.getvalue())
        self.resume()
        eng.work_one(CFG)
        self.assertEqual(self.doc(recent)["status"], "in_coda")              # the recent one still waits
        self.assertEqual([(n["title"], n["message"], n["tag"]) for n in self.notifications()],
                         [("Lettura non riuscita", f"Documento #{doc}: {DOWN_24H}. Riprova o mandalo di nuovo.",
                           f"{P}-d{doc}")])

    def test_a_decoder_that_fails_inside_uses_the_attempts(self):
        def internal(path, max_pixels=None):
            raise RuntimeError("il decodificatore non ha finito la lettura")
        eng.barcodes.read_barcodes = internal
        doc = self.photo()
        self.assertEqual([eng.work_one(CFG) for _ in range(eng.MAX_ATTEMPTS)], [30, 30, 5])
        d = self.doc(doc)
        self.assertEqual((d["status"], d["error"]), ("errore", "RuntimeError: il decodificatore non ha finito la lettura"))
        self.assertEqual(eng._decoder_pause_until, 0.0)                     # no pause: the decoder answered


class WithTheRealClient(CoreCase):
    """read_document -> barcodes.read_barcodes -> a stub decoder: read, then down, then up again."""

    def test_through_a_stub(self):
        eng.barcodes.read_barcodes = self.saved_reader                       # the real client
        stub = StubDecoder()
        self.addCleanup(stub.close)
        eng.barcodes.configure(stub.url)
        stub.codes(["https://example.invalid", tf.bcbp(seat="014C")])
        first = eng.save_upload(te.tiny_png(), "png", USER)[0]
        self.assertEqual(eng.work_one(CFG), 0)
        self.assertEqual((self.doc(first)["status"], self.doc(first)["merchant"]), ("letto", "FR 1234"))
        stub.close()                                                        # the decoder stops
        second = eng.save_upload(te.tiny_png((7, 7, 7)), "png", USER)[0]
        self.assertEqual(eng.work_one(CFG), 0)
        self.assertEqual((self.doc(second)["status"], self.doc(second)["attempts"]), ("in_coda", 0))
        self.assertEqual(eng.barcodes.status(), "down")
        stub2 = StubDecoder()
        self.addCleanup(stub2.close)
        eng.barcodes.configure(stub2.url)
        eng._decoder_pause_until = 0.0
        self.assertEqual(eng.work_one(CFG), 0)
        self.assertEqual(self.doc(second)["status"], "archiviato")           # no code: archived, as today

    def test_a_file_whose_answer_is_too_long_does_not_hold_the_queue(self):
        """SECURITY-02 (probe_queue_block.py): a 200 over MAX_REPLY used to be "decoder down", so the first photo
        kept its attempt and stayed first in the queue (up to DECODER_GIVE_UP). Now it uses its three attempts and
        goes in error, and the next photo is read."""
        eng.barcodes.read_barcodes = self.saved_reader
        stub = StubDecoder()
        self.addCleanup(stub.close)
        eng.barcodes.configure(stub.url)
        big = b'{"api": 1, "barcodes": [' + b",".join([b'{"text": "' + b"x" * 1000 + b'"}'] *
                                                    (eng.barcodes.MAX_REPLY // 1000 + 10)) + b"]}"
        stub.answer(200, raw=big)
        sheet = eng.save_upload(te.tiny_png((1, 1, 1)), "png", USER)[0]
        photo = eng.save_upload(te.tiny_png((2, 2, 2)), "png", USER)[0]
        self.assertEqual([eng.work_one(CFG) for _ in range(eng.MAX_ATTEMPTS)], [30, 30, 5])
        d = self.doc(sheet)
        self.assertEqual((d["status"], d["attempts"], d["error"]),
                         ("errore", 3, "RuntimeError: il decodificatore non ha finito la lettura"))
        self.assertEqual((self.doc(photo)["status"], eng._decoder_pause_until, eng.barcodes.status()),
                         ("in_coda", 0.0, "ok"))
        stub.codes([tf.bcbp(seat="014C")])
        self.assertEqual(eng.work_one(CFG), 0)
        self.assertEqual((self.doc(photo)["status"], self.doc(photo)["merchant"]), ("letto", "FR 1234"))


if __name__ == "__main__":
    unittest.main()
