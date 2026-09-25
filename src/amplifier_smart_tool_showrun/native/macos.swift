// Fixed JSON-line bridge: one bound window, ScreenCaptureKit and Accessibility.
// No shell, arbitrary code, model coordinates/keys, or app launch. The only
// permission prompt is the explicit request_microphone setup operation.
import AppKit
import ApplicationServices
import AVFoundation
import CoreMedia
import ScreenCaptureKit

struct BridgeError: Error {
    let code: String
    let diagnostics: [String: Any]
}
func fail(_ code: String, _ diagnostics: [String: Any] = [:]) throws -> Never {
    throw BridgeError(code: code, diagnostics: diagnostics)
}
func attr(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
    return value
}
func string(_ element: AXUIElement, _ name: String) -> String {
    return attr(element, name) as? String ?? ""
}
// Rich native editors may expose text through the parameterized text API only.
func editorText(_ element: AXUIElement) -> String? {
    if let value = attr(element, kAXValueAttribute) as? String { return value }
    guard let count = attr(element, kAXNumberOfCharactersAttribute) as? NSNumber,
          count.intValue >= 0, count.intValue <= 16000 else { return nil }
    if count.intValue == 0 { return "" }
    var range = CFRange(location: 0, length: count.intValue)
    guard let parameter = AXValueCreate(.cfRange, &range) else { return nil }
    var value: CFTypeRef?
    guard AXUIElementCopyParameterizedAttributeValue(element, kAXStringForRangeParameterizedAttribute as CFString,
                                                     parameter, &value) == .success else { return nil }
    return value as? String
}
func rectangle(_ element: AXUIElement) -> CGRect? {
    guard let p = attr(element, kAXPositionAttribute), let s = attr(element, kAXSizeAttribute),
          CFGetTypeID(p) == AXValueGetTypeID(), CFGetTypeID(s) == AXValueGetTypeID() else { return nil }
    var point = CGPoint.zero, size = CGSize.zero
    guard AXValueGetValue(p as! AXValue, .cgPoint, &point),
          AXValueGetValue(s as! AXValue, .cgSize, &size) else { return nil }
    return CGRect(origin: point, size: size)
}

@available(macOS 14.0, *)
final class Bridge {
    var window: SCWindow?
    var root: AXUIElement?
    var app: AXUIElement?
    var bundle = "", title = ""
    var refs: [String: AXUIElement] = [:]
    var generation = 0
    var terminal = false
    var terminalFocus: AXUIElement?

