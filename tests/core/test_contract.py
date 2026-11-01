"""Contract tests: the English names that Home Assistant, the dashboards and the Shortcut read, as
docs/TRANSLATION_MAP.md lists them (the naming contract of the split, version 1.1.0). The file was generated
from that contract, the same way for household-ledger and boarding-pass-tracker. These names are an interface: a
change to one of them is a breaking change, made in TRANSLATION_MAP.md and in this file together.

The expected names sit here as plain dicts, new names only; old names appear only in FORBIDDEN_OLD_KEYS and in
FORBIDDEN, the static checks. Schema walks compute_sensors() on a seeded database, Discovery and Wire check the MQTT
identity, Notifications the tags, the group and the persistent notification, Http the HTTP API (docs/ARCHITECTURE.md),
StaticNames and Dashboards the dashboards and cards. No network: the HTTP server listens on an ephemeral 127.0.0.1
port.

  cd tracker && python3 -I -B -m unittest discover -s . -p test_contract.py -v
"""
import atexit, contextlib, datetime, glob, http.client, io, json, os, re, shutil, struct, sys, tempfile, threading
import unittest, unittest.mock, zlib
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import support  # noqa: E402  (the ported core on sys.path; its log lines to the current stdout)
ROOT = support.ROOT                    # the repository (dashboards/ and the integration's frontend/)

REPO = 'BPT'
PROJECT = 'boarding-pass-tracker'
ENV_PREFIX = 'TRACKER_'
TEST_ENV = {'TRACKER_MQTT_PREFIX': 'bptprova', 'TRACKER_NO_NOTIFY': '1', 'TRACKER_NO_HA': '1', 'TRACKER_NO_MQTT': '1'}
IDENTITY = {'PROJECT': 'boarding-pass-tracker',
            'PRODUCT': 'Boarding Pass Tracker',
            'VERSION': '0.1.0',
            'DEFAULT_PREFIX': 'boarding_pass_tracker'}
HA_DEFAULTS = {}
SENSORS = {'next_flight': {'name': 'Prossimo volo', 'icon': 'mdi:airplane-clock'},
           'upcoming_flights': {'name': 'Voli', 'icon': 'mdi:airplane'},
           'flight_statistics': {'name': 'Statistiche voli', 'icon': 'mdi:chart-box-outline'}}
STATE_KINDS = {'next_flight': 'people', 'upcoming_flights': 'int', 'flight_statistics': 'int'}
STATE_PATTERNS = {'euros': '^-?\\d+\\.\\d{2}$', 'date_or_unknown': '^(\\d{4}-\\d{2}-\\d{2}|unknown)$'}
EMPTY_ATTRIBUTES = ['next_flight']
ATTRIBUTES = {'next_flight': {'flight': {},
                              'route': {},
                              'date': {},
                              'origin': {},
                              'destination': {},
                              'origin_city': {},
                              'destination_city': {},
                              'phase': {'nullable': True, 'enum': 'phase'},
                              'takeoff': {'nullable': True},
                              'flight_iata': {},
                              'status': {},
                              'departure': {'nullable': True},
                              'departure_time': {'nullable': True},
                              'departure_scheduled_time': {'nullable': True},
                              'arrival': {'nullable': True},
                              'arrival_time': {'nullable': True},
                              'delay_min': {'nullable': True},
                              'boarding_time': {'nullable': True},
                              'boarding_estimated': {},
                              'at_gate_time': {'nullable': True},
                              'leave_for_gate_time': {'nullable': True},
                              'priority': {},
                              'gate': {'nullable': True},
                              'terminal': {'nullable': True},
                              'arrival_gate': {'nullable': True},
                              'arrival_terminal': {'nullable': True},
                              'baggage_belt': {'nullable': True},
                              'passengers': {'items': {}},
                              'updated_at': {'nullable': True},
                              'source': {'enum': 'source'}},
              'upcoming_flights': {'list': {'items': {'keys': {'flight': {},
                                                               'route': {},
                                                               'date': {},
                                                               'departure_time': {'nullable': True},
                                                               'status': {},
                                                               'gate': {'nullable': True},
                                                               'passengers': {'items': {}}}}}},
              'flight_statistics': {'flights': {},
                                    'cancelled': {},
                                    'diverted': {},
                                    'upcoming': {},
                                    'air_time_min': {},
                                    'air_time_h': {},
                                    'estimated_durations': {},
                                    'no_data': {},
                                    'km': {},
                                    'earth_laps': {},
                                    'departure_delay_min': {},
                                    'arrival_delay_min': {},
                                    'mean_delay_min': {'nullable': True},
                                    'on_time_pct': {'nullable': True},
                                    'countries': {'items': {'keys': {'code': {}, 'flag': {}, 'flights': {}}}},
                                    'countries_total': {},
                                    'airports': {'items': {'keys': {'code': {}, 'name': {}, 'flights': {}}}},
                                    'airports_total': {},
                                    'airlines': {'items': {'keys': {'code': {}, 'flights': {}}}},
                                    'routes': {'items': {'keys': {'route': {}, 'flights': {}}}},
                                    'cabins': {'dict_keys_enum': 'cabin', 'all_keys': True},
                                    'passengers': {'items': {'keys': {'name': {},
                                                                      'flights': {},
                                                                      'km': {},
                                                                      'air_time_h': {}}}},
                                    'by_person': {'items': {'keys': {'name': {},
                                                                     'flights': {},
                                                                     'cancelled': {},
                                                                     'diverted': {},
                                                                     'air_time_min': {},
                                                                     'estimated_durations': {},
                                                                     'km': {},
                                                                     'arrival_delay_min': {},
                                                                     'mean_delay_min': {'nullable': True},
                                                                     'on_time_pct': {'nullable': True},
                                                                     'countries': {'items': {}},
                                                                     'countries_total': {},
                                                                     'airports_total': {},
                                                                     'airlines': {'items': {'keys': {'code': {},
                                                                                                     'flights': {}}}},
                                                                     'longest': {'nullable': True,
                                                                                 'ref': 'flight_entry'}}}},
                                    'records': {'keys': {'longest': {'nullable': True, 'ref': 'flight_entry'},
                                                         'shortest': {'nullable': True, 'ref': 'flight_entry'},
                                                         'max_duration': {'nullable': True, 'ref': 'flight_entry'},
                                                         'max_delay': {'nullable': True, 'ref': 'flight_entry'}}},
                                    'by_year': {'items': {'keys': {'year': {},
                                                                   'flights': {},
                                                                   'air_time_h': {},
                                                                   'km': {},
                                                                   'arrival_delay_min': {},
                                                                   'on_time_pct': {'nullable': True}}}},
                                    'list': {'items': {'ref': 'flight_entry'}},
                                    'list_total': {},
                                    'updated_on': {}}}
