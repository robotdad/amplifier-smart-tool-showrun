"""SQLite reservations survive process failure. No lease expiry implies permission to replay."""

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote

from .errors import ShowrunError
from .schema import validate_new
from .stories_helper import process_identity, same_process


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def atomic_json(path, value):
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    if os.name == 'nt':
        # Windows cannot open directories with os.open. The file was flushed;
        # atomic replacement remains, but directory fsync is not claimed.
        return
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Store:
    def __init__(self, root, readonly=False):
        self.root = Path(root).expanduser().resolve()
        self.db_path = self.root / "takes.sqlite3"
        self.readonly = readonly
        if readonly:
            if not self.db_path.is_file():
                raise ShowrunError("not_found", "No retained store exists.", "Check storage and request_id.")
            return
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS takes
                (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL,
                 cancel INTEGER NOT NULL DEFAULT 0, pid INTEGER NOT NULL)""")
            if "owner" not in {r[1] for r in db.execute("PRAGMA table_info(takes)")}:
                db.execute("ALTER TABLE takes ADD COLUMN owner TEXT")
        self.db_path.chmod(0o600)

    def connect(self):
        if self.readonly:
            return sqlite3.connect("file:" + quote(str(self.db_path)) + "?mode=ro", uri=True, timeout=10)
        db = sqlite3.connect(self.db_path, timeout=10)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def reserve(self, request, model, receipt):
        fingerprint = hashlib.sha256(canonical({"request": request, "model": model}).encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT fingerprint,receipt FROM takes WHERE id=?", (request["request_id"],)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise ShowrunError("request_conflict", "This request_id already names different effective inputs.")
                return json.loads(row[1])
            # Same transaction as key comparison: old exact retries/conflicts win,
            # but invalid new shapes never consume a key or artifact directory.
            validate_new(request)
            try:
                (self.root / request["request_id"]).mkdir(mode=0o700)
            except FileExistsError:
                raise ShowrunError("artifact_conflict", "This take's output directory already exists.",
                                   "Preserve the existing files; select a new request_id.") from None
            db.execute("INSERT INTO takes(id,fingerprint,receipt,pid,owner) VALUES(?,?,?,?,?)",
                       (request["request_id"], fingerprint, canonical(receipt), os.getpid(),
                        canonical(process_identity(os.getpid()))))
        return None

    def save(self, receipt):
        with self.connect() as db:
            db.execute("UPDATE takes SET receipt=? WHERE id=?", (canonical(receipt), receipt["request_id"]))
        atomic_json(self.directory(receipt["request_id"]) / "receipt.json", receipt)

    def directory(self, request_id):
        folder = self.root / request_id
        if folder.is_symlink() or folder.resolve().parent != self.root:
            raise ShowrunError("artifact_scope", "Take storage no longer resolves within the selected store.")
        return folder

    def status(self, request_id):
        with self.connect() as db:
            owner = "owner" if "owner" in {r[1] for r in db.execute("PRAGMA table_info(takes)")} else "NULL"
            row = db.execute(f"SELECT receipt,cancel,pid,{owner} FROM takes WHERE id=?", (request_id,)).fetchone()
        if not row:
            raise ShowrunError("not_found", "No retained request with that ID.", "Check request_id and storage.")
        result = json.loads(row[0])
        result["cancel_requested"] = bool(row[1])
        if result["status"] == "running":
            try:
                identity = json.loads(row[3]) if row[3] else None
            except ValueError:
                identity = None
            if not isinstance(identity, dict) or identity.get("pid") != row[2] or not same_process(identity):
                result["status"] = "uncertain"
                result["notice"] = "Exact owner identity cannot be verified; work and cleanup may be incomplete. Never replay this ID."
                for step in result.get("steps", []):
                    if step["status"] == "in_progress":
                        step["status"] = "uncertain"
        return result

    def cancelled(self, request_id):
        with self.connect() as db:
            return bool(db.execute("SELECT cancel FROM takes WHERE id=?", (request_id,)).fetchone()[0])

    def cancel(self, request_id):
        with self.connect() as db:
            # Serialize the decision with receipt writes. A terminal save that
            # wins this lock must not receive a later cancellation flag.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT receipt,cancel FROM takes WHERE id=?", (request_id,)).fetchone()
            if not row:
                raise ShowrunError("not_found", "No retained request with that ID.", "Check request_id and storage.")
            status = json.loads(row[0])["status"]
            if status != "running":
                return {"request_id": request_id, "status": "already_terminal",
                        "operation_status": status, "cancel_requested": bool(row[1]),
                        "notice": "No change. The retained outcome and cleanup evidence remain authoritative."}
            db.execute("UPDATE takes SET cancel=1 WHERE id=?", (request_id,))
        return {"request_id": request_id, "status": "cancellation_requested",
                "notice": "Acknowledgment only; work may finish before observing this request. "
                          "Poll status for outcome and owned cleanup; no rollback or crash recovery."}
