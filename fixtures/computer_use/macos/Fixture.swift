import AppKit

final class StateHost {
    let process = Process()
    let input = Pipe(), output = Pipe()
    var buffer = Data()
    init(_ args: [String: String]) throws {
        process.executableURL = URL(fileURLWithPath: args["python"]!)
        process.arguments = [args["host"]!, "--state", args["state"]!, "--run-id", args["run-id"]!, "--platform", "macos"]
        process.standardInput = input; process.standardOutput = output
        try process.run()
    }
    func call(_ action: String, _ value: String? = nil) -> [String: Any] {
        do {
            var request: [String: Any] = ["action": action]
            if let value { request["value"] = value }
            var data = try JSONSerialization.data(withJSONObject: request); data.append(10)
            try input.fileHandleForWriting.write(contentsOf: data)
            while !buffer.contains(10) {
                let next = output.fileHandleForReading.availableData
                if next.isEmpty { throw NSError(domain: "State host disconnected", code: 1) }
                buffer.append(next)
            }
            let end = buffer.firstIndex(of: 10)!
            let line = buffer.prefix(upTo: end); buffer.removeSubrange(...end)
            let reply = try JSONSerialization.jsonObject(with: line) as! [String: Any]
            guard let state = reply["state"] as? [String: Any] else {
                throw NSError(domain: reply["error"] as? String ?? "State failure", code: 1)
            }
            return state
        } catch { fputs("Fixture state failure: \(error)\n", stderr); exit(1) }
    }
    func close() { try? input.fileHandleForWriting.close(); process.waitUntilExit() }
}

final class Fixture: NSObject, NSApplicationDelegate, NSTextFieldDelegate {
    var host: StateHost!
    var window: NSWindow!
    var draft = NSTextField(), note = NSTextField()
    var status = NSTextField(labelWithString: "Ready")
    var saved = NSTextField(labelWithString: "Saved task: none")
    var save: NSButton!, saveNote: NSButton!, start: NSButton!, cancel: NSButton!
    var noteLabel = NSTextField(labelWithString: "Detail note")
    var generation = -1
    var state: [String: Any] = [:]
    var timer: Timer?
    var args: [String: String] = [:]
    var selfTest = false

