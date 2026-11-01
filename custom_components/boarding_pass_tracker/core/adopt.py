"""Read-only inspection of a data folder before the config flow adopts it (INTEGRATION_PLAN.md 4.5, C4, 5.8).

inspect(folder) answers the config flow's `adopt` step: what is in /config/boarding_pass_tracker (the export of the
standalone service, or the integration's own folder kept after the entry was removed), and whether the integration
can open it. It never writes into the folder: tracker.db (with its -wal, if any) is copied into a new private
temporary folder, every check runs on the copy, and the copy is deleted in `finally`. The result holds counts and the
stored user names only: never a passenger name, a flight code or a file name.

names_in_use() gives the same user names from the integration's open database (an executor job of the runtime): the
person subentry flow refuses to rename a person whose name the rows use (`name_in_use`).

Both run in the executor (Home Assistant's job pool), never on the event loop.
"""
import hashlib, json, os, re, shutil, sqlite3, stat, tempfile

if __package__:
    from . import engine
else:
    import engine

DATABASE = "tracker.db"
MANIFEST = "manifest.json"
MAX_MANIFEST = 4 * 1024 * 1024
# documents.file: relative to the folder (engine.save_upload); heic too, which the integration stores (Q4)
FILE_RE = re.compile(r"file/\d{4}/\d{2}/[0-9a-f]{16}\.(?:jpg|png|webp|pdf|pkpass|heic)")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TABLES = ("flights", "documents", "boarding_passes")
REASONS = ("broken_database", "newer_database", "foreign_database", "old_database_v0", "file_mismatch")


def _report(state="empty", reason=None, schema=0):
    return {"state": state, "reason": reason, "schema": schema,
            "counts": {"flights": 0, "documents": 0, "boarding_passes": 0}, "files": 0, "queued": 0,
            "unnotified": 0, "users": [], "config_users": [], "mismatched": 0}


def _users(rows, snapshots):
    """The distinct stored names: documents.uploaded_by, boarding_passes.uploaded_by, and the people of each flight's
    snapshot (gate_sent_to, the keys of walk). `rows` and `snapshots` are iterables of 1-tuples (or dicts)."""
    names = set()
    for r in rows:
        v = r[0] if not isinstance(r, dict) else next(iter(r.values()))
        if isinstance(v, str):
            names.add(v)
    for r in snapshots:
        raw = r[0] if not isinstance(r, dict) else next(iter(r.values()))
        try:
            s = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(s, dict):
            continue
        sent = s.get("gate_sent_to") or []
        names.update(u for u in (sent if isinstance(sent, list) else []) if isinstance(u, str))
        walk = s.get("walk") or {}
        names.update(u for u in (walk if isinstance(walk, dict) else {}) if isinstance(u, str))
    return sorted(names)


_USERS_SQL = "SELECT uploaded_by FROM documents UNION SELECT uploaded_by FROM boarding_passes"
_SNAPSHOTS_SQL = "SELECT snapshot FROM flights"


def names_in_use():
    """The stored user names of the integration's open database (engine.q, under the engine's lock)."""
    return _users(engine.q(_USERS_SQL), engine.q(_SNAPSHOTS_SQL))


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _manifest(folder):
    """({relative file: sha256}, [config user names]) of the export's manifest.json; empty when there is none, or
    when it is not a readable JSON object (then documents.sha256 is the reference)."""
    path = os.path.join(folder, MANIFEST)
    try:
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_MANIFEST:
            return {}, []
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return {}, []
    if not isinstance(m, dict):
        return {}, []
    files = m.get("files")
    files = {k: v for k, v in files.items() if isinstance(k, str) and isinstance(v, str) and _SHA256.fullmatch(v)} \
        if isinstance(files, dict) else {}
    users = m.get("config_users")
    users = sorted({u for u in users if isinstance(u, str)}) if isinstance(users, list) else []
    return files, users


def _file_ok(folder, real_folder, rel, sha, listed):
    """documents.file `rel`: the pattern, a regular file (not a link) inside the folder, with the sha256 of its row
    and, when the manifest lists it, of the manifest."""
    if not isinstance(rel, str) or not FILE_RE.fullmatch(rel):
        return False
    path = os.path.join(folder, rel)
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode) or not os.path.realpath(path).startswith(real_folder + os.sep):
        return False
    try:
        digest = _sha256(path)
    except OSError:
        return False
    if digest != sha:
        return False
    return rel not in listed or listed[rel] == digest


