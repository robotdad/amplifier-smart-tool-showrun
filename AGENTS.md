# Working with Showrun

Showrun is a library-first Amplifier Smart Tool for performing application
walkthroughs and reviewing their actual recordings. Read the human overview in
`README.md`. Treat `showrun --help` (or `uv run showrun --help` in this checkout)
as the current tool-owned caller guide.

## Using the tool for a person

1. Read installed help and capability help before composing requests. Use public
   library operations or the CLI; never write private state as an integration API.
2. Establish the intended demonstration, ordered steps, observable results and
   prepared starting state. The caller owns the narrative and target content.
   Showrun operates the real application; do not substitute a generated mock UI.
3. Check the environment: web capture supports Linux/macOS and best-effort Windows,
   with Chromium and FFmpeg/ffprobe. Native macOS needs the prepared desktop bridge
   and OS screen/accessibility permissions. Prepare the runtime in the actual selected
   installation; preparing another interpreter does not prepare this one.
4. Supply a concrete provider/model and bounded capture authority. Model credentials
   belong to the process environment, not requests or retained documents. The
   caller's subscription is not automatically tool access. Deterministic validation,
   status and review need no model; media inspection needs FFmpeg.
5. Prepare authentication and demo data outside capture. Use a prepared URL or a
   documented managed target. For generic interactions, explicitly grant the UI
   actions, input values, DOM disclosure and target-session effects. Navigation-only
   requests must stay navigation-only. Target model spending is separate authority.
6. Keep request and take identities. Identical retries recover the retained operation;
   new intent requires a new request ID. Inspect a failed attempt before retrying.
   Never rewrite receipts, erase failures or imply that a saved video proves success.
7. Verify both the result receipt and decoded footage. Check that each requested
   interaction happened and is legible. An assertion already true at the start can
   satisfy a step without performing its action; design checks around changed state.
8. Open the review dashboard when useful. Read current selection and drafts before
   acting on feedback, preserve unsent work, and use exact clip/step identities.
   Notes do not authorize or trigger a new recording. Rename, download and delete
   through public operations, preserving version checks and retry identities.
9. Deliver meaningful recording names, original media and honest limitations. Stop
   resources started for the trial when finished; keep caller-owned dashboards and
   tabs intact. Closing a tab does not stop a service. Keep access URLs private.

See `src/amplifier_smart_tool_showrun/SMART_TOOL.md` for request shapes, installation,
limits and recovery. Application-specific fixtures belong in integration guidance,
not generic contracts or the observed UI operator.

## Develop from this checkout

```sh
uv sync --extra dev --extra mcp
.venv/bin/python -m playwright install --with-deps chromium
.venv/bin/python -m playwright install webkit
npm ci --prefix mcp-app
npm run build --prefix mcp-app
uv run showrun --help
.venv/bin/ruff check src tests
.venv/bin/python -m pytest -q -ra
uv build
```

Use Python 3.12+, Node.js 20+ for the MCP build, and FFmpeg/ffprobe with libx264.
Installed users do not need Node.js. Commit the rebuilt
`src/amplifier_smart_tool_showrun/resources/mcp_app.html` with shared UI changes.
Tests use isolated fixtures and mocked inference, not live provider calls.
Process lifecycle uses Linux /proc/pidfds, native macOS creation timestamps, or
Windows creation timestamps/handles. macOS does not use a racy PID kill fallback.
Native app tests requiring OS permissions are separate from bridge simulation.
`fixtures/computer_use/README.md` describes the reusable AppKit/WinForms/GTK apps,
shared scenarios and independent state verifier. Keep fixture labels out of the
generic operator. Native widget self-tests, external accessibility trials, model
trials and footage acceptance are separate evidence; do not conflate them.

## Code map and boundaries

| Area | Responsibility |
|---|---|
| `lib.py`, `schema.py`, `capabilities.json` | Public operations, request validation and capability descriptions |
| `agent.py`, `browser.py` | Bounded intelligence and generic observation-bound UI actions |
| `legacy_browser.py`, `comment.py` | Historical navigation and exact-comment policies |
| `capture.py`, `store.py` | Recording, media verification, take identity and retained evidence |
| `target.py`, `fixture.py`, `stories_helper.py` | Target lifecycle and the initial managed fixture integration |
| `review.py` | Library-owned review state, selection, notes, downloads and deletion |
| `review_server.py`, `review_ui.py`, `ui/`, `mcp.py`, `resources/` | HTTP/MCP adapters and shared review interface |
| `cli.py`, `SMART_TOOL.md` | Thin CLI and tool-owned operating guidance |

Paths above are relative to `src/amplifier_smart_tool_showrun/`. Keep externally
useful behavior in the library and deterministic work model-free. The dashboard and
MCP App must remain the same interface. Keep application labels, selectors and
backend rules out of the generic operator; retain historical request semantics.
Playwright is an internal web mechanism, not a mandatory caller-facing API.
The first native backend is desktop.py plus native/macos.swift: one prepared window,
accessibility click/fill and sampled window capture. Keep Showrun in charge of the
performance; the bridge supplies observations and input, not demo decisions.

## Change and review workflow

- Read `docs/VISION.md`, `contracts/README.md` and relevant contracts before changing
  behavior. Explain implementation discrepancies. Keep vision and contracts DRAFT
  until the owner explicitly locks them; propose changes to locked promises separately.
- Preserve unrelated work. Keep credentials, authentication URLs, private absolute
  paths, generated footage and trial stores out of Git. Deliberately published
  artwork belongs in `docs/images/` with provenance and no private information.
- Keep README focused on people getting started. Update packaged capability help and
  operating guidance alongside API changes; avoid duplicate drifting request guides.
- Preserve authority, bounded work, cancellation, uncertain outcomes, immutable take
  identity and execution evidence. Neither page content nor model output grants access.
