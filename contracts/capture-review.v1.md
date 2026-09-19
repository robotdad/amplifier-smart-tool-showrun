# Capture review workspace contract — v1 (DRAFT)

**Who builds against this:** People reviewing produced demonstrations, calling
agents, and maintainers of Showrun's dashboard and MCP App.

## What it looks like

The person reviews Showrun's produced recordings, not the live application being
demonstrated. The caller remains the primary place to discuss and refine the demo.
This is a behavioral example, not API syntax:

```text
Caller requests demo → Showrun retains an identified take, recordings and receipt
Person opens review → watches the actual video and selects a particular clip
Caller reads selection → knows exactly which take and clip the person means
Person optionally leaves a note → retained note targets that clip and time or step
Caller discusses changes → deliberately requests a new take; earlier takes remain
Person renames, deletes, downloads an MP4, or downloads the demo asset ZIP
```

Right: download the clip being displayed, even if another take has since arrived.
Wrong: selecting a clip or submitting a note silently starts another recording.

## Purpose and boundaries

Provide one optional review and management interface over library-owned retained
work. The standalone dashboard and MCP App are the same interface, not separate
products. Neither is required to request, record or inspect a demonstration.

[Caller interaction](caller-interaction.v1.md) owns request authority, take identity
and deliberate retakes. [Demo capture](demo-capture.v1.md) owns footage, media
identity, timeline precision and partial results. [Invocation](invocation.v1.md)
owns portable access; [internal execution](internal-execution.v1.md) owns capture
enforcement and resource cleanup. This contract adds review behavior without
redefining execution success.

For this contract, a **demo** groups a demonstration's explicitly related takes.
A **take** is an identified execution of a particular request. A **clip** is an
identified retained recording belonging to that take, not an editing instruction
or a newly trimmed segment. A single-recording take has one clip. Grouping is
explicit; similar names or text do not silently merge unrelated requests.

## Core (the teeth)

1. **Review opens retained work without executing it.** People can browse demos,
   their takes and available clips, including work created headlessly, and open
   an exact requested item. Opening, refreshing, reconnecting and inspecting review
   use no model, launch no target application and do not replay a request. Missing,
   deleted, restricted or unfinalized material has an explicit state, not a blank
   successful player. The library and CLI retain access without either UI.
2. **The dashboard and MCP App are exactly the same interface.** Reuse the same UI
   implementation, layout, controls, capabilities and workflow; only transport and
   host attachment differ. At equivalent viewport and theme they present the same
   interface. The same responsive rules apply at narrower sizes. Neither surface
   may omit rename, delete, playback, selection, notes or downloads, introduce an
   alternate workflow, or add adapter-only toolbars. Host chrome outside Showrun is
   not Showrun UI. A host integration that cannot deliver a required capability is
   reported as unsupported or failing, not called a conforming reduced interface.
3. **Playback presents the actual selected clip.** Provide play/pause, seeking,
   elapsed time, duration and fullscreen. A step with a recorded interval can seek
   to that interval using the delivered media timebase; missing intervals are not
   invented. The player binds to the retained clip identity and content hash, not
   a regenerated substitute. Playback does not require browser access to a path
   on the producing machine. Loading and playback errors are visible. A thumbnail,
   successful resource read or mounted iframe is not evidence of working playback.
4. **Outcomes remain distinct from review.** Display complete, failed, cancelled
   and uncertain takes honestly, with playable partial footage where available.
   Make the requested flow, per-step outcomes, capture limitations and cleanup
   findings inspectable without overwhelming the video. Successful execution,
   valid media and a person's assessment are separate facts. Notes or selection
   cannot rewrite an execution receipt or turn a failed take into a successful one.
5. **Selection is exact public state.** Selecting a clip durably identifies its demo,
   take and clip, plus the media identity, for the calling agent. Time/step context,
   when supplied, names that same media. Selection is scoped to an identified review
   workspace so unrelated callers or windows do not silently replace each other's
   targets. Public reads report the current selection and its version; UI context
   alone is not authoritative. Acknowledged selection survives reconnect. Conflicting
   updates are surfaced, not silently applied to a different take. Selection is
   neither approval nor permission to execute, and does not promise to wake a caller.
