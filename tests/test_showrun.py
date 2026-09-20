import asyncio
import copy
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path

import pytest

from amplifier_smart_tool_showrun import Showrun, ShowrunError
from amplifier_smart_tool_showrun.browser import Browser
from amplifier_smart_tool_showrun.cli import main
from amplifier_smart_tool_showrun.lib import CAPABILITIES
from amplifier_smart_tool_showrun.store import Store

MODEL = {"provider": "openai", "model": "explicit-test-model"}
HTML = b"""<!doctype html><html><body style="margin:0;background:white">
<h1 id="title">Prepared deck</h1><p id="body">Title visible</p>
<button aria-label="Next slide" onclick="next()">Next</button>
<button aria-label="Comment" onclick="fetch('/mutation',{method:'POST'})">Comment</button>
<script>let i=0; const slides=['Prepared deck','Middle destination','Final result'];
function next(){i=Math.min(i+1,2);document.querySelector('h1').textContent=slides[i];}
document.body.onkeydown=e=>{if(e.key==='End'){i=2;document.querySelector('h1').textContent=slides[i];}};
</script></body></html>"""


@pytest.fixture
def target_server():
    class Handler(BaseHTTPRequestHandler):
        mutations = 0
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(HTML)
        def do_POST(self):
            type(self).mutations += 1
            self.send_response(204)
            self.end_headers()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", Handler
    server.shutdown()
    server.server_close()
    thread.join()


def request(url="http://127.0.0.1:8080", identity="test-take"):
    return {
        "request_id": identity, "target": {"kind": "url", "url": url, "origins": [url]},
        "starting_state": "Prepared deck",
        "steps": [
            {"id": "title", "instruction": "Show title", "visible_text": "Prepared deck"},
            {"id": "final", "instruction": "Navigate final slide", "visible_text": "Final result"},
        ],
        "authority": {"navigation_only": True, "disclose_dom": True, "max_seconds": 25,
                      "max_model_calls": 4, "max_actions": 4},
    }


class ScriptedNavigator:
    calls = 0
    delay = 0
    def __init__(self, config):
        pass
    async def start(self):
        pass
    async def decide(self, step, observation, context, remaining):
        type(self).calls += 1
        await asyncio.sleep(self.delay)
        for frame in observation["frames"]:
            for control in frame["controls"]:
                if control["label"] == "Next slide":
                    return {"action": "click", "ref": control["ref"]}
        return {"action": "fail"}
    async def close(self):
        pass


@pytest.fixture
def scripted(monkeypatch):
    from amplifier_smart_tool_showrun import agent
    ScriptedNavigator.calls = 0
    ScriptedNavigator.delay = 0
    monkeypatch.setattr(agent, "Navigator", ScriptedNavigator)
    return ScriptedNavigator


