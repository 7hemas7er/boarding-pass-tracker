"""Tests for ha.py without a real HA or broker: a fake HA (REST and websocket) and a fake MQTT broker on
ephemeral 127.0.0.1 ports, and a fake MQTT client injected in place of paho.

  cd tracker && python3 -I -B -m unittest discover -s . -p 'test_ha.py' -v
"""
import contextlib, datetime, io, json, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402,F401  (the ported core on sys.path; its log lines to the current stdout)
import ha  # noqa: E402

TOKEN = "secret-test-token"
D = ha.DEFAULT_PREFIX                 # boarding_pass_tracker
P = "bptprova"                        # the prefix of a test instance


# ---------------------------------------------------------------- fake HA
class FakeTransport:
    """The facade ha.HA on an in-memory transport (H1 deleted the REST client): what the integration's bridge would
    send to HA is recorded as the REST requests were, {"method", "path", "body"}."""

    def __init__(self):
        self.reset()
        fake = self

        class Facade(ha.HA):
            def _get_state(self, entity):
                fake.requests.append({"method": "GET", "path": f"/api/states/{entity}", "body": None})
                return fake.states.get(entity)

            def _call_service(self, domain, name, data):
                fake.requests.append({"method": "POST", "path": f"/api/services/{domain}/{name}", "body": data})
        self.ha = Facade(timeout=5)

    def reset(self):
        self.requests, self.ws_received, self.states = [], [], {}

    def close(self):
        pass

    def service_calls(self, domain, name):
        return [r for r in self.requests if r["path"] == f"/api/services/{domain}/{name}"]


class WithFakeHA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = FakeTransport()
        cls.ha = cls.fake.ha

    @classmethod
    def tearDownClass(cls):
        cls.fake.close()

    def setUp(self):
        self.fake.reset()
        self.output = io.StringIO()
        r = contextlib.redirect_stdout(self.output)
        r.__enter__()
        self.addCleanup(r.__exit__, None, None, None)

    def tearDown(self):
        self.assertNotIn(TOKEN, self.output.getvalue())          # never the token in the log


class Adversarial(WithFakeHA):
    """Real and dirty data: texts from passes and AviationStack, malicious names, an HA and a broker that answer
    badly."""

    def test_dirty_texts_arrive_intact(self):
        dirty = "XY 101\tBLQ → STN\r\nposto 12A \\ \"quote\" {{ states('lock.porta') }} 🛫\u200b"
        self.assertTrue(self.ha.notify("mobile_app_prova", dirty, dirty, tag=f"{P}-f1"))
        notif = self.fake.service_calls("notify", "mobile_app_prova")[-1]["body"]
        self.assertEqual((notif["title"], notif["message"]), (dirty, dirty))
        data = {"entity_id": "text.flightradar24_add_to_track", "value": '{"entity_id": "text.other"} ' + dirty}
        self.ha.service("text", "set_value", data)
        self.assertEqual(self.fake.service_calls("text", "set_value")[-1]["body"], data)  # the JSON in the text stays text

    def test_malicious_entities_and_services(self):
        for e in ("person.alice?x=1", "person.alice#", "Person.alice", "person.alice\n", "person.alice/../../config",
                  "person.", ".alice", "person..x", " person.alice", "person.alicé", "person", None, 123):
            with self.assertRaises(ValueError, msg=repr(e)):
                self.ha.state(e)
        for domain, name in (("text", "set_value/../x"), ("text", "set value"), ("Text", "set_value"),
                             ("persistent_notification", "create?return_response"), ("text", "")):
            with self.assertRaises(ValueError, msg=repr((domain, name))):
                self.ha.service(domain, name, {})
        for n in ("mobile_app_x/../../states", "mobile_app_x?return_response", "mobile_app_x\r\nX: y", "mobile_app_ñ",
                  "", "mobile app"):
            self.assertFalse(self.ha.notify(n, "t", "m"))
        self.assertEqual(self.fake.requests, [])
        self.assertEqual(self.fake.ws_received, [])

    # ------------------------------------------------ MQTT
    def test_dirty_states_and_attributes(self):
        state, attributes = ha.payloads("x" * 1000, {"route": "Città di Prova (XXX) → \"Altrove\" \\ 🛬",
                                                     "day": datetime.date(2026, 11, 15), "gate": None})
        self.assertEqual(len(state), 255)
        self.assertEqual(json.loads(attributes),
                         {"route": "Città di Prova (XXX) → \"Altrove\" \\ 🛬", "day": "2026-11-15", "gate": None})
        self.assertEqual((ha.payloads(-1, {})[0], ha.payloads(123456789012, None)[0]), ("-1", "123456789012"))
        self.assertEqual(ha.payloads(None, None), ("None", "{}"))

    def test_unencodable_value_does_not_silence_the_sensors(self):
        # half an emoji (a lone surrogate) cannot be encoded: the payload replaces it, so the value can be written
        # and the other sensors are not held back
        state, attributes = ha.payloads("In orario", {"passengers": ["Ada \ud83d"]})
        (state + attributes).encode("utf-8")
        self.assertEqual(json.loads(attributes)["passengers"][0][:4], "Ada ")
        self.assertEqual(ha.payloads(4, None), ("4", "{}"))


if __name__ == "__main__":
    unittest.main()
