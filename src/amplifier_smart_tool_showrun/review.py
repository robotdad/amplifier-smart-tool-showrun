"""Library-first retained capture review and management.

The review store is deliberately separate from the execution store.  It reads a
configured Showrun take root, snapshots the public receipt into review metadata,
and never changes a receipt or starts a target.  All identifiers accepted by
this module are database identities; callers never supply filesystem paths for
media or receipts.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Showrun review targets POSIX hosts.
    fcntl = None

from .errors import ShowrunError, require

MAX_PAGE = 100
MAX_MEDIA_READ = 512 * 1024
MAX_TRANSFER_BYTES = 256 * 1024 * 1024
MAX_NOTE = 4000
MAX_NAME = 200
IDENTITY = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
NAME = re.compile(r"[^\x00-\x1f\x7f]{1,200}\Z")
THEMES = {"light", "dark", "system"}
INTENT_SCHEMA_VERSION = 1
INTENT_STATES = {
    "pending",                  # admitted, effect may not have started
    "prepared",                 # an intermediate effect (draft save) is retained
    "completed_unacknowledged", # effect receipt exists; UI has not acknowledged it
    "acknowledged",              # receipt was consumed by this named workspace
    "rejected",                 # known no-effect failure; never retry automatically
    "revoked",                  # target was deleted or scope was withdrawn
}
DELETE_LEASE_SECONDS = 5.0
_ACTIVE_DELETION_LOCK = threading.RLock()
_ACTIVE_DELETIONS: set[str] = set()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity(value: str, label: str = "identity") -> str:
    require(isinstance(value, str) and IDENTITY.fullmatch(value), f"Invalid {label}.", "invalid_identity")
    return value


def _display_name(value: str) -> str:
    require(isinstance(value, str) and value.strip() and len(value) <= MAX_NAME and NAME.fullmatch(value),
            "Name must be nonempty text without control characters.", "invalid_name")
    return value.strip()


def _json(value: str | None, fallback: Any = None) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


class ReviewStore:
    """A bounded metadata store over one explicitly configured retained take root."""

    def __init__(
        self,
        takes_root: str | os.PathLike[str],
        metadata_root: str | os.PathLike[str] | None = None,
        readonly: bool = False,
        workspace_scopes: dict[str, list[str] | set[str] | None] | None = None,
    ):
        raw_root = Path(takes_root).expanduser()
        require(not raw_root.is_symlink(), "The configured take store must not be a symlink.", "store_scope")
        self.takes_root = raw_root.resolve()
        self.metadata_root = Path(metadata_root).expanduser().resolve() if metadata_root else self.takes_root / ".showrun-review"
        require(self.metadata_root != self.takes_root and self.metadata_root != self.takes_root.parent,
                "Review metadata must use a dedicated directory.", "store_scope")
        self.readonly = readonly
        self._sync_lock = threading.Lock()
        self._scope_enforced = workspace_scopes is not None
        self.workspace_scopes = {
            workspace: None if demos is None else {_identity(item, "demo ID") for item in demos}
            for workspace, demos in (workspace_scopes or {}).items()
        }
        self.db_path = self.metadata_root / "review.sqlite3"
        if readonly:
            require(self.db_path.is_file() and not self.db_path.is_symlink(),
                    "No retained review metadata exists.", "not_found")
        else:
            require(not self.metadata_root.is_symlink(), "Review metadata must not be a symlink.", "store_scope")
            self.metadata_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if self.readonly:
            db = sqlite3.connect(
                f"file:{self.db_path}?mode=ro", uri=True, timeout=10, check_same_thread=False
            )
            db.execute("PRAGMA busy_timeout=10000")
            db.row_factory = sqlite3.Row
            return db
        db = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        db.row_factory = sqlite3.Row
        return db

    def _init_db(self) -> None:
        with self._database_write_lock():
            self._init_db_unlocked()

    def _init_db_unlocked(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS demos (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    explicit_group INTEGER NOT NULL DEFAULT 0,
                    created REAL NOT NULL,
                    updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS takes (
                    id TEXT PRIMARY KEY,
                    demo_id TEXT NOT NULL REFERENCES demos(id),
                    name TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    receipt_json TEXT NOT NULL,
                    receipt_sha256 TEXT NOT NULL,
                    receipt_path TEXT NOT NULL,
                    discovered_at REAL NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    integrity TEXT NOT NULL DEFAULT 'verified'
                );
                CREATE TABLE IF NOT EXISTS clips (
                    id TEXT PRIMARY KEY,
                    demo_id TEXT NOT NULL,
                    take_id TEXT NOT NULL REFERENCES takes(id),
                    name TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    media_path TEXT,
                    media_sha256 TEXT,
                    media_bytes INTEGER,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    discovered_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workspaces (
                    id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL DEFAULT 0,
                    selection_json TEXT,
                    invalidated_json TEXT,
                    playback_json TEXT,
                    draft_json TEXT,
                    appearance TEXT NOT NULL DEFAULT 'system',
                    updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS playback_positions (
                    workspace_id TEXT NOT NULL,
                    clip_id TEXT NOT NULL,
                    time_seconds REAL NOT NULL,
                    PRIMARY KEY(workspace_id, clip_id)
                );
                CREATE TABLE IF NOT EXISTS drafts (
                    workspace_id TEXT NOT NULL,
                    clip_id TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    text TEXT NOT NULL,
                    anchor_json TEXT NOT NULL,
                    updated REAL NOT NULL,
                    PRIMARY KEY(workspace_id, clip_id)
                );
                CREATE TABLE IF NOT EXISTS step_drafts (
                    workspace_id TEXT NOT NULL, clip_id TEXT NOT NULL, step_id TEXT NOT NULL,
                    version INTEGER NOT NULL, text TEXT NOT NULL, anchor_json TEXT NOT NULL,
                    updated REAL NOT NULL, PRIMARY KEY(workspace_id, clip_id, step_id)
                );
                CREATE TABLE IF NOT EXISTS notes (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    clip_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    anchor_json TEXT NOT NULL,
                    created REAL NOT NULL,
                    request_id TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS operations (
                    request_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS delete_intents (
                    token TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    workspace_id TEXT,
                    scope TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    expected_version INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    commit_request_id TEXT,
                    state TEXT NOT NULL DEFAULT 'prepared',
                    result_json TEXT,
                    effect_started REAL,
                    cleanup_owner TEXT,
                    cleanup_pid INTEGER,
                    cleanup_thread INTEGER,
                    cleanup_heartbeat REAL,
                    created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS review_intents (
                    intent_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    result_json TEXT,
                    created REAL NOT NULL,
                    updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS request_tombstones (
                    request_id TEXT PRIMARY KEY,
                    take_id TEXT NOT NULL,
                    deleted_at REAL NOT NULL,
                    result_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS clips_by_take ON clips(take_id);
                CREATE INDEX IF NOT EXISTS takes_by_demo ON takes(demo_id);
                CREATE INDEX IF NOT EXISTS operations_by_kind ON operations(kind);
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(delete_intents)")}
            migrations = {
                "workspace_id": "ALTER TABLE delete_intents ADD COLUMN workspace_id TEXT",
                "commit_request_id": "ALTER TABLE delete_intents ADD COLUMN commit_request_id TEXT",
                "state": "ALTER TABLE delete_intents ADD COLUMN state TEXT NOT NULL DEFAULT 'prepared'",
                "result_json": "ALTER TABLE delete_intents ADD COLUMN result_json TEXT",
                "effect_started": "ALTER TABLE delete_intents ADD COLUMN effect_started REAL",
                "cleanup_owner": "ALTER TABLE delete_intents ADD COLUMN cleanup_owner TEXT",
                "cleanup_pid": "ALTER TABLE delete_intents ADD COLUMN cleanup_pid INTEGER",
                "cleanup_thread": "ALTER TABLE delete_intents ADD COLUMN cleanup_thread INTEGER",
                "cleanup_heartbeat": "ALTER TABLE delete_intents ADD COLUMN cleanup_heartbeat REAL",
            }
            for column, statement in migrations.items():
                if column not in columns:
                    db.execute(statement)
        self.db_path.chmod(0o600)

    @contextmanager
    def _database_write_lock(self):
        """Serialize cross-process metadata discovery with a bounded OS lock."""
        if self.readonly or fcntl is None:
            yield
            return
        lock_path = self.metadata_root / "review.sqlite3.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        deadline = time.monotonic() + 10.0
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ShowrunError(
                            "database_busy",
                            "The retained review metadata is busy; retry this bounded operation.",
                            "Retry after the current review read finishes.",
                        ) from None
                    time.sleep(0.01)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _require_write(self) -> None:
        require(not self.readonly, "This review store is read-only.", "read_only")

    def _assert_workspace_scope(self, workspace_id: str | None) -> str:
        require(isinstance(workspace_id, str), "An explicitly authorized review workspace is required.",
                "scope_required")
        _identity(workspace_id, "workspace ID")
        if self._scope_enforced:
            require(workspace_id in self.workspace_scopes,
                    "This review workspace is outside the authorized scope.", "scope_denied")
        return workspace_id

    def _assert_demo_scope(self, workspace_id: str | None, demo_id: str) -> None:
        workspace_id = self._assert_workspace_scope(workspace_id) if self._scope_enforced else workspace_id
        if workspace_id is None:
            return
        allowed = self.workspace_scopes.get(workspace_id)
        if allowed is not None:
            require(demo_id in allowed, "The demo is outside this review workspace scope.", "scope_denied")

    def set_workspace_scope(self, workspace_id: str, demo_ids: list[str] | set[str] | None) -> None:
        """Set an in-process scope for a service; the scope is never inferred from names."""
        _identity(workspace_id, "workspace ID")
        self._scope_enforced = True
        self.workspace_scopes[workspace_id] = (
            None if demo_ids is None else {_identity(item, "demo ID") for item in demo_ids}
        )

    def _safe_child(self, parent: Path, relative: str, *, must_exist: bool = False) -> Path:
        require(isinstance(relative, str) and relative and not Path(relative).is_absolute(),
                "Retained artifact reference is not relative.", "artifact_scope")
        candidate = parent / relative
        resolved_parent = parent.resolve()
        require(not candidate.is_symlink() and candidate.resolve().parent == resolved_parent,
                "Retained artifact reference left its take.", "artifact_scope")
        if must_exist:
            require(candidate.is_file() and not candidate.is_symlink(), "Retained artifact is missing.",
                    "artifact_missing")
        return candidate

    def _take_dir(self, take_id: str) -> Path:
        _identity(take_id, "take ID")
        return self._safe_child(self.takes_root, take_id)

    def _receipt_path(self, take_id: str) -> Path:
        return self._safe_child(self._take_dir(take_id), "receipt.json")

    def _read_receipt(self, take_id: str) -> tuple[dict[str, Any], str]:
        path = self._receipt_path(take_id)
        require(path.is_file() and not path.is_symlink(), "Retained receipt is missing.", "receipt_missing")
        try:
            raw = path.read_bytes()
            receipt = json.loads(raw)
        except (OSError, ValueError, TypeError):
            raise ShowrunError("receipt_invalid", "Retained receipt is not valid JSON.",
                               "Preserve the original take and inspect its receipt manually.") from None
        require(isinstance(receipt, dict), "Retained receipt must be an object.", "receipt_invalid")
        return receipt, hashlib.sha256(raw).hexdigest()

    def _media_info(self, take_id: str, receipt: dict[str, Any], stored_hash: str | None = None) -> dict[str, Any]:
        try:
            current, _ = self._read_receipt(take_id)
        except ShowrunError:
            return {"status": "unavailable", "reason": "The retained receipt is unavailable."}
        if current != receipt:
            return {"status": "restricted" if current.get("restricted") else "changed",
                    "reason": "The retained receipt changed; media is withheld."}
        media = receipt.get("media")
        if receipt.get("restricted"):
            return {"status": "restricted", "reason": "Sensitive media is withheld."}
        if not isinstance(media, dict):
            return {"status": "unfinalized", "reason": "The take has no verified retained media."}
        relative = media.get("path")
        if relative != "capture.mp4":
            return {"status": "unavailable", "reason": "The retained format is not a direct MP4 clip."}
        try:
            path = self._safe_child(self._take_dir(take_id), relative)
        except ShowrunError as error:
            return {"status": "restricted", "reason": error.message}
        if not path.is_file() or path.is_symlink():
            return {"status": "missing", "reason": "The retained MP4 is missing."}
        expected = stored_hash or media.get("sha256")
        if not isinstance(expected, str) or _file_hash(path) != expected:
            return {"status": "changed", "reason": "The retained MP4 no longer matches its receipt."}
        return {
            "status": "available",
            "path": relative,
            "sha256": expected,
            "bytes": path.stat().st_size,
            "mime_type": "video/mp4",
            "duration_seconds": media.get("duration_seconds"),
        }

    def sync(self) -> dict[str, int]:
        with self._database_write_lock():
            return self._sync_unlocked()

    def _sync_unlocked(self) -> dict[str, int]:
        """Discover direct child take directories; never recursively scan a user store."""
        self._require_write()
        now = time.time()
        discovered = created = changed = 0
        require(self.takes_root.is_dir() and not self.takes_root.is_symlink(),
                "Configured retained take store is missing.", "store_missing")
        entries = sorted(self.takes_root.iterdir(), key=lambda item: item.name)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for folder in entries:
                if not folder.is_dir() or folder.is_symlink() or not IDENTITY.fullmatch(folder.name):
                    continue
                receipt_path = folder / "receipt.json"
                if not receipt_path.is_file() or receipt_path.is_symlink():
                    continue
                take_id = folder.name
                try:
                    receipt, receipt_sha = self._read_receipt(take_id)
                except ShowrunError:
                    continue
                discovered += 1
                row = db.execute("SELECT * FROM takes WHERE id=?", (take_id,)).fetchone()
                if row is None:
                    demo_id = f"demo-{take_id}"
                    demo_name = receipt.get("request", {}).get("context") or take_id
                    # Context is useful evidence but a poor display name. Legacy
                    # groups remain one-take groups unless explicitly regrouped.
                    demo_name = take_id if not isinstance(demo_name, str) else take_id
                    db.execute(
                        "INSERT OR IGNORE INTO demos(id,name,created,updated) VALUES(?,?,?,?)",
                        (demo_id, demo_name, now, now),
                    )
                    media = receipt.get("media") if isinstance(receipt.get("media"), dict) else {}
                    media_path = media.get("path") if media.get("path") == "capture.mp4" else None
                    db.execute(
                        """INSERT INTO takes(id,demo_id,name,receipt_json,receipt_sha256,receipt_path,discovered_at)
                           VALUES(?,?,?,?,?,?,?)""",
                        (take_id, demo_id, take_id, _canonical(receipt), receipt_sha, "receipt.json", now),
                    )
                    db.execute(
                        """INSERT INTO clips(id,demo_id,take_id,name,media_path,media_sha256,media_bytes,discovered_at)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (
                            f"clip-{take_id}",
                            demo_id,
                            take_id,
                            "Clip 1",
                            media_path,
                            media.get("sha256") if media_path else None,
                            media.get("bytes") if media_path else None,
                            now,
                        ),
                    )
                    created += 1
                    continue
                if row["receipt_sha256"] != receipt_sha:
                    db.execute("UPDATE takes SET integrity='changed' WHERE id=?", (take_id,))
                    changed += 1
        return {"discovered": discovered, "created": created, "changed": changed}

    def _sync_read(self) -> None:
        # A write-capable sync is intentional: metadata is separate, and discovery
        # does not alter execution evidence. Read-only callers use an already-built
        # index and are not allowed to scan/create one.
        if not self.readonly:
            with self._sync_lock:
                self.sync()

    def register_demo(
        self,
        name: str,
        demo_id: str | None = None,
        *,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_write()
        name = _display_name(name)
        demo_id = _identity(demo_id, "demo ID") if demo_id else f"demo-{uuid.uuid4().hex}"
        if self._scope_enforced:
            workspace_id = self._assert_workspace_scope(workspace_id)
        if workspace_id is not None:
            self._assert_demo_scope(workspace_id, demo_id)
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            require(db.execute("SELECT 1 FROM demos WHERE id=?", (demo_id,)).fetchone() is None,
                    "That demo identity already exists.", "already_exists")
            db.execute("INSERT INTO demos(id,name,created,updated,explicit_group) VALUES(?,?,?,?,1)",
                       (demo_id, name, now, now))
        return self.get_demo(demo_id, workspace_id=workspace_id)

    create_demo = register_demo

    def attach_take(
        self,
        demo_id: str,
        take_id: str,
        expected_version: int | None = None,
        source_expected_version: int | None = None,
        *,
        workspace_id: str | None = None,
        workspace_expected_version: int | None = None,
    ) -> dict[str, Any]:
        self._require_write()
        _identity(demo_id, "demo ID")
        _identity(take_id, "take ID")
        if self._scope_enforced:
            workspace_id = self._assert_workspace_scope(workspace_id)
        if workspace_id is not None:
            self._assert_demo_scope(workspace_id, demo_id)
        self._sync_read()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            demo = db.execute("SELECT * FROM demos WHERE id=? AND deleted=0", (demo_id,)).fetchone()
            take = db.execute("SELECT * FROM takes WHERE id=? AND deleted=0", (take_id,)).fetchone()
            require(demo is not None and take is not None, "Demo or take was not found.", "not_found")
            source_demo = db.execute(
                "SELECT * FROM demos WHERE id=? AND deleted=0", (take["demo_id"],)
            ).fetchone()
            require(source_demo is not None, "The take's current demo is not available.", "not_found")
            if workspace_id is not None:
                self._assert_demo_scope(workspace_id, source_demo["id"])
            if expected_version is not None:
                require(expected_version == demo["version"], "The demo changed; refresh before grouping.",
                        "review_conflict")
            if source_demo["id"] != demo_id:
                if source_expected_version is None and not self._scope_enforced:
                    source_expected_version = source_demo["version"]
                require(
                    type(source_expected_version) is int
                    and source_expected_version == source_demo["version"],
                    "The take's current demo changed; refresh both demos before grouping.",
                    "review_conflict",
                )
            if workspace_id is not None:
                require(
                    type(workspace_expected_version) is int,
                    "Grouping requires the current workspace version.",
                    "review_conflict",
                )
                workspace = self._ensure_workspace(db, workspace_id)
                require(
                    workspace_expected_version == workspace["version"],
                    "Review state changed; refresh before grouping.",
                    "review_conflict",
                )
            else:
                workspace = None
            if source_demo["id"] == demo_id:
                return self._demo_public(db, demo)
            db.execute("UPDATE takes SET demo_id=?,version=version+1 WHERE id=?", (demo_id, take_id))
            db.execute("UPDATE clips SET demo_id=? WHERE take_id=?", (demo_id, take_id))
            db.execute("UPDATE demos SET version=version+1,updated=? WHERE id=?", (time.time(), demo_id))
            db.execute(
                "UPDATE demos SET version=version+1,updated=? WHERE id=?",
                (time.time(), source_demo["id"]),
            )
            if workspace is not None:
                selection = _json(workspace["selection_json"])
                if selection and selection.get("take_id") == take_id:
                    selection = {**selection, "demo_id": demo_id}
                    db.execute(
                        "UPDATE workspaces SET selection_json=?,version=version+1,updated=? WHERE id=?",
                        (_canonical(selection), time.time(), workspace_id),
                    )
                else:
                    db.execute(
                        "UPDATE workspaces SET version=version+1,updated=? WHERE id=?",
                        (time.time(), workspace_id),
                    )
        return self.get_demo(demo_id, workspace_id=workspace_id)

    group_take = attach_take

    def _ensure_workspace(self, db: sqlite3.Connection, workspace_id: str) -> sqlite3.Row:
        self._assert_workspace_scope(workspace_id)
        row = db.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
        if row is None:
            require(not self.readonly, "Review workspace has not been initialized.", "not_found")
            now = time.time()
            db.execute("INSERT INTO workspaces(id,updated) VALUES(?,?)", (workspace_id, now))
            row = db.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
        return row

    def open_workspace(self, workspace_id: str = "default") -> dict[str, Any]:
        self._sync_read()
        with self._connect() as db:
            self._ensure_workspace(db, workspace_id)
        return self.workspace(workspace_id)

    def _operation(self, db: sqlite3.Connection, request_id: str, kind: str, payload: Any) -> dict[str, Any] | None:
        _identity(request_id, "request ID")
        fingerprint = _hash({"kind": kind, "payload": payload})
        row = db.execute("SELECT fingerprint,result_json FROM operations WHERE request_id=?", (request_id,)).fetchone()
        if row:
            require(row["fingerprint"] == fingerprint, "This request ID was already used for different inputs.",
                    "request_conflict")
            return _json(row["result_json"], {})
        return None

    def _save_operation(self, db: sqlite3.Connection, request_id: str, kind: str, payload: Any, result: Any) -> None:
        db.execute("INSERT INTO operations(request_id,kind,fingerprint,result_json,created) VALUES(?,?,?,?,?)",
                   (request_id, kind, _hash({"kind": kind, "payload": payload}), _canonical(result), time.time()))

    def _row_demo(self, db: sqlite3.Connection, demo_id: str, include_deleted: bool = False) -> sqlite3.Row:
        query = "SELECT * FROM demos WHERE id=?" + ("" if include_deleted else " AND deleted=0")
        row = db.execute(query, (demo_id,)).fetchone()
        require(row is not None, "The requested demo is not available.", "not_found")
        return row

    def _row_take(self, db: sqlite3.Connection, take_id: str, include_deleted: bool = False) -> sqlite3.Row:
        query = "SELECT * FROM takes WHERE id=?" + ("" if include_deleted else " AND deleted=0")
        row = db.execute(query, (take_id,)).fetchone()
        require(row is not None, "The requested take is not available.", "not_found")
        return row

    def _row_clip(self, db: sqlite3.Connection, clip_id: str, include_deleted: bool = False) -> sqlite3.Row:
        query = "SELECT * FROM clips WHERE id=?" + ("" if include_deleted else " AND deleted=0")
        row = db.execute(query, (clip_id,)).fetchone()
        require(row is not None, "The requested clip is not available.", "not_found")
        return row

    def _clip_public(self, row: sqlite3.Row, take: sqlite3.Row | None = None) -> dict[str, Any]:
        take = take or row
        receipt = _json(take["receipt_json"], {})
        info = self._media_info(take["id"], receipt, row["media_sha256"])
        return {
            "id": row["id"],
            "clip_id": row["id"],
            "demo_id": row["demo_id"],
            "take_id": row["take_id"],
            "name": row["name"],
            "version": row["version"],
            "status": "deleted" if row["deleted"] else info["status"],
            "media": {k: v for k, v in info.items() if k != "path"} if info.get("status") != "available" else info,
            "content_sha256": row["media_sha256"],
            "receipt_sha256": take["receipt_sha256"],
            "duration_seconds": (receipt.get("media") or {}).get("duration_seconds"),
            "deleted": bool(row["deleted"]),
        }

    def _take_public(self, row: sqlite3.Row, include_receipt: bool = False) -> dict[str, Any]:
        receipt = _json(row["receipt_json"], {})
        clips = []
        with self._connect() as db:
            for clip in db.execute("SELECT * FROM clips WHERE take_id=? ORDER BY id", (row["id"],)):
                clips.append(self._clip_public(clip, row))
        result = {
            "id": row["id"],
            "take_id": row["id"],
            "demo_id": row["demo_id"],
            "name": row["name"],
            "version": row["version"],
            "status": "deleted" if row["deleted"] else receipt.get("status", "unknown"),
            "added_at": row["discovered_at"],
            "partial": bool(receipt.get("partial", receipt.get("status") != "succeeded")),
            "integrity": row["integrity"],
            "receipt_sha256": row["receipt_sha256"],
            "clips": clips,
            "steps": [
                {
                    "id": step.get("id"),
                    "status": step.get("status"),
                    "requested": step.get("requested"),
                    "interval": step.get("interval"),
                    "error": step.get("error"),
                }
                for step in receipt.get("steps", [])[:200]
                if isinstance(step, dict)
            ],
            "error": receipt.get("error"),
            "cleanup": receipt.get("cleanup"),
            "media": receipt.get("media"),
        }
        if include_receipt:
            result["receipt"] = receipt
        return result

    def _demo_public(self, db: sqlite3.Connection, row: sqlite3.Row, include_deleted: bool = False) -> dict[str, Any]:
        query = "SELECT * FROM takes WHERE demo_id=?" + ("" if include_deleted else " AND deleted=0") + " ORDER BY discovered_at,id"
        takes = [self._take_public(take) for take in db.execute(query, (row["id"],))]
        return {
            "id": row["id"],
            "demo_id": row["id"],
            "name": row["name"],
            "version": row["version"],
            "deleted": bool(row["deleted"]),
            "explicit_group": bool(row["explicit_group"]),
            "added_at": row["created"],
            "recent_at": max([row["created"], *(take["added_at"] for take in takes)]),
            "takes": takes,
            "take_count": len(takes),
        }

    def list_demos(self, workspace_id: str = "default", offset: int = 0, limit: int = MAX_PAGE) -> dict[str, Any]:
        self.open_workspace(workspace_id)
        require(type(offset) is int and 0 <= offset <= 1_000_000 and type(limit) is int and 1 <= limit <= MAX_PAGE,
                "Page bounds are invalid.", "invalid_range")
        with self._connect() as db:
            rows = [
                row for row in db.execute("SELECT * FROM demos WHERE deleted=0 ORDER BY created,id").fetchall()
                if self._demo_in_scope(workspace_id, row["id"])
            ][offset:offset + limit]
            total = sum(1 for row in db.execute("SELECT id FROM demos WHERE deleted=0")
                        if self._demo_in_scope(workspace_id, row["id"]))
            return {"workspace_id": workspace_id, "demos": [self._demo_public(db, row) for row in rows],
                    "offset": offset, "limit": limit, "total": total, "has_more": offset + len(rows) < total}

    def _demo_in_scope(self, workspace_id: str, demo_id: str) -> bool:
        allowed = self.workspace_scopes.get(workspace_id)
        return allowed is None or demo_id in allowed

    def get_demo(self, demo_id: str, include_deleted: bool = False, *, workspace_id: str | None = None) -> dict[str, Any]:
        _identity(demo_id, "demo ID")
        if self._scope_enforced:
            workspace_id = self._assert_workspace_scope(workspace_id)
        if workspace_id is not None:
            self._assert_demo_scope(workspace_id, demo_id)
        self._sync_read()
        with self._connect() as db:
            return self._demo_public(db, self._row_demo(db, demo_id, include_deleted), include_deleted)

    def get_take(
        self,
        take_id: str,
        include_deleted: bool = False,
        include_receipt: bool = True,
        *,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        _identity(take_id, "take ID")
        if self._scope_enforced:
            workspace_id = self._assert_workspace_scope(workspace_id)
        self._sync_read()
        with self._connect() as db:
            take = self._row_take(db, take_id, include_deleted)
            if workspace_id is not None:
                self._assert_demo_scope(workspace_id, take["demo_id"])
            return self._take_public(take, include_receipt)

    def get_clip(self, clip_id: str, include_deleted: bool = False, *, workspace_id: str | None = None) -> dict[str, Any]:
        _identity(clip_id, "clip ID")
        if self._scope_enforced:
            workspace_id = self._assert_workspace_scope(workspace_id)
        self._sync_read()
        with self._connect() as db:
            clip = self._row_clip(db, clip_id, include_deleted)
            if workspace_id is not None:
                self._assert_demo_scope(workspace_id, clip["demo_id"])
            take = self._row_take(db, clip["take_id"], True)
            return self._clip_public(clip, take)

    @staticmethod
    def _intent_public(row: sqlite3.Row) -> dict[str, Any]:
        # A revoked intent is deliberately still visible as a tombstone, but its
        # original target, text, anchors and effect receipt are not recoverable.
        revoked = row["state"] == "revoked"
        return {
            "intent_id": row["intent_id"],
            "workspace_id": row["workspace_id"],
            "kind": row["kind"],
            "state": row["state"],
            "payload": None if revoked else _json(row["payload_json"], {}),
            "result": (
                {"status": "revoked", "intent_id": row["intent_id"], "reason": "review_deleted"}
                if revoked else _json(row["result_json"]) if row["result_json"] else None
            ),
            "created": row["created"],
            "updated": row["updated"],
        }

    def _intent_target(
        self,
        db: sqlite3.Connection,
        workspace_id: str,
        envelope: dict[str, Any],
    ) -> tuple[sqlite3.Row, sqlite3.Row, sqlite3.Row]:
        """Authorize the exact retained target before using or persisting an intent."""
        require(
            envelope.get("schema_version") == INTENT_SCHEMA_VERSION
            and envelope.get("workspace_id") == workspace_id,
            "The review intent envelope is invalid for this workspace.",
            "scope_denied",
        )
        target = envelope.get("target")
        require(isinstance(target, dict), "The review intent has no exact retained target.", "invalid_target")
        demo_id = target.get("demo_id")
        take_id = target.get("take_id")
        clip_id = target.get("clip_id")
        require(
            all(isinstance(value, str) for value in (demo_id, take_id, clip_id))
            and "content_sha256" in target,
            "The review intent target is incomplete.",
            "invalid_target",
        )
        _identity(demo_id, "demo ID")
        _identity(take_id, "take ID")
        _identity(clip_id, "clip ID")
        self._assert_demo_scope(workspace_id, demo_id)
        demo = self._row_demo(db, demo_id)
        take = self._row_take(db, take_id)
        clip = self._row_clip(db, clip_id)
        require(
            take["demo_id"] == demo_id and clip["demo_id"] == demo_id and clip["take_id"] == take_id,
            "The review intent target relationships changed.",
            "review_conflict",
        )
        require(
            clip["media_sha256"] == target.get("content_sha256"),
            "The retained clip media identity changed.",
            "review_conflict",
        )
        return demo, take, clip

    def _normalize_intent(
        self,
        db: sqlite3.Connection,
        workspace_id: str,
        kind: str,
        payload: dict[str, Any],
        *, check_version: bool = True,
    ) -> dict[str, Any]:
        """Convert legacy flattened callers into one bounded, versioned envelope."""
        require(kind in {"save_draft", "submit_note"}, "Unsupported review intent.", "invalid_target")
        require(isinstance(payload, dict), "Review intent payload must be an object.", "invalid_request")
        nested_target = payload.get("target")
        nested_payload = payload.get("payload")
        if isinstance(nested_target, dict) and isinstance(nested_payload, dict):
            target = dict(nested_target)
            operation = dict(nested_payload)
        else:
            target = {
                key: payload.get(key)
                for key in ("demo_id", "take_id", "clip_id", "content_sha256")
                if key in payload
            }
            operation = {
                key: payload.get(key)
                for key in (
                    "text", "anchor", "expected_version", "request_id",
                    "save_request_id", "submit_request_id",
                    "step_id", "time_seconds", "range_start_seconds", "range_end_seconds",
                )
                if key in payload
            }
            if "anchor" not in operation:
                operation["anchor"] = {
                    key: operation.pop(key)
                    for key in ("step_id", "time_seconds", "range_start_seconds", "range_end_seconds")
                    if key in operation
                }
        require(payload.get("workspace_id", workspace_id) == workspace_id,
                "Review intent workspace does not match its envelope.", "scope_denied")
        require(isinstance(target, dict) and isinstance(operation, dict),
                "Review intent target and payload must be objects.", "invalid_request")
        require("content_sha256" in target,
                "The review intent must include the exact retained media identity.", "invalid_target")
        envelope = {
            "schema_version": INTENT_SCHEMA_VERSION,
            "workspace_id": workspace_id,
            "target": target,
            "payload": operation,
        }
        _, take, clip = self._intent_target(db, workspace_id, envelope)
        text = operation.get("text")
        require(isinstance(text, str) and len(text) <= MAX_NOTE,
                "Review intent text is invalid or too long.", "invalid_note")
        anchor = operation.get("anchor")
        require(isinstance(anchor, dict), "Review intent anchor must be an object.", "invalid_anchor")
        anchor = self._validate_anchor(take, clip, anchor)
        expected_version = operation.get("expected_version")
        require(type(expected_version) is int, "Review intent needs a workspace version.", "review_conflict")
        workspace = self._ensure_workspace(db, workspace_id)
        require(not check_version or expected_version == workspace["version"],
                "Review state changed; start a new exact review intent.", "review_conflict")
        request_id = operation.get("request_id")
        save_request_id = operation.get("save_request_id")
        submit_request_id = operation.get("submit_request_id")
        if kind == "save_draft":
            request_id = request_id or save_request_id
            require(isinstance(request_id, str), "A draft request identity is required.", "invalid_request")
            _identity(request_id, "request ID")
        else:
            submit_request_id = submit_request_id or request_id
            require(isinstance(submit_request_id, str), "A submit request identity is required.", "invalid_request")
            _identity(submit_request_id, "request ID")
            if save_request_id is not None:
                _identity(save_request_id, "request ID")
        canonical_operation = {
            "text": text,
            "anchor": anchor,
            "expected_version": expected_version,
            "request_id": request_id if kind == "save_draft" else None,
            "save_request_id": save_request_id,
            "submit_request_id": submit_request_id,
        }
        envelope["payload"] = canonical_operation
        return envelope

    def begin_intent(self, workspace_id: str, intent_id: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist one exact intent before its effect.

        Lifecycle: pending -> prepared (intermediate draft) -> completed_unacknowledged
        -> acknowledged. A known no-effect failure goes to rejected. Deletion or
        scope revocation goes to revoked and redacts the payload/result. Acknowledgment
        is scoped to the named workspace; it is never inferred from a later read.
        """
        self._require_write()
        self._assert_workspace_scope(workspace_id)
        _identity(intent_id, "intent ID")
        self._sync_read()
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._ensure_workspace(db, workspace_id)
            row = db.execute("SELECT * FROM review_intents WHERE intent_id=?", (intent_id,)).fetchone()
            if row is not None:
                require(row["workspace_id"] == workspace_id,
                        "Review intent is outside this workspace.", "scope_denied")
                if row["state"] == "revoked":
                    return self._intent_public(row)
                canonical = self._normalize_intent(db, workspace_id, kind, payload, check_version=False)
                require(row["fingerprint"] == _hash({"kind": kind, "payload": canonical}),
                        "This intent ID was already used for different inputs.", "request_conflict")
                self._intent_target(db, workspace_id, _json(row["payload_json"], {}))
                return self._intent_public(row)
            canonical = self._normalize_intent(db, workspace_id, kind, payload)
            fingerprint = _hash({"kind": kind, "payload": canonical})
            db.execute(
                """INSERT INTO review_intents(
                   intent_id,workspace_id,kind,fingerprint,payload_json,state,created,updated
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (intent_id, workspace_id, kind, fingerprint, _canonical(canonical), "pending", now, now),
            )
            return self._intent_public(
                db.execute("SELECT * FROM review_intents WHERE intent_id=?", (intent_id,)).fetchone()
            )

    def ack_intent(self, workspace_id: str, intent_id: str) -> dict[str, Any]:
        """Consume a completed receipt for this workspace without replaying its effect."""
        self._require_write()
        self._assert_workspace_scope(workspace_id)
        _identity(intent_id, "intent ID")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM review_intents WHERE intent_id=?", (intent_id,)).fetchone()
            require(row is not None and row["workspace_id"] == workspace_id,
                    "Review intent is not available in this workspace.", "scope_denied")
            if row["state"] == "revoked":
                return self._intent_public(row)
            self._intent_target(db, workspace_id, _json(row["payload_json"], {}))
            if row["state"] == "completed_unacknowledged":
                db.execute(
                    "UPDATE review_intents SET state='acknowledged',updated=? WHERE intent_id=?",
                    (time.time(), intent_id),
                )
            return self._intent_public(
                db.execute("SELECT * FROM review_intents WHERE intent_id=?", (intent_id,)).fetchone()
            )

    def reject_intent(self, workspace_id: str, intent_id: str, code: str, message: str,
                      remedy: str | None = None) -> dict[str, Any]:
        """Record a definitive no-effect rejection so recovery will not retry it."""
        self._require_write()
        self._assert_workspace_scope(workspace_id)
        _identity(intent_id, "intent ID")
        require(isinstance(code, str) and isinstance(message, str), "Intent rejection is invalid.", "invalid_request")
        result = {
            "status": "rejected",
            "intent_id": intent_id,
            "error": {"code": code, "message": message, "remedy": remedy or ""},
        }
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM review_intents WHERE intent_id=?", (intent_id,)).fetchone()
            require(row is not None and row["workspace_id"] == workspace_id,
                    "Review intent is not available in this workspace.", "scope_denied")
            if row["state"] != "revoked":
                self._intent_target(db, workspace_id, _json(row["payload_json"], {}))
            if row["state"] in {"completed_unacknowledged", "acknowledged", "revoked"}:
                return self._intent_public(row)
            db.execute(
                "UPDATE review_intents SET state='rejected',result_json=?,updated=? WHERE intent_id=?",
                (_canonical(result), time.time(), intent_id),
            )
            return self._intent_public(
                db.execute("SELECT * FROM review_intents WHERE intent_id=?", (intent_id,)).fetchone()
            )

    def _intent_row(self, db: sqlite3.Connection, intent_id: str | None,
                    workspace_id: str, kind: str | set[str]) -> sqlite3.Row | None:
        if intent_id is None:
            return None
        _identity(intent_id, "intent ID")
        row = db.execute("SELECT * FROM review_intents WHERE intent_id=?", (intent_id,)).fetchone()
        allowed_kinds = {kind} if isinstance(kind, str) else kind
        require(row is not None and row["workspace_id"] == workspace_id and row["kind"] in allowed_kinds,
                "The review intent is missing or outside this workspace.", "intent_missing")
        if row["state"] == "rejected":
            error = _json(row["result_json"], {}).get("error", {})
            raise ShowrunError(error.get("code", "intent_rejected"), error.get("message", "Review intent was rejected."),
                               error.get("remedy") or "Start a new explicit review action.")
        if row["state"] == "revoked":
            raise ShowrunError("intent_revoked", "This review intent was revoked with its deleted target.",
                               "Select a retained clip and start a new review action.")
        return row

    def _check_intent_operation(
        self,
        db: sqlite3.Connection,
        intent: sqlite3.Row | None,
        workspace_id: str,
        kind: str,
        request_id: str,
        text: str | None,
        anchor: dict[str, Any],
        expected_version: int,
        clip_id: str,
    ) -> tuple[dict[str, Any] | None, sqlite3.Row | None, sqlite3.Row | None]:
        if intent is None:
            return None, None, None
        envelope = _json(intent["payload_json"], {})
        _, _, clip = self._intent_target(db, workspace_id, envelope)
        require(clip["id"] == clip_id, "This intent targets a different retained clip.", "request_conflict")
        operation = envelope["payload"]
        expected_request = (
            operation.get("request_id") or operation.get("save_request_id")
            if kind == "save_draft"
            else operation.get("submit_request_id") or operation.get("request_id")
        )
        require(request_id == expected_request, "This intent was called with a different request identity.",
                "request_conflict")
        require(text is None or text == operation.get("text"),
                "This intent was called with different note text.", "request_conflict")
        require(anchor == operation.get("anchor", {}),
                "This intent was called with different note anchors.", "request_conflict")
        if kind == "save_draft":
            require(expected_version == operation.get("expected_version"),
                    "This intent was called with a different workspace version.", "request_conflict")
        return envelope, clip, None

    def _complete_intent(self, db: sqlite3.Connection, intent_id: str | None, state: str,
                         result: dict[str, Any]) -> None:
        if intent_id is None:
            return
        require(state in {"prepared", "completed_unacknowledged"}, "Invalid review intent state.", "invalid_request")
        db.execute(
            "UPDATE review_intents SET state=?,result_json=?,updated=? WHERE intent_id=? AND state IN ('pending','prepared')",
            (state, _canonical(result), time.time(), intent_id),
        )

    def _workspace_json(self, row: sqlite3.Row) -> dict[str, Any]:
        selection = _json(row["selection_json"])
        invalidated = _json(row["invalidated_json"])
        playback = _json(row["playback_json"], {})
        draft = _json(row["draft_json"])
        return {
            "workspace_id": row["id"],
            "version": row["version"],
            "appearance": row["appearance"],
            "selection": selection,
            "invalidated_selection": invalidated,
            "playback": playback,
            "draft": draft,
        }

    def workspace(self, workspace_id: str = "default") -> dict[str, Any]:
        self._sync_read()
        with self._connect() as db:
            row = self._ensure_workspace(db, workspace_id)
            state = self._workspace_json(row)
            demos = [
                self._demo_public(db, demo) for demo in db.execute(
                    "SELECT * FROM demos WHERE deleted=0 ORDER BY created,id LIMIT ?", (MAX_PAGE,)
                ) if self._demo_in_scope(workspace_id, demo["id"])
            ]
            selected_clip = None
            if state["selection"] and state["selection"].get("clip_id"):
                clip = db.execute("SELECT * FROM clips WHERE id=?", (state["selection"]["clip_id"],)).fetchone()
                if clip:
                    self._assert_demo_scope(workspace_id, clip["demo_id"])
                    take = db.execute("SELECT * FROM takes WHERE id=?", (clip["take_id"],)).fetchone()
                    selected_clip = self._clip_public(clip, take)
            scoped_takes = [
                item for item in db.execute(
                    "SELECT id,demo_id,discovered_at FROM takes WHERE deleted=0 AND discovered_at>?",
                    (row["updated"],),
                )
                if self._demo_in_scope(workspace_id, item["demo_id"])
            ]
            intents = [
                self._intent_public(item)
                for item in db.execute(
                    "SELECT * FROM review_intents WHERE workspace_id=? ORDER BY updated DESC LIMIT 100",
                    (workspace_id,),
                )
                if item["state"] == "revoked" or self._demo_in_scope(
                    workspace_id, _json(item["payload_json"], {}).get("target", {}).get("demo_id")
                )
            ]
            deletions = [
                {
                    "confirmation_token": item["token"],
                    "request_id": item["request_id"],
                    "commit_request_id": item["commit_request_id"],
                    "scope": item["scope"],
                    "target_id": item["target_id"],
                    "state": item["state"],
                    "snapshot": _json(item["snapshot_json"], {}),
                    "result": _json(item["result_json"]) if item["result_json"] else None,
                }
                for item in db.execute(
                    """SELECT * FROM delete_intents
                       WHERE (workspace_id=? OR workspace_id IS NULL)
                       ORDER BY created DESC LIMIT 100""",
                    (workspace_id,),
                )
                if item["workspace_id"] in {None, workspace_id}
                and self._demo_in_scope(workspace_id, self._demo_for_delete_snapshot(_json(item["snapshot_json"], {})))
            ]
            state.update({
                "schema_version": 1,
                "demos": demos,
                "selected_clip": selected_clip,
                "notes": [
                    {
                        "id": note["id"],
                        "clip_id": note["clip_id"],
                        "text": note["text"],
                        "anchor": _json(note["anchor_json"], {}),
                        "created": note["created"],
                        "submitted": True,
                    }
                    for note in db.execute(
                        """SELECT notes.*, clips.demo_id FROM notes
                           JOIN clips ON clips.id=notes.clip_id
                           WHERE notes.workspace_id=? AND clips.deleted=0
                           ORDER BY notes.created LIMIT 100""",
                        (workspace_id,),
                    )
                    if self._demo_in_scope(workspace_id, note["demo_id"])
                ],
                "new_take_count": len(scoped_takes),
                "review_intents": intents,
                "pending_deletions": [item for item in deletions if item["result"] is None],
                "deletion_results": [item for item in deletions if item["result"] is not None],
                "capabilities": {
                    "playback": True,
                    "selection": True,
                    "rename": True,
                    "delete": True,
                    "notes": True,
                    "downloads": True,
                    "model": False,
                },
            })
            return state

    def _validate_anchor(self, take: sqlite3.Row, clip: sqlite3.Row, anchor: dict[str, Any] | None) -> dict[str, Any]:
        anchor = {} if anchor is None else dict(anchor)
        require(set(anchor) <= {"step_id", "time_seconds", "range_start_seconds", "range_end_seconds"},
                "Note anchor contains unsupported fields.", "invalid_anchor")
        receipt = _json(take["receipt_json"], {})
        steps = {step.get("id"): step for step in receipt.get("steps", []) if isinstance(step, dict)}
        if "step_id" in anchor:
            _identity(anchor["step_id"], "step ID")
            require(anchor["step_id"] in steps, "The requested step is not in this retained receipt.", "invalid_anchor")
            interval = steps[anchor["step_id"]].get("interval")
            require(isinstance(interval, dict) or not any(key in anchor for key in
                    ("time_seconds", "range_start_seconds", "range_end_seconds")),
                    "An unrecorded step cannot have a time anchor.", "invalid_anchor")
        duration = (receipt.get("media") or {}).get("duration_seconds")
        duration = float(duration) if isinstance(duration, (int, float)) else None
        values = []
        for key in ("time_seconds", "range_start_seconds", "range_end_seconds"):
            if key in anchor:
                value = anchor[key]
                require(type(value) in {int, float} and 0 <= value, "Anchor time is invalid.", "invalid_anchor")
                if duration is not None:
                    require(value <= duration + 0.08, "Anchor time is outside the selected clip.", "invalid_anchor")
                values.append(value)
        if "range_start_seconds" in anchor or "range_end_seconds" in anchor:
            require("range_start_seconds" in anchor and "range_end_seconds" in anchor
                    and anchor["range_start_seconds"] <= anchor["range_end_seconds"],
                    "A note range needs ordered start and end times.", "invalid_anchor")
        return anchor

    def select_clip(self, workspace_id: str, demo_id: str, take_id: str, clip_id: str,
                    expected_version: int, request_id: str, *, step_id: str | None = None,
                    time_seconds: float | None = None) -> dict[str, Any]:
        self._require_write()
        payload = {"workspace_id": workspace_id, "demo_id": demo_id, "take_id": take_id, "clip_id": clip_id,
                   "expected_version": expected_version, "step_id": step_id, "time_seconds": time_seconds}
        whole_clip = step_id == ""
        if whole_clip:
            step_id = None
        self._sync_read()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._assert_workspace_scope(workspace_id)
            self._assert_demo_scope(workspace_id, demo_id)
            self._row_clip(db, clip_id)
            prior = self._operation(db, request_id, "select_clip", payload)
            if prior is not None:
                return prior
            workspace = self._ensure_workspace(db, workspace_id)
            require(type(expected_version) is int and expected_version == workspace["version"],
                    "Review state changed; refresh and select the exact clip again.", "review_conflict")
            clip = self._row_clip(db, clip_id)
            take = self._row_take(db, take_id)
            self._assert_demo_scope(workspace_id, demo_id)
            require(clip["take_id"] == take_id and clip["demo_id"] == demo_id and take["demo_id"] == demo_id,
                    "The selected identities do not describe one clip.", "selection_conflict")
            if step_id is not None:
                self._validate_anchor(take, clip, {"step_id": step_id})
            if time_seconds is not None:
                self._validate_anchor(take, clip, {"time_seconds": time_seconds})
            info = self._clip_public(clip, take)
            saved_draft = db.execute("SELECT * FROM drafts WHERE workspace_id=? AND clip_id=?",
                                     (workspace_id, clip_id)).fetchone()
            if step_id is not None or whole_clip:
                step_draft = db.execute(
                    "SELECT * FROM step_drafts WHERE workspace_id=? AND clip_id=? AND step_id=?",
                    (workspace_id, clip_id, step_id or "")).fetchone()
                # Preserve an older saved draft without rewriting its identity.
                saved_draft = step_draft or (saved_draft if saved_draft and
                    _json(saved_draft["anchor_json"], {}).get("step_id") == step_id else None)
            draft = ({"workspace_id": workspace_id, "clip_id": clip_id, "version": saved_draft["version"],
                      "text": saved_draft["text"], "anchor": _json(saved_draft["anchor_json"], {}),
                      "submitted": False} if saved_draft else None)
            saved_position = db.execute(
                "SELECT time_seconds FROM playback_positions WHERE workspace_id=? AND clip_id=?",
                (workspace_id, clip_id),
            ).fetchone()
            position = time_seconds if time_seconds is not None else saved_position[0] if saved_position else 0
            playback = {"clip_id": clip_id, "time_seconds": position}
            selection = {
                "demo_id": demo_id, "take_id": take_id, "clip_id": clip_id,
                "media_id": clip_id, "content_sha256": clip["media_sha256"],
                "step_id": step_id, "time_seconds": time_seconds,
                "selection_version": workspace["version"] + 1,
            }
            result = {
                "status": "selected", "workspace_id": workspace_id, "version": workspace["version"] + 1,
                "selection": selection, "clip": info, "draft": draft, "playback": playback,
            }
            db.execute(
                """UPDATE workspaces SET version=?,selection_json=?,invalidated_json=NULL,playback_json=?,
                   draft_json=?,updated=? WHERE id=?""",
                (result["version"], _canonical(selection), _canonical(playback),
                 _canonical(draft) if draft else None, time.time(), workspace_id),
            )
            self._save_operation(db, request_id, "select_clip", payload, result)
            return result

    select = select_clip

    def jump_to_step(self, workspace_id: str, clip_id: str, step_id: str, expected_version: int,
                     request_id: str) -> dict[str, Any]:
        clip = self.get_clip(clip_id)
        return self.select_clip(workspace_id, clip["demo_id"], clip["take_id"], clip_id, expected_version,
                                request_id, step_id=step_id)

    def set_playback(self, workspace_id: str, clip_id: str, position_seconds: float,
                     request_id: str | None = None) -> dict[str, Any]:
        self._require_write()
        _identity(workspace_id, "workspace ID")
        _identity(clip_id, "clip ID")
        require(type(position_seconds) in {int, float} and position_seconds >= 0,
                "Playback position is invalid.", "invalid_range")
        self._sync_read()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            workspace = self._ensure_workspace(db, workspace_id)
            selection = _json(workspace["selection_json"])
            require(selection and selection.get("clip_id") == clip_id,
                    "Playback must target the current exact clip.", "selection_conflict")
            clip = self._row_clip(db, clip_id)
            take = self._row_take(db, clip["take_id"], True)
            duration = (_json(take["receipt_json"], {}).get("media") or {}).get("duration_seconds")
            require(duration is None or position_seconds <= float(duration) + 0.08,
                    "Playback position is outside the selected media.", "invalid_range")
            self._assert_demo_scope(workspace_id, clip["demo_id"])
            playback = {"clip_id": clip_id, "time_seconds": float(position_seconds)}
            db.execute(
                "INSERT INTO playback_positions VALUES(?,?,?) ON CONFLICT(workspace_id,clip_id) "
                "DO UPDATE SET time_seconds=excluded.time_seconds",
                (workspace_id, clip_id, float(position_seconds)),
            )
            db.execute("UPDATE workspaces SET playback_json=?,updated=? WHERE id=?",
                       (_canonical(playback), time.time(), workspace_id))
            return {"status": "playback_saved", "workspace_id": workspace_id, "version": workspace["version"],
                    "playback": playback}

    def set_appearance(self, workspace_id: str, appearance: str, expected_version: int | None = None,
                       request_id: str | None = None) -> dict[str, Any]:
        self._require_write()
        require(appearance in THEMES, "Appearance must be light, dark or system.", "invalid_appearance")
        require(type(expected_version) is int, "Appearance updates require the current workspace version.",
                "review_conflict")
        request_id = request_id or f"appearance-{uuid.uuid4().hex}"
        payload = {"workspace_id": workspace_id, "appearance": appearance, "expected_version": expected_version}
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._assert_workspace_scope(workspace_id)
            prior = self._operation(db, request_id, "appearance", payload)
            if prior is not None:
                return prior
            row = self._ensure_workspace(db, workspace_id)
            if expected_version is not None:
                require(expected_version == row["version"], "Review state changed; refresh appearance settings.",
                        "review_conflict")
            version = row["version"] + 1
            result = {"status": "appearance_saved", "workspace_id": workspace_id, "appearance": appearance,
                      "version": version}
            db.execute("UPDATE workspaces SET appearance=?,version=?,updated=? WHERE id=?",
                       (appearance, version, time.time(), workspace_id))
            self._save_operation(db, request_id, "appearance", payload, result)
            return result

    def rename(self, item_type: str, item_id: str, name: str, expected_version: int, request_id: str,
               *, workspace_id: str | None = None) -> dict[str, Any]:
        self._require_write()
        if self._scope_enforced:
            workspace_id = self._assert_workspace_scope(workspace_id)
        require(item_type in {"demo", "take", "clip"}, "Unsupported review item.", "invalid_target")
        _identity(item_id, f"{item_type} ID")
        name = _display_name(name)
        payload = {"item_type": item_type, "item_id": item_id, "name": name, "expected_version": expected_version}
        self._sync_read()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            table = {"demo": "demos", "take": "takes", "clip": "clips"}[item_type]
            row = db.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
            require(row is not None and not row["deleted"], "The requested review item is not available.", "not_found")
            if workspace_id:
                demo_id = row["id"] if item_type == "demo" else row["demo_id"]
                self._assert_demo_scope(workspace_id, demo_id)
            prior = self._operation(db, request_id, "rename", payload)
            if prior is not None:
                return prior
            require(row["version"] == expected_version, "The item changed; refresh before renaming.", "review_conflict")
            version = row["version"] + 1
            db.execute(f"UPDATE {table} SET name=?,version=? WHERE id=?", (name, version, item_id))
            result = {"status": "renamed", "item_type": item_type, "item_id": item_id, "name": name,
                      "version": version}
            self._save_operation(db, request_id, "rename", payload, result)
            return result

    def rename_demo(self, demo_id: str, name: str, expected_version: int, request_id: str) -> dict[str, Any]:
        return self.rename("demo", demo_id, name, expected_version, request_id)

    def rename_take(self, take_id: str, name: str, expected_version: int, request_id: str) -> dict[str, Any]:
        return self.rename("take", take_id, name, expected_version, request_id)

    def rename_clip(self, clip_id: str, name: str, expected_version: int, request_id: str) -> dict[str, Any]:
        return self.rename("clip", clip_id, name, expected_version, request_id)

    def _revoke_review_records(
        self,
        db: sqlite3.Connection,
        clip_ids: set[str],
        take_ids: set[str] | None = None,
        demo_ids: set[str] | None = None,
    ) -> None:
        """Redact every review recovery surface for deleted retained targets."""
        take_ids = set(take_ids or ())
        demo_ids = set(demo_ids or ())
        if not clip_ids and not take_ids and not demo_ids:
            return
        if clip_ids:
            placeholders = ",".join("?" for _ in clip_ids)
            values = tuple(clip_ids)
            db.execute(f"DELETE FROM notes WHERE clip_id IN ({placeholders})", values)
            db.execute(f"DELETE FROM drafts WHERE clip_id IN ({placeholders})", values)
            db.execute(f"DELETE FROM step_drafts WHERE clip_id IN ({placeholders})", values)
            db.execute(f"DELETE FROM playback_positions WHERE clip_id IN ({placeholders})", values)
        for workspace in db.execute("SELECT * FROM workspaces").fetchall():
            draft = _json(workspace["draft_json"])
            selection = _json(workspace["selection_json"])
            draft_revoked = isinstance(draft, dict) and draft.get("clip_id") in clip_ids
            selection_revoked = isinstance(selection, dict) and selection.get("clip_id") in clip_ids
            if not draft_revoked and not selection_revoked:
                continue
            invalidated = ({**selection, "reason": "deleted"} if selection_revoked else
                           _json(workspace["invalidated_json"]))
            db.execute(
                """UPDATE workspaces
                   SET draft_json=?,selection_json=?,invalidated_json=?,version=version+1,updated=?
                   WHERE id=?""",
                (
                    None if draft_revoked else workspace["draft_json"],
                    None if selection_revoked else workspace["selection_json"],
                    _canonical(invalidated) if invalidated else None,
                    time.time(),
                    workspace["id"],
                ),
            )
        def revoked_result(intent_id: str) -> str:
            return _canonical({
                "status": "revoked",
                "intent_id": intent_id,
                "reason": "review_deleted",
            })
        for intent in db.execute("SELECT * FROM review_intents").fetchall():
            envelope = _json(intent["payload_json"], {})
            target = envelope.get("target") if isinstance(envelope, dict) else None
            if not isinstance(target, dict):
                target = envelope if isinstance(envelope, dict) else {}
            if not (
                target.get("clip_id") in clip_ids
                or target.get("take_id") in take_ids
                or target.get("demo_id") in demo_ids
            ):
                continue
            db.execute(
                """UPDATE review_intents
                   SET state='revoked',payload_json=?,result_json=?,updated=?
                   WHERE intent_id=?""",
                (
                    _canonical({"schema_version": INTENT_SCHEMA_VERSION, "revoked": True}),
                    revoked_result(intent["intent_id"]),
                    time.time(),
                    intent["intent_id"],
                ),
            )
        for operation in db.execute(
            "SELECT request_id,kind,result_json FROM operations WHERE kind IN ('save_draft','submit_note')"
        ).fetchall():
            result = _json(operation["result_json"], {})
            if not isinstance(result, dict) or result.get("clip_id") not in clip_ids:
                note = result.get("note") if isinstance(result, dict) else None
                draft = result.get("draft") if isinstance(result, dict) else None
                if not (
                    isinstance(note, dict) and note.get("clip_id") in clip_ids
                ) and not (
                    isinstance(draft, dict) and draft.get("clip_id") in clip_ids
                ):
                    continue
            db.execute(
                "UPDATE operations SET result_json=? WHERE request_id=?",
                (
                    _canonical({
                        "status": "revoked",
                        "reason": "review_deleted",
                        "request_id": operation["request_id"],
                        "kind": operation["kind"],
                    }),
                    operation["request_id"],
                ),
            )

    def _deletion_assets(self, db: sqlite3.Connection, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        assets = snapshot.get("assets")
        if isinstance(assets, list):
            return assets
        # Migration path for confirmations created before asset snapshots existed.
        result = []
        for clip_id in snapshot.get("clip_ids", []):
            clip = db.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
            if clip:
                result.append({
                    "clip_id": clip["id"],
                    "take_id": clip["take_id"],
                    "media_path": clip["media_path"],
                    "media_sha256": clip["media_sha256"],
                    "media_bytes": clip["media_bytes"],
                })
        return result

    def _perform_deletion_cleanup(
        self,
        snapshot: dict[str, Any],
        *,
        recovery: bool,
        heartbeat=None,
    ) -> tuple[list[str], list[str], list[str]]:
        """Unlink only admitted assets, returning removed, incomplete and uncertain IDs."""
        removed: list[str] = []
        incomplete: list[str] = []
        uncertain: list[str] = []
        with self._connect() as db:
            for asset in snapshot.get("assets", []):
                if heartbeat:
                    heartbeat()
                clip_id = asset["clip_id"]
                relative = asset.get("media_path")
                if not relative:
                    removed.append(clip_id)
                    continue
                sibling = db.execute(
                    """SELECT id FROM clips
                       WHERE take_id=? AND media_path=? AND id<>? AND deleted=0""",
                    (asset["take_id"], relative, clip_id),
                ).fetchone()
                if sibling:
                    incomplete.append(clip_id)
                    continue
                try:
                    path = self._safe_child(self._take_dir(asset["take_id"]), relative)
                    if path.is_symlink() or not path.is_file():
                        (uncertain if recovery else incomplete).append(clip_id)
                        continue
                    path.unlink()
                    removed.append(clip_id)
                except (OSError, ShowrunError):
                    (uncertain if recovery else incomplete).append(clip_id)
        return removed, incomplete, uncertain

    def prepare_delete(self, scope: str, target_id: str, expected_version: int, request_id: str | None = None,
                       *, workspace_id: str | None = None) -> dict[str, Any]:
        self._require_write()
        if self._scope_enforced:
            workspace_id = self._assert_workspace_scope(workspace_id)
        require(scope in {"clip", "take", "demo"}, "Deletion scope is invalid.", "invalid_target")
        _identity(target_id, f"{scope} ID")
        require(type(expected_version) is int, "Deletion confirmation needs the current target version.",
                "review_conflict")
        request_id = request_id or f"delete-prepare-{uuid.uuid4().hex}"
        payload = {
            "workspace_id": workspace_id,
            "scope": scope,
            "target_id": target_id,
            "expected_version": expected_version,
        }
        self._sync_read()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            table = {"demo": "demos", "take": "takes", "clip": "clips"}[scope]
            row = db.execute(f"SELECT * FROM {table} WHERE id=?", (target_id,)).fetchone()
            require(row is not None and not row["deleted"], "The deletion target is not available.", "not_found")
            demo_id = row["id"] if scope == "demo" else row["demo_id"]
            self._assert_demo_scope(workspace_id, demo_id)
            prior = self._operation(db, request_id, "delete_prepare", payload)
            if prior is not None:
                return prior
            require(row["version"] == expected_version, "The target changed; refresh before confirming deletion.",
                    "review_conflict")
            if scope == "clip":
                take_ids = [row["take_id"]]
                take = db.execute("SELECT * FROM takes WHERE id=?", (row["take_id"],)).fetchone()
                items = [self._clip_public(row, take)]
                clip_rows = [row]
            elif scope == "take":
                take_ids = [target_id]
                items = [self._take_public(row)]
                clip_rows = db.execute(
                    "SELECT * FROM clips WHERE take_id=? AND deleted=0", (target_id,)
                ).fetchall()
            else:
                take_ids = [item["id"] for item in db.execute(
                    "SELECT id FROM takes WHERE demo_id=? AND deleted=0", (target_id,)
                )]
                take_rows = db.execute("SELECT * FROM takes WHERE demo_id=? AND deleted=0", (target_id,)).fetchall()
                items = [self._take_public(item) for item in take_rows]
                clip_rows = db.execute(
                    "SELECT * FROM clips WHERE demo_id=? AND deleted=0", (target_id,)
                ).fetchall()
            for take_id in take_ids:
                take = db.execute("SELECT receipt_json FROM takes WHERE id=?", (take_id,)).fetchone()
                receipt = _json(take["receipt_json"], {}) if take else {}
                require(receipt.get("status") not in {"running", "uncertain"},
                        "Active or uncertain work cannot be deleted.", "active_work")
            assets = [
                {
                    "clip_id": clip["id"],
                    "take_id": clip["take_id"],
                    "media_path": clip["media_path"],
                    "media_sha256": clip["media_sha256"],
                    "media_bytes": clip["media_bytes"],
                }
                for clip in clip_rows
            ]
            snapshot = {
                "scope": scope,
                "target_id": target_id,
                "target_version": expected_version,
                "take_ids": take_ids,
                "clip_ids": [clip["id"] for clip in clip_rows],
                "assets": assets,
                "items": items,
            }
            token = secrets.token_urlsafe(32)
            commit_request_id = f"delete-commit-{uuid.uuid4().hex}"
            db.execute(
                """INSERT INTO delete_intents(
                   token,request_id,workspace_id,scope,target_id,expected_version,snapshot_json,
                   commit_request_id,state,result_json,effect_started,created
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    token,
                    request_id,
                    workspace_id,
                    scope,
                    target_id,
                    expected_version,
                    _canonical(snapshot),
                    commit_request_id,
                    "prepared",
                    None,
                    None,
                    time.time(),
                ),
            )
            result = {
                "status": "confirmation_required",
                "confirmation_token": token,
                "request_id": request_id,
                "commit_request_id": commit_request_id,
                "workspace_id": workspace_id,
                "scope": scope,
                "target_id": target_id,
                "expected_version": expected_version,
                "snapshot": snapshot,
            }
            self._save_operation(db, request_id, "delete_prepare", payload, result)
            return result

    @staticmethod
    def _delete_owner_active(intent: sqlite3.Row) -> bool:
        owner = intent["cleanup_owner"]
        with _ACTIVE_DELETION_LOCK:
            if owner and owner in _ACTIVE_DELETIONS:
                return True
        pid = intent["cleanup_pid"]
        heartbeat = intent["cleanup_heartbeat"] or intent["effect_started"]
        if not isinstance(pid, int) or not isinstance(heartbeat, (int, float)):
            return False
        if pid == os.getpid():
            owner_thread = intent["cleanup_thread"]
            return (
                isinstance(owner_thread, int)
                and owner_thread != threading.get_ident()
                and any(thread.ident == owner_thread and thread.is_alive()
                        for thread in threading.enumerate())
            )
        if time.time() - heartbeat > DELETE_LEASE_SECONDS:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _touch_delete_owner(self, token: str, owner: str) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE delete_intents SET cleanup_heartbeat=?
                   WHERE token=? AND cleanup_owner=? AND state='effect_started'""",
                (time.time(), token, owner),
            )

    def _admit_deletion(self, intent: sqlite3.Row, workspace_id: str | None) -> dict[str, Any]:
        snapshot = _json(intent["snapshot_json"], {})
        scope, target_id = snapshot["scope"], snapshot["target_id"]
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT * FROM delete_intents WHERE token=?", (intent["token"],)).fetchone()
            if current["result_json"]:
                return {"_admitted": False, "_result": _json(current["result_json"], {}), **snapshot}
            if current["state"] == "effect_started":
                return {
                    "_admitted": False,
                    "_owner_active": self._delete_owner_active(current),
                    "_result": None,
                    **snapshot,
                }
            self._assert_demo_scope(workspace_id, self._demo_for_delete_snapshot(snapshot))
            table = {"demo": "demos", "take": "takes", "clip": "clips"}[scope]
            target = db.execute(f"SELECT * FROM {table} WHERE id=?", (target_id,)).fetchone()
            require(target is not None and not target["deleted"],
                    "Deletion admission is no longer recoverable; confirm again.", "review_conflict")
            require(target["version"] == intent["expected_version"],
                    "The deletion target changed after confirmation; confirm again.", "review_conflict")
            for take_id in snapshot.get("take_ids", []):
                take = self._row_take(db, take_id)
                current_receipt, current_hash = self._read_receipt(take_id)
                require(current_hash == take["receipt_sha256"],
                        "The take changed after confirmation; inspect it before deletion.", "review_conflict")
                require(current_receipt.get("status") not in {"running", "uncertain"},
                        "Active or uncertain work cannot be deleted.", "active_work")
            if not isinstance(snapshot.get("assets"), list):
                snapshot["assets"] = self._deletion_assets(db, snapshot)
                db.execute(
                    "UPDATE delete_intents SET snapshot_json=? WHERE token=?",
                    (_canonical(snapshot), intent["token"]),
                )
            owner = f"{os.getpid()}:{threading.get_ident()}:{secrets.token_urlsafe(12)}"
            now = time.time()
            claimed = db.execute(
                """UPDATE delete_intents
                   SET state='effect_started',effect_started=?,cleanup_owner=?,
                       cleanup_pid=?,cleanup_thread=?,cleanup_heartbeat=?
                   WHERE token=? AND state='prepared'""",
                (now, owner, os.getpid(), threading.get_ident(), now, intent["token"]),
            ).rowcount
            require(claimed == 1, "Deletion admission changed; inspect the retained deletion outcome.",
                    "review_conflict")
            clip_ids = set(snapshot.get("clip_ids", []))
            for clip_id in clip_ids:
                db.execute("UPDATE clips SET deleted=1,version=version+1 WHERE id=? AND deleted=0", (clip_id,))
            if scope in {"take", "demo"}:
                for take_id in snapshot.get("take_ids", []):
                    db.execute("UPDATE takes SET deleted=1,version=version+1 WHERE id=? AND deleted=0", (take_id,))
                    db.execute(
                        "INSERT OR REPLACE INTO request_tombstones(request_id,take_id,deleted_at,result_json)"
                        " VALUES(?,?,?,?)",
                        (intent["commit_request_id"], take_id, time.time(), "{}"),
                    )
            elif snapshot.get("take_ids"):
                db.execute(
                    "INSERT OR REPLACE INTO request_tombstones(request_id,take_id,deleted_at,result_json)"
                    " VALUES(?,?,?,?)",
                    (intent["commit_request_id"], snapshot["take_ids"][0], time.time(), "{}"),
                )
            if scope == "demo":
                db.execute(
                    "UPDATE demos SET deleted=1,version=version+1,updated=? WHERE id=? AND deleted=0",
                    (time.time(), target_id),
                )
            demo_id = self._demo_for_delete_snapshot(snapshot)
            self._revoke_review_records(
                db,
                clip_ids,
                set(snapshot.get("take_ids", [])) if scope in {"take", "demo"} else set(),
                {demo_id} if scope == "demo" else set(),
            )
            return {"_admitted": True, "_owner": owner, "_result": None, **snapshot}

    @staticmethod
    def _demo_for_delete_snapshot(snapshot: dict[str, Any]) -> str:
        items = snapshot.get("items") or []
        if snapshot.get("scope") == "demo":
            return snapshot["target_id"]
        if items:
            return items[0].get("demo_id") or snapshot.get("target_id")
        return snapshot.get("target_id")

    def _finalize_deletion(
        self,
        confirmation_token: str,
        snapshot: dict[str, Any],
        result: dict[str, Any],
        request_id: str | None,
        intent_workspace: str | None,
    ) -> dict[str, Any]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT * FROM delete_intents WHERE token=?", (confirmation_token,)
            ).fetchone()
            if current["result_json"]:
                return _json(current["result_json"], {})
            db.execute(
                "UPDATE delete_intents SET state=?,result_json=? WHERE token=?",
                (result["status"], _canonical(result), confirmation_token),
            )
            tombstone = _canonical({
                "status": result["status"],
                "media_removed": result["media_removed"],
            })
            for take_id in snapshot.get("take_ids", []):
                db.execute(
                    "UPDATE request_tombstones SET result_json=? WHERE request_id=? AND take_id=?",
                    (tombstone, current["commit_request_id"] or current["request_id"], take_id),
                )
                if request_id and request_id != (current["commit_request_id"] or current["request_id"]):
                    db.execute(
                        """INSERT OR REPLACE INTO request_tombstones(
                           request_id,take_id,deleted_at,result_json
                        ) VALUES(?,?,?,?)""",
                        (request_id, take_id, time.time(), tombstone),
                    )
            self._save_operation(
                db,
                current["commit_request_id"] or current["request_id"],
                "delete",
                {
                    "token": confirmation_token,
                    "workspace_id": intent_workspace,
                    "scope": snapshot["scope"],
                    "target_id": snapshot["target_id"],
                },
                result,
            )
            return result

    def delete(self, confirmation_token: str | dict[str, Any], request_id: str | None = None,
               *, workspace_id: str | None = None) -> dict[str, Any]:
        self._require_write()
        if isinstance(confirmation_token, dict):
            request_id = request_id or confirmation_token.get("request_id")
            confirmation_token = confirmation_token.get("confirmation_token")
        require(isinstance(confirmation_token, str) and 20 <= len(confirmation_token) <= 100,
                "A deletion confirmation token is required.", "confirmation_required")
        if self._scope_enforced:
            workspace_id = self._assert_workspace_scope(workspace_id)
        with self._connect() as db:
            intent = db.execute("SELECT * FROM delete_intents WHERE token=?", (confirmation_token,)).fetchone()
        require(intent is not None, "Deletion confirmation is missing or expired.", "confirmation_required")
        intent_workspace = intent["workspace_id"]
        if intent_workspace is not None:
            require(workspace_id == intent_workspace, "Deletion confirmation is outside this workspace scope.",
                    "scope_denied")
        elif self._scope_enforced:
            raise ShowrunError("scope_denied", "This legacy confirmation has no authorized workspace binding.")
        if workspace_id is not None:
            self._assert_demo_scope(workspace_id, self._demo_for_delete_snapshot(_json(intent["snapshot_json"], {})))
        with self._connect() as db:
            current = db.execute("SELECT * FROM delete_intents WHERE token=?", (confirmation_token,)).fetchone()
        if current["result_json"]:
            return _json(current["result_json"], {})
        # Publish in-process ownership before another caller can inspect admission.
        with _ACTIVE_DELETION_LOCK:
            admission = self._admit_deletion(current, workspace_id)
            if admission.get("_admitted"):
                _ACTIVE_DELETIONS.add(admission["_owner"])
        if admission.get("_result") is not None:
            return admission["_result"]
        snapshot = {key: value for key, value in admission.items() if not key.startswith("_")}
        if not admission.get("_admitted"):
            if admission.get("_owner_active"):
                return {
                    "status": "pending",
                    "scope": snapshot["scope"],
                    "target_id": snapshot["target_id"],
                    "request_id": current["commit_request_id"] or current["request_id"],
                    "pending": True,
                    "receipt_retained": True,
                }
            uncertain_result = {
                "status": "uncertain",
                "scope": snapshot["scope"],
                "target_id": snapshot["target_id"],
                "request_id": current["commit_request_id"] or current["request_id"],
                "requested_request_id": request_id,
                "removed_clip_ids": [],
                "incomplete_clip_ids": [],
                "uncertain_clip_ids": snapshot.get("clip_ids", []),
                "media_removed": False,
                "receipt_retained": True,
                "request_tombstone_retained": True,
                "recovery_uncertain": True,
            }
            return self._finalize_deletion(
                confirmation_token, snapshot, uncertain_result, request_id, intent_workspace
            )
        owner = admission["_owner"]
        try:
            removed, incomplete, uncertain = self._perform_deletion_cleanup(
                snapshot, recovery=False,
                heartbeat=lambda: self._touch_delete_owner(confirmation_token, owner),
            )
            status = "uncertain" if uncertain else "cleanup_incomplete" if incomplete else "deleted"
            result = {
                "status": status,
                "scope": snapshot["scope"],
                "target_id": snapshot["target_id"],
                "request_id": current["commit_request_id"] or current["request_id"],
                "requested_request_id": request_id,
                "removed_clip_ids": removed,
                "incomplete_clip_ids": incomplete,
                "uncertain_clip_ids": uncertain,
                "media_removed": status == "deleted",
                "receipt_retained": True,
                "request_tombstone_retained": True,
                "recovery_uncertain": status == "uncertain",
            }
            return self._finalize_deletion(
                confirmation_token, snapshot, result, request_id, intent_workspace
            )
        finally:
            with _ACTIVE_DELETION_LOCK:
                _ACTIVE_DELETIONS.discard(owner)

    confirm_delete = delete

    def _selected(self, db: sqlite3.Connection, workspace_id: str, clip_id: str) -> tuple[sqlite3.Row, sqlite3.Row, sqlite3.Row]:
        workspace = self._ensure_workspace(db, workspace_id)
        selection = _json(workspace["selection_json"])
        require(selection and selection.get("clip_id") == clip_id,
                "The requested clip is not the current exact workspace selection.", "selection_conflict")
        clip = self._row_clip(db, clip_id)
        take = self._row_take(db, clip["take_id"], True)
        self._assert_demo_scope(workspace_id, clip["demo_id"])
        return workspace, clip, take

    def save_draft(self, workspace_id: str, clip_id: str, text: str, *, step_id: str | None = None,
                   time_seconds: float | None = None, range_start_seconds: float | None = None,
                   range_end_seconds: float | None = None, expected_version: int | None = None,
                   request_id: str | None = None, intent_id: str | None = None) -> dict[str, Any]:
        self._require_write()
        require(isinstance(text, str) and len(text) <= MAX_NOTE, "Note text is too long.", "invalid_note")
        require(type(expected_version) is int, "Draft saves require the current workspace version.",
                "review_conflict")
        request_id = request_id or f"draft-{uuid.uuid4().hex}"
        anchor = {key: value for key, value in {
            "step_id": step_id, "time_seconds": time_seconds, "range_start_seconds": range_start_seconds,
            "range_end_seconds": range_end_seconds,
        }.items() if value is not None}
        payload = {"workspace_id": workspace_id, "clip_id": clip_id, "text": text, "anchor": anchor,
                   "expected_version": expected_version, "intent_id": intent_id}
        self._sync_read()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            intent = self._intent_row(db, intent_id, workspace_id, {"save_draft", "submit_note"})
            if intent is not None:
                self._check_intent_operation(
                    db, intent, workspace_id, "save_draft", request_id, text, anchor, expected_version, clip_id
                )
                if intent["state"] in {"prepared", "completed_unacknowledged", "acknowledged"}:
                    return _json(intent["result_json"], {})
            workspace, clip, take = self._selected(db, workspace_id, clip_id)
            prior = self._operation(db, request_id, "save_draft", payload)
            if prior is not None:
                return prior
            require(expected_version == workspace["version"], "Review state changed; keep this draft and refresh.",
                    "review_conflict")
            self._validate_anchor(take, clip, anchor)
            old = db.execute("SELECT version FROM drafts WHERE workspace_id=? AND clip_id=?",
                             (workspace_id, clip_id)).fetchone()
            version = (old["version"] + 1) if old else 1
            draft = {"workspace_id": workspace_id, "clip_id": clip_id, "version": version,
                     "text": text, "anchor": anchor, "submitted": False}
            db.execute(
                """INSERT INTO drafts(workspace_id,clip_id,version,text,anchor_json,updated) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(workspace_id,clip_id) DO UPDATE SET version=excluded.version,text=excluded.text,
                   anchor_json=excluded.anchor_json,updated=excluded.updated""",
                (workspace_id, clip_id, version, text, _canonical(anchor), time.time()),
            )
            db.execute(
                """INSERT INTO step_drafts VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(workspace_id,clip_id,step_id) DO UPDATE SET
                   version=excluded.version,text=excluded.text,anchor_json=excluded.anchor_json,updated=excluded.updated""",
                (workspace_id, clip_id, anchor.get("step_id", ""), version, text, _canonical(anchor), time.time()),
            )
            workspace_version = workspace["version"] + 1
            db.execute("UPDATE workspaces SET draft_json=?,version=?,updated=? WHERE id=?",
                       (_canonical(draft), workspace_version, time.time(), workspace_id))
            result = {"status": "draft_saved", "submitted": False, "draft": draft,
                      "workspace_id": workspace_id, "version": workspace_version}
            self._save_operation(db, request_id, "save_draft", payload, result)
            self._complete_intent(
                db,
                intent_id,
                "prepared" if intent is not None and intent["kind"] == "submit_note"
                else "completed_unacknowledged",
                result,
            )
            return result

    def submit_note(self, workspace_id: str, clip_id: str, *, text: str | None = None,
                    step_id: str | None = None, time_seconds: float | None = None,
                    range_start_seconds: float | None = None, range_end_seconds: float | None = None,
                    expected_version: int | None = None, request_id: str | None = None,
                    intent_id: str | None = None) -> dict[str, Any]:
        self._require_write()
        require(type(expected_version) is int, "Note submissions require the current workspace version.",
                "review_conflict")
        request_id = request_id or f"note-{uuid.uuid4().hex}"
        anchor = {key: value for key, value in {
            "step_id": step_id, "time_seconds": time_seconds, "range_start_seconds": range_start_seconds,
            "range_end_seconds": range_end_seconds,
        }.items() if value is not None}
        payload = {"workspace_id": workspace_id, "clip_id": clip_id, "text": text, "anchor": anchor,
                   "expected_version": expected_version, "intent_id": intent_id}
        self._sync_read()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            intent = self._intent_row(db, intent_id, workspace_id, "submit_note")
            if intent is not None:
                envelope, _, _ = self._check_intent_operation(
                    db, intent, workspace_id, "submit_note", request_id, text, anchor, expected_version, clip_id
                )
                if intent["state"] in {"completed_unacknowledged", "acknowledged"}:
                    return _json(intent["result_json"], {})
            workspace, clip, take = self._selected(db, workspace_id, clip_id)
            prior = self._operation(db, request_id, "submit_note", payload)
            if prior is not None:
                return prior
            require(expected_version == workspace["version"], "Review state changed; refresh the exact clip.",
                    "review_conflict")
            draft = db.execute("SELECT * FROM drafts WHERE workspace_id=? AND clip_id=?",
                               (workspace_id, clip_id)).fetchone()
            if step_id is not None:
                draft = db.execute(
                    "SELECT * FROM step_drafts WHERE workspace_id=? AND clip_id=? AND step_id=?",
                    (workspace_id, clip_id, step_id)).fetchone() or (
                        draft if draft and _json(draft["anchor_json"], {}).get("step_id") == step_id else None)
            elif text is not None and not anchor.get("step_id"):
                whole_draft = db.execute(
                    "SELECT * FROM step_drafts WHERE workspace_id=? AND clip_id=? AND step_id=''",
                    (workspace_id, clip_id)).fetchone()
                if whole_draft is not None:
                    draft = whole_draft
            if draft is None:
                require(text is not None, "Save a targeted draft or supply exact note text before submitting.",
                        "draft_missing")
                body = text
                submitted_anchor = anchor
            else:
                if text is not None:
                    require(text == draft["text"], "The submitted text differs from the saved exact draft.",
                            "draft_conflict")
                body = draft["text"]
                submitted_anchor = _json(draft["anchor_json"], {}) if not anchor else anchor
            require(isinstance(body, str) and body.strip() and len(body) <= MAX_NOTE,
                    "A submitted note must contain bounded text.", "invalid_note")
            self._validate_anchor(take, clip, submitted_anchor)
            if intent is not None:
                expected_body = envelope["payload"]["text"]
                expected_anchor = envelope["payload"]["anchor"]
                require(body == expected_body and submitted_anchor == expected_anchor,
                        "This intent was called with a different note payload.", "request_conflict")
            note_id = f"note-{uuid.uuid4().hex}"
            note = {"id": note_id, "workspace_id": workspace_id, "clip_id": clip_id, "text": body,
                    "anchor": submitted_anchor, "submitted": True, "created": time.time()}
            db.execute("INSERT INTO notes(id,workspace_id,clip_id,text,anchor_json,created,request_id) VALUES(?,?,?,?,?,?,?)",
                       (note_id, workspace_id, clip_id, body, _canonical(submitted_anchor), note["created"], request_id))
            workspace_version = workspace["version"] + 1
            db.execute("UPDATE workspaces SET version=?,updated=? WHERE id=?",
                       (workspace_version, time.time(), workspace_id))
            result = {"status": "note_submitted", "submitted": True, "note": note,
                      "workspace_id": workspace_id, "version": workspace_version,
                      "request_id": request_id}
            self._save_operation(db, request_id, "submit_note", payload, result)
            self._complete_intent(db, intent_id, "completed_unacknowledged", result)
            return result

    def notes(self, workspace_id: str = "default", clip_id: str | None = None) -> list[dict[str, Any]]:
        if self._scope_enforced:
            self._assert_workspace_scope(workspace_id)
        if clip_id is not None:
            _identity(clip_id, "clip ID")
        self._sync_read()
        with self._connect() as db:
            query = (
                "SELECT notes.*,clips.demo_id FROM notes "
                "JOIN clips ON clips.id=notes.clip_id "
                "WHERE notes.workspace_id=? AND clips.deleted=0"
                + (" AND notes.clip_id=?" if clip_id else "")
                + " ORDER BY notes.created"
            )
            args = (workspace_id, clip_id) if clip_id else (workspace_id,)
            return [
                {"id": row["id"], "workspace_id": row["workspace_id"], "clip_id": row["clip_id"],
                 "text": row["text"], "anchor": _json(row["anchor_json"], {}), "created": row["created"],
                 "submitted": True}
                for row in db.execute(query, args)
                if self._demo_in_scope(workspace_id, row["demo_id"])
            ]

    def describe_media(self, workspace_id: str, clip_id: str) -> dict[str, Any]:
        self._sync_read()
        with self._connect() as db:
            _, clip, take = self._selected(db, workspace_id, clip_id)
            info = self._clip_public(clip, take)
            require(info["status"] == "available", info["media"].get("reason", "Media is not available."),
                    "media_unavailable")
            return {
                **info,
                "bytes": info["media"]["bytes"],
                "mime_type": info["media"]["mime_type"],
                "duration_seconds": info["media"].get("duration_seconds"),
                "resource_uri": f"showrun://workspace/{workspace_id}/media/{clip_id}/0",
                "chunk_bytes": MAX_MEDIA_READ,
            }

    def read_media(self, workspace_id: str, clip_id: str, offset: int, limit: int = MAX_MEDIA_READ) -> bytes:
        require(type(offset) is int and offset >= 0 and type(limit) is int and 0 < limit <= MAX_MEDIA_READ,
                "Media range is invalid.", "invalid_range")
        require(offset % MAX_MEDIA_READ == 0, "Media offset must align to the bounded chunk size.", "invalid_range")
        data, _ = self.download_mp4(workspace_id, clip_id)
        require(offset < len(data) or offset == len(data) == 0,
                "Media offset is outside retained content.", "invalid_range")
        return data[offset:offset + limit]

    def _verified_media_bytes(
        self,
        take_id: str,
        receipt: dict[str, Any],
        clip: sqlite3.Row,
    ) -> tuple[bytes | None, dict[str, Any]]:
        info = self._media_info(take_id, receipt, clip["media_sha256"])
        if info.get("status") != "available":
            return None, info
        try:
            path = self._safe_child(self._take_dir(take_id), info["path"], must_exist=True)
            with path.open("rb") as stream:
                data = stream.read(MAX_TRANSFER_BYTES + 1)
            require(len(data) <= MAX_TRANSFER_BYTES, "The retained media exceeds the review transfer limit.",
                    "transfer_limit")
        except (OSError, ShowrunError):
            return None, {"status": "missing", "reason": "The retained MP4 changed during ZIP preparation."}
        expected_hash = clip["media_sha256"] or (receipt.get("media") or {}).get("sha256")
        expected_bytes = clip["media_bytes"] or (receipt.get("media") or {}).get("bytes")
        if (
            not isinstance(expected_hash, str)
            or hashlib.sha256(data).hexdigest() != expected_hash
            or (isinstance(expected_bytes, int) and len(data) != expected_bytes)
        ):
            return None, {"status": "changed", "reason": "The retained MP4 no longer matches its receipt."}
        return data, {**info, "status": "available", "sha256": expected_hash, "bytes": len(data)}

    def _zip_member_records(self, db: sqlite3.Connection, demo_id: str) -> list[dict[str, Any]]:
        """Capture only the identities in one ZIP snapshot.

        New takes are intentionally absent. Rename/version changes are not part of
        authorization for an already-created snapshot; deletion or media identity
        changes of an included member are.
        """
        records: list[dict[str, Any]] = []
        takes = db.execute(
            "SELECT * FROM takes WHERE demo_id=? ORDER BY discovered_at,id", (demo_id,)
        ).fetchall()
        for take in takes:
            records.append({
                "kind": "take",
                "id": take["id"],
                "demo_id": demo_id,
                "deleted": bool(take["deleted"]),
                "receipt_sha256": take["receipt_sha256"],
            })
            for clip in db.execute(
                "SELECT * FROM clips WHERE take_id=? ORDER BY id", (take["id"],)
            ):
                records.append({
                    "kind": "clip",
                    "id": clip["id"],
                    "demo_id": demo_id,
                    "take_id": take["id"],
                    "deleted": bool(clip["deleted"]),
                    "media_sha256": clip["media_sha256"],
                    "media_bytes": clip["media_bytes"],
                    "available": not take["deleted"] and not clip["deleted"] and self._media_info(
                        take["id"], _json(take["receipt_json"], {}), clip["media_sha256"]
                    )["status"] == "available",
                })
        return records

    @staticmethod
    def _zip_token(workspace_id: str, demo_id: str, members: list[dict[str, Any]]) -> str:
        return _hash({
            "workspace_id": workspace_id,
            "demo_id": demo_id,
            "members": members,
        })

    def zip_scope_token(self, workspace_id: str, demo_id: str) -> str:
        """Return a token for the members present at snapshot admission."""
        self.open_workspace(workspace_id)
        self._assert_demo_scope(workspace_id, demo_id)
        with self._connect() as db:
            self._row_demo(db, demo_id)
            return self._zip_token(workspace_id, demo_id, self._zip_member_records(db, demo_id))

    def assert_zip_scope(
        self,
        workspace_id: str,
        demo_id: str,
        scope_token: str,
        members: list[dict[str, Any]] | None = None,
    ) -> None:
        self._assert_demo_scope(workspace_id, demo_id)
        if members is None:
            # Compatibility for callers that only have the old public token:
            # current membership is the only safe interpretation.
            require(
                secrets.compare_digest(self.zip_scope_token(workspace_id, demo_id), scope_token),
                "The retained ZIP scope was deleted or changed; restart the download.",
                "transfer_expired",
            )
            return
        require(
            secrets.compare_digest(self._zip_token(workspace_id, demo_id, members), scope_token),
            "The retained ZIP snapshot identity is invalid.",
            "transfer_expired",
        )
        with self._connect() as db:
            try:
                self._row_demo(db, demo_id)
            except ShowrunError:
                raise ShowrunError(
                    "transfer_expired",
                    "The retained ZIP demo was deleted or is no longer authorized.",
                    "Restart the download from an available demo.",
                ) from None
            for member in members:
                kind = member.get("kind")
                member_id = member.get("id")
                row = db.execute(
                    "SELECT * FROM takes WHERE id=?" if kind == "take"
                    else "SELECT * FROM clips WHERE id=?",
                    (member_id,),
                ).fetchone()
                require(row is not None, "An included ZIP member was deleted.", "transfer_expired")
                if kind == "take":
                    try:
                        _, current_hash = self._read_receipt(member_id)
                    except ShowrunError:
                        current_hash = None
                    require(current_hash == member.get("receipt_sha256"),
                            "An included ZIP receipt is no longer available unchanged.", "transfer_expired")
                    require(row["demo_id"] == demo_id, "An included ZIP take changed scope.", "transfer_expired")
                    require(
                        bool(row["deleted"]) == bool(member.get("deleted")),
                        "An included ZIP take was deleted or restored.",
                        "transfer_expired",
                    )
                    require(
                        row["receipt_sha256"] == member.get("receipt_sha256"),
                        "An included ZIP receipt changed.",
                        "transfer_expired",
                    )
                else:
                    if member.get("available"):
                        take = self._row_take(db, row["take_id"], True)
                        info = self._media_info(take["id"], _json(take["receipt_json"], {}), row["media_sha256"])
                        require(info["status"] == "available", "An included ZIP clip is no longer available; restart the download.",
                                "transfer_expired")
                    require(
                        row["demo_id"] == demo_id and row["take_id"] == member.get("take_id"),
                        "An included ZIP clip changed scope.",
                        "transfer_expired",
                    )
                    require(
                        bool(row["deleted"]) == bool(member.get("deleted")),
                        "An included ZIP clip was deleted or restored.",
                        "transfer_expired",
                    )
                    require(
                        row["media_sha256"] == member.get("media_sha256")
                        and row["media_bytes"] == member.get("media_bytes"),
                        "An included ZIP media identity changed.",
                        "transfer_expired",
                    )

    def _zip_inventory(self, demo_id: str, workspace_id: str = "") -> tuple[bytes, dict[str, Any]]:
        _identity(demo_id, "demo ID")
        self._sync_read()
        with self._connect() as db:
            # Hold the metadata admission lock while taking the byte snapshot.
            # A deletion cannot revoke this scope halfway through preparation.
            db.execute("BEGIN IMMEDIATE")
            demo = self._row_demo(db, demo_id)
            takes = db.execute("SELECT * FROM takes WHERE demo_id=? ORDER BY discovered_at,id", (demo_id,)).fetchall()
            members = self._zip_member_records(db, demo_id)
            scope_token = self._zip_token(workspace_id, demo_id, members)
            entries: list[dict[str, Any]] = []
            contents: list[tuple[str, bytes]] = []
            complete = True
            for take in takes:
                receipt = _json(take["receipt_json"], {})
                receipt_name = f"receipts/{take['id']}.json"
                receipt_bytes = (_canonical(receipt) + "\n").encode()
                contents.append((receipt_name, receipt_bytes))
                entries.append({"kind": "receipt", "take_id": take["id"], "path": receipt_name,
                                "sha256": hashlib.sha256(receipt_bytes).hexdigest(), "bytes": len(receipt_bytes),
                                "source_sha256": take["receipt_sha256"],
                                "status": "included"})
                for clip in db.execute("SELECT * FROM clips WHERE take_id=? ORDER BY id", (take["id"],)):
                    data, info = self._verified_media_bytes(take["id"], receipt, clip)
                    if clip["deleted"] or take["deleted"] or data is None:
                        complete = False
                        entries.append({"kind": "clip", "clip_id": clip["id"], "take_id": take["id"],
                                        "path": f"media/{clip['id']}.mp4",
                                        "status": "deleted" if clip["deleted"] or take["deleted"]
                                        else info.get("status", "unavailable"),
                                        "sha256": clip["media_sha256"], "bytes": clip["media_bytes"]})
                        continue
                    require(sum(len(value) for _, value in contents) + len(data) <= MAX_TRANSFER_BYTES,
                            "The ZIP exceeds the 256 MiB review transfer limit.", "transfer_limit")
                    media_name = f"media/{clip['id']}.mp4"
                    contents.append((media_name, data))
                    entries.append({"kind": "clip", "clip_id": clip["id"], "take_id": take["id"],
                                    "path": media_name, "status": "included",
                                    "sha256": info["sha256"], "bytes": len(data)})
            public_meta = {
                "schema_version": 1, "demo": self._demo_public(db, demo),
                "notes": self.notes_for_demo(db, demo_id), "complete": complete,
                "limitations": [] if complete else ["One or more retained clips are missing, restricted, changed or deleted."],
            }
            meta_bytes = (_canonical(public_meta) + "\n").encode()
            contents.append(("review/metadata.json", meta_bytes))
            entries.append({"kind": "review_metadata", "path": "review/metadata.json",
                            "sha256": hashlib.sha256(meta_bytes).hexdigest(), "bytes": len(meta_bytes),
                            "status": "included"})
            inventory = {"schema_version": 1, "demo_id": demo_id, "complete": complete,
                         "scope_token": scope_token, "scope_members": members, "entries": entries,
                         "limitations": public_meta["limitations"]}
            inventory_bytes = (_canonical(inventory) + "\n").encode()
            contents.append(("manifest.json", inventory_bytes))
            entries.append({"kind": "manifest", "path": "manifest.json",
                            "sha256": hashlib.sha256(inventory_bytes).hexdigest(), "bytes": len(inventory_bytes),
                            "status": "included"})
            require(sum(len(data) for _, data in contents) <= MAX_TRANSFER_BYTES,
                    "The ZIP exceeds the 256 MiB review transfer limit.", "transfer_limit")
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in contents:
                    info = zipfile.ZipInfo(name)
                    info.date_time = (1980, 1, 1, 0, 0, 0)
                    info.external_attr = 0o600 << 16
                    archive.writestr(info, data)
            return output.getvalue(), inventory

    def notes_for_demo(self, db: sqlite3.Connection, demo_id: str) -> list[dict[str, Any]]:
        clip_ids = [row["id"] for row in db.execute(
            "SELECT id FROM clips WHERE demo_id=? AND deleted=0", (demo_id,)
        )]
        if not clip_ids:
            return []
        placeholders = ",".join("?" for _ in clip_ids)
        return [
            {"id": row["id"], "clip_id": row["clip_id"], "text": row["text"],
             "anchor": _json(row["anchor_json"], {}), "created": row["created"], "submitted": True}
            for row in db.execute(
                f"""SELECT notes.* FROM notes
                    JOIN clips ON clips.id=notes.clip_id
                    WHERE notes.clip_id IN ({placeholders}) AND clips.deleted=0
                    ORDER BY notes.created""",
                clip_ids,
            )
        ]

    def download_mp4(self, workspace_id: str, clip_id: str) -> tuple[bytes, dict[str, Any]]:
        info = self.describe_media(workspace_id, clip_id)
        path = self._safe_child(self._take_dir(info["take_id"]), "capture.mp4", must_exist=True)
        with path.open("rb") as stream:
            data = stream.read(MAX_TRANSFER_BYTES + 1)
        require(len(data) <= MAX_TRANSFER_BYTES, "The MP4 exceeds the 256 MiB review transfer limit.", "transfer_limit")
        require(hashlib.sha256(data).hexdigest() == info["content_sha256"], "Retained media changed during download.",
                "media_changed")
        return data, {"filename": f"{clip_id}.mp4", "mime_type": "video/mp4", "sha256": info["content_sha256"]}

    def download_zip(self, workspace_id: str, demo_id: str) -> tuple[bytes, dict[str, Any]]:
        # Workspace is an authorization boundary even for demo-wide snapshots.
        self.open_workspace(workspace_id)
        self._assert_demo_scope(workspace_id, demo_id)
        return self._zip_inventory(demo_id, workspace_id)

    def prepare_zip(self, workspace_id: str, demo_id: str) -> tuple[bytes, dict[str, Any], str]:
        self.open_workspace(workspace_id)
        self._assert_demo_scope(workspace_id, demo_id)
        data, inventory = self._zip_inventory(demo_id, workspace_id)
        return data, inventory, inventory["scope_token"]

    def describe_zip(self, workspace_id: str, demo_id: str) -> dict[str, Any]:
        _, inventory = self.download_zip(workspace_id, demo_id)
        return {"workspace_id": workspace_id, **inventory}