REFS = {'flight_entry': {'keys': {'date': {'nullable': True},
                                  'flight': {},
                                  'route': {},
                                  'cities': {},
                                  'duration_min': {'nullable': True},
                                  'km': {'nullable': True},
                                  'delay_min': {'nullable': True},
                                  'outcome': {'enum': 'outcome'},
                                  'estimated': {},
                                  'passengers': {'items': {}}}}}
ENUMS = {'cabin': ['business', 'economy', 'unknown'],
         'connection_state': ['off', 'starting', 'connected', 'subscribed', 'refused', 'down'],
         'decoder_state': ['off', 'ok', 'down'],
         'document_kind': ['boarding_pass', 'document'],
         'document_status': ['queued', 'read', 'archived', 'error'],
         'outcome': ['flown', 'diverted', 'cancelled', 'upcoming'],
         'phase': ['scheduled', 'departed', 'airborne', 'landed', 'cancelled', 'diverted'],
         'source': ['aviationstack', 'boarding_pass', 'manual']}
ENUM_PATTERNS = {'connection_state': ['^down \\(\\w+\\)$']}
FORBIDDEN_OLD_KEYS = ['a',
                      'aeroporti',
                      'aeroporti_totale',
                      'aggiornato',
                      'anno',
                      'arrivo',
                      'bandiera',
                      'cancellati',
                      'citta',
                      'citta_a',
                      'citta_da',
                      'classi',
                      'codice',
                      'compagnie',
                      'da',
                      'data',
                      'decollo',
                      'deviati',
                      'durata_max',
                      'durata_min',
                      'durate_stimate',
                      'elenco',
                      'elenco_totale',
                      'fase',
                      'fonte',
                      'fr24',
                      'gate_arrivo',
                      'giri_terra',
                      'imbarco',
                      'imbarco_stimato',
                      'in_programma',
                      'minuti_volo',
                      'nastro',
                      'nome',
                      'ora_arrivo',
                      'ora_avviarsi',
                      'ora_gate',
                      'ora_partenza',
                      'ora_prevista',
                      'ore',
                      'ore_volo',
                      'paesi',
                      'paesi_totale',
                      'partenza',
                      'passeggeri',
                      'per_anno',
                      'per_persona',
                      'piu_corto',
                      'piu_lungo',
                      'prioritario',
                      'puntualita',
                      'record',
                      'ritardo_arrivo_min',
                      'ritardo_max',
                      'ritardo_medio_min',
                      'ritardo_min',
                      'ritardo_partenza_min',
                      'rotte',
                      'senza_dati',
                      'stato',
                      'stimato',
                      'terminal_arrivo',
                      'tratta',
                      'voli',
                      'volo']
