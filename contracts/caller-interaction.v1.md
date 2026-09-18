# Calling-agent interaction contract — v1 (DRAFT)

**Who builds against this:** Agents and applications requesting demonstrations,
people steering them, and consumers inspecting prior takes.

## What it looks like

The caller knows the product and chooses the demonstration. Showrun owns operating
the target and recording the requested flow. This is illustrative, not API syntax:

```text
Caller → target + starting state + ordered steps + expected outcomes + authority
Showrun → identified take + footage + step timeline + observed outcomes + limitations
Caller → inspect that take, clarify a blocked step, or request a deliberate new take
```

A step may say “Assign Prepare launch to Alex and open Alex's board,” expect that
the task appears with Alex as owner, and ask for a pause on the result. A label or
locator can help; the caller need not enumerate the underlying clicks.

## Purpose

Define what crosses the boundary without exposing an automation engine or requiring
the caller to run Showrun's internal workflow. The [capture contract](demo-capture.v1.md)
governs performance and footage; [invocation](invocation.v1.md) governs transport.

## Core (the teeth)

1. **A request states a bounded demonstration.** It identifies the target and expected
   starting state, an ordered set of uniquely identified steps, desired observable
   outcomes and output preferences. Optional context supplies product meaning,
   relevant facts and presentation cues. Missing consequential details produce a
   focused need or validation failure, not invented product intent.
2. **The target is not the automation engine.** Web targets carry web-specific access
   information such as a starting URL. Step meaning does not require Playwright
   objects, DOM selectors, browser session handles or code. Optional target-specific
   hints are validated against current observations. Unsupported targets fail
   explicitly; a desktop request is not silently approximated in a web mockup.
3. **The caller supplies readiness and access.** For the initial web scope, the caller
   prepares a running app, suitable demo data and authorized authenticated access.
   Showrun checks relevant preconditions before mutation and reports what it could
   and could not verify. Authentication material uses protected configuration,
   separate from narrative context and retained result records. Missing access does
   not authorize credential discovery, interactive login or account creation.
4. **Authority is distinct from intent.** Selected target scope, permitted mutations,
   disclosure destinations, output/storage locations and finite work limits are
   explicit request or configuration choices. Existing valid authority can cover the
   flow without per-click human approval. A broad goal, page content or UI hint
   cannot expand authority. Unsupported scope constraints are reported before acting.
   For web targets, allowed interaction origins are declared explicitly; the starting
   URL is an entry point, not permission for other origins. Redirects, new pages and
   frames do not expand that set. Downloads, uploads, file chooser and clipboard
   access are denied unless separately selected and supported. Declared scope is
   a maximum boundary, not permission to perform unrelated actions within an origin.
5. **Showrun chooses interaction details, not a different demonstration.** It may
   discover controls and intermediate navigation within scope. It preserves required
   order, values, outcomes and constraints. If two interpretations would materially
   change the demo, it returns the affected step, ambiguity and useful next action.
   A question is data for the caller, not a promise of a conversational service.
6. **Every take is identifiable.** Public results associate request identity, effective
   brief, take identity and step identities with their artifacts and observations.
   New intent or another performance produces a new take and preserves the earlier
   record. Exact retries return the existing operation or result, including uncertain
   or failed outcomes, rather than starting again; conflicting identity reuse fails.
   The caller supplies a request key before execution. The library durably reserves
   it before dispatch and documents its scope, retention window and canonical
   comparison of effective inputs, including selected configuration and authority.
   The guarantee applies within that documented window and store; outside it,
   callers must inspect state and deliberately request a new take, not assume
   that an old key still protects against replay. Credentials are not stored in keys.
7. **Observation does not repeat execution.** A fresh caller can inspect status,
   outcomes and retained artifacts through the public interface without model use,
   app access or the original conversation. Retention limits and missing artifacts
   are reported. Long-running execution is observable and cancellable; reading
   status does not restart it, and cancellation acknowledgment is not cleanup proof.
8. **Incomplete work remains incomplete.** Results distinguish completed, failed,
   unattempted and uncertain steps, including any mutations already made or possibly
   made. They name the failed requirement, evidence and remedy. Partial footage is
   useful material, not a successful complete demo. Success requires the full requested
   flow and capture obligations; omitted optional checks stay disclosed.
   In the initial ordered flow, a failed, blocked or uncertain required step stops
   subsequent demo actions; later steps remain unattempted, not silently skipped.
9. **Retakes, reset and continuation are deliberate.** A new recording requires valid
   authority and a checked starting state. Showrun does not reset data, deploy code
   or repeat uncertain mutations to repair a take. If continuation is supported,
   an answer targets the identified need and execution re-observes current state.
   Otherwise it returns a clear restart requirement; stored context is not proof
   that the app is still in the state it left.

## What v1 deliberately does NOT freeze

- A required schema for locators, freeform cues, structured assertions or target
  extensions. Both semantic instructions and useful optional hints must remain possible.
- Interactive continuation, automated rehearsal, reset hooks or a take-management UI.
- Native desktop execution; its access and capture semantics need actual examples.
- Autonomous discovery of compelling demos, repository analysis or larger story planning.

## Showrun acceptance checks

- Clauses 1–2, 5: demonstrate a prepared app from semantic steps without selectors;
  change its layout while retaining usable labels and repeat with a new take.
  Duplicate labels that cannot be resolved safely produce an actionable ambiguity.
- Clauses 3–4: wrong user, missing demo data, expired access, unsupported targets
  and insufficient permissions do not trigger unauthorized corrective mutations.
  Out-of-scope redirects, popups, frames and login surfaces do not acquire interaction
  or provider-disclosure authority. File-transfer and clipboard fixtures exercise
  default denial and explicitly authorized behavior separately.
- Clauses 6–7: duplicate a request during and after execution, then reuse its ID
  with changed intent. Observe no second execution and a conflict for changed input.
  Drop responses after dispatch and after durable completion, then retry the same
  caller-supplied key within its retention window; neither starts a second take.
  A fresh process can inspect an existing result without a provider or target.
- Clauses 8–9: interrupt after a mutation but before acknowledgment; the result
  preserves uncertainty, and retry does not duplicate the mutation. A blocked
  middle step leaves later steps unattempted rather than falsely complete.
- Clauses 7–9: cancel during work and verify the reported cleanup state; a new take
  preserves the previous artifact and checks readiness instead of resetting silently.

These are proposed checks, not executed results or a promise that every UI is supported.

## Changelog

- **2026-09-18** — Initial draft; no lock or implementation claim.