    func bind(_ request: [String: Any]) async throws -> [String: Any] {
        guard CGPreflightScreenCaptureAccess(), AXIsProcessTrusted() else {
            try fail("desktop_permission_missing")
        }
        guard let b = request["bundle_id"] as? String else {
            try fail("invalid_request")
        }
        let requestedTitle = request["window_title"] as? String
        bundle = b
        terminal = request["input_mode"] as? String == "terminal"
        let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true)
        let candidates = content.windows.filter {
            $0.owningApplication?.bundleIdentifier == b && $0.windowLayer == 0
                && !$0.frame.isEmpty
        }
        let matches = candidates.filter { requestedTitle == nil || $0.title == requestedTitle }
        // Requested app only; titles can contain terminal prompts or private paths.
        // Return numeric selection hints, not titles, other apps, or screen contents.
        var diagnostics: [String: Any] = [
            "candidate_count": candidates.count, "match_count": matches.count,
            "candidates": candidates.prefix(8).map {
                ["window_id": Int($0.windowID), "width": Int($0.frame.width),
                 "height": Int($0.frame.height),
                 "title_matches": requestedTitle == nil || $0.title == requestedTitle] as [String: Any]
            }
        ]
        guard !matches.isEmpty else { try fail("desktop_window_not_found", diagnostics) }
        guard matches.count == 1, let selected = matches.first,
              let owner = selected.owningApplication else { try fail("desktop_window_ambiguous", diagnostics) }
        let t = selected.title ?? ""
        title = t
        let application = AXUIElementCreateApplication(owner.processID)
        let windows = attr(application, kAXWindowsAttribute) as? [AXUIElement] ?? []
        let targets = windows.filter { string($0, kAXTitleAttribute) == t }
        diagnostics["ax_window_count"] = windows.count
        diagnostics["ax_match_count"] = targets.count
        guard !targets.isEmpty else { try fail("desktop_ax_window_not_found", diagnostics) }
        guard targets.count == 1 else { try fail("desktop_ax_window_ambiguous", diagnostics) }
        window = selected; root = targets[0]; app = application
        return ["status": "ready", "window_id": selected.windowID, "pid": owner.processID, "window_title": t]
    }

    func check() throws {
        guard let root, let app, let window,
              (terminal || string(root, kAXTitleAttribute) == title),
              let running = NSRunningApplication(processIdentifier: window.owningApplication!.processID),
              running.bundleIdentifier == bundle, !running.isTerminated,
              (attr(app, kAXWindowsAttribute) as? [AXUIElement] ?? []).contains(where: { CFEqual($0, root) }),
              (attr(root, kAXMinimizedAttribute) as? Bool) != true else {
            try fail("desktop_surface_changed")
        }
    }

    func rejectSensitive(_ element: AXUIElement, _ remaining: inout Int, _ depth: Int = 0) throws {
        remaining -= 1
        guard remaining >= 0, depth <= 40 else { try fail("desktop_observation_limit") }
        if string(element, kAXSubroleAttribute) == kAXSecureTextFieldSubrole { try fail("sensitive_surface") }
        for child in attr(element, kAXChildrenAttribute) as? [AXUIElement] ?? [] {
            try rejectSensitive(child, &remaining, depth + 1)
        }
    }

    func keyboardEditor(_ element: AXUIElement) -> Bool {
        var writable = DarwinBoolean(false)
        _ = AXUIElementIsAttributeSettable(element, kAXValueAttribute as CFString, &writable)
        return string(element, kAXRoleAttribute) == kAXTextAreaRole && !writable.boolValue
    }

    func supportsFill(_ element: AXUIElement) -> Bool {
        let role = string(element, kAXRoleAttribute)
        guard [kAXTextFieldRole, kAXTextAreaRole, kAXComboBoxRole].contains(role),
              string(element, kAXSubroleAttribute) != kAXSecureTextFieldSubrole else { return false }
        var writable = DarwinBoolean(false), focusable = DarwinBoolean(false)
        _ = AXUIElementIsAttributeSettable(element, kAXValueAttribute as CFString, &writable)
        _ = AXUIElementIsAttributeSettable(element, kAXFocusedAttribute as CFString, &focusable)
        return writable.boolValue || (role == kAXTextAreaRole && focusable.boolValue
            )
    }

    func walk(_ element: AXUIElement, _ bounds: CGRect, _ depth: Int,
              _ count: inout Int, _ texts: inout [String], _ controls: inout [[String: Any]]) throws {
        count += 1
        guard count <= 2000, depth <= 40 else { try fail("desktop_observation_limit") }
        let role = string(element, kAXRoleAttribute)
        let subrole = string(element, kAXSubroleAttribute)
        if subrole == kAXSecureTextFieldSubrole { try fail("sensitive_surface") }
        if (attr(element, "AXHidden") as? Bool) == true { return }
        let visible = rectangle(element).map { !$0.isEmpty && bounds.intersects($0) } ?? false
        if visible {
            let label = [string(element, kAXTitleAttribute), string(element, kAXDescriptionAttribute),
                         string(element, kAXHelpAttribute)].first(where: { !$0.isEmpty }) ?? ""
            let value = editorText(element) ?? string(element, kAXValueAttribute)
            texts += [label, value].filter { !$0.isEmpty }.map { terminal ? String($0.suffix(16000)) : String($0.prefix(4000)) }
            var names: CFArray?
            AXUIElementCopyActionNames(element, &names)
            var actions: [String] = []
            let enabled = (attr(element, kAXEnabledAttribute) as? Bool) != false
            let confirmable = role == kAXComboBoxRole && (names as? [String] ?? []).contains(kAXConfirmAction)
            if enabled && ((names as? [String] ?? []).contains(kAXPressAction) || confirmable) {
                actions.append("click")
            }
            if enabled && supportsFill(element) { actions.append("fill") }
            if !actions.isEmpty {
                let ref = "g\(generation).e\(refs.count)"
                refs[ref] = element
                controls.append(["ref": ref, "label": String(label.prefix(500)), "role": role,
                                 "value": String(value.prefix(4000)), "enabled": enabled, "actions": actions,
                                 "fill_method": keyboardEditor(element) ? "focused_text_keyboard" : "accessibility_value",
                                 "identity": string(element, kAXIdentifierAttribute).isEmpty ? label : string(element, kAXIdentifierAttribute),
                                 "value_readable": editorText(element) != nil])
            }
        }
        for child in attr(element, kAXChildrenAttribute) as? [AXUIElement] ?? [] {
            try walk(child, bounds, depth + 1, &count, &texts, &controls)
        }
    }

    func observe() throws -> [String: Any] {
        try check()
        guard let root, let bounds = rectangle(root) else { try fail("desktop_surface_changed") }
        generation += 1; refs = [:]
        var count = 0, texts: [String] = [], controls: [[String: Any]] = []
        try walk(root, bounds, 0, &count, &texts, &controls)
        if terminal {
            guard let focus = attr(app!, kAXFocusedUIElementAttribute),
                  CFGetTypeID(focus) == AXUIElementGetTypeID() else { try fail("desktop_action_uncertain") }
            terminalFocus = (focus as! AXUIElement)
            let ref = "g\(generation).terminal"
            refs = [ref: root]
            controls = [["ref": ref, "label": "Bound terminal window", "role": "terminal",
                         "value": "", "actions": ["type", "key"], "enabled": true]]
        }
        guard texts.joined(separator: "\n").count <= 64000 else { try fail("desktop_observation_limit") }
        return ["generation": generation, "text": texts.joined(separator: "\n"), "controls": controls]
    }

    func refreshWindow() async throws {
        try check()
        let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true)
        guard let current = content.windows.first(where: { $0.windowID == window!.windowID }) else {
            try fail("desktop_surface_changed")
        }
        window = current
    }

    func resize(_ request: [String: Any]) async throws -> [String: Any] {
        try check()
        guard let width = request["width"] as? Int, let height = request["height"] as? Int,
              width > 0, height > 0, width <= 7680, height <= 7680 else { try fail("invalid_request") }
        var settable = DarwinBoolean(false)
        guard AXUIElementIsAttributeSettable(root!, kAXSizeAttribute as CFString, &settable) == .success,
              settable.boolValue else { try fail("desktop_resize_unavailable") }
        refs = [:]
        // Convert capture pixels to AX points; correct for any window decoration difference.
        for _ in 0..<3 {
            try await refreshWindow()
            let filter = SCContentFilter(desktopIndependentWindow: window!)
            let scale = CGFloat(filter.pointPixelScale)
            let pixels = CGSize(width: filter.contentRect.width * scale, height: filter.contentRect.height * scale)
            if Int(pixels.width) == width && Int(pixels.height) == height { break }
            guard scale > 0, let bounds = rectangle(root!) else { try fail("desktop_surface_changed") }
            var size = CGSize(width: bounds.width + (CGFloat(width) - pixels.width) / scale,
                              height: bounds.height + (CGFloat(height) - pixels.height) / scale)
            guard size.width > 0, size.height > 0,
                  let value = AXValueCreate(.cgSize, &size) else { try fail("desktop_resize_unavailable") }
            guard AXUIElementSetAttributeValue(root!, kAXSizeAttribute as CFString, value) == .success else {
                try fail("desktop_resize_unavailable")
            }
            try await Task.sleep(nanoseconds: 200_000_000)
        }
        try await refreshWindow()
        let sample = try await screenshot()
        return ["width": sample["width"]!, "height": sample["height"]!]
    }

    func screenshot() async throws -> [String: Any] {
        try await refreshWindow()
        var remaining = 2000
        try rejectSensitive(root!, &remaining)
        let filter = SCContentFilter(desktopIndependentWindow: window!)
        let config = SCStreamConfiguration()
        config.width = Int(filter.contentRect.width * CGFloat(filter.pointPixelScale))
        config.height = Int(filter.contentRect.height * CGFloat(filter.pointPixelScale))
        config.showsCursor = false
        config.ignoreShadowsSingleWindow = true
        let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
        try check()
        remaining = 2000
        try rejectSensitive(root!, &remaining)
        let rep = NSBitmapImageRep(cgImage: image)
        guard let png = rep.representation(using: .png, properties: [:]), png.count <= 24 * 1024 * 1024 else {
            try fail("desktop_capture_failed")
        }
        return ["png": png.base64EncodedString(), "width": image.width, "height": image.height]
    }

    func clickElement(_ element: AXUIElement) async throws {
            let pid = window!.owningApplication!.processID
            guard let running = NSRunningApplication(processIdentifier: pid),
                  running.activate(options: [.activateIgnoringOtherApps]) else { try fail("desktop_action_uncertain") }
            _ = AXUIElementPerformAction(root!, kAXRaiseAction as CFString)
            try await Task.sleep(nanoseconds: 150_000_000)
            try check()
            guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid,
                  let currentRect = rectangle(element), !currentRect.isEmpty else { try fail("stale_ref") }
            let point = CGPoint(x: currentRect.midX, y: currentRect.midY)
            var hit: AXUIElement?
            guard AXUIElementCopyElementAtPosition(AXUIElementCreateSystemWide(), Float(point.x), Float(point.y), &hit) == .success,
                  let hit else { try fail("stale_ref") }
            var candidate: AXUIElement? = hit
            var matched = false
            for _ in 0..<10 {
                guard let current = candidate else { break }
                if CFEqual(current, element) { matched = true; break }
                candidate = attr(current, kAXParentAttribute) as! AXUIElement?
            }
            guard matched,
                  let down = CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left),
                  let up = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left) else {
                try fail("stale_ref")
            }
            down.setIntegerValueField(.mouseEventClickState, value: 1)
            up.setIntegerValueField(.mouseEventClickState, value: 1)
            down.post(tap: .cghidEventTap)
            try await Task.sleep(nanoseconds: 50_000_000)
            up.post(tap: .cghidEventTap)
    }

    func terminalInput(_ request: [String: Any]) async throws -> [String: Any] {
        guard terminal, request["generation"] as? Int == generation,
              let ref = request["ref"] as? String, let element = refs[ref],
              CFEqual(element, root!) else { try fail("stale_ref") }
        let action = request["action"] as? String
        let keys: [String: (CGKeyCode, CGEventFlags)] = [
            "Enter": (36, []), "Escape": (53, []), "Tab": (48, []), "Backspace": (51, []),
            "ArrowUp": (126, []), "ArrowDown": (125, []), "ArrowLeft": (123, []),
            "ArrowRight": (124, []), "Control+C": (8, .maskControl)]
        let text = request["text"] as? String ?? ""
        let key = keys[request["key"] as? String ?? ""]
        guard (action == "type" && !text.isEmpty && text.count <= 4000 &&
               !text.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) })) ||
              (action == "key" && key != nil) else { try fail("invalid_action") }
        refs = [:]
        let pid = window!.owningApplication!.processID
        guard let running = NSRunningApplication(processIdentifier: pid),
              running.activate(options: []) else { try fail("desktop_action_uncertain") }
        _ = AXUIElementPerformAction(root!, kAXRaiseAction as CFString)
        try await Task.sleep(nanoseconds: 150_000_000)
        func ready() throws {
            try check()
            guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid,
                  let focusedWindow = attr(app!, kAXFocusedWindowAttribute), CFEqual(focusedWindow, root!),
                  let expectedFocus = terminalFocus, let focus = attr(app!, kAXFocusedUIElementAttribute),
                  CFEqual(focus, expectedFocus),
                  CGEventSource.flagsState(.combinedSessionState)
                    .intersection([.maskCommand, .maskControl, .maskAlternate, .maskShift]).isEmpty else {
                try fail("desktop_action_uncertain")
            }
            var budget = 2000
            try rejectSensitive(root!, &budget)
        }
        func send(_ code: CGKeyCode, _ flags: CGEventFlags, _ units: [UniChar] = []) throws {
            try ready()
            guard let down = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true),
                  let up = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false) else {
                try fail("desktop_action_uncertain")
            }
            down.flags = flags; up.flags = flags
            if !units.isEmpty {
                down.keyboardSetUnicodeString(stringLength: units.count, unicodeString: units)
                up.keyboardSetUnicodeString(stringLength: units.count, unicodeString: units)
            }
            down.postToPid(pid); up.postToPid(pid)
        }
        if action == "type" {
            for character in text { try send(0, [], Array(String(character).utf16)) }
        } else if let key { try send(key.0, key.1) }
        return ["status": "returned", "verification": "input_dispatched_not_command_completion"]
    }

    func act(_ request: [String: Any]) async throws -> [String: Any] {
        try check()
        if terminal { return try await terminalInput(request) }
        guard request["generation"] as? Int == generation,
              let ref = request["ref"] as? String, let element = refs[ref],
              let bounds = rectangle(root!), let rect = rectangle(element), bounds.intersects(rect),
              (attr(element, kAXEnabledAttribute) as? Bool) != false else { try fail("stale_ref") }
        // Re-walk the current tree to reject detached controls before dispatch.
        var remaining = 2000
        func contains(_ parent: AXUIElement, _ depth: Int) -> Bool {
            remaining -= 1
            if remaining < 0 { return false }
            if CFEqual(parent, element) { return true }
            if depth > 40 { return false }
            return (attr(parent, kAXChildrenAttribute) as? [AXUIElement] ?? []).prefix(2000)
                .contains { contains($0, depth + 1) }
        }
        guard contains(root!, 0) else { try fail("stale_ref") }
        refs = [:] // Invalidate before any attempted effect.
        let result: AXError
        if request["action"] as? String == "click", string(element, kAXRoleAttribute) == kAXComboBoxRole {
            var names: CFArray?
            AXUIElementCopyActionNames(element, &names)
            guard (names as? [String] ?? []).contains(kAXConfirmAction) else { try fail("invalid_action") }
            result = AXUIElementPerformAction(element, kAXConfirmAction as CFString)
        } else if request["action"] as? String == "click" {
            try await clickElement(element)
            result = .success
        } else if request["action"] as? String == "fill", let text = request["text"] as? String {
            guard string(element, kAXSubroleAttribute) != kAXSecureTextFieldSubrole else { try fail("sensitive_surface") }
            guard supportsFill(element) else { try fail("invalid_action") }
            let pid = window!.owningApplication!.processID
            guard let running = NSRunningApplication(processIdentifier: pid),
                  running.activate(options: [.activateIgnoringOtherApps]) else {
                try fail("desktop_action_uncertain")
            }
            _ = AXUIElementSetAttributeValue(element, kAXFocusedAttribute as CFString, kCFBooleanTrue)
            func hasFocus() -> Bool {
                guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid,
                      let value = attr(app!, kAXFocusedUIElementAttribute),
                      CFGetTypeID(value) == AXUIElementGetTypeID() else { return false }
                var current = value as! AXUIElement
                for _ in 0..<8 {
                    if CFEqual(current, element) { return true }
                    guard string(element, kAXRoleAttribute) == kAXComboBoxRole,
                          string(current, kAXSubroleAttribute) != kAXSecureTextFieldSubrole,
                          let parent = attr(current, kAXParentAttribute),
                          CFGetTypeID(parent) == AXUIElementGetTypeID() else { return false }
                    current = parent as! AXUIElement
                }
                return false
            }
            func focused() throws {
                try check()
                guard hasFocus() else { try fail("desktop_action_uncertain") }
            }
            func matchesText() -> Bool {
                let value = editorText(element) ?? string(element, kAXValueAttribute)
                return value == text || value == text + "\n"
            }
            try await Task.sleep(nanoseconds: 100_000_000)
            if !hasFocus() {
                try await clickElement(element)
                try await Task.sleep(nanoseconds: 100_000_000)
            }
            try focused()
            if keyboardEditor(element) {
                guard !text.isEmpty,
                      !text.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
                    try fail("invalid_action")
                }
                func keyboardReady() throws {
                    try focused()
                    guard CGEventSource.flagsState(.combinedSessionState)
                        .intersection([.maskCommand, .maskControl, .maskAlternate, .maskShift]).isEmpty else {
                        try fail("desktop_action_uncertain")
                    }
                }
                try keyboardReady()
                // Never assume a native editor implements select-all as text replacement.
                // Focus may expose rich text APIs that were unavailable while inactive.
                guard editorText(element) == "" else { try fail("desktop_action_uncertain") }
                for character in text {
                    try keyboardReady()
                    let units = Array(String(character).utf16)
                    guard let down = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true),
                          let up = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false) else {
                        try fail("desktop_action_uncertain")
                    }
                    down.flags = []; up.flags = []
                    down.keyboardSetUnicodeString(stringLength: units.count, unicodeString: units)
                    up.keyboardSetUnicodeString(stringLength: units.count, unicodeString: units)
                    down.postToPid(pid); up.postToPid(pid)
                }
                try await Task.sleep(nanoseconds: 200_000_000)
                try focused()
                if let actual = editorText(element), actual != text && actual != text + "\n" {
                    try fail("desktop_action_uncertain")
                }
                return ["status": "returned", "verification": editorText(element) == nil
                    ? "input_sent_without_value_readback" : "text_readback"]
            }
            let original = editorText(element)
            _ = AXUIElementSetAttributeValue(element, kAXValueAttribute as CFString, text as CFString)
            try await Task.sleep(nanoseconds: 150_000_000)
            if !matchesText() {
                // Some native editors reject or ignore AXValue writes. Only
                // fall back when the attempted write left the field unchanged and empty.
                // No clipboard, shortcuts, Return, or arbitrary model-selected keys.
                guard editorText(element) == original, let original,
                      original.trimmingCharacters(in: .newlines).isEmpty,
                      !text.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
                    try fail("desktop_action_uncertain")
                }
                for character in text {
                    try focused()
                    let modifiers = CGEventSource.flagsState(.combinedSessionState)
                    guard modifiers.intersection([.maskCommand, .maskControl, .maskAlternate, .maskShift]).isEmpty else {
                        try fail("desktop_action_uncertain")
                    }
                    let units = Array(String(character).utf16)
                    guard let down = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true),
                          let up = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false) else {
                        try fail("desktop_action_uncertain")
                    }
                    down.keyboardSetUnicodeString(stringLength: units.count, unicodeString: units)
                    up.keyboardSetUnicodeString(stringLength: units.count, unicodeString: units)
                    down.postToPid(pid)
                    up.postToPid(pid)
                }
                try await Task.sleep(nanoseconds: 200_000_000)
            }
            try focused()
            guard matchesText() else { try fail("desktop_action_uncertain") }
            result = .success
        } else { try fail("invalid_action") }
        refs = [:] // Never replay a reference after an attempted effect.
        guard result == .success else { try fail("desktop_action_uncertain") }
        return ["status": "returned"]
    }

    var audio: AudioRecorder?

    func audioStart(_ request: [String: Any]) async throws -> [String: Any] {
        try check()
        guard audio == nil else { try fail("audio_start_failed", ["reason": "already_recording"]) }
        guard CGPreflightScreenCaptureAccess() else {
            try fail("audio_permission_missing", ["screen_recording": false])
        }
        guard let directory = request["directory"] as? String,
              let scope = request["output"] as? String, ["application", "system"].contains(scope) else {
            try fail("invalid_request")
        }
        let includes = request["include_bundle_ids"] as? [String] ?? []
        guard includes.count <= 8, includes.allSatisfy({ bundleLike($0) }) else { try fail("invalid_request") }
        let microphone = request["microphone"] as? Bool ?? false
        let device = request["microphone_device"] as? String
        guard device == nil || (microphone && device!.count <= 300) else { try fail("invalid_request") }
        let recorder = AudioRecorder(directory: directory, scope: scope, roots: [bundle] + includes,
                                     window: window!, microphone: microphone, device: device)
        let started = try await recorder.start()
        audio = recorder
        return started
    }

    func audioStop() async throws -> [String: Any] {
        guard let recorder = audio else { try fail("audio_not_running") }
        audio = nil
        return await recorder.stop()
    }
}

