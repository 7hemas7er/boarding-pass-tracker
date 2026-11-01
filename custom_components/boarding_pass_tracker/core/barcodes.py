"""barcodes: the texts of the barcodes in a photo, a screenshot or a PDF (there, the BCBP of a boarding pass), read by
the optional barcode decoder (a separate app or container, API v1: INTEGRATION_PLAN.md 5.9) over HTTP.

The integration has no barcode library: it posts the file to DECODER_URL/v1/decode and keeps today's
post-processing of the texts. Before any request, the size of a photo is read from its header here (the standalone's
pixel guard, unchanged): beyond `max_pixels` read_barcodes raises ValueError, whose Italian message the engine stores
as the document error and quotes to the user, and PIL's DecompressionBombError beyond PIL's own limit. A HEIC/HEIF
photo has no Pillow plugin in Home Assistant: its size comes from the `ispe` property of its header, read by a small
bounded parser (heif_size); a header it cannot read leaves the check to the decoder, whose 422 too_many_pixels gives
the same ValueError.

What a decoder answer becomes (plan 5.10):
  - 200: the codes (stripped, at most 500 characters, no empty ones, no duplicates, at most MAX_CODES);
  - 400, 413, 415, 422 unreadable, 504 with its JSON error: [] (the engine archives the document, as today when
    pdftoppm failed or timed out);
  - 422 too_many_pixels: the ValueError above;
  - 500 with its JSON error, and a 200 over MAX_REPLY bytes or in the decoder's JSON (api 1) without a usable
    barcodes list: RuntimeError (one attempt used; after three the document is in error), so one file the decoder
    answers badly cannot hold the queue;
  - no URL, refused, DNS, timeout, a connection cut, 503, a 200 that is not the decoder's JSON, any other answer:
    DecoderUnavailable (the engine keeps the document queued, gives the attempt back and waits: engine.work_one).

The only network call is the POST to the configured URL (and its GET /health probe), with no proxy and no redirect,
run in Home Assistant's executor. Nothing decoded is logged. Tests: tests/core/test_barcodes_client.py,
test_too_large.py, test_heif.py.
"""
import http.client, json, logging, os, struct, threading, time, urllib.error, urllib.parse, urllib.request

from PIL import Image

