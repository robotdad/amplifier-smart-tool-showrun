"""Dependency UX: isolated probes, no installation or live model/target calls."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_showrun import MODEL, request

from amplifier_smart_tool_showrun import Showrun, ShowrunError, capture, diagnostics
from amplifier_smart_tool_showrun.cli import main


@pytest.fixture
def media_tools(monkeypatch):
    state = {"missing": set(), "broken": {}, "encoder": True, "calls": []}

    def which(name):
        return None if name in state["missing"] else f"/tools/{name}"

    async def probe(path, *args):
        name = Path(path).name
        state["calls"].append((name, args))
        if name in state["broken"]:
            raise state["broken"][name]
        if args == ("-version",):
            return f"{name} version fixture-1\nbuild details\n"
        assert name == "ffmpeg" and args == ("-hide_banner", "-encoders")
        return " V....D libx264 fixture encoder\n" if state["encoder"] else " V....D libx264rgb not libx264\n"

    monkeypatch.setattr(diagnostics.shutil, "which", which)
    monkeypatch.setattr(diagnostics, "_probe", probe)
    return state


@pytest.mark.parametrize("missing", [{"ffmpeg"}, {"ffprobe"}, {"ffmpeg", "ffprobe"}])
def test_missing_tools_and_all_ready_recovery(media_tools, missing):
    media_tools["missing"] = missing
    result = Showrun.doctor("inspect")
    assert result["status"] == "failed" and not result["ready"]
    for row in result["checks"]:
        if row["name"] in missing:
            assert row["path"] is None
            assert row["error"]["code"] == "capture_dependency_missing"
            remedy = row["error"]["remedy"]
            for expected in ("PATH", "restart", "ffmpeg -version", "ffprobe -version", "showrun doctor"):
                assert expected in remedy
            assert "new request_id" not in remedy
        else:
            assert row["status"] == "ready"
    media_tools["missing"].clear()
    ready = Showrun.doctor("inspect")
    assert ready["status"] == "ready" and ready["ready"]
    assert ready["model_calls"] == 0
    assert all(row["path"] and row["version"] for row in ready["checks"])


@pytest.mark.parametrize("name", ["ffmpeg", "ffprobe"])
@pytest.mark.parametrize("failure", [PermissionError(), OSError(), TimeoutError()])
def test_unusable_binaries_are_dependency_failures(media_tools, name, failure):
    media_tools["broken"][name] = failure
    result = Showrun.doctor("inspect")
    row = next(row for row in result["checks"] if row["name"] == name)
    assert result["status"] == "failed"
    assert row["path"] == f"/tools/{name}"
    assert row["error"]["code"] == "capture_dependency_missing"
    assert "could not run" in row["error"]["message"]


def test_unrecognized_version_fails(monkeypatch, media_tools):
    async def probe(*args):
        return "not a media executable"
    monkeypatch.setattr(diagnostics, "_probe", probe)
    assert all(row["status"] == "failed" for row in Showrun.doctor("inspect")["checks"])


def test_encoder_required_only_for_recording(monkeypatch, media_tools):
    async def backend(mode):
        assert mode in {"web", "inspect"}
        return []
    monkeypatch.setattr(diagnostics, "backend_checks", backend)
    media_tools["encoder"] = False
    result = Showrun.doctor()
    assert result["status"] == "failed"
    assert result["checks"][-1]["name"] == "libx264"
    assert "libx264" in result["checks"][-1]["error"]["message"]
    media_tools["calls"].clear()
    assert Showrun.doctor("inspect")["status"] == "ready"
    assert all(args == ("-version",) for _, args in media_tools["calls"])
    media_tools["encoder"] = True
    assert Showrun.doctor()["status"] == "ready"


def test_encoder_probe_failure(monkeypatch, media_tools):
    original = diagnostics._probe
    async def probe(path, *args):
        if "-encoders" in args:
            raise OSError()
        return await original(path, *args)
    monkeypatch.setattr(diagnostics, "_probe", probe)
    result = asyncio.run(diagnostics.media_checks())
    assert result[-1]["error"]["code"] == "capture_dependency_missing"
    assert "encoder query" in result[-1]["error"]["message"]


@pytest.mark.parametrize("platform,command", [
    ("darwin", "brew install ffmpeg"), ("win32", "winget install --id Gyan.FFmpeg --exact"),
    ("linux", "sudo apt-get update && sudo apt-get install ffmpeg"),
])
def test_platform_specific_remedies(monkeypatch, platform, command):
    monkeypatch.setattr(diagnostics, "sys", SimpleNamespace(platform=platform))
    assert command in diagnostics.media_remedy()


@pytest.mark.parametrize("module", ["capture", "desktop", "windows_desktop"])
def test_recording_backends_share_media_failure(monkeypatch, media_tools, module):
    from amplifier_smart_tool_showrun import desktop, windows_desktop

    media_tools["missing"] = {"ffprobe"}
    backend = {"capture": capture, "desktop": desktop, "windows_desktop": windows_desktop}[module]
    with pytest.raises(ShowrunError) as error:
        asyncio.run(backend.preflight())
    assert error.value.code == "capture_dependency_missing"
    assert error.value.message.startswith("ffprobe:")
    assert "new request_id" in error.value.remedy
    assert "exact retry returns the retained failure" in error.value.remedy


def test_record_failure_is_retained_and_exact_retry_does_not_recheck(tmp_path, monkeypatch, media_tools):
    from amplifier_smart_tool_showrun import agent, target

    def forbidden(*args, **kwargs):
        pytest.fail("Failed preflight must not initialize a provider or launch a target")
    monkeypatch.setattr(agent, "Navigator", forbidden)
    monkeypatch.setattr(target, "Target", forbidden)
    media_tools["missing"] = {"ffmpeg"}
    api = Showrun(tmp_path, MODEL)
    failed = api.record(request())
    assert failed["status"] == "failed"
    assert failed["usage"]["model_calls"] == 0 and failed["resources"] == {}
    assert failed["error"]["code"] == "capture_dependency_missing"
    assert "new request_id" in failed["error"]["remedy"]
    assert failed["cleanup"] == "verified"
    # Repair must not silently turn an exact retry into a new execution.
    media_tools["missing"].clear()
    media_tools["calls"].clear()
    again = api.record(request())
    assert again["error"] == failed["error"]
    assert again["status"] == "failed" and not media_tools["calls"]


def retained_media(tmp_path):
    api = Showrun(tmp_path / "takes")
    store = api._store()
    req = api.validate(request())["request"]
    data = b"fixture MP4; decode is stubbed in this test"
    receipt = {"request_id": req["request_id"], "status": "succeeded", "restricted": False,
               "media": {"path": "capture.mp4", "sha256": hashlib.sha256(data).hexdigest()}}
    store.reserve(req, MODEL, receipt)
    store.save(receipt)
    (store.directory(req["request_id"]) / "capture.mp4").write_bytes(data)
    return api, req["request_id"]


@pytest.mark.parametrize("missing", ["ffmpeg", "ffprobe"])
def test_inspect_dependency_failure_and_same_take_recovery(tmp_path, monkeypatch, capsys, media_tools, missing):
    api, identity = retained_media(tmp_path)
    before = {str(p): p.read_bytes() for p in api.storage.rglob("*") if p.is_file()}
    media_tools["missing"] = {missing}
    with pytest.raises(ShowrunError) as failure:
        api.inspect(identity)
    assert failure.value.code == "capture_dependency_missing"
    assert missing in failure.value.message
    assert "same take" in failure.value.remedy and "no new request_id" in failure.value.remedy
    assert main(["--storage", str(api.storage), "inspect", identity]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "capture_dependency_missing"  # never input_error
    assert "same take" in result["error"]["remedy"]

    async def command(*args, **kwargs):
        if args[0] == "ffprobe":
            return json.dumps({"streams": [{"codec_type": "video", "width": 640, "height": 360}],
                               "format": {"format_name": "mp4", "duration": "1"}}).encode()
        assert args[0] == "ffmpeg" and "-i" in args
        return b""
    monkeypatch.setattr(capture, "command", command)
    media_tools["missing"].clear()
    media_tools["encoder"] = False  # decoding does not require libx264
    result = api.inspect(identity)
    assert result["inspection"]["decoded"] is True
    assert result["request_id"] == identity
    assert main(["--storage", str(api.storage), "inspect", identity]) == 0
    assert json.loads(capsys.readouterr().out)["inspection"]["decoded"]
    after = {str(p): p.read_bytes() for p in api.storage.rglob("*") if p.is_file()}
    assert before == after


def test_disappearing_media_executable_is_not_input_error(monkeypatch):
    async def missing(*args, **kwargs):
        raise FileNotFoundError()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", missing)
    with pytest.raises(ShowrunError) as error:
        asyncio.run(capture.command("ffmpeg", "-version"))
    assert error.value.code == "capture_dependency_missing"
    assert "PATH" in error.value.remedy


def test_inspection_race_keeps_same_take_retry_guidance(tmp_path, monkeypatch, media_tools):
    api, identity = retained_media(tmp_path)
    async def disappeared(*args, **kwargs):
        raise diagnostics.media_error("ffprobe", "executable disappeared after preflight.")
    monkeypatch.setattr(capture, "command", disappeared)
    with pytest.raises(ShowrunError) as error:
        api.inspect(identity)
    assert error.value.code == "capture_dependency_missing"
    assert "same take" in error.value.remedy and "no new request_id" in error.value.remedy


def test_invalid_media_not_reclassified_as_dependency_error(tmp_path, monkeypatch, media_tools):
    async def invalid(*args, **kwargs):
        raise ShowrunError("media_invalid", "Media processing failed.")
    monkeypatch.setattr(capture, "command", invalid)
    with pytest.raises(ShowrunError) as error:
        asyncio.run(capture.inspect_media(tmp_path / "bad.mp4"))
    assert error.value.code == "media_invalid"


@pytest.mark.parametrize("mode,platform,module", [
    ("macos", "darwin", "desktop"), ("windows", "win32", "windows_desktop"),
])
def test_native_doctor_does_not_launch_companion(tmp_path, monkeypatch, media_tools, mode, platform, module):
    from amplifier_smart_tool_showrun import desktop, windows_desktop

    backend = desktop if module == "desktop" else windows_desktop
    helper = tmp_path / "companion"
    monkeypatch.setattr(diagnostics, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(backend, "helper_path", lambda: helper)
    assert Showrun.doctor(mode)["checks"][-1]["error"]["code"] == "desktop_not_prepared"
    helper.write_text("fixture, never executed")
    result = Showrun.doctor(mode)
    assert result["status"] == "ready"
    assert result["checks"][-1]["path"] == str(helper)
    assert "not launched" in result["checks"][-1]["verification"]
    assert "permissions" in " ".join(result["limitations"])


def test_unsupported_native_does_not_block_inspection(monkeypatch, media_tools):
    monkeypatch.setattr(diagnostics, "sys", SimpleNamespace(platform="linux"))
    result = Showrun.doctor("macos")
    assert result["status"] == "failed"
    assert result["checks"][-1]["error"]["code"] == "desktop_unsupported"
    assert Showrun.doctor("inspect")["status"] == "ready"


def test_web_doctor_checks_only_driver_and_executable(tmp_path, monkeypatch, media_tools):
    import playwright.async_api

    browser = tmp_path / "chromium"
    class Driver:
        async def __aenter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(executable_path=str(browser)))
        async def __aexit__(self, *args):
            pass
    monkeypatch.setattr(playwright.async_api, "async_playwright", Driver)
    result = Showrun.doctor()
    assert result["checks"][-1]["error"]["code"] == "capture_dependency_missing"
    assert "python -m playwright install chromium" in result["checks"][-1]["error"]["remedy"]
    browser.write_text("fixture, never executed")
    assert Showrun.doctor()["status"] == "ready"

    class BrokenDriver(Driver):
        async def __aenter__(self):
            raise OSError("broken driver")
    monkeypatch.setattr(playwright.async_api, "async_playwright", BrokenDriver)
    assert Showrun.doctor()["checks"][-1]["status"] == "failed"


def test_cli_doctor_structured_output_and_no_store(tmp_path, capsys, media_tools):
    storage = tmp_path / "must-not-exist"
    assert main(["--storage", str(storage), "doctor", "--mode", "inspect"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == Showrun(storage).doctor("inspect")
    assert not storage.exists()
    media_tools["missing"] = {"ffmpeg"}
    assert main(["--storage", str(storage), "doctor", "--mode", "inspect"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "failed" and not captured.err
    assert not storage.exists()
    assert "doctor" in {row["name"] for row in Showrun.manifest()["capabilities"]}
    assert "PATH" in Showrun.skill("doctor")
    assert "prepare-runtime" in Showrun.skill("doctor")


def test_invalid_doctor_mode():
    with pytest.raises(ShowrunError) as error:
        Showrun.doctor("all")
    assert error.value.code == "invalid_request"
    assert "request_id" not in error.value.remedy


def test_doctor_missing_path_fresh_process_no_runtime_or_storage(tmp_path):
    script = """
