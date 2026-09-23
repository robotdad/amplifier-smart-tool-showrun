"""Offline receipt/provider/companion boundaries; not native macOS acceptance."""
import asyncio
import json
import logging
import sys
from types import SimpleNamespace

import pytest
from test_desktop import Bridge, native_runtime, request  # noqa: F401
from test_showrun import MODEL

from amplifier_smart_tool_showrun import Showrun, agent, desktop
from amplifier_smart_tool_showrun.agent import Navigator
from amplifier_smart_tool_showrun.cli import main
from amplifier_smart_tool_showrun.errors import ShowrunError


@pytest.mark.parametrize("instruction", ["Show prepared state", "Execute a command producing Prepared"])
@pytest.mark.usefixtures("native_runtime")
def test_already_true_is_visible_without_forcing_action(tmp_path, instruction, capsys):
    value = request()
    value["steps"][0].update(instruction=instruction, visible_text="Prepared")
    api = Showrun(tmp_path, MODEL)
    result = api.record(value)
    assert result["status"] == "succeeded"
    row = result["steps"][0]
    assert row["status"] == "completed" and row["outcome"] == "satisfied_without_action"
    assert row["initial_result_satisfied"] is True
    assert row["first_interaction_seconds"] is None
    assert result["usage"] == {"model_calls": 0, "actions": 0}
    assert row["warnings"] == result["warnings"]
    assert "does not prove fresh execution" in result["warnings"][0]["message"]
    assert not Bridge.instances[0].actions
    assert api.record(value)["warnings"] == result["warnings"]
    assert len(Bridge.instances) == 1
    assert main(["--storage", str(tmp_path), "status", value["request_id"]]) == 0
    assert json.loads(capsys.readouterr().out)["warnings"] == result["warnings"]


@pytest.mark.usefixtures("native_runtime")
def test_later_observation_only_result_is_not_initial_success(tmp_path, monkeypatch):
    original = Bridge.call

    async def changing(self, operation, **payload):
        if operation == "observe" and self.generation >= 3:
            self.saved = True
        return await original(self, operation, **payload)

    monkeypatch.setattr(Bridge, "call", changing)
    value = request()
    value["steps"][0]["wait_for_result"] = True
    result = Showrun(tmp_path, MODEL).record(value)
    assert result["status"] == "succeeded"
    assert result["steps"][0]["initial_result_satisfied"] is False
    assert result["steps"][0]["outcome"] == "satisfied_without_action"
    assert result["usage"] == {"model_calls": 0, "actions": 0}


@pytest.mark.usefixtures("native_runtime")
def test_real_interaction_does_not_get_no_action_warning(tmp_path):
    result = Showrun(tmp_path, MODEL).record(request())
    assert result["status"] == "succeeded"
    assert result["steps"][0]["outcome"] == "satisfied_after_interaction"
    assert result["steps"][0]["initial_result_satisfied"] is False
    assert not result.get("warnings")


def navigator_with_error(error):
    navigator = object.__new__(Navigator)
    navigator.config = MODEL
    navigator.response_tokens = 2048
    navigator.reasoning_effort = None
    requests = []

    async def complete(request):
        requests.append(request)
        raise error

    navigator.provider = SimpleNamespace(complete=complete)
    return navigator, requests


@pytest.mark.parametrize(("name", "category"), [
    ("AuthenticationError", "authentication"), ("AccessDeniedError", "access_denied"),
    ("ContextLengthError", "context_length"), ("RateLimitError", "rate_limit"),
    ("QuotaExceededError", "quota"), ("NotFoundError", "model_not_found"),
    ("InvalidRequestError", "invalid_request"), ("LLMTimeoutError", "timeout"),
    ("ProviderUnavailableError", "unavailable"), ("NetworkError", "network"),
    ("ContentFilterError", "content_filter"), ("ConfigurationError", "configuration"),
])
def test_taxonomy_errors_are_safe_actionable_and_single_dispatch(name, category):
    from amplifier_core import llm_errors

    error = getattr(llm_errors, name)("sk-secret PRIVATE_PROMPT PRIVATE_SCREEN")
    navigator, requests = navigator_with_error(error)
    with pytest.raises(ShowrunError) as caught:
        asyncio.run(navigator.decide({}, {"frames": [], "screenshot_png": "c2NyZWVu"}, "PRIVATE_PROMPT", 2))
    public = caught.value.public()
    assert public["code"] == "provider_" + category
    assert "new request_id" in public["remedy"]
    assert not any(secret in json.dumps(public) for secret in ("sk-secret", "PRIVATE_PROMPT", "PRIVATE_SCREEN"))
    assert caught.value.__cause__ is error
    diagnostics = public["diagnostics"]["request"]
    assert diagnostics["screenshot_base64_bytes"] == 8
    assert diagnostics["input_tokens"] is None and diagnostics["max_output_tokens"] == 2048
    assert diagnostics["history_messages"] == 0
    assert len(requests) == 1 and len(requests[0].messages) == 2
    assert navigator._dispatch_available is False


