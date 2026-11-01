"""AviationStack refused the key (INTEGRATION_PLAN.md 3.7 E7, CBV-01, IBC21): the 6-hour wait is written first, then
the hook (the integration's reauth: it only hops to the event loop) is called in check_flights; an error of the hook
is only logged, and the monthly count goes up once per real call, whatever the hook does.

  python3 -I -B -m unittest discover -s tests/core -p test_reauth_hook.py -v
"""
import datetime, os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core_case import CFG, CoreCase, eng, te  # noqa: E402

REFUSED = "AviationStack 401: invalid_access_key"


class AuthHook(CoreCase):
    LONDON = te.Flights.LONDON

    def setUp(self):
        super().setUp()
        eng.NO_FLIGHTS, eng.AVIATIONSTACK_KEY = False, "fake-flights-key"
        self.day = datetime.datetime.now(self.LONDON).date() + datetime.timedelta(days=1)
        self.fid = self.new_flight(airline="FR", number="1234", date=self.day.isoformat(),
                                   next_check="2000-01-01T00:00:00Z")
        self.now = datetime.datetime.combine(self.day, datetime.time(1), datetime.timezone.utc)
        self.lookups, self.hooked = [], []

    def lookup(self, code=401, text=REFUSED):
        def answer(*a):
            self.lookups.append(a)
            raise eng.flights.APIError(text, code)
        return answer

    def hook_that_raises(self):
        v = eng.q("SELECT next_check, error FROM flights WHERE id=?", (self.fid,), one=True)
        self.hooked.append(dict(v))                       # what the database holds when the hook runs
        raise RuntimeError("the loop is gone")

    def calls(self):
        return eng.q("SELECT coalesce(sum(calls), 0) AS n FROM api_usage", one=True)["n"]

    def test_the_wait_is_written_before_the_hook(self):
        eng._on_auth_error = self.hook_that_raises
        self.assertEqual(eng.check_flights(CFG, self.now, lookup=self.lookup()), 1)
        six_hours = eng.utc(self.now + datetime.timedelta(hours=6))
        self.assertEqual(self.hooked, [{"next_check": six_hours, "error": REFUSED}])
        v = eng.q("SELECT next_check, error FROM flights WHERE id=?", (self.fid,), one=True)
        self.assertEqual((v["next_check"], v["error"]), (six_hours, REFUSED))
        self.assertIn("warning: reauth hook: RuntimeError", self.output.getvalue())
        self.assertNotIn("the loop is gone", self.output.getvalue())          # only the type

    def test_three_rounds_one_call(self):
        eng._on_auth_error = self.hook_that_raises
        st = eng.ClockState()
        lookup = self.lookup()
        with mock.patch.object(eng.flights, "lookup_flight", lookup):
            for i in range(3):
                eng.clock_round(CFG, st, self.now + datetime.timedelta(seconds=60 * i))
        self.assertEqual((len(self.lookups), self.calls(), len(self.hooked)), (1, 1, 1))

    def test_only_a_refused_key_calls_the_hook(self):
        eng._on_auth_error = lambda: self.hooked.append("called")
        for code, text in ((403, "AviationStack 403: function_access_restricted"), (429, "AviationStack: usage_limit"),
                           (401, "AviationStack 401: errore"), (None, "AviationStack non raggiungibile (URLError)")):
            with self.subTest(code=code):
                eng.update_flight(self.fid, next_check="2000-01-01T00:00:00Z")
                eng.check_flights(CFG, self.now, lookup=self.lookup(code, text))
        self.assertEqual(self.hooked, [])
        eng.update_flight(self.fid, next_check="2000-01-01T00:00:00Z")
        eng.check_flights(CFG, self.now, lookup=self.lookup(401, "AviationStack: invalid_access_key"))   # in a 200
        self.assertEqual(self.hooked, ["called"])

    def test_the_default_hook_does_nothing(self):
        self.assertIs(eng._on_auth_error, eng._no_auth_hook)
        eng.check_flights(CFG, self.now, lookup=self.lookup())
        self.assertNotIn("reauth hook", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
