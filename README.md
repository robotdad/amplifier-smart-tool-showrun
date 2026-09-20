# Showrun

Showrun records bounded application demonstrations and returns the actual footage
with per-step evidence. Its Python library owns capture and review; the CLI,
standalone dashboard and optional MCP App adapt those same capabilities.

Capture currently targets Linux with Chromium and FFmpeg. It supports prepared
web applications and isolated installed Stories dashboards, with navigation-only
access by default and optional exact Stories comment authority. Capture needs an
explicit provider/model and prepared runtime. Reviewing retained work uses no model.

## Review existing recordings

```sh
uv sync --extra dev --extra mcp
showrun --storage /path/to/takes review state --workspace editing
showrun --storage /path/to/takes review serve --workspace editing
# Alternatively, configure an MCP host to launch:
showrun-mcp --storage /path/to/takes --workspace editing
```

With a source checkout, use `uv run showrun` / `uv run showrun-mcp`, or activate
`.venv` before using these commands. Open the service's one-use bootstrap URL to
review its explicitly configured store. The dashboard and MCP App share the same
interface for playback, exact selection, drafts/notes, rename, deletion and downloads.
An MCP host must support Apps, binary resources and browser downloads.

Drafts and playback positions remain tied to their clips. Retries recover the
original operation without duplicating a note or retargeting a deletion. Deletion
removes scoped media and review text, preserving execution receipts and request-key
protection. Partial cleanup and uncertain outcomes remain explicit.

MP4 downloads preserve original bytes. ZIPs contain a fixed demo snapshot and a
hash inventory; incomplete snapshots are disclosed before browser download. Changed
or revoked members stop an existing transfer. Transfers are limited to 256 MiB;
each adapter retains at most four prepared ZIP snapshots. CLI exports never
overwrite an existing destination.

## Development and verification

Use Python 3.12+, Node.js 20+ and FFmpeg/ffprobe:

```sh
uv sync --extra dev --extra mcp
.venv/bin/python -m playwright install --with-deps chromium
npm ci --prefix mcp-app
npm run build --prefix mcp-app
.venv/bin/ruff check src tests
.venv/bin/python -m pytest -q -ra
uv build
```

The generated MCP HTML is checked in; installed users do not need Node.js.
The Linux CI workflow also installs a pinned Stories version and a second Showrun
wheel environment, prepares both runtimes without inference, and sets
`SHOWRUN_TEST_STORIES_PYTHON` and `SHOWRUN_TEST_OTHER_PYTHON` to exercise integration
checks. Without those installations, those checks explicitly skip. Capture tests
use Linux process identity; a macOS run cannot certify capture lifecycle behavior.

The tests use synthetic recordings and mocked inference. They verify mechanics,
not live model competence or the quality of a new demonstration.

Read the [packaged operating guide](src/amplifier_smart_tool_showrun/SMART_TOOL.md),
[vision](docs/VISION.md), [draft contracts](contracts/README.md) and
[contributor instructions](AGENTS.md) for capabilities, boundaries and test details.