func bundleLike(_ value: String) -> Bool {
    return value.count <= 200 && !value.isEmpty && value.range(of: "^[A-Za-z0-9.-]+$", options: .regularExpression) != nil
}

func microphoneStatus() -> String {
    switch AVCaptureDevice.authorizationStatus(for: .audio) {
    case .authorized: return "authorized"
    case .denied: return "denied"
    case .restricted: return "restricted"
    case .notDetermined: return "not_determined"
    @unknown default: return "unknown"
    }
}

// ---- Audio: pure placement/conversion core (unit-tested via -D SHOWRUN_SELFTEST) ----

struct PCMFormat {
    let rate: Double
    let channels: Int
    let float: Bool
    let bits: Int
    let interleaved: Bool
}

// Convert one sample buffer's planes into interleaved Float32; nil for unsupported layouts.
func interleavedFloat(_ f: PCMFormat, _ planes: [UnsafeRawBufferPointer], frames: Int) -> [Float]? {
    guard f.channels >= 1, f.channels <= 8, frames >= 0,
          (f.float && f.bits == 32) || (!f.float && (f.bits == 16 || f.bits == 32)) else { return nil }
    let bytes = f.bits / 8
    let perPlane = f.interleaved ? frames * f.channels : frames
    guard planes.count == (f.interleaved ? 1 : f.channels),
          planes.allSatisfy({ $0.count >= perPlane * bytes }) else { return nil }
    func read(_ plane: UnsafeRawBufferPointer, _ i: Int) -> Float {
        if f.float { return plane.loadUnaligned(fromByteOffset: i * 4, as: Float.self) }
        if f.bits == 16 { return Float(plane.loadUnaligned(fromByteOffset: i * 2, as: Int16.self)) / 32768 }
        return Float(Double(plane.loadUnaligned(fromByteOffset: i * 4, as: Int32.self)) / 2147483648)
    }
    var out = [Float](repeating: 0, count: frames * f.channels)
    for frame in 0..<frames {
        for channel in 0..<f.channels {
            out[frame * f.channels + channel] = f.interleaved
                ? read(planes[0], frame * f.channels + channel) : read(planes[channel], frame)
        }
    }
    return out
}

