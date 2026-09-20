---
smart_tool_format: 1
name: showrun
version: 0.1.0
description: >-
  Performs bounded web demonstrations, navigation-only by default, and returns continuous
  application footage with step evidence. Use for prepared demo dashboards,
  with optional exact Stories comment authority; not generation, login or post-production.
use_cases:
  - Record a prepared pitch deck without taking over human browser tabs
  - Demonstrate an isolated installed Stories revision through its actual dashboard
  - Inspect a prior take without repeating model calls or target interactions
platforms:
  - linux
requires:
  - name: Chromium
    purpose: Playwright headless viewport capture; deterministic metadata works without it.
    optional: true
    install: https://playwright.dev/python/docs/browsers
  - name: FFmpeg
    purpose: Finalizes and decodes captured video; record and inspect require ffmpeg and ffprobe.
    optional: true
    install: https://ffmpeg.org/download.html
  - name: configured-model-provider
    purpose: Record needs an explicitly selected OpenAI or Anthropic model and credentials.
    optional: true
    install: https://github.com/microsoft/amplifier-agent/blob/main/docs/INTEGRATION.md
  - name: Stories
    purpose: Managed Stories targets require an isolated v0.1.0 installation and fixture store.
    optional: true
    install: https://github.com/robotdad/amplifier-smart-tool-stories
---
# Showrun

**The library is the tool.** Compose operations with
`from amplifier_smart_tool_showrun import Showrun`; the CLI only adapts arguments
and JSON. This first slice navigates a prepared application using current visible
DOM observations, not caller-authored automation scripts. It does not generate
target content, click arbitrary buttons, reset data, make arbitrary edits or publish.
An explicit exact-comment grant adds scoped Stories UI fill and submission; it
does not widen navigation-only requests or authorize Stories model use.
Capture itself remains web-only, silent, single-surface and does not accept login,
uploads or clipboard access. The provider-free `review` capability browses retained
demos, plays their original MP4 bytes, and manages exact review metadata; it never
opens the target application or calls a model.

## Install and prerequisites

```sh
uv tool install git+https://github.com/robotdad/amplifier-smart-tool-showrun
# In the selected Showrun installation, explicitly install Chromium:
python -m playwright install chromium
showrun prepare-runtime
showrun manifest
```

Select the Python interpreter belonging to that installation for the browser
install command. FFmpeg must provide `ffmpeg`, `ffprobe` and the libx264 encoder.
`prepare-runtime` explicitly prepares Amplifier Agent modules and may download
code. It does not call a model. Ordinary requests never install dependencies.
Readiness and prepared-bundle snapshots are scoped to the actual interpreter
(symlink aliases normalized), environment prefix, Python/Agent version and Agent's
bundle-manifest hash. Preparing a wheel installation does not replace a source
installation's snapshot. Prefix, prepared-snapshot checksum and resolved
module-directory presence checks remain mandatory. These checks do not hash or
attest every file inside those module directories. A moved/deleted installation
requires explicit preparation again.
There is no live model configuration default, fallback, automatic model switch
or provider retry. Initial provider support is OpenAI and Anthropic; use a concrete
model identifier rather than a moving `-latest` alias. Live model competence and
human acceptance require separate trials; deterministic tests do not prove them.

The library owns its observe/decide/act loop and calls the selected provider's
`complete(ChatRequest)` directly inside a fresh Amplifier Agent session. It does
not run the stock `session.execute` loop or expose Agent tools to the model.
OpenAI transport is non-streaming, including the SDK path. Provider and SDK
transient retries are zero. Because OpenAI provider truncation/continuation
logic is separate from `max_retries`, an instance-local dispatch gate also
rejects incomplete responses and any second inference dispatch before it can
spend again or raise the token budget. Cached provider files are not modified.

## Library and request

```python
from amplifier_smart_tool_showrun import Showrun

api = Showrun(storage="/tmp/my-isolated-takes", model={
    "provider": "openai", "model": "CALLER_SELECTED_CONCRETE_MODEL",
    "credential_env": "OPENAI_API_KEY",
    "response_tokens": 2048,
    "reasoning_effort": "low",
})
request = {
    "request_id": "demo-take-001",
    "target": {
        "kind": "url", "url": "http://127.0.0.1:8080",
        "origins": ["http://127.0.0.1:8080"],
    },
    "starting_state": "My prepared deck",
    "steps": [
        {"id": "title", "instruction": "Show the deck title",
         "visible_text": "My prepared deck", "hold_seconds": 3},
        {"id": "final", "instruction": "Navigate to the final slide",
         "visible_text": "Thank you", "hold_seconds": 3},
    ],
    "authority": {
        "navigation_only": True, "disclose_dom": True,
        "max_seconds": 180, "max_model_calls": 12, "max_actions": 30,
    },
}
api.validate(request)  # no model, target or browser startup
# receipt = api.record(request)  # explicit paid/authorized execution
```

