# Showrun contracts (DRAFT)

The [vision](../docs/VISION.md) describes the intended experience. These contracts
state the behavioral promises against which implementation can be checked.
All are owner-reviewed drafts that remain unlocked; none is evidence of working software
until the corresponding checks are actually run.

| Contract | Boundary |
|---|---|
| [Portable invocation](invocation.v1.md) | Installation, library/CLI parity, discovery, configuration and failure transport |
| [Calling-agent interaction](caller-interaction.v1.md) | Demo brief, authority, take identity, clarification and retry behavior |
| [Demo performance and capture](demo-capture.v1.md) | Faithful UI performance, viewer legibility, recording and media handoff |
| [Capture review workspace](capture-review.v1.md) | Identical dashboard/MCP App, retained-clip playback and selection, rename/delete, notes and MP4/ZIP downloads |
| [Internal execution](internal-execution.v1.md) | Enforcement, observation/action loop, budgets, validation and cleanup |