// Places buffers on one shared host-clock anchor. Gaps become silence; overlaps and
// pre-anchor samples are dropped. Small jitter/drift within tolerance stays contiguous.
struct PCMPlacement {
    let rate: Double
    let channels: Int
    let anchor: Double
    let tolerance: Int
    let maxFrames: Int
    var written = 0, captured = 0, gapFrames = 0, droppedFrames = 0, buffers = 0
    var peak: Float = 0
    var sumSquares: Double = 0
    var firstBufferSeconds: Double?

    init(rate: Double, channels: Int, anchor: Double, maxSeconds: Double = 1900) {
        self.rate = rate; self.channels = channels; self.anchor = anchor
        self.tolerance = Int(rate * 0.010)
        self.maxFrames = Int(rate * maxSeconds)
    }

    // Returns zero frames to write first, then the kept range of this buffer; nil when over the limit.
    mutating func place(pts: Double, samples: [Float]) -> (pad: Int, keep: ArraySlice<Float>)? {
        let frames = samples.count / channels
        buffers += 1
        if firstBufferSeconds == nil { firstBufferSeconds = pts - anchor }
        var start = Int(((pts - anchor) * rate).rounded())
        if abs(start - written) <= tolerance { start = written }
        let pad = max(0, start - written)
        let skip = min(frames, max(0, written - start))
        let keep = frames - skip
        guard written + pad + keep <= maxFrames else { return nil }
        written += pad + keep; captured += keep; gapFrames += pad; droppedFrames += skip
        let slice = samples[(skip * channels)...]
        for value in slice where value.isFinite {
            peak = max(peak, abs(value))
            sumSquares += Double(value) * Double(value)
        }
        return (pad, slice)
    }

