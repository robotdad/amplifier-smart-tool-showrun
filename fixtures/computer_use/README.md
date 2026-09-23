# Native computer-use fixtures

One small application, implemented with **AppKit, WinForms and GTK 3**, with a
shared Python data model and scenario specification. The apps contain fictional
data only. This directory is self-contained; it needs no Showrun model or runtime
to build apps, run widget checks, or verify persisted state.

These are deliberately test applications, not substitutes for a real product
demo. Their purpose is to expose differences in native controls, accessibility,
dialogs, delayed operations and recovery before operating a person's application.

## Shared scenarios

Every external trial starts a fresh app and an absent output directory.
`scenarios.json` owns the instructions, expected state, ordered effects and
support limits. The six scenarios are:

| Scenario | Behavior tested |
|---|---|
| `save-task` | Disabled Save becomes enabled after entering text; save persists |
| `details` | Reveal a hidden field and save its value |
| `dialog` | Open and confirm a native modal dialog |
| `delayed-save` | Observe pending work and eventual completion |
| `cancel-save` | Cancel before the deadline; verify no later save occurred |
| `replace-control` | Move and replace Save; discard the old control reference |

The same model runs as a private child of each native UI. Native event handlers
send commands on its private stdin pipe. There is no HTTP mutation endpoint or
harness command to complete tasks through the backend. Computer-use agents must
use the UI. The verifier reads `state.json` independently and checks run identity,
persisted values, effect order and duplicate effects. Editing this file or calling
the state host directly invalidates an external UI trial.

## Build and launch

Run commands from the repository root with Python 3.12+. Use a fresh `--run`
directory for each attempt; a prior failed attempt is never overwritten.

macOS needs Apple Command Line Tools (`xcrun swiftc`). The build produces an app
bundle with ID `org.showrun.ComputerUseFixture`:

```sh
python fixtures/computer_use/harness.py build --platform macos --output .fixture-build
python fixtures/computer_use/harness.py launch --build .fixture-build/macos.json --run .fixture-runs/save-01 --scenario save-task
```

Windows needs the .NET 8 SDK and desktop runtime. Run the same commands with
`--platform windows` and `.fixture-build/windows.json`. The project can be
cross-built on macOS/Linux, but running WinForms requires Windows.

Linux uses GTK 3 and the system Python with PyGObject. On Ubuntu install
`python3-gi gir1.2-gtk-3.0`; use `--platform linux` and
`.fixture-build/linux.json`. `build --gtk-python` selects another GTK-enabled
interpreter. Run inside a graphical session. Xvfb provides an isolated test display
in CI; that is not a claim about Wayland desktop-control support.

The launcher prints readiness and instructions, then waits while you or a driver
operate the native app. It verifies state after the app closes. You can also
verify while it is open, from another terminal:

```sh
python fixtures/computer_use/harness.py verify --run .fixture-runs/save-01 --scenario save-task
```

Outputs include `run.json`, `ready.json`, `state.json`, `app.log` and the final
`result.json`. App state is atomically replaced after each mutation. Each event
has a sequence number, and delayed work captures the input value at dispatch.
The cancellation verifier refuses to pass before the original deadline.

## What each test proves

`self-test` launches a real native app and invokes its widget handlers for all six
behaviors, including a modal confirmation and elapsed/cancelled delayed work:

```sh
python fixtures/computer_use/harness.py self-test --build .fixture-build/macos.json --run .fixture-runs/widgets-01
```

It writes a `widget-preview.png` for render inspection. **This is a widget test,
not an external accessibility-driver trial or a recorded demonstration.** Unit
tests of the model are a third, separate level of evidence.

Linux also has a real external AT-SPI trial runner. It selects only the fixture
child's PID, edits native fields through accessibility, invokes native controls,
reacquires replaced controls and checks persisted state afterward. It never calls
the private state host. On Ubuntu install `python3-pyatspi xvfb xauth dbus-x11`,
then run in a dedicated display/DBus session:

```sh
dbus-run-session -- xvfb-run -a /usr/bin/python3 fixtures/computer_use/linux/atspi_trial.py --output .fixture-runs/atspi-01
```

Use `--scenario dialog`, for example, to run just one scenario. A failure retains
the app log and an accessible-tree diagnostic. A container version is available:

```sh
docker build -t showrun-fixtures -f fixtures/computer_use/linux/Dockerfile fixtures/computer_use/linux
docker run --rm -v "$PWD/fixtures/computer_use:/fixture:ro" -v "$PWD/.fixture-runs/container-01:/output" showrun-fixtures
```

