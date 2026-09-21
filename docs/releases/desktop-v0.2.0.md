# Windows desktop companion — early access

This release adds a prebuilt Windows x64 companion and native Windows demos to
Showrun. Tested on Windows 11. The executable includes the .NET 8 Desktop runtime;
users do not need Visual Studio, the .NET SDK, or a separate runtime installation.
The existing macOS companion remains pinned to desktop-v0.1.0.

## Install

Install Python 3.12+, Git, uv, and FFmpeg/ffprobe with libx264. Then:

```sh
uv tool install --force 'git+https://github.com/robotdad/amplifier-smart-tool-showrun@desktop-v0.2.0'
showrun prepare-desktop
showrun desktop-status
```

`prepare-desktop` selects the platform release and verifies its package-pinned
SHA-256. Windows installs `%LOCALAPPDATA%\Showrun\Desktop\ShowrunDesktop.exe`.
It is explicit setup, never an implicit download during recording. Stop active
Showrun takes before upgrading. `--build` remains available to Windows developers
with the .NET 8 SDK; the downloaded build does not require it.

If Git dependency installation reports filenames that are too long, retry with
`core.longpaths=true` for that setup process (Git's `GIT_CONFIG_COUNT`,
`GIT_CONFIG_KEY_0` and `GIT_CONFIG_VALUE_0` environment variables can scope this
without changing global Git settings).

The companion is **unsigned early access**. Windows security prompts or managed
policies may block it. Only use this binary if you accept that trust model; Showrun
does not bypass operating-system policy. A checksum verifies the pinned asset,
not a publisher certificate.

Keep a desktop logged in and unlocked as the same user running Showrun. The
companion launches through a temporary limited-privilege interactive Scheduled
Task and authenticates its loopback connection with a per-run token. An SSH
caller in session 0 can use the logged-in user's desktop. Do not interact with
the computer or disconnect/minimize its RDP session during a take. No isolation,
elevated application access, secure-desktop access, or unattended locked-session
operation is promised.

Recording also requires `showrun prepare-runtime` and an explicitly configured
model/API credential. `showrun record --help` and the packaged skill describe
request shapes and bounded authority. `desktop-status` is provider-free and checks
companion/session availability, not compatibility with a particular application.

## Included and verified

- Exact PID/window-title binding, retained process/window identity, and optional
  resize to the requested capture dimensions.
- Native observation-bound controls and text entry; ribbon/menu clicks use
  accessibility-provided points with foreground/focus and hit-target checks.
- Excel grid entry through focused Unicode typing plus Enter, independently
  verified against actual cell values and a SUM formula. No Excel object-model
  writes are used by the driver.
- Per-step `text_entry`: `immediate` (default), `paced`, or `fast_imperfect`.
  Showrun owns timing and typo corrections. Paced styles are Windows-only and
  currently limited to nongrid fields. Grid entry requires immediate, nonempty,
  single-line text.
- Detection of repeated ineffective fills, plus UTF-8 receipt files on Windows.
- Native fixture, Notepad, Excel cells/formula, and Excel ribbon/menu trials.

## Recording limitations

Capture uses PrintWindow: silent footage, no cursor, background sampling up to
5 Hz plus character samples for paced entry. Some popups are absent from the
video even when interaction succeeds. In the Excel trial, Freeze Top Row worked
and was independently verified, but the open drop-down was not recorded.
Separate windows are not automatically captured. Always inspect decoded footage;
API success and accessibility readback alone do not prove a useful demo.

The caller owns demo content, application setup, credentials, and authority.
Showrun owns performance and recording; the companion supplies observations and
input. The caller-owned application is left open in its resulting state.

## Assets

`showrun-desktop-windows-x64.zip`, `SHA256SUMS`, and `companion.json` describe the
Windows binary. No credentials, trial stores, or personal recordings are included.
The source tag includes installation help, packaging code, and tests.

## Release validation

The packaged ZIP was installed on Ducasse, launched as the limited interactive
user, and exercised by the external native fixture driver. UIA entry/invoke,
stale-reference rejection, resize, decoded video and independent saved-state
verification passed. The final frame was visually inspected. Source validation:
229 tests passed, with 16 optional installed-Stories/second-interpreter integration
tests skipped; 12 installer checks passed separately. Wheel/sdist construction,
Windows self-contained publish and lint passed. This is early-access evidence,
not certification across Windows applications or clean Windows installations.
