# Internal execution contract — v1 (DRAFT)

**Who builds against this:** Maintainers implementing Showrun's library, embedded
intelligence, target control, capture and result validation.

## What it looks like

This describes obligations, not an internal tool catalog or a mandatory agent graph:

```text
Validate request, authority and non-launch prerequisites → establish capture support
Managed target: start supported dashboard → verify endpoint and readiness
Caller-provided target: verify endpoint and readiness
Ready target → observe current target → resolve next permitted action → act → check outcome
Finalize capture → validate media and receipt → clean up owned resources → report
```

Right: a proposed action is checked against scope before it reaches the target.
Wrong: a page instructs the model to upload a recording and gains authority to do so.

## Purpose

Make [caller interaction](caller-interaction.v1.md), [capture](demo-capture.v1.md)
and [invocation](invocation.v1.md) promises enforceable. Playwright is the first
web implementation to explore, not a public dependency callers must orchestrate.

## Core (the teeth)

1. **Executable boundaries enforce authority.** Library-controlled capabilities
   restrict target access, action classes, output destinations, disclosure and work
   limits. Prompts alone are not enforcement. General shell, unrestricted browser
   code execution, filesystem access and arbitrary network access are not implicit
   privileges of the embedded agent. Nested work shares the same restrictions.
   The web boundary enforces the caller contract's navigation/interaction origins
   across redirects, new pages and frames. Unapproved surfaces are not passed to
   the model or handed off as footage; an unexpected transition stops the flow.
   The target's own resource/API network traffic is distinct from agent navigation:
   required destinations and the enforcement limits are documented and selected
   during setup. An interaction-origin list is not a claim of full network isolation.
2. **Observed content is data, not permission.** Page text, screenshots, source context,
   optional hints and model output cannot modify grants. The implementation validates
   proposed targets and actions against both the authorized flow and enforceable
   scope. When it cannot determine that a consequential action is permitted, it
   stops with a need or failure instead of inferring authority from the UI.
3. **Intelligence can inspect and perform the permitted task.** It receives appropriate
   current observations and bounded interaction capabilities, with capture and outcome
   inspection available to the workflow. It does not depend on the caller supplying
   every click. Stale element references, changed pages and ambiguous targets prompt
   re-observation or an explicit failure, not arbitrary coordinate fallback.
4. **Readiness precedes mutation.** Validate request structure, effective permissions,
   required provider/dependencies, target readiness and supported capture requirements
   before performing the demo. Runtime changes such as expired access remain possible
   and are reported when observed. Preflight is not proof that the whole run will work.
5. **External effects are accounted for.** The execution record distinguishes an
   intended action, a dispatched action and an observed outcome. Uncertain completion
   remains uncertain. Recovery re-observes state; it does not blindly replay
   non-idempotent actions. Reset, preparation and new takes require the caller's
   applicable authority and cannot be smuggled into an internal retry.
6. **Work is bounded and stoppable.** Enforce finite, documented limits for model use,
   actions, elapsed time and recording/storage. Repairs and internal delegation share
   the allowance. Exhaustion and cancellation stop new actions and attempt bounded
   capture finalization. Report delayed or failed cleanup and late in-flight effects;
   do not convert cancellation into completed demo success or promise rollback.
7. **Completion is checked outside model prose.** Library validation binds the effective
   request, action/outcome evidence, exact media and step timeline to the submitted
   result. Validate required fields, references, output scope, artifact identity and
   media integrity. Missing steps, evidence or media prevent complete success.
   Structural validation cannot certify semantic correctness; model judgments
   remain labeled and unsupported claims remain unresolved.