The external runner proves accessibility interaction and fixture state. It does
not prove Showrun native Linux support, model competence, or video quality.
The CI workflow builds/runs native widget scenarios on all three OSes and runs
external AT-SPI scenarios on Linux. Windows UI execution still needs a Windows host.

## Use with Showrun on macOS

The AppKit executable accepts the optional fixture-only `--title-after-save TITLE`
argument. Its normal Save button handler changes the window title after saving,
allowing external drivers to test binding identity across a real UI-triggered
title change. This does not expose a backend mutation endpoint.

A deterministic external trial of the exact signed v0.5.0 candidate verified
missing/ambiguous window errors, a nonmatching initial title, selection by exact
title across two fixture processes, AX fill/click with independently verified
persisted state, and no changes to the second fixture. A terminal-mode bridge
retained the same window through the Save-triggered title change. Window-only
before/after PNGs decoded at 680 x 502; OCR showed `Saved task: none` followed by
`Saved task: Example task`. This is not terminal typing or video watchability
acceptance. Exact-version candidate qualification is tracked in
[`desktop-v0.5.0` release notes](../../docs/releases/desktop-v0.5.0.md).

Keep a fresh `launch` running in one terminal. Generate a request in another:

```sh
python fixtures/computer_use/harness.py showrun-request --run .fixture-runs/save-01 --request-id native-save-01 > /tmp/native-request.json
showrun validate /tmp/native-request.json
```

This uses the app's actual window title and pixel dimensions; it does not assume
Retina scale or hard-code desktop coordinates. After preparing Showrun and granting
its helper Screen Recording/Accessibility permissions, use the normal `record`
operation with an explicitly selected image-capable provider/model. Model access
is a separate requirement; no command above invokes inference.

Generated requests currently cover save, details, delayed save and replaced
controls. The fixture also provides dialog and timed cancellation challenges, but
the current Showrun native backend does not claim reliable support for them; request
generation refuses those scenarios. Generated native requests remain macOS-only. Windows has an experimental backend
with manually composed requests; native Linux execution remains unsupported.

Verify the fixture state separately after recording, inspect the receipt, and watch
the decoded footage. Keep those three results distinct. No fixture pass certifies
viewer comprehension or that all changes were captured on video.

## Windows external UIA and capture trial

After `showrun prepare-desktop --build`, run this inside the logged-in Windows
desktop (or launch it with a limited interactive Scheduled Task):

```sh
python fixtures/computer_use/windows/uia_trial.py --build .fixture-build/windows.json --output .fixture-runs/uia-01
```

This is a deterministic external driver using Showrun's actual Windows companion,
not a widget-handler self-test or live model trial. It fills through UI Automation,
moves/replaces Save, rejects an old reference, saves through UIA, and independently
verifies persisted state. It also resizes and records the real window, decodes the
MP4, and closes only its own fixture. Requires Showrun's Windows dependencies
(including pywin32 through the Agent installation), .NET 8 and FFmpeg.

Verified on Windows 11 on 2026-09-20: external trial passed, 800×600 MP4 decoded,
7.2 seconds; sampled footage showed the saved value and moved button. Companion
launch from an SSH caller in session 0 reached the logged-in desktop in session 1.
This does not certify third-party app compatibility or model-driven performance.


## Windows Notepad live trial

On 2026-09-20, a public `Showrun.record` call using OpenAI gpt-4.1 operated Windows
11 Notepad through the companion from an SSH caller. One model call selected the
observed editor and one UIA fill replaced the demo text. The field assertion and
five-second hold passed; the decoded 1280×720 recording was 9.64 seconds and the
final text was visually inspected. Notepad stayed open, with unsaved demo text;
the companion and its scheduled task were removed.

The initial attempt exposed UIA carriage-return normalization and Notepad's
editing-driven title change. The bridge now normalizes observed line endings and
retains the originally bound HWND/process identity across title changes. Earlier
failed takes were preserved. This validates a basic text editor, not games,
arbitrary applications, saved files, or general keyboard input.

A subsequent paced-entry trial used one model call and one fill, with a 450 ms
initial hesitation and small text bursts. The 16.52-second recording included
about 8.6 seconds of entry and a five-second final hold. Decoded intermediate and
final frames showed progressive text and the exact complete result. This Windows
pacing uses UIA values, not physical keystrokes; the original element and prior
prefix are checked before every continuation.

The revised caller-selected `text_entry: paced` trial recorded 27.96 seconds,
including about 19 seconds of character entry and a five-second hold. One model
call and one fill succeeded. Decoded adjacent frames showed `O`, `On`, and `One`;
Showrun captured each Unicode text element before advancing. Timing policy now
lives in Showrun, and the Windows adapter supplies only incremental entry mechanics.
Immediate fill is again the default; the earlier burst timing is superseded.

