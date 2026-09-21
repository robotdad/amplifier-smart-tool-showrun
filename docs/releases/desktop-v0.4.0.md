# macOS desktop companion v0.4.0 - early access

This release adds experimental native terminal input on Apple Silicon macOS 14+.
The companion is ad-hoc signed, not notarized. Windows remains pinned to
`desktop-v0.2.0`; Windows terminal input needs adapter work and is not supported.
Linux native app support is tracked in issue #1.

## Install

```sh
uv tool install --force 'git+https://github.com/robotdad/amplifier-smart-tool-showrun@desktop-v0.4.0'
showrun prepare-desktop
showrun desktop-status
```

The installer downloads the prebuilt companion and verifies its pinned checksum
and signature. No Swift compiler is needed. Existing macOS editable-control
support from v0.3.0 remains available.

## Terminal recording

Use `target.kind: "macos"`, the terminal application's `bundle_id`, and
`input_mode: "terminal"`. The caller prepares a clean, dedicated single-pane
window. `window_title` may be omitted when the app has exactly one eligible
on-screen window. Ambiguous selection fails before input. Once bound, the same
terminal window remains selected through title changes.

Explicitly grant `type` and `key` with exact allowed text and supported keys.
Typing appends single-line text; it does not clear the prompt or press Enter.
Submission is a separate observed action. Stale references, changed focus,
ungranted input and repeated identical terminal input are rejected.

Showrun owns the recording and its bounded input. The caller owns the shell,
commands, coding-agent permissions and target-agent spending. Cancellation stops
capture; it does not terminate an already-running command. This is foreground
control on the user's desktop: pause other typing while the take runs.

The live macOS Terminal.app trial launched `copilot`, entered the demo request,
and captured the coding agent's exchange and results. The published edit is
not an uninterrupted successful take: original failed and partial receipts were
retained, and the recording's completion-text mismatch required cooperative stop.
This is evidence for the tested Terminal/Copilot path, not all terminal emulators.

Ad-hoc updates can invalidate existing OS grants. If status reports missing
permissions, remove and re-add Showrun Desktop in Accessibility and the upper
Screen & System Audio Recording list, not System Audio Recording Only.

Asset: `showrun-desktop-macos-arm64.zip`

SHA-256: `913cbf9d0c5b5835440702daf27f7a7b674047824085d68566f6ac6ca89eefc9`
