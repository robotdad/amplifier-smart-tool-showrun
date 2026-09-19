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
- Support existing dashboard URLs and managed startup of supported installed smart
  tools through their public libraries. Keep lifecycle integration reusable, background
  capture isolated, and target-tool authority separate. Do not generate replacement UIs
  or execute fresh unrestricted launch glue for each take.
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

## MVP build and checks

- `uv sync --extra dev`; install Chromium explicitly with
  `.venv/bin/python -m playwright install chromium`. FFmpeg/ffprobe must be on PATH.
- `.venv/bin/python -m pytest` runs deterministic lifecycle, packaging and local
  headless-browser tests. These do not call a live provider or certify watchability.
- `.venv/bin/ruff check src tests` and `uv build` check code/package construction.
- `showrun manifest` is the provider-free installed smoke. `showrun --help` and
  each capability's `--help` are library-owned packaged operating skills.
- `showrun prepare-runtime` is explicit module setup, not part of routine record,
  status, validation or help. Live provider trials need separate authorization.
- **2026-09-19:** The owner reviewed the actual successful `happy01` take
  (28.6 seconds, 1080p navigation) and said the video was good. Its MP4 SHA-256 is
  `038f5f730e5de7fc1d4d65cc214baeb98266b20851a5dff75417100cffd2084b`.
  This is human acceptance of the limited first MVP slice, not full contract
  conformance or a claim about other takes.
