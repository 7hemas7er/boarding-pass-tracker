"""The clock round (INTEGRATION_PLAN.md 3.7 E11, 3.5, CBV-05): the standalone's clock loop without the sleep and
without the entity check, one round per call. The port of test_engine.EntityCheck.test_the_clock_runs_the_flight_jobs
(the order and the counts of the jobs over six rounds a minute apart), the try structure, the cadences with a given
`now` (Flightradar24 every 300 s, the sensors and the decoder's /health every 600 s) and the report.

  python3 -I -B -m unittest discover -s tests/core -p test_clock_round.py -v
"""
import contextlib, datetime, os, socket, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core_case import CFG, USER, CoreCase, eng, te  # noqa: E402
from support.stub_decoder import StubDecoder  # noqa: E402

JOBS = ("check_flights", "notify_gate", "track_fr24", "publish_sensors")


class ClockRound(CoreCase):
    def jobs(self, calls, fail=None):
        """The four jobs replaced by recorders; `fail`: {name: exception} raised by that job."""
        stack = contextlib.ExitStack()

        def job(name):
            def run(*a, **k):
                calls.append(name)
                if fail and name in fail:
                    raise fail[name]
            return run
        for name in JOBS:
            stack.enter_context(mock.patch.object(eng, name, job(name)))
        return stack

    def test_the_clock_runs_the_flight_jobs(self):
        """Every round (60 s) the flights and «go to the gate»; Flightradar24 every 300 s; the sensors every EVERY_S
        (the port of the standalone's clock test, without the entity check)."""
        now, calls = [1000.0], []
        st = eng.ClockState()
        with self.jobs(calls), mock.patch.object(eng.time, "time", lambda: now[0]):
            for _ in range(6):
                eng.clock_round(CFG, st)
                now[0] += 60
        self.assertEqual({name: calls.count(name) for name in set(calls)},
                         {"check_flights": 6, "notify_gate": 6, "track_fr24": 2, "publish_sensors": 1})
        self.assertEqual(calls[:7], ["check_flights", "notify_gate", "track_fr24", "publish_sensors", "check_flights",
                                     "notify_gate", "check_flights"])

    def test_flightradar24_down_does_not_skip_the_sensors(self):
        calls = []
        with self.jobs(calls, {"track_fr24": eng.ha.HAError("HA unreachable (test)")}):
            eng.clock_round(CFG, eng.ClockState())
        self.assertEqual(calls, list(JOBS))
        self.assertIn("Flightradar24: HAError: HA unreachable (test)", self.output.getvalue())

    def test_an_error_in_the_flights_skips_the_rest_of_the_round(self):
        for failing in ("check_flights", "notify_gate"):
            with self.subTest(job=failing):
                calls = []
                with self.jobs(calls, {failing: KeyError("boom")}):
                    report = eng.clock_round(CFG, eng.ClockState())
                self.assertEqual(calls, list(JOBS[:JOBS.index(failing) + 1]))
                self.assertIn("clock: KeyError: 'boom'", self.output.getvalue())
                self.assertEqual(report["decoder_status"], "off")          # the report all the same

    def test_cadences_with_a_given_now(self):
        stub = StubDecoder()
        self.addCleanup(stub.close)
        eng.barcodes.configure(stub.url)
        calls, seen_now = [], []
        start = datetime.datetime(2026, 11, 12, 4, 0, tzinfo=datetime.timezone.utc)
        st = eng.ClockState()
        with self.jobs(calls), mock.patch.object(eng, "check_flights", lambda cfg, now=None: seen_now.append(now)):
            for minute in range(0, 21):
                eng.clock_round(CFG, st, start + datetime.timedelta(minutes=minute))
        self.assertEqual(seen_now, [start + datetime.timedelta(minutes=m) for m in range(21)])   # passed on
        self.assertEqual(calls.count("track_fr24"), 5)                      # minutes 0, 5, 10, 15, 20
        self.assertEqual(calls.count("publish_sensors"), 3)                 # minutes 0, 10, 20
        self.assertEqual([r["path"] for r in stub.requests], ["/health"] * 3)
        eng.barcodes.configure(None)
        eng.clock_round(CFG, st, start + datetime.timedelta(minutes=40))
        self.assertEqual(len(stub.requests), 3)                             # no URL: no probe

    def test_the_report(self):
        eng.save_upload(te.tiny_png(), "png", USER)
        eng.save_upload(te.tiny_png((5, 5, 5)), "jpg", USER)
        eng.save_upload(te.tf.pkpass(te.tf.bcbp()), "pkpass", USER)
        with self.jobs([]):
            self.assertEqual(eng.clock_round(CFG, eng.ClockState()),
                             {"queued_photos": 2, "decoder_status": "off", "decoder_down_since": None})
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            s.close()
            eng.barcodes.configure(f"http://127.0.0.1:{port}")              # nothing listens there
            report = eng.clock_round(CFG, eng.ClockState())
        self.assertEqual((report["queued_photos"], report["decoder_status"]), (2, "down"))
        self.assertIsInstance(report["decoder_down_since"], float)

    def test_the_real_jobs_in_one_round(self):
        """Without stand-ins: a pass uploaded, then one round with the time of the flight (no key: no call)."""
        eng.NO_FLIGHTS = False
        doc = eng.save_upload(te.tf.pkpass(te.tf.bcbp()), "pkpass", USER)[0]
        eng.read_document(self.doc(doc))
        report = eng.clock_round(CFG, eng.ClockState(), datetime.datetime.now(datetime.timezone.utc))
        self.assertEqual(report["queued_photos"], 0)
        self.assertEqual(set(self.sensors.values), set(eng.ha.SENSORS))     # published at the first round
        self.assertNotIn("clock:", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
