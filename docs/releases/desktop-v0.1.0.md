Early-access macOS companion for Showrun native application recording.

## Install

Install or update Showrun from this release tag, then run:

```sh
uv tool install --force 'git+https://github.com/robotdad/amplifier-smart-tool-showrun@desktop-v0.1.0'
showrun prepare-desktop
showrun desktop-status
```

`prepare-desktop` downloads the Apple Silicon binary and verifies its package-pinned SHA-256 before installation. No Swift compiler or Xcode is required. Developers can use `showrun prepare-desktop --build` instead.

Grant **Showrun Desktop.app** both Accessibility and Screen & System Audio Recording in System Settings → Privacy & Security. The installed app is at `~/Applications/Showrun Desktop.app`; use Command-Shift-G in the add-app picker to enter that path. `desktop-status` checks both grants without inspecting target applications. `ready: false` means setup is incomplete.

## Early-access signing

This binary is **ad-hoc signed, not Developer ID signed or notarized**. Only install if you accept that early-access trust model. If macOS blocks its first launch, inspect Privacy & Security and explicitly allow this app using Open Anyway if you trust the release. Do not disable Gatekeeper globally. Updates can require removing and re-adding permission grants.

Apple Silicon only; binary deployment target macOS 14.0. Live trials were performed on the maintainer's current Mac, not a clean macOS 14 installation. Intel prebuilt binaries, Windows native control and Linux native control are not included.

## Included

- Stable companion app identity, LaunchServices launch and a private authenticated per-run connection.
- Permission checks attributed to the companion rather than its caller.
- Explicit window resizing to capture dimensions, checked against actual pixels.
- Control-bound native clicks and verified text entry, including a bounded empty-editor fallback.
- Observation-only result waiting and recording budgets up to 30 minutes; storage limits still apply.
- Silent named-window capture, up to 5 samples/second. Separate browser windows are not automatically captured.

Recording still needs FFmpeg/ffprobe, `showrun prepare-runtime`, and an explicitly configured model/API credential. `showrun record --help` and the packaged skill describe authority and limitations.

The ZIP, SHA256SUMS and companion.json describe the binary. Failed trial receipts and private recordings are not part of the release.
