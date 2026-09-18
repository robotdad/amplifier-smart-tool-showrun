# Demo performance and capture contract — v1 (DRAFT)

**Who builds against this:** Callers requesting usable demo footage, people reviewing
the recording, and downstream narrative or video post-production tools.

## What it looks like

```text
Right: the footage shows the requested change, then holds long enough to read it.
Right: a partial take identifies the failed step and the changes already made.
Wrong: a valid video file is reported as proof that the requested demonstration worked.
Wrong: an invisible backend write replaces the UI action the viewer was meant to see.
```

## Purpose

Make a recorded performance useful and faithful to the intended flow, without turning
Showrun into a video editor. [Caller interaction](caller-interaction.v1.md) defines
intent and authority; [internal execution](internal-execution.v1.md) enforces them.

## Core (the teeth)

1. **The recording depicts the actual target.** Required actions are performed through
   the target UI. Showrun does not rewrite page content, simulate success screens or
   substitute hidden API/database operations for demonstrated interactions. Caller
   preparation stays distinguishable from recorded performance. Known seeded, mocked
   or simulated target behavior is disclosed without claiming production validity.
2. **A step completes on evidence, not on an issued click.** Showrun observes the UI
   before acting, waits for relevant state changes and checks the requested outcome.
   Outcomes identify what was inspected and the method and limits of the check.
   Deterministic assertions, model assessments and human review remain distinct.
   A visible success message alone does not prove unobserved backend correctness.
3. **The performance is legible.** Requested controls and outcomes remain in the capture
   area with readable framing. Scrolling, typing and holds serve viewer understanding
   rather than merely minimizing execution time. Supported presentation preferences
   are honored; hard timing or framing constraints that cannot be met are reported.
   Model deliberation time is not presented as intentional pacing. The initial
   single-surface take is one continuous recording, starting before the first
   required UI interaction and ending after the final required result hold.
   Thinking and application-wait time remain in the raw footage and are identified
   separately from intentional holds in the receipt. Missing capture intervals
   make the take incomplete; pauses are not silently removed to improve pacing.
4. **Supported capture scope is explicit.** Before performance, Showrun establishes
   the chosen target surface, dimensions and supported media/audio behavior. A
   browser-viewport recording is not a recording of browser chrome or the whole
   desktop. Popups, multiple pages, native dialogs and focus changes are either
   captured under documented support and authority or reported as unsupported.
   Required actions cannot disappear outside the captured surface without failure.
5. **A delivered recording is finalized and inspected.** Success requires nonempty,
   decodable media covering the requested performance. Necessary capture shutdown
   and finalization finish before the artifact is reported ready. The result records
   actual container, dimensions, duration and audio disposition. Requested settings
   are distinguished from actual values; an unsupported format or quality request
   does not silently degrade into a different promise.
6. **Footage and its receipt form an identifiable handoff.** The result identifies
   artifact locations and content identity, take and step IDs, requested versus
   observed outcomes, limitations and checks performed. Step intervals use the
   delivered media's timebase, state units and reference origin, and describe their
   precision. Wall-clock action timestamps alone are not a video timeline. Consumers
   need no private database or agent transcript to interpret the handoff.
   A step interval starts with its first UI interaction and ends after its outcome
   check and requested hold; action dispatch and visible-result observations are
   identified within it where observed. Unobserved boundaries are not invented.
   Media references resolve relative to the delivered handoff directory and carry
   content hashes, so moving that directory does not require the producer's paths.
7. **Partial and transformed material is labeled.** Interrupted or failed takes retain
   useful finalized footage where possible, clearly marked incomplete with the
   coverage and failure reason. Unfinalized media is not advertised as playable.
   Any supported framing conversion or transcode preserves provenance and updates
   artifact identity and timing as needed. No silent trimming, time compression,
   stitching or replacement conceals failed actions or changes apparent behavior.
8. **Capture is not post-production or publication.** The primary handoff is footage
   plus metadata. Editing, titles, narration, soundtrack and assembly of a larger
   story belong to separate tools or the caller. Showrun works without those tools
   and does not require their proprietary project format. Delivering to a selected
   local destination does not authorize uploading, sharing or opening a viewer.

## What v1 deliberately does NOT freeze

- Exact codecs, frame rates, presets, cursor emphasis or pacing controls.
- A requirement to capture system audio, microphone, browser chrome, several
  windows or a complete desktop; support must be explicit and evidenced.
- Automatic removal of thinking time, a replay compiler or guaranteed fixed-duration
  performances. Better takes must not erase the distinction between execution and editing.
- A downstream editor integration or automatic narrative quality rating.

## Showrun acceptance checks

- Clauses 1–2: use a target fixture with known independent state; compare the footage
  and application changes to requested actions. A click that does nothing and a
  misleading success indicator do not pass a stronger requested outcome check.
- Clauses 3–4: review decoded footage for readable controls, visible changes,
  deliberate holds and coverage. Exercise a popup or out-of-scope surface; either
  supported capture covers it or the take reports the limitation without false success.
  Inject a long mid-flow model delay and an action occurring before capture begins:
  the former remains identified in raw footage; the latter prevents complete success.
- Clauses 5–6: open and decode the final artifact, compare its metadata to the
  receipt, and locate each step's reported interval in the media. Copy the handoff
  directory to an unrelated location, then resolve references, verify hashes,
  decode media and locate intervals using only the copied receipt and contents.
- Clause 7: interrupt capture and inject finalization failure; distinguish usable
  partial footage, invalid media and complete output. Do not fabricate intervals
  for steps that were not recorded.
- Clause 8: give another tool the media and receipt without Showrun's session;
  confirm it can identify and use them without automatic publication or editing.

Visual and temporal review must inspect recorded media, not only screenshots or
DOM assertions. Checks of format, behavior and watchability are separate findings.
Before a product acceptance trial, the owner approves a fixture-specific rubric:
required controls and states, capture dimensions, readable text and visible hold
intervals. A human reviewer independent of the executing model records whether
the footage meets that rubric. Model review is diagnostic, not human acceptance
or proof of viewer comprehension; routine operation need not wait for human review.
No acceptance result is claimed by this document.

## Changelog

- **2026-09-18** — Initial draft; no lock or implementation claim.