"""api: what the HTTP views of the integration answer (views.py), without sockets: each function takes the request's
parts and returns (HTTP code, JSON body). Called in Home Assistant's executor (Runtime.run), never on its loop.

The logic of the standalone's Handler (tracker/engine.py, E:1700-1795), moved here word for word: the same routes,
JSON keys, codes and Italian texts. Identity, the body caps and the streaming of the body are the views' (the user
is the HA user of the token, mapped to a person): here `u` is that person, {"name", "notify", "person"}, the shape
of the standalone's users. Changed from the standalone (INTEGRATION_PLAN.md 6.2, section 11 Q4): /health reports
the decoder instead of MQTT; photos and PDFs (HEIC too) are refused with 415 no_decoder when no decoder is
configured, and a HEIC photo is accepted like a JPEG when one is (the standalone's HEIC 415 is gone).

The log line of every POST and of every answer that is not 200 (log_reply) holds no name, header or address.
"""
import logging

if __package__:
    from . import barcodes, engine, manual
else:
    import barcodes, engine, manual

MAX_BODY = engine.MAX_BODY                  # POST /upload: 1..MAX_BODY bytes (25 MiB)
MAX_JSON_BODY = manual.MAX_JSON_BODY        # POST /flight: 1..MAX_JSON_BODY bytes (16 KiB)
BODY_TIMEOUT_S = manual.BODY_TIMEOUT_S      # POST /flight: the whole body within this many seconds
PHOTO_TYPES = ("jpg", "png", "webp", "pdf", "heic")     # the types read by the decoder (barcodes.py)

TEXTS = {
    # the standalone's (unchanged)
    "not_found": "percorso sconosciuto",
    "empty_or_too_large": "File vuoto o oltre 25 MB.",
    "unknown_type": ("Mandami una carta d'imbarco: il pass del Wallet, oppure una foto (JPEG o PNG) o un PDF con il "
                     "suo codice a barre."),
    # the integration's (INTEGRATION_PLAN.md 5.1)
    "unknown_user": ("Non so chi sei: il tuo utente di Home Assistant non è collegato a nessuna persona di Boarding "
                     "Pass Tracker."),
    "not_ready": "Boarding Pass Tracker non è attivo in questo momento: riprova tra poco.",
    "no_decoder": ("Per leggere foto e PDF serve il decodificatore, che non è configurato: manda il pass del Wallet, "
                   "oppure aggiungi il volo con «Aggiungi volo»."),
    "body_timeout": "Richiesta arrivata troppo lentamente: riprova.",
}

_LOGGER = logging.getLogger(__name__)


def log_reply(method, path, code, body):
    """The standalone's request line «<METHOD> <path> <code> <message>», for every POST and every answer that is
    not 200: INFO, WARNING for a 5xx. The query string is not logged."""
    if code != 200 or method == "POST":
        line = f"{method} {path.split('?')[0]} {code} {body.get('message', '')}"
        (_LOGGER.warning if code >= 500 else _LOGGER.info)(line)


def health():
    """GET /health: the queue and the decoder ("off" without one, "ok", "down")."""
    n = engine.q("SELECT count(*) AS n FROM documents WHERE status='in_coda'", one=True)["n"]
    return 200, {"ok": True, "queued": n, "decoder": barcodes.status()}


def documents(u):
    """GET /documents: the latest 30, with the stored codes as the English ones."""
    rows = engine.q("SELECT id, uploaded_by, uploaded_at, status, kind, merchant, doc_date FROM documents "
                    "ORDER BY id DESC LIMIT 30")
    return 200, {"user": u["name"], "documents": [engine.document_json(r) for r in rows]}


def flights(u):
    """GET /flights: the latest 30, each with its status for people (Italian)."""
    rows = engine.q("SELECT * FROM flights ORDER BY id DESC LIMIT 30")
    of = engine._passes_of([v["id"] for v in rows])
    return 200, {"user": u["name"], "flights": [engine.flight_json(v, of.get(v["id"], [])) for v in rows]}


def choices(u, now=None):
    """GET /choices: the flights to choose from in «Modifica volo» (engine.flight_choices)."""
    try:
        body = engine.flight_choices(now)
    except engine.Closed:
        raise
    except Exception as e:
        engine.log(f"choices request failed: {type(e).__name__}")
        return 500, {"message": manual.text("internal_error")}
    return 200, {"user": u["name"], **body}


def upload(content_type, body, u):
    """POST /upload, the body already read (the view checked its length): the file (the bytes as they are, or the
    first file of a multipart form), typed by content. 415 for anything that is not a boarding pass, and for a photo
    or a PDF with no decoder configured (nothing is stored); else saved, de-duplicated by sha256, and queued."""
    data = engine.extract_file(content_type, body)
    file_type = engine.file_type_from_bytes(data or b"")
    if not file_type:                           # an e-invoice or a bank statement too: household-ledger's
        return 415, {"message": TEXTS["unknown_type"]}
    if file_type in PHOTO_TYPES and not engine.READ_PHOTOS:
        return 415, {"message": TEXTS["no_decoder"]}
    doc_id, status, msg = engine.save_upload(data, file_type, u)
    return 200, {"id": doc_id, "status": engine._code(engine._STATUS_CODES, status), "message": msg}


def flight(body, u, cfg, now=None):
    """POST /flight, the body already read: add, edit or delete a flight by hand (engine.change_flight)."""
    return engine.change_flight(body, u, cfg, now)
