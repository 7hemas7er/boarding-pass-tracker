"""api.py, what the integration's views answer (INTEGRATION_PLAN.md 5.1, 6.2, section 11 Q4): photos and PDFs refused
with 415 no_decoder when no decoder is configured (nothing stored), a HEIC photo queued like a JPEG when one is, the
standalone's other texts and codes, /health with the decoder, the request log line and its levels, the new texts,
and the file modes of an upload (E10). The standalone's routes through the test handler are in the copies
(test_engine.Http, test_editing.Requests, test_contract.Http).

  python3 -I -B -m unittest discover -s tests/core -p test_api.py -v
"""
import glob, logging, os, socket, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core_case import CFG, USER, CoreCase, eng, te  # noqa: E402
import api  # noqa: E402
import support  # noqa: E402

tf = te.tf
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 16
RECEIVED = {"id": 1, "status": "queued", "message": "Ricevuto: lo leggo e ti mando una notifica."}


def form(data, boundary="XX"):
    return (f"multipart/form-data; boundary={boundary}",
            (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"x\"\r\n"
             "Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode())


class Upload(CoreCase):
    def photos(self):
        return {"jpg": b"\xff\xd8\xff\xe0" + b"0" * 20, "png": te.tiny_png(), "webp": b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"0" * 8,
                "pdf": b"%PDF-1.7\n", "heic": HEIC}

    def test_no_decoder_photos_and_pdfs_are_refused_and_not_stored(self):
        eng.READ_PHOTOS = False
        for kind, data in self.photos().items():
            with self.subTest(kind=kind):
                self.assertEqual(eng.file_type_from_bytes(data), kind)
                self.assertEqual(api.upload("application/octet-stream", data, USER),
                                 (415, {"message": api.TEXTS["no_decoder"]}))
                self.assertEqual(api.upload(*form(data), USER), (415, {"message": api.TEXTS["no_decoder"]}))
        self.assertEqual(eng.q("SELECT count(*) AS n FROM documents", one=True)["n"], 0)
        self.assertFalse(os.path.exists(os.path.join(eng.DATA_DIR, "file")))
        self.assertEqual(api.upload(None, tf.pkpass(tf.bcbp()), USER), (200, RECEIVED))     # Wallet passes: yes

    def test_a_heic_photo_is_queued_like_a_jpeg(self):
        eng.READ_PHOTOS = True
        self.assertEqual(api.upload("image/heic", HEIC, USER), (200, RECEIVED))
        d = self.doc(1)
        self.assertEqual((d["file_type"], d["status"], os.path.splitext(d["file"])[1]), ("heic", "in_coda", ".heic"))
        with open(os.path.join(eng.DATA_DIR, d["file"]), "rb") as f:
            self.assertEqual(f.read(), HEIC)
        code, body = api.upload(*form(HEIC + b"x"), USER)                   # the Shortcut's «Form» too
        self.assertEqual((code, body["id"], body["status"]), (200, 2, "queued"))
        code, body = api.upload(None, HEIC, USER)
        self.assertTrue(body["message"].startswith("Questo l'avevi gia' mandato il "))
        sources = "".join(open(p, encoding="utf-8").read() for p in glob.glob(os.path.join(support.CORE, "*.py")))
        self.assertNotIn("Foto in HEIC", sources)                           # the standalone's HEIC 415 is gone

    def test_every_heif_brand_of_q4_is_queued(self):
        """PARITY-04 (FIX-repo-1): section 11 Q4 and the decoder accept eight major brands, hevx included; the
        standalone's list had seven, so an hevx photo got 415 unknown_type."""
        q4 = (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1")
        self.assertEqual(sorted(eng.barcodes.HEIF_BRANDS), sorted(q4))
        eng.READ_PHOTOS = True
        for n, brand in enumerate(q4, 1):
            with self.subTest(brand=brand):
                data = b"\x00\x00\x00\x18ftyp" + brand + b"\x00\x00\x00\x00mif1" + brand + bytes([n]) * 16
                self.assertEqual(eng.file_type_from_bytes(data), "heic")
                code, body = api.upload("image/heic", data, USER)
                self.assertEqual((code, body["status"]), (200, "queued"))
                self.assertTrue(self.doc(body["id"])["file"].endswith(".heic"))
        eng.READ_PHOTOS = False
        hevx = b"\x00\x00\x00\x18ftyphevx\x00\x00\x00\x00mif1hevx" + b"\x07" * 16
        self.assertEqual(api.upload("image/heic", hevx, USER), (415, {"message": api.TEXTS["no_decoder"]}))
        for other in (b"avif", b"avis", b"mp41", b"isom"):                 # not HEIF photos: the standalone's 415
            self.assertIsNone(eng.file_type_from_bytes(b"\x00\x00\x00\x18ftyp" + other + b"\x00" * 16))

    def test_anything_else_is_refused_with_the_standalones_text(self):
        for read_photos in (True, False):
            eng.READ_PHOTOS = read_photos
            for data in (te.INVOICE_XML, te.BANK_CSV, b"just text", b"", b"\x00\x00\x00\x18ftypavif" + b"0" * 8):
                with self.subTest(read_photos=read_photos, data=data[:12]):
                    self.assertEqual(api.upload("application/octet-stream", data, USER), (415, {"message": te.NOT_A_PASS}))
        self.assertEqual(api.TEXTS["unknown_type"], te.NOT_A_PASS)
        self.assertEqual(eng.q("SELECT count(*) AS n FROM documents", one=True)["n"], 0)

    def test_files_are_private_whatever_the_umask(self):
        eng.READ_PHOTOS = True
        old = os.umask(0o022)
        try:
            api.upload(None, te.tiny_png(), USER)
        finally:
            os.umask(old)
        path = os.path.join(eng.DATA_DIR, self.doc(1)["file"])
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        for folder in (os.path.dirname(path), os.path.dirname(os.path.dirname(path)),
                       os.path.join(eng.DATA_DIR, "file")):
            self.assertEqual(os.stat(folder).st_mode & 0o777, 0o700, folder)


class Endpoints(CoreCase):
    def test_health_reports_the_decoder(self):
        self.new_doc(status="in_coda")
        self.assertEqual(api.health(), (200, {"ok": True, "queued": 1, "decoder": "off"}))
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        eng.barcodes.configure(f"http://127.0.0.1:{port}")
        self.assertEqual(api.health()[1]["decoder"], "ok")                  # not known to be down yet
        eng.barcodes.probe()
        self.assertEqual(api.health()[1]["decoder"], "down")

    def test_documents_flights_choices_and_flight(self):
        self.new_doc(merchant="XY 101", doc_date="2026-10-28")
        self.new_flight()
        code, body = api.documents(USER)
        self.assertEqual((code, body["user"], [d["status"] for d in body["documents"]]), (200, "alice", ["read"]))
        code, body = api.flights(USER)
        self.assertEqual((code, body["flights"][0]["status"]), (200, "Dalla carta d'imbarco"))
        code, body = api.choices(USER)
        self.assertEqual((code, body["user"], len(body["choices"])), (200, "alice", 1))
        self.assertEqual(api.flight(b"{}", USER, CFG), eng.change_flight(b"{}", USER, CFG))

    def test_choices_failing(self):
        with mock.patch.object(eng, "flight_choices", side_effect=KeyError("x")):
            self.assertEqual(api.choices(USER), (500, {"message": "Errore interno: riprova tra poco."}))
        self.assertIn("choices request failed: KeyError", self.output.getvalue())
        with mock.patch.object(eng, "flight_choices", side_effect=eng.Closed("shut down")):
            with self.assertRaises(eng.Closed):
                api.choices(USER)

    def test_the_request_line_and_its_level(self):
        records = []

        class Keep(logging.Handler):
            def emit(self, record):
                records.append((record.levelname, record.getMessage()))
        h = Keep()
        logging.getLogger("api").addHandler(h)
        self.addCleanup(logging.getLogger("api").removeHandler, h)
        api.log_reply("GET", "/api/boarding_pass_tracker/health", 200, {"ok": True})
        api.log_reply("POST", "/api/boarding_pass_tracker/upload?x=1", 200, {"message": "Ricevuto: «prova»"})
        api.log_reply("GET", "/api/boarding_pass_tracker/flights", 403, {"message": api.TEXTS["unknown_user"]})
        api.log_reply("GET", "/x", 404, {"messaggio": "vecchio"})
        api.log_reply("POST", "/api/boarding_pass_tracker/flight", 503, {"message": api.TEXTS["not_ready"]})
        self.assertEqual(records, [
            ("INFO", "POST /api/boarding_pass_tracker/upload 200 Ricevuto: «prova»"),
            ("INFO", f"GET /api/boarding_pass_tracker/flights 403 {api.TEXTS['unknown_user']}"),
            ("INFO", "GET /x 404 "),
            ("WARNING", f"POST /api/boarding_pass_tracker/flight 503 {api.TEXTS['not_ready']}")])

    def test_the_texts(self):
        self.assertEqual(api.TEXTS, {
            "not_found": "percorso sconosciuto",
            "empty_or_too_large": "File vuoto o oltre 25 MB.",
            "unknown_type": te.NOT_A_PASS,
            "unknown_user": "Non so chi sei: il tuo utente di Home Assistant non è collegato a nessuna persona di "
                            "Boarding Pass Tracker.",
            "not_ready": "Boarding Pass Tracker non è attivo in questo momento: riprova tra poco.",
            "no_decoder": "Per leggere foto e PDF serve il decodificatore, che non è configurato: manda il pass del "
                          "Wallet, oppure aggiungi il volo con «Aggiungi volo».",
            "body_timeout": "Richiesta arrivata troppo lentamente: riprova."})
        self.assertEqual((api.MAX_BODY, api.MAX_JSON_BODY, api.BODY_TIMEOUT_S), (26_214_400, 16_384, 30))


if __name__ == "__main__":
    unittest.main()
