# macOS desktop companion v0.5.0 - early access

This Apple Silicon macOS 14+ update adds authenticated companion build-version,
protocol and capability reporting, plus distinct missing/ambiguous window errors.
Windows remains pinned to `desktop-v0.2.0`. Historical release assets are unchanged.

## Install

```sh
uv tool install --force 'git+https://github.com/robotdad/amplifier-smart-tool-showrun@desktop-v0.5.0'
showrun prepare-desktop
showrun desktop-status
```

The installer verifies the pinned SHA-256 and app signature. The companion is
ad-hoc signed, not notarized. Updates can invalidate Accessibility and Screen &
System Audio Recording grants even when Settings shows enabled switches. Check
actual `desktop-status` results; build identity is independent of permission
readiness. Older companions without handshake metadata report `unknown` rather
than the Python download pin.

## Changes

- `desktop-status` reports `desktop-v0.5.0`, protocol 1 and advertised capabilities.
- No eligible window or no title match returns `desktop_window_not_found`;
  multiple matches return `desktop_window_ambiguous` before input.
- Accessibility association failures likewise distinguish missing and ambiguous
  matches. Diagnostic candidates contain only IDs, dimensions and title-match
  flags for the requested app, not window titles or other applications.
- Terminal-mode binding remains tied to the selected window through title changes.
  Control mode retains its existing stricter title check.

## Evidence and limits

The exact-version signed candidate passed a deterministic external native fixture trial
on macOS 26.7 arm64: zero windows, two windows, nonmatching title, exact selected
window, actual AX fill and Save click, independent persisted fixture state, and
an unchanged second fixture. A terminal-mode bridge retained the original window
ID after the Save handler changed its title. Before/after window-only PNG captures
(680 x 502) decoded successfully; native OCR confirmed `Saved task: none` before
the click and `Saved task: Example task` afterward. No live provider or personal application was
used. Owned fixture processes were stopped.

Three further repetitions passed during an explicitly coordinated quiet-desktop
interval, with Save succeeding on the first attempt each time and the second
fixture unchanged. Earlier attempts are retained: one rejected a stale reference
before dispatch; another received unrelated typing while the owner was using the
computer. The exact stale-reference cause was not established. The test driver
now waits for stable control observations and reacquires references; it never
retries an uncertain or successful dispatch. Coordinate foreground tests with
the person using the desktop before launching fixture windows.

The exact-version candidate compiles, verifies its ad-hoc signature and reports
consistent handshake/plist/package version metadata. Its first permission check
reported both grants missing. After the owner enabled both grants for that exact
candidate, status reported ready and the native trial passed without rebuilding,
re-signing or changing the archive. The installed companion remained unchanged.

Qualified binary SHA-256:
`2aac9347eb4be1f1d2a8e60e475ac0a56e4de42158f0d7c85ae013d9660b6450`.
Qualified Swift source SHA-256:
`d7d850175c3a0758f9d8ae256bb38707235e03ee2dc2258341e859732f633dbd`.

This is bounded fixture evidence, not a model-driven demo, general terminal
compatibility, full video/watchability acceptance, or AX-association failure
coverage on every application.

Asset: `showrun-desktop-macos-arm64.zip`

SHA-256: `74fd9be87e6474f534225605d1766f6af00b316d6b65cfe690dfb29a2f887aea`
