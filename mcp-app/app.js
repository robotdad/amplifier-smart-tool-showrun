import { App } from "@modelcontextprotocol/ext-apps";

const app = new App({ name: "Showrun capture review", version: "0.1.0" }, {});
let hostContext = {};
const hostListeners = new Set();

function mergeContext(delta) {
  if (!delta || typeof delta !== "object") return;
  const sdkContext = app.getHostContext?.() || {};
  hostContext = { ...hostContext, ...sdkContext, ...delta };
  for (const listener of hostListeners) listener({ ...hostContext });
}

app.onhostcontextchanged = (delta) => mergeContext(delta);

function unwrap(result) {
  if (result?.isError) {
    const content = result.content?.find((item) => item.type === "text");
    const raw = content?.text || "";
    let detail = {};
    try {
      detail = JSON.parse(raw)?.error || {};
    } catch (_) {
      const start = raw.indexOf("{");
      if (start >= 0) {
        try { detail = JSON.parse(raw.slice(start))?.error || {}; } catch (_) {}
      }
    }
    const message = detail.message || raw || "The Showrun review operation failed.";
    const failure = Error(message);
    Object.assign(failure, {code: detail.code, remedy: detail.remedy, error: detail});
    throw failure;
  }
  const value = result?.structuredContent || result;
  return value?.result ?? value;
}

async function call(operation, arguments_ = {}) {
  return unwrap(await app.callServerTool({
    name: {
      workspace: "showrun_review",
      list_demos: "showrun_list_demos",
      select_clip: "showrun_select_clip",
      playback: "showrun_set_playback",
      appearance: "showrun_set_appearance",
      rename: "showrun_rename",
      prepare_delete: "showrun_prepare_delete",
      delete: "showrun_delete",
      save_draft: "showrun_save_note_draft",
      submit_note: "showrun_submit_note",
      describe_media: "showrun_describe_media",
      describe_zip: "showrun_describe_zip",
      download_zip: "showrun_download_zip",
    }[operation] || `showrun_${operation}`,
    arguments: arguments_,
  }));
}

async function bytesFrom(info) {
  if (!Number.isSafeInteger(info.bytes) || info.bytes < 0 || info.bytes > 256 * 1024 * 1024)
    throw Error("Retained transfer is outside the bounded AppBridge buffer.");
  const chunk = info.chunk_bytes || 524288;
  const parts = [];
  for (let offset = 0; offset < info.bytes; offset += chunk) {
    const uri = info.resource_uri.replace(/\/\d+$/, `/${offset}`);
    const loaded = await app.readServerResource({ uri });
    const content = loaded.contents?.find((item) => item.uri === uri);
    if (!content?.blob) throw Error("The MCP host did not return retained binary content.");
    const raw = atob(content.blob);
    const bytes = Uint8Array.from(raw, (char) => char.charCodeAt(0));
    if (bytes.length !== Math.min(chunk, info.bytes - offset))
      throw Error("The MCP host returned an incomplete bounded chunk.");
    parts.push(bytes);
  }
  return new Blob(parts, { type: info.mime_type || "application/octet-stream" });
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const ready = (async () => {
  await app.connect();
  hostContext = { ...hostContext, ...(app.getHostContext?.() || {}) };
  return {
    call,
    mediaUrl: async (clip) => {
      const info = await call("describe_media", {
        workspace_id: clip.workspace_id,
        clip_id: clip.clip_id,
      });
      return URL.createObjectURL(await bytesFrom(info));
    },
    download: async (kind, arguments_) => {
      if (kind === "mp4") {
        const info = await call("describe_media", arguments_);
        saveBlob(await bytesFrom(info), `${arguments_.clip_id}.mp4`);
        return info;
      }
      const info = await call("download_zip", arguments_);
      saveBlob(await bytesFrom(info), `${arguments_.demo_id}.zip`);
      return info;
    },
    onHostContext: (listener) => {
      hostListeners.add(listener);
      listener({ ...hostContext });
    },
    hostContext: () => ({ ...hostContext }),
  };
})();

window.__SHOWRUN_REVIEW_READY__ = ready;