6. **New work does not disrupt review.** A newly available take is indicated without
   switching the displayed clip, resetting playback position or discarding typing.
   Names, current selection and retained availability can refresh without changing
   the review target. Returning to a workspace restores its acknowledged selection
   when available. If that item was deleted, report it and require a new selection
   rather than substituting the newest clip. Per-clip playback position is preserved
   during background updates; reopening does not automatically start playback.
7. **Rename changes a name, not identity.** Demos and retained takes/clips can have
   editable display names. A rename targets the exact item and expected metadata
   version; it preserves media, execution evidence, notes and references. Names are
   not filesystem paths or lookup authority. Empty/invalid names fail with a remedy.
   Conflicting edits are visible. A repeated rename request returns its retained
   outcome rather than renaming whichever item is now selected.
8. **Delete is explicit, scoped and safe for other work.** Before confirmation, show
   the exact selected item and deletion scope: a clip, take, or entire demo, including
   affected recordings, receipts and associated review records. The confirmation
   binds to that scope/version, not a mutable selection or display name. If new
   takes change a demo-wide deletion scope, confirmation must be renewed. Refuse
   deletion of active work; cancellation and terminal cleanup are separate actions.
   Remove only the confirmed items and exclusively owned assets; preserve other
   takes and assets still referenced by them. Invalidate affected selections and
   resource access and retain a minimal deletion outcome without the removed media.
   Report incomplete cleanup honestly; a success toast is not proof of removal.
   Deletion never deletes the demonstrated application's data or grants permission
   to repeat an old execution. Preserve request-key non-replay protection even when
   its media is deleted. Already downloaded copies cannot be recalled.
9. **Downloads deliver the selected retained assets.** Offer a direct MP4 download
   for the selected finalized clip and a ZIP containing all retained, downloadable
   demo assets at an identified snapshot: its clips, receipts and associated public
   review metadata. Include an inventory of identities, relative paths, hashes and
   completeness/limitations. ZIP contents can be resolved and hash-checked after
   extraction without Showrun's store or conversation. Disclose missing or restricted
   items before download and in the inventory; never label an incomplete package
   complete. Do not include unrelated demos, source collections, authentication,
   credentials, private runtime logs or temporary capture material. Downloads preserve
   original media bytes and receipt timing; they do not silently transcode, trim,
   regenerate or apply notes. If the retained format cannot satisfy direct MP4,
   state that explicitly rather than disguising another format as MP4. A concurrent
   rename, new take or selection change cannot retarget an initiated download;
   deletion/revoked access fails clearly rather than switching to another artifact.
10. **Notes are lightweight and precisely targeted.** A person may submit a note
    on the selected clip, optionally identifying a recorded step, timestamp or range.
    Validate anchors against that exact media and its duration. Draft notes are
    visibly unsubmitted; acknowledged saved drafts survive refresh, and save failures
    are visible. Submission produces a durable note identity and target receipt,
    available to the caller through public state. Switching clips cannot submit
    text against the new clip by accident; stale/deleted targets fail explicitly.
    Submission does not reinterpret the brief, start model work, reset the target
    or record another take. The caller handles discussion and refinement. No chat
    agent, threaded discussion system, automatic notification or caller wake-up is
    implied. These notes review Showrun output; they are not comments injected into
    the application being demonstrated.
11. **Appearance supports light, dark and system.** Both surfaces expose the same
    three choices, with documented persistence scope. Light/dark remain explicit
    overrides. System resolves to the current system appearance, including through
    an MCP host, and follows runtime changes without remounting the workspace,
    resetting playback or losing drafts. Partial host-context updates preserve the
    effective appearance. Styling applies to Showrun controls and surroundings,
    never recolors or otherwise alters the recorded video.