    func stats() -> [String: Any] {
        func db(_ x: Double) -> Any { x > 0 && x.isFinite ? (20 * log10(x) * 10).rounded() / 10 : NSNull() }
        let count = Double(captured * channels)
        var first: Any = NSNull()  // Never serialize an Optional through Any (JSONSerialization aborts).
        if let value = firstBufferSeconds, value.isFinite { first = (value * 1000).rounded() / 1000 }
        return ["sample_rate": Int(rate), "channels": channels, "buffers": buffers,
                "frames": written, "captured_frames": captured, "gap_frames": gapFrames,
                "dropped_frames": droppedFrames, "peak_dbfs": db(Double(peak)),
                "rms_dbfs": count > 0 ? db((sumSquares / count).squareRoot()) : NSNull(),
                "first_buffer_seconds": first]
    }
}

func int16Bytes(_ samples: ArraySlice<Float>) -> [Int16] {
    return samples.map { Int16((max(-1, min(1, $0)) * 32767).rounded()) }
}

func hostSeconds() -> Double { CMClockGetTime(CMClockGetHostTimeClock()).seconds }

// ---- Audio: ScreenCaptureKit stream and raw s16le writers ----

final class AudioSource {
    let kind: String
    let name: String
    let fd: Int32
    let anchor: Double
    let queue: DispatchQueue
    var placement: PCMPlacement?
    var format: PCMFormat?
    var nominal: (Double, Int)
    var error: String?
    var basis = "host_clock"
    var clockOffset = 0.0

