// Fixed JSON-line bridge: one bound window, ScreenCaptureKit and Accessibility.
// No shell, arbitrary code, model coordinates/keys, app launch, or permission prompts.
import AppKit
import ApplicationServices
import ScreenCaptureKit

struct BridgeError: Error { let code: String }
func fail(_ code: String) throws -> Never { throw BridgeError(code: code) }
func attr(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
    return value
}
func string(_ element: AXUIElement, _ name: String) -> String {
    return attr(element, name) as? String ?? ""
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

    func bind(_ request: [String: Any]) async throws -> [String: Any] {
        guard CGPreflightScreenCaptureAccess(), AXIsProcessTrusted() else {
            try fail("desktop_permission_missing")
        }
        guard let b = request["bundle_id"] as? String, let t = request["window_title"] as? String else {
            try fail("invalid_request")
        }
        bundle = b; title = t
        let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true)
        let matches = content.windows.filter { $0.owningApplication?.bundleIdentifier == b && $0.title == t }
        guard matches.count == 1, let selected = matches.first,
              let owner = selected.owningApplication else { try fail("desktop_window_ambiguous") }
        let application = AXUIElementCreateApplication(owner.processID)
        let windows = attr(application, kAXWindowsAttribute) as? [AXUIElement] ?? []
        let targets = windows.filter { string($0, kAXTitleAttribute) == t }
        guard targets.count == 1 else { try fail("desktop_window_ambiguous") }
        window = selected; root = targets[0]; app = application
        return ["status": "ready", "window_id": selected.windowID, "pid": owner.processID]
    }

    func check() throws {
        guard let root, let app, let window,
              string(root, kAXTitleAttribute) == title,
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
            let value = string(element, kAXValueAttribute)
            texts += [label, value].filter { !$0.isEmpty }.map { String($0.prefix(4000)) }
            var names: CFArray?
            AXUIElementCopyActionNames(element, &names)
            var actions: [String] = []
            let enabled = (attr(element, kAXEnabledAttribute) as? Bool) != false
            if enabled && (names as? [String] ?? []).contains(kAXPressAction) { actions.append("click") }
            var settable = DarwinBoolean(false)
            if enabled && [kAXTextFieldRole, kAXTextAreaRole].contains(role)
                && AXUIElementIsAttributeSettable(element, kAXValueAttribute as CFString, &settable) == .success
                && settable.boolValue { actions.append("fill") }
            if !actions.isEmpty {
                let ref = "g\(generation).e\(refs.count)"
                refs[ref] = element
                controls.append(["ref": ref, "label": String(label.prefix(500)), "role": role,
                                 "value": String(value.prefix(4000)), "enabled": enabled, "actions": actions])
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

    func act(_ request: [String: Any]) async throws -> [String: Any] {
        try check()
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
        if request["action"] as? String == "click" {
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
            result = .success
        } else if request["action"] as? String == "fill", let text = request["text"] as? String {
            guard string(element, kAXSubroleAttribute) != kAXSecureTextFieldSubrole else { try fail("sensitive_surface") }
            let pid = window!.owningApplication!.processID
            guard let running = NSRunningApplication(processIdentifier: pid),
                  running.activate(options: [.activateIgnoringOtherApps]),
                  AXUIElementSetAttributeValue(element, kAXFocusedAttribute as CFString, kCFBooleanTrue) == .success else {
                try fail("desktop_action_uncertain")
            }
            func focused() throws {
                try check()
                guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid,
                      let current = attr(app!, kAXFocusedUIElementAttribute), CFEqual(current, element) else {
                    try fail("desktop_action_uncertain")
                }
            }
            func matchesText() -> Bool {
                let value = string(element, kAXValueAttribute)
                return value == text || value == text + "\n"
            }
            try await Task.sleep(nanoseconds: 100_000_000)
            try focused()
            let original = string(element, kAXValueAttribute)
            _ = AXUIElementSetAttributeValue(element, kAXValueAttribute as CFString, text as CFString)
            try await Task.sleep(nanoseconds: 150_000_000)
            if !matchesText() {
                // Some Electron editors advertise AXValue writes but ignore them. Only
                // fall back when the attempted write left the field unchanged and empty.
                // No clipboard, shortcuts, Return, or arbitrary model-selected keys.
                guard string(element, kAXValueAttribute) == original,
                      original.trimmingCharacters(in: .newlines).isEmpty,
                      !text.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
                    try fail("desktop_action_uncertain")
                }
                for character in text {
                    try focused()
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
}

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
    let hello = try! JSONSerialization.data(withJSONObject: ["token": args[4]])
    print(String(data: hello, encoding: .utf8)!); fflush(stdout)
    return true
}

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
                              "accessibility": AXIsProcessTrusted(), "bundle_id": "org.showrun.desktop"]
                case "bind": result = try await bridge.bind(request)
                case "observe": result = try bridge.observe()
                case "resize": result = try await bridge.resize(request)
                case "screenshot": result = try await bridge.screenshot()
                case "act": result = try await bridge.act(request)
                default: try fail("invalid_request")
                }
            } catch let error as BridgeError { result = ["error": error.code] }
            catch { result = ["error": "desktop_bridge_failed"] }
            if let data = try? JSONSerialization.data(withJSONObject: result), let output = String(data: data, encoding: .utf8) {
                print(output); fflush(stdout)
            }
        }
    }
}