TAG_KINDS = ['d', 'f']
NOTIFICATION_ID = '{p}_entity_ids'
ENTITY_CHECK_TITLE = '{PRODUCT}: id già in uso'
SERVER_HEADER = 'boarding-pass-tracker'
HTTP = {'health': {'method': 'GET',
                   'path': '/health',
                   'response': {'keys': {'ok': {}, 'queued': {}, 'decoder': {'enum': 'decoder_state'}}}},
        'documents': {'method': 'GET',
                      'path': '/documents',
                      'response': {'keys': {'user': {},
                                            'documents': {'items': {'keys': {'id': {},
                                                                             'uploaded_by': {},
                                                                             'uploaded_at': {},
                                                                             'status': {'enum': 'document_status'},
                                                                             'kind': {'nullable': True,
                                                                                      'enum': 'document_kind'},
                                                                             'merchant': {'nullable': True},
                                                                             'doc_date': {'nullable': True}}}}}}},
        'flights': {'method': 'GET',
                    'path': '/flights',
                    'response': {'keys': {'user': {},
                                          'flights': {'items': {'keys': {'id': {},
                                                                         'airline': {},
                                                                         'number': {},
                                                                         'date': {},
                                                                         'origin': {},
                                                                         'destination': {},
                                                                         'updated_at': {'nullable': True},
                                                                         'next_check': {'nullable': True},
                                                                         'error': {'nullable': True},
                                                                         'status': {}}}}}}},
        'upload': {'method': 'POST',
                   'path': '/upload',
                   'response': {'keys': {'id': {}, 'status': {'enum': 'document_status'}, 'message': {}}}}}
HTTP_GONE = [('GET', '/documenti'),
             ('GET', '/payments'),
             ('GET', '/salute'),
             ('GET', '/scadenze'),
             ('GET', '/voli'),
             ('POST', '/carica')]
HTTP_ERROR_BODY = {'keys': {'message': {}}}
HTTP_TEXTS = {'not_found': 'percorso sconosciuto',
              'forbidden_get': 'Non so chi sei: passa dal tailnet.',
              'forbidden_post': 'Non so chi sei: la Scorciatoia deve passare dal tailnet.',
              'empty_or_too_large': 'File vuoto o oltre 25 MB.',
              'heic': 'Foto in HEIC: nella Scorciatoia serve «Converti immagine» in JPEG.',
              'unknown_type': ("Mandami una carta d'imbarco: il pass del Wallet, oppure una foto (JPEG o PNG) o un P"
                               'DF con il suo codice a barre.'),
              'received': 'Ricevuto: lo leggo e ti mando una notifica.',
              'received_again': 'Ricevuto: lo rileggo e ti mando una notifica.',
              'duplicate': "Questo l'avevi gia' mandato il <dd/mm>."}
