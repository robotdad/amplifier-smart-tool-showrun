---
smart_tool_format: 1
name: showrun
version: 0.1.0
description: >-
  Performs bounded, navigation-only web demonstrations and returns continuous
  application footage with step evidence. Use for prepared demo dashboards,
  not target generation, editing, comments, login or video post-production.
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
target content, click arbitrary buttons, reset data, edit, comment or publish.
No native desktop, audio, login, multiple pages, uploads, downloads or clipboard.

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

The caller supplies semantic instructions and the literal visible text required
at each result, not selectors. The model chooses among observed navigation
controls or bounded navigation keys. Required result text must remain visible for
at least three seconds. `starting_state` is also required visible text.
`context` is optional caller content (not a file reference). Page text never
changes permissions. Only one exact HTTP(S) origin is allowed for resource traffic
and interaction. The MVP denies non-read requests except fixed Stories read APIs.
Arbitrary applications requiring other resources or mutations are unsupported.
Labels are conservatively limited to navigation; no free-form click or script tool.
Sandboxed opaque `srcdoc` previews are supported; they grant no extra origins.

`capture` optionally supplies even `width` and `height`, each 320–3840.
Defaults are 1920×1080, square pixels. The isolated viewport uses those exact
dimensions; no resizing, stretching, padding or cropping to fit source content.
Applications must be prepared to render at the chosen viewport dimensions.
Viewport capture excludes browser chrome and all other human windows/tabs.

## Managed Stories

Replace `target` with:

```json
{
  "kind": "stories",
  "python": "/absolute/isolated-stories/bin/python",
  "storage": "/absolute/isolated-fixture-store",
  "story_id": "story_ID",
  "revision_id": "rev_ID"
}
```

Supported library: `amplifier-smart-tool-stories==0.1.0`, public API shape at
commit `25d6bb06d2f41336593edc5df9ddb7d3889a894e`. The fixed packaged helper
runs in the selected installed interpreter with `-I`, an isolated HOME and only
PATH/LANG/PYTHONUNBUFFERED forwarded. No Showrun credentials go to Stories.
It calls `Stories(storage, model_env=False, execution="queued")`,
`get_revision`, `start_dashboard`, and `stop_dashboard`. No arbitrary target
imports, fresh launch code, live-store discovery or target installation.
The returned endpoint must be HTTP on numeric loopback. Readiness checks the
exact selected revision and actual rendered starting-state text, not just a socket.
Shutdown acknowledgment is followed by Linux process-exit verification.
`owned-dashboard.json` retains only the acquired service identity for recovery.
Managed service startup can take up to 25 seconds within the elapsed grant.
For an already-running Stories URL, also set `stories_revision` to the expected
revision ID. Its auth fragment is used only by the isolated browser, never sent
to the model or included in a receipt. That dashboard remains caller-owned.

## Lifecycle, budgets and retries

`record` runs synchronously in the caller process, not a background job service.
Another process can call `status(request_id)` or `cancel(request_id)` in the same
store. Cancellation is an acknowledgment; poll until terminal and inspect
`cleanup`. Caller death may leave uncertain work and incomplete resources.
The helper attempts service cleanup on pipe EOF; no crash-restart guarantee.
Inspect `owned-dashboard.json` and the isolated target store if cleanup is uncertain.
Never kill a process by its port or assume a shutdown acknowledgment proves exit.

A SQLite transaction durably reserves the caller's request ID before model or
target effects. Scope: that store, retained indefinitely until the caller explicitly
deletes it. Effective defaults, ordered steps, target/configuration and model
selection are compared using canonical sorted JSON and SHA-256; credential
values are never persisted. Exact retry returns the same retained take, including
failed or uncertain outcomes, with **no new execution**. Different inputs under
that ID fail with `request_conflict`. New intent/retake requires a new ID.
Never delete the store and assume an old key still prevents replay.

Authority caps: 180 elapsed seconds, 12 provider calls and 30 navigation actions;
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