    init(kind: String, directory: String, anchor: Double, nominal: (Double, Int)) throws {
        self.kind = kind; self.anchor = anchor; self.nominal = nominal
        name = kind + ".s16le"
        queue = DispatchQueue(label: "org.showrun.audio." + kind)
        let path = (directory as NSString).appendingPathComponent(name)
        fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o600)
        guard fd >= 0 else { try fail("audio_start_failed", ["reason": "file_create"]) }
    }

    func write(_ values: [Int16]) -> Bool {
        return values.withUnsafeBytes { raw in
            var offset = 0
            while offset < raw.count {
                let n = Darwin.write(fd, raw.baseAddress! + offset, raw.count - offset)
                if n <= 0 { return false }
                offset += n
            }
            return true
        }
    }

    // Called on this source's serial queue only.
    func handle(_ sample: CMSampleBuffer) {
        guard error == nil, CMSampleBufferDataIsReady(sample), CMSampleBufferIsValid(sample),
              let description = CMSampleBufferGetFormatDescription(sample),
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(description)?.pointee else { return }
        let frames = CMSampleBufferGetNumSamples(sample)
        guard frames > 0 else { return }
        let current = PCMFormat(rate: asbd.mSampleRate, channels: Int(asbd.mChannelsPerFrame),
                                float: asbd.mFormatFlags & kAudioFormatFlagIsFloat != 0,
                                bits: Int(asbd.mBitsPerChannel),
                                interleaved: asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved == 0)
        if let known = format {
            guard known.rate == current.rate, known.channels == current.channels else {
                error = "audio_format_changed"; return
            }
        }
        var needed = 0
        CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sample, bufferListSizeNeededOut: &needed, bufferListOut: nil, bufferListSize: 0,
            blockBufferAllocator: nil, blockBufferMemoryAllocator: nil,
            flags: kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment, blockBufferOut: nil)
        guard needed > 0 else { error = "audio_format_unsupported"; return }
        let raw = UnsafeMutableRawPointer.allocate(byteCount: needed, alignment: 16)
        defer { raw.deallocate() }
        let list = raw.bindMemory(to: AudioBufferList.self, capacity: 1)
        var block: CMBlockBuffer?
        guard CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sample, bufferListSizeNeededOut: nil, bufferListOut: list, bufferListSize: needed,
            blockBufferAllocator: nil, blockBufferMemoryAllocator: nil,
            flags: kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment, blockBufferOut: &block) == noErr else {
            error = "audio_format_unsupported"; return
        }
        let planes = UnsafeMutableAudioBufferListPointer(list).map {
            UnsafeRawBufferPointer(start: $0.mData, count: Int($0.mDataByteSize))
        }
        guard let samples = interleavedFloat(current, planes, frames: frames) else {
            error = "audio_format_unsupported"; return
        }
        var pts = CMSampleBufferGetPresentationTimeStamp(sample).seconds
        let arrival = hostSeconds() - Double(frames) / current.rate
        if placement == nil {
            format = current
            // Expect host-clock timestamps; otherwise align on arrival and say so.
            if !pts.isFinite || abs(arrival - pts) > 5 {
                basis = "arrival_adjusted"
                clockOffset = pts.isFinite ? arrival - pts : 0
            }
            placement = PCMPlacement(rate: current.rate, channels: current.channels, anchor: anchor)
        }
        if pts.isFinite { pts += clockOffset } else { pts = arrival; basis = "arrival_adjusted" }
        guard let placed = placement!.place(pts: pts, samples: samples) else {
            error = "audio_storage_limit"; return
        }
        var ok = true
        var pad = placed.pad * current.channels
        while ok && pad > 0 {
            let chunk = min(pad, 48000 * 8)
            ok = write([Int16](repeating: 0, count: chunk))
            pad -= chunk
        }
        if ok && !placed.keep.isEmpty { ok = write(int16Bytes(placed.keep)) }
        if !ok { error = "audio_write_failed" }
    }

    func finish() -> [String: Any] {
        return queue.sync {
            close(fd)
            var result = placement?.stats() ?? ["sample_rate": Int(nominal.0), "channels": nominal.1, "buffers": 0,
                                                 "frames": 0, "captured_frames": 0, "gap_frames": 0,
                                                 "dropped_frames": 0, "peak_dbfs": NSNull(), "rms_dbfs": NSNull(),
                                                 "first_buffer_seconds": NSNull()]
            result["kind"] = kind
            result["file"] = name
            result["encoding"] = "s16le"
            result["timestamp_basis"] = basis
            if let error { result["error"] = error }
            return result
        }
    }
}

final class AudioRecorder: NSObject, SCStreamOutput, SCStreamDelegate {
    let directory: String, scope: String, roots: [String], window: SCWindow
    let microphone: Bool, device: String?
    var stream: SCStream?
    var sources: [String: AudioSource] = [:]
    let lock = NSLock()
    var stopError: String?
    var included: [pid_t: String] = [:]
    var seen = Set<String>()
    var filterUpdates = 0
    var refresh: Task<Void, Never>?
    var display: SCDisplay?
    var anchorUnix = 0.0
    let screenQueue = DispatchQueue(label: "org.showrun.audio.screen")

    init(directory: String, scope: String, roots: [String], window: SCWindow, microphone: Bool, device: String?) {
        self.directory = directory; self.scope = scope; self.roots = roots; self.window = window
        self.microphone = microphone; self.device = device
    }

    func wanted(_ app: SCRunningApplication) -> Bool {
        let id = app.bundleIdentifier
        guard app.processID != getpid(), id != "org.showrun.desktop" else { return false }
        return roots.contains { id == $0 || id.hasPrefix($0 + ".") }
    }

    func filter(_ content: SCShareableContent) throws -> SCContentFilter {
        guard let display = display ?? content.displays.first(where: { $0.frame.intersects(window.frame) })
                ?? content.displays.first else { try fail("audio_start_failed", ["reason": "no_display"]) }
        self.display = display
        if scope == "system" {
            let own = content.applications.filter { $0.processID == getpid() }
            return SCContentFilter(display: display, excludingApplications: own, exceptingWindows: [])
        }
        let apps = content.applications.filter(wanted)
        guard !apps.isEmpty else { try fail("audio_start_failed", ["reason": "application_not_found"]) }
        lock.withLock {
            included = Dictionary(apps.map { ($0.processID, $0.bundleIdentifier) }, uniquingKeysWith: { a, _ in a })
            seen.formUnion(apps.map { $0.bundleIdentifier })
        }
        return SCContentFilter(display: display, including: apps, exceptingWindows: [])
    }

    func validateDirectory() throws {
        var info = stat()
        guard (directory as NSString).isAbsolutePath, lstat(directory, &info) == 0,
              info.st_mode & S_IFMT == S_IFDIR, info.st_uid == getuid(), info.st_mode & 0o077 == 0 else {
            try fail("audio_start_failed", ["reason": "directory"])
        }
    }

