"""Single isolated Chromium page; navigation-only actions over visible DOM."""

import asyncio
import hashlib
import json
import re
from urllib.parse import urlsplit

from .capture import Capture
from .errors import ShowrunError, require
from .schema import obj, origin

# These are action classes, not slide names or a target-specific click sequence.
NAVIGATION = re.compile(r"^(?:(?:next|previous|prev|first|last)(?: slide| page)?|"
                        r"(?:go to )?(?:slide|page) \d+|hide review|show review)$", re.I)
KEYS = {"ArrowRight", "ArrowLeft", "Home", "End", "PageDown", "PageUp"}
STORIES_READS = {"bootstrap", "get-story", "get-preview", "media", "narration-settings",
                 "provider-settings", "get-speaker-notes", "list-narrations", "list-narration-scripts"}

# No DOM mutation. The center point must be within the viewport and not occluded.
VISIBLE = """el => {
 const r=el.getBoundingClientRect(), s=getComputedStyle(el);
 if(r.width<=0||r.height<=0||s.visibility!=='visible'||Number(s.opacity)===0) return false;
 for(let p=el.parentElement;p;p=p.parentElement){
   const ps=getComputedStyle(p);
   if(Number(ps.opacity)===0||ps.visibility!=='visible'||ps.display==='none')return false;
 }
 const x=r.x+r.width/2,y=r.y+r.height/2;
 if(x<0||y<0||x>=innerWidth||y>=innerHeight) return false;
 const top=document.elementFromPoint(x,y);
 return !!top && (el.contains(top)||top.contains(el));
}"""
OBSERVE = """() => {
 const visible = """ + VISIBLE + """;
 const text=[], headings=[];
 const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
 let n;
 while(n=walker.nextNode()){
   if(!n.textContent.trim()||!n.parentElement||!visible(n.parentElement)) continue;
   if(n.parentElement.closest('script,style,textarea,input,[hidden],[aria-hidden="true"]')) continue;
   const range=document.createRange();range.selectNodeContents(n);
   const rects=[...range.getClientRects()];
   const ink=getComputedStyle(n.parentElement).color;
   if(ink==='transparent'||/rgba\\([^)]*,\\s*0\\)/.test(ink))continue;
   if(!rects.length||!rects.every(r=>{
     if(r.width<=0||r.height<=0||r.top<0||r.left<0||r.bottom>innerHeight||r.right>innerWidth)return false;
     const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
     return !!hit&&(n.parentElement.contains(hit)||hit.contains(n.parentElement));
   }))continue;
   text.push(n.textContent.trim());
   if(n.parentElement.closest('h1,h2,h3,[role="heading"]'))headings.push(n.textContent.trim());
 }
 return {text:text.join('\\n').slice(0,22000),headings:headings.slice(0,40),
         sensitive:!!document.querySelector('input[type="password"]')};
}"""