def test_manifest_skill_and_cli_without_runtime(tmp_path, capsys):
    code = """import sys
from amplifier_smart_tool_showrun import Showrun
assert Showrun.manifest()['name']=='showrun'
from amplifier_smart_tool_showrun.lib import CAPABILITIES
for capability in [None, *CAPABILITIES]:
    assert '<skill_content' in Showrun.skill(capability)
assert 'amplifier_agent_lib' not in sys.modules
assert 'playwright' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True, cwd=tmp_path)
    api = Showrun(tmp_path)
    assert api.validate(request())["request"]["capture"] == {"width": 1920, "height": 1080}
    assert not (tmp_path / "takes.sqlite3").exists()
    with pytest.raises(ShowrunError):
        Showrun(tmp_path / "nonexistent").status("missing")
    assert not (tmp_path / "nonexistent").exists()
    for capability in [None, *CAPABILITIES]:
        assert "<skill_content" in Showrun.skill(capability)
        assert main(([capability] if capability else []) + ["--help"]) == 0
        assert "<skill_content" in capsys.readouterr().out
    assert main(["manifest"]) == 0
    assert json.loads(capsys.readouterr().out)["name"] == "showrun"


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_capability_help_is_focused_and_matches_library(capability, capsys):
    guides = json.loads(files("amplifier_smart_tool_showrun").joinpath("capabilities.json").read_text())
    guide = guides[capability]
    text = Showrun.skill(capability)
    assert f"# showrun {capability}\n" in text
    for section in ("When to use", "Execution and prerequisites", "Arguments", "Example",
                    "Result", "Failures and recovery", "Further guidance"):
        assert f"## {section}\n" in text
    for field in ("example", "guidance", "result", "failures"):
        assert guide[field] in text
    for name, detail in guide["arguments"].items():
        assert f"`{name}` — {detail}" in text
    assert len(text) < len(Showrun.skill()) / 2
    assert "## Install and prerequisites" not in text
    assert '"arguments":' not in text
    for other, other_guide in guides.items():
        if other not in {capability, "manifest"}:
            assert other_guide["example"] not in text
    assert main([capability, "--help"]) == 0
    assert capsys.readouterr().out == text + "\n"


@pytest.mark.parametrize("change", [
    lambda r: r.update(unknown=True),
    lambda r: r["authority"].update(max_seconds=181),
    lambda r: r["authority"].update(max_model_calls=13),
    lambda r: r["authority"].update(max_actions=31),
    lambda r: r["authority"].update(disclose_dom=False),
    lambda r: r.update(capture={"width": 1919}),
    lambda r: r["steps"][0].update(hold_seconds=2),
    lambda r: r["steps"][1].update(id="title"),
    lambda r: r["target"].update(url="file:///etc/passwd"),
])
def test_validation_fails_closed(tmp_path, change):
    value = request()
    change(value)
    with pytest.raises(ShowrunError):
        Showrun(tmp_path, MODEL).validate(value)


def test_atomic_reservation_and_conflicts(tmp_path):
    value = request()
    store = Store(tmp_path)
    receipt = {"request_id": "test-take", "status": "running"}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.reserve(value, MODEL, receipt), range(8)))
    assert sum(r is None for r in results) == 1
    changed = copy.deepcopy(value)
    changed["steps"][0]["instruction"] = "Different intent"
    with pytest.raises(ShowrunError, match="different effective"):
        store.reserve(changed, MODEL, receipt)
    # A dead owner is uncertain, never permission to replay.
    with store.connect() as db:
        db.execute("UPDATE takes SET pid=2147483647")
    assert store.status("test-take")["status"] == "uncertain"
    assert store.reserve(value, MODEL, receipt) is not None


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled", "uncertain"])
@pytest.mark.parametrize("prior_cancel", [False, True])
def test_store_cancel_terminal_is_noop(tmp_path, status, prior_cancel):
    store = Store(tmp_path)
    receipt = {"request_id": "test-take", "status": "running"}
    store.reserve(request(), MODEL, receipt)
    if prior_cancel:
        store.cancel("test-take")
    receipt.update(status=status, cleanup="failed_or_uncertain")
    store.save(receipt)
    before = (tmp_path / "test-take/receipt.json").read_bytes()
    with store.connect() as db:
        row_before = db.execute("SELECT * FROM takes").fetchone()
    result = store.cancel("test-take")
    assert result["status"] == "already_terminal"
    assert result["operation_status"] == status
    assert result["cancel_requested"] is prior_cancel
    assert (tmp_path / "test-take/receipt.json").read_bytes() == before
    with store.connect() as db:
        assert db.execute("SELECT * FROM takes").fetchone() == row_before


def test_store_cancel_missing_remains_error(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(ShowrunError) as failure:
        store.cancel("missing")
    assert failure.value.code == "not_found"
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM takes").fetchone()[0] == 0


def test_store_cancel_waits_for_terminal_save_transaction(tmp_path, monkeypatch):
    """Hold the real save after its UPDATE; cancellation must read the committed terminal receipt."""
    import sqlite3

    store = Store(tmp_path)
    receipt = {"request_id": "test-take", "status": "running"}
    store.reserve(request(), MODEL, receipt)
    updated, release_save, cancel_lock_attempted = threading.Event(), threading.Event(), threading.Event()
    class GatedConnection(sqlite3.Connection):
        def execute(self, statement, parameters=()):
            if statement == "BEGIN IMMEDIATE":
                cancel_lock_attempted.set()
            result = super().execute(statement, parameters)
            if statement.startswith("UPDATE takes SET receipt="):
                updated.set()
                assert release_save.wait(5)
            return result

    def traced_connect():
        return sqlite3.connect(store.db_path, timeout=5, factory=GatedConnection)

    monkeypatch.setattr(store, "connect", traced_connect)
    with ThreadPoolExecutor(max_workers=2) as pool:
        saving = pool.submit(store.save, {**receipt, "status": "succeeded"})
        try:
            assert updated.wait(5)
            cancelling = pool.submit(store.cancel, "test-take")
            assert cancel_lock_attempted.wait(5)
        finally:
            release_save.set()
        saving.result(timeout=5)
        result = cancelling.result(timeout=5)
    assert result["status"] == "already_terminal"
    assert result["operation_status"] == "succeeded"
    assert store.cancelled("test-take") is False


def test_store_cancel_before_terminal_save_is_only_acknowledgment(tmp_path):
    store = Store(tmp_path)
    receipt = {"request_id": "test-take", "status": "running"}
    store.reserve(request(), MODEL, receipt)
    response = store.cancel("test-take")
    assert response["status"] == "cancellation_requested"
    assert "work may finish" in response["notice"]
    # A worker completing concurrently need not have observed the flag.
    # Preserve its actual outcome, never fabricate that cancellation stopped it.
    store.save({**receipt, "status": "succeeded", "cleanup": "verified"})
    status = store.status("test-take")
    assert status["status"] == "succeeded"
    assert status["cancel_requested"] is True
    assert store.cancel("test-take")["status"] == "already_terminal"


def test_preexisting_output_is_not_overwritten(tmp_path):
    folder = tmp_path / "test-take"
    folder.mkdir()
    sentinel = folder / "receipt.json"
    sentinel.write_text("unrelated")
    with pytest.raises(ShowrunError, match="already exists"):
        Showrun(tmp_path, MODEL).record(request())
    assert sentinel.read_text() == "unrelated"


def test_missing_provider_retained_no_retry(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    api = Showrun(tmp_path, MODEL)
    first = api.record(request())
    assert first["status"] == "failed"
    assert first["error"]["code"] == "provider_missing"
    assert all(row["status"] == "unattempted" for row in first["steps"])
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    assert api.record(request())["status"] == "failed"


def test_real_continuous_capture_retry_and_inspection(tmp_path, target_server, scripted):
    url, handler = target_server
    value = request(url)
    value["capture"] = {"width": 640, "height": 480}
    scripted.delay = .6
    api = Showrun(tmp_path, MODEL)
    result = api.record(value)
    assert result["status"] == "succeeded", result
    assert result["cleanup"] == "verified"
    assert result["resources"]["dashboard"] == "caller"
    assert result["media"]["width"] == 640
    assert result["media"]["height"] == 480
    assert result["media"]["decoded"]
    assert result["media"]["duration_seconds"] >= 7.2  # deliberate model waits were not cut
    assert all(row["hold"]["end_seconds"] - row["hold"]["start_seconds"] >= 3 for row in result["steps"])
    assert result["steps"][0]["first_interaction_seconds"] is None
    assert result["steps"][1]["first_interaction_seconds"] is not None
    assert scripted.calls == 2
    assert api.record(value)["take_id"] == result["take_id"]
    assert scripted.calls == 2
    assert handler.mutations == 0
    assert api.inspect("test-take")["inspection"]["sha256"] == result["media"]["sha256"]
    # Caller-owned service remains reachable.
    import urllib.request
    assert urllib.request.urlopen(url).status == 200
    changed = copy.deepcopy(value)
    changed["capture"]["height"] = 640
    with pytest.raises(ShowrunError):
        api.record(changed)
    media = tmp_path / "test-take" / result["media"]["path"]
    media.write_bytes(b"not-video")
    with pytest.raises(ShowrunError):
        api.inspect("test-take")


def test_missing_middle_stops_later_steps(tmp_path, target_server, scripted):
    value = request(target_server[0])
    value["capture"] = {"width": 640, "height": 480}
    value["steps"].insert(1, {"id": "missing", "instruction": "Show nonexistent slide",
                              "visible_text": "No such destination"})
    value["authority"]["max_model_calls"] = 2
    result = Showrun(tmp_path, MODEL).record(value)
    assert result["status"] == "failed", result
    assert result["partial"]
    assert [r["status"] for r in result["steps"]] == ["completed", "failed", "unattempted"]
    assert result["error"]["code"] == "model_limit"
    assert result["media"]["decoded"]
    assert result["cleanup"] == "verified"


def test_external_cancel_during_reasoning(tmp_path, target_server, scripted):
    value = request(target_server[0])
    value["capture"] = {"width": 640, "height": 480}
    scripted.delay = 30
    api = Showrun(tmp_path, MODEL)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(api.record, value)
        import time
        deadline = time.monotonic() + 15
        while scripted.calls == 0:
            assert time.monotonic() < deadline
            time.sleep(.1)
        assert Showrun(tmp_path).cancel("test-take")["status"] == "cancellation_requested"
        result = future.result(timeout=30)
    assert result["status"] == "cancelled", result
    assert result["cleanup"] == "verified"
    assert result["media"]["decoded"]
    assert api.record(value)["status"] == "cancelled"


def test_browser_ref_scope_and_sandbox(tmp_path, target_server):
    from amplifier_smart_tool_showrun.target import Target
    async def run():
        config = request(target_server[0])["target"]
        target = Target(config, tmp_path)
        await target.start()
        browser = Browser(target, tmp_path, {"width": 640, "height": 480})
        try:
            await browser.start(target.url)
            observed = await browser.observe()
            assert browser.visible(observed, "Prepared deck")
            assert [c["label"] for f in observed["frames"] for c in f["controls"]] == ["Next slide"]
            old = observed["frames"][0]["controls"][0]["ref"]
            await browser.observe()
            with pytest.raises(ShowrunError):
                await browser.act({"action": "click", "ref": old})
            with pytest.raises(ShowrunError):
                await browser.act({"action": "type", "text": "unauthorized"})
            with pytest.raises(ShowrunError):
                await browser.act({"action": "key", "frame": 0, "key": "Control+V"})
            # Test-owned fixture insertion only; production code never rewrites target DOM.
            await browser.page.set_content(
                '<iframe sandbox="allow-scripts" style="width:500px;height:250px" '
                'srcdoc="<h1>Sandbox heading</h1><p hidden>Hidden impostor</p>"></iframe>')
            await browser.page.frame_locator("iframe").locator("h1").wait_for()
            observed = await browser.observe()
            assert browser.visible(observed, "Sandbox heading")
            assert not browser.visible(observed, "Hidden impostor")
            await browser.page.set_content('<input type="password" value="synthetic-only">')
            with pytest.raises(ShowrunError):
                await browser.observe()
            assert browser.restricted
        finally:
            await browser.close()
    asyncio.run(run())


def test_target_helper_is_packaged():
    assert files("amplifier_smart_tool_showrun").joinpath("stories_helper.py").is_file()
    assert files("amplifier_smart_tool_showrun").joinpath("SMART_TOOL.md").is_file()
    assert files("amplifier_smart_tool_showrun").joinpath("capabilities.json").is_file()


@pytest.mark.parametrize("cancel", [False, True])
def test_installed_stories_managed_dashboard_and_cleanup(tmp_path, scripted, monkeypatch, cancel):
    python = Path(os.environ.get("SHOWRUN_TEST_STORIES_PYTHON",
                  Path.home() / ".local/share/uv/tools/amplifier-smart-tool-stories/bin/python"))
    if not python.is_file():
        pytest.skip("Set SHOWRUN_TEST_STORIES_PYTHON to the installed Stories v0.1.0 interpreter.")
    storage = tmp_path / "stories-fixture"
    created = Showrun.prepare_fixture({
        "title": "Prepared deck", "html": '<html><head><style>section{padding:50px}</style></head><body>'
        '<section class="slide"><h1>Prepared deck</h1></section>'
        '<section class="slide"><h1>Final result</h1></section></body></html>',
        "sources": [], "purpose": "Isolated test", "audience": "Test",
    }, storage, python)
    value = request()
    value["target"] = created["target"]
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-do-not-forward")
    api = Showrun(tmp_path / "takes", MODEL)
    if cancel:
        scripted.delay = 30
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(api.record, value)
            import time
            deadline = time.monotonic() + 18
            while scripted.calls == 0:
                assert time.monotonic() < deadline
                time.sleep(.1)
            api.cancel(value["request_id"])
            result = future.result(timeout=30)
    else:
        result = api.record(value)
    assert result["status"] == ("cancelled" if cancel else "succeeded"), result
    assert result["resources"]["dashboard"] == "showrun"
    assert result["resources"]["startup"] == "ready"
    assert result["resources"]["cleanup"] == "verified_stopped"
    assert result["media"]["width"] == 1920
    assert result["media"]["height"] == 1080
    assert result["media"]["sample_aspect_ratio"] == "1:1"
    assert result["media"]["display_aspect_ratio"] == "16:9"
    owner = json.loads((tmp_path / "takes/test-take/owned-dashboard.json").read_text())
    target_state = json.loads((storage / (owner["service_id"] + ".json")).read_text())
    assert target_state["status"] == "stopped"
    from amplifier_smart_tool_showrun.stories_helper import exited
    assert exited(target_state["pid"])
    assert "synthetic-do-not-forward" not in json.dumps(result)
    assert target_state["token"] not in json.dumps(result)
    assert api.record(value)["status"] == result["status"]
    assert len(list(storage.glob("viewer_*.json"))) == 1


def test_explicit_missing_destination_is_failed_not_uncertain(tmp_path, target_server, scripted, monkeypatch):
    async def cannot_find(self, *args):
        return {"action": "fail"}
    monkeypatch.setattr(scripted, "decide", cannot_find)
    value = request(target_server[0])
    value["capture"] = {"width": 640, "height": 480}
    value["steps"].insert(1, {"id": "missing", "instruction": "Find nonexistent slide",
                              "visible_text": "Nonexistent slide"})
    result = Showrun(tmp_path, MODEL).record(value)
    assert result["error"]["code"] == "destination_not_found"
    assert [s["status"] for s in result["steps"]] == ["completed", "failed", "unattempted"]
    assert result["usage"]["actions"] == 0


@pytest.mark.parametrize("failure", ["action_limit", "elapsed_limit", "finalization", "cleanup"])
def test_limits_and_failed_cleanup_cannot_succeed(tmp_path, target_server, scripted, monkeypatch, failure):
    from amplifier_smart_tool_showrun.capture import Capture
    value = request(target_server[0])
    value["capture"] = {"width": 640, "height": 480}
    if failure == "action_limit":
        value["authority"]["max_actions"] = 1
    elif failure == "elapsed_limit":
        value["authority"]["max_seconds"] = 7
        scripted.delay = 10
    elif failure == "finalization":
        async def fail_finish(self):
            raise ShowrunError("capture_failed", "Injected finalization failure.")
        monkeypatch.setattr(Capture, "finish", fail_finish)
    else:
        close = Browser.close
        async def fail_close(self):
            await close(self)
            raise ShowrunError("cleanup_failed", "Injected cleanup confirmation failure.")
        monkeypatch.setattr(Browser, "close", fail_close)
    result = Showrun(tmp_path, MODEL).record(value)
    assert result["status"] == "failed", result
    assert result["partial"]
    if failure in {"action_limit", "elapsed_limit"}:
        assert result["error"]["code"] == failure
    if failure == "finalization":
        assert result["media"] is None
        assert "capture_error" in result
    if failure == "cleanup":
        assert result["cleanup"] == "failed_or_uncertain"


def test_agent_session_restricted_and_action_contract(monkeypatch):
    """Real Agent session setup, mocked complete only: no provider inference/network."""
    from types import SimpleNamespace

    from amplifier_smart_tool_showrun.agent import Navigator, _location
    if not (_location()[1] / "showrun-ready.json").is_file():
        pytest.skip("Explicit showrun prepare-runtime has not been run in this installation.")
    monkeypatch.setenv("SHOWRUN_TEST_KEY", "synthetic-no-inference")
    async def run():
        for provider in ("openai", "anthropic"):
            navigator = Navigator({"provider": provider, "model": "explicit-test-model",
                                   "credential_env": "SHOWRUN_TEST_KEY"})
            try:
                await navigator.start()
                assert navigator.session.coordinator.get("tools") == {}
                assert navigator.bundle.mount_plan["hooks"] == []
                assert navigator.bundle.bundle.context == {}
                assert navigator.bundle.bundle.instruction == ""
                assert navigator.bundle.resolver._activator is None
                assert navigator.provider._retry_config.max_retries == 0
                async def complete(request):
                    assert request.model == "explicit-test-model"
                    assert request.max_output_tokens == 2048
                    assert request.stream is False
                    assert request.metadata == {"stream": False}
                    assert not request.tools
                    assert len(request.messages) == 2
                    return SimpleNamespace(tool_calls=None, content='{"action":"wait"}')
                monkeypatch.setattr(navigator.provider, "complete", complete)
                assert await navigator.decide({}, {"frames": []}, "test context", 2) == {"action": "wait"}
            finally:
                await navigator.close()
    asyncio.run(run())


@pytest.mark.parametrize("extra", [
    {"response_tokens": 511}, {"response_tokens": 4097}, {"response_tokens": True},
    {"response_tokens": 2048.0}, {"reasoning_effort": "none"}, {"reasoning_effort": "max"},
    {"reasoning_effort": []}, {"temperature": .2},
])
def test_bounded_model_configuration_rejected_without_runtime(tmp_path, extra):
    with pytest.raises(ShowrunError):
        Showrun(tmp_path, {**MODEL, **extra}).validate(request())


def test_reasoning_provider_configuration_scope(tmp_path):
    with pytest.raises(ShowrunError):
        Showrun(tmp_path, {"provider": "anthropic", "model": "test", "reasoning_effort": "low"}).validate(request())
    for tokens in (512, 2048, 4096):
        assert Showrun(tmp_path, {**MODEL, "response_tokens": tokens,
                                 "reasoning_effort": "low"}).validate(request())["status"] == "valid"


@pytest.mark.parametrize("reply", ["completed", "incomplete", "truncated_tool", "429", "400"])
@pytest.mark.parametrize("tokens", [2048, 4096])
def test_mounted_openai_wire_budget_no_retry_or_stream(monkeypatch, reply, tokens):
    """Use the actual mounted provider/SDK with HTTP mocked below dispatch. No live calls."""
    import httpx
    from openai import AsyncOpenAI

    from amplifier_smart_tool_showrun.agent import Navigator, _location

    if not (_location()[1] / "showrun-ready.json").is_file():
        pytest.skip("Explicit showrun prepare-runtime has not been run in this installation.")
    monkeypatch.setenv("SHOWRUN_TEST_KEY", "synthetic-no-inference")
    sent = []

    def transport(req):
        payload = json.loads(req.content)
        sent.append(payload)
        assert req.url.path == "/v1/responses"
        assert payload["model"] == "gpt-6-astra"
        assert payload["max_output_tokens"] == tokens
        assert payload["reasoning"]["effort"] == "low"
        assert not payload.get("stream") and not payload.get("tools") and not payload.get("background")
        if reply in {"400", "429"}:
            return httpx.Response(int(reply), json={"error": {
                "message": "Synthetic unsupported parameter or rate limit",
                "type": "invalid_request_error" if reply == "400" else "rate_limit_error",
            }})
        output = [{"type": "message", "id": "msg-test", "role": "assistant", "status": "completed",
                   "content": [{"type": "output_text", "text": '{"action":"wait"}', "annotations": []}]}]
        if reply == "incomplete":
            output = []
        elif reply == "truncated_tool":
            output = [{"type": "function_call", "id": "fc-test", "call_id": "call-test",
                       "name": "unauthorized", "arguments": "{", "status": "incomplete"}]
        return httpx.Response(200, json={
            "id": "resp-test", "object": "response", "created_at": 1,
            "model": "gpt-6-astra", "status": "completed" if reply == "completed" else "incomplete",
            "output": output, "incomplete_details": None if reply == "completed" else {"reason": "max_output_tokens"},
            "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        })

    async def run():
        config = {"provider": "openai", "model": "gpt-6-astra", "credential_env": "SHOWRUN_TEST_KEY"}
        if tokens != 2048:
            config.update(response_tokens=tokens, reasoning_effort="low")
        navigator = Navigator(config)
        client = AsyncOpenAI(api_key="synthetic-no-inference", max_retries=0,
                             http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))
        try:
            await navigator.start()
            assert navigator.provider.reasoning_effort == "low"
            assert navigator.provider.use_streaming is False
            assert navigator.provider._retry_config.max_retries == 0
            assert navigator.provider.client.max_retries == 0
            assert navigator.provider.max_output_tokens == tokens
            await navigator.provider.client.close()
            navigator.provider._client = client
            # Token-count measurement is not inference; keep this test entirely offline.
            async def count(params):
                return None
            monkeypatch.setattr(navigator.provider, "_guard_assembled_params_with_provider_count", count)
            if reply == "completed":
                assert await navigator.decide({}, {"frames": []}, "test", 2) == {"action": "wait"}
            else:
                with pytest.raises(Exception) as failure:
                    await navigator.decide({}, {"frames": []}, "test", 2)
                if reply in {"incomplete", "truncated_tool"}:
                    assert "continuation and token escalation are forbidden" in str(failure.value)
            assert len(sent) == 1
            assert sent[0]["model"] == "gpt-6-astra"
            assert sent[0]["max_output_tokens"] == tokens
            assert sent[0]["reasoning"]["effort"] == "low"
            assert not sent[0].get("stream")
            assert navigator._dispatch_available is False
            # The gate remains closed even if provider code attempts another create.
            with pytest.raises(ShowrunError, match="continuation or retry"):
                await navigator.provider._create_response(sent[0])
            assert len(sent) == 1
        finally:
            await client.close()
            await navigator.close()
    asyncio.run(run())


def test_explicit_reasoning_on_non_reasoning_model_fails_before_inference(monkeypatch):
    from amplifier_smart_tool_showrun.agent import Navigator, _location

    if not (_location()[1] / "showrun-ready.json").is_file():
        pytest.skip("Explicit showrun prepare-runtime has not been run in this installation.")
    monkeypatch.setenv("SHOWRUN_TEST_KEY", "synthetic-no-inference")
    async def run():
        navigator = Navigator({"provider": "openai", "model": "gpt-4.1",
                               "reasoning_effort": "low", "credential_env": "SHOWRUN_TEST_KEY"})
        try:
            with pytest.raises(ShowrunError, match="does not support reasoning_effort"):
                await navigator.start()
        finally:
            await navigator.close()
    asyncio.run(run())