    func label(_ text: String, _ x: CGFloat, _ y: CGFloat) {
        let label = NSTextField(labelWithString: text)
        label.frame = NSRect(x: x, y: y, width: 580, height: 24)
        window.contentView!.addSubview(label)
    }
    func button(_ title: String, _ x: CGFloat, _ y: CGFloat, _ selector: Selector) -> NSButton {
        let result = NSButton(title: title, target: self, action: selector)
        result.frame = NSRect(x: x, y: y, width: 180, height: 32)
        result.bezelStyle = .rounded
        result.setAccessibilityLabel(title)
        window.contentView!.addSubview(result)
        return result
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        let values = Array(CommandLine.arguments.dropFirst())
        var i = 0
        while i < values.count {
            if values[i] == "--self-test" { selfTest = true; i += 1; continue }
            guard i + 1 < values.count else { exit(2) }
            args[String(values[i].dropFirst(2))] = values[i + 1]; i += 2
        }
        do { host = try StateHost(args) } catch { fputs("Cannot start fixture host\n", stderr); exit(1) }
        window = NSWindow(contentRect: NSRect(x: 100, y: 100, width: 680, height: 470),
                          styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "Showrun Fixture - " + String(args["run-id"]!.prefix(8))
        window.appearance = NSAppearance(named: .aqua)
        window.contentView!.wantsLayer = true
        window.contentView!.layer!.backgroundColor = NSColor(white: 0.95, alpha: 1).cgColor
        label("Computer-use fixture · fictional data only", 24, 425)
        label("Task name", 24, 389)
        draft.frame = NSRect(x: 24, y: 353, width: 380, height: 28)
        draft.delegate = self; draft.setAccessibilityLabel("Task name")
        window.contentView!.addSubview(draft)
        _ = button("More options", 24, 295, #selector(details))
        _ = button("Open dialog", 220, 295, #selector(dialog))
        _ = button("Move Save button", 416, 295, #selector(move))
        noteLabel.frame = NSRect(x: 24, y: 259, width: 200, height: 24)
        window.contentView!.addSubview(noteLabel)
        note.frame = NSRect(x: 24, y: 225, width: 380, height: 28)
        note.delegate = self; note.setAccessibilityLabel("Detail note")
        window.contentView!.addSubview(note)
        saveNote = button("Save note", 430, 222, #selector(saveDetail))
        start = button("Start delayed save", 24, 162, #selector(delay))
        cancel = button("Cancel operation", 220, 162, #selector(cancelDelay))
        saved.frame = NSRect(x: 24, y: 100, width: 620, height: 28)
        status.frame = NSRect(x: 24, y: 55, width: 620, height: 28)
        window.contentView!.addSubview(saved); window.contentView!.addSubview(status)
        render(host.call("snapshot"))
        window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        let scale = window.backingScaleFactor
        let ready: [String: Any] = ["run_id": args["run-id"]!, "platform": "macos",
            "bundle_id": Bundle.main.bundleIdentifier ?? "org.showrun.ComputerUseFixture",
            "window_title": window.title, "width": Int(window.frame.width * scale),
            "height": Int(window.frame.height * scale), "pid": ProcessInfo.processInfo.processIdentifier]
        do { try JSONSerialization.data(withJSONObject: ready).write(to: URL(fileURLWithPath: args["ready"]!), options: .atomic) }
        catch { exit(1) }
        timer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            guard let self else { return }; self.syncInputs(); self.render(self.host.call("tick"))
        }
        if selfTest { DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { self.testWidgets() } }
    }
    func render(_ value: [String: Any]) {
        state = value
        let next = value["save_control_generation"] as! Int
        if next != generation {
            save?.removeFromSuperview()
            save = button("Save task", next % 2 == 0 ? 430 : 480, next % 2 == 0 ? 350 : 110, #selector(saveTask))
            generation = next
        }
        let hasDraft = !(value["draft"] as! String).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        save.isEnabled = hasDraft
        let visible = value["details_visible"] as! Bool
        note.isHidden = !visible; noteLabel.isHidden = !visible; saveNote.isHidden = !visible
        saveNote.isEnabled = !(value["note_draft"] as! String).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        let pending = value["operation_status"] as! String == "pending"
        start.isEnabled = hasDraft && !pending; cancel.isEnabled = pending
        status.stringValue = value["status_text"] as! String
        saved.stringValue = "Saved task: " + ((value["saved_task"] as! String).isEmpty ? "none" : value["saved_task"] as! String)
    }
    func controlTextDidChange(_ notification: Notification) {
        guard let field = notification.object as? NSTextField else { return }
        field.stringValue = String(field.stringValue.prefix(100))
        render(host.call(field === draft ? "set_draft" : "set_note", field.stringValue))
    }
    func syncInputs() {
        // Accessibility setValue need not emit the keyboard editing notification.
        // Reconcile real widget values before commands and on the ordinary UI timer.
        if draft.stringValue != state["draft"] as? String {
            draft.stringValue = String(draft.stringValue.prefix(100)); render(host.call("set_draft", draft.stringValue))
        }
        if note.stringValue != state["note_draft"] as? String {
            note.stringValue = String(note.stringValue.prefix(100)); render(host.call("set_note", note.stringValue))
        }
    }
    @objc func saveTask() {
        syncInputs(); render(host.call("save"))
        if let title = args["title-after-save"] { window.title = title }
    }
    @objc func details() { render(host.call("show_details")) }
    @objc func saveDetail() { syncInputs(); render(host.call("save_note")) }
    @objc func move() { render(host.call("move_save")) }
    @objc func delay() { syncInputs(); render(host.call("start_delay")) }
    @objc func cancelDelay() { render(host.call("cancel_delay")) }
    @objc func dialog() {
        syncInputs()
        render(host.call("open_dialog"))
        let alert = NSAlert(); alert.messageText = "Confirm task"; alert.informativeText = draft.stringValue
        alert.addButton(withTitle: "Confirm"); alert.addButton(withTitle: "Cancel")
        alert.beginSheetModal(for: window) { response in
            self.render(self.host.call(response == .alertFirstButtonReturn ? "confirm_dialog" : "cancel_dialog"))
        }
        if selfTest { DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { alert.buttons[0].performClick(nil) } }
    }
    func testWidgets() {
        guard !save.isEnabled else { exit(3) }
        draft.stringValue = "Widget trial"
        controlTextDidChange(Notification(name: NSControl.textDidChangeNotification, object: draft))
        save.performClick(nil)
        guard state["saved_task"] as? String == "Widget trial" else { exit(3) }
        move(); save.performClick(nil)
        details(); note.stringValue = "Widget note"
        controlTextDidChange(Notification(name: NSControl.textDidChangeNotification, object: note))
        saveNote.performClick(nil); dialog()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            guard self.state["dialog_status"] as? String == "confirmed" else { exit(3) }
            self.delay()
            DispatchQueue.main.asyncAfter(deadline: .now() + 3.4) {
                guard self.state["operation_status"] as? String == "completed" else { exit(3) }
                self.delay(); self.cancel.performClick(nil)
                DispatchQueue.main.asyncAfter(deadline: .now() + 3.4) { self.finishTest() }
            }
        }
    }
    func finishTest() {
            guard self.state["operation_status"] as? String == "cancelled" else { exit(3) }
            if let view = self.window.contentView,
               let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) {
                view.cacheDisplay(in: view.bounds, to: rep)
                let path = URL(fileURLWithPath: self.args["ready"]!).deletingLastPathComponent().appendingPathComponent("widget-preview.png")
                try? rep.representation(using: .png, properties: [:])?.write(to: path)
            }
            NSApp.terminate(nil)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) { timer?.invalidate(); host?.close() }
}

@main struct Main {
    static func main() {
        let app = NSApplication.shared
        let delegate = Fixture(); app.delegate = delegate
        app.setActivationPolicy(.regular); app.run()
    }
}