FRONTEND = 'custom_components/boarding_pass_tracker/frontend'   # the cards' source
CARD_JS = f'{FRONTEND}/*-card.js'
FORBIDDEN = [('old_entity_ids', '\\bsensor\\.casa_\\w+', ['dashboards/*.json', CARD_JS]),
             ('old_fr24_default',
              '\\bsensor\\.flightradar24_seguiti_extra\\b',
              ['dashboards/*.json', CARD_JS]),
             ('old_elements', '\\bcasa-(volo|statistiche)-card\\b', ['dashboards/*.json', CARD_JS]),
             ('old_custom_type', 'custom:casa-', ['dashboards/*.json', CARD_JS]),
             ('old_card_names', '\\((household-ledger)\\)', [CARD_JS]),
             ('old_flights_json_access',
              ("\\ba\\.get\\('(volo|fase|stato|fonte)'\\)|\\ba\\.(volo|tratta|data|da|a|citta_da|citta_a|fase|decoll"
               'o|fr24|stato|partenza|ora_partenza|ora_prevista|arrivo|ora_arrivo|ritardo_min|imbarco|imbarco_stimat'
               'o|ora_gate|ora_avviarsi|prioritario|gate_arrivo|terminal_arrivo|nastro|passeggeri|aggiornato|fonte)'
               '\\b'),
              ['dashboards/*.json']),
             ('old_flights_json_list',
              "'elenco'|\\bv\\.(volo|tratta|data|ora_partenza|stato|passeggeri)\\b",
              ['dashboards/*.json']),
             ('old_status_text_test', "\\bstato not in\\b|'Partito dal gate'", ['dashboards/*.json']),
             ('old_view_paths', '"path"\\s*:\\s*"(voli|statistiche)"', ['dashboards/*.json']),
             ('old_upload_text', 'Manda file a Casa', ['dashboards/*.json', CARD_JS]),
             ('old_option_fr24', '\\.fr24\\b|\\bfr24[\\"\']?\\s*:', ['dashboards/*.json', CARD_JS]),
             ('old_flight_card_access',
              ('(?<![\\w.$-])[A-Za-z_$][\\w$]*(?:\\[\\w+\\])?(?:\\??\\.[A-Za-z_$][\\w$]*(?:\\[\\w+\\])?)*\\??\\.(vol'
               'o|tratta|data|da|a|citta_da|citta_a|fase|decollo|stato|partenza|ora_partenza|ora_prevista|arrivo|ora'
               '_arrivo|ritardo_min|imbarco|imbarco_stimato|ora_gate|ora_avviarsi|prioritario|gate_arrivo|terminal_a'
               'rrivo|nastro|passeggeri|aggiornato|fonte)\\b'),
              [f'{FRONTEND}/boarding-pass-tracker-flight-card.js']),
             ('old_stats_card_access',
              ('(?<![\\w.$-])[A-Za-z_$][\\w$]*(?:\\[\\w+\\])?(?:\\??\\.[A-Za-z_$][\\w$]*(?:\\[\\w+\\])?)*\\??\\.(vol'
               'i|cancellati|deviati|in_programma|minuti_volo|ore_volo|durate_stimate|senza_dati|giri_terra|ritardo_'
               'partenza_min|ritardo_arrivo_min|ritardo_medio_min|puntualita|paesi|codice|bandiera|paesi_totale|aero'
               'porti|aeroporti_totale|compagnie|rotte|classi|passeggeri|per_persona|record|piu_lungo|piu_corto|dura'
               'ta_max|ritardo_max|per_anno|anno|elenco|elenco_totale|aggiornato|data|volo|tratta|citta|durata_min|r'
               'itardo_min|stato|stimato|nome|ore|minuti)\\b'),
              [f'{FRONTEND}/boarding-pass-tracker-stats-card.js']),
             ('old_phase_literals',
              '(===|!==|\\(|\\[|,)\\s*[\\"\'](programmato|partito|in_volo|atterrato|cancellato|deviato)[\\"\']',
              [CARD_JS]),
             ('old_outcome_literals',
              ('(===|!==|\\(|\\[|,)\\s*[\\"\'](volato|in programma|in_programma|sconosciuta)[\\"\']|[{,]\\s*(cancell'
               'ato|deviato|volato)\\s*:'),
              [CARD_JS]),
             ('old_options',
              ('\\.(sezioni|unita_distanza|formato_ore)\\b|(?<!dataset)\\.persona\\b|\\^decimale\\$|_choice\\(\\s*['
               '\\"\'](persona|unita_distanza|formato_ore)[\\"\']\\s*\\)|===\\s*[\\"\']decimali[\\"\']|HOUR_FORMATS'
               '\\s*=\\s*\\[[^\\]]*[\\"\']decimali[\\"\']'),
              [CARD_JS]),
             ('old_section_values',
              ('(===|includes\\()\\s*[\\"\'](riepilogo|anni|classifiche|record|elenco)[\\"\']|\\bSECTIONS\\s*=\\s*\\'
               '[\\s*[\\"\']riepilogo'),
              [CARD_JS]),
             ('old_console_prefix', 'casa-statistiche-card:', [CARD_JS]),
             ('old_event', 'casa-statistiche-card-scelte', ['dashboards/*.json', CARD_JS])]
DASHBOARD = {'file': 'dashboards/flights.json',
             'views': [['flights', 'Voli'], ['statistics', 'Statistiche']],
             'must': ['sensor\\.boarding_pass_tracker_next_flight',
                      'sensor\\.boarding_pass_tracker_upcoming_flights',
                      'sensor\\.boarding_pass_tracker_flight_statistics',
                      'device_tracker\\.flightradar24',
                      'custom:boarding-pass-tracker-flight-card',
                      'custom:boarding-pass-tracker-stats-card',
                      '["\'`]aviationstack["\'`]\\s*:\\s*["\'`]AviationStack["\'`]',
                      '["\'`]boarding_pass["\'`]\\s*:\\s*["\'`]carta d\'imbarco["\'`]',
                      '["\'`]manual["\'`]\\s*:\\s*["\'`]inserimento a mano["\'`]',
                      '["\'`]flight["\'`]',
                      '["\'`]list["\'`]',
                      '["\'`]scheduled["\'`]',
                      'Manda volo',
                      '\\ba\\.(get\\(["\'`]departure_time["\'`]\\)|departure_time)',
                      '\\ba\\.(get\\(["\'`]boarding_time["\'`]\\)|boarding_time)',
                      '\\ba\\.(get\\(["\'`]at_gate_time["\'`]\\)|at_gate_time)',
                      '\\ba\\.(get\\(["\'`]leave_for_gate_time["\'`]\\)|leave_for_gate_time)',
                      '\\ba\\.(get\\(["\'`]arrival_time["\'`]\\)|arrival_time)',
                      '\\ba\\.(get\\(["\'`]baggage_belt["\'`]\\)|baggage_belt)',
                      '\\ba\\.(get\\(["\'`]passengers["\'`]\\)|passengers)',
                      '\\ba\\.(get\\(["\'`]updated_at["\'`]\\)|updated_at)',
                      '\\ba\\.(get\\(["\'`]source["\'`]\\)|source)',
                      '\\ba\\.(get\\(["\'`]phase["\'`]\\)|phase)',
                      '\\bv\\.flight\\b',
                      '\\bv\\.route\\b',
                      '\\bv\\.date\\b',
                      '\\bv\\.departure_time\\b',
                      '\\bv\\.status\\b']}
