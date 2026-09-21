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
  - macos
  - windows
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
Capture is silent and single-surface, using either Chromium or the first native
macOS or experimental Windows window backend. Login, uploads and clipboard access are unsupported.
Windows web capture is best effort; managed Stories on Windows is not supported. The provider-free `review` capability browses retained
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
and interaction. In legacy navigation mode, non-read requests are denied except fixed Stories read APIs and
the exact granted comment transport described below.
Other origins remain unsupported. Use the explicit generic UI grant below for forms and mutations.
Legacy labels are conservatively limited to navigation; no arbitrary script tool is exposed.
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

## Generic web application interaction

For an existing web app or smart-tool dashboard URL, set `navigation_only: false`
and explicitly supply `authority.ui`:

```json
{"actions": ["click", "fill", "select", "check", "scroll", "key"],
 "allowed_values": ["Milo", "alex"], "target_effects": "all_in_session"}
```

This grants all target-session effects reachable through the permitted UI actions,
including the application's same-origin API traffic regardless of HTTP method.
Use an appropriately restricted demo session or isolated store; Showrun cannot
promise read-only or no-spending behavior from button labels. Restrict those
capabilities in the target itself. No target-specific button or API rules are needed.
The caller owns startup/authentication for URL targets. Query-bearing entry URLs
are supported; credentials belong in protected access configuration, not context.

Generic assertions also support `{"kind":"field","label":"Walker","value":"alex"}`
or a boolean `checked` instead of `value`; an ambiguous field fails the check.

The operator observes accessible labels, local context, input state and available
select options. It clicks, fills, selects, checks and scrolls with current references;
input/selection values must be explicitly supplied in `allowed_values`. Duplicate
labels with distinct local context can be resolved. Changed controls reobserve;
unrelated page clocks do not invalidate a decision. No arbitrary scripts, URLs,
file transfers, clipboard, popups, WebSockets or cross-origin resources are granted.
Native macOS uses a separate window backend; see Native macOS window below.

Legacy navigation and exact comment requests keep their original restricted
behavior in the compatibility implementation; they do not gain generic authority.

## Initial managed fixture integration

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

Shutdown acknowledgment is followed by platform process-exit verification. Managed
Stories is unsupported on Windows and depends on the installed Stories platform support.
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

## Native macOS window

The first native backend supports macOS 14+ and one already-open, uniquely named
window in a named application bundle. Showrun owns the observe/decide/act loop;
the packaged bridge supplies window screenshots, accessibility observations and
control-bound click/fill operations. Clicks activate and raise the target window,
resolve the selected accessibility control’s current center, and verify that it
is the control under that point before sending a mouse click. This temporarily takes foreground control.
There is no fallback replay after a click. The model receives the screenshot and current
control references. It does not receive arbitrary keyboard, coordinates, shell,
clipboard, app-launch or file access. Inaccessible controls fail explicitly.
Native fill focuses the target application and field, then verifies the entered
value. If an accessibility value write leaves an empty editor unchanged, the
bridge can type the exact granted single-line text using process-targeted Unicode
events. It checks foreground app and field focus and never sends Return or uses
the clipboard. Nonempty fields, control characters, focus changes and unverified
text stop this fallback with an uncertain action rather than automatically retrying.

Run `showrun prepare-desktop` (library: `Showrun.prepare_desktop()`) to download
the version-pinned Apple Silicon companion from the repository's GitHub Releases.
The installer verifies a SHA-256 pinned in the package and the app's ad-hoc
signature before replacing the installed app. It does not require Xcode or a
compiler. Developer builds use `showrun prepare-desktop --build` (library:
`Showrun.prepare_desktop(build=True)`) with Apple Command Line Tools.
The early-access binary is ad-hoc signed, **not Developer ID signed or notarized**.
If macOS blocks the first launch, attempt `showrun desktop-status`, then use
System Settings → Privacy & Security → Open Anyway for this app if you trust it.
Do not disable Gatekeeper globally. Run `showrun desktop-status` again after
granting both permissions; it reports `screen_recording`, `accessibility` and
`ready` without inspecting a target app. A completed check with `ready: false`
means setup is incomplete even though the CLI check itself exited successfully. This is explicit setup,
not part of recording, and does not inspect applications or request permissions.
In macOS System Settings → Privacy & Security, grant Screen Recording and
Accessibility to `~/Applications/Showrun Desktop.app` (the returned `app` path).
Showrun launches the companion through macOS LaunchServices, rather than as a
child executable of the calling terminal or agent. The companion connects to a
private per-run Unix socket authenticated with a one-time nonce. Closing that
connection exits the companion; it does not close the target application.
Screen Recording permission checks are attributed to `org.showrun.desktop` on the
verified host. Grant permissions to Showrun Desktop, not to each caller. Ad-hoc
updates may require removing and re-adding the app in both permission panels.
The app has a stable bundle identifier and executable path. Preparation reuses an
unchanged build. Local builds are ad-hoc signed, not Developer ID signed or
notarized, so updates may still require a new permission grant. Missing permissions fail before UI
actions. FFmpeg/ffprobe and the configured model runtime are still required;
Chromium is not required for native capture. Select a model supporting image input.

