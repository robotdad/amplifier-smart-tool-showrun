# Generic UI and recipe-step review verification

## Supported boundary

Prepared URL targets use one observed UI operator for web apps and smart-tool
dashboards. The caller explicitly grants action classes, allowed input values and
all effects reachable in the target session. This is not semantic read-only
protection: narrower data/spending/effect permissions must be enforced by the
target itself. Browser traffic stays within one declared origin. Downloads,
uploads, popups, WebSockets and clipboard remain unsupported. Managed startup
still uses documented integrations; native computer use is not implemented.
Historical navigation/comment requests retain their original policy in the
compatibility module. No Outtake-specific grant or button handling remains.

Recipe-step selection seeks to its recording and selects its note anchor. Drafts
persist per clip/step, including the whole clip. Failed saves prevent a switch.
Public selection and notes use the original step identity, including duplicate
labels and steps without footage. `select_clip(step_id="")` explicitly returns
to whole-clip notes; omitted step_id preserves the previous clip draft behavior.

## Trials on 2026-09-20 UTC

- Local dog-walking form fixture: type Milo, select Alex, submit through the UI,
  and open care instructions. Four model decisions and four actions; all three
  steps passed. Independent saved application data contained Milo/alex. The final
  25.08-second 1440×900 MP4 decoded and sampled frames showed the form, entered
  values, confirmation and care checklist. SHA-256:
  `5451d823fa090bd1e9e51f99fcba9bbe5995e103c7e6df78443229ef1b807da7`.
- Unchanged Outtake dashboard: workspace → Saved Outputs → workspace. Two model
  decisions and two actions; all three steps passed. The final 24.4-second 1080p
  MP4 decoded and sampled frames showed each section. SHA-256:
  `01a290d885e793164ca7f638f1063b841f9c4f2c346fe5369dbab1eee2f72c91`.
  Exports were prepared beforehand; this does not demonstrate on-camera export.

These are live-model trials and diagnostic frame reviews, not independent human
acceptance or proof of unrestricted application support. Earlier trial takes
remain retained. An initial form take exposed a sampling bug that dropped a final
static update; first/latest frame retention per 50ms bucket fixes it. An initial
Outtake take failed because its requested final status message was transient;
the corrected brief checks the workspace title and playback control instead.

## Checks

- Broad Linux suite before the final capture correction: 161 passed, 16 skipped;
  one MCP browser test hit the host's macOS esbuild binary mounted into Linux.
  That same official AppBridge test passed in the host browser suite.
- After the capture correction: Linux capture/generic regression run, 62 passed,
  two optional installed-fixture checks skipped.
- Host review, recovery and real-browser suite: 28 passed, including the official
  independent MCP host and step draft switching/refresh. Additional whole-clip
  restoration and note submission checks pass.
- Ruff, package builds and provider-free manifest smoke passed. Optional installed
  fixture/second-installation checks were not available in the local container;
  CI provisions these explicitly. Generated MCP assets are rebuilt from shared UI.

No footage, access tokens or private trial directories are committed.
