"""Fixture application data model, shared by three native UI adapters.

This is the application's private backend, not an automation interface. Trials
must operate the UI; the harness only reads the persisted snapshot afterward.
"""

import copy
import time


class Model:
    def __init__(self, run_id, platform, clock=time.monotonic, wall=time.time):
        self.clock, self.wall = clock, wall
        self.deadline = None
        self.pending = None
        self.state = {
            "schema_version": 1,
            "run_id": run_id,
            "platform": platform,
            "revision": 0,
            "draft": "",
            "note_draft": "",
            "saved_task": "",
            "saved_note": "",
            "confirmed_task": "",
            "details_visible": False,
            "dialog_status": "closed",
            "operation_status": "idle",
            "operation_not_before": None,
            "save_control_generation": 0,
            "status_text": "Ready",
            "events": [],
        }

    def apply(self, action, value=None):
        state = self.state
        if action == "snapshot":
            return self.snapshot()
        if action == "tick":
            if self.deadline is None or self.clock() < self.deadline:
                return self.snapshot()
            action, value = "complete_delay", self.pending
        if action in {"set_draft", "set_note"}:
            if not isinstance(value, str) or len(value) > 100:
                raise ValueError("Input must be at most 100 characters")
            key = "draft" if action == "set_draft" else "note_draft"
            if state[key] == value:
                return self.snapshot()
            state[key] = value
        elif action == "save":
            if not state["draft"].strip():
                raise ValueError("Save is disabled for an empty task")
            state["saved_task"] = state["draft"]
            state["status_text"] = "Saved task: " + state["saved_task"]
        elif action == "show_details":
            state["details_visible"] = not state["details_visible"]
            state["status_text"] = "Details shown" if state["details_visible"] else "Details hidden"
        elif action == "save_note":
            if not state["details_visible"] or not state["note_draft"].strip():
                raise ValueError("Detail note is unavailable or empty")
            state["saved_note"] = state["note_draft"]
            state["status_text"] = "Saved note: " + state["saved_note"]
        elif action == "open_dialog":
            if state["dialog_status"] == "open":
                raise ValueError("Dialog is already open")
            state["dialog_status"] = "open"
        elif action in {"confirm_dialog", "cancel_dialog"}:
            if state["dialog_status"] != "open":
                raise ValueError("No dialog is open")
            state["dialog_status"] = "confirmed" if action == "confirm_dialog" else "cancelled"
            if action == "confirm_dialog":
                state["confirmed_task"] = state["draft"]
            state["status_text"] = "Dialog " + state["dialog_status"]
        elif action == "start_delay":
            if self.deadline is not None or not state["draft"].strip():
                raise ValueError("Operation is already pending or task is empty")
            self.pending, self.deadline = state["draft"], self.clock() + 3
            state["operation_not_before"] = self.wall() + 3
            state["operation_status"], state["status_text"] = "pending", "Saving in three seconds"
        elif action == "cancel_delay":
            if self.deadline is None:
                raise ValueError("No operation is pending")
            self.deadline, self.pending = None, None
            state["operation_status"], state["status_text"] = "cancelled", "Operation cancelled"
        elif action == "complete_delay":
            if self.deadline is None or self.clock() < self.deadline:
                raise ValueError("Operation is not ready")
            state["saved_task"] = self.pending
            self.deadline, self.pending = None, None
            state["operation_status"], state["status_text"] = "completed", "Delayed save completed"
        elif action == "move_save":
            state["save_control_generation"] += 1
            state["status_text"] = "Save button moved and replaced"
        else:
            raise ValueError("Unknown fixture action")
        state["revision"] += 1
        state["events"].append({"seq": state["revision"], "kind": action})
        if len(state["events"]) > 2000:
            raise ValueError("Fixture event budget exhausted")
        return self.snapshot()

    def snapshot(self):
        return copy.deepcopy(self.state)