class Browser:
    def __init__(self, target, folder, geometry, secrets=()):
        self.target, self.folder, self.geometry = target, folder, geometry
        self.secrets = [s for s in secrets if s]
        self.pw = self.browser = self.context = self.page = None
        self.capture = Capture(folder, geometry)
        self.fault = None
        self.restricted = False
        self.refs = {}
        self.generation = 0
        self.frame_list = []

    def fail(self, code, message, restricted=False):
        if not self.fault:
            self.fault = ShowrunError(code, message)
        self.restricted = self.restricted or restricted

    def check(self):
        if self.fault:
            raise self.fault
        if self.capture.error:
            raise self.capture.error

    async def route(self, route):
        request = route.request
        parsed = urlsplit(request.url)
        allowed = parsed.scheme in {"http", "https"} and origin(request.url) == self.allowed_origin
        if request.method not in {"GET", "HEAD"}:
            allowed = allowed and self.stories and request.method == "POST" and parsed.path.startswith("/api/") \
                and parsed.path[5:] in STORIES_READS
        if not allowed:
            self.fail("network_scope", "Target attempted an unapproved origin or non-read request.")
            await route.abort()
        else:
            await route.continue_()

    async def start(self, url):
        from playwright.async_api import async_playwright

        self.allowed_origin = origin(url)
        self.stories = self.target.config["kind"] == "stories" or bool(self.target.revision)
        self.secrets.append(urlsplit(url).fragment)
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True)
        self.context = await self.browser.new_context(
            viewport=self.geometry, device_scale_factor=1, accept_downloads=False,
            permissions=[], service_workers="block",
        )
        await self.context.route("**/*", self.route)
        await self.context.route_web_socket("**/*", lambda socket: socket.close())
        self.page = await self.context.new_page()
        self.context.on("page", lambda _: self.fail("popup_unsupported", "New pages are unsupported.", True))
        self.page.on("download", lambda _: self.fail("download_unsupported", "Downloads are not authorized."))
        self.page.on("filechooser", lambda _: self.fail("upload_unsupported", "Uploads are not authorized."))
        self.page.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))
        self.page.set_default_timeout(3000)
        await self.capture.start(self.context, self.page)
        await self.page.goto(url, wait_until="domcontentloaded", timeout=15000)
        if self.target.revision:
            # Real rendered readiness: exact version selected plus srcdoc preview.
            await self.page.wait_for_function(
                "revision => document.querySelector('#versions')?.value === revision && "
                "!!document.querySelector('iframe')?.srcdoc", arg=self.target.revision, timeout=15000)
        self.target.ownership["startup"] = "ready"
        self.check()

    async def frames(self):
        require(origin(self.page.url) == self.allowed_origin, "Page left its authorized origin.", "target_scope")
        result = []
        for frame in self.page.frames:
            if frame == self.page.main_frame:
                result.append(frame)
                continue
            if frame.url == "about:srcdoc":
                element = await frame.frame_element()
                sandbox = await element.get_attribute("sandbox")
                require(sandbox is not None and "allow-same-origin" not in sandbox,
                        "Only opaque sandboxed srcdoc frames are supported.", "target_scope")
                if not await element.evaluate(VISIBLE):
                    continue
                result.append(frame)
            elif frame.url != "about:blank":
                require(origin(frame.url) == self.allowed_origin, "An unauthorized frame appeared.", "target_scope")
                element = await frame.frame_element()
                if await element.evaluate(VISIBLE):
                    result.append(frame)
        return result

    async def observe(self):
        self.check()
        self.generation += 1
        self.refs = {}
        self.frame_list = await self.frames()
        result = {"generation": self.generation, "frames": []}
        for i, frame in enumerate(self.frame_list):
            data = await frame.evaluate(OBSERVE)
            serialized = json.dumps(data)
            if data["sensitive"] or any(s and s in serialized for s in self.secrets):
                self.fail("sensitive_surface", "Sensitive access content appeared; handoff is restricted.", True)
                self.check()
            row = {"frame": i, "text": data["text"], "headings": data["headings"], "controls": []}
            for handle in await frame.query_selector_all("button,[role=button],a"):
                if not await handle.evaluate(VISIBLE) or not await handle.is_enabled():
                    continue
                label = (await handle.get_attribute("aria-label") or await handle.inner_text()).strip()
                if not NAVIGATION.fullmatch(label):
                    continue
                # Anchor navigation is limited to fragments; no arbitrary GET side effects.
                href = await handle.get_attribute("href")
                if href and not href.startswith("#"):
                    continue
                if await handle.get_attribute("type") == "submit" or await handle.evaluate("el=>!!el.form"):
                    continue
                ref = f"g{self.generation}.f{i}.e{len(self.refs)}"
                self.refs[ref] = (handle, label)
                row["controls"].append({"ref": ref, "label": label})
            result["frames"].append(row)
        self.check()
        return result

    @staticmethod
    def visible(observation, expected):
        # Whitespace folding is mechanical, not a model declaration of success.
        expected = " ".join(expected.split())
        return any(expected in " ".join(f["text"].split()) for f in observation["frames"])

    @staticmethod
    def evidence(observation):
        return {"method": "visible DOM text, viewport and center-point occlusion checks",
                "sha256": hashlib.sha256(json.dumps(observation, sort_keys=True).encode()).hexdigest(),
                "generation": observation["generation"],
                "limits": "DOM observation is not pixel-level readability or backend correctness proof."}

    async def validate_action(self, action):
        self.check()
        name = action.get("action")
        if name == "click":
            obj(action, {"action", "ref"}, {"action", "ref"})
            require(action["ref"] in self.refs, "Stale or unknown element ref.", "invalid_action")
            handle, label = self.refs[action["ref"]]
            current = (await handle.get_attribute("aria-label") or await handle.inner_text()).strip()
            require(current == label and await handle.evaluate(VISIBLE), "Navigation control changed.", "stale_ref")
        elif name == "key":
            obj(action, {"action", "frame", "key"}, {"action", "frame", "key"})
            require(type(action["frame"]) is int and 0 <= action["frame"] < len(self.frame_list)
                    and action["key"] in KEYS, "Unsupported navigation key/frame.", "invalid_action")
        elif name == "wait":
            obj(action, {"action"}, {"action"})
        elif name == "fail":
            raise ShowrunError("destination_not_found", "The requested destination could not be established.",
                               "Check the requested visible_text and prepared target; use a new request_id.")
        else:
            raise ShowrunError("invalid_action", "The model proposed an unsupported action.")
        return name

    async def act(self, action):
        name = await self.validate_action(action)
        if name == "click":
            await self.refs[action["ref"]][0].click(timeout=3000)
        elif name == "key":
            # Body focus is not arbitrary model code or a content edit.
            await self.frame_list[action["frame"]].locator("body").press(action["key"])
        elif name == "wait":
            await asyncio.sleep(.25)
        self.check()

    async def close(self):
        # Every acquired resource gets an independent cleanup attempt.
        failures = []
        for resource in (self.context, self.browser, self.pw):
            if resource:
                try:
                    await asyncio.wait_for(resource.stop() if resource is self.pw else resource.close(), 5)
                except Exception:
                    failures.append(type(resource).__name__)
        if failures:
            raise ShowrunError("cleanup_failed", "One or more owned browser resources did not confirm closure.")