Prepare a non-sensitive window and its contents first. The target's bundle ID and
exact window title must each identify the same unique window. Title changes,
minimization or a lost window stop the take. Match capture dimensions to the
window's actual pixel dimensions, including Retina scaling. Alternatively set
`target.resize_to_capture: true` to explicitly allow Showrun to resize the named
window before recording to match `capture.width` and `capture.height` (which also
specify the aspect ratio). It converts pixels to macOS points and verifies a real
screenshot before capture or model actions. App minimum sizes and display limits
may prevent an exact match: `capture_geometry` reports requested and measured
sizes, while `desktop_resize_unavailable` means the app refused window sizing.
No stretching or cropping is performed. The window is left at its resulting size,
including when preparation fails. Omit the option to preserve existing behavior. For example:

```json
{
  "request_id": "native-take-01",
  "target": {
    "kind": "macos",
    "bundle_id": "com.example.DemoApp",
    "window_title": "Prepared Demo"
  },
  "starting_state": "Ready",
  "capture": {"width": 1280, "height": 720},
  "steps": [
    {"id": "save", "instruction": "Press Save and show the saved result",
     "visible_text": "Saved", "hold_seconds": 3}
  ],
  "authority": {
    "navigation_only": false,
    "disclose_dom": false,
    "disclose_accessibility": true,
    "disclose_screenshots": true,
    "max_seconds": 60,
    "max_model_calls": 8,
    "max_actions": 12,
    "ui": {
      "actions": ["click", "fill"],
      "allowed_values": ["Example task"],
      "target_effects": "all_in_session"
    }
  }
}
```

The caller owns the application and session effects; Showrun closes only its
helper. This backend is not a desktop sandbox and does not promise background
focus isolation. Use an app/session prepared for the demo. App effects, dialogs
and sensitive UI may exceed what window capture can prove. Secure accessibility
fields stop capture and restrict retained footage, but this is not a universal
sensitive-content detector or automatic redaction. Avoid login and real secrets.

Capture samples the actual window at up to 5 Hz, preserving waits, then encodes
the samples into the usual silent MP4. It omits the cursor and can miss transient
states; the receipt declares these limitations and approximate timing. It is not
a claim of full-motion 25 fps capture. Outcome checks use accessibility text,
accessible control visibility, or field values, not the model's success assertion
or proof of backend persistence. `checked` field assertions are unsupported.

Timing drift is advisory: decodable footage remains available, with media timing
metadata and any `timing_warnings` in the receipt. Structurally inconsistent step
evidence, failed actions, invalid media, wrong geometry and restricted footage
still prevent complete success. Downstream editing owns retiming and polish.

## Lifecycle, budgets and retries

`record` runs synchronously in the caller process, not a background job service.
Another process can call `status(request_id)` or `cancel(request_id)` in the same
store. Cancellation is an acknowledgment; poll until terminal and inspect
`cleanup`. Caller death may leave uncertain work and incomplete resources.
The helper attempts service cleanup on pipe EOF; no crash-restart guarantee.
Inspect `owned-dashboard.json` and the isolated target store if cleanup is uncertain.
Never kill a process by its port or assume a shutdown acknowledgment proves exit.
Retained running owners with a reused PID, different boot, missing legacy identity
or unreadable process identity are reported `uncertain`, not live. Linux force-stop uses pidfd; Windows uses one verified process handle. macOS
refuses force-stop of retained PIDs rather than using a race-prone kill fallback.
Windows flushes receipt files and replaces them atomically but does not claim
POSIX directory-fsync durability.

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
Input retains the first and latest frame in each 50ms bucket (at most 40 frames
per second) to bound animated-page storage without dropping a final static update.
Encoded frames arriving within a 250ms reorder window retain their original
compositor timestamps and are ordered before encoding; larger regressions fail.
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
every capability also has focused `--help` covering only its purpose, arguments,
prerequisites, example, results and recovery. Use top-level `--help` for the full
manual; command help links to packaged resources instead of repeating that manual. For composition, use the library rather than
parsing CLI output.

