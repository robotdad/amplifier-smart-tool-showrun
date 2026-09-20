"""GTK 3 native fixture. Run through ../harness.py with the system GTK Python."""

import argparse
import json
import os
import subprocess
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402


class Fixture:
    def __init__(self, args):
        self.args = args
        self.host = subprocess.Popen(
            [args.python, args.host, "--state", args.state, "--run-id", args.run_id, "--platform", "linux"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self.generation = -1
        self.window = Gtk.Window(title="Showrun Fixture - " + args.run_id[:8])
        self.window.set_default_size(780, 460)
        self.window.connect("destroy", Gtk.main_quit)
        self.grid = Gtk.Grid(column_spacing=16, row_spacing=12, margin=24)
        self.window.add(self.grid)
        self.grid.attach(Gtk.Label(label="Computer-use fixture · fictional data only"), 0, 0, 3, 1)
        self.grid.attach(Gtk.Label(label="Task name", xalign=0), 0, 1, 1, 1)
        self.draft = self.entry("Task name", 0, 2)
        self.draft.connect("changed", lambda entry: self.act("set_draft", entry.get_text()))
        self.button("More options", 0, 3, lambda _: self.act("show_details"))
        self.button("Open dialog", 1, 3, self.dialog)
        self.button("Move Save button", 2, 3, lambda _: self.act("move_save"))
        self.note_label = Gtk.Label(label="Detail note", xalign=0)
        self.grid.attach(self.note_label, 0, 4, 1, 1)
        self.note = self.entry("Detail note", 0, 5)
        self.note.connect("changed", lambda entry: self.act("set_note", entry.get_text()))
        self.save_note = self.button("Save note", 2, 5, lambda _: self.act("save_note"))
        self.start = self.button("Start delayed save", 0, 6, lambda _: self.act("start_delay"))
        self.cancel = self.button("Cancel operation", 1, 6, lambda _: self.act("cancel_delay"))
        self.saved = Gtk.Label(label="Saved task: none", xalign=0)
        self.status = Gtk.Label(label="Ready", xalign=0)
        self.grid.attach(self.saved, 0, 8, 3, 1)
        self.grid.attach(self.status, 0, 9, 3, 1)
        self.window.show_all()
        self.act("snapshot")
        ready = {
            "run_id": args.run_id,
            "platform": "linux",
            "window_title": self.window.get_title(),
            "pid": os.getpid(),
            "toolkit": "GTK 3",
        }
        Path(args.ready).write_text(json.dumps(ready), encoding="utf-8")
        self.timer = GLib.timeout_add(100, self.tick)
        if args.self_test:
            GLib.timeout_add(200, self.test_widgets)

    def entry(self, name, column, row):
        widget = Gtk.Entry(max_length=100)
        widget.get_accessible().set_name(name)
        self.grid.attach(widget, column, row, 2, 1)
        return widget

    def button(self, name, column, row, callback):
        widget = Gtk.Button(label=name)
        widget.get_accessible().set_name(name)
        widget.connect("clicked", callback)
        self.grid.attach(widget, column, row, 1, 1)
        return widget

    def act(self, action, value=None):
        self.host.stdin.write(json.dumps({"action": action, "value": value}) + "\n")
        self.host.stdin.flush()
        reply = json.loads(self.host.stdout.readline())
        if "error" in reply:
            raise RuntimeError(reply["error"])
        self.state = reply["state"]
        state = self.state
        if self.generation != state["save_control_generation"]:
            if self.generation >= 0:
                self.grid.remove(self.save)
            self.generation = state["save_control_generation"]
            self.save = self.button(
                "Save task", 2, 2 if self.generation % 2 == 0 else 7, lambda _: self.act("save")
            )
            self.save.show()
        self.save.set_sensitive(bool(state["draft"].strip()))
        for widget in (self.note_label, self.note, self.save_note):
            widget.set_visible(state["details_visible"])
        self.save_note.set_sensitive(bool(state["note_draft"].strip()))
        self.start.set_sensitive(bool(state["draft"].strip()) and state["operation_status"] != "pending")
        self.cancel.set_sensitive(state["operation_status"] == "pending")
        self.saved.set_text("Saved task: " + (state["saved_task"] or "none"))
        self.status.set_text(state["status_text"])

    def tick(self):
        self.act("tick")
        return True

    def dialog(self, unused):
        self.act("open_dialog")
        dialog = Gtk.MessageDialog(transient_for=self.window, modal=True, text="Confirm task")
        dialog.format_secondary_text(self.draft.get_text())
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Confirm", Gtk.ResponseType.OK)
        if self.args.self_test:

            def confirm():
                dialog.get_widget_for_response(Gtk.ResponseType.OK).emit("clicked")
                return False

            GLib.timeout_add(200, confirm)

        def respond(widget, result):
            widget.destroy()
            self.act("confirm_dialog" if result == Gtk.ResponseType.OK else "cancel_dialog")

        # Return to GTK's main loop so external accessibility requests can reach
        # the dialog while it is open, instead of nesting a blocking dialog.run.
        dialog.connect("response", respond)
        dialog.show_all()

    def test_widgets(self):
        assert not self.save.get_sensitive()
        self.draft.set_text("Widget trial")
        self.save.emit("clicked")
        assert self.state["saved_task"] == "Widget trial"
        self.act("move_save")
        self.save.emit("clicked")
        self.act("show_details")
        self.note.set_text("Widget note")
        self.save_note.emit("clicked")
        self.dialog(None)

        def confirmed():
            assert self.state["dialog_status"] == "confirmed"
            self.start.emit("clicked")
            GLib.timeout_add(3400, complete_then_cancel)
            return False

        def complete_then_cancel():
            assert self.state["operation_status"] == "completed"
            self.start.emit("clicked")
            self.cancel.emit("clicked")
            GLib.timeout_add(3400, finish)
            return False

        def finish():
            assert self.state["operation_status"] == "cancelled"
            window = self.window.get_window()
            width, height = self.window.get_size()
            pixels = Gdk.pixbuf_get_from_window(window, 0, 0, width, height)
            pixels.savev(str(Path(self.args.ready).with_name("widget-preview.png")), "png", [], [])
            self.window.destroy()
            return False

        GLib.timeout_add(400, confirmed)
        return False

    def close(self):
        GLib.source_remove(self.timer)
        self.host.stdin.close()
        self.host.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser()
    for name in ("python", "host", "state", "run-id", "ready"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--self-test", action="store_true")
    fixture = Fixture(parser.parse_args())
    try:
        Gtk.main()
    finally:
        fixture.close()


if __name__ == "__main__":
    main()
