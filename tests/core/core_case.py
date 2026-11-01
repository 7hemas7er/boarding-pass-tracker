"""Shared by the new core tests (not a copy of the standalone's): CoreCase is test_engine's WithEngine (a new
database in a new folder, the fake HA and sensors, the stubbed barcode reader, the captured log) that also saves and
restores every global configure(), shutdown(), the backup hooks and the decoder queue change.

test_engine is imported first: it sets the TRACKER_* environment, then imports the engine (test_editing does the same).
"""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_engine as te  # noqa: E402  (first: the environment, then the engine)

eng = te.eng
CFG, USER, P = te.CFG, te.USER, te.P
SAVED = ("DATA_DIR", "DATABASE", "LOCAL_TZ", "NO_NOTIFY", "NO_HA", "NO_FLIGHTS", "MAX_API_CALLS_PER_MONTH",
         "AVIATIONSTACK_KEY", "AVIATIONSTACK_KEY_FILE", "FR24_ADD", "FR24_REMOVE", "FR24_TRACKED", "READ_PHOTOS",
         "MQTT_PREFIX", "_closing", "_closed", "_decoder_pause_until", "_decoder_pause_index", "_backup_active",
         "_on_auth_error", "_wakeup")


def settings(data_dir, **over):
    """A complete settings dict for engine.configure, as the integration builds it."""
    s = {"data_dir": data_dir, "local_tz": None, "no_notify": False, "no_flights": False,
         "max_api_calls_per_month": 95, "aviationstack_key": "", "fr24_add": "text.flightradar24_add_to_track",
         "fr24_remove": "text.flightradar24_remove_from_track",
         "fr24_tracked": "sensor.flightradar24_additional_tracked", "decoder_url": None, "read_photos": False}
    s.update(over)
    return s


class CoreCase(te.WithEngine):
    def setUp(self):
        super().setUp()
        self.core_saved = {k: getattr(eng, k) for k in SAVED}
        self.core_saved["flights.LOCAL_TZ"] = eng.flights.LOCAL_TZ
        self.core_saved["DECODER_URL"] = eng.barcodes.DECODER_URL
        self.fr24 = (dict(eng._fr24_attempts), set(eng._fr24_forget))
        self.addCleanup(self.restore_core)

    def restore_core(self):
        with eng._db_lock:
            if eng._db is not None:
                eng._db.close()
                eng._db = None
        for k, v in self.core_saved.items():
            if k == "flights.LOCAL_TZ":
                eng.flights.LOCAL_TZ = v
            elif k == "DECODER_URL":
                eng.barcodes.configure(v)
            else:
                setattr(eng, k, v)
        eng._fr24_attempts.clear()
        eng._fr24_attempts.update(self.fr24[0])
        eng._fr24_forget.clear()
        eng._fr24_forget.update(self.fr24[1])

    def folder(self):
        return tempfile.mkdtemp(dir=te.TEST_DIR)

    def configure(self, **over):
        """configure() with the folder of this test (the fake HA, sensors and reader stay installed)."""
        s = settings(over.pop("data_dir", eng.DATA_DIR), **over)
        eng.configure(s)
        return s