@pytest.mark.parametrize("sdk", ["openai", "anthropic"])
def test_sdk_body_is_not_retained_but_status_and_context_code_are(sdk):
    import importlib

    import httpx

    module = importlib.import_module(sdk)
    response = httpx.Response(400, request=httpx.Request("POST", "https://invalid.example"))
    error = module.BadRequestError("PRIVATE_PROMPT sk-secret", response=response,
                                  body={"message": "PRIVATE_SCREEN", "code": "context_length_exceeded"})
    public = agent.provider_error(error, {}).public()
    assert public["diagnostics"]["http_status"] == 400
    assert public["code"] in {"provider_context_length", "provider_invalid_request"}
    assert "PRIVATE" not in json.dumps(public) and "sk-secret" not in json.dumps(public)


def test_wrapped_provider_cause_and_unknown_errors_are_bounded():
    from amplifier_core.llm_errors import RateLimitError

    inner = RateLimitError("PRIVATE", status_code=429)
    outer = RuntimeError("SECRET")
    outer.__cause__ = inner
    inner.__cause__ = outer  # Malformed/cyclic chains cannot loop.
    public = agent.provider_error(outer, {}).public()
    assert public["code"] == "provider_rate_limit"
    assert public["diagnostics"]["exception_types"] == ["unclassified", "RateLimitError"]
    assert public["diagnostics"]["http_status"] == 429
    assert agent.provider_error(ValueError("SECRET"), {}).code == "provider_failed"
    policy = ShowrunError("provider_incomplete", "Continuation refused", "Inspect this take.")
    outer.__cause__ = policy
    assert agent.provider_error(outer, {}).public() == policy.public()


def test_provider_logs_cannot_echo_sensitive_error_but_other_tasks_keep_logs(caplog):
    logger = logging.getLogger("amplifier_module_provider_anthropic")

    async def other_task():
        logger.warning("Unrelated task evidence")

    async def run():
        unrelated = asyncio.create_task(other_task())  # Created outside the guarded context.
        with agent.safe_provider_logs():
            try:
                raise ValueError("PRIVATE_SCREEN")
            except ValueError:
                logger.exception("sk-secret %s", "PRIVATE_PROMPT", extra={"raw": "PRIVATE"})
            await unrelated
        logger.warning("After scope evidence")

    asyncio.run(run())
    assert "PRIVATE" not in caplog.text and "sk-secret" not in caplog.text
    assert "sanitized Showrun error" in caplog.text
    assert "Unrelated task evidence" in caplog.text and "After scope evidence" in caplog.text
    assert not any(hasattr(record, "raw") for record in caplog.records)


@pytest.mark.parametrize("error", [
    ShowrunError("provider_policy", "Policy refused"), asyncio.CancelledError(),
])
def test_policy_and_cancellation_are_not_reclassified(error):
    navigator, calls = navigator_with_error(error)
    with pytest.raises(type(error)) as caught:
        asyncio.run(navigator.decide({}, {"frames": []}, "", 2))
    assert caught.value is error
    assert len(calls) == 1 and navigator._dispatch_available is False


@pytest.mark.usefixtures("native_runtime")
def test_provider_failure_receipt_keeps_diagnostics_and_never_replays(tmp_path, monkeypatch):
    from amplifier_core.llm_errors import ContextLengthError

    failing, calls = navigator_with_error(ContextLengthError("PRIVATE_SCREEN", status_code=413))
    # Keep the actual Navigator decision/error path; no provider or runtime setup.
    async def noop():
        pass
    failing.start = failing.close = noop
    monkeypatch.setattr(agent, "Navigator", lambda config: failing)
    api = Showrun(tmp_path, MODEL)
    result = api.record(request())
    assert result["status"] == "failed"
    assert result["error"]["code"] == "provider_context_length"
    assert result["steps"][0]["error"] == result["error"]
    event = result["steps"][0]["events"][0]
    assert event["request_diagnostics"] == result["error"]["diagnostics"]["request"]
    assert event["end_seconds"] >= event["start_seconds"]
    assert result["usage"] == {"model_calls": 1, "actions": 0}
    assert result["media"]["decoded"] and result["cleanup"] == "verified"
    assert "PRIVATE_SCREEN" not in json.dumps(result)
    assert api.record(request())["error"] == result["error"]
    assert len(calls) == 1 and len(Bridge.instances) == 1


@pytest.mark.parametrize("metadata", [None, {
    "version": "source-unreleased", "protocol": 1, "capabilities": ["permissions", "terminal_type"],
}, {"version": "future", "protocol": 2, "capabilities": []},
    {"version": "malformed", "protocol": "2", "capabilities": []}])
