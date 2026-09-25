// Audio verification fixture: a window that alternates a known tone with silence.
// White "TONE ON" = 440 Hz sine at -12 dBFS; black "TONE OFF" = digital silence.
// Picture and sound switch together, so a recording can check signal AND sync.
// Usage: ToneFixture.app/Contents/MacOS/tone-fixture [--period 1.5] [--seconds 60]
import AppKit
import AVFoundation

final class Tone {
    let engine = AVAudioEngine()
    var on = false
    var phase = 0.0

    func start() throws {
        let format = engine.outputNode.inputFormat(forBus: 0)
        let rate = format.sampleRate
        let source = AVAudioSourceNode { [unowned self] _, _, frames, buffers in
            let list = UnsafeMutableAudioBufferListPointer(buffers)
            for frame in 0..<Int(frames) {
                let value = self.on ? Float(sin(self.phase) * 0.25) : 0
                self.phase += 2 * Double.pi * 440 / rate
                if self.phase > 2 * Double.pi { self.phase -= 2 * Double.pi }
                for buffer in list { buffer.mData!.assumingMemoryBound(to: Float.self)[frame] = value }
            }
            return noErr
        }
        let stereo = AVAudioFormat(standardFormatWithSampleRate: rate, channels: 2)!
        engine.attach(source)
        engine.connect(source, to: engine.mainMixerNode, format: stereo)
        try engine.start()
    }
}

final class Delegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    let label = NSTextField(labelWithString: "TONE OFF")
    let tone = Tone()
    var period = 1.5, seconds = 60.0
    var started = Date()
    var shown: Bool?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--period"), i + 1 < args.count, let v = Double(args[i + 1]) { period = v }
        if let i = args.firstIndex(of: "--seconds"), i + 1 < args.count, let v = Double(args[i + 1]) { seconds = v }
        window = NSWindow(contentRect: NSRect(x: 200, y: 200, width: 640, height: 360),
                          styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        window.title = "Showrun Tone Fixture"
        label.font = .boldSystemFont(ofSize: 64)
        label.alignment = .center
        label.frame = NSRect(x: 0, y: 130, width: 640, height: 90)
        label.autoresizingMask = [.width, .minYMargin, .maxYMargin]
        window.contentView!.wantsLayer = true
        window.contentView!.addSubview(label)
        window.center()  // room to resize_to_capture in either direction
        window.makeKeyAndOrderFront(nil)
        do { try tone.start() } catch { label.stringValue = "AUDIO ERROR"; return }
        started = Date()
        update()
        Timer.scheduledTimer(withTimeInterval: 0.01, repeats: true) { [weak self] _ in self?.update() }
    }

    func update() {
        let elapsed = Date().timeIntervalSince(started)
        let on = elapsed < seconds && Int(elapsed / period) % 2 == 0
        guard on != shown else { return }
        shown = on
        tone.on = on
        label.stringValue = on ? "TONE ON" : "TONE OFF"
        label.textColor = on ? .black : .white
        window.contentView!.layer!.backgroundColor = (on ? NSColor.white : NSColor.black).cgColor
    }
}

let app = NSApplication.shared
let delegate = Delegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