    func start() async throws -> [String: Any] {
        try validateDirectory()
        var micInfo: [String: Any]? = nil
        if microphone {
            guard #available(macOS 15.0, *) else { try fail("audio_unavailable", ["reason": "microphone_requires_macos_15"]) }
            guard microphoneStatus() == "authorized" else {
                try fail("audio_permission_missing", ["microphone": microphoneStatus()])
            }
            let devices = AVCaptureDevice.DiscoverySession(deviceTypes: [.microphone, .external],
                                                           mediaType: .audio, position: .unspecified).devices
            let chosen: AVCaptureDevice?
            if let device {
                let matches = devices.filter { $0.uniqueID == device || $0.localizedName == device }
                guard matches.count == 1 else {
                    try fail("audio_device_not_found", ["match_count": matches.count, "device_count": devices.count])
                }
                chosen = matches[0]
            } else {
                chosen = AVCaptureDevice.default(for: .audio)
            }
            guard let chosen else { try fail("audio_device_not_found", ["match_count": 0, "device_count": devices.count]) }
            micInfo = ["name": String(chosen.localizedName.prefix(200)), "unique_id": String(chosen.uniqueID.prefix(300)),
                       "selection": device == nil ? "system_default" : "explicit"]
        }
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        let contentFilter = try filter(content)
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        config.sampleRate = 48000
        config.channelCount = 2
        config.width = 64
        config.height = 64
        config.minimumFrameInterval = CMTime(value: 1, timescale: 2)
        config.showsCursor = false
        if microphone, #available(macOS 15.0, *) {
            config.captureMicrophone = true
            config.microphoneCaptureDeviceID = micInfo?["unique_id"] as? String
        }
        let anchor = hostSeconds()
        anchorUnix = Date().timeIntervalSince1970 - (hostSeconds() - anchor)
        sources["output"] = try AudioSource(kind: "output", directory: directory, anchor: anchor, nominal: (48000, 2))
        if microphone {
            sources["microphone"] = try AudioSource(kind: "microphone", directory: directory, anchor: anchor,
                                                    nominal: (48000, 1))
        }
        let stream = SCStream(filter: contentFilter, configuration: config, delegate: self)
        do {
            try stream.addStreamOutput(self, type: .screen, sampleHandlerQueue: screenQueue)
            try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: sources["output"]!.queue)
            if microphone, #available(macOS 15.0, *) {
                try stream.addStreamOutput(self, type: .microphone, sampleHandlerQueue: sources["microphone"]!.queue)
            }
            try await stream.startCapture()
        } catch {
            _ = sources.values.map { $0.finish() }
            let code = (error as NSError).code
            try fail(code == -3801 ? "audio_permission_missing" : "audio_start_failed",
                     ["reason": "stream_start", "code": code])
        }
        self.stream = stream
        if scope == "application" {
            refresh = Task.detached { [weak self] in
                while !Task.isCancelled {
                    try? await Task.sleep(nanoseconds: 1_000_000_000)
                    guard let self, !Task.isCancelled else { return }
                    await self.refreshFilter()
                }
            }
        }
        var result: [String: Any] = ["status": "recording", "anchor_unix": anchorUnix, "scope": scope,
                                     "sources": sources.keys.sorted()]
        result["applications"] = lock.withLock { seen.sorted() }
        if let micInfo { result["microphone_device"] = micInfo }
        return result
    }

    // Include helper processes (for example browser audio services) that start mid-take.
    func refreshFilter() async {
        guard let stream,
              let content = try? await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false) else { return }
        let apps = content.applications.filter(wanted)
        let pids = Set(apps.map { $0.processID })
        let changed = lock.withLock { pids != Set(included.keys) }
        guard changed, !apps.isEmpty, let display else { return }
        do {
            try await stream.updateContentFilter(SCContentFilter(display: display, including: apps, exceptingWindows: []))
            lock.withLock {
                included = Dictionary(apps.map { ($0.processID, $0.bundleIdentifier) }, uniquingKeysWith: { a, _ in a })
                seen.formUnion(apps.map { $0.bundleIdentifier })
                filterUpdates += 1
            }
        } catch {
            lock.withLock { stopError = stopError ?? "audio_filter_update_failed" }
        }
    }

    func stop() async -> [String: Any] {
        refresh?.cancel()
        let stopUnix = Date().timeIntervalSince1970
        if let stream {
            do { try await stream.stopCapture() } catch {
                lock.withLock { stopError = stopError ?? "audio_stop_failed" }
            }
        }
        let results = sources.keys.sorted().map { sources[$0]!.finish() }
        return lock.withLock {
            var result: [String: Any] = ["status": "stopped", "anchor_unix": anchorUnix, "stop_unix": stopUnix,
                                         "scope": scope, "sources": results, "applications": seen.sorted(),
                                         "filter_updates": filterUpdates]
            if let stopError { result["interrupted"] = stopError }
            return result
        }
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sample: CMSampleBuffer, of type: SCStreamOutputType) {
        if type == .audio { sources["output"]?.handle(sample); return }
        if #available(macOS 15.0, *), type == .microphone { sources["microphone"]?.handle(sample) }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        lock.withLock {
            stopError = stopError ?? ((error as NSError).code == -3801 ? "audio_permission_missing" : "audio_stream_stopped")
        }
    }
}

