/* Shared review workspace controller.
 *
 * The standalone service and the MCP App provide only this transport:
 * call(operation, arguments), mediaUrl(info), download(kind, arguments), and
 * optional onHostContext(callback).  This file owns every control and workflow
 * on both surfaces.
 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let transport, state = null, selectedIdentity = "", dirty = false, deleting = null;
  let mediaUrl = "", mediaObjectUrl = null, refreshing = false, initial = true;
  let hostContext = {}, editorBinding = null, inputGeneration = 0;
  let pendingSelection = null, pendingDraft = null, pendingSubmit = null, pendingRename = null;
  let restoredIntents = false, view = null, sectionEnd = null;
  let mediaReady = Promise.resolve();
  let librarySort = null;
  const requestId = () => (crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.floor(Math.random() * 0x100000000).toString(16)}`);
  const identity = (selection) => selection ? [selection.demo_id, selection.take_id, selection.clip_id, selection.content_sha256].join("/") : "";
  const clone = (value) => value == null ? value : JSON.parse(JSON.stringify(value));
  const stable = (value) => Array.isArray(value)
    ? value.map(stable)
    : value && typeof value === "object"
      ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, stable(value[key])]))
      : value;
  const same = (left, right) => JSON.stringify(stable(left)) === JSON.stringify(stable(right));
  const selectedTarget = () => identity(selectedClip());
  const shortTarget = (target) => target ? target.split("/").slice(0, 3).join("/") : "no selected clip";
  const noteAnchor = (draft) => clone(draft?.anchor || {});
  const snapshotFromDraft = (draft) => ({text: draft?.text || "", anchor: noteAnchor(draft)});
  const currentSnapshot = () => ({text: $("note-text").value, anchor: noteArgs()});
  const draftMatchesSelection = (draft) => !!draft && draft.clip_id === selectedClip()?.clip_id;
  const message = (text, error = false) => {
    $("notice").textContent = text || "";
    $("notice").className = `notice${error ? " error" : " ok"}`;
  };
  const errorText = (error) => {
    const detail = error?.error || error?.cause || error;
    const base = detail?.message || error?.message || String(error);
    const remedy = detail?.remedy || error?.remedy;
    return remedy && !base.includes(remedy) ? `${base} ${remedy}` : base;
  };
  const definiteCodes = new Set([
    "invalid_anchor", "invalid_note", "invalid_range", "review_conflict", "selection_conflict",
    "draft_conflict", "scope_denied", "not_found", "media_unavailable", "invalid_target",
    "intent_revoked", "request_conflict", "invalid_name", "intent_missing", "draft_missing",
  ]);
  const isDefinite = (error) => definiteCodes.has(error?.code || error?.error?.code);
  async function rejectIntent(intent, error) {
    if (!intent?.intentId || !transport) return;
    try {
      await transport.call("reject_intent", {
        workspace_id: state.workspace_id, intent_id: intent.intentId,
        code: error?.code || error?.error?.code || "rejected",
        message: error?.message || error?.error?.message || errorText(error),
        remedy: error?.remedy || error?.error?.remedy || "Start a new explicit review action.",
      });
    } catch (_) { /* Preserve the local error; the next public read remains authoritative. */ }
  }
  async function acknowledgeIntent(intentId) {
    const receipt = await transport.call("ack_intent", {
      workspace_id: state.workspace_id, intent_id: intentId,
    });
    if (receipt?.state === "revoked") {
      throw Error("This review effect was revoked with its deleted retained target.");
    }
    return receipt;
  }
  async function run(work) {
    try { return await work(); }
    catch (error) { message(errorText(error), true); throw error; }
  }
  function selectedClip() { return state?.selected_clip || null; }
  function selectedTake() {
    const clip = selectedClip();
    return clip && state.demos.flatMap((d) => d.takes).find((take) => take.id === clip.take_id);
  }
  function selectedDemo() {
    const clip = selectedClip();
    return clip && state.demos.find((demo) => demo.id === clip.demo_id);
  }
  function allSteps() { return selectedTake()?.steps || []; }
  function adoptResult(result) {
    if (!result || !state) return false;
    const resultVersion = Number(result.version);
    if (Number.isFinite(resultVersion) && Number.isFinite(Number(state.version))
      && resultVersion < Number(state.version)) return false;
    const next = {...state};
    if (Number.isFinite(resultVersion)) next.version = resultVersion;
    ["selection", "selected_clip", "appearance", "playback", "invalidated_selection", "draft"].forEach((key) => {
      if (Object.prototype.hasOwnProperty.call(result, key)) next[key] = result[key];
    });
    if (result.clip) next.selected_clip = result.clip;
    if (result.selection && !Object.prototype.hasOwnProperty.call(result, "draft")) next.draft = null;
    if (result.note) {
      const notes = [...(state.notes || [])];
      if (!notes.some((note) => note.id === result.note.id)) notes.push(result.note);
      next.notes = notes;
    }
    state = next;
    return true;
  }
  function applyWorkspace(next) {
    if (!next) return;
    if (state && Number(next.version) < Number(state.version)) {
      state = {...state, demos: next.demos, notes: next.notes, new_take_count: next.new_take_count};
    } else {
      state = next;
    }
    state.notes ||= [];
  }
  function intentParts(intent) {
    const envelope = intent?.payload || {};
    const target = envelope.target || envelope;
    const operation = envelope.payload || envelope;
    return {envelope, target, operation};
  }
  function intentTarget(payload) {
    const target = payload?.target || payload;
    return target ? [
      target.demo_id, target.take_id, target.clip_id, target.content_sha256 || ""
    ].join("/") : "";
  }
  function intentSavePayload(intent) {
    const {target, operation} = intentParts(intent);
    return {
      workspace_id: state.workspace_id,
      clip_id: target.clip_id,
      text: operation.text || "",
      expected_version: operation.expected_version,
      request_id: operation.request_id || operation.save_request_id,
      intent_id: intent.intent_id,
      ...clone(operation.anchor || {}),
    };
  }
  function intentSubmitPayload(intent, expectedVersion) {
    const {target, operation} = intentParts(intent);
    return {
      workspace_id: state.workspace_id,
      clip_id: target.clip_id,
      expected_version: expectedVersion ?? operation.expected_version,
      request_id: operation.submit_request_id || operation.request_id,
      intent_id: intent.intent_id,
      text: operation.text,
      ...clone(operation.anchor || {}),
    };
  }
  function bindRecoveredEditor(target, snapshot) {
    if (selectedTarget() !== target) return;
    editorBinding = {target, snapshot: clone(snapshot)};
    dirty = false;
    applyEditorSnapshot(snapshot);
    inputGeneration += 1;
  }
  async function restoreIntents() {
    if (restoredIntents || !state) return;
    for (const intent of state.review_intents || []) {
      if (["acknowledged", "rejected", "revoked"].includes(intent.state)) continue;
      const {operation} = intentParts(intent);
      const target = intentTarget(intent.payload);
      const snapshot = {text: operation.text || "", anchor: clone(operation.anchor || {})};
      if (intent.kind === "save_draft" && !pendingDraft && !pendingSubmit) {
        pendingDraft = {
          target,
          intentId: intent.intent_id,
          snapshot,
          generation: inputGeneration,
          requestId: operation.request_id || operation.save_request_id || intent.intent_id,
          payload: intentSavePayload(intent),
          started: true,
        };
        bindRecoveredEditor(target, snapshot);
        pendingDraft.generation = inputGeneration;
      } else if (intent.kind === "submit_note" && !pendingSubmit) {
        const prepared = intent.state === "prepared";
        const completed = intent.state === "completed_unacknowledged";
        pendingSubmit = {
          target,
          intentId: intent.intent_id,
          snapshot,
          started: true,
          saveRequestId: operation.save_request_id || `${intent.intent_id}-save`,
          submitRequestId: operation.submit_request_id || operation.request_id || intent.intent_id,
          phase: prepared || completed ? "submit" : "save",
          savePayload: prepared || completed ? null : intentSavePayload(intent),
          submitPayload: intentSubmitPayload(
            intent,
            prepared ? intent.result?.version : completed ? intent.result?.version : operation.expected_version,
          ),
        };
        bindRecoveredEditor(target, snapshot);
        pendingSubmit.generation = inputGeneration;
      }
    }
    restoredIntents = true;
    if (!pendingDraft && !pendingSubmit && selectedClip()) bindEditorToCurrentSelection();
    renderNote();
  }
  function setView(next) {
    view = next === "review" && selectedClip() ? "review" : "library";
    document.body.dataset.view = view;
    $("show-library").setAttribute("aria-pressed", String(view === "library"));
    $("show-review").setAttribute("aria-pressed", String(view === "review"));
    $("show-review").disabled = !selectedClip();
    if (view === "library") $("player").pause();
  }
  function updatePlayback() {
    const player = $("player");
    const available = selectedClip()?.status === "available";
    $("toggle-play").disabled = !available;
    $("toggle-play").textContent = player.paused ? "Play recording" : "Pause recording";
    const format = value => `${Math.floor((value || 0) / 60)}:${String(Math.floor((value || 0) % 60)).padStart(2, "0")}`;
    $("playback-state").textContent = !available ? "Recording unavailable"
      : `${player.paused ? "Paused" : "Playing"} · ${format(player.currentTime)} / ${format(player.duration)}`;
  }
  async function startPlayback(step = null) {
    const target = selectedTarget();
    await mediaReady;
    if (target !== selectedTarget()) throw Error("The recording changed before playback could start.");
    const player = $("player");
    sectionEnd = step?.interval ? Number(step.interval.end_seconds) : null;
    if (step?.interval) player.currentTime = Number(step.interval.start_seconds);
    else if (player.ended) player.currentTime = 0;
    try { await player.play(); }
    catch (error) {
      sectionEnd = null;
      message("Playback did not start. Use Play recording to retry. " + errorText(error), true);
      throw error;
    }
    updatePlayback();
  }
  const icons = {
    download: "M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5",
    trash: "M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7",
    light: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5",
    dark: "M20.5 13A9 9 0 0 1 11 3.5 9 9 0 1 0 20.5 13Z",
    system: "M3 3h18v14H3zM8 21h8m-4-4v4",
  };
  function actionIcon(button, name, icon) {
    button.classList.add("icon-action");
    button.title = name;
    button.setAttribute("aria-label", name);
    button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="' + icons[icon] + '"/></svg>';
    return button;
  }
  function iconButton(name, icon, work) {
    const button = actionIcon(document.createElement("button"), name, icon);
    button.type = "button";
    button.onclick = () => run(work).catch(() => {});
    return button;
  }
  function editableName(scope, item) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "editable-name";
    button.textContent = item.name;
    button.title = "Rename " + scope;
    button.setAttribute("aria-label", "Rename " + scope + ": " + item.name);
    button.onclick = () => {
      const input = document.createElement("input");
      input.className = "inline-name";
      input.value = item.name;
      input.maxLength = 200;
      input.title = "Enter to save · Escape to cancel";
      input.setAttribute("aria-label", "New " + scope + " name");
      let finished = false;
      const cancel = () => { finished = true; input.replaceWith(button); button.focus(); };
      const save = async () => {
        if (finished) return;
        if (input.value === item.name) return cancel();
        finished = true;
        input.disabled = true;
        try { await rename(scope, item, input.value); }
        catch (error) {
          finished = false;
          input.disabled = false;
          message(errorText(error), true);
          input.focus();
        }
      };
      input.onkeydown = (event) => {
        if (event.key === "Enter") { event.preventDefault(); save(); }
        if (event.key === "Escape") { event.preventDefault(); cancel(); }
      };
      input.onblur = save;
      button.replaceWith(input);
      input.focus();
      input.select();
    };
    return button;
  }
  const sortKey = () => "showrun.library-sort." + (state?.workspace_id || "default");
  function libraryOrder(items, recentField) {
    return [...items].sort((a, b) => {
      const byName = String(a.name).localeCompare(String(b.name), undefined, {numeric: true, sensitivity: "base"});
      return (librarySort === "recent" ? (Number(b[recentField]) || 0) - (Number(a[recentField]) || 0) : byName)
        || byName || String(a.id).localeCompare(String(b.id));
    });
  }
  function renderTree() {
    if (!librarySort) {
      try { librarySort = localStorage.getItem(sortKey()); } catch (_) {}
      if (!["recent", "name"].includes(librarySort)) librarySort = "recent";
    }
    $("library-sort").value = librarySort;
    const root = $("demo-tree");
    root.replaceChildren();
    const demos = libraryOrder(state?.demos || [], "recent_at");
    $("demo-count").textContent = String(demos.length);
    $("tree-empty").hidden = demos.length > 0;
    demos.forEach((demo) => {
      const group = document.createElement("section");
      group.className = "tree-group" + (state.selection?.demo_id === demo.id ? " selected-demo" : "");
      const heading = document.createElement("div");
      heading.className = "library-row demo-heading";
      heading.append(editableName("demo", demo),
        iconButton("Download demo ZIP: " + demo.name, "download", () => downloadDemo(demo)),
        iconButton("Delete demo: " + demo.name, "trash", () => prepareDelete("demo", demo)));
      group.append(heading);
      libraryOrder(demo.takes, "added_at").forEach((take) => {
        const takeWrap = document.createElement("div");
        takeWrap.className = "tree-take";
        const heading = document.createElement("div");
        heading.className = "library-row take-heading";
        const status = document.createElement("span");
        status.className = "badge " + outcomeClass(take.status);
        status.textContent = take.status;
        heading.append(editableName("take", take), status,
          iconButton("Delete take: " + take.name, "trash", () => prepareDelete("take", take)));
        takeWrap.append(heading);
        take.clips.forEach((clip) => {
          const selected = identity(state.selection) === identity(clip);
          const row = document.createElement("div");
          row.className = "library-row clip-row" + (selected ? " selected" : "");
          row.append(editableName("clip", clip));
          const status = document.createElement("span");
          status.className = "muted small clip-status";
          status.textContent = selected ? "Selected · " + clip.status : clip.status;
          row.append(status);
          const open = document.createElement("button");
          open.type = "button";
          open.className = "tree-clip" + (selected ? " selected" : "");
          open.textContent = "Open";
          open.setAttribute("aria-label", "Open " + demo.name + " · " + take.name + " · " + clip.name);
          open.setAttribute("aria-current", String(selected));
          open.onclick = () => run(() => selectClip(clip)).catch(() => {});
          row.append(open, iconButton("Delete clip: " + clip.name + " · " + take.name,
            "trash", () => prepareDelete("clip", clip)));
          takeWrap.append(row);
        });
        group.append(takeWrap);
      });
      root.append(group);
    });
  }
  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[char]));
  }
  function outcomeClass(status) {
    return ["succeeded", "available", "completed"].includes(status) ? "ok" : "bad";
  }
  function renderSelection({ reloadMedia = false } = {}) {
    const clip = selectedClip();
    $("empty-player").hidden = !!clip;
    $("player-wrap").hidden = !clip;
    $("clip-identity").hidden = !clip;
    $("step-list").hidden = !clip;
    $("receipt-details").hidden = !clip;
    $("download-mp4").disabled = !clip || clip.status !== "available";
    if (!clip) {
      const player = $("player");
      player.pause();
      player.removeAttribute("src");
      player.load();
      if (mediaObjectUrl) URL.revokeObjectURL(mediaObjectUrl);
      mediaObjectUrl = null;
      $("empty-player").querySelector("strong").textContent = state.invalidated_selection
        ? "Selected clip was deleted" : "No clip selected";
      $("selected-name").textContent = "Choose a retained clip";
      $("selected-status").textContent = "Select a demo, take and clip to review.";
      $("outcome-badge").hidden = true;
      updatePlayback();
      renderNote();
      return;
    }
    const take = selectedTake();
    const demo = selectedDemo();
    $("selected-name").textContent = demo?.name || clip.demo_id;
    $("selected-status").textContent = `${take?.name || clip.take_id} · ${clip.name} · ${take?.status || "unknown"}`;
    $("outcome-badge").hidden = false;
    $("outcome-badge").textContent = take?.status || clip.status;
    $("outcome-badge").className = `badge ${outcomeClass(take?.status || clip.status)}`;
    $("clip-identity").textContent = [
      `demo_id=${clip.demo_id}`, `take_id=${clip.take_id}`, `clip_id=${clip.clip_id}`,
      `media_sha256=${clip.content_sha256 || "none"}`, `receipt_sha256=${clip.receipt_sha256 || "none"}`,
    ].join(" · ");
    $("receipt").textContent = JSON.stringify(take?.receipt || {
      status: take?.status, error: take?.error, cleanup: take?.cleanup, partial: take?.partial,
    }, null, 2);
    const steps = $("steps");
    steps.replaceChildren();
    for (const step of allSteps()) {
      const row = document.createElement("div");
      row.className = "step-row";
      const detail = document.createElement("div");
      const title = document.createElement("div");
      title.className = "step-name";
      title.textContent = `${allSteps().indexOf(step) + 1}. ${step.requested?.instruction || step.id || "Unnamed step"}`;
      const status = document.createElement("div");
      status.className = "step-state";
      const interval = step.interval;
      status.textContent = `${step.status || "unknown"}${interval ? ` · ${Number(interval.start_seconds).toFixed(2)}–${Number(interval.end_seconds).toFixed(2)}s` : " · no recorded interval"}`;
      detail.append(title, status);
      row.append(detail);
      const jump = document.createElement("button");
      jump.type = "button";
      jump.textContent = interval && clip.status === "available" ? "Play step" : "Note step";
      jump.setAttribute("aria-pressed", String(state.selection?.step_id === step.id));
      jump.onclick = () => run(() => selectStep(step));
      row.append(jump);
      steps.append(row);
    }
    if (reloadMedia) {
      sectionEnd = null;
      mediaReady = loadMedia(clip);
      mediaReady.catch((error) => message(errorText(error), true));
    }
    updatePlayback();
    renderNote();
  }
  async function loadMedia(clip) {
    if (!clip || clip.status !== "available") {
      $("media-error").hidden = !clip;
      $("media-error").textContent = clip ? `Media is ${clip.media?.status || clip.status}; playback is unavailable.` : "";
      $("player").removeAttribute("src");
      return;
    }
    const current = identity(state.selection);
    const url = await transport.mediaUrl({ ...clip, workspace_id: state.workspace_id });
    if (!state.selection || identity(state.selection) !== current || selectedClip()?.clip_id !== clip.clip_id) return;
    if (mediaObjectUrl) URL.revokeObjectURL(mediaObjectUrl);
    mediaObjectUrl = url.startsWith("blob:") ? url : null;
    mediaUrl = url;
    const player = $("player");
    const position = state.playback?.clip_id === clip.clip_id ? Number(state.playback.time_seconds || 0) : 0;
    player.pause();
    player.src = url;
    player.load();
    await new Promise((resolve, reject) => {
      const finish = (error) => {
        clearTimeout(timer);
        player.removeEventListener("loadedmetadata", loaded);
        player.removeEventListener("error", failed);
        error ? reject(error) : resolve();
      };
      const loaded = () => {
        if (identity(state.selection) !== current) return finish();
        if (Number.isFinite(position)) player.currentTime = Math.min(position, player.duration || position);
        updatePlayback();
        finish();
      };
      const failed = () => finish(Error("The recording could not be loaded."));
      const timer = setTimeout(() => finish(Error("The recording did not become ready. Reload to retry.")), 15000);
      player.addEventListener("loadedmetadata", loaded, {once: true});
      player.addEventListener("error", failed, {once: true});
      if (player.readyState >= 1) loaded();
    });
    $("media-error").hidden = true;
  }

  function applyEditorSnapshot(snapshot) {
    const anchor = snapshot?.anchor || {};
    $("note-text").value = snapshot?.text || "";
    $("note-step").value = anchor.step_id || "";
    $("note-time").value = anchor.time_seconds ?? anchor.range_start_seconds ?? "";
    $("note-range-end").value = anchor.range_end_seconds ?? "";
  }
  function bindEditorToCurrentSelection() {
    const target = selectedTarget();
    const draft = draftMatchesSelection(state?.draft) ? state.draft : null;
    const snapshot = snapshotFromDraft(draft);
    if (!draft && state.selection?.step_id) snapshot.anchor.step_id = state.selection.step_id;
    editorBinding = {target, snapshot};
    dirty = false;
    inputGeneration += 1;
  }
  function renderNote() {
    const clip = selectedClip();
    if (!clip) {
      $("note-text").value = "";
      $("note-text").disabled = true;
      $("note-step").disabled = true;
      $("note-time").disabled = true;
      $("note-range-end").disabled = true;
      $("save-draft").disabled = true;
      $("submit-note").disabled = true;
      $("draft-state").textContent = "Unsubmitted";
      return;
    }
    if (!editorBinding) bindEditorToCurrentSelection();
    const target = selectedTarget();
    if (!dirty && !pendingDraft && !pendingSubmit && editorBinding.target !== target) {
      bindEditorToCurrentSelection();
    }
    const exactTarget = editorBinding.target === target;
    const pendingOtherTarget = (pendingDraft && pendingDraft.target !== target)
      || (pendingSubmit && pendingSubmit.target !== target);
    const canEdit = exactTarget && !pendingOtherTarget;
    $("note-text").disabled = !canEdit;
    $("note-step").disabled = !canEdit;
    $("note-time").disabled = !canEdit;
    $("note-range-end").disabled = !canEdit;
    $("save-draft").disabled = !canEdit || !!pendingSubmit;
    $("submit-note").disabled = !canEdit || !!pendingDraft;
    const activeStep = dirty ? $("note-step").value : editorBinding.snapshot?.anchor?.step_id || state.selection?.step_id || "";
    $("note-step").value = activeStep;
    if (exactTarget && !dirty && !pendingDraft && !pendingSubmit) {
      const draft = draftMatchesSelection(state?.draft) ? state.draft : null;
      if (draft) editorBinding.snapshot = snapshotFromDraft(draft);
      applyEditorSnapshot(editorBinding.snapshot);
    }
    if (!exactTarget) {
      $("draft-state").textContent = `${dirty ? "Unsaved" : "Saved"} note bound to ${shortTarget(editorBinding.target)} · reselect that clip to continue`;
    } else if (pendingSubmit) {
      $("draft-state").textContent = `Submission pending · retry uses ${pendingSubmit.submitRequestId}`;
    } else if (pendingDraft) {
      $("draft-state").textContent = "Draft save pending · retry uses the same request";
    } else if (dirty) {
      $("draft-state").textContent = "Unsaved draft";
    } else {
      $("draft-state").textContent = draftMatchesSelection(state?.draft)
        ? "Saved draft · not submitted" : "Unsubmitted";
    }
    const stepId = $("note-step").value;
    $("note-heading").textContent = stepId ? "Note on this step" : "Note on whole recording";
    const notedStep = allSteps().find(item => item.id === stepId);
    $("note-target").textContent = stepId ? (notedStep?.requested?.instruction || stepId) : "Whole recording";
    $("whole-clip-note").setAttribute("aria-pressed", String(!stepId));
    $("step-notes").replaceChildren();
    for (const note of state.notes || []) {
      if (note.clip_id !== clip.clip_id || (note.anchor?.step_id || "") !== stepId) continue;
      const item = document.createElement("p");
      item.textContent = note.text;
      $("step-notes").append(item);
    }

  }
  function render() {
    if (!state) return;
    const before = selectedIdentity;
    selectedIdentity = identity(state.selection);
    renderTree();
    renderSelection({ reloadMedia: before !== selectedIdentity || initial });
    setView(view || (selectedClip() ? "review" : "library"));
    initial = false;
  }
  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    try {
      const previous = selectedIdentity;
      const next = await transport.call("workspace", { workspace_id: state?.workspace_id || transport.workspaceId || "default" });
      if (!next) return;
      applyWorkspace(next);
      themeChanged(state.appearance || "system");
      const revoked = new Set((state.review_intents || []).filter((item) => item.state === "revoked").map((item) => item.intent_id));
      if (revoked.has(pendingDraft?.intentId)) pendingDraft = null;
      if (revoked.has(pendingSubmit?.intentId)) pendingSubmit = null;
      if (state.invalidated_selection && !state.selection) {
        dirty = false;
        editorBinding = null;
      }
      if (!deleting && state.pending_deletions?.length) {
        deleting = state.pending_deletions[0];
        $("delete-preview").hidden = false;
        $("delete-preview").textContent = JSON.stringify(deleting.snapshot, null, 2);
        $("confirm-delete").hidden = false;
        $("confirm-delete").textContent = deleting.state === "effect_started" ? "Check deletion outcome" : "Confirm exact deletion";
        $("delete-description").textContent = "A prior deletion is awaiting confirmation or its outcome. Review the exact affected items.";
        $("review-deletion").hidden = false;
      }
      if (!deleting && state.deletion_results?.length) {
        deleting = state.deletion_results[0];
        $("delete-preview").hidden = false;
        $("delete-preview").textContent = JSON.stringify(deleting.result, null, 2);
        $("confirm-delete").hidden = true;
        if (deleting.result?.status !== "deleted") {
          $("delete-description").textContent = "A previous deletion needs attention: " + deleting.result?.status;
          $("cancel-delete").textContent = "Close";
          $("review-deletion").hidden = false;
        }
      }
      const same = identity(state.selection) === previous;
      render();
      if (same && $("player").src && !$("player").paused) {
        // Rendering metadata must never reload a playing retained clip.
        $("player").currentTime = $("player").currentTime;
      }
      if (state.new_take_count) message(`${state.new_take_count} retained take(s) available. Current selection was preserved.`);
      else if (!previous) message("Ready · retained work only.");
    } finally { refreshing = false; }
  }
  async function selectClip(clip, openReview = true) {
    if (!clip || !state) return;
    const target = identity(clip);
    if (pendingSelection && pendingSelection.target !== target) {
      throw Error(`Selection request for ${shortTarget(pendingSelection.target)} is still pending; retry or refresh before switching.`);
    }
    if (!pendingSelection) {
      pendingSelection = {
        target,
        payload: {
          workspace_id: state.workspace_id, demo_id: clip.demo_id, take_id: clip.take_id, clip_id: clip.clip_id,
          expected_version: state.version, request_id: requestId(),
        },
      };
    }
    const operation = pendingSelection;
    let result;
    try { result = await transport.call("select_clip", operation.payload); }
    catch (error) { if (isDefinite(error)) pendingSelection = null; throw error; }
    adoptResult(result);
    pendingSelection = null;
    if (!dirty && !pendingDraft && !pendingSubmit) bindEditorToCurrentSelection();
    message(openReview ? "Recording selected. Choose Play recording or a recipe step." : "Manage the selected library recording.");
    view = openReview ? "review" : "library";
    render();
    return result;
  }
  async function selectStep(step) {
    if (pendingSubmit) throw Error("Finish the pending note submission before switching steps.");
    if (dirty || pendingDraft) await saveDraft();
    // Saving is awaited so failure preserves both text and the old selection.
    const clip = selectedClip();
    if (!clip) throw Error("Select a clip first.");
    const target = `${identity(clip)}/${step.id || ""}`;
    if (pendingSelection && pendingSelection.target !== target) throw Error("Retry the pending step selection first.");
    if (!pendingSelection) pendingSelection = {target, payload: {
      workspace_id: state.workspace_id, demo_id: clip.demo_id, take_id: clip.take_id,
      clip_id: clip.clip_id, expected_version: state.version, request_id: requestId(),
      step_id: step.id || "",
      ...(step.interval ? {time_seconds: Number(step.interval.start_seconds)} : {}),
    }};
    let result;
    try { result = await transport.call("select_clip", pendingSelection.payload); }
    catch (error) { if (isDefinite(error)) pendingSelection = null; throw error; }
    pendingSelection = null;
    adoptResult(result);
    bindEditorToCurrentSelection();
    if (!state.draft) editorBinding.snapshot = {text: "", anchor: step.id ? {step_id: step.id} : {}};
    render();
    if (step.interval && clip.status === "available") {
      await startPlayback(step);
      message("Playing the selected section. Your note stays attached to this step.");
    } else {
      sectionEnd = null;
      $("player").pause();
      message(step.id ? "This step has no recorded section. You can still leave a step note." : "Your note applies to the whole recording.");
    }
  }
  function noteArgs() {
    const anchor = {};
    if ($("note-step").value) anchor.step_id = $("note-step").value;
    if ($("note-time").value !== "") anchor.time_seconds = Number($("note-time").value);
    if ($("note-range-end").value !== "") {
      anchor.range_start_seconds = anchor.time_seconds;
      anchor.range_end_seconds = Number($("note-range-end").value);
      delete anchor.time_seconds;
    }
    return anchor;
  }
  function makeDraftOperation(snapshot, target) {
    const clip = selectedClip();
    return {
      target,
      snapshot: clone(snapshot),
      generation: inputGeneration,
      intentId: requestId(),
      started: false,
      requestId: requestId(),
      payload: {
        workspace_id: state.workspace_id, clip_id: clip.clip_id, text: snapshot.text,
        demo_id: clip.demo_id, take_id: clip.take_id, content_sha256: clip.content_sha256,
        expected_version: state.version, request_id: null, intent_id: null, ...clone(snapshot.anchor),
      },
    };
  }
  async function saveDraft() {
    const clip = selectedClip();
    if (!clip) throw Error("Select an exact clip before saving a draft.");
    if (!editorBinding || editorBinding.target !== selectedTarget()) {
      throw Error(`This note is bound to ${shortTarget(editorBinding?.target)}. Reselect that clip before saving.`);
    }
    if (pendingSubmit) throw Error("A note submission is pending; retry it before starting another save.");
    const snapshot = currentSnapshot();
    if (pendingDraft && pendingDraft.target !== selectedTarget()) {
      throw Error("Reselect the original clip to retry its pending draft.");
    }
    if (!pendingDraft) pendingDraft = makeDraftOperation(snapshot, selectedTarget());
    const operation = pendingDraft;
    operation.payload.request_id = operation.requestId;
    operation.payload.intent_id = operation.intentId;
    try {
      if (!operation.started) {
        await transport.call("begin_intent", {
          workspace_id: state.workspace_id, intent_id: operation.intentId, kind: "save_draft",
          payload: {
            target: {
              demo_id: operation.payload.demo_id, take_id: operation.payload.take_id,
              clip_id: operation.payload.clip_id, content_sha256: operation.payload.content_sha256,
            },
            payload: {
              text: operation.payload.text, anchor: clone(operation.snapshot.anchor),
              expected_version: operation.payload.expected_version,
              request_id: operation.payload.request_id,
            },
          },
        });
        operation.started = true;
      }
      const result = await transport.call("save_draft", operation.payload);
      adoptResult(result);
      await acknowledgeIntent(operation.intentId);
      pendingDraft = null;
      const stillSame = editorBinding?.target === operation.target
        && selectedTarget() === operation.target
        && inputGeneration === operation.generation
        && same(currentSnapshot(), operation.snapshot);
      if (stillSame) {
        editorBinding.snapshot = clone(operation.snapshot);
        dirty = false;
        message("Draft saved · not submitted.");
      } else {
        dirty = true;
        message("Draft saved, but newer local note text remains unsaved.");
      }
      renderNote();
      return result;
    } catch (error) {
      if (isDefinite(error)) {
        await rejectIntent(operation, error);
        pendingDraft = null;
      }
      throw error;
    }
  }
  function savedDraftMatches(target, snapshot) {
    return draftMatchesSelection(state?.draft)
      && same(snapshotFromDraft(state.draft), snapshot);
  }
  async function submitNote() {
    const clip = selectedClip();
    if (!clip) throw Error("Select an exact clip before submitting a note.");
    if (!pendingSubmit && (!editorBinding || editorBinding.target !== selectedTarget())) {
      throw Error(`This note is bound to ${shortTarget(editorBinding?.target)}. Reselect that clip before submitting.`);
    }
    if (!pendingSubmit) {
      const target = editorBinding.target;
      const snapshot = currentSnapshot();
      pendingSubmit = {
        target,
        snapshot: clone(snapshot),
        intentId: requestId(),
        started: false,
        saveRequestId: requestId(),
        submitRequestId: requestId(),
        phase: savedDraftMatches(target, snapshot) ? "submit" : "save",
        savePayload: null,
        submitPayload: null,
      };
      if (pendingSubmit.phase === "save") {
        pendingSubmit.savePayload = {
          workspace_id: state.workspace_id, clip_id: clip.clip_id, text: snapshot.text,
          expected_version: state.version, request_id: pendingSubmit.saveRequestId,
          intent_id: pendingSubmit.intentId,
          ...clone(snapshot.anchor),
        };
      } else {
        pendingSubmit.submitPayload = {
          workspace_id: state.workspace_id, clip_id: clip.clip_id,
          expected_version: state.version, request_id: pendingSubmit.submitRequestId,
          intent_id: pendingSubmit.intentId, text: snapshot.text, ...clone(snapshot.anchor),
        };
      }
    }
    const operation = pendingSubmit;
    message("Submitting the exact retained note…");
    try {
      if (!operation.started) {
        await transport.call("begin_intent", {
          workspace_id: state.workspace_id, intent_id: operation.intentId, kind: "submit_note",
          payload: {
            target: {
              demo_id: selectedClip()?.demo_id, take_id: selectedClip()?.take_id,
              clip_id: selectedClip()?.clip_id, content_sha256: selectedClip()?.content_sha256,
            },
            payload: {
              text: operation.snapshot.text, anchor: clone(operation.snapshot.anchor),
              expected_version: state.version, save_request_id: operation.saveRequestId,
              submit_request_id: operation.submitRequestId,
            },
          },
        });
        operation.started = true;
      }
      if (operation.phase === "save") {
        const saved = await transport.call("save_draft", operation.savePayload);
        adoptResult(saved);
        operation.phase = "submit";
        operation.submitPayload = {
          ...operation.submitPayload,
          workspace_id: state.workspace_id, clip_id: operation.savePayload.clip_id,
          expected_version: saved.version, request_id: operation.submitRequestId,
          intent_id: operation.intentId, text: operation.snapshot.text,
          ...clone(operation.snapshot.anchor),
        };
      }
      const result = await transport.call("submit_note", operation.submitPayload);
      adoptResult(result);
      await acknowledgeIntent(operation.intentId);
      pendingSubmit = null;
      const stillSame = editorBinding?.target === operation.target
        && selectedTarget() === operation.target
        && same(currentSnapshot(), operation.snapshot);
      if (stillSame) {
        editorBinding.snapshot = clone(operation.snapshot);
        dirty = false;
      } else dirty = true;
      message("Note submitted on the exact retained clip.");
      await refresh();
      return result;
    } catch (error) {
      if (isDefinite(error)) {
        await rejectIntent(operation, error);
        pendingSubmit = null;
      }
      throw error;
    }
  }
  async function rename(scope, item, name) {
    if (pendingRename && (pendingRename.payload.item_id !== item.id || pendingRename.payload.item_type !== scope
      || pendingRename.payload.name !== name)) {
      throw Error("Retry the pending rename with the same name before renaming another item.");
    }
    if (!pendingRename) {
      if (!item) throw Error("Select an exact retained item before renaming.");
      pendingRename = {
        payload: {
          workspace_id: state.workspace_id, item_type: scope, item_id: item.id, name,
          expected_version: item.version, request_id: requestId(),
        },
      };
    }
    let result;
    try { result = await transport.call("rename", pendingRename.payload); }
    catch (error) { if (isDefinite(error)) pendingRename = null; throw error; }
    pendingRename = null;
    message(`Renamed ${scope} without changing its stable identity.`);
    await refresh();
    return result;
  }
  async function prepareDelete(scope, item) {
    if (!item) throw Error("Select an exact retained item before deletion.");
    const result = await transport.call("prepare_delete", {
      workspace_id: state.workspace_id, scope, target_id: item.id, expected_version: item.version,
      request_id: requestId(),
    });
    deleting = result;
    $("delete-preview").hidden = false;
    $("delete-preview").textContent = JSON.stringify(result.snapshot, null, 2);
    $("confirm-delete").hidden = false;
    $("confirm-delete").textContent = "Confirm exact deletion";
    $("delete-title").textContent = "Delete " + scope + " “" + item.name + "”?";
    $("delete-description").textContent = "This removes media for " + result.snapshot.clip_ids.length
      + " clip(s). Receipts remain. This cannot be undone.";
    $("cancel-delete").textContent = "Cancel";
    $("delete-dialog").showModal();
    message("Review the exact deletion scope, then confirm.");
  }
  async function confirmDelete() {
    if (!deleting) throw Error("Prepare an exact deletion first.");
    const operation = deleting;
    const result = await transport.call("delete", {
      request_id: operation.commit_request_id || operation.request_id,
      workspace_id: state.workspace_id, confirmation_token: operation.confirmation_token,
    });
    deleting = {...operation, result};
    $("confirm-delete").hidden = result.status !== "pending";
    $("confirm-delete").textContent = "Check deletion outcome";
    $("delete-preview").hidden = false;
    $("delete-preview").textContent = JSON.stringify(result, null, 2);
    const status = result.status;
    const text = status === "deleted"
      ? "Deletion completed; retained receipt and request protection remain."
      : status === "cleanup_incomplete"
        ? "Deletion recorded, but cleanup is incomplete; retained media remains for the affected item."
        : status === "uncertain"
          ? "Deletion effect is uncertain; no success was inferred. Inspect retained state."
          : `Deletion outcome: ${status || "unknown"}.`;
    message(text, status !== "deleted");
    $("delete-description").textContent = text;
    $("cancel-delete").textContent = "Close";
    if (status === "deleted") $("delete-dialog").close();
    await refresh();
  }
  async function downloadDemo(demo) {
    if (!demo) throw Error("Select a demo first.");
    const result = await transport.download("zip", { workspace_id: state.workspace_id, demo_id: demo.id }, (inventory) => {
      if (typeof inventory.complete !== "boolean") throw Error("ZIP completeness was not reported.");
      if (inventory.complete) return true;
      const detail = `ZIP snapshot incomplete. ${(inventory.limitations || []).join(" ")}`;
      $("download-status").textContent = detail;
      message(detail, true);
      return window.confirm(`${detail} Download this incomplete snapshot?`);
    });
    if (result.cancelled) return result;
    if (typeof result.complete !== "boolean") {
      $("download-status").textContent = "ZIP inventory was not returned; download was not called safely.";
      throw Error("The ZIP inventory did not report completeness.");
    }
    const limitation = result.limitations?.length ? ` ${result.limitations.join(" ")}` : "";
    $("download-status").textContent = result.complete
      ? "ZIP snapshot complete; manifest and hashes are included."
      : `ZIP snapshot incomplete; review limitations before using it.${limitation}`;
    if (!result.complete) message($("download-status").textContent, true);
    return result;
  }
  function themeChanged(preference, host = null) {
    if (host && typeof host === "object") hostContext = {...hostContext, ...host};
    const system = hostContext.theme || hostContext.colorScheme
      || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const resolved = preference === "system" ? (system === "dark" ? "dark" : "light") : preference;
    document.documentElement.dataset.theme = resolved;
    document.documentElement.dataset.themePreference = preference;
    document.querySelectorAll("[data-theme-choice]").forEach(button => {
      button.setAttribute("aria-pressed", String(button.dataset.themeChoice === preference));
    });
  }
  function bind() {
    $("refresh").onclick = () => run(refresh);
    $("show-library").onclick = () => setView("library");
    $("library-sort").onchange = () => {
      librarySort = $("library-sort").value;
      try { localStorage.setItem(sortKey(), librarySort); } catch (_) {}
      renderTree();
    };
    $("show-review").onclick = () => setView("review");
    $("whole-clip-note").onclick = () => run(() => selectStep({id: null}));
    $("toggle-play").onclick = () => run(async () => {
      if ($("player").paused) await startPlayback();
      else $("player").pause();
    });
    ["play", "playing", "pause", "ended", "loadedmetadata"].forEach(event => $("player").addEventListener(event, updatePlayback));
    ["light", "dark", "system"].forEach(preference => {
      const button = $("theme-" + preference);
      actionIcon(button, {light: "Light theme", dark: "Dark theme", system: "Use system theme"}[preference], preference);
      button.onclick = () => run(async () => {
        const result = await transport.call("appearance", {
          workspace_id: state.workspace_id, appearance: preference, expected_version: state.version,
          request_id: requestId(),
        });
        if (adoptResult(result)) themeChanged(result.appearance);
        render();
      }).catch(() => {});
    });
    actionIcon($("download-mp4"), "Download this clip (MP4)", "download");
    $("cancel-delete").onclick = () => $("delete-dialog").close();
    $("review-deletion").onclick = () => $("delete-dialog").showModal();
    $("save-draft").onclick = () => run(saveDraft).catch(() => renderNote());
    $("submit-note").onclick = () => run(submitNote).catch(() => renderNote());
    $("confirm-delete").onclick = () => run(confirmDelete);
    $("download-mp4").onclick = () => run(async () => {
      const clip = selectedClip();
      if (!clip) throw Error("Select an exact clip first.");
      await transport.download("mp4", { workspace_id: state.workspace_id, clip_id: clip.clip_id });
      message("Original MP4 download started.");
    });
    const noteChanged = () => {
      inputGeneration += 1;
      dirty = true;
      $("draft-state").textContent = "Unsaved draft";
    };
    $("note-text").oninput = noteChanged;
    $("note-time").oninput = noteChanged;
    $("note-range-end").oninput = noteChanged;
    $("player").ontimeupdate = () => {
      if (sectionEnd !== null && $("player").currentTime >= sectionEnd) {
        $("player").pause();
        sectionEnd = null;
        message("Section finished. Replay it or leave a note on this step.");
      }
      updatePlayback();
      const clip = selectedClip();
      if (clip && $("player").currentTime) transport.call("playback", {
        workspace_id: state.workspace_id, clip_id: clip.clip_id, position_seconds: $("player").currentTime,
      }).catch(() => {});
    };
    $("player").onerror = () => {
      $("media-error").hidden = false;
      $("media-error").textContent = "The selected MP4 could not be decoded or read.";
    };
    const queryTheme = matchMedia("(prefers-color-scheme: dark)");
    queryTheme.addEventListener?.("change", () => themeChanged(state?.appearance || "system"));
    transport.onHostContext?.((ctx) => {
      // Host updates are partial; the transport supplies its merged context.
      themeChanged(state?.appearance || "system", ctx || {});
    });
  }
  async function boot() {
    transport = await window.__SHOWRUN_REVIEW_READY__;
    hostContext = {...(transport.hostContext?.() || {})};
    bind();
    await refresh();
    await restoreIntents();
    themeChanged(state.appearance || "system", hostContext);
  }
  window.mountShowrunReview = boot;
  if (window.__SHOWRUN_REVIEW_READY__) boot().catch((error) => message(errorText(error), true));
})();