8. **Sensitive access and evidence are controlled.** Credentials and authentication
   state stay out of prompts, logs, receipts and exports. Target text/screenshots
   are disclosed only to the configured authorized provider under the selected
   policy. Capture scope and foreseeable sensitive UI must be considered before
   recording; use prepared demo data and avoid recording login. If a sensitive
   surface appears unexpectedly, stop and restrict the affected output, report the
   exposure and require caller direction rather than claiming automatic sanitization.
   Withhold affected footage, derivatives and observations from ordinary artifact
   inspection and downstream handoff. Return a non-sensitive notice instead.
   Sensitive partial files and temporary material use access-restricted storage
   under the declared retention policy; release or deletion requires an explicit
   caller decision. Restrictions apply to Showrun's interfaces and storage controls,
   not a promise of isolation from the machine owner.
   Restricting output does not undo any already authorized provider disclosure.
9. **Owned resources are cleaned up without harming the caller.** Finalize or release
   owned capture sessions, pages, managed dashboard services and temporary resources
   on success, failure, blocked outcomes and cancellation.
   Do not close caller-owned applications or destroy caller data, valid earlier
   takes or retained results as cleanup. Document retention for recordings and
   evidence, including restricted partial material. Explicit deletion is separate
   from stopping execution; unpublished footage is still sensitive stored data.
   Record resource ownership as resources are acquired, including partial startup;
   use verified resource handles rather than killing whichever process occupies a
   port. Capture finalization precedes teardown of resources it depends on. Failed
   cleanup preserves a non-sensitive diagnostic and an actionable remedy.
   On Linux, process ownership includes boot identity and process start ticks, not
   just a PID. Reused PIDs, another boot, missing legacy identity or inability to
   verify ownership produce uncertainty, never a live claim, blind signal or replay.
   macOS uses native process start seconds/microseconds and declines retained-PID
   force-stop. Windows uses native creation FILETIME and verifies identity on the
   same handle used for termination. Platform receipt formats remain distinct;
   historical Linux identities are not rewritten.
10. **Managed dashboard launch is reusable library behavior.** Supported integrations
   identify the installed tool/version, accepted configuration, public-library launch
   and shutdown operations, readiness signal and endpoint policy. Intelligence may
   interpret permitted documentation, but execution uses validated integration code,
   not fresh unrestricted model-authored shell or Python glue on every take.
   Arbitrary module imports are not a safe discovery mechanism: running third-party
   library code requires explicit tool authorization and a documented execution
   boundary. An integration that cannot meet that boundary is unsupported.
   Validate non-launch prerequisites before startup, then check readiness with a
   bounded observable condition tied to that dashboard; guessed ports, fixed sleeps
   and a listening socket alone are not proof. Starting the service is authorized
   preparation, not evidence that any demo step has completed.
11. **Background performance preserves real UI and downstream authority.** Interaction
   and recording mechanisms are internal: Playwright is the initial web path, while
   later computer-use support must declare its authorized surfaces, observation and
   action capabilities, capture coverage and isolation limits before execution.
   Neither DOM access nor a browser viewport is a universal requirement. Outcome
   evidence states its method and limits, whether structured, visual or independently
   read from the application. Unsupported isolation or capture guarantees fail explicitly.
   Use an owned isolated headless browser for unattended web capture, without attaching to
   the person's ordinary profile, tabs or foreground window. Library calls manage
   dashboard lifecycle and readiness; required demonstrated operations still happen
   through the real dashboard UI, not hidden library shortcuts. Startup and capture
   budgets include their lifecycle work. Target-tool operations use separately
   configured credentials, disclosure and spending limits; unsupported enforcement
   fails before the affected operation. Do not pass Showrun's ambient credentials
   wholesale to the target. Cancelling Showrun or closing a dashboard does not prove
   a target job was cancelled: use supported public job cancellation within authority,
   and report any continuing or uncertain target work without automatically replaying it.

## What v1 deliberately does NOT freeze

- Agent topology, internal prompts/tools, browser observation format or storage engine.
- A backend plugin abstraction, deterministic replay engine, browser attachment
  mechanism or native desktop implementation.

## Current native slice