#if SHOWRUN_SELFTEST
// Deterministic checks for placement/conversion; never part of the shipped companion.
func runAudioSelfTest() -> Int32 {
    var failures: [String] = []
    func expect(_ ok: Bool, _ name: String) { if !ok { failures.append(name) } }
    var p = PCMPlacement(rate: 1000, channels: 2, anchor: 100)
    // Pre-anchor buffer: 30 frames starting 20 frames before the anchor keeps 10.
    let a = p.place(pts: 99.98, samples: [Float](repeating: 0.5, count: 60))!
    expect(a.pad == 0 && a.keep.count == 20 && p.written == 10 && p.droppedFrames == 20, "pre_anchor")
    // Jitter within 10 ms tolerance stays contiguous.
    let b = p.place(pts: 100.015, samples: [Float](repeating: -0.25, count: 20))!
    expect(b.pad == 0 && b.keep.count == 20 && p.written == 20, "jitter_contiguous")
    // A 100 ms gap is filled with silence.
    let c = p.place(pts: 100.120, samples: [Float](repeating: 0.1, count: 20))!
    expect(c.pad == 100 && p.written == 130 && p.gapFrames == 100, "gap_fill")
    // A 50 ms overlap is dropped, not duplicated.
    let d = p.place(pts: 100.080, samples: [Float](repeating: 0.1, count: 120))!
    expect(d.pad == 0 && d.keep.count == 20 && p.written == 140 && p.droppedFrames == 70, "overlap_drop")
    let s = p.stats()
    expect(abs((s["peak_dbfs"] as! Double) - (-6.0)) < 0.1 && s["captured_frames"] as! Int == 40, "stats")
    var q = PCMPlacement(rate: 1000, channels: 1, anchor: 0, maxSeconds: 1)
    expect(q.place(pts: 2, samples: [0]) == nil, "storage_limit")
    // Conversion: planar float, interleaved int16, unsupported.
    let left: [Float] = [0.5, -0.5], right: [Float] = [1, 0]
    let planar = left.withUnsafeBytes { l in right.withUnsafeBytes { r in
        interleavedFloat(PCMFormat(rate: 48000, channels: 2, float: true, bits: 32, interleaved: false), [l, r], frames: 2)
    } }
    expect(planar == [0.5, 1, -0.5, 0], "planar_float")
    let ints: [Int16] = [16384, -32768]
    let mono = ints.withUnsafeBytes {
        interleavedFloat(PCMFormat(rate: 48000, channels: 1, float: false, bits: 16, interleaved: true), [$0], frames: 2)
    }
    expect(mono == [0.5, -1], "interleaved_int16")
    let bad = ints.withUnsafeBytes {
        interleavedFloat(PCMFormat(rate: 48000, channels: 1, float: false, bits: 24, interleaved: true), [$0], frames: 1)
    }
    expect(bad == nil, "unsupported_format")
    expect(int16Bytes([2.0, -2.0, 0.5][...]) == [32767, -32767, 16384], "clamp_int16")
    // Every reply must be JSON-serializable: an Optional or NaN would abort the companion.
    var nan = PCMPlacement(rate: 1000, channels: 1, anchor: 0)
    _ = nan.place(pts: 0, samples: [Float.nan, 0.25])
    for stats in [PCMPlacement(rate: 1000, channels: 1, anchor: 0).stats(), nan.stats(), p.stats()] {
        expect(JSONSerialization.isValidJSONObject(stats), "json_stats")
    }
    expect(abs((nan.stats()["peak_dbfs"] as! Double) - (-12.0)) < 0.1, "nan_ignored")
    let data = try! JSONSerialization.data(withJSONObject: ["failures": failures, "passed": failures.isEmpty])
    print(String(data: data, encoding: .utf8)!)
    return failures.isEmpty ? 0 : 1
}
#endif

// The CLI launches this app through LaunchServices and provides a private session
// socket. Never accept a public listener or inherit the caller's standard input.
func connectSession() -> Bool {
    let args = CommandLine.arguments
    guard args.count == 5, args[1] == "--socket", args[3] == "--token",
          args[4].count == 64 else { return false }
    let path = args[2]
    var address = sockaddr_un()
    address.sun_family = sa_family_t(AF_UNIX)
    let bytes = Array(path.utf8) + [0]
    guard bytes.count <= MemoryLayout.size(ofValue: address.sun_path) else { return false }
    withUnsafeMutableBytes(of: &address.sun_path) { buffer in
        buffer.copyBytes(from: bytes)
    }
    let fd = socket(AF_UNIX, SOCK_STREAM, 0)
    guard fd >= 0 else { return false }
    let connected = withUnsafePointer(to: &address) { pointer in
        pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
        }
    }
    guard connected == 0 else { close(fd); return false }
    var timeout = timeval(tv_sec: 300, tv_usec: 0)
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
    guard dup2(fd, STDIN_FILENO) >= 0, dup2(fd, STDOUT_FILENO) >= 0 else { close(fd); return false }
    close(fd)
    // This identifies the build; it does not assert that a release has been published.
    let hello = try! JSONSerialization.data(withJSONObject: [
        "token": args[4],
        "companion": ["version": "desktop-v0.6.0", "protocol": 1,
                      "capabilities": ["permissions", "bind", "observe", "screenshot", "resize",
                                       "click", "fill", "terminal_type", "terminal_key",
                                       "window_selection_diagnostics", "audio_output"]
                        + (microphoneCapable() ? ["audio_microphone", "request_microphone"] : [])]
    ] as [String: Any])
    print(String(data: hello, encoding: .utf8)!); fflush(stdout)
    return true
}

func microphoneCapable() -> Bool {
    if #available(macOS 15.0, *) { return true }
    return false
}

#if SHOWRUN_SELFTEST
@main struct SelfTest {
    static func main() { exit(runAudioSelfTest()) }
}
#else
@main struct Main {
    static func main() async {
        guard #available(macOS 14.0, *) else { print("{\"error\":\"desktop_unsupported\"}"); return }
        guard connectSession() else { return }
        // Initialize the AppKit connection before ScreenCaptureKit captures a window.
        _ = NSApplication.shared
        let bridge = Bridge()
        while let line = readLine() {
            var result: [String: Any]
            do {
                guard let data = line.data(using: .utf8), data.count <= 65536,
                      let request = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                    try fail("invalid_request")
                }
                switch request["operation"] as? String {
                case "permissions":
                    result = ["screen_recording": CGPreflightScreenCaptureAccess(),
                              "accessibility": AXIsProcessTrusted(), "microphone": microphoneStatus(),
                              "bundle_id": "org.showrun.desktop"]
                case "request_microphone":
                    // Explicit setup only: shows the macOS prompt once when undetermined.
                    guard microphoneCapable() else { try fail("audio_unavailable") }
                    if microphoneStatus() == "not_determined" {
                        _ = await AVCaptureDevice.requestAccess(for: .audio)
                    }
                    result = ["microphone": microphoneStatus()]
                case "audio_start": result = try await bridge.audioStart(request)
                case "audio_stop": result = try await bridge.audioStop()
                case "bind": result = try await bridge.bind(request)
                case "observe": result = try bridge.observe()
                case "resize": result = try await bridge.resize(request)
                case "screenshot": result = try await bridge.screenshot()
                case "act": result = try await bridge.act(request)
                default: try fail("invalid_request")
                }
            } catch let error as BridgeError { result = ["error": error.code, "diagnostics": error.diagnostics] }
            catch { result = ["error": "desktop_bridge_failed"] }
            if let data = try? JSONSerialization.data(withJSONObject: result), let output = String(data: data, encoding: .utf8) {
                print(output); fflush(stdout)
            }
        }
    }
}
#endif
