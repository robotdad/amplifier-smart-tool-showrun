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
generation refuses those scenarios. Native Windows/Linux Showrun execution remains
unsupported even though their fixture apps exist.

Verify the fixture state separately after recording, inspect the receipt, and watch
the decoded footage. Keep those three results distinct. No fixture pass certifies
viewer comprehension or that all changes were captured on video.