CARDS = {f'{FRONTEND}/boarding-pass-tracker-flight-card.js': [('customElements\\.define\\(\\s*["\'`]boarding-pass-tra'
                                                              'cker-flight-card["\'`]'),
                                                             '["\'`]sensor\\.boarding_pass_tracker_next_flight["\'`]',
                                                             '["\'`]Volo \\(Boarding Pass Tracker\\)["\'`]',
                                                             '["\'`]Il prossimo volo su un arco\\.["\'`]',
                                                             'getStubConfig\\s*\\(',
                                                             '\\bFlightCard\\b',
                                                             '["\'`]sensor\\.flightradar24_additional_tracked["\'`]',
                                                             '\\bfr24_entity\\b',
                                                             '\\.flight_iata\\b',
                                                             '["\'`]landed["\'`]',
                                                             '["\'`]airborne["\'`]',
                                                             '["\'`]departed["\'`]',
                                                             '["\'`]cancelled["\'`]',
                                                             '["\'`]diverted["\'`]'],
         f'{FRONTEND}/boarding-pass-tracker-stats-card.js': [('customElements\\.define\\(\\s*["\'`]boarding-pass-trac'
                                                             'ker-stats-card["\'`]'),
                                                            ('["\'`]sensor\\.boarding_pass_tracker_flight_statistics'
                                                             '["\'`]'),
                                                            '["\'`]Statistiche voli \\(Boarding Pass Tracker\\)["\'`]',
                                                            ('["\'`]Voli, ore in aria, distanze, ritardi, classifich'
                                                             'e e record, anche per persona\\.["\'`]'),
                                                            'getStubConfig\\s*\\(',
                                                            '\\bFlightStatsCard\\b',
                                                            '\\bsections\\b',
                                                            '\\bdistance_unit\\b',
                                                            '\\btime_format\\b',
                                                            '\\bperson\\b',
                                                            '["\'`]summary["\'`]',
                                                            '["\'`]years["\'`]',
                                                            '["\'`]rankings["\'`]',
                                                            '["\'`]records["\'`]',
                                                            '["\'`]flights["\'`]',
                                                            '["\'`]km["\'`]',
                                                            '["\'`]mi["\'`]',
                                                            '["\'`]h:mm["\'`]',
                                                            '["\'`]decimal["\'`]',
                                                            '["\'`]boarding-pass-tracker-stats-card["\'`]',
                                                            '["\'`]boarding-pass-tracker-stats-card-choices["\'`]',
                                                            'boarding-pass-tracker-stats-card:',
                                                            'Scegli «Manda volo»',
                                                            '["\'`]flown["\'`]',
                                                            '["\'`]upcoming["\'`]',
                                                            '["\'`]unknown["\'`]']}

TODAY = datetime.date(2026, 10, 28)
NOW = datetime.datetime(2026, 10, 28, 8, 0, tzinfo=datetime.timezone.utc)
USER = {"name": "alice", "notify": "mobile_app_alice"}
CFG = {"users": {"alice@example.com": USER}}
_ENGINE = []


def engine():
    """The engine module: the one test_engine.py already imported in this process, otherwise imported here with
    the dry-run environment (the env is touched only in that case)."""
    if not _ENGINE:
        if "engine" not in sys.modules:
            if ENV_PREFIX + "DATA_DIR" not in os.environ:
                tmp = tempfile.mkdtemp(prefix="contract-test-")
                atexit.register(shutil.rmtree, tmp, True)
                os.environ[ENV_PREFIX + "DATA_DIR"] = tmp
                os.environ.setdefault(ENV_PREFIX + "CONFIG", os.path.join(tmp, "none.json"))
            for k, v in TEST_ENV.items():
                os.environ.setdefault(k, v)
        import engine as eng
        _ENGINE.append(eng)
    return _ENGINE[0]


