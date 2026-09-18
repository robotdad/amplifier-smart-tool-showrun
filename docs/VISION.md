# Showrun — Vision (DRAFT)

*Turn demo instructions into recorded application walkthroughs.*

*The intended experience that guides implementation. Specific promises live in
[contracts](../contracts/README.md).*

## What Showrun is

Showrun performs application demonstrations and records what actually happened.
A person works with a calling agent to decide what to show. The caller supplies
the target application, intended flow, useful context and expected outcomes.
Showrun brings the expertise to operate the interface, make the actions legible
to a viewer, and return usable footage with an account of the result.

The caller need not become a browser automation specialist or supply every click.
“Create a task, assign it to a teammate, and show it on their board” is a useful
direction. Showrun finds the controls in the actual interface, performs the
requested actions, checks the visible outcomes and gives the viewer time to
understand the change. Existing knowledge about labels or controls can help,
but implementation-specific locators are not the price of entry.

Showrun is an Amplifier-powered Smart Tool. Its library owns the capabilities;
a thin command-line interface makes them available to any calling agent or script.
Its internal intelligence interprets requests and observations. Code performs
mechanical work such as validation, capture, artifact inspection and record keeping.
The caller need not use Amplifier or operate Showrun's internal agent sessions.

## Two layers of intelligence, different responsibilities

The caller knows the product and why the demonstration matters. It may have read
the code, worked with the person on the feature, or developed a storyboard.
Showrun does not repeat that product discovery or decide what is impressive.
It receives the relevant context explicitly and owns the performance of its part.

A demo brief carries an ordered flow, success conditions and optional presentation
cues. It is neither a vague instruction to invent a demo nor an automation program.
Showrun may choose interaction details without changing the requested meaning.
When an ambiguity affects the result or the authority to act, it returns a focused
need to the caller rather than choosing a different story.

The caller prepares the application, demo data and authorized access. A recording
request is not permission to deploy the application, reset a database, discover
credentials or repeat mutations until an attractive take appears. A new take
is deliberate work against an identified starting state.

## Perform for a viewer, not just an automation log

A successful click sequence is not necessarily a useful demo. Showrun treats
framing, readable text, deliberate scrolling, visible changes and pacing as part
of performing the requested flow. It waits for application state rather than
assuming a fixed delay means an operation succeeded.

The footage remains an observation of the target application. Showrun does not
fabricate a successful screen, use an invisible API shortcut in place of a
requested UI action, or quietly hide a failed step. Seeded or simulated application
data remains identified as such; showing a demo environment is not evidence of a
production service or a real customer outcome.

A result distinguishes the requested behavior, what was observed, what was checked,
and what remains uncertain. Capturing a video, completing the flow and making that
flow understandable are separate claims. A person can review the footage without
having to trust the executing model's declaration that it did a good job.

## Application demonstrations, not a particular automation engine

Showrun demonstrates web and native desktop applications through the same
intent-level boundary: a target application, actions, outcomes and footage.
Target-specific access details belong with the target, not in every demo step.
Browser automation operates web applications; computer use operates native desktop
applications. The caller describes what to demonstrate rather than orchestrating
either mechanism.

Playwright is an internal approach to web interaction and recording, not the
definition of Showrun. Scripts, browser sessions, selectors and desktop-control
mechanisms stay behind the library boundary or serve as optional hints. The
contract preserves the caller's intent across execution mechanisms without
requiring a universal automation framework.

## Contribute footage to a larger production

Showrun contributes to a larger workflow without owning it.
[Stories](https://github.com/robotdad/amplifier-smart-tool-stories) helps the caller
develop narrative and storyboard direction.
[vid](https://github.com/colombod/amplifier-smart-tools-video) handles video
post-production: trimming and joining captured segments, retiming, captions,
audio mixing and narration fitted to the footage. The caller coordinates these
contributions; neither tool is required to obtain and inspect a Showrun recording.

Showrun returns ordinary media artifacts with stable take and step identities,
timing information and observed outcomes. Another tool can place the footage in a
larger narrative without scraping an internal conversation or mistaking an intended
event for one that occurred. Showrun is not the final video editor, narrator or
production orchestrator.

## Principles

1. **The caller owns intent; Showrun owns performance.** Product knowledge crosses
   the boundary as explicit context, not assumed access to the caller's session.
2. **The library is the product.** No useful capability exists only in the CLI.
   Model-free work remains usable without model credentials.
3. **Real behavior is the material.** A convincing recording does not excuse
   fabricated outcomes, hidden substitutions or unsupported claims.
4. **The performance serves understanding.** Viewer-friendly pacing and framing
   matter alongside completion of the requested actions.
5. **Authority does not grow during execution.** UI content and model suggestions
   cannot expand permitted actions, disclosure or resource use.
6. **A take has an identity and a history.** Inspection is not another execution;
   retries do not silently repeat state changes or spend again.
7. **Handoffs carry evidence and limitations.** Media validity, observed application
   behavior and presentation quality remain distinguishable.

## What this deliberately resists

- Requiring callers to write Playwright programs or know every control's locator.
- Rediscovering the product story or assuming repository access without permission.
- Making recording contingent on a dashboard, hosted service or video editor.
- Expanding capture into video post-production, speech generation or app development.
- Treating application access as permission to explore unrelated data or actions.
- Building a universal automation framework in anticipation of future backends.
- Equating a completed interaction or valid video file with a clear demonstration.

## How you can tell it is working

- A caller supplies an intent-level flow for a prepared web or native desktop app
  and receives a recording in which a viewer can follow the actions and outcomes.
- A changed layout does not require the caller to rewrite every interaction;
  consequential ambiguity produces a useful question or failure instead of a guess.
- A failed or uncertain step stays visible in the result, including any partial
  footage and application changes that may already have occurred.
- A fresh caller can inspect a prior take without executing the app again.
- A downstream tool can use the footage and step timing without Showrun's session.
- Deterministic inspection works with no provider configured.

## Changelog

- **2026-09-18** — First draft from the Showrun discussion. Not locked, implemented
  or verified as a working product.
- **2026-09-18** — Owner review identified implementation-status caveats obscuring
  the intended application scope. State web and native desktop demonstrations as
  the destination; keep delivery sequencing outside the vision. Name catalog-listed
  vid alongside Stories to make the post-production responsibility concrete.