Optional model controls are library constructor fields: `response_tokens` is an
integer from 512 through 4096, default 2048 per call. That fixed allowance gives
reasoning models room for both internal reasoning and short action JSON; it is
not an automatic escalation policy or a guarantee that every response fits.
`reasoning_effort` accepts `low`, `medium` or `high` only for OpenAI. The default
is explicitly `low` on supported reasoning models (including `gpt-6-astra`),
not the provider's inherited default. For a non-reasoning model it is omitted;
explicitly requesting it there fails rather than silently ignoring it. Other
model-specific rejections are terminal, with no parameter correction/retry.
Anthropic does not accept this optional reasoning field in the MVP.
The current CLI uses these defaults; use the library for explicit overrides.
No caller-selected provider or model is replaced.

The caller supplies semantic instructions and observable checks, not selectors.
Steps require `visible_text`, `assertions`, or both (all must pass). The model chooses
observed authorized controls or bounded navigation keys. Required checks hold for
at least three seconds. `starting_state` is also required visible text.
`context` is optional caller content (not a file reference). Page text never
changes permissions. Only one exact HTTP(S) origin is allowed for resource traffic
and interaction. Non-read requests are denied except fixed Stories read APIs and
the exact granted comment transport described below.
Arbitrary applications requiring other resources or mutations are unsupported.
Labels are conservatively limited to navigation; no free-form click or script tool.
Unresolved duplicate navigation labels fail with `ambiguous_navigation` before
a click; give the controls distinct accessible labels. Stale decisions reobserve
within the original model/elapsed grant rather than clicking a substitute.
Both clicks and keys are bound to the observation generation, visible state,
document/frame identities and navigation controls. Preconditions are checked again
at the action boundary. Known rejections before dispatch consume no action and
are recorded `not_dispatched`; a durably reserved attempt with no return remains
uncertain. This is not an atomic snapshot of a concurrently changing application.
After navigation a bounded, model-free render observation window precedes another
decision. This does not guarantee arbitrary applications finish within that window.
Sandboxed opaque `srcdoc` previews are supported; they grant no extra origins.

`capture` optionally supplies even `width` and `height`, each 320–3840.
Defaults are 1920×1080, square pixels. The isolated viewport uses those exact
dimensions; no resizing, stretching, padding or cropping to fit source content.
Applications must be prepared to render at the chosen viewport dimensions.
Viewport capture excludes browser chrome and all other human windows/tabs.

## Managed Stories

First prepare a fixture from supplied exported presentation content, not a live
Stories store. The library operation is
`Showrun.prepare_fixture(presentation, destination, python)`; the thin CLI is:

```sh
showrun prepare-fixture exported.json --destination /tmp/new-demo-fixture --python /absolute/stories/bin/python
```

The input object contains `title`, `html`, optional `sources`, `purpose` and
`audience`. Retained source metadata is filtered to the public import fields
`id`, `name`, `content`, `kind`, `attribution`; the original input is not modified.
Media assets/documents are not imported by this narrow presentation fixture path.
Use an absent or empty caller-owned destination with an existing parent.
Existing stores and symlinks (including ancestor or contained symlinks) are refused.
The operation imports through public `Stories.create_story`, returns a supplied
content hash, and records exact imported story/revision/public-content identity.
It does not launch a dashboard, call a model or discover/copy any live store.

Use the returned `target` unchanged. Its shape is:

```json
{
  "kind": "stories",
  "python": "/absolute/isolated-stories/bin/python",
  "storage": "/absolute/isolated-fixture-store",
  "story_id": "story_ID",
  "revision_id": "rev_ID",
  "fixture_sha256": "64-lowercase-hex-characters-returned-by-prepare-fixture"
}
```