import json, sys
from amplifier_smart_tool_showrun import Showrun
api = Showrun(sys.argv[1])
result = api.doctor('inspect')
assert not api.storage.exists()
for name in ('amplifier_agent_lib', 'amplifier_smart_tool_showrun.agent',
             'amplifier_smart_tool_showrun.target', 'playwright'):
    assert name not in sys.modules, name
assert result['status'] == 'failed'
assert all(row['error']['code'] == 'capture_dependency_missing' for row in result['checks'])
print(json.dumps(result))
"""
    env = {key: value for key, value in os.environ.items() if not key.endswith("API_KEY")}
    env["PATH"] = ""
    run = subprocess.run([sys.executable, "-c", script, str(tmp_path / "no-takes")],
                         env=env, cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["model_calls"] == 0


@pytest.mark.parametrize("failure", ["nonzero", "timeout", "cancel"])
def test_probe_subprocess_is_bounded_and_cleaned_up(monkeypatch, failure):
    class Process:
        returncode = None
        killed = False
        waited = False
        async def communicate(self):
            if failure == "nonzero":
                self.returncode = 1
                return b"", None
            if failure == "cancel":
                raise asyncio.CancelledError()
            raise TimeoutError()
        def kill(self):
            self.killed = True
        async def wait(self):
            self.waited = True
            self.returncode = -9
    proc = Process()
    async def spawn(*args, **kwargs):
        assert args == ("/tools/ffmpeg", "-version")
        return proc
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    expected = asyncio.CancelledError if failure == "cancel" else (OSError, TimeoutError)
    with pytest.raises(expected):
        asyncio.run(diagnostics._probe("/tools/ffmpeg", "-version"))
    assert proc.killed == (failure != "nonzero")
    assert proc.waited == (failure != "nonzero")