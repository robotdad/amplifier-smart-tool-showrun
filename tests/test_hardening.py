"""Offline mechanics only: installed public Stories API, real browser/media, mocked inference."""

import asyncio
import copy
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_showrun import MODEL, request

from amplifier_smart_tool_showrun import Showrun, ShowrunError
from amplifier_smart_tool_showrun import stories_helper as ownership
from amplifier_smart_tool_showrun.browser import Browser
from amplifier_smart_tool_showrun.cli import main
from amplifier_smart_tool_showrun.fixture import validate_fixture
from amplifier_smart_tool_showrun.schema import validate
from amplifier_smart_tool_showrun.store import Store, canonical

pytest_plugins = ["test_showrun"]

PRESENTATION = {
    "title": "Prepared deck",
    "html": '<html><body><section class="slide"><h1>Prepared deck</h1></section>'
            '<section class="slide"><h1>Final result</h1></section></body></html>',
    "sources": [{"id": "source", "content": "Supplied fixture", "sha256": "retained-extra",
                 "name": "Fixture"}],
    "purpose": "Isolated deterministic test", "audience": "Test",
}


@pytest.fixture
def stories_python():
    python = Path(os.environ.get("SHOWRUN_TEST_STORIES_PYTHON",
                  Path.home() / ".local/share/uv/tools/amplifier-smart-tool-stories/bin/python"))
    if not python.is_file():
        pytest.skip("Set SHOWRUN_TEST_STORIES_PYTHON to installed Stories v0.1.0.")
    return str(python)


def test_fixture_cli_public_import_exact_validation(tmp_path, stories_python, capsys):
    source = tmp_path / "supplied.json"
    source.write_text(json.dumps(PRESENTATION))
    before = source.read_bytes()
    assert main(["prepare-fixture", str(source), "--destination", str(tmp_path / "fixture"),
                 "--python", stories_python]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "prepared"
    assert source.read_bytes() == before
    target = result["target"]
    assert asyncio.run(validate_fixture(target))["status"] == "valid"
    assert asyncio.run(validate_fixture(target))["status"] == "valid"
    assert len(target["fixture_sha256"]) == 64
    with pytest.raises(ShowrunError, match="empty"):
        Showrun.prepare_fixture(PRESENTATION, tmp_path / "fixture", stories_python)
    assert not list((tmp_path / "fixture").glob("viewer_*.json"))


@pytest.mark.parametrize("fault", ["missing_marker", "changed_content", "additional_story", "wrong_revision", "wrong_hash",
                                       "symlink_store", "symlink_inside"])
def test_fixture_rejected_before_model_or_launch(tmp_path, stories_python, monkeypatch, fault):
    from amplifier_smart_tool_showrun import agent
    from amplifier_smart_tool_showrun import target as targets
    target = Showrun.prepare_fixture(PRESENTATION, tmp_path / "fixture", stories_python)["target"]
    if fault == "missing_marker":
        (tmp_path / "fixture/showrun-fixture.json").unlink()
    elif fault == "changed_content":
        code = """import sys
from amplifier_smart_tool_stories import Stories
api=Stories(sys.argv[1],model_env=False,execution='queued')
api.accept_revision(sys.argv[2],sys.argv[3],request_id='changed-acceptance')
"""
        subprocess.run([stories_python, "-I", "-c", code, target["storage"],
                        target["story_id"], target["revision_id"]], check=True)
    elif fault == "additional_story":
        # Change retained public state through the installed public API, not the DB.
        code = """import sys
from amplifier_smart_tool_stories import Stories
api=Stories(sys.argv[1],model_env=False,execution='queued')
api.create_story(title='Extra',html='<html><body><section class="slide"><h1>Extra</h1></section></body></html>',
                 request_id='changed')
"""
        subprocess.run([stories_python, "-I", "-c", code, target["storage"]], check=True)
    elif fault == "wrong_revision":
        target["revision_id"] = "rev_wrong"
    elif fault == "wrong_hash":
        target["fixture_sha256"] = "0" * 64
    elif fault == "symlink_store":
        (tmp_path / "link").symlink_to(tmp_path / "fixture", target_is_directory=True)
        target["storage"] = str(tmp_path / "link")
    else:
        (tmp_path / "fixture/linked").symlink_to(tmp_path / "supplied")

    def forbidden(*a, **kw):
        raise AssertionError("Target/model must not be acquired")
    monkeypatch.setattr(agent, "Navigator", forbidden)
    monkeypatch.setattr(targets.Target, "start", forbidden)
    value = request()
    value["target"] = target
    result = Showrun(tmp_path / "takes", MODEL).record(value)
    assert result["error"]["code"] == "fixture_invalid", result
    assert result["usage"] == {"actions": 0, "model_calls": 0}
    assert result["resources"] == {}
    assert not list((tmp_path / "fixture").glob("viewer_*.json"))


def test_prepare_refuses_live_symlink_and_existing(tmp_path, stories_python, monkeypatch):
    live = tmp_path / "live"
    monkeypatch.setenv("STORIES_STORE", str(live))
    with pytest.raises(ShowrunError):
        Showrun.prepare_fixture(PRESENTATION, live, stories_python)
    assert not live.exists()
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "sentinel").write_bytes(b"unchanged")
    link = tmp_path / "linked"
    link.symlink_to(existing, target_is_directory=True)
    for path in (existing, link, link / "new"):
        with pytest.raises(ShowrunError):
            Showrun.prepare_fixture(PRESENTATION, path, stories_python)
    assert (existing / "sentinel").read_bytes() == b"unchanged"
    assert list(existing.iterdir()) == [existing / "sentinel"]


