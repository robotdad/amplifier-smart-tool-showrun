# Showrun contracts (DRAFT)

The [vision](../docs/VISION.md) describes the intended experience. These contracts
state the behavioral promises against which implementation can be checked.
All are owner-reviewed drafts that remain unlocked; none is evidence of working software
until the corresponding checks are actually run.

Product promises apply across supported web and desktop targets. Playwright web
interaction and video recording were the first implementation. Computer use shares
the same intent, authority, capture and evidence boundary.
The first macOS implementation is a bounded native window slice; the experimental Windows backend uses UI Automation and PrintWindow in an interactive login session. Web-specific clauses apply only to web targets. Backend support and limitations
must be declared before execution, without treating a narrow slice as general desktop support.
Application-specific launch, fixture and permission details belong in integration
documentation, not these general contracts. See the historical
[initial integration scope](../docs/integrations/initial-scope.md).

| Contract | Boundary |
|---|---|
| [Portable invocation](invocation.v1.md) | Installation, library/CLI parity, discovery, configuration and failure transport |
| [Calling-agent interaction](caller-interaction.v1.md) | Demo brief, authority, take identity, clarification and retry behavior |
| [Demo performance and capture](demo-capture.v1.md) | Faithful UI performance, viewer legibility, recording and media handoff |
| [Capture review workspace](capture-review.v1.md) | Identical dashboard/MCP App, retained-clip playback and selection, rename/delete, notes and MP4/ZIP downloads |
| [Internal execution](internal-execution.v1.md) | Enforcement, observation/action loop, budgets, validation and cleanup |
