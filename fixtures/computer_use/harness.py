"""Build/launch native fixture apps and verify their independent persisted state.

No operation in this harness performs a task by invoking the fixture backend.
`self-test` deliberately exercises widget handlers, not OS computer-use drivers.
"""

import argparse
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLATFORM = {"darwin": "macos", "win32": "windows", "linux": "linux"}.get(sys.platform)


def scenarios():
    return json.loads((ROOT / "scenarios.json").read_text(encoding="utf-8"))["scenarios"]


def scenario(name):
    return next(item for item in scenarios() if item["id"] == name)


def build(platform, output, gtk_python="/usr/bin/python3"):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if platform == "macos":
        if sys.platform != "darwin":
            raise ValueError("Build AppKit on macOS")
        app = output / "Showrun Fixture.app"
        binary = app / "Contents" / "MacOS" / "Fixture"
        binary.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["xcrun", "swiftc", "-parse-as-library", str(ROOT / "macos/Fixture.swift"), "-o", str(binary)],
            check=True,
        )
        with (app / "Contents/Info.plist").open("wb") as stream:
            plistlib.dump(
                {
                    "CFBundleIdentifier": "org.showrun.ComputerUseFixture",
                    "CFBundleName": "Showrun Fixture",
                    "CFBundleExecutable": "Fixture",
                    "CFBundlePackageType": "APPL",
                    "CFBundleVersion": "1",
                    "NSHighResolutionCapable": True,
                },
                stream,
            )
        command = [str(binary)]
    elif platform == "windows":
        subprocess.run(
            [
                "dotnet",
                "build",
                str(ROOT / "windows/Fixture.csproj"),
                "-c",
                "Release",
                "-o",
                str(output / "windows"),
                "-p:BaseIntermediateOutputPath=" + str(output / "obj") + os.sep,
            ],
            check=True,
        )
        command = ["dotnet", str(output / "windows/Fixture.dll")]
    elif platform == "linux":
        subprocess.run(
            [gtk_python, "-c", "import gi; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk"],
            check=True,
        )
        command = [gtk_python, str(ROOT / "linux/app.py")]
    else:
        raise ValueError("Unknown platform")
    descriptor = {"schema_version": 1, "platform": platform, "command": command}
    (output / (platform + ".json")).write_text(json.dumps(descriptor, indent=2), encoding="utf-8")
    return descriptor


def verify(run, name, now=None):
    """Read state only. Passing proves fixture data/events, not visible UI or video."""
    metadata = json.loads((run / "run.json").read_text(encoding="utf-8"))
    raw = (run / "state.json").read_bytes()
    state = json.loads(raw)
    spec = scenario(name)
    errors = []
    if metadata["scenario"] != name:
        errors.append("Run belongs to another scenario")
    if (
        state.get("schema_version") != 1
        or state.get("run_id") != metadata["run_id"]
        or state.get("platform") != metadata["platform"]
    ):
        errors.append("Snapshot identity does not match the run")
    events = state.get("events", [])
    if [event.get("seq") for event in events] != list(range(1, len(events) + 1)) or state.get(
        "revision"
    ) != len(events):
        errors.append("Event history is incomplete or out of order")
    kinds = [event.get("kind") for event in events]
    cursor = 0
    for expected in spec["events"]:
        try:
            cursor = kinds.index(expected, cursor) + 1
        except ValueError:
            errors.append("Missing ordered event: " + expected)
        if expected not in {"set_draft", "set_note"} and kinds.count(expected) != 1:
            errors.append("Expected one effect: " + expected)
    for key, expected in spec["expected"].items():
        if state.get(key) != expected:
            errors.append("Unexpected persisted field: " + key)
    for forbidden in spec.get("absent_events", []):
        if forbidden in kinds:
            errors.append("Unexpected effect: " + forbidden)
    if spec.get("wait_past_deadline"):
        deadline = state.get("operation_not_before")
        if not isinstance(deadline, (int, float)) or (time.time() if now is None else now) <= deadline:
            errors.append("Wait beyond the original delayed-operation deadline")
    return {
        "status": "passed" if not errors else "failed",
        "scenario": name,
        "run_id": metadata["run_id"],
        "platform": metadata["platform"],
        "state_sha256": hashlib.sha256(raw).hexdigest(),
        "revision": state.get("revision"),
        "evidence": "independent persisted fixture state and ordered events",
        "limitations": ["Does not certify UI-driver execution, capture coverage, or watchability."],
        "errors": errors,
    }


def verify_widgets(run):
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    metadata = json.loads((run / "run.json").read_text(encoding="utf-8"))
    kinds = [event["kind"] for event in state["events"]]
    expected = [
        "set_draft",
        "save",
        "move_save",
        "save",
        "show_details",
        "set_note",
        "save_note",
        "open_dialog",
        "confirm_dialog",
        "start_delay",
        "complete_delay",
        "start_delay",
        "cancel_delay",
    ]
    passed = (
        state["run_id"] == metadata["run_id"]
        and state["saved_task"] == "Widget trial"
        and state["saved_note"] == "Widget note"
        and state["confirmed_task"] == "Widget trial"
        and state["operation_status"] == "cancelled"
        and kinds == expected
        and time.time() > state["operation_not_before"]
    )
    return {
        "status": "passed" if passed else "failed",
        "evidence": "native widget-handler self-test",
        "limitations": ["No external accessibility driver or recording was exercised."],
    }


