# Portable invocation contract — v1 (DRAFT)

**Who builds against this:** Calling agents, Python applications and people invoking
the Showrun command line.

The external baseline is [Smart Tools at 70432044](https://github.com/microsoft/amplifier-smart-tools/tree/70432044f26e2094b5894516adab68aa14f88592/spec).
This draft describes intended behavior, not installed capabilities.

## What it looks like

A caller installs Showrun and requests a demonstration through its library or CLI,
without loading a bundle or adopting the tool's internal runtime.

```text
Right: request validation and retained-result inspection work without a provider.
Right: a recording requiring intelligence fails with setup guidance if none is configured.
Wrong: importing the library launches a browser or asks the caller to sign in.
```

## Purpose

Make Showrun independently usable and predictable to compose. The
[calling-agent contract](caller-interaction.v1.md) defines request and result
semantics; [demo capture](demo-capture.v1.md) defines the delivered performance.

## Core (the teeth)

1. **The library owns every capability.** The intended distribution is
   `amplifier-smart-tool-showrun`, Python import `amplifier_smart_tool_showrun`,
   and command `showrun`. The package installs from Git and runs outside its source
   checkout. The CLI adapts arguments and I/O without exclusive domain behavior.
2. **The installed tool describes its real surface.** A packaged `smart-tool.json`,
   `SMART_TOOL.md` and library-accessible manifest agree about capabilities,
   prerequisites and supported targets. Tool-level and capability-level `--help`
   render library-owned operating skills; `-h` remains terse. Help identifies
   arguments, results, recovery, AI use and a provider-free smoke invocation.
3. **Deterministic paths stay model-free.** Import, help, manifest access, structural
   request validation and inspection of retained results do not initialize an
   agent, launch a target, call a provider or perform a new demonstration.
4. **Intelligence is embedded, not borrowed from the caller.** Model-backed work uses
   Amplifier Agent behind the library boundary, following the sibling Smart Tool
   pattern. The caller needs no Amplifier CLI, bundle, host session or specialist
   catalog. Provider/model configuration and permitted disclosure are explicit.
   Missing configuration produces a remedy, not a silent downgrade.
5. **Context enters as data.** Required demo inputs and optional context are
   documented separately. The library accepts content without requiring access to
   its original file. CLI file conveniences load that content mechanically rather
   than silently summarizing it. Additional repository, filesystem or network
   inspection requires separately scoped access; it is not implied by context.
6. **Results and failures are machine-usable.** CLI machine results use documented
   JSON on stdout, separate from stderr diagnostics and progress. Failures exit nonzero with a cause, affected
   operation or step when available, and a remedy. An accepted asynchronous request
   is not completed work; any asynchronous surface documents terminal outcome
   retrieval. Noninteractive invocation never waits on an inaccessible prompt.
7. **Prerequisites and side effects are explicit.** Missing browser, capture or media
   dependencies fail with setup instructions; ordinary requests do not auto-install
   dependencies. Target launch and capture happen only for an authorized operation.
   State uses documented per-user or caller-selected locations, temporary data uses
   temporary storage, and artifacts use selected output destinations. Existing files
   are not overwritten without permission. No automatic publishing, external sharing,
   unrelated service startup, commit or push accompanies capture.
8. **Adapters preserve the same contract.** Library and CLI expose the same success,
   failure, authority and retry semantics. Optional future adapters add no hidden
   capability or dependency required for headless operation.

## What v1 deliberately does NOT freeze

- Function signatures and command verbs beyond the intended package/import/command
  identity; concrete caller examples and implementation will establish them.
- Exact JSON serialization, storage layout or synchronous versus asynchronous API.
- Provider/model defaults, browser engine versions, installation extras and platform
  support; shipped help must describe only verified combinations.
- MCP, a dashboard, remote hosting or a universal backend plugin interface.

## Showrun acceptance checks

- Clauses 1–2: install outside the checkout; compare library and CLI capabilities,
  manifest and descriptor; inspect both help levels on every capability.
- Clauses 3–4: remove provider credentials and deny network; deterministic paths
  complete without engine/browser startup. Smart execution fails with a remedy.
  With bounded authorization, a live smart call works without a caller host session.
- Clause 5: pass content after making its original file unavailable; confirm no
  undeclared source access or model-based context summarization by the adapter.
- Clause 6: exercise malformed inputs, missing dependencies and a failed demo with
  stdin closed; verify documented JSON, nonzero terminal failure and diagnostics.
- Clauses 7–8: compare filesystem/process snapshots for unintended changes and
  exercise the same meaningful failure through library and CLI.

Run upstream conformance separately. Its packaging checks do not certify demo
correctness or watchability. Missing evidence and skipped checks are not passes.

## Changelog

- **2026-09-18** — Initial draft; no lock or implementation claim.