def png(n):
    """A valid 1x1 PNG, different for every n."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    pixel = bytes([0, n % 256, n // 256 % 256, 7])
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixel)) + chunk(b"IEND", b""))


def new_document(eng, n, **fields):
    """A document through the engine's own upload path, then its fields as a reading would leave them."""
    doc_id, _, _ = eng.save_upload(png(n), "png", USER)
    if fields:
        eng.update_document(doc_id, **fields)
    return doc_id


def insert(eng, table, **row):
    with eng._db_lock:
        return eng.db().execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                                tuple(row.values())).lastrowid


STATUS_KEYS = ("api_status", "cancelled", "diverted", "departure_scheduled", "departure_estimated",
               "departure_actual", "takeoff", "landing", "arrival_scheduled", "arrival_estimated", "arrival_actual",
               "departure_delay", "arrival_delay", "gate", "terminal", "arrival_gate", "arrival_terminal",
               "baggage_belt", "origin_city", "destination_city", "origin_tz", "destination_tz")


def status(**fields):
    """A flights.status JSON as flights.normalize() leaves it (fictional times, UTC)."""
    s = {**{k: None for k in STATUS_KEYS}, "cancelled": False, "diverted": False, "origin_tz": "Europe/Rome",
         "destination_tz": "Europe/London"}
    return json.dumps({**s, **fields})


def seed(eng):
    """Fictional flights: one upcoming with an AviationStack status, one upcoming from the pass only, one landed
    with a delay, one flown without news, one cancelled; two passengers."""
    doc = new_document(eng, 1, kind=eng.KIND_BOARDING_PASS, status=eng.DOC_READ, merchant="XY 101",
                       doc_date="2026-10-28", notified=1, outcome="carta di prova")
    flights = [
        ("101", "2026-10-28", "BLQ", "STN", None, status(api_status="scheduled", gate="A1", terminal="1",
                                                         departure_scheduled="2026-10-28T15:00:00+00:00",
                                                         departure_estimated="2026-10-28T15:00:00+00:00",
                                                         arrival_scheduled="2026-10-28T17:20:00+00:00")),
        ("102", "2026-10-29", "STN", "BLQ", "2026-10-29T09:00:00+00:00", None),
        ("103", "2026-10-18", "BLQ", "STN", None, status(api_status="landed",
                                                         departure_scheduled="2026-10-18T05:30:00+00:00",
                                                         departure_actual="2026-10-18T05:55:00+00:00",
                                                         takeoff="2026-10-18T06:10:00+00:00",
                                                         landing="2026-10-18T07:55:00+00:00",
                                                         arrival_scheduled="2026-10-18T07:50:00+00:00",
                                                         arrival_actual="2026-10-18T08:05:00+00:00", baggage_belt="3")),
        ("104", "2026-09-30", "STN", "FCO", None, None),
        ("105", "2026-10-20", "FCO", "BLQ", None, status(api_status="cancelled", cancelled=True,
                                                         departure_scheduled="2026-10-20T10:00:00+00:00")),
    ]
    for number, day, origin, destination, pass_departure, st in flights:
        flight = insert(eng, "flights", airline="XY", number=number, date=day, origin=origin, destination=destination,
                        pass_departure=pass_departure, status=st, updated_at=eng.now_iso() if st else None,
                        created_at=eng.now_iso())
        passengers = [("Alice Prova", "Alice", "12A", "Y")] + ([("Bruno Prova", "Bruno", "2C", "J")]
                                                               if number == "103" else [])
        for passenger, first, seat, cabin in passengers:
            insert(eng, "boarding_passes", document_id=doc, flight_id=flight, passenger=passenger, first_name=first,
                   seat=seat, cabin=cabin, uploaded_by="alice")


def sensor_values(eng):
    return eng.compute_sensors(TODAY, NOW)


class FakeSensors:
    connected = True

    def __init__(self):
        self.values = {}

    def publish(self, name, state, attributes=None):
        self.values[name] = (state, attributes)


