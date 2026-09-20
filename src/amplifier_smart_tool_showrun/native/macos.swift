// Fixed JSON-line bridge: one bound window, ScreenCaptureKit and Accessibility.
// No shell, arbitrary code, coordinate fallback, app launch, or permission prompts.
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

    func screenshot() async throws -> [String: Any] {
        try check()
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

    func act(_ request: [String: Any]) throws -> [String: Any] {
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
        let result: AXError
        if request["action"] as? String == "click" {
            result = AXUIElementPerformAction(element, kAXPressAction as CFString)
        } else if request["action"] as? String == "fill", let text = request["text"] as? String {
            guard string(element, kAXSubroleAttribute) != kAXSecureTextFieldSubrole else { try fail("sensitive_surface") }
            result = AXUIElementSetAttributeValue(element, kAXValueAttribute as CFString, text as CFString)
        } else { try fail("invalid_action") }
        refs = [:] // Never replay a reference after an attempted effect.
        guard result == .success else { try fail("desktop_action_uncertain") }
        return ["status": "returned"]
    }
}

@main struct Main {
    static func main() async {
        guard #available(macOS 14.0, *) else { print("{\"error\":\"desktop_unsupported\"}"); return }
        let bridge = Bridge()
        while let line = readLine() {
            var result: [String: Any]
            do {
                guard let data = line.data(using: .utf8), data.count <= 65536,
                      let request = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                    try fail("invalid_request")
                }
                switch request["operation"] as? String {
                case "bind": result = try await bridge.bind(request)
                case "observe": result = try bridge.observe()
                case "screenshot": result = try await bridge.screenshot()
                case "act": result = try bridge.act(request)
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
