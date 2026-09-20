import { build } from "esbuild";
import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const out = resolve(root, "src/amplifier_smart_tool_showrun/resources/mcp_bridge.js");
await build({
  entryPoints: [resolve(here, "app.js")],
  outfile: out,
  bundle: true,
  format: "iife",
  platform: "browser",
  target: ["es2020"],
  logLevel: "error",
});
const [template, css, controller, bridge] = await Promise.all([
  readFile(resolve(root, "src/amplifier_smart_tool_showrun/ui/index.html"), "utf8"),
  readFile(resolve(root, "src/amplifier_smart_tool_showrun/ui/review.css"), "utf8"),
  readFile(resolve(root, "src/amplifier_smart_tool_showrun/ui/review.js"), "utf8"),
  readFile(out, "utf8"),
]);
const html = template
  .replace("<!--SHOWRUN_CSS-->", () => `<style>${css}</style>`)
  .replace("<!--SHOWRUN_TRANSPORT-->", () => `<script>${bridge}</script>`)
  .replace("<!--SHOWRUN_SCRIPT-->", () => `<script>${controller}</script>`);
await writeFile(resolve(root, "src/amplifier_smart_tool_showrun/resources/mcp_app.html"), html);