def test_fixture_helper_no_reply_is_actionable(tmp_path):
    with pytest.raises(ShowrunError) as failure:
        Showrun.prepare_fixture(PRESENTATION, tmp_path / "fixture", "/usr/bin/true")
    assert failure.value.code == "fixture_invalid"
    assert not (tmp_path / "fixture/showrun-fixture.json").exists()


def test_legacy_receipt_retry_and_readonly_migration(tmp_path, monkeypatch):
    value = request()
    value["target"] = {"kind": "stories", "python": "/absent/python", "storage": "/absent/fixture",
                       "story_id": "story_old", "revision_id": "rev_old"}
    effective = validate(value, MODEL)
    receipt = {"schema_version": 1, "request_id": value["request_id"], "status": "succeeded",
               "media": None, "steps": [], "resources": {}, "old_field": "unchanged"}
    fingerprint = hashlib.sha256(canonical({"request": effective, "model": MODEL}).encode()).hexdigest()
    with sqlite3.connect(tmp_path / "takes.sqlite3") as db:
        db.execute("CREATE TABLE takes(id TEXT PRIMARY KEY,fingerprint TEXT,receipt TEXT,cancel INTEGER,pid INTEGER)")
        db.execute("INSERT INTO takes VALUES(?,?,?,0,?)",
                   (value["request_id"], fingerprint, canonical(receipt), os.getpid()))
    folder = tmp_path / value["request_id"]
    folder.mkdir()
    (folder / "receipt.json").write_text(canonical(receipt))
    before = (folder / "receipt.json").read_bytes()
    # Readonly status must not need migration. It does not change terminal evidence.
    assert Showrun(tmp_path).status(value["request_id"])["old_field"] == "unchanged"
    from amplifier_smart_tool_showrun import agent
    monkeypatch.setattr(agent, "Navigator", lambda *a: pytest.fail("Legacy take replayed"))
    assert Showrun(tmp_path, MODEL).record(value)["status"] == "succeeded"
    assert (folder / "receipt.json").read_bytes() == before
    with sqlite3.connect(tmp_path / "takes.sqlite3") as db:
        assert db.execute("SELECT receipt FROM takes").fetchone()[0] == canonical(receipt)
        assert db.execute("SELECT owner FROM takes").fetchone()[0] is None
        db.execute("UPDATE takes SET receipt=?", (canonical({**receipt, "status": "running"}),))
    assert Showrun(tmp_path).status(value["request_id"])["status"] == "uncertain"
    changed = copy.deepcopy(value)
    changed["steps"][0]["instruction"] = "Refined intent"
    with pytest.raises(ShowrunError, match="different effective"):
        Showrun(tmp_path, MODEL).record(changed)
    # Missing fixture authority may be read for old keys but never run under a new one.
    value["request_id"] = "new-legacy-input"
    with pytest.raises(ShowrunError) as failure:
        Showrun(tmp_path, MODEL).record(value)
    assert failure.value.code == "fixture_invalid"
    assert not (tmp_path / value["request_id"]).exists()
    with sqlite3.connect(tmp_path / "takes.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM takes WHERE id=?", (value["request_id"],)).fetchone()[0] == 0


