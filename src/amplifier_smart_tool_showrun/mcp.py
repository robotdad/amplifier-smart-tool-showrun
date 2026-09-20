"""Official stdio MCP/AppBridge adapter over the public review library.

The adapter has no domain state of its own.  Its allow-list is the caller's
explicit workspace boundary and every tool/resource delegates to ReviewStore.
"""

from __future__ import annotations

import json
import threading
import uuid
from functools import wraps
from importlib.resources import files
from typing import Any

from .errors import ShowrunError, require
from .review import MAX_MEDIA_READ, ReviewStore
from .review_ui import mcp_html

UI_URI = "ui://showrun/capture-review"
CHUNK_BYTES = MAX_MEDIA_READ


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def create_server(store: ReviewStore, allowed_workspaces: list[str] | tuple[str, ...] | set[str],
                  *, workspace_scopes: dict[str, list[str] | set[str] | None] | None = None,
                  ui_resource: str | None = None):
    """Build an MCP server over an explicit metadata/media scope."""
    try:
        from mcp.server import MCPServer
        from mcp.server.apps import Apps, ResourceCsp
        from mcp.server.mcpserver.exceptions import ResourceError, ToolError
        from mcp.types import ToolAnnotations
    except ImportError:
        raise ShowrunError("mcp_missing", "MCP support is not installed.",
                           "Install the optional MCP review dependency.") from None

    allowed = {_id for _id in allowed_workspaces if isinstance(_id, str)}
    require(allowed, "At least one explicitly authorized review workspace is required.", "scope_required")
    supplied_scopes = workspace_scopes or {}
    for workspace_id, demos in supplied_scopes.items():
        require(workspace_id in allowed, "A scope was supplied for an unauthorized workspace.", "scope_denied")
    for workspace_id in allowed:
        store.set_workspace_scope(workspace_id, supplied_scopes.get(workspace_id))
    transfers: dict[str, tuple[str, str, str, bytes, dict[str, Any]]] = {}
    transfer_lock = threading.Lock()
    apps = Apps()
    html = ui_resource
    if html is None:
        resource = files("amplifier_smart_tool_showrun").joinpath("resources", "mcp_app.html")
        html = resource.read_text(encoding="utf-8") if resource.is_file() else mcp_html()
    bootstrap = "<script>window.__SHOWRUN_WORKSPACE__=" + json.dumps(sorted(allowed)[0]) + ";</script>"
    html = html.replace("<head>", "<head>" + bootstrap, 1)
    apps.add_html_resource(
        UI_URI, html, title="Showrun · Capture review",
        description="Review retained Showrun demonstrations without starting capture or model work.",
        csp=ResourceCsp(connectDomains=[], resourceDomains=[], frameDomains=["blob:"]),
        prefers_border=True,
    )
    server = MCPServer(
        "showrun",
        title="Showrun capture review",
        version="0.1.0",
        extensions=[apps],
        instructions=(
            "This adapter reviews produced Showrun recordings only. It never launches a target, "
            "calls a model or replays a request. Use an explicitly authorized workspace ID. "
            "Selection state is versioned and includes demo, take, clip and media identities. "
            "Media resources are bounded chunks; paths and arbitrary files are never accepted. "
            "Renames use stable identities and expected versions. Deletion requires an exact "
            "confirmation snapshot and retains execution receipts/request tombstones. Draft notes "
            "are not submitted notes. The dashboard and MCP App use the same UI and capabilities; "
            "host context theme changes are presentation-only."
        ),
    )

    def checked(workspace_id: str) -> str:
        require(workspace_id in allowed, "This review workspace is outside the authorized scope.", "scope_denied")
        return workspace_id

    def result(value: dict[str, Any]) -> dict[str, Any]:
        return {"operation": "showrun_review", "result": value}

    def _public_errors(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except ShowrunError as error:
                raise ToolError(_json({"status": "failed", "error": error.public()})) from None
        return wrapped

    @server.tool(
        name="showrun_review",
        description="Read bounded retained review state for one explicitly authorized workspace.",
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False),
        meta={"ui": {"resourceUri": UI_URI, "visibility": ["model", "app"]}},
        structured_output=True,
    )
    @_public_errors
    def review(workspace_id: str = "default") -> dict[str, Any]:
        checked(workspace_id)
        return result(store.workspace(workspace_id))

    @server.tool(
        name="showrun_list_demos",
        description="List explicitly grouped retained demos and their takes/clips.",
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False),
        meta={"ui": {"resourceUri": UI_URI, "visibility": ["model", "app"]}},
        structured_output=True,
    )
    @_public_errors
    def list_demos(workspace_id: str = "default", offset: int = 0, limit: int = 100) -> dict[str, Any]:
        checked(workspace_id)
        return result(store.list_demos(workspace_id, offset, limit))

    @server.tool(
        name="showrun_select_clip",
        description="Persist a version-checked exact demo/take/clip selection.",
        annotations=ToolAnnotations(read_only_hint=False, idempotent_hint=True, open_world_hint=False),
        meta={"ui": {"resourceUri": UI_URI, "visibility": ["model", "app"]},
              "review_scope": "workspace"},
        structured_output=True,
    )
    @_public_errors
    def select_clip(workspace_id: str, demo_id: str, take_id: str, clip_id: str,
                    expected_version: int, request_id: str, step_id: str | None = None,
                    time_seconds: float | None = None) -> dict[str, Any]:
        checked(workspace_id)
        return result(store.select_clip(workspace_id, demo_id, take_id, clip_id, expected_version, request_id,
                                        step_id=step_id, time_seconds=time_seconds))

    @server.tool(
        name="showrun_set_playback",
        description="Persist the current position for the currently selected exact clip.",
        annotations=ToolAnnotations(read_only_hint=False, idempotent_hint=True, open_world_hint=False),
        meta={"ui": {"resourceUri": UI_URI, "visibility": ["model", "app"]}},
        structured_output=True,
    )
    @_public_errors
    def set_playback(workspace_id: str, clip_id: str, position_seconds: float) -> dict[str, Any]:
        checked(workspace_id)
        return result(store.set_playback(workspace_id, clip_id, position_seconds))

    @server.tool(
        name="showrun_set_appearance",
        description="Persist light, dark or system preference for this review workspace.",
        annotations=ToolAnnotations(read_only_hint=False, idempotent_hint=True, open_world_hint=False),
        meta={"ui": {"resourceUri": UI_URI, "visibility": ["model", "app"]}},
        structured_output=True,
    )
    @_public_errors
    def set_appearance(workspace_id: str, appearance: str, expected_version: int | None = None,
                       request_id: str | None = None) -> dict[str, Any]:
        checked(workspace_id)
        return result(store.set_appearance(workspace_id, appearance, expected_version, request_id))

    @server.tool(
        name="showrun_begin_intent",
        description="Durably record one exact review mutation intent before its effect.",
        annotations=ToolAnnotations(read_only_hint=False, idempotent_hint=True, open_world_hint=False),
        meta={"ui": {"resourceUri": UI_URI, "visibility": ["model", "app"]}},
        structured_output=True,
    )
    @_public_errors
    def begin_intent(workspace_id: str, intent_id: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        checked(workspace_id)
        return result(store.begin_intent(workspace_id, intent_id, kind, payload))

    @server.tool(
        name="showrun_ack_intent",
        description="Acknowledge a completed review receipt for this named workspace without replaying it.",
        annotations=ToolAnnotations(read_only_hint=False, idempotent_hint=True, open_world_hint=False),
        meta={"ui": {"resourceUri": UI_URI, "visibility": ["model", "app"]}},
        structured_output=True,
    )
    @_public_errors
    def ack_intent(workspace_id: str, intent_id: str) -> dict[str, Any]:
        checked(workspace_id)
        return result(store.ack_intent(workspace_id, intent_id))

    @server.tool(
        name="showrun_reject_intent",
        description="Record a definitive no-effect review rejection so it is not retried after reconnect.",
        annotations=ToolAnnotations(read_only_hint=False, idempotent_hint=True, open_world_hint=False),
        meta={"ui": {"resourceUri": UI_URI, "visibility": ["model", "app"]}},
        structured_output=True,
    )
    @_public_errors
    def reject_intent(workspace_id: str, intent_id: str, code: str, message: str,
                      remedy: str | None = None) -> dict[str, Any]:
        checked(workspace_id)
        return result(store.reject_intent(workspace_id, intent_id, code, message, remedy))

    @server.tool(
        name="showrun_rename",
        description="Rename one exact demo, take or clip with stable identity and CAS version.",
        annotations=ToolAnnotations(read_only_hint=False, idempotent_hint=True, open_world_hint=False),
        meta={"ui": {"resourceUri": UI_URI, "visibility": ["model", "app"]}},
        structured_output=True,
    )
    @_public_errors
    def rename(workspace_id: str, item_type: str, item_id: str, name: str, expected_version: int,
               request_id: str) -> dict[str, Any]:
        checked(workspace_id)
        return result(store.rename(item_type, item_id, name, expected_version, request_id,
                                   workspace_id=workspace_id))

    @server.tool(
        name="showrun_prepare_delete",
        description="Create an exact deletion confirmation snapshot; this does not delete media.",
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False),
        meta={"ui": {"resourceUri": UI_URI, "visibility": ["model", "app"]}},
        structured_output=True,
    )
    @_public_errors
    def prepare_delete(workspace_id: str, scope: str, target_id: str, expected_version: int,
                       request_id: str | None = None) -> dict[str, Any]:
        checked(workspace_id)
        return result(store.prepare_delete(scope, target_id, expected_version, request_id,
                                           workspace_id=workspace_id))

    @server.tool(
        name="showrun_delete",
        description="Confirm deletion of exactly the previously snapshotted scope.",
        annotations=ToolAnnotations(read_only_hint=False, idempotent_hint=True, destructive_hint=True,
                                    open_world_hint=False),
        meta={"ui": {"resourceUri": UI_URI, "visibility": ["model", "app"]}},
        structured_output=True,
    )
    @_public_errors
    def delete(workspace_id: str, confirmation_token: str, request_id: str | None = None) -> dict[str, Any]:
        checked(workspace_id)
        return result(store.delete(confirmation_token, request_id, workspace_id=workspace_id))

    @server.tool(
        name="showrun_save_note_draft",
        description="Save an optional bounded, precisely targeted note draft on the selected clip.",
        annotations=ToolAnnotations(read_only_hint=False, idempotent_hint=True, open_world_hint=False),
        meta={"ui": {"resourceUri": UI_URI, "visibility": ["model", "app"]}},
        structured_output=True,
    )
    @_public_errors
    def save_note_draft(workspace_id: str, clip_id: str, text: str, step_id: str | None = None,
                        time_seconds: float | None = None, range_start_seconds: float | None = None,
                        range_end_seconds: float | None = None, expected_version: int | None = None,
                        request_id: str | None = None, intent_id: str | None = None) -> dict[str, Any]:
        checked(workspace_id)
        return result(store.save_draft(
            workspace_id, clip_id, text, step_id=step_id, time_seconds=time_seconds,
            range_start_seconds=range_start_seconds, range_end_seconds=range_end_seconds,
            expected_version=expected_version, request_id=request_id, intent_id=intent_id,
        ))

    @server.tool(
        name="showrun_submit_note",
        description="Submit the saved draft for the still-selected exact clip; never starts model/capture work.",
        annotations=ToolAnnotations(read_only_hint=False, idempotent_hint=True, open_world_hint=False),
        meta={"ui": {"resourceUri": UI_URI, "visibility": ["model", "app"]}},
        structured_output=True,
    )
    @_public_errors
    def submit_note(workspace_id: str, clip_id: str, expected_version: int | None = None,
                    request_id: str | None = None, text: str | None = None,
                    step_id: str | None = None, time_seconds: float | None = None,
                    range_start_seconds: float | None = None, range_end_seconds: float | None = None,
                    intent_id: str | None = None) -> dict[str, Any]:
        checked(workspace_id)
        return result(store.submit_note(
            workspace_id, clip_id, expected_version=expected_version, request_id=request_id,
            text=text, step_id=step_id, time_seconds=time_seconds,
            range_start_seconds=range_start_seconds, range_end_seconds=range_end_seconds,
            intent_id=intent_id,
        ))

    @server.tool(
        name="showrun_describe_media",
        description="Describe bounded MP4 resource delivery for the exact selected clip.",
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False),
        meta={"ui": {"resourceUri": UI_URI, "visibility": ["model", "app"]}},
        structured_output=True,
    )
    @_public_errors
    def describe_media(workspace_id: str, clip_id: str) -> dict[str, Any]:
        checked(workspace_id)
        return result(store.describe_media(workspace_id, clip_id))

    @server.tool(
        name="showrun_describe_zip",
        description="Prepare an identified ZIP inventory of all authorized assets in one demo.",
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False),
        meta={"ui": {"resourceUri": UI_URI, "visibility": ["model", "app"]}},
        structured_output=True,
    )
    @_public_errors
    def describe_zip(workspace_id: str, demo_id: str) -> dict[str, Any]:
        checked(workspace_id)
        return result(store.describe_zip(workspace_id, demo_id))

    @server.tool(
        name="showrun_download_zip",
        description="Prepare an immutable ZIP snapshot resource for one authorized demo.",
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False),
        meta={"ui": {"resourceUri": UI_URI, "visibility": ["model", "app"]}},
        structured_output=True,
    )
    @_public_errors
    def download_zip(workspace_id: str, demo_id: str) -> dict[str, Any]:
        checked(workspace_id)
        data, inventory, scope_token = store.prepare_zip(workspace_id, demo_id)
        transfer_id = uuid.uuid4().hex
        with transfer_lock:
            while len(transfers) >= 4:
                transfers.pop(next(iter(transfers)))
            transfers[transfer_id] = (workspace_id, demo_id, scope_token, data, inventory)
        return result({
            "workspace_id": workspace_id, "demo_id": demo_id, **inventory,
            "resource_uri": f"showrun://workspace/{workspace_id}/zip/{transfer_id}/0",
            "mime_type": "application/zip", "bytes": len(data), "chunk_bytes": CHUNK_BYTES,
        })

    @server.resource(
        "showrun://workspace/{workspace_id}/review",
        name="showrun-review-state",
        description="Bounded public review state for an explicitly authorized workspace.",
        mime_type="application/json",
    )
    def review_resource(workspace_id: str) -> str:
        checked(workspace_id)
        return _json(store.workspace(workspace_id))

    @server.resource(
        "showrun://workspace/{workspace_id}/media/{clip_id}/{offset}",
        name="showrun-media",
        description="One bounded chunk of the selected retained MP4; no filesystem paths.",
        mime_type="video/mp4",
    )
    def media_resource(workspace_id: str, clip_id: str, offset: int) -> bytes:
        try:
            checked(workspace_id)
            return store.read_media(workspace_id, clip_id, offset, CHUNK_BYTES)
        except ShowrunError as error:
            raise ResourceError(_json({"status": "failed", "error": error.public()})) from None

    @server.resource(
        "showrun://workspace/{workspace_id}/zip/{transfer_id}/{offset}",
        name="showrun-zip",
        description="One bounded chunk of an identified ZIP snapshot.",
        mime_type="application/zip",
    )
    def zip_resource(workspace_id: str, transfer_id: str, offset: int) -> bytes:
        try:
            checked(workspace_id)
            require(type(offset) is int and offset >= 0 and offset % CHUNK_BYTES == 0,
                    "ZIP offset must be aligned to the bounded chunk size.", "invalid_range")
            with transfer_lock:
                transfer = transfers.get(transfer_id)
            require(transfer is not None and transfer[0] == workspace_id,
                    "ZIP transfer is expired or out of scope.", "transfer_expired")
            store.assert_zip_scope(
                workspace_id, transfer[1], transfer[2], transfer[4].get("scope_members")
            )
            data = transfer[3]
            require(
                offset < len(data) or offset == len(data) == 0,
                "ZIP offset is outside the snapshot.",
                "invalid_range",
            )
            return data[offset:offset + CHUNK_BYTES]
        except ShowrunError as error:
            raise ResourceError(_json({"status": "failed", "error": error.public()})) from None

    @server.tool(
        name="showrun_status",
        description="Provider-free MCP review adapter status.",
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False),
        meta={"ui": {"resourceUri": UI_URI, "visibility": ["model", "app"]}},
        structured_output=True,
    )
    @_public_errors
    def status(workspace_id: str = "default") -> dict[str, Any]:
        checked(workspace_id)
        return result({
            "workspace_id": workspace_id, "ui_resource": UI_URI, "model_access": False,
            "authorized_workspaces": sorted(allowed), "media_chunk_bytes": CHUNK_BYTES,
            "limits": ["No target startup", "No provider/model calls", "No arbitrary paths or live capture"],
        })

    return server


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Serve Showrun retained capture review over official stdio MCP/AppBridge."
    )
    parser.add_argument("--storage", required=True, help="Configured retained take root; never a raw media path.")
    parser.add_argument("--workspace", action="append", required=True,
                        help="Explicit workspace ID; repeat to authorize more workspaces.")
    parser.add_argument("--demo", action="append",
                        help="Optional demo ID scope for the default workspace; repeatable.")
    args = parser.parse_args(argv)
    try:
        import mcp  # noqa: F401
    except ImportError:
        parser.error("MCP review support is optional; install the package with its MCP extra.")
    scopes = {"default": args.demo} if args.demo else None
    server = create_server(ReviewStore(args.storage), args.workspace, workspace_scopes=scopes)
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
