"""Shared retained-review UI asset assembly.

The HTML structure, CSS and controller are one checked-in implementation.  A
transport bootstrap is the only surface-specific part: the local dashboard
uses authenticated HTTP and the MCP App uses the official AppBridge.
"""

from importlib.resources import files


def _asset(name: str) -> str:
    return files("amplifier_smart_tool_showrun").joinpath("ui", name).read_text(encoding="utf-8")


def render_html(transport_script: str) -> str:
    html = _asset("index.html")
    return (
        html.replace("<!--SHOWRUN_CSS-->", f"<style>{_asset('review.css')}</style>")
        .replace("<!--SHOWRUN_TRANSPORT-->", f"<script>{transport_script}</script>")
        .replace("<!--SHOWRUN_SCRIPT-->", f"<script>{_asset('review.js')}</script>")
    )


def standalone_html() -> str:
    return render_html(
        """
        const reviewCsrf = () => {
          const item = document.cookie.split(";").map((part) => part.trim())
            .find((part) => part.startsWith("showrun_review_csrf="));
          return item ? decodeURIComponent(item.slice("showrun_review_csrf=".length)) : "";
        };
        const reviewHeaders = () => {
          const csrf = reviewCsrf();
          return csrf ? {"Content-Type": "application/json", "X-Showrun-CSRF": csrf}
            : {"Content-Type": "application/json"};
        };
        window.__SHOWRUN_REVIEW_READY__ = Promise.resolve({
          call: (operation, arguments) => fetch("/api/call", {
            method: "POST", credentials: "same-origin",
            headers: reviewHeaders(),
            body: JSON.stringify({operation, arguments})
          }).then(async (response) => {
            const value = await response.json();
            if (!response.ok || value.error) {
              const error = value.error || {};
              const failure = Error(error.message || value.error || "Review request failed.");
              Object.assign(failure, {code: error.code, remedy: error.remedy, error});
              throw failure;
            }
            return value;
          }),
          mediaUrl: async (info) => "/media/" + encodeURIComponent(info.workspace_id) + "/" +
            encodeURIComponent(info.clip_id),
          downloadBlob: async (url, fallbackName) => {
            const response = await fetch(url, {credentials: "same-origin"});
            if (!response.ok) {
              let value = {};
              try { value = await response.json(); } catch (_) {}
              const error = value.error || {};
              const failure = Error(error.message || `Download failed (${response.status}).`);
              Object.assign(failure, {code: error.code, remedy: error.remedy, error});
              throw failure;
            }
            const maxBytes = 256 * 1024 * 1024;
            const declared = Number(response.headers.get("Content-Length") || 0);
            if (Number.isFinite(declared) && declared > maxBytes)
              throw Error("The retained download exceeds the bounded review transfer limit.");
            const blob = await response.blob();
            if (blob.size > maxBytes)
              throw Error("The retained download exceeds the bounded review transfer limit.");
            const disposition = response.headers.get("Content-Disposition") || "";
            const match = disposition.match(/filename="?([^"]+)"?/i);
            const name = match?.[1] || fallbackName;
            const objectUrl = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = objectUrl;
            link.download = name;
            link.hidden = true;
            document.body.append(link);
            link.click();
            setTimeout(() => { URL.revokeObjectURL(objectUrl); link.remove(); }, 0);
            return {status: "download_started", filename: name, bytes: blob.size};
          },
          download: async (kind, arguments) => {
            const query = new URLSearchParams(arguments);
            if (kind === "zip") {
              const metadata = await fetch("/api/call", {
                method: "POST", credentials: "same-origin",
                headers: reviewHeaders(),
                body: JSON.stringify({operation: "prepare_zip", arguments})
              }).then(async (response) => {
                const value = await response.json();
                if (!response.ok || value.error) {
                  const error = value.error || {};
                  const failure = Error(error.message || "ZIP inventory could not be prepared.");
                  Object.assign(failure, {code: error.code, remedy: error.remedy, error});
                  throw failure;
                }
                return value;
              });
              await (await window.__SHOWRUN_REVIEW_READY__).downloadBlob(
                "/download/zip?" + query.toString()
                  + "&transfer_id=" + encodeURIComponent(metadata.transfer_id),
                `${arguments.demo_id}.zip`,
              );
              return metadata;
            }
            const result = await (await window.__SHOWRUN_REVIEW_READY__).downloadBlob(
              "/download/" + kind + "?" + query.toString(),
              `${arguments.clip_id || "showrun"}.${kind === "mp4" ? "mp4" : "bin"}`,
            );
            return {...result, kind};
          },
          hostContext: () => ({
            theme: window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
          }),
          onHostContext: (callback) => {
            callback({
              theme: window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
            });
            window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () =>
              callback({theme: window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"}));
          }
        });
        """
    )


def mcp_html() -> str:
    # The MCP bundle replaces the transport promise before this controller is
    # evaluated. It is intentionally the same UI document as standalone_html()
    # after the transport bootstrap.
    return render_html(
        """
        window.__SHOWRUN_REVIEW_READY__ = window.__SHOWRUN_REVIEW_READY__ ||
          Promise.reject(Error("MCP AppBridge transport was not installed."));
        """
    )