MAX_CODES = 20
MAX_PIXELS = 50_000_000     # the iPhone's 48 Mpx photo passes
PDF_PAGES = 3               # pages of a PDF the decoder reads (the standalone's PDF_PAGES)
DECODER_MAX_CODES = 50      # codes the decoder returns at most (its own limit)
TIMEOUT_S = 120             # one decode; the decoder stops its own job at 60 s
PROBE_TIMEOUT_S = 10
# bytes of a decoder answer read at most: above the API's worst 200 for DECODER_MAX_CODES codes (each `text` of 8,192
# characters escaped up to 12 bytes each, plus 10,924 of bytes_b64: about 110 KB a code, 5.5 MB for 50). A 200 that
# is longer, or is the decoder's but not its JSON, is the document's failure (RuntimeError), not a decoder outage
MAX_REPLY = 8 * 1024 * 1024
# the major brands typed "heic" by engine.file_type_from_bytes, as the decoder accepts them (section 11 Q4)
HEIF_BRANDS = (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1")
DECODER_URL = None          # engine.configure (decoder_url): "http://local-barcode-decoder:8099"; None: no decoder

_LOGGER = logging.getLogger(__name__)
_lock = threading.Lock()
_down_since = None          # time.time() of the first failed call or probe in a row; None: up (or no URL)
_last_health = None         # the last /health body (diagnostics)


class DecoderUnavailable(Exception):
    """No decoder configured, or it did not answer as a decoder: the document waits (engine.work_one)."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None                   # a redirect becomes an HTTPError (DecoderUnavailable), it is not followed


# a proxy taken from the environment would see the photos of the boarding passes
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def _log(*a):
    _LOGGER.info(" ".join(str(x) for x in a))


def configure(url):
    """The decoder's base URL (http or https, no query), or None/"" for none. The status starts again."""
    global DECODER_URL, _down_since, _last_health
    if url:
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("http", "https") or not u.hostname or u.query or u.fragment:
            raise ValueError("invalid decoder URL")
        url = url.rstrip("/")
    with _lock:
        DECODER_URL, _down_since, _last_health = url or None, None, None


def status():
    """"off" (no URL), "down" (the last call or probe failed) or "ok"."""
    with _lock:
        return "off" if not DECODER_URL else "down" if _down_since is not None else "ok"


def down_since():
    """time.time() since when the decoder does not answer, None if it does (or there is none)."""
    with _lock:
        return _down_since if DECODER_URL else None


def last_health():
    with _lock:
        return _last_health


def _mark(up, health=None):
    global _down_since, _last_health
    with _lock:
        if up:
            _down_since = None
            if health is not None:
                _last_health = health
        elif _down_since is None:
            _down_since = time.time()


# ---------------------------------------------------------------- HEIC/HEIF: the size from the header
_HEIF_MAX_TOP_BOXES = 64        # top-level boxes looked at before `meta` (ftyp, meta, mdat...: a handful)
_HEIF_MAX_META = 1024 * 1024    # bytes of the `meta` box read at most (an iPhone photo has a few dozen KB)
_HEIF_MAX_BOXES = 4096          # boxes and ipma entries walked inside `meta`, all levels together


class _Bad(Exception):
    pass


def _boxes(buf, start, end, budget):
    """[(type, payload start, payload end)] of the ISOBMFF boxes in buf[start:end], one level, with every size
    checked against `end` (never trusted). budget: a one-item list, the boxes left for the whole parse."""
    out, pos = [], start
    while pos + 8 <= end:
        budget[0] -= 1
        if budget[0] < 0:
            raise _Bad("too many boxes")
        size, kind = struct.unpack_from(">I4s", buf, pos)
        head = 8
        if size == 1:                                   # a 64-bit size follows the type
            if pos + 16 > end:
                raise _Bad("truncated box")
            size, head = struct.unpack_from(">Q", buf, pos + 8)[0], 16
        elif size == 0:                                 # up to the end of the parent
            size = end - pos
        if size < head or pos + size > end:
            raise _Bad("box size outside its parent")
        out.append((kind, pos + head, pos + size))
        pos += size
    return out


def _ipma(buf, start, end, budget):
    """{item id: [property index]} of an ItemPropertyAssociation box (a FullBox)."""
    if end - start < 8:
        raise _Bad("short ipma")
    version, flags = buf[start], int.from_bytes(buf[start + 1:start + 4], "big")
    count, pos, out = struct.unpack_from(">I", buf, start + 4)[0], start + 8, {}
    id_size, wide = (2 if version < 1 else 4), bool(flags & 1)
    for _ in range(count):
        budget[0] -= 1
        if budget[0] < 0 or pos + id_size + 1 > end:
            raise _Bad("ipma entries outside the box")
        item = int.from_bytes(buf[pos:pos + id_size], "big")
        n = buf[pos + id_size]
        pos += id_size + 1
        step = 2 if wide else 1
        if pos + n * step > end:
            raise _Bad("ipma associations outside the box")
        mask = 0x7FFF if wide else 0x7F
        out.setdefault(item, []).extend(int.from_bytes(buf[pos + i * step:pos + (i + 1) * step], "big") & mask
                                        for i in range(n))
        pos += n * step
    return out


def _heif_meta_size(meta):
    """(width, height) of the primary image from the payload of a `meta` box (a FullBox): its `ispe` property
    (pitm -> iprp/ipma -> iprp/ipco), else the largest `ispe` of the file; None if there is none."""
    budget = [_HEIF_MAX_BOXES]
    primary, ipco, assoc = None, None, {}
    for kind, a, b in _boxes(meta, 4, len(meta), budget):
        if kind == b"pitm" and b - a >= 6:
            primary = struct.unpack_from(">H", meta, a + 4)[0] if meta[a] == 0 else (
                struct.unpack_from(">I", meta, a + 4)[0] if b - a >= 8 else None)
        elif kind == b"iprp":
            for kind2, c, d in _boxes(meta, a, b, budget):
                if kind2 == b"ipco" and ipco is None:
                    ipco = _boxes(meta, c, d, budget)
                elif kind2 == b"ipma":
                    for item, props in _ipma(meta, c, d, budget).items():
                        assoc.setdefault(item, []).extend(props)
    sizes = {}
    for index, (kind, a, b) in enumerate(ipco or (), 1):   # property indexes start at 1
        if kind == b"ispe" and b - a >= 12:
            w, h = struct.unpack_from(">II", meta, a + 4)
            if w and h:
                sizes[index] = (w, h)
    if not sizes:
        return None
    for index in assoc.get(primary, ()) if primary is not None else ():
        if index in sizes:
            return sizes[index]
    return max(sizes.values(), key=lambda s: s[0] * s[1])


def heif_size(path):
    """(width, height) of a HEIC/HEIF photo from its header, None if it is not one or the header cannot be read
    (then the decoder checks the size). Reads at most _HEIF_MAX_TOP_BOXES box headers and one `meta` box of at
    most _HEIF_MAX_META bytes; no recursion, and every size is checked against the file and its parent box."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            if len(head) < 12 or head[4:8] != b"ftyp" or head[8:12] not in HEIF_BRANDS:
                return None
            end = f.seek(0, os.SEEK_END)
            pos = 0
            for _ in range(_HEIF_MAX_TOP_BOXES):
                if pos + 8 > end:
                    return None
                f.seek(pos)
                h = f.read(16)
                size, kind = struct.unpack_from(">I4s", h, 0)
                head_len = 8
                if size == 1:
                    if len(h) < 16:
                        return None
                    size, head_len = struct.unpack_from(">Q", h, 8)[0], 16
                elif size == 0:
                    size = end - pos
                if size < head_len or pos + size > end:
                    return None
                if kind == b"meta":
                    if size - head_len > _HEIF_MAX_META:
                        return None
                    f.seek(pos + head_len)
                    meta = f.read(size - head_len)
                    return _heif_meta_size(meta) if len(meta) == size - head_len else None
                pos += size
            return None
    except (_Bad, struct.error, OSError, ValueError):
        return None


# ---------------------------------------------------------------- the pixel guard (the standalone's, B:47-76)
def _photo_size(path):
    """(width, height) from the header, or None for a PDF (and for a HEIC whose header cannot be read: the decoder
    checks it). Image.open raises DecompressionBombError beyond twice Image.MAX_IMAGE_PIXELS, also before
    decoding."""
    with open(path, "rb") as f:
        head = f.read(12)
    if head[:5] == b"%PDF-":
        return None
    if head[4:8] == b"ftyp" and head[8:12] in HEIF_BRANDS:     # no Pillow plugin for HEIF in Home Assistant
        return heif_size(path)
    with Image.open(path) as im:
        return im.size


# ---------------------------------------------------------------- the decoder
def _error_of(raw):
    """The {"code", ...} object of a decoder error body, None if the body is not one."""
    try:
        err = json.loads(raw).get("error")
    except (ValueError, AttributeError):
        return None
    return err if isinstance(err, dict) and isinstance(err.get("code"), str) else None


def _codes(body):
    """Today's post-processing (B:88-95) of the decoder's barcodes, in its order."""
    items = body.get("barcodes") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise ValueError("no barcodes list")
    out = []
    for item in items:
        s = item.get("text") if isinstance(item, dict) else None
        if not isinstance(s, str):
            continue
        s = s.strip()[:500]
        if s and s not in out:
            out.append(s)
    return out[:MAX_CODES]


class _BadReply(Exception):
    """A 200 of the decoder (its JSON, api 1) that cannot be used, or one over MAX_REPLY: this file's answer."""


def _ok_codes(raw):
    """The codes of a 200 body. _BadReply when it is over MAX_REPLY or the decoder's JSON (api 1) without a usable
    barcodes list; ValueError when it is not the decoder's JSON at all (another server: DecoderUnavailable)."""
    if len(raw) > MAX_REPLY:
        raise _BadReply("too long")
    body = json.loads(raw)
    try:
        return _codes(body)
    except ValueError:
        if isinstance(body, dict) and body.get("api") == 1 and not isinstance(body.get("api"), bool):
            raise _BadReply("no barcodes list") from None
        raise


def _decode(path):
    """POST the file to the decoder: the codes, [], or the exceptions of the table in the module docstring."""
    query = urllib.parse.urlencode({"pages": PDF_PAGES, "max_codes": DECODER_MAX_CODES})
    with open(path, "rb") as f:
        data = f.read()
    req = urllib.request.Request(f"{DECODER_URL}/v1/decode?{query}", data=data, method="POST",
                                 headers={"Content-Type": "application/octet-stream"})
    try:
        with _OPENER.open(req, timeout=TIMEOUT_S) as r:
            raw = r.read(MAX_REPLY + 1)
        codes = _ok_codes(raw)
    except _BadReply:                               # the decoder answered for this file: one attempt used, as a 500
        _mark(True)
        raise RuntimeError("il decodificatore non ha finito la lettura") from None
    except urllib.error.HTTPError as e:
        try:
            raw = e.read(MAX_REPLY)
        except Exception:
            raw = b""
        err = _error_of(raw)
        if err is None:                             # not the decoder's own error: a proxy, another server
            if e.code in (400, 413, 415):
                _mark(True)
                return []
            _mark(False)
            raise DecoderUnavailable(f"HTTP {e.code}") from None
        _mark(True)
        if e.code == 422 and err["code"] == "too_many_pixels":
            w, h = err.get("width"), err.get("height")
            if isinstance(w, int) and isinstance(h, int) and not isinstance(w, bool) and not isinstance(h, bool):
                raise ValueError(f"immagine di {w}x{h} pixel: troppo grande") from None
            raise ValueError("immagine troppo grande") from None
        if e.code in (400, 413, 415, 422) or (e.code == 504 and err["code"] == "timeout"):
            return []
        if e.code == 500:
            raise RuntimeError("il decodificatore non ha finito la lettura") from None
        _mark(False)
        raise DecoderUnavailable(f"HTTP {e.code}") from None
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:   # refused, DNS, timeout,
        # a broken answer, bad JSON
        _mark(False)
        raise DecoderUnavailable(type(e).__name__) from None
    _mark(True)
    return codes


def read_barcodes(path, max_pixels=MAX_PIXELS):
    """Texts of the barcodes in the photo or in the first PDF_PAGES pages of the PDF, at most MAX_CODES, without
    duplicates. ValueError if the photo has more than `max_pixels` pixels (from the header, before any request:
    also without a decoder), PIL's DecompressionBombError beyond its own limit; [] for a photo whose header cannot
    be read, and for the decoder's "not readable" answers (the caller archives the document); DecoderUnavailable
    without a decoder or when it does not answer; RuntimeError when it failed inside."""
    path = os.path.abspath(path)
    try:
        size = _photo_size(path)
    except Image.DecompressionBombError:
        raise
    except Exception as e:
        _log(f"barcodes not read ({type(e).__name__})")
        return []
    if size and size[0] * size[1] > max_pixels:
        raise ValueError(f"immagine di {size[0]}x{size[1]} pixel: troppo grande")
    if not DECODER_URL:
        raise DecoderUnavailable("no decoder configured")
    return _decode(path)


def probe():
    """GET /health of the decoder: its body (a dict) if it answers as a decoder, None otherwise (and without a
    URL). Updates status() and down_since(). Never raises."""
    if not DECODER_URL:
        return None
    try:
        with _OPENER.open(urllib.request.Request(f"{DECODER_URL}/health"), timeout=PROBE_TIMEOUT_S) as r:
            body = json.loads(r.read(64 * 1024))
        ok = isinstance(body, dict) and body.get("ok") is True
    except Exception as e:
        _log(f"decoder health: {type(e).__name__}")
        body, ok = None, False
    _mark(ok, body if ok else None)
    return body if ok else None