def test_corrected_new_fixture_shape_can_use_same_id(tmp_path, stories_python, scripted):
    target = Showrun.prepare_fixture(PRESENTATION, tmp_path / "fixture", stories_python)["target"]
    value = request()
    value["target"] = {k: v for k, v in target.items() if k != "fixture_sha256"}
    api = Showrun(tmp_path / "takes", MODEL)
    with pytest.raises(ShowrunError) as failure:
        api.record(value)
    assert failure.value.code == "fixture_invalid"
    assert not (api.storage / value["request_id"]).exists()
    with api._store().connect() as db:
        assert db.execute("SELECT count(*) FROM takes").fetchone()[0] == 0
    value["target"] = target
    value["capture"] = {"width": 1280, "height": 720}
    result = api.record(value)
    assert result["status"] == "succeeded", result
    assert (api.storage / value["request_id"] / "receipt.json").is_file()


@pytest.mark.parametrize("fault", ["boot", "ticks", "missing", "permission"])
def test_process_identity_uncertainty_no_signals(tmp_path, monkeypatch, fault):
    store = Store(tmp_path)
    store.reserve(request(), MODEL, {"request_id": "test-take", "status": "running",
                                    "steps": [{"status": "in_progress"}]})
    assert store.status("test-take")["status"] == "running"
    identity = ownership.process_identity(os.getpid())
    if fault == "boot":
        identity["boot_id"] = "different"
    if fault == "ticks":
        identity["start_ticks"] = identity.get("start_ticks", 0) + 1
    if fault == "missing":
        identity = None
    if fault == "permission":
        monkeypatch.setattr(ownership, "process_identity", lambda pid: (_ for _ in ()).throw(PermissionError()))
    with store.connect() as db:
        db.execute("UPDATE takes SET owner=?", (canonical(identity) if identity else None,))
    monkeypatch.setattr(os, "kill", lambda *a: pytest.fail("No PID signal probes"))
    result = store.status("test-take")
    assert result["status"] == "uncertain"
    assert result["steps"][0]["status"] == "uncertain"
    assert store.reserve(request(), MODEL, {}) is not None


def test_managed_cleanup_refuses_reused_identity_and_pidfd_race(tmp_path, monkeypatch):
    identity = ownership.process_identity(os.getpid())
    path = tmp_path / "viewer_test.json"
    path.write_text(json.dumps({"pid": os.getpid()}))
    api = SimpleNamespace(stop_dashboard=lambda *a: pytest.fail("Uncertain owner must not be stopped"))
    changed = {**identity, "start_ticks": identity.get("start_ticks", 0) + 1}
    assert not ownership.stop_service(api, tmp_path, {"service_id": "viewer_test"}, changed)
    assert not ownership.stop_service(api, tmp_path, {"service_id": "viewer_test"}, None)
    monkeypatch.setattr(ownership.signal, "pidfd_send_signal",
                        lambda *a: pytest.fail("Reused PID signal"), raising=False)
    assert not ownership.signal_owned(changed)
    monkeypatch.setattr(ownership, "process_identity", lambda pid: (_ for _ in ()).throw(PermissionError()))
    assert not ownership.stop_service(api, tmp_path, {"service_id": "viewer_test"}, identity)
    assert not ownership.signal_owned(identity)


def test_cleanup_does_not_certify_recycled_zombie(tmp_path, monkeypatch):
    identity = ownership.process_identity(os.getpid())
    (tmp_path / "viewer_test.json").write_text(json.dumps({"pid": os.getpid()}))
    calls = []
    api = SimpleNamespace(stop_dashboard=lambda service: calls.append(service))
    def changed_after_stop(pid, allow_exited=False):
        return {**identity, "start_ticks": identity.get("start_ticks", 0) + 1} if allow_exited else identity
    monkeypatch.setattr(ownership, "process_identity", changed_after_stop)
    monkeypatch.setattr(ownership, "exited", lambda pid: True)
    assert not ownership.stop_service(api, tmp_path, {"service_id": "viewer_test"}, identity)
    assert calls == ["viewer_test"]