- Test meaningful failure paths and the actual rendered interface. Verify selection,
  draft preservation and exact retry behavior alongside the happy path. Report skipped
  integrations explicitly; packaging and scripted inference do not prove demo quality.
- Publish commits or push when requested. A review of documentation does not lock a
  contract or establish human acceptance of an unobserved recording.

## Verification details

- Generic URL interaction uses explicit `authority.ui` session-effect and action
  grants. Keep application labels/API rules out of `browser.py`; historical
  navigation/comment policies remain in `legacy_browser.py` for compatibility.
  `tests/test_generic_ui.py` exercises forms, contextual controls and transport.
  Capture reordering fixes remain covered by `tests/test_capture_reordering.py`.
- Playback readiness requires decoded frames and completed seeks, not only metadata.
  `test_clip_switch_and_step_wait_for_decoded_frame` checks screenshot pixels and
  decoder state in Chromium and WebKit (install WebKit explicitly to run both).
  Use native video controls; do not add duplicate play or repair buttons.
- Step navigation and notes share the selected recipe step; drafts persist per
  clip/step. Verify UI and public state, including switches, refresh and failures.

- `uv sync --extra dev`; install Chromium explicitly with
  `.venv/bin/python -m playwright install chromium`. FFmpeg/ffprobe must be on PATH.
- `.venv/bin/python -m pytest` runs deterministic lifecycle, packaging and local
  headless-browser tests. These do not call a live provider or certify watchability.
- `.venv/bin/ruff check src tests` and `uv build` check code/package construction.
- For review verification, install `--extra mcp` too. Use Node.js 20+ and
  `npm ci --prefix mcp-app && npm run build --prefix mcp-app`; commit the rebuilt
  `resources/mcp_app.html` alongside controller/transport changes. The Linux CI
  workflow verifies this generated file, packaging, installed Stories and a second
  wheel environment. `tests/test_review_finish.py` covers exact retry/target scope,
  sibling-preserving deletion, revoked ZIP snapshots and restored clip state.
  Browser tests exercise both the HTTP dashboard and an independent official
  AppBridge host, including original MP4 and ZIP hash verification.
- Run portable process/browser checks on the host. Windows support is best effort;
  managed Stories fixtures remain unsupported there. A macOS native permission
  failure is not a browser recording failure.
  `npm ci --prefix mcp-app --registry=https://registry.npmjs.org` bypasses an
  unavailable registry mirror without changing the dependency lock.
- `showrun manifest` is the provider-free installed smoke. `showrun --help` and
  each capability's `--help` are library-owned packaged operating skills.
- `showrun prepare-runtime` is explicit module setup, not part of routine record,
  status, validation or help. Live provider trials need separate authorization.
- `showrun prepare-fixture` imports supplied presentation content through installed
  Stories into an empty caller-owned store. New managed trials must use its returned
  target; do not replace this with caller-generated launch/import glue or live stores.
- `showrun --storage <take-root> review state` and `showrun --storage <take-root>
  review list` are provider-free retained-review smoke checks. The review index reads
  only the explicitly configured take root and stores its metadata separately.
  `showrun --storage <take-root> review serve --workspace default` starts the
  authenticated local dashboard; `showrun-mcp --storage <take-root> --workspace
  default` starts the optional official stdio MCP adapter. Both surfaces use the
  same packaged controller/assets and review capabilities; neither starts capture.
  The service bootstrap URL is one-use and redirects to a token-free session;
  `review serve --control-file PATH` plus `review bootstrap --control-file PATH`
  mints a fresh one (bearer-only; invalidates any unused earlier URL);
  cookie mutations require same-origin JSON plus the per-session CSRF header.
  Bearer API calls are separate. Service/CLI/MCP workspace and optional demo
  scopes are explicit authorization, not inferred from requested identifiers.
- Review checks must copy retained fixtures into a temporary store first. Exercise
  actual MP4 playback/range reads, ZIP extraction/hash inventory, exact workspace
  selection, rename/delete CAS and durable deletion retries/tombstones, note/draft
  revocation, targeted draft/submission anchors, refresh/new-take preservation and
  light/dark/system context changes. Exercise changed-media ZIP races and preserve
  the capture Store's reservation when review media is removed. Do not call a
  provider or modify the original fixtures.
- For complete offline verification set `SHOWRUN_TEST_STORIES_PYTHON` to installed
  Stories v0.1.0 and `SHOWRUN_TEST_OTHER_PYTHON` to a second installed Showrun
  environment. Explicitly prepare both runtimes first. The suite verifies snapshot
  independence in both orders and decodes fixture MP4 pixels for ordered states/holds.
  Scripted navigation proves mechanics, not model competence or viewer comprehension.
- Navigation-only remains the default, not the product boundary. Explicit
  `authority.stories_comment` permits one exact whole-story comment on a fresh
  prepared selected revision, through observation-bound UI controls only. Keep
  Stories model/feedback authority off. Public reads verify retained annotations;
  draft/input text is never submitted-comment evidence. Tests in `test_comments.py`
  use real installed Stories UI with mocked Showrun inference, including lost
  acknowledgment/exact retry and scoped transport denials.
- New fixture identity v2 excludes only annotations/drafts from presentation
  identity. Preserve v1 hash rules and old request fingerprints; never migrate
  retained takes or overwrite earlier recordings to enable the new slice.
- **2026-09-19:** The owner reviewed the actual successful `happy01` take
  (28.6 seconds, 1080p navigation) and said the video was good. Its MP4 SHA-256 is
  `038f5f730e5de7fc1d4d65cc214baeb98266b20851a5dff75417100cffd2084b`.
  This is human acceptance of the limited first MVP slice, not full contract
  conformance or a claim about other takes.