def _copy(folder, tmp):
    """tracker.db (+ tracker.db-wal) into `tmp`; the copy's path. The -shm is not copied: SQLite rebuilds it."""
    src = os.path.join(folder, DATABASE)
    dst = os.path.join(tmp, DATABASE)
    shutil.copyfile(src, dst)
    if os.path.isfile(src + "-wal"):
        shutil.copyfile(src + "-wal", dst + "-wal")
    return dst


def inspect(folder):
    """What the folder holds and whether it can be adopted (INTEGRATION_PLAN.md 4.5, 5.8):

      {"state": "empty" | "ok" | "refused", "reason": None or one of REASONS, "schema": user_version,
       "counts": {"flights", "documents", "boarding_passes"}, "files": the documents' files that passed every check,
       "queued": documents in the queue, "unnotified": finished documents not notified yet,
       "users": the stored names, "config_users": the manifest's config names, "mismatched": the documents whose
       file is missing, outside the folder, not a regular file, or with another sha256}

    "empty": no tracker.db (or a database without any table of the tracker): a fresh install. Refusals, in this
    order: broken_database (integrity_check, not a database, a -wal without its tracker.db, an unreadable snapshot),
    newer_database (a schema newer than engine.SCHEMA_VERSION), foreign_database (engine's application id rule),
    old_database_v0 (the v0 schema of App Casa), file_mismatch. The folder is never written."""
    folder = os.path.abspath(folder)
    db = os.path.join(folder, DATABASE)
    if not os.path.lexists(db):
        return _report("refused", "broken_database") if os.path.lexists(db + "-wal") else _report()
    if not os.path.isfile(db):
        return _report("refused", "broken_database")
    tmp = tempfile.mkdtemp(prefix="bpt-adopt-")           # 0700
    try:
        return _inspect_copy(folder, _copy(folder, tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _inspect_copy(folder, path):
    try:
        c = sqlite3.connect(path, isolation_level=None)
    except sqlite3.Error:
        return _report("refused", "broken_database")
    try:
        return _check(folder, c, path)
    except sqlite3.DatabaseError:
        return _report("refused", "broken_database")
    finally:
        c.close()


def _check(folder, c, path):
    one = lambda sql: c.execute(sql).fetchone()[0]           # noqa: E731
    if [tuple(r) for r in c.execute("PRAGMA integrity_check").fetchall()] != [("ok",)]:
        return _report("refused", "broken_database")
    version = one("PRAGMA user_version")
    if version > engine.SCHEMA_VERSION:
        return _report("refused", "newer_database", version)
    try:
        engine._application_id(c, path, version)
    except engine.DatabaseRefused as e:
        return _report("refused", e.reason, version)
    if version == 0 and engine._table_exists(c, "documenti"):
        return _report("refused", "old_database_v0", version)
    present = [t for t in _TABLES if engine._table_exists(c, t)]
    if not present and version == 0:
        return _report(schema=version)                       # a new, empty file: open_db makes the schema
    if len(present) != len(_TABLES):
        return _report("refused", "broken_database", version)
    out = _report("ok", None, version)
    out["counts"] = {t: one(f"SELECT count(*) FROM {t}") for t in _TABLES}
    out["queued"] = one("SELECT count(*) FROM documents WHERE status='in_coda'")
    out["unnotified"] = one("SELECT count(*) FROM documents WHERE notified=0 AND status IN "
                            "('letto','archiviato','errore')")
    snapshots = c.execute(_SNAPSHOTS_SQL).fetchall()
    for (raw,) in snapshots:
        try:
            if not isinstance(json.loads(raw or "{}"), dict):
                raise ValueError
        except (TypeError, ValueError):
            return _report("refused", "broken_database", version)
    out["users"] = _users(c.execute(_USERS_SQL).fetchall(), snapshots)
    listed, out["config_users"] = _manifest(folder)
    real_folder = os.path.realpath(folder)
    good = bad = 0
    for rel, sha in c.execute("SELECT file, sha256 FROM documents ORDER BY id").fetchall():
        if _file_ok(folder, real_folder, rel, sha, listed):
            good += 1
        else:
            bad += 1
    out["files"], out["mismatched"] = good, bad
    if bad:
        out["state"], out["reason"] = "refused", "file_mismatch"
    return out