def test_runtime_snapshots_are_per_install_and_checksum_enforced(tmp_path, monkeypatch):
    import amplifier_agent_lib.bundle.cache as cache

    from amplifier_smart_tool_showrun import agent

    class Resolver:
        _paths = {"test-module": tmp_path}
        async def async_resolve(self, *args, **kwargs):
            return None
    bundle = SimpleNamespace(resolver=Resolver())
    async def prepared(**kwargs):
        return bundle
    monkeypatch.setattr(cache, "load_and_prepare_cached", prepared)
    monkeypatch.setattr(cache, "cache_dir_for_version", lambda version: tmp_path / "cache")
    # The actual serialization type must be portable; mock just this mechanical
    # boundary here. Real two-interpreter setup is verified separately on the wheel.
    monkeypatch.setattr(agent.pickle, "dumps", lambda bundle: b"prepared-snapshot")
    paths = []
    for prefix in ("source", "wheel", "source", "wheel"):
        monkeypatch.setattr(sys, "prefix", prefix)
        monkeypatch.setattr(sys, "executable", prefix + "/bin/python")
        asyncio.run(agent.prepare_runtime())
        paths.append(agent._location()[1])
    assert paths[0] == paths[2] and paths[1] == paths[3] and paths[0] != paths[1]
    for path in paths[:2]:
        assert (path / "prepared.pickle").read_bytes() == b"prepared-snapshot"
    monkeypatch.setenv("SHOWRUN_TEST_KEY", "synthetic")
    path = agent._location()[1]
    (path / "prepared.pickle").write_bytes(b"corrupt")
    with pytest.raises(ShowrunError, match="not prepared"):
        agent.preflight({**MODEL, "credential_env": "SHOWRUN_TEST_KEY"})


def test_two_real_installations_prepare_in_both_orders(tmp_path):
    other = os.environ.get("SHOWRUN_TEST_OTHER_PYTHON")
    if not other:
        pytest.skip("Set SHOWRUN_TEST_OTHER_PYTHON to a second installed Showrun interpreter.")
    code = """import hashlib,json,os,sys
from amplifier_smart_tool_showrun import Showrun
from amplifier_smart_tool_showrun.agent import preflight,_location
if sys.argv[1]=='prepare': Showrun.prepare_runtime()
os.environ['SHOWRUN_TEST_KEY']='synthetic-no-inference'
b=preflight({'provider':'openai','model':'gpt-6-astra','credential_env':'SHOWRUN_TEST_KEY'})
assert b.resolver._activator is None
p=_location()[1]
print(json.dumps({'prefix':sys.prefix,'path':str(p),
                  'hash':hashlib.sha256((p/'prepared.pickle').read_bytes()).hexdigest()}))
"""
    def run(python, operation):
        output = subprocess.run([python, "-c", code, operation], cwd=tmp_path,
                                capture_output=True, text=True, check=True, timeout=60)
        return json.loads(output.stdout)
    for first, second in ((sys.executable, other), (other, sys.executable)):
        before = run(first, "prepare")
        after = run(second, "prepare")
        assert before["prefix"] != after["prefix"]
        assert before["path"] != after["path"]
        assert run(first, "probe") == before
        assert run(second, "probe") == after


