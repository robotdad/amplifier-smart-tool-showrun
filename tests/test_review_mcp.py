"""Real stdio MCP transport checks; no fake postMessage transport is used here."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from test_review import _clips, _take

from amplifier_smart_tool_showrun import ReviewStore


def test_stdio_mcp_retained_review_reconnect_and_bounded_resource(tmp_path):
    pytest.importorskip("mcp")
    from mcp import Client, StdioServerParameters

    _take(tmp_path, "take-a", "red")
    # Build the metadata once in the caller to discover the exact stable identity.
    state = ReviewStore(tmp_path).workspace()
    clip = _clips(state)[0]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "amplifier_smart_tool_showrun.mcp", "--storage", str(tmp_path), "--workspace", "default"],
        env=env,
    )

    async def run():
        for index in range(2):
            async with Client(params) as client:
                tools = await client.list_tools()
                assert "showrun_review" in {tool.name for tool in tools.tools}
                result = await client.call_tool("showrun_review", {"workspace_id": "default"})
                value = result.structured_content["result"]
                assert value["capabilities"]["model"] is False
                assert value["demos"]
                if index == 0:
                    selected = await client.call_tool(
                        "showrun_select_clip",
                        {
                            "workspace_id": "default",
                            "demo_id": clip["demo_id"],
                            "take_id": clip["take_id"],
                            "clip_id": clip["clip_id"],
                            "expected_version": value["version"],
                            "request_id": "stdio-select",
                        },
                    )
                    assert selected.structured_content["result"]["selection"]["clip_id"] == clip["clip_id"]
                    descriptor = await client.call_tool(
                        "showrun_describe_media",
                        {"workspace_id": "default", "clip_id": clip["clip_id"]},
                    )
                    info = descriptor.structured_content["result"]
                    assert info["chunk_bytes"] == 524288
                    chunk = await client.read_resource(info["resource_uri"])
                    assert chunk.contents[0].blob
                else:
                    refreshed = await client.call_tool("showrun_review", {"workspace_id": "default"})
                    assert refreshed.structured_content["result"]["selection"]["clip_id"] == clip["clip_id"]

    asyncio.run(run())


def test_stdio_mcp_domain_errors_keep_code_and_remedy(tmp_path):
    pytest.importorskip("mcp")
    from mcp import Client, StdioServerParameters

    _take(tmp_path, "take-a", "red")
    state = ReviewStore(tmp_path).workspace()
    clip = _clips(state)[0]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "amplifier_smart_tool_showrun.mcp", "--storage", str(tmp_path), "--workspace", "default"],
        env=env,
    )

    async def run():
        async with Client(params) as client:
            selected = await client.call_tool(
                "showrun_select_clip",
                {
                    "workspace_id": "default",
                    "demo_id": clip["demo_id"],
                    "take_id": clip["take_id"],
                    "clip_id": clip["clip_id"],
                    "expected_version": state["version"],
                    "request_id": "structured-error-select",
                },
            )
            version = selected.structured_content["result"]["version"]
            failure = await client.call_tool(
                "showrun_save_note_draft",
                {
                    "workspace_id": "default",
                    "clip_id": clip["clip_id"],
                    "text": "bad range",
                    "range_start_seconds": 1.5,
                    "range_end_seconds": 0.5,
                    "expected_version": version,
                    "request_id": "structured-error-draft",
                },
            )
            assert failure.is_error is True
            text = failure.content[0].text
            detail = json.loads(text[text.index("{"):])
            assert detail["error"]["code"] == "invalid_anchor"
            assert "ordered" in detail["error"]["message"]
            assert detail["error"]["remedy"]

    asyncio.run(run())


def test_stdio_mcp_zip_resource_reports_actionable_expiry_after_revoke(tmp_path):
    pytest.importorskip("mcp")
    from mcp import Client, StdioServerParameters

    _take(tmp_path, "take-a", "red")
    external = ReviewStore(tmp_path)
    clip = _clips(external.workspace())[0]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "amplifier_smart_tool_showrun.mcp", "--storage", str(tmp_path), "--workspace", "default"],
        env=env,
    )

    async def run():
        async with Client(params) as client:
            prepared = await client.call_tool(
                "showrun_download_zip",
                {"workspace_id": "default", "demo_id": clip["demo_id"]},
            )
            uri = prepared.structured_content["result"]["resource_uri"]
            confirmation = external.prepare_delete(
                "clip", clip["clip_id"], clip["version"], "mcp-resource-delete", workspace_id="default"
            )
            external.delete(
                confirmation["confirmation_token"], "mcp-resource-delete-commit", workspace_id="default"
            )
            with pytest.raises(Exception) as failure:
                await client.read_resource(uri)
            text = str(failure.value)
            assert "transfer_expired" in text
            assert "restart" in text.lower() or "deleted" in text.lower()

    asyncio.run(run())
