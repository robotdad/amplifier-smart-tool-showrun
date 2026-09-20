import {
  AppBridge,
  PostMessageTransport,
} from "@modelcontextprotocol/ext-apps/app-bridge";

window.mountShowrunReview = async (html, initial, hostContext = { theme: "light", displayMode: "inline" }) => {
  const frame = document.createElement("iframe");
  frame.id = "app";
  frame.sandbox = "allow-scripts allow-downloads";
  frame.style = "width:100%;height:1100px;border:0";
  document.body.replaceChildren(frame);
  const bridge = new AppBridge(
    null,
    { name: "Independent standards host", version: "1.0.0" },
    { serverTools: {}, serverResources: {}, updateModelContext: {} },
    { hostContext },
  );
  bridge.oncalltool = (args) => window.hostCall(args);
  bridge.onreadresource = (args) => window.hostRead(args);
  bridge.onupdatemodelcontext = async (args) => {
    window.savedContext = args;
    return {};
  };
  bridge.oninitialized = async () => {
    if (initial) await bridge.sendToolResult(initial);
  };
  await bridge.connect(new PostMessageTransport(frame.contentWindow, frame.contentWindow));
  frame.srcdoc = html;
  window.showrunBridge = bridge;
  return frame;
};