def launch(descriptor, run, name, self_test=False):
    if descriptor["platform"] != PLATFORM:
        raise ValueError("Launch on the native platform; cross-building does not execute the app")
    run.mkdir(parents=True, exist_ok=False)
    run_id = str(uuid.uuid4())
    metadata = {
        "schema_version": 1,
        "run_id": run_id,
        "platform": PLATFORM,
        "scenario": name,
        "mode": "widget-self-test" if self_test else "external-ui-trial",
    }
    (run / "run.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    argv = descriptor["command"] + [
        "--python",
        sys.executable,
        "--host",
        str(ROOT / "state_host.py"),
        "--state",
        str(run / "state.json"),
        "--run-id",
        run_id,
        "--ready",
        str(run / "ready.json"),
    ]
    if self_test:
        argv.append("--self-test")
    with (run / "app.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen(argv, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 20
            while not (run / "ready.json").exists():
                if child.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Native app did not become ready; inspect app.log")
                time.sleep(0.05)
            print(
                json.dumps(
                    {
                        "status": "ready",
                        "run": str(run),
                        "pid": child.pid,
                        "instructions": [] if self_test else scenario(name)["instructions"],
                    }
                ),
                flush=True,
            )
            code = child.wait(timeout=20 if self_test else None)
            if code != 0:
                raise RuntimeError("Native fixture exited unsuccessfully; inspect app.log")
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
    result = verify_widgets(run) if self_test else verify(run, name)
    (run / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def showrun_request(run, request_id):
    metadata = json.loads((run / "run.json").read_text(encoding="utf-8"))
    ready = json.loads((run / "ready.json").read_text(encoding="utf-8-sig"))
    spec = scenario(metadata["scenario"])
    if metadata["platform"] != "macos" or not spec["showrun_macos"]:
        raise ValueError("This scenario/platform is outside the current Showrun native backend")
    if ready["run_id"] != metadata["run_id"]:
        raise ValueError("Ready window belongs to a different run")
    recipes = {
        "save-task": [
            ("save", "Enter Example task in Task name and click Save task.", "Saved task: Example task")
        ],
        "details": [
            ("reveal", "Click More options.", "Details shown"),
            ("note", "Enter Release note in Detail note and click Save note.", "Saved note: Release note"),
        ],
        "delayed-save": [
            (
                "delay",
                "Enter Delayed task, click Start delayed save, and wait for completion.",
                "Delayed save completed",
            )
        ],
        "replace-control": [
            (
                "move",
                "Enter Relocated task in Task name, then click Move Save button.",
                "Save button moved and replaced",
            ),
            ("save", "Find the new Save task control and click it.", "Saved task: Relocated task"),
        ],
    }
    return {
        "request_id": request_id,
        "target": {"kind": "macos", "bundle_id": ready["bundle_id"], "window_title": ready["window_title"]},
        "starting_state": "Ready",
        "capture": {"width": ready["width"], "height": ready["height"]},
        "steps": [
            {"id": ident, "instruction": instruction, "visible_text": visible, "hold_seconds": 3}
            for ident, instruction, visible in recipes[spec["id"]]
        ],
        "authority": {
            "navigation_only": False,
            "disclose_dom": False,
            "disclose_accessibility": True,
            "disclose_screenshots": True,
            "max_seconds": 120,
            "max_actions": 20,
            "max_model_calls": 12,
            "ui": {
                "actions": ["click", "fill"],
                "allowed_values": ["Example task", "Release note", "Delayed task", "Relocated task"],
                "target_effects": "all_in_session",
            },
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--platform", choices=["macos", "windows", "linux"], default=PLATFORM)
    build_parser.add_argument("--output", type=Path, required=True)
    build_parser.add_argument("--gtk-python", default="/usr/bin/python3")
    for operation in ("launch", "self-test"):
        command = sub.add_parser(operation)
        command.add_argument("--build", type=Path, required=True, help="Platform JSON produced by build")
        command.add_argument("--run", type=Path, required=True, help="New, absent output directory")
        command.add_argument("--scenario", choices=[s["id"] for s in scenarios()], default="save-task")
    read = sub.add_parser("verify")
    read.add_argument("--run", type=Path, required=True)
    read.add_argument("--scenario", choices=[s["id"] for s in scenarios()], required=True)
    request = sub.add_parser("showrun-request")
    request.add_argument("--run", type=Path, required=True)
    request.add_argument("--request-id", required=True)
    sub.add_parser("scenarios")
    args = parser.parse_args()
    try:
        if args.operation == "build":
            result = build(args.platform, args.output, args.gtk_python)
        elif args.operation in {"launch", "self-test"}:
            descriptor = json.loads(args.build.read_text(encoding="utf-8"))
            result = launch(descriptor, args.run.resolve(), args.scenario, args.operation == "self-test")
        elif args.operation == "verify":
            result = verify(args.run, args.scenario)
        elif args.operation == "showrun-request":
            result = showrun_request(args.run, args.request_id)
        else:
            result = scenarios()
        print(json.dumps(result, indent=2))
        return 1 if isinstance(result, dict) and result.get("status") == "failed" else 0
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
