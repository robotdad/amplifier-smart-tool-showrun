"""Fixture ground truth and verifier failure paths; no model/desktop required."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from fixtures.computer_use.harness import scenario, showrun_request, verify
from fixtures.computer_use.model import Model

ROOT = Path(__file__).resolve().parents[1] / "fixtures/computer_use"


@pytest.fixture
def fixture_model():
    clock = [100.0]
    return Model("trial-identity", "linux", clock=lambda: clock[0], wall=lambda: clock[0]), clock


def write_run(path, state, name):
    (path / "state.json").write_text(json.dumps(state))
    (path / "run.json").write_text(
        json.dumps({"run_id": "trial-identity", "platform": "linux", "scenario": name})
    )


@pytest.mark.parametrize(
    "name", ["save-task", "details", "dialog", "delayed-save", "cancel-save", "replace-control"]
)
def test_scenario_state_and_independent_verification(tmp_path, fixture_model, name):
    model, clock = fixture_model
    if name == "details":
        model.apply("show_details")
        model.apply("set_note", "Release note")
        model.apply("save_note")
    else:
        value = {
            "save-task": "Example task",
            "dialog": "Review task",
            "delayed-save": "Delayed task",
            "cancel-save": "Cancelled task",
            "replace-control": "Relocated task",
        }[name]
        model.apply("set_draft", value)
        if name == "dialog":
            model.apply("open_dialog")
            model.apply("confirm_dialog")
        elif name in {"delayed-save", "cancel-save"}:
            model.apply("start_delay")
            if name == "cancel-save":
                model.apply("cancel_delay")
            clock[0] += 4
            model.apply("tick")
        else:
            if name == "replace-control":
                model.apply("move_save")
            model.apply("save")
    write_run(tmp_path, model.snapshot(), name)
    assert verify(tmp_path, name, now=clock[0])["status"] == "passed"
    # A copied final screen/state without action history cannot pass.
    forged = model.snapshot()
    forged["events"] = []
    forged["revision"] = 0
    write_run(tmp_path, forged, name)
    assert verify(tmp_path, name, now=clock[0])["status"] == "failed"


def test_cancel_verification_waits_for_deadline(tmp_path, fixture_model):
    model, clock = fixture_model
    model.apply("set_draft", "Cancelled task")
    model.apply("start_delay")
    model.apply("cancel_delay")
    write_run(tmp_path, model.snapshot(), "cancel-save")
    assert verify(tmp_path, "cancel-save", now=clock[0])["status"] == "failed"
    clock[0] += 4
    model.apply("tick")
    write_run(tmp_path, model.snapshot(), "cancel-save")
    assert verify(tmp_path, "cancel-save", now=clock[0])["status"] == "passed"


def test_delayed_save_captures_value_at_start(fixture_model):
    model, clock = fixture_model
    model.apply("set_draft", "Original")
    model.apply("start_delay")
    model.apply("set_draft", "Later")
    clock[0] += 4
    model.apply("tick")
    assert model.snapshot()["saved_task"] == "Original"
    assert model.snapshot()["draft"] == "Later"


def test_repeated_effect_and_wrong_run_cannot_pass(tmp_path, fixture_model):
    model, _ = fixture_model
    model.apply("set_draft", "Example task")
    model.apply("save")
    model.apply("save")
    state = model.snapshot()
    write_run(tmp_path, state, "save-task")
    assert verify(tmp_path, "save-task")["status"] == "failed"
    state["events"] = state["events"][:2]
    state["revision"] = 2
    state["run_id"] = "another-run"
    write_run(tmp_path, state, "save-task")
    assert verify(tmp_path, "save-task")["status"] == "failed"


def test_disabled_operations_and_snapshots(fixture_model):
    model, _ = fixture_model
    for action in ("save", "save_note", "confirm_dialog", "cancel_delay", "complete_delay"):
        with pytest.raises(ValueError):
            model.apply(action)
    original = copy.deepcopy(model.state)
    model.apply("snapshot")["events"].append({"kind": "fake"})
    assert model.state == original


def test_state_host_persists_and_refuses_reused_output(tmp_path):
    path = tmp_path / "state.json"
    command = [
        sys.executable,
        str(ROOT / "state_host.py"),
        "--state",
        str(path),
        "--run-id",
        "trial",
        "--platform",
        "linux",
    ]
    result = subprocess.run(
        command,
        input='{"action":"set_draft","value":"Example"}\n{"action":"save"}\n',
        text=True,
        capture_output=True,
        check=True,
    )
    assert len(result.stdout.splitlines()) == 2
    state = json.loads(path.read_text())
    assert state["saved_task"] == "Example"
    repeated = subprocess.run(command, input="", text=True, capture_output=True)
    assert repeated.returncode != 0 and json.loads(path.read_text()) == state


def test_showrun_recipe_is_valid_and_unsupported_scenarios_fail(tmp_path):
    from amplifier_smart_tool_showrun import Showrun

    (tmp_path / "ready.json").write_text(
        json.dumps(
            {
                "run_id": "trial",
                "bundle_id": "org.showrun.ComputerUseFixture",
                "window_title": "Fixture",
                "width": 1360,
                "height": 984,
            }
        )
    )
    for name in ["save-task", "details", "dialog", "delayed-save", "cancel-save", "replace-control"]:
        (tmp_path / "run.json").write_text(
            json.dumps({"run_id": "trial", "platform": "macos", "scenario": name})
        )
        if scenario(name)["showrun_macos"]:
            assert Showrun().validate(showrun_request(tmp_path, "test"))["status"] == "valid"
        else:
            with pytest.raises(ValueError):
                showrun_request(tmp_path, "test")