def decode_colors(path):
    """Decode EVERY delivered 25fps frame; inspect a distinctive fixture rectangle."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", "crop=8:8:600:400",
                          "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    assert len(raw) % (8 * 8 * 3) == 0
    colors = []
    for offset in range(0, len(raw), 8 * 8 * 3):
        rgb = raw[offset:offset + 3]
        if max(rgb) > 180 and min(rgb) < 60 and sorted(rgb)[1] < 60:
            colors.append(("red", "green", "blue")[rgb.index(max(rgb))])
        else:
            colors.append("other")
    return colors


def assert_pixel_timeline(colors, receipt):
    """Fixture-specific oracle, not a product claim of universal visual understanding."""
    previous = -1
    for row, color in zip(receipt["steps"], ("red", "green", "blue"), strict=True):
        hold = row["hold"]
        interval = row["interval"]
        precision = interval["precision_seconds"]
        assert precision == .08
        assert 0 <= interval["start_seconds"] <= hold["start_seconds"]
        assert hold["end_seconds"] <= interval["end_seconds"] <= len(colors) / 25 + precision
        assert interval["end_seconds"] == row["ended_seconds"] == hold["end_seconds"]
        assert hold["start_seconds"] == row["visible_result_seconds"]
        interactions = [e for e in row["events"] if e["kind"] == "interaction"
                        and e.get("state") != "not_dispatched"]
        first = row["first_interaction_seconds"]
        assert first == (interactions[0]["dispatch_seconds"] if interactions else None)
        assert interval["start_seconds"] == (first if first is not None else row["visible_result_seconds"])
        for event in interactions:
            assert event["state"] == "returned"
            assert interval["start_seconds"] <= event["dispatch_seconds"] <= event["returned_seconds"]
            assert event["returned_seconds"] <= hold["start_seconds"] <= interval["end_seconds"]
        for event in row["events"]:
            if "start_seconds" in event:
                assert row["started_seconds"] <= event["start_seconds"] <= event["end_seconds"]
                assert event["end_seconds"] <= hold["start_seconds"]
        # Ignore at most the declared uncertainty at either edge, never elsewhere.
        start = int((hold["start_seconds"] + precision) * 25) + 1
        end = int((hold["end_seconds"] - precision) * 25)
        assert end > start and end <= len(colors)
        assert set(colors[start:end]) == {color}
        positions = [i for i in range(len(colors)) if colors[i] == color]
        assert positions and positions[0] > previous
        previous = positions[-1]
        # The same contiguous state must cover >=3 seconds in actual decoded pixels.
        left, right = start, end
        while left > 0 and colors[left - 1] == color:
            left -= 1
        while right < len(colors) and colors[right] == color:
            right += 1
        assert (right - left) / 25 >= hold["requested_seconds"]
        assert left / 25 <= hold["start_seconds"] + precision
        assert right / 25 >= hold["end_seconds"] - precision


def test_moved_delayed_navigation_pixel_oracle_and_retained_refinement(
    tmp_path, target_server, scripted, monkeypatch
):
    import test_showrun
    # Label-based controls reordered and moved. The red/green/blue rectangle is
    # test-only visual state; production code cannot know or fake these colors.
    html = b"""<!doctype html><body style="margin:0;background:rgb(255,0,0)">
<h1>Prepared deck</h1><div style="position:absolute;right:15px;top:160px">
<button aria-label="Previous slide">Back</button>
<button aria-label="Next slide" onclick="advance()">Go</button></div>
<script>let i=0;let pending=false;
function advance(){if(pending)return;pending=true;document.querySelector('h1').textContent='Rendering...';setTimeout(()=>{
 i=Math.min(2,i+1);document.querySelector('h1').textContent=['Prepared deck','Middle destination','Final result'][i];
 document.body.style.background=['rgb(255,0,0)','rgb(0,255,0)','rgb(0,0,255)'][i];pending=false;
},550);}</script></body>"""
    monkeypatch.setattr(test_showrun, "HTML", html)
    scripted.delay = .65
    value = request(target_server[0])
    value["capture"] = {"width": 640, "height": 480}
    value["steps"].insert(1, {"id": "middle", "instruction": "Navigate to middle",
                              "visible_text": "Middle destination"})
    api = Showrun(tmp_path, MODEL)
    results = []
    for n in range(2):
        value["request_id"] = f"refinement-{n}"
        value["steps"][1]["instruction"] = f"Show middle destination, review refinement {n}"
        result = api.record(value)
        assert result["status"] == "succeeded", result
        assert result["usage"] == {"actions": 2, "model_calls": 2}
        path = tmp_path / value["request_id"] / "capture.mp4"
        colors = decode_colors(path)
        assert_pixel_timeline(colors, result)
        from amplifier_smart_tool_showrun.capture import validate_interval
        for i, row in enumerate(result["steps"]):
            validate_interval(row, result["media"]["duration_seconds"])
            for edge in ("start_seconds", "end_seconds"):
                for shift in (-1, 1):
                    displaced = copy.deepcopy(result)
                    displaced["steps"][i]["interval"][edge] += shift
                    with pytest.raises(AssertionError):
                        assert_pixel_timeline(colors, displaced)
                    with pytest.raises(ShowrunError):
                        validate_interval(displaced["steps"][i], result["media"]["duration_seconds"])
        waits = [e for r in result["steps"] for e in r["events"] if e["kind"] == "application_wait"]
        assert sum(e["end_seconds"] - e["start_seconds"] for e in waits) >= .9
        thoughts = [e for r in result["steps"] for e in r["events"] if e["kind"] == "model_deliberation"]
        assert sum(e["end_seconds"] - e["start_seconds"] for e in thoughts) >= 1.2
        assert result["media"]["duration_seconds"] >= 11.2
        results.append(result)
        assert api.record(value)["media"] == result["media"]
        changed = copy.deepcopy(value)
        changed["steps"][1]["instruction"] += " changed"
        with pytest.raises(ShowrunError, match="different effective"):
            api.record(changed)
        # Corrupted/frozen/missing states and dishonest interval shifts cannot pass.
        for bad in ([colors[0]] * len(colors),
                    ["red" if c == "green" else c for c in colors],
                    colors[100:] + colors[:100]):
            with pytest.raises(AssertionError):
                assert_pixel_timeline(bad, result)
        displaced = copy.deepcopy(result)
        displaced["steps"][1]["hold"]["start_seconds"] = 0
        displaced["steps"][1]["hold"]["end_seconds"] = 3.2
        with pytest.raises(AssertionError):
            assert_pixel_timeline(colors, displaced)
    assert results[0]["take_id"] != results[1]["take_id"]
    from amplifier_smart_tool_showrun import agent
    monkeypatch.setattr(agent, "Navigator", lambda *a: pytest.fail("Inspection started provider"))
    monkeypatch.setattr(Browser, "start", lambda *a: pytest.fail("Inspection started browser"))
    for result in results:
        folder = tmp_path / result["request_id"]
        receipt_hash = hashlib.sha256((folder / "receipt.json").read_bytes()).hexdigest()
        assert Showrun(tmp_path).inspect(result["request_id"])["inspection"]["sha256"] == result["media"]["sha256"]
        assert hashlib.sha256((folder / "receipt.json").read_bytes()).hexdigest() == receipt_hash
    # A genuinely fresh process can inspect without access to our navigator patch.
    code = """import sys
