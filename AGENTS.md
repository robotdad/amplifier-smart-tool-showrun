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
- `showrun prepare-fixture` imports supplied presentation content through installed
  Stories into an empty caller-owned store. New managed trials must use its returned
  target; do not replace this with caller-generated launch/import glue or live stores.
- For complete offline verification set `SHOWRUN_TEST_STORIES_PYTHON` to installed
  Stories v0.1.0 and `SHOWRUN_TEST_OTHER_PYTHON` to a second installed Showrun
  environment. Explicitly prepare both runtimes first. The suite verifies snapshot
  independence in both orders and decodes fixture MP4 pixels for ordered states/holds.
  Scripted navigation proves mechanics, not model competence or viewer comprehension.
- Navigation-only remains the default, not the product boundary. Explicit
  `authority.stories_comment` permits one exact whole-story comment on a fresh
  prepared selected revision, through observation-bound UI controls only. Keep
  Stories model/feedback authority off. Public reads verify retained annotations;
  draft/input text is never submitted-comment evidence. Tests in `test_comments.py`
  use real installed Stories UI with mocked Showrun inference, including lost
  acknowledgment/exact retry and scoped transport denials.
- New fixture identity v2 excludes only annotations/drafts from presentation
  identity. Preserve v1 hash rules and old request fingerprints; never migrate
  retained takes or overwrite earlier recordings to enable the new slice.
- **2026-09-19:** The owner reviewed the actual successful `happy01` take
  (28.6 seconds, 1080p navigation) and said the video was good. Its MP4 SHA-256 is
  `038f5f730e5de7fc1d4d65cc214baeb98266b20851a5dff75417100cffd2084b`.
  This is human acceptance of the limited first MVP slice, not full contract
  conformance or a claim about other takes.
