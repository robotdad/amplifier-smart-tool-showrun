"""Authenticated local dashboard for retained capture review.

This is intentionally a small local service rather than a capture job service.
It owns only the review store and serves bounded IDs/media from that store.
"""

from __future__ import annotations

import json
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .errors import ShowrunError, require
from .review import MAX_MEDIA_READ, ReviewStore
from .review_ui import standalone_html


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


class _ReviewHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class ReviewService:
    """Lifecycle wrapper for an authenticated, loopback-first review server."""

    def __init__(
        self,
        store: ReviewStore,
        host: str = "127.0.0.1",
        port: int = 0,
        token: str | None = None,
        authorized_workspaces: dict[str, list[str] | set[str] | None] | None = None,
    ):
        self.store = store
        self.host = host
        self.port = port
        require(authorized_workspaces, "The review service requires an explicit workspace scope.", "scope_required")
        self.authorized_workspaces = {
            workspace: demos for workspace, demos in authorized_workspaces.items()
        }
        for workspace, demos in self.authorized_workspaces.items():
            store.set_workspace_scope(workspace, demos)
        self.default_workspace = next(iter(self.authorized_workspaces))
        self.token = token or secrets.token_urlsafe(32)
        self._bootstrap_token = secrets.token_urlsafe(32)
        self._bootstrap_consumed = False
        self._sessions: dict[str, str] = {}
        self._session_lock = threading.Lock()
        self._zip_transfers: dict[str, tuple[str, str, str, bytes, dict]] = {}
        self._zip_lock = threading.Lock()
        self._http: _ReviewHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def check_workspace(self, workspace_id: str) -> str:
        require(workspace_id in self.authorized_workspaces,
                "This review workspace is outside the authorized scope.", "scope_denied")
        return workspace_id

    def _new_session(self) -> tuple[str, str]:
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        with self._session_lock:
            self._sessions[session_id] = csrf_token
        return session_id, csrf_token

    def _session_csrf(self, session_id: str) -> str | None:
        with self._session_lock:
            return self._sessions.get(session_id)

    def start(self) -> dict:
        if self._http is not None:
            return self.info()
        service = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "ShowrunReview/1"

            def log_message(self, *_args):
                return

            def _authorized(self) -> bool:
                parsed = urlparse(self.path)
                query_token = parse_qs(parsed.query).get("token", [None])[0]
                header = self.headers.get("Authorization", "")
                bearer = header[7:] if header.startswith("Bearer ") else None
                cookies = self.headers.get("Cookie", "")
                cookie_values = {}
                for part in cookies.split(";"):
                    if "=" in part:
                        key, value = part.strip().split("=", 1)
                        cookie_values[key] = value
                session_id = cookie_values.get("showrun_review_session")
                if query_token:
                    if self.command != "GET" or parsed.path not in {"/", "/index.html"}:
                        self.send_error(HTTPStatus.UNAUTHORIZED, "The review bootstrap URL is not an API credential.")
                        return False
                    with service._session_lock:
                        if (
                            service._bootstrap_consumed
                            or not secrets.compare_digest(query_token, service._bootstrap_token)
                        ):
                            self.send_error(HTTPStatus.UNAUTHORIZED, "The review bootstrap URL has expired.")
                            return False
                        service._bootstrap_consumed = True
                    self._auth_mode = "bootstrap"
                    self._session_to_set = service._new_session()
                    return True
                if bearer and secrets.compare_digest(bearer, service.token):
                    self._auth_mode = "bearer"
                    return True
                if session_id and service._session_csrf(session_id):
                    self._auth_mode = "cookie"
                    self._session_id = session_id
                    return True
                self.send_error(HTTPStatus.UNAUTHORIZED, "Review authentication required.")
                return False

            def _headers(self, content_type: str, length: int | None = None, status: int = 200):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                if length is not None:
                    self.send_header("Content-Length", str(length))
                session_to_set = getattr(self, "_session_to_set", None)
                if session_to_set:
                    session_id, csrf_token = session_to_set
                    self.send_header(
                        "Set-Cookie",
                        f"showrun_review_session={session_id}; HttpOnly; SameSite=Strict; Path=/",
                    )
                    self.send_header(
                        "Set-Cookie",
                        f"showrun_review_csrf={csrf_token}; SameSite=Strict; Path=/",
                    )

            def _require_mutation_security(self, parsed):
                require(parsed.path == "/api/call", "Mutations must use the review API endpoint.", "csrf")
                content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                require(content_type == "application/json", "Review mutations require application/json.", "csrf")
                if getattr(self, "_auth_mode", None) != "cookie":
                    return
                expected_origin = f"http://{service.host}:{service.port}"
                origin = self.headers.get("Origin")
                referer = self.headers.get("Referer")
                if origin:
                    require(origin == expected_origin, "Review mutation origin is not authorized.", "csrf")
                else:
                    require(
                        referer and urlparse(referer).scheme + "://" + urlparse(referer).netloc == expected_origin,
                        "Review mutation needs a same-origin Origin or Referer.", "csrf",
                    )
                csrf_cookie = next(
                    (
                        part.split("=", 1)[1] for part in self.headers.get("Cookie", "").split(";")
                        if part.strip().startswith("showrun_review_csrf=") and "=" in part
                    ),
                    None,
                )
                csrf_header = self.headers.get("X-Showrun-CSRF")
                expected_csrf = service._session_csrf(self._session_id)
                require(
                    csrf_cookie and csrf_header and expected_csrf
                    and secrets.compare_digest(csrf_cookie, expected_csrf)
                    and secrets.compare_digest(csrf_header, expected_csrf),
                    "Review mutation CSRF validation failed.", "csrf",
                )

            def _send_json(self, value, status=200):
                data = _json_bytes(value)
                self._headers("application/json; charset=utf-8", len(data), status)
                self.end_headers()
                self.wfile.write(data)

            def _error(self, error: Exception):
                if isinstance(error, ShowrunError):
                    self._send_json({"status": "failed", "error": error.public()}, 409 if error.code in {
                    "review_conflict", "active_work", "selection_conflict", "confirmation_required",
                    "transfer_expired"
                    } else 400)
                else:
                    self._send_json({"status": "failed", "error": {
                        "code": "review_failed", "message": "The review operation could not be completed.",
                        "remedy": "Inspect the retained review state and retry with the exact target.",
                    }}, 500)

            def do_GET(self):
                if not self._authorized():
                    return
                parsed = urlparse(self.path)
                try:
                    if getattr(self, "_auth_mode", None) == "bootstrap":
                        session_id, csrf_token = self._session_to_set
                        self.send_response(HTTPStatus.SEE_OTHER)
                        self.send_header("Location", parsed.path or "/")
                        self.send_header("Cache-Control", "no-store")
                        self.send_header(
                            "Set-Cookie",
                            f"showrun_review_session={session_id}; HttpOnly; SameSite=Strict; Path=/",
                        )
                        self.send_header(
                            "Set-Cookie",
                            f"showrun_review_csrf={csrf_token}; SameSite=Strict; Path=/",
                        )
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    if parsed.path in {"/", "/index.html"}:
                        data = standalone_html().encode()
                        self._headers("text/html; charset=utf-8", len(data))
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    if parsed.path == "/api/state":
                        query = parse_qs(parsed.query)
                        workspace_id = service.check_workspace(
                            query.get("workspace_id", [service.default_workspace])[0]
                        )
                        self._send_json(service.store.workspace(workspace_id))
                        return
                    if parsed.path.startswith("/media/"):
                        parts = parsed.path.split("/")
                        if len(parts) != 4:
                            raise ShowrunError("not_found", "The requested media resource is not available.")
                        workspace_id = service.check_workspace(parts[2])
                        clip_id = parts[3]
                        info = service.store.describe_media(workspace_id, clip_id)
                        path = service.store._safe_child(service.store._take_dir(info["take_id"]), "capture.mp4", must_exist=True)
                        total = path.stat().st_size
                        start, end, status = 0, total - 1, 200
                        range_header = self.headers.get("Range")
                        if range_header:
                            try:
                                value = range_header.removeprefix("bytes=").split("-", 1)
                                start = int(value[0])
                                end = min(total - 1, int(value[1])) if value[1] else total - 1
                            except (ValueError, IndexError):
                                raise ShowrunError("invalid_range", "Media range is invalid.") from None
                            if start < 0 or start > end or start >= total:
                                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                                return
                            status = 206
                        length = end - start + 1
                        self._headers("video/mp4", length, status)
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("ETag", info["content_sha256"])
                        if status == 206:
                            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
                        self.end_headers()
                        with path.open("rb") as stream:
                            stream.seek(start)
                            remaining = length
                            while remaining:
                                block = stream.read(min(MAX_MEDIA_READ, remaining))
                                if not block:
                                    break
                                self.wfile.write(block)
                                remaining -= len(block)
                        return
                    if parsed.path == "/download/mp4":
                        query = parse_qs(parsed.query)
                        workspace_id = service.check_workspace(query["workspace_id"][0])
                        data, info = service.store.download_mp4(
                            workspace_id, query["clip_id"][0]
                        )
                        self._headers(info["mime_type"], len(data))
                        self.send_header("Content-Disposition", f'attachment; filename="{info["filename"]}"')
                        self.send_header("ETag", info["sha256"])
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    if parsed.path == "/download/zip":
                        query = parse_qs(parsed.query)
                        workspace_id = service.check_workspace(query["workspace_id"][0])
                        transfer_id = query.get("transfer_id", [None])[0]
                        require(transfer_id, "A ZIP snapshot transfer is required.", "transfer_expired")
                        with service._zip_lock:
                            transfer = service._zip_transfers.get(transfer_id)
                        require(
                            transfer is not None and transfer[0] == workspace_id
                            and transfer[1] == query["demo_id"][0],
                            "The ZIP snapshot transfer is expired or outside this workspace.",
                            "transfer_expired",
                        )
                        service.store.assert_zip_scope(
                            workspace_id, transfer[1], transfer[2], transfer[4].get("scope_members")
                        )
                        data, inventory = transfer[3], transfer[4]
                        filename = f"{query['demo_id'][0]}.zip"
                        self._headers("application/zip", len(data))
                        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                        self.send_header("X-Showrun-Zip-Complete", str(bool(inventory["complete"])).lower())
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    self.send_error(HTTPStatus.NOT_FOUND, "Review route not found.")
                except Exception as error:
                    self._error(error)

            def do_POST(self):
                if not self._authorized():
                    return
                try:
                    self._require_mutation_security(urlparse(self.path))
                    length = int(self.headers.get("Content-Length", "0"))
                    if length > 2 * 1024 * 1024:
                        raise ShowrunError("input_limit", "Review request is too large.")
                    value = json.loads(self.rfile.read(length) or b"{}")
                    operation = value.get("operation")
                    args = value.get("arguments") or {}
                    result = service.call(operation, args)
                    self._send_json(result)
                except Exception as error:
                    self._error(error)

        self._http = _ReviewHTTPServer((self.host, self.port), Handler)
        self.host, self.port = self._http.server_address[:2]
        self._thread = threading.Thread(target=self._http.serve_forever, name="showrun-review", daemon=True)
        self._thread.start()
        return self.info()

    def call(self, operation: str, args: dict) -> dict:
        args = dict(args or {})
        if operation in {
            "workspace", "list_demos", "select_clip", "playback", "appearance", "rename",
            "prepare_delete", "delete", "save_draft", "submit_note", "begin_intent", "ack_intent",
            "reject_intent", "prepare_zip",
        }:
            args["workspace_id"] = self.check_workspace(
                args.get("workspace_id", self.default_workspace)
            )
        if operation == "workspace":
            return self.store.workspace(args["workspace_id"])
        if operation == "list_demos":
            return self.store.list_demos(
                args["workspace_id"], args.get("offset", 0), args.get("limit", 100)
            )
        if operation == "select_clip":
            return self.store.select_clip(
                args["workspace_id"], args["demo_id"], args["take_id"], args["clip_id"],
                args["expected_version"], args["request_id"], step_id=args.get("step_id"),
                time_seconds=args.get("time_seconds"),
            )
        if operation == "playback":
            return self.store.set_playback(args["workspace_id"], args["clip_id"], args["position_seconds"])
        if operation == "appearance":
            return self.store.set_appearance(
                args["workspace_id"], args["appearance"], args.get("expected_version"), args.get("request_id")
            )
        if operation == "rename":
            return self.store.rename(
                args["item_type"], args["item_id"], args["name"], args["expected_version"], args["request_id"],
                workspace_id=args.get("workspace_id"),
            )
        if operation == "prepare_delete":
            return self.store.prepare_delete(
                args["scope"], args["target_id"], args["expected_version"], args.get("request_id"),
                workspace_id=args.get("workspace_id"),
            )
        if operation == "delete":
            return self.store.delete(args["confirmation_token"], args.get("request_id"),
                                     workspace_id=args.get("workspace_id"))
        if operation == "save_draft":
            anchor = {key: args[key] for key in (
                "step_id", "time_seconds", "range_start_seconds", "range_end_seconds"
            ) if key in args}
            return self.store.save_draft(
                args["workspace_id"], args["clip_id"], args.get("text", ""), expected_version=args.get("expected_version"),
                request_id=args.get("request_id"), intent_id=args.get("intent_id"), **anchor
            )
        if operation == "submit_note":
            return self.store.submit_note(
                args["workspace_id"], args["clip_id"], expected_version=args.get("expected_version"),
                request_id=args.get("request_id"), text=args.get("text"),
                step_id=args.get("step_id"), time_seconds=args.get("time_seconds"),
                range_start_seconds=args.get("range_start_seconds"),
                range_end_seconds=args.get("range_end_seconds"), intent_id=args.get("intent_id"),
            )
        if operation == "begin_intent":
            return self.store.begin_intent(
                args["workspace_id"], args["intent_id"], args["kind"], args.get("payload") or {}
            )
        if operation == "ack_intent":
            return self.store.ack_intent(args["workspace_id"], args["intent_id"])
        if operation == "reject_intent":
            return self.store.reject_intent(
                args["workspace_id"], args["intent_id"], args["code"], args["message"], args.get("remedy")
            )
        if operation == "prepare_zip":
            data, inventory, scope_token = self.store.prepare_zip(args["workspace_id"], args["demo_id"])
            transfer_id = secrets.token_urlsafe(24)
            with self._zip_lock:
                self._zip_transfers[transfer_id] = (
                    args["workspace_id"], args["demo_id"], scope_token, data, inventory
                )
            return {
                "status": "zip_inventory",
                "workspace_id": args["workspace_id"],
                "demo_id": args["demo_id"],
                "transfer_id": transfer_id,
                **inventory,
            }
        raise ShowrunError("unsupported_operation", "The requested review operation is not supported.",
                           "Use the documented review capabilities.")

    def info(self) -> dict:
        return {
            "status": "ready" if self._http else "stopped",
            "host": self.host,
            "port": self.port,
            "url": f"http://{self.host}:{self.port}/?token={self._bootstrap_token}",
            "authenticated": True,
            "bootstrap_token_one_time": True,
            "authorized_workspaces": sorted(self.authorized_workspaces),
            "workspace": "retained capture review only",
        }

    def stop(self) -> None:
        if self._http is not None:
            self._http.shutdown()
            self._http.server_close()
            if self._thread:
                self._thread.join(timeout=5)
        self._http = self._thread = None

    def serve_forever(self) -> dict:
        info = self.start()
        try:
            assert self._thread is not None
            self._thread.join()
        except KeyboardInterrupt:
            self.stop()
        return info


def serve_review(takes_root: str | Path, host: str = "127.0.0.1", port: int = 0,
                 token: str | None = None) -> ReviewService:
    authorized_workspaces = {"default": None}
    service = ReviewService(
        ReviewStore(takes_root, workspace_scopes=authorized_workspaces),
        host=host,
        port=port,
        token=token,
        authorized_workspaces=authorized_workspaces,
    )
    service.start()
    return service


def _serve_review_with_scope(
    takes_root: str | Path,
    host: str = "127.0.0.1",
    port: int = 0,
    token: str | None = None,
    authorized_workspaces: dict[str, list[str] | set[str] | None] | None = None,
):
    scopes = authorized_workspaces or {"default": None}
    service = ReviewService(
        ReviewStore(takes_root, workspace_scopes=scopes),
        host=host,
        port=port,
        token=token,
        authorized_workspaces=scopes,
    )
    service.start()
    return service


serve_review = _serve_review_with_scope  # noqa: F811