### Waiting for target work

Set a step's `wait_for_result: true` to keep recording and polling its assertions
without model calls or UI actions. Use a preceding submission step to establish
that work started; an already-true completion assertion can otherwise skip the wait.
`authority.max_seconds` allows up to 1800 seconds, including all steps and holds.
Storage limits still apply. A named-window recording does not follow other apps.

## Experimental native Windows backend

Windows has an early-access UI Automation / PrintWindow backend tested on Windows 11 x64.
Install with Python 3.12+, Git/uv, and FFmpeg/ffprobe:

```sh
showrun prepare-desktop
showrun desktop-status
```

The package pins the Windows x64 `desktop-v0.2.0` ZIP and SHA-256. Installation
places the unsigned, self-contained executable in `%LOCALAPPDATA%/Showrun/Desktop`.
No .NET runtime or compiler is needed. Early-access Windows security prompts or
organization policies may block unsigned executables; this tool does not change
those policies. `--build` is the developer path and requires the .NET 8 SDK/runtime.
Mac installations continue to use the existing macOS release and permission setup. If Git
reports long dependency paths during installation, enable `core.longpaths` for
that setup process. The companion is launched as a temporary, limited-privilege
interactive Scheduled Task under the caller's Windows account. SSH can run the
caller in session 0 while the companion connects from the user's logged-in
desktop. A random token authenticates its loopback-only connection. Closing the
connection stops the companion and removes its task, never the target app.

Use `target: {"kind":"windows","pid":1234,"window_title":"Exact title",
"resize_to_capture":true}`. PID and exact title must identify one visible window
in the companion's session. The bridge then retains the window handle and process
creation time; normal title changes during editing do not switch the target.
UIA field observations normalize CR/CRLF line endings to LF, so use LF in multiline
field assertions. Text-entry style belongs to the demo step: `"text_entry":"immediate"` (the default)
or `"text_entry":"paced"`. `"text_entry":"fast_imperfect"` selects shorter delays
and two deliberate extra-letter mistakes followed by deletion and correction.
This opt-in style permits transient misspellings; the final text remains exact.
Use it only in a prepared demo field where partial input has acceptable effects.
Paced entry currently requires Windows; other targets
reject it during validation. Showrun adds a 400 ms initial hesitation, short varied
character delays, and punctuation pauses. It captures each displayed character
before requesting the next, in addition to normal background sampling.
The adapter only retains the authorized text and original control, advances one
Unicode text element per request, and checks the previous value for interference.
Paced fill remains one action; cancellation can leave partial text and never retries
it automatically. Long text takes longer and remains subject to the take deadline.
This uses progressive UIA values, not physical keystrokes. macOS is unchanged.

Use the same screenshot/accessibility grants and click/fill actions as macOS.
UIA invoke, selection, expand/collapse, toggle and writable-value controls are
supported. Tabs, menu items and expandable controls use actual mouse clicks at
UIA-provided clickable points, after foreground, focus and hit-target checks.
The model still supplies only an observed control reference, never coordinates. Observed grid
cells additionally use UIA selection/focus followed by exact Unicode input and a
fixed Enter commit, rather than ValuePattern writes. This path requires foreground
focus, no held modifiers, and immediate nonempty single-line input. It exposes no
model-selected keys or clipboard operations; paced grid input is not yet supported.
No arbitrary coordinate/keyboard fallback, elevated app access, secure desktop, minimized
windows, or separate dialog/browser-window capture is promised. Keep the desktop
unlocked and avoid disconnecting or minimizing RDP during a take.

PrintWindow asks the target to render its window; some apps return incomplete or
blank content despite API success. Always inspect actual footage. This initial
backend is not general Windows Graphics Capture support. Recorded native fixture
trials and live model/application trials are separate evidence.

Windows grid-entry evidence: Excel ValuePattern writes can echo requested text
without changing workbook cells. Grid cells therefore use focused keyboard entry;
ValuePattern is read for outcome assertions, not used for the write. Formula
assertions should check the displayed calculated value. A repeated fill of the
same observed control with unchanged or already-matching text stops with
`desktop_no_progress` instead of repeatedly spending the model/action budget.
Native accessibility assertions still do not independently prove workbook
persistence; inspect footage and independently verify important demo results.

Excel menu trial: mouse clicks successfully switched ribbon tabs, opened Freeze
Panes and selected Freeze Top Row; independent Excel state confirmed a one-row
freeze. PrintWindow omitted the open drop-down from the recording despite the
menu being available for interaction. Cursor capture is also absent. Do not claim
that native menu interaction success proves a complete or readable menu recording.