from amplifier_smart_tool_showrun import Showrun
r=Showrun(sys.argv[1]).inspect('refinement-0')
assert r['inspection']['sha256']==r['media']['sha256']
assert 'amplifier_agent_lib' not in sys.modules and 'playwright' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=tmp_path, check=True)


def test_duplicate_labels_fail_before_click(tmp_path, target_server, scripted, monkeypatch):
    import test_showrun
    monkeypatch.setattr(test_showrun, "HTML", b"""<h1>Prepared deck</h1>
<button aria-label="Next slide" onclick="document.querySelector('h1').textContent='Unsafe'">A</button>
<button aria-label="Next slide" onclick="document.querySelector('h1').textContent='Final result'">B</button>""")
    value = request(target_server[0])
    value["capture"] = {"width": 640, "height": 480}
    result = Showrun(tmp_path, MODEL).record(value)
    assert result["error"]["code"] == "ambiguous_navigation", result
    assert result["usage"]["actions"] == 0
    assert not any(e.get("state") == "dispatched" for r in result["steps"] for e in r.get("events", []))


def test_stale_decision_reobserved_without_dispatch(tmp_path, target_server, scripted, monkeypatch):
    original = scripted.decide
    generations = []
    async def stale_once(self, step, observation, *args):
        generations.append(observation["generation"])
        if len(generations) == 1:
            return {"action": "click", "ref": "g0.f0.e0"}
        return await original(self, step, observation, *args)
    monkeypatch.setattr(scripted, "decide", stale_once)
    value = request(target_server[0])
    value["capture"] = {"width": 640, "height": 480}
    result = Showrun(tmp_path, MODEL).record(value)
    assert result["status"] == "succeeded", result
    assert result["usage"] == {"actions": 2, "model_calls": 3}
    assert generations == sorted(set(generations))
    events = result["steps"][1]["events"]
    assert sum(e["kind"] == "stale_observation" for e in events) == 1