## Excel exploratory trial

On 2026-09-20, Showrun created a new blank workbook through an observed native
control. Excel's Unicode direction markers exposed a Windows default-encoding
failure when writing receipt JSON; receipt files now explicitly use UTF-8.

A second live trial attempted A1:B4 labels, numeric inputs, and a SUM formula.
UIA ValuePattern writes returned successfully and subsequent observations echoed
the supplied strings. However, decoded footage and a separate foreground screen
capture both showed an empty grid. A read-only Excel object-model diagnostic of
Book1/Sheet1 A1:B4 returned empty values and formulas. Thus the seven completed
field assertions in that receipt are NOT evidence of successful workbook edits.
The formula step repeatedly issued the same fill until the model budget was
exhausted; the overall take failed. The retained recording was 137.44 seconds.

This exposes an unresolved input/verification gap, not a verified capture bug:
Excel cell ValuePattern readback alone is insufficient. Reliable grid editing
needs a different validated UI interaction path and independent outcome evidence.
No existing workbook was opened or changed. The new unsaved workbook stayed open.

The follow-up grid-input trial succeeded: eight public Showrun cell edits used
observed UIA selection/focus, exact Unicode keyboard input, and Enter to commit.
The 109.72-second 1280×720 recording visibly contained Drink/Count, Water/7,
Juice/9, and Sum/16. An independent read-only Excel object-model check confirmed
numeric values 7, 9 and 16 and the stored formula `=SUM(B2:B3)` in B4.
Eight model calls and eight actions completed with verified companion cleanup.
Focus followed by ValuePattern alone had been tested separately and still did not
change the workbook. The earlier failed takes and their observations remain intact.

The new path applies to observed writable grid items and supports immediate,
nonempty single-line input only. It checks the original cell focus before typing,
bound window/foreground focus and modifiers during entry, and commits with a fixed
Enter. It does not write through Excel COM or expose arbitrary keyboard commands.
Repeated fills that leave the same control unchanged or already matching now stop
with `desktop_no_progress`. Unicode receipt files use explicit UTF-8.

## Excel ribbon and menu trial

On 2026-09-20, the Windows adapter added selection, expand/collapse and toggle
capability discovery. Tabs, menu items and expandable controls use real mouse
clicks at UIA-provided clickable points with foreground/focus and hit-target checks.
A public Showrun take switched Home → View and opened Freeze Panes in three model
calls/actions (46.52 seconds). A second take selected Freeze Top Row in one call
and action (16.48 seconds). Independent read-only Excel state reported
FreezePanes=true and SplitRow=1; table values and the SUM formula were unchanged.

The menu was actionable but missing in decoded PrintWindow footage. Therefore
this is successful mouse/ribbon/menu interaction, not accepted menu-demo capture.
The cursor is also absent from the recording. Popup/cursor capture remains open.
The workbook was left open with its first row frozen. Failed/incomplete visual
results and original recordings were retained.

## macOS Excel input trial

On 2026-09-20, public Showrun recordings entered a new workbook's A1:B4 table:
Drink/Count, Water/7, Juice/9 and Sum/16. Three final row takes completed with
23 model calls/actions in total, at 1280×720 (37.28, 38.60 and 38.84 seconds).
Headers were entered in earlier retained trials. Decoded final frames showed the
results. After recording, the caller saved a local XLSX through the native UI;
read-only ZIP/XML inspection independently confirmed all eight values, numeric
7 and 9, and B4's stored SUM(B2:B3) formula with cached value 16. Saving was
verification setup, not a Showrun-recorded action. All companion cleanup passed.

Excel exposed writable combo boxes for the Name Box but a formula editor without
AXValue writes. The Mac bridge now observes editable combo boxes, invokes their
native Confirm action on click, and uses verified mouse focus when AX focus alone
fails. Rich text readback uses AXStringForRange when AXValue is unavailable.
Non-writable text areas accept immediate, exact single-line Unicode input only
when focused text is independently readable as empty. Committing the edit remains
a separate observed button click. No Excel object-model writes, clipboard, Return
or arbitrary shortcut interface were used for the demo.

An attempted select-all shortcut produced an unwanted equals sign in Excel's
formula editor. The uncertain take was preserved, the uncommitted edit cancelled
through Showrun, and that shortcut removed. Unknown/nonempty editor contents now
fail rather than assuming replacement works. Another take stopped a repeated
Name Box fill with desktop_no_progress; separate address/Confirm steps succeeded.
These trials establish new-cell entry, not general editing of populated cells,
paced Mac typing, popup capture, or arbitrary spreadsheet compatibility. The
changes ship in desktop-v0.3.0 and are not in desktop-v0.1.0.