Supported library: `amplifier-smart-tool-stories==0.1.0`, public API shape at
commit `25d6bb06d2f41336593edc5df9ddb7d3889a894e`. The fixed packaged helper
runs in the selected installed interpreter with `-I`, an isolated HOME and only
PATH/LANG/PYTHONUNBUFFERED forwarded. No Showrun credentials go to Stories.
It calls `Stories(storage, model_env=False, execution="queued")`,
`list_stories`, `get_story`, `get_revision`, `get_review_view`, `start_dashboard`, and `stop_dashboard`. No arbitrary target
imports, fresh launch code, live-store discovery or target installation.
The returned endpoint must be HTTP on numeric loopback. Readiness checks the
exact selected revision and actual rendered starting-state text, not just a socket.
Before model/browser startup, the positive preparation marker, directory identity
and exact public content are validated through that installed interpreter. The
launch helper repeats this check immediately before launch. Wrong revisions,
changed presentation/authority state, additional stories, missing markers and symlinks fail closed.
New fixture markers use identity version 2: only annotations and drafts are excluded
from the content hash; all other public story and exact revision fields remain bound.
Version 1 markers keep their original full-state hash rules; no migration rewrites
old hashes, request identities or retained receipts. Prepare a fresh fixture for comments.
The fixture remains caller-owned: this prevents accidental use of arbitrary
stores, not tampering by the machine owner or concurrent owner edits.
Failed preparation may leave a partial import; it is preserved, never overwritten.

Shutdown acknowledgment is followed by Linux process-exit verification.
`owned-dashboard.json` retains the acquired service and process identity (PID,
boot ID and start ticks), plus revision/content identity, never the access token.
The public shutdown call is refused if exact identity cannot be verified.
Managed service startup can take up to 25 seconds within the elapsed grant.
For an already-running Stories URL, also set `stories_revision` to the expected
revision ID. Its auth fragment is used only by the isolated browser, never sent
to the model or included in a receipt. That dashboard remains caller-owned.

### Explicit single-comment path

Only a freshly prepared managed Stories fixture supports comment authority; URL
targets remain navigation-only. Initial comments, drafts or feedback grants are
refused before model or dashboard startup. Use a **new take ID** and the returned
target unchanged. Set `authority.navigation_only` to `false` and add:

```python
request["authority"]["stories_comment"] = {
    "story_id": request["target"]["story_id"],
    "revision_id": request["target"]["revision_id"],
    "text": "The exact caller-approved comment",
}
request["steps"] = [
    {"id": "hide", "instruction": "Hide review",
     "assertions": [{"kind": "control", "label": "Show review", "visible": True},
                    {"kind": "review_panel", "visible": False}]},
    # Add intent-level navigation steps with visible_text outcomes here.
    {"id": "restore", "instruction": "Restore review",
     "assertions": [{"kind": "control", "label": "Hide review", "visible": True},
                    {"kind": "review_panel", "visible": True}]},
    {"id": "comment", "instruction": "Comment on the whole story, fill the exact authorized text, then Send.",
     "assertions": [{"kind": "retained_comment"}]},
]
```

`control` checks a visible button/link's exact accessible label, independently of
whether it is enabled or authorized to click. `review_panel` checks the installed
Stories review state and actual review-control/composer visibility. `retained_comment`
requires both visible submitted text (never textarea contents) and independent public
`Stories.get_story` readback: one annotation, exact story/revision/text, whole-story
anchor, `awaiting_authority`, no operation/result revision/responses or feedback grant.
Its evidence returns `comment_id`, `revision_id`, `status`, `operation_id` and count.
At least one retained-comment assertion is mandatory with this grant.

Only main-dashboard `Comment on story`, its exact comment textarea, and `Send` gain
action refs. The model chooses them from current observations; code does not run
a prescribed interaction sequence. One exact fill and one Send attempt are allowed.
Input values are not disclosed; the grant's exact allowed text is provided for fill.
No acceptance, regeneration, reply, delete, settings, model grant or arbitrary input.
Stories stays `model_env=False`, without feedback authority: the submitted comment
is retained awaiting authority, never used to generate content.

Transport admits only the real UI's exact `save-draft`/`add-comment` payload fields,
the selected revision and whole-story anchor, from the bound main frame. One draft
identity, increasing sequence numbers, at most eight draft writes (empty/exact text),
and one submission are enforced. UI attempts and each network effect are durably
recorded before dispatch, including concurrent autosaves. Unknown submission effects
are never resent. A new take against a fixture with retained review state is refused;
inspect it and prepare a fresh fixture rather than silently duplicating the comment.
Draft clearing is a bounded UI side effect, not evidence of submission. Transport
`dispatched` entries do not assert server completion; the independent readback does.