def test_detached_and_new_duplicate_controls_fail_closed(tmp_path, target_server):
    from amplifier_smart_tool_showrun.target import Target

    async def run():
        target = Target(request(target_server[0])["target"], tmp_path)
        await target.start()
        browser = Browser(target, tmp_path, {"width": 640, "height": 480})
        try:
            await browser.start(target.url)
            observation = await browser.observe()
            ref = observation["frames"][0]["controls"][0]["ref"]
            # Test-owned fixture changes while a navigator could be thinking.
            await browser.page.locator("button").first.evaluate("el=>el.replaceWith(el.cloneNode(true))")
            with pytest.raises(ShowrunError) as failure:
                await browser.act({"action": "click", "ref": ref})
            assert failure.value.code == "stale_ref"
            observation = await browser.observe()
            ref = observation["frames"][0]["controls"][0]["ref"]
            await browser.page.locator("button").first.evaluate("el=>el.after(el.cloneNode(true))")
            with pytest.raises(ShowrunError) as failure:
                await browser.act({"action": "click", "ref": ref})
            assert failure.value.code == "ambiguous_navigation"
            assert await browser.page.locator("h1").inner_text() == "Prepared deck"
        finally:
            await browser.close()
    asyncio.run(run())


@pytest.mark.parametrize("change", ["text", "duplicate", "frame", "frame_replace",
                                  "document", "control", "superseded"])
def test_key_decision_is_bound_to_observed_state(tmp_path, target_server, scripted, monkeypatch, change):
    from playwright.async_api import ElementHandle

    browsers, decisions, dispatches = [], [], []
    start = Browser.start
    async def started(self, url):
        await start(self, url)
        if change == "frame_replace":
            await self.page.evaluate("""() => {
                let f=document.createElement('iframe');f.sandbox='allow-scripts';
                f.srcdoc='<h1>Stable frame text</h1>';document.body.append(f);
            }""")
            await self.page.frame_locator("iframe").locator("h1").wait_for()
        browsers.append(self)
    monkeypatch.setattr(Browser, "start", started)
    async def forbidden_press(self, *args, **kwargs):
        dispatches.append(args)
        raise AssertionError("Stale key reached Playwright")
    monkeypatch.setattr(ElementHandle, "press", forbidden_press)
    async def decide(self, step, observation, *args):
        decisions.append(observation["generation"])
        if len(decisions) > 1:
            return {"action": "fail"}
        browser = browsers[0]
        if change == "text":
            await browser.page.locator("h1").evaluate("el=>el.textContent='Changed while thinking'")
        elif change == "duplicate":
            await browser.page.locator("button").first.evaluate("el=>el.after(el.cloneNode(true))")
        elif change == "frame":
            await browser.page.evaluate("""() => {
                let f=document.createElement('iframe');f.sandbox='allow-scripts';
                f.srcdoc='<h1>New frame</h1>';document.body.append(f);
            }""")
            await browser.page.frame_locator("iframe").locator("h1").wait_for()
        elif change == "frame_replace":
            await browser.page.locator("iframe").evaluate("el=>el.replaceWith(el.cloneNode(true))")
            await browser.page.frame_locator("iframe").locator("h1").wait_for()
        elif change == "document":
            await browser.page.locator("body").evaluate("el=>el.replaceWith(el.cloneNode(true))")
        elif change == "control":
            await browser.page.locator("button").first.evaluate("el=>el.replaceWith(el.cloneNode(true))")
        else:
            await browser.observe()
        return {"action": "key", "frame": 0, "key": "End"}
    monkeypatch.setattr(scripted, "decide", decide)
    value = request(target_server[0])
    value["capture"] = {"width": 640, "height": 480}
    result = Showrun(tmp_path, MODEL).record(value)
    assert result["status"] == "failed", result
    assert result["steps"][1]["status"] == "failed"
    assert result["usage"]["actions"] == 0 and dispatches == []
    assert result["steps"][1]["first_interaction_seconds"] is None
    assert result["error"]["code"] == ("ambiguous_navigation" if change == "duplicate" else "destination_not_found")
    events = result["steps"][1]["events"]
    assert any(e.get("state") == "not_dispatched" for e in events)
    assert not any("dispatch_seconds" in e for e in events)
    if change != "duplicate":
        assert len(decisions) == 2 and decisions[1] > decisions[0]


