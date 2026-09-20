# Verification — 2026-09-20

| Layer | Result |
|---|---|
| Shared model/verifier plus existing desktop/process checks | 26 pytest checks passed |
| Python lint | Passed for source, tests and fixture scripts |
| macOS AppKit app | Compiled; all six widget scenarios passed in a real app window |
| macOS render | Inspected the app-rendered preview, including revealed field and relocated Save |
| Linux GTK app | All six widget scenarios passed under Xvfb in Ubuntu 24.04 |
| Linux external accessibility | All six scenarios passed through AT-SPI against fresh app processes |
| Linux render | Inspected the actual GTK window preview from Xvfb |
| Windows WinForms app | Cross-compiled successfully with zero warnings/errors; not executed on Windows locally |
| Native CI | Added macOS, Windows and Linux jobs; no remote CI result claimed here |

The Linux external trial exposed a modal interaction issue: a blocking nested
dialog loop prevented the external driver from reaching confirmation. The app
now uses GTK's response callback and returns to its main loop. The driver pumps
accessibility events and refreshes observations when controls/windows change.

Widget self-tests call real controls/handlers inside the apps. The Linux external
trial instead edits fields and invokes controls through AT-SPI, then checks the
persisted model record with the shared read-only verifier. Neither test uses
inference, and neither records a Showrun video. Native macOS external-driver
verification still needs the previously identified OS permissions. Native
Windows UI execution is left to a Windows host/CI.

Trial state, logs, compiled applications and previews were retained outside the
repository. Temporary test containers were removed. No user's application data
was read or changed.