@pytest.mark.skipif(sys.platform == "win32", reason="Mac handshake simulation uses Unix-domain sockets.")
def test_authenticated_companion_status_with_old_and_new_handshake(tmp_path, monkeypatch, metadata):
    helper = tmp_path / "helper"
    helper.touch()
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setattr(desktop, "helper_path", lambda: helper)
    tasks, operations = [], []

    async def launch(*args, **kwargs):
        path, token = args[args.index("--socket") + 1], args[args.index("--token") + 1]

        async def serve():
            # Invalid/non-ASCII nonces cannot claim or break the authenticated session.
            bad_reader, bad_writer = await asyncio.open_unix_connection(path)
            bad_writer.write((json.dumps({"token": "é" * 64}) + "\n").encode())
            await bad_writer.drain()
            assert await bad_reader.read() == b""
            bad_writer.close()
            await bad_writer.wait_closed()
            reader, writer = await asyncio.open_unix_connection(path)
            hello = {"token": token}
            if metadata is not None:
                hello["companion"] = metadata
            writer.write((json.dumps(hello) + "\n").encode())
            await writer.drain()
            line = await reader.readline()
            if line:
                operations.append(json.loads(line)["operation"])
                writer.write(b'{"screen_recording":true,"accessibility":true}\n')
                await writer.drain()
                assert await reader.read() == b""
            writer.close()
            await writer.wait_closed()

        tasks.append(asyncio.create_task(serve()))

    monkeypatch.setattr(desktop, "command", launch)

    async def run():
        bridge = desktop.MacBridge()
        try:
            if metadata and metadata["protocol"] != 1:
                with pytest.raises(ShowrunError, match="incompatible") as caught:
                    await bridge.start()
                assert caught.value.code == "desktop_protocol_unsupported"
                assert caught.value.public()["diagnostics"]["companion"] == desktop.companion_info(
                    {"companion": metadata})
                assert "desktop-status" in caught.value.remedy
                assert not operations
            else:
                result = await bridge.start()
                assert result["companion"] == (metadata or {
                    "version": "unknown", "protocol": "unknown", "capabilities": "unknown"})
                assert operations == ["permissions"]
        finally:
            await bridge.close()
            await asyncio.gather(*tasks)

    asyncio.run(run())


@pytest.mark.parametrize("code", [
    "desktop_window_not_found", "desktop_window_ambiguous",
    "desktop_ax_window_not_found", "desktop_ax_window_ambiguous",
])
def test_bridge_window_error_transport_is_bounded_and_sanitized(code):
    payload = {"error": code, "message": "PRIVATE", "diagnostics": {
        "candidate_count": 12, "match_count": 0, "ax_match_count": 0, "other_app": "PRIVATE",
        "candidates": [{"window_id": 7, "width": 640, "height": 360,
                        "title_matches": False, "title": "PRIVATE"}] * 12}}

    async def run():
        bridge = desktop.MacBridge()
        bridge.reader = asyncio.StreamReader()
        bridge.reader.feed_data((json.dumps(payload) + "\n").encode())

        async def drain():
            pass
        bridge.writer = SimpleNamespace(is_closing=lambda: False, write=lambda data: None, drain=drain)
        with pytest.raises(ShowrunError) as caught:
            await bridge.call("bind", bundle_id="test.app")
        public = caught.value.public()
        assert public["code"] == code
        assert len(public["diagnostics"]["candidates"]) == 8
        assert public["diagnostics"]["candidate_count"] == 12
        assert "PRIVATE" not in json.dumps(public)

    asyncio.run(run())


def test_desktop_status_exposes_metadata_separately_from_readiness(monkeypatch):
    closed = []

    class StatusBridge:
        async def start(self):
            return {"screen_recording": True, "accessibility": False,
                    "companion": {"version": "source-unreleased", "protocol": 1,
                                  "capabilities": ["permissions"]}}

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(desktop, "MacBridge", StatusBridge)
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    result = Showrun.desktop_status()
    assert result["ready"] is False and result["model_calls"] == 0
    assert result["companion"]["protocol"] == 1
    assert result["companion"]["capabilities"] == ["permissions"]
    assert closed == [True]


@pytest.mark.parametrize("argv", [["-h"], ["record", "-h"]])
def test_terse_help_points_to_focused_and_full_guides(argv, capsys):
    with pytest.raises(SystemExit) as exit:
        main(argv)
    assert exit.value.code == 0
    text = capsys.readouterr().out
    assert "showrun --help" in text and "--help" in text
    assert len(text) < len(Showrun.skill()) / 4


def test_help_pin_and_metadata_do_not_claim_unpublished_download():
    from amplifier_smart_tool_showrun.desktop_install import RELEASE

    assert RELEASE in Showrun.skill("record") and RELEASE in Showrun.skill("prepare-desktop")
    assert "No terminal mode in the pinned" not in Showrun.skill("record")
    assert "unknown" in Showrun.skill("desktop-status")
    assert desktop.COMPANION_VERSION in Showrun.skill("desktop-status")