## Lifecycle, budgets and retries

`record` runs synchronously in the caller process, not a background job service.
Another process can call `status(request_id)` or `cancel(request_id)` in the same
store. Cancellation is an acknowledgment; poll until terminal and inspect
`cleanup`. Caller death may leave uncertain work and incomplete resources.
The helper attempts service cleanup on pipe EOF; no crash-restart guarantee.
Inspect `owned-dashboard.json` and the isolated target store if cleanup is uncertain.
Never kill a process by its port or assume a shutdown acknowledgment proves exit.
Retained running owners with a reused PID, different boot, missing legacy identity
or unreadable process identity are reported `uncertain`, not live. Where pidfd
signaling is unavailable, failed helper cleanup remains uncertain rather than
falling back to a blind PID signal.

A SQLite transaction durably reserves the caller's request ID before model or
target effects. Scope: that store, retained indefinitely until the caller explicitly
deletes it. Effective defaults, ordered steps, target/configuration and model
selection are compared using canonical sorted JSON and SHA-256; credential
values are never persisted. Exact retry returns the same retained take, including
failed or uncertain outcomes, with **no new execution**. Different inputs under
that ID fail with `request_conflict`. New intent/retake requires a new ID.
Never delete the store and assume an old key still prevents replay.

## Retained capture review

Review is library-first and independent of capture lifecycle:

```python
from amplifier_smart_tool_showrun import ReviewStore

review = ReviewStore("/tmp/takes")
state = review.open_workspace("default")
```

The review index reads only direct child take directories of the configured store.
Legacy single takes become one explicit demo each; similar names never merge. New
grouping uses `register_demo` and `attach_take`. `workspace()` returns the versioned
selection `{demo_id, take_id, clip_id, media_id, content_sha256}` and any
invalidation or playback state. A fresh caller can read the same state without a
provider, target, browser or original conversation.

`select_clip` and `set_playback` target stable identities. `rename` uses an expected
metadata version. `prepare_delete` returns a scope/version-bound confirmation
snapshot; `delete` removes only exclusively owned retained media, preserves receipts
and request tombstones, refuses active/uncertain work, binds all retries to one durable
commit result, and reports incomplete or uncertain cleanup without inferring success
from a missing file. Review note/draft text and old operation results are revoked with
the affected clip. `save_draft`, `submit_note` and appearance changes use the returned
workspace version as a compare-and-swap token; stale writers fail rather than overwrite.
`save_draft` and `submit_note` validate a step/time/range anchor against that exact
clip; a draft is never submission evidence. `download_mp4` returns original bytes.
`download_zip` includes all authorized takes in the explicit demo, receipts, public
review metadata and a hash/completeness manifest; the final packaged bytes must still
match their retained receipt hashes. Missing, restricted, changed or revoked material
keeps the package incomplete and is disclosed. Construct a remote-facing library,
CLI, service or MCP adapter with an explicit workspace/demo scope; an identifier does
not widen that scope.

Review mutation recovery uses one canonical, versioned target envelope. Its durable
state transitions are:

```text
pending -> prepared -> completed_unacknowledged -> acknowledged
pending/prepared -> rejected       (known no-effect validation or conflict)
any retained state -> revoked      (deleted target or withdrawn scope; payload/result redacted)
```

`begin_intent` admits the exact workspace, demo, take, clip and media identity before
persistence. A lost response retries the same request and intent identity; an
acknowledged intent is history and is never restored as a pending action. An explicit
new intent may use identical text. Deletion revokes pending and completed recovery
records, notes, drafts and operation receipts together. ZIP transfers pin the exact
member identities and bytes admitted at preflight: a later non-destructive take,
rename or note does not retarget or expire the transfer, while deletion, media
replacement or scope withdrawal fails with a bounded actionable error.

The optional authenticated local dashboard is started from the same library:

```sh
showrun --storage /tmp/takes review serve --workspace default
```

