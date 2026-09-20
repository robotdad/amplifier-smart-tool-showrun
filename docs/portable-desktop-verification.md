# Portable capture and first macOS native slice

Verified locally on macOS on 2026-09-20. This is implementation verification,
not human acceptance of a native demonstration or a live model competence claim.

- Real Chromium/FFmpeg recording, decoding, exact retry and cancellation pass on
  macOS. A direct timing check measured 1.066 seconds captured and 1.080 seconds
  decoded after limiting the encoder's duplicate-tail padding to the capture end.
- The full local suite returned 177 passed, 28 skipped, and one failure in
  `test_library_sort_preserves_selection_draft_and_preference`. That unchanged
  review test passed on an isolated rerun; the full run is not reported as clean.
- Native bridge compilation and packaged wheel/source builds pass. The wheel
  contains the Swift bridge, Python backend and portable process module.
- Native lifecycle tests use a scripted bridge and real FFmpeg output. They cover
  disclosure/action grants, geometry failure before actions, exact retry, uncertain
  lost acknowledgments, screenshot model input, sensitive-output restriction and
  advisory duration drift. These do not prove native OS interaction or watchability.
- The actual native helper returns `desktop_permission_missing` on this host.
  Screen Recording and Accessibility grants are needed before a real native-app
  trial. No live inference was invoked and no user application was operated.
- Process identity uses real macOS creation timestamps and boot-session UUID.
  Linux receipt identities remain unchanged. Windows handle verification has
  simulated tests and a CI smoke job, but was not executed on a Windows host here.
  Native Windows interaction and managed Stories on Windows remain unsupported.
- Skips cover installed Stories/second-interpreter integrations and the explicitly
  prepared model runtime. Existing Linux CI retains those installation checks;
  the new macOS/Windows jobs exercise portable process and browser capture paths.

The packaged operating guide owns request shapes and setup. Timing drift is
reported alongside playable media for downstream editing; invalid media, geometry
mismatch, contradictory step evidence and failed interactions still fail.