The first macOS implementation uses one caller-prepared named window. Showrun's
intelligence selects actions; a packaged OS bridge observes and performs accessible
click/fill operations. It is not a general desktop agent, a separate demo planner,
or a desktop isolation boundary. Screenshot sampling and accessibility evidence
limits are declared in the packaged operating guide. Windows support currently
targets portable process tracking and web capture, not native interaction.
- Universal semantic safety, automated privacy redaction or guaranteed recovery from
  arbitrary external effects. Unsupported guarantees fail explicitly.
- A generic app-hosting platform, generated dashboard, automatic installation,
  runtime adapter generation or universal smart-tool library signature.

## Showrun acceptance checks

- Clauses 1–2: inject page instructions and model proposals that attempt to widen
  origins, actions, file access, disclosure or budgets; verify the execution boundary
  rejects them independently of prompt compliance.
- Clauses 3–4: invalidate an element reference, change the target unexpectedly,
  remove access and fail capture setup; verify re-observation or an honest stop,
  and no demo mutation when preflight prerequisites fail.
- Clause 5: crash after a non-idempotent action and before its result is stored;
  retry/recovery reports uncertainty without performing the action twice.
- Clause 6: exhaust each limit and cancel during action/capture; inspect target
  effects, finalization, cleanup and terminal outcome, including late results.
- Clause 7: submit model claims with missing steps, nonexistent files, mismatched
  hashes or invalid media intervals; library validation rejects complete success.
- Clauses 8–9: use synthetic secrets and a controlled sensitive-surface fixture;
  verify protected auth handling, disclosure boundaries, restricted-output reporting,
  documented retention and cleanup ownership without exposing real credentials.
  Verify ordinary take inspection and handoff cannot retrieve the restricted media
  or derivatives, and the exposure notice itself contains no fixture secret.
- Clauses 9–11: exercise the same validated integration across multiple fresh takes
  without caller-written launch glue. Check delayed readiness, port collision,
  partial startup failure, cancellation and shutdown failure. No unrelated process
  is stopped; each acquired resource has an ownership record and cleanup outcome.
- Clauses 10–11: attempt an unsupported version, an unapproved import, malicious
  launch documentation and an out-of-policy endpoint. Verify rejection before the
  corresponding unsafe action. Inspect that target UI steps were not replaced by
  direct library mutations.
- Clause 11: run with a foreground browser containing sentinel tabs and no visible
  capture window. Verify unchanged tabs/focus and decodable footage. With downstream
  authorization absent, no target model call occurs. Cancel a long-running target
  job and verify its actual disposition rather than inferring it from browser closure.

Scripted-provider tests establish mechanics, not live model competence. Real demo
quality requires separately authorized runs and inspection of actual recorded output.
Unrun scenarios and missing evidence are not passes.

## Changelog

- **2026-09-18** — Initial draft; no lock or implementation claim.
- **2026-09-18** — Define reusable public-library dashboard integrations, readiness,
  background browser isolation, owned cleanup and downstream job/spending boundaries.
- **2026-09-19** — Approved hardening makes crash and cleanup process identity
  explicit; inability to establish identity retains uncertainty.

- **2026-09-19** — Clarify backend-independent observation, evidence and capture obligations, with Playwright first and computer use later. Still DRAFT; no new implementation claim.

## Terminal mode (DRAFT)

macOS terminal mode is explicit target authority, separate from native form fill.
Exact allowed text is appended as Unicode input; a caller-selected finite key
list governs submission/navigation/interrupts. No model-generated arbitrary key
sequence or implicit shell privilege is introduced. The caller prepares and owns
the terminal and target agent; target-side effects and spending are not bounded by
Showrun's provider budget. Same-window identity survives title changes. Focus and
modifiers are checked at dispatch; output echo is not command-completion evidence.
A repeat of the same input without an intervening different input is rejected.
The macOS Terminal.app/Copilot path was exercised with live input and decoded
recordings for desktop-v0.4.0. This contract remains DRAFT; that bounded result
does not establish compatibility with other terminal emulators or Windows.
