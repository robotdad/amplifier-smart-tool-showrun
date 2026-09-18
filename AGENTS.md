# Working on Showrun

Read [the vision](docs/VISION.md) and [the contracts](contracts/README.md) before
changing product behavior. They describe intended behavior, not implementation status.
Keep the vision about the destination; delivery sequencing and current support
belong in implementation scope and status, not caveats in the intended experience.

- Keep vision and contracts DRAFT until the owner explicitly locks them. Review is
  not locking. Propose changes to locked promises separately rather than rewriting them.
- The library owns domain capabilities; the CLI adapts arguments and I/O. Follow the
  Smart Tools baseline pinned in the invocation contract, including capability help.
- The caller owns product intent. Showrun owns application interaction and recording,
  not narrative discovery or video post-production.
- Explore Playwright for the first web implementation without making its objects or
  scripts mandatory public inputs. Desktop computer use is a later direction, not
  a reason to build a generic backend framework now.
- Keep mechanical work model-free and internal intelligence behind the library.
  Do not expose unrestricted execution merely because an agent needs to operate a UI.
- Preserve caller authority, uncertain external effects, take identity and evidence.
  Never treat a video file, model report or packaging check as proof of a successful demo.
- For document changes, check relative links, cross-contract consistency and coverage
  of user intent. Acceptance scenarios are proposed checks until actually executed.
- When implementation exists, document real test commands here. Verify library/CLI
  parity, failure paths, installed use and actual footage; report skipped checks.
- Keep generated footage, authentication state, credentials and private trial data out
  of Git. Use temporary or caller-selected storage; no private absolute paths in docs.
- Preserve unrelated work. Commit and publish only when requested; show substantial
  generated drafts before publishing them.