12. **All domain actions share library state and retry semantics.** Browsing,
    selection, names, deletion, submitted notes, saved drafts, media access and
    downloads are public library capabilities with CLI access. The dashboard and
    MCP adapter do not maintain competing domain stores. Mutations use stable
    request identities, exact targets and conflict checks; lost acknowledgments,
    reconnects and retries do not duplicate notes or effects. The UI distinguishes
    pending acceptance, completion and failure. Media and metadata delivery enforce
    the configured demo/take scope through bounded reads or streaming, not arbitrary
    paths or unrestricted resource handles. Another demo's identifiers do not grant
    access. Restricted footage remains withheld in both surfaces and downloads.
13. **Review lifecycle is independent of capture lifecycle.** Opening a viewer and
    taking browser focus require explicit selection. The standalone service is
    authenticated and local by default; the MCP adapter uses the host's declared
    access boundary without exposing service secrets to content. Closing a tab does
    not cancel recording, delete work, accept a result or grant further actions.
    Stopping the review service releases only its owned resources; retained work
    remains available. Review-service failure does not invalidate a completed take.
    Media, note text and imported metadata remain untrusted content, not authority.

## Acceptance checks

These are proposed checks, not executed results. Test both surfaces against the
same retained fixtures and public state; packaging conformance is separate.

| Clauses | Observable check | False positive to reject |
|---|---|---|
| 1, 4, 13 | Open successful and partial takes with providers disabled and the target stopped; inspect receipt outcomes and play available media. | Opening review silently launches the target, or playable partial footage is labeled complete. |
| 2, 11 | Run the same review, rename, delete, selection, note and download flows in dashboard and independent MCP host; compare layout/controls at matched desktop and narrow sizes in all three themes. Change system theme during playback and typing. | A second UI, adapter-only controls, working static theme but lost runtime theme, or a missing capability excused as host behavior. |
| 3, 9 | Load actual MP4, play through advancing decoded frames, pause, seek to a known step and fullscreen; download and hash-check the exact original. Repeat on a browser without access to producer-local paths. | A thumbnail, iframe load or download receipt mistaken for working video playback/delivery. |
| 5–6, 12 | Select clip A and read exact selection through a fresh caller; introduce take B, reconnect and switch workspaces. Keep A selected and preserve active playback/draft on updates. | Caller sees newest B or another workspace's selection while person still reviews A. |
| 7, 12 | Rename A concurrently, lose its response and retry with the same key; inspect identities, hashes and note targets. | Name used as identity, lost update, duplicate effect, or rename redirected by changed selection. |
| 8, 12 | Confirm deletion of A, change selection to B, then submit; race a new take against demo-wide confirmation and verify conflict. Exercise shared assets, active takes and cleanup failure. | B deleted, shared media removed, partial deletion called success, or deleted take key replayed. |
| 9 | Download the demo ZIP, move/extract it independently and resolve/hash-check every inventoried asset. Exercise unavailable/restricted clips and selection/deletion races. | Silent omissions, secret/local-path leakage, missing receipts or a ZIP requiring the private store. |
| 10, 12 | Save a draft on clip A at a known time, switch to B, then submit/retry against A; read one retained note via library. Delete A before another submission and verify rejection. | Draft presented as submitted, note silently targets B, or submission starts inference/recording. |
| 12–13 | Attempt out-of-scope IDs, paths and media reads; close/reopen the viewer during independently running capture. | Data from unrelated demos leaks, or viewing/closing reruns or cancels capture. |

## What v1 deliberately does NOT freeze

- Exact UI layout, icon artwork, API verbs or ZIP directory naming. Once implemented,
  both surfaces use the same design and behavior; this is not permission for variants.
- A video editor, trim/cut workflow, captions, narration or other post-production.
- A publishing, hosting, external sharing or approval-to-publish flow.
- Deep feedback threads, an in-app conversational agent, automatic brief rewriting
  or automatic re-recording from notes.
- Live remote control of the demonstrated app, automatic rehearsal/reset, or a new
  durable execution service. Existing capture authority and lifecycle still govern.
- Undo/restore for deleted media or synchronization with copies downloaded elsewhere.

## Changelog

- **2026-09-19** — Initial draft from owner direction: review produced demos,
  expose exact clip selection to the caller, support rename/delete and MP4/ZIP
  downloads, keep feedback lightweight, and use an identical dashboard/MCP App
  interface with light/dark/system appearance. No lock or implementation claim.