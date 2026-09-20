"""External AT-SPI driver for isolated fixture trials; never calls its state host.

Run in a dedicated desktop/DBus session. Each scenario launches a fresh app,
finds only that child's PID, operates GTK accessibility interfaces, then reads
the independent fixture state through the common verifier. No model or video.
"""

import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pyatspi
from gi.repository import GLib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness import ROOT, scenarios, verify  # noqa: E402


def wait(predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise TimeoutError("Fixture UI did not reach the expected state")


def find(app, name, role=None):
    # Polling without a GLib event loop otherwise retains stale child lists when
    # a dialog opens or a control is replaced.
    app.clear_cache()

    def walk(node, depth=0):
        if depth > 40:
            return None
        if node.name == name and (role is None or node.getRoleName() == role):
            return node
        for child in node:
            if child is not None:
                found = walk(child, depth + 1)
                if found:
                    return found
        return None

    return walk(app)


def trial(output, spec):
    run = output / spec["id"]
    run.mkdir(parents=True, exist_ok=False)
    run_id = str(uuid.uuid4())
    (run / "run.json").write_text(
        json.dumps(
            {"run_id": run_id, "platform": "linux", "scenario": spec["id"], "mode": "external-atspi-trial"}
        )
    )
    argv = [
        sys.executable,
        str(ROOT / "linux/app.py"),
        "--python",
        sys.executable,
        "--host",
        str(ROOT / "state_host.py"),
        "--state",
        str(run / "state.json"),
        "--ready",
        str(run / "ready.json"),
        "--run-id",
        run_id,
    ]
    with (run / "app.log").open("w") as log:
        process = subprocess.Popen(argv, stdout=log, stderr=log)
        try:
            wait(lambda: (run / "ready.json").exists())

            def application():
                return next(
                    (a for a in pyatspi.Registry.getDesktop(0) if a and a.get_process_id() == process.pid),
                    None,
                )

            app = wait(application)

            def click(name):
                def enabled():
                    control = find(app, name, "push button")
                    return control if control and control.getState().contains(pyatspi.STATE_ENABLED) else None

                control = wait(enabled)
                if not control.queryAction().doAction(0):
                    raise AssertionError("AT-SPI rejected action: " + name)

            def fill(name, value):
                control = wait(lambda: find(app, name, "text"))
                if not control.queryEditableText().setTextContents(value):
                    raise AssertionError("AT-SPI rejected text: " + name)

            save = wait(lambda: find(app, "Save task", "push button"))
            assert not save.getState().contains(pyatspi.STATE_ENABLED)
            name = spec["id"]
            if name == "details":
                click("More options")
                fill("Detail note", "Release note")
                click("Save note")
            else:
                value = {
                    "save-task": "Example task",
                    "dialog": "Review task",
                    "delayed-save": "Delayed task",
                    "cancel-save": "Cancelled task",
                    "replace-control": "Relocated task",
                }[name]
                fill("Task name", value)
                if name == "dialog":
                    click("Open dialog")
                    click("Confirm")
                elif name in {"delayed-save", "cancel-save"}:
                    click("Start delayed save")
                    if name == "cancel-save":
                        click("Cancel operation")
                    time.sleep(3.3)
                else:
                    if name == "replace-control":
                        click("Move Save button")
                        # Reacquire the new accessible object; never reuse `save`.
                    click("Save task")
            result = wait(lambda: r if (r := verify(run, name))["status"] == "passed" else None)
            result["driver"] = "external AT-SPI accessibility actions against the exact fixture PID"
            result["limitations"] = [
                "No Showrun Linux backend, model inference, or video capture was exercised."
            ]
            (run / "result.json").write_text(json.dumps(result, indent=2))
            return result
        except Exception:
            if "app" in locals():
                lines = []

                def describe(node, depth=0):
                    if depth > 20 or len(lines) > 200:
                        return
                    lines.append("  " * depth + node.getRoleName() + ": " + node.name)
                    for child in node:
                        if child is not None:
                            describe(child, depth + 1)

                try:
                    describe(app)
                except Exception as error:
                    lines.append("Accessible-tree read failed: " + str(error))
                (run / "accessibility-failure.txt").write_text("\n".join(lines))
            raise
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario", choices=[s["id"] for s in scenarios()])
    args = parser.parse_args()
    results = [
        trial(args.output, spec) for spec in scenarios() if not args.scenario or spec["id"] == args.scenario
    ]
    print(json.dumps({"status": "passed", "trials": results}, indent=2))


if __name__ == "__main__":
    main()