The local URL contains a one-time bootstrap token and redirects to a token-free
SameSite session cookie. Cookie mutations require same-origin Origin/Referer,
`application/json`, and that session's `X-Showrun-CSRF`; explicit bearer API calls
are separate. `showrun-mcp --storage /tmp/takes --workspace default` serves the
same controller/layout/capabilities over official stdio MCP Apps, with bounded
resources and the declared workspace scope. Install the optional MCP extra for that
adapter (`uv pip install '.[mcp]'` from a checkout). Neither review surface requires model configuration.
Named workspaces are honored by both adapters. Drafts and playback positions are
retained per clip. Incomplete ZIP inventories are shown before the browser asks
whether to download; cancelling leaves the retained assets untouched. CLI exports
refuse to overwrite existing files.

Build the packaged MCP interface with Node.js 20 or newer:
`npm ci --prefix mcp-app && npm run build --prefix mcp-app`.
The generated HTML is committed so installed Python packages need no Node runtime.
If a configured registry mirror cannot supply a locked dependency, use
`npm ci --prefix mcp-app --registry=https://registry.npmjs.org`.

CLI review state also includes notes and pending intent receipts. `review notes`,
`review playback CLIP_ID --time-seconds N`, and
`review appearance dark --expected-version N --request-id ID` expose their library
operations. Recovery uses `review begin-intent ID --kind save_draft --payload FILE`,
`review ack-intent ID`, or `review reject-intent ID --error-code CODE --message TEXT`.
Use the same `--workspace` and optional `--demo` scope throughout.
Pre-hardening terminal receipts and artifacts are not rewritten by migration.
Legacy managed requests without a fixture hash can still return an exact retained
result; they cannot launch under a new ID. Structural validation of a new request
requires the hash; actual record preflight verifies the fixture content.
A review/refinement uses a different request ID and preserves both videos and
receipts. Review notes are retained separately and never trigger a new recording.

Authority caps: 180 elapsed seconds, 12 provider calls and 30 UI actions;
callers may lower them. Startup and reasoning consume that grant. No repairs,
fallbacks or budget escalation. Separate bounded cleanup follows: up to 40 seconds
for media finalization/decoding, 16 for browser closure, 18 for dashboard cleanup,
5 for Agent shutdown. Frame storage has a hard 512 MiB cap.
The ordered flow stops at the first required failure; later steps stay unattempted.
Dispatched actions with no observed return are uncertain, not automatically retried.

## Footage and receipts

Each take lives under `<storage>/<request_id>/`, with `receipt.json` and, when
verified, `capture.mp4`. Copy the entire directory for a portable handoff.
All public artifact paths are relative and carry SHA-256 hashes. Capture starts
before first navigation and ends after result holds. Chrome compositor timestamps
define media origin zero; FFmpeg preserves their durations at 25 fps, including
all thinking/wait time. No editing, trimming, stitching or time compression.
MP4 H.264, no audio. Static compositor frames are held until the next frame.
The receipt states 80ms timing precision and sampled-capture limitations.
Per-step checks identify DOM evidence, first interaction, visible result and hold
times; steps already satisfied without interaction have a null first-interaction.
Model deliberation is labeled separately from intentional holds.

Success requires all checks, finalized decodable nonempty media with the requested
geometry and verified cleanup. Media validity is not proof of human readability
or unobserved backend behavior. Review actual video independently.

Default state: `$XDG_STATE_HOME/showrun` or `~/.local/state/showrun`; override
`storage` in the library or `--storage` in the CLI. Retained records/footage are not
automatically deleted. Use prepared nonsensitive data. Visible DOM is disclosed
only to the selected provider. A detected password surface or known credential
exposure stops the flow and withholds all capture material in a private
`restricted/` directory. General secret detection is not guaranteed. Restriction
does not undo earlier authorized provider disclosure. No release/delete API is
included; owner intervention is required. Private storage is not isolation from
the machine owner.

## CLI and recovery

```sh
showrun manifest
showrun validate request.json
showrun --storage /tmp/takes --provider openai --model CALLER_SELECTED_MODEL record request.json
showrun --storage /tmp/takes status demo-take-001
showrun --storage /tmp/takes cancel demo-take-001
showrun --storage /tmp/takes inspect demo-take-001
```

Global options precede the capability. JSON results are stdout; diagnostics are
stderr. Terminal failure/partial/cancelled/uncertain/restricted outcomes exit
nonzero. `status` is passive, `inspect` checks delivered hashes/decoding, neither
boots a provider. Errors identify a stable code, safe message and remedy.
There are no interactive prompts. `-h` is terse; `--help` is this library skill;
every capability also has `--help`. For composition, use the library rather than
parsing CLI output.