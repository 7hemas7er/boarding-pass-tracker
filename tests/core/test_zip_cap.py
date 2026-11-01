"""SECURITY-05 (FIX-repo-1, flights.py hunk F2): a Wallet pass has at most MAX_ZIP_ENTRIES entries, and that is
checked BEFORE zipfile parses the central directory (one Python object per entry, in Home Assistant's executor:
300,000 empty entries in 25 MB took a second and 50 MB). The entries are counted by their signature PK\\x01\\x02, so
an end record that lies about the count changes nothing. An upload with more entries is not a pass (415, nothing
stored); a stored one (adopted) is still refused by read_pass with the standalone's text.

  python3 -I -B -m unittest discover -s tests/core -p test_zip_cap.py -v
"""
import io, json, os, struct, sys, unittest, zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core_case import USER, CoreCase, eng, te  # noqa: E402
import api  # noqa: E402

fl = eng.flights
tf = te.tf
TODAY = tf.TODAY


def zip_of(n_extra, pass_json=True):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if pass_json:
            z.writestr("pass.json", json.dumps({"formatVersion": 1, "boardingPass": {"transitType": "PKTransitTypeAir"},
                                                "barcodes": [{"message": tf.bcbp()}]}))
        for i in range(n_extra):
            z.writestr(f"f{i}", "")
    return buf.getvalue()


def lying_end_record(data, entries):
    """The same ZIP with the end of central directory record declaring `entries` entries."""
    at = data.rindex(b"PK\x05\x06")
    return data[:at + 8] + struct.pack("<HH", entries, entries) + data[at + 12:]


class ZipCap(unittest.TestCase):
    def test_at_most_max_zip_entries_is_still_a_pass(self):
        data = zip_of(fl.MAX_ZIP_ENTRIES - 1)                       # exactly MAX_ZIP_ENTRIES entries
        self.assertEqual(data.count(b"PK\x01\x02"), fl.MAX_ZIP_ENTRIES)
        self.assertTrue(fl.is_pkpass(data))
        self.assertEqual(fl.read_pass(data)["formatVersion"], 1)

    def test_more_are_refused_before_zipfile_parses_them(self):
        data = zip_of(fl.MAX_ZIP_ENTRIES)
        with mock.patch.object(fl.zipfile, "ZipFile", side_effect=AssertionError("zipfile parsed the directory")):
            self.assertFalse(fl.is_pkpass(data))
            with self.assertRaises(fl.NotABoardingPass) as e:
                fl.read_pass(data)
        self.assertEqual(str(e.exception), "pkpass con troppi file")       # the standalone's text
        with self.assertRaises(fl.NotABoardingPass):
            fl.read_boarding_pass(data, TODAY)

    def test_an_end_record_that_lies_changes_nothing(self):
        for declared in (1, 0, 0xFFFF):
            with self.subTest(declared=declared):
                data = lying_end_record(zip_of(fl.MAX_ZIP_ENTRIES + 50), declared)
                with mock.patch.object(fl.zipfile, "ZipFile", side_effect=AssertionError("parsed")):
                    self.assertFalse(fl.is_pkpass(data))
                    with self.assertRaises(fl.NotABoardingPass):
                        fl.read_pass(data)

    def test_the_count_is_fast_on_a_big_directory(self):
        """60,000 empty entries (about 5 MB): zipfile would build 60,000 ZipInfo objects; the count reads bytes."""
        data = zip_of(60_000)
        calls = []
        real = zipfile.ZipFile
        with mock.patch.object(fl.zipfile, "ZipFile", side_effect=lambda *a, **k: calls.append(1) or real(*a, **k)):
            self.assertFalse(fl.is_pkpass(data))
        self.assertEqual(calls, [])


class Upload(CoreCase):
    def test_an_upload_with_too_many_entries_is_not_stored(self):
        eng.READ_PHOTOS = True
        data = zip_of(fl.MAX_ZIP_ENTRIES)
        self.assertIsNone(eng.file_type_from_bytes(data))
        self.assertEqual(api.upload("application/vnd.apple.pkpass", data, USER), (415, {"message": te.NOT_A_PASS}))
        self.assertEqual(eng.q("SELECT count(*) AS n FROM documents", one=True)["n"], 0)
        self.assertEqual(eng.file_type_from_bytes(zip_of(3)), "pkpass")


if __name__ == "__main__":
    unittest.main()