@pytest.mark.parametrize("change", ["detached", "duplicate"])
def test_final_precondition_rejection_is_not_dispatch(tmp_path, target_server, scripted, monkeypatch, change):
    from playwright.async_api import ElementHandle

    act = Browser.act
    attempts, dispatches = [], []
    async def changed(self, action, **kwargs):
        attempts.append(action)
        if len(attempts) == 1:
            script = ("el=>el.replaceWith(el.cloneNode(true))" if change == "detached"
                      else "el=>el.after(el.cloneNode(true))")
            await self.page.locator("button").first.evaluate(script)
        return await act(self, action, **kwargs)
    async def forbidden_click(self, *args, **kwargs):
        dispatches.append(args)
        raise AssertionError("Known precondition rejection dispatched")
    monkeypatch.setattr(Browser, "act", changed)
    monkeypatch.setattr(ElementHandle, "click", forbidden_click)
    # One decision bounds stale repair: no second action is possible in this test.
    value = request(target_server[0])
    value["authority"]["max_model_calls"] = 1
    value["capture"] = {"width": 640, "height": 480}
    result = Showrun(tmp_path, MODEL).record(value)
    assert result["steps"][1]["status"] == "failed", result
    assert result["usage"]["actions"] == 0 and dispatches == []
    assert result["steps"][1]["first_interaction_seconds"] is None
    assert result["error"]["code"] == ("model_limit" if change == "detached" else "ambiguous_navigation")
    events = result["steps"][1]["events"]
    assert any(e.get("state") == "not_dispatched" for e in events)
    assert not any("dispatch_seconds" in e for e in events)


@pytest.mark.parametrize("failure", ["stale_reply", "closed_target"])
def test_transport_failure_after_reserved_dispatch_stays_uncertain(
    tmp_path, target_server, scripted, monkeypatch, failure
):
    from playwright.async_api import ElementHandle

    dispatches = []
    click = ElementHandle.click
    async def lost_reply(self, *args, **kwargs):
        saved = json.loads((tmp_path / "test-take/receipt.json").read_text())
        dispatched = saved["steps"][1]["events"][-1]
        assert dispatched["state"] == "dispatched"
        assert saved["usage"]["actions"] == 1
        assert saved["steps"][1]["first_interaction_seconds"] == dispatched["dispatch_seconds"]
        dispatches.append(dispatched)
        if failure == "closed_target":
            frame = await self.owner_frame()
            await frame.page.close()  # test-only transport loss AFTER durable reservation
            return await click(self, *args, **kwargs)  # real Playwright TargetClosedError
        # Even a stale-looking error after the dispatch seam must not be retried.
        raise ShowrunError("stale_ref", "Transport failed after attempted dispatch.")
    monkeypatch.setattr(ElementHandle, "click", lost_reply)
    value = request(target_server[0])
    value["capture"] = {"width": 640, "height": 480}
    api = Showrun(tmp_path, MODEL)
    result = api.record(value)
    assert result["steps"][1]["status"] == "uncertain", result
    assert result["usage"] == {"actions": 1, "model_calls": 1}
    assert len(dispatches) == 1
    assert result["steps"][1]["events"][-1]["state"] == "dispatched"
    assert api.record(value)["steps"][1]["status"] == "uncertain"
    assert len(dispatches) == 1


def test_current_key_dispatch_retains_real_outcome(tmp_path, target_server, scripted, monkeypatch):
    async def decide(self, *args):
        return {"action": "key", "frame": 0, "key": "End"}
    monkeypatch.setattr(scripted, "decide", decide)
    value = request(target_server[0])
    value["capture"] = {"width": 640, "height": 480}
    result = Showrun(tmp_path, MODEL).record(value)
    assert result["status"] == "succeeded", result
    assert result["usage"] == {"actions": 1, "model_calls": 1}
    event = next(e for e in result["steps"][1]["events"] if e["kind"] == "interaction")
    assert event["state"] == "returned" and event["observation_generation"] > 0


def test_impossible_interval_cannot_advertise_success(tmp_path, target_server, scripted, monkeypatch):
    from amplifier_smart_tool_showrun.capture import Capture

    original_save = Store.save
    finish = Capture.finish
    receipts = []
    def track(self, receipt):
        receipts[:] = [receipt]
        return original_save(self, receipt)
    async def inconsistent(self):
        media = await finish(self)
        receipts[0]["steps"][0]["interval"]["start_seconds"] += 1
        return media
    monkeypatch.setattr(Store, "save", track)
    monkeypatch.setattr(Capture, "finish", inconsistent)
    value = request(target_server[0])
    value["capture"] = {"width": 640, "height": 480}
    result = Showrun(tmp_path, MODEL).record(value)
    assert result["status"] == "failed" and result["partial"]
    assert result["capture_error"]["code"] == "capture_timing"