class Base(unittest.TestCase):
    """A new database in a new folder for every test; the engine globals it changes come back afterwards."""
    SAVED = ("DATA_DIR", "DATABASE", "_db", "_ha", "_sensors", "NO_HA", "NO_NOTIFY", "NO_MQTT")

    def setUp(self):
        self.eng = eng = engine()
        self.saved = {k: getattr(eng, k) for k in self.SAVED if hasattr(eng, k)}
        self.dir = tempfile.mkdtemp(prefix="contract-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        eng.DATA_DIR, eng.DATABASE, eng._db = self.dir, None, None
        self.output = io.StringIO()
        r = contextlib.redirect_stdout(self.output)
        r.__enter__()
        self.addCleanup(r.__exit__, None, None, None)

    def tearDown(self):
        if self.eng._db is not None:
            self.eng._db.close()
        for k, v in self.saved.items():
            setattr(self.eng, k, v)

    # ------------------------------------------------ the recursive check of a value against a node
    def check(self, value, node, path, seen=None):
        if seen is not None:
            seen.add(path)                  # the key is there; what is under it only if it is not null or empty
        if value is None:
            self.assertTrue(node.get("nullable"), f"{path} is null")
            return
        if "ref" in node:
            node = {**REFS[node["ref"]], **{k: v for k, v in node.items() if k != "ref"}}
        if "keys" in node:
            self.assertIsInstance(value, dict, path)
            self.assertEqual(sorted(value), sorted(node["keys"]), f"keys of {path}")
            for k, sub in node["keys"].items():
                self.check(value[k], sub, f"{path}.{k}", seen)
        elif "items" in node:
            self.assertIsInstance(value, list, path)
            for x in value:
                self.check(x, node["items"], f"{path}[]", seen)
        elif "dict_keys_enum" in node:
            self.assertIsInstance(value, dict, path)
            codes = ENUMS[node["dict_keys_enum"]]
            self.assertLessEqual(set(value), set(codes), f"keys of {path}")
            if node.get("all_keys"):
                self.assertEqual(set(value), set(codes), f"keys of {path}")
            if seen is not None and value:
                seen.add(path + "{}")
        else:
            self.assertNotIsInstance(value, (dict, list), path)
        if "enum" in node:
            name = node["enum"]
            ok = value in ENUMS[name] or any(re.fullmatch(p, str(value)) for p in ENUM_PATTERNS.get(name, []))
            self.assertTrue(ok, f"{path}: {value!r} is not a code of {name}")


def all_paths(node, path):
    """Every path check() can reach, for the coverage of the seeded data."""
    if "ref" in node:
        node = REFS[node["ref"]]
    out = {path}
    if "keys" in node:
        for k, sub in node["keys"].items():
            out |= all_paths(sub, f"{path}.{k}")
    if "items" in node:
        out |= all_paths(node["items"], f"{path}[]")
    if "dict_keys_enum" in node:
        out.add(path + "{}")
    return out


def all_keys(value):
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in all_keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in all_keys(v)}
    return set()


# ---------------------------------------------------------------- sensors
class Schema(Base):
    def test_sensor_names(self):
        self.assertEqual(sorted(sensor_values(self.eng)), sorted(SENSORS))

    def test_seeded_attributes(self):
        seed(self.eng)
        values = sensor_values(self.eng)
        self.assertEqual(sorted(values), sorted(SENSORS))
        seen = set()
        for name, (state, attributes) in values.items():
            with self.subTest(sensor=name):
                self.check_state(name, state)
                self.check(attributes, {"keys": ATTRIBUTES[name]}, name, seen)
                self.assertFalse(all_keys(attributes) & set(FORBIDDEN_OLD_KEYS), f"old keys in {name}")
        expected = set().union(*(all_paths({"keys": t}, n) for n, t in ATTRIBUTES.items()))
        self.assertEqual(sorted(expected - seen), [], "paths the seeded data does not reach")

    def test_empty_database(self):
        for name, (state, attributes) in sensor_values(self.eng).items():
            with self.subTest(sensor=name):
                self.check_state(name, state)
                if name in EMPTY_ATTRIBUTES:
                    self.assertEqual(attributes, {})
                else:
                    self.check(attributes, {"keys": ATTRIBUTES[name]}, name)

    def check_state(self, name, state):
        kind = STATE_KINDS[name]
        if kind == "int":
            self.assertIs(type(state), int, f"state of {name}: {state!r}")
        elif kind == "people":
            self.assertTrue(isinstance(state, str) and state.strip(), f"state of {name}: {state!r}")
        else:
            self.assertRegex(str(state), STATE_PATTERNS[kind], f"state of {name}")


# ---------------------------------------------------------------- notifications
class Notifications(Base):
    def test_tags(self):
        p = self.eng.MQTT_PREFIX
        for kind in TAG_KINDS:
            self.assertEqual(self.eng.tag(kind, 7), f"{p}-{kind}7")

    def test_group(self):
        h = self.eng.ha.HA("http://192.0.2.1:8123", lambda: "token", group="grp")
        calls = []
        h.service = lambda domain, name, data, response=False: calls.append((domain, name, data))
        self.assertTrue(h.notify("mobile_app_alice", "T", "M", tag="grp-d1"))
        self.assertEqual(calls[0][2]["data"], {"group": "grp", "tag": "grp-d1"})


# ---------------------------------------------------------------- HTTP API
class Http(Base):
    def setUp(self):
        super().setUp()
        from support import handler        # after engine(): the handler imports the engine
        self.Handler = handler.Handler
        self.saved_cfg = self.Handler.cfg
        self.Handler.cfg = CFG
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), self.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.Handler.cfg = self.saved_cfg
        super().tearDown()

    def call(self, method, path, body=None, user=True, content_type="application/octet-stream"):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
        headers = {"Host": f"127.0.0.1:{self.srv.server_address[1]}"}
        if user:
            headers["Tailscale-User-Login"] = "alice@example.com"
        if body is not None:
            headers["Content-Type"] = content_type
        c.request(method, path, body=body, headers=headers)
        r = c.getresponse()
        raw, server = r.read(), r.getheader("Server") or ""
        c.close()
        return r.status, json.loads(raw), server.strip()

    def endpoint(self, name, body=None):
        e = HTTP[name]
        code, data, _ = self.call(e["method"], e["path"], body)
        self.assertEqual(code, 200, data)
        self.check(data, e["response"], name)
        return data

    def test_health(self):
        self.endpoint("health")

    def test_upload_and_documents(self):
        data = self.endpoint("upload", png(1))
        self.assertEqual(data["message"], HTTP_TEXTS["received"])
        new_document(self.eng, 2, merchant="PROVA", doc_date="2026-10-05",
                     kind=self.eng.KIND_BOARDING_PASS, status=self.eng.DOC_READ)
        rows = self.endpoint("documents")["documents"]
        self.assertEqual(len(rows), 2)

    def test_flights(self):
        insert(self.eng, "flights", airline="XY", number="101", date="2026-10-28", origin="BLQ", destination="STN",
               status=status(api_status="scheduled", departure_scheduled="2026-10-28T15:00:00+00:00"),
               created_at=self.eng.now_iso())
        insert(self.eng, "flights", airline="XY", number="102", date="2026-10-29", origin="STN", destination="BLQ",
               created_at=self.eng.now_iso())
        self.assertEqual(len(self.endpoint("flights")["flights"]), 2)

    def test_old_and_foreign_paths_are_gone(self):
        for method, path in HTTP_GONE:
            with self.subTest(path=path):
                code, data, _ = self.call(method, path, png(3) if method == "POST" else None)
                self.assertEqual((code, data), (404, {"message": HTTP_TEXTS["not_found"]}))

    def test_error_bodies(self):
        code, data, _ = self.call("GET", HTTP["documents"]["path"], user=False)
        self.assertEqual((code, data), (403, {"message": HTTP_TEXTS["forbidden_get"]}))
        code, data, _ = self.call("POST", HTTP["upload"]["path"], png(4), user=False)
        self.assertEqual((code, data), (403, {"message": HTTP_TEXTS["forbidden_post"]}))
        code, data, _ = self.call("POST", HTTP["upload"]["path"], b"not a document")
        self.assertEqual((code, data), (415, {"message": HTTP_TEXTS["unknown_type"]}))
        self.check(data, HTTP_ERROR_BODY, "error")

    def test_server_header(self):
        self.assertEqual(self.call("GET", HTTP["health"]["path"])[2], SERVER_HEADER)


# ---------------------------------------------------------------- dashboards and cards
class StaticNames(unittest.TestCase):
    """None of the old names in the dashboards (and cards); docs and the v0 migration constants are not scanned."""

    def test_no_old_names(self):
        for fid, regex, globs in FORBIDDEN:
            rx = re.compile(regex)
            for g in globs:
                paths = sorted(glob.glob(os.path.join(ROOT, g)))
                self.assertTrue(paths, f"{g}: no file")
                for path in paths:
                    with open(path, encoding="utf-8") as f:
                        hits = [f"{i}: {line.strip()[:140]}" for i, line in enumerate(f, 1) if rx.search(line)]
                    with self.subTest(check=fid, file=os.path.relpath(path, ROOT)):
                        self.assertEqual(hits, [])


def strings(x):
    """Every string of a decoded JSON value: the Jinja templates as HA reads them (no JSON escapes)."""
    if isinstance(x, dict):
        return [s for v in x.values() for s in strings(v)]
    if isinstance(x, list):
        return [s for v in x for s in strings(v)]
    return [x] if isinstance(x, str) else []


class Dashboards(unittest.TestCase):
    def test_dashboard(self):
        with open(os.path.join(ROOT, DASHBOARD["file"]), encoding="utf-8") as f:
            d = json.load(f)
        self.assertEqual([[v.get("path"), v.get("title")] for v in d["views"]], DASHBOARD["views"])
        text = "\n".join(strings(d))
        for rx in DASHBOARD["must"]:
            with self.subTest(must=rx):
                self.assertRegex(text, rx)

    def test_cards(self):
        for name, must in CARDS.items():
            with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                text = f.read()
            for rx in must:
                with self.subTest(card=name, must=rx):
                    self.assertRegex(text, rx)


if __name__ == "__main__":
    unittest.main()
