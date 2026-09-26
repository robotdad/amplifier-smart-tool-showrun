"""Single isolated Chromium page; observation-bound, explicitly scoped UI actions."""

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
         input_values:[...document.querySelectorAll('input,textarea,select')].filter(visible).map(el=>el.value),
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
        self.ambiguous = set()
        self.observed_state = None
        self.documents = []
        self.comment = None
        self.auth_state = None
        self.auth_check = None

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
            read = self.stories and request.method == "POST" and parsed.path.startswith("/api/") \
                and parsed.path[5:] in STORIES_READS
            if (allowed and self.comment and request.method == "POST"
                    and parsed.path in {"/api/save-draft", "/api/add-comment"}):
                try:
                    require(not parsed.query and request.frame == self.page.main_frame
                            and request.redirected_from is None,
                            "Comment writes require the bound dashboard frame.", "comment_scope")
                    self.comment.authorize(parsed.path[5:], request.post_data_json)
                    read = True
                except (ShowrunError, ValueError, TypeError):
                    self.fail("comment_scope", "Comment request exceeded exact UI authority.")
                    read = False
            allowed = allowed and read
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
            **({"storage_state": self.auth_state} if self.auth_state is not None else {}),
        )
        if self.auth_state is not None:
            from .auth import secret_values, signed_in

            self.secrets.extend(secret_values(self.auth_state))
            # Before capture: an expired or rejected session must never film a login page.
            status, accepted = await signed_in(self.context, url)
            self.auth_check = {"status": status, "accepted": accepted}
            if not accepted:
                raise ShowrunError("auth_expired", "The target did not accept the saved sign-in; nothing was recorded.",
                                   "Run `showrun auth prepare <profile> --url <entry URL> --replace`, sign in again, "
                                   "then retake with a new request_id.")
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

    async def _settled_snapshot(self, attempts=10):
        """Re-read a page that re-renders mid-observation; never surface a raw driver error.

        Apps commonly replace DOM subtrees right after a click (for example a library
        re-render). Handles collected earlier in the same read then detach. The whole
        snapshot is retried so observations stay internally consistent.
        """
        from playwright.async_api import Error as DriverError

        try:
            from playwright._impl._errors import TargetClosedError
        except ImportError:  # pragma: no cover - older drivers
            TargetClosedError = ()
        for attempt in range(attempts):
            try:
                return await self._snapshot()
            except ShowrunError:
                raise
            except TargetClosedError:
                raise
            except DriverError:
                if attempt == attempts - 1:
                    raise ShowrunError('observation_unstable', 'The page kept changing while it was being observed.',
                                       'Let the target settle (avoid continuous re-rendering during the step) '
                                       'and retake with a new request_id.') from None
                await asyncio.sleep(.1)

    async def observe(self):
        self.generation += 1
        result, refs, frames, ambiguous = await self._settled_snapshot()
        self.refs, self.frame_list, self.ambiguous = refs, frames, ambiguous
        self.documents = [await frame.query_selector("body") for frame in frames]
        self.observed_urls = [frame.url for frame in frames]  # private: may contain access fragments
        self.observed_state = result
        return result

    async def _snapshot(self):
        """Read without superseding the decision's references or frame identities."""
        self.check()
        refs = {}
        frames = await self.frames()
        labels = {}
        result = {"generation": self.generation, "frames": []}
        if self.target.config["kind"] == "stories":
            require(await self.page.locator("#versions").input_value() == self.target.revision,
                    "Selected revision left the authorized target.", "target_scope")
            hidden = await self.page.locator("body").evaluate("el=>el.classList.contains('review-hidden')")
            panel = await self.page.locator("#overall").is_visible()
            result["review_panel"] = not hidden and panel
            result["review_panel_hidden"] = hidden and not panel and not await self.page.locator("#composer").is_visible()
        for i, frame in enumerate(frames):
            data = await frame.evaluate(OBSERVE)
            serialized = json.dumps(data)
            if data["sensitive"] or any(s and s in serialized for s in self.secrets):
                self.fail("sensitive_surface", "Sensitive access content appeared; handoff is restricted.", True)
                self.check()
            row = {"frame": i, "text": data["text"], "headings": data["headings"], "controls": [],
                   "visible_controls": []}
            for handle in await frame.query_selector_all("button,[role=button],a"):
                if not await handle.evaluate(VISIBLE):
                    continue
                label = (await handle.get_attribute("aria-label") or await handle.inner_text()).strip()
                row["visible_controls"].append({"label": label, "enabled": await handle.is_enabled()})
                if not await handle.is_enabled():
                    continue
                comment_kind = None
                if self.comment and frame == self.page.main_frame:
                    element_id = await handle.get_attribute("id")
                    if element_id == "overall" and label == "Comment on story" and not self.comment.opened:
                        comment_kind = "open_comment"
                    elif element_id == "send" and label == "Send" and self.comment.filled and not self.comment.submitting:
                        comment_kind = "submit_comment"
                if not NAVIGATION.fullmatch(label) and not comment_kind:
                    continue
                # Anchor navigation is limited to fragments; no arbitrary GET side effects.
                href = await handle.get_attribute("href")
                if href and not href.startswith("#"):
                    continue
                if await handle.get_attribute("type") == "submit" or await handle.evaluate("el=>!!el.form"):
                    continue
                ref = f"g{self.generation}.f{i}.e{len(refs)}"
                refs[ref] = (handle, label)
                labels[label.casefold()] = labels.get(label.casefold(), 0) + 1
                row["controls"].append({"ref": ref, "label": label, **({"capability": comment_kind} if comment_kind else {})})
            if self.comment and frame == self.page.main_frame:
                handle = await frame.query_selector("#comment")
                if handle and await handle.evaluate(VISIBLE) and await handle.is_enabled() and self.comment.opened:
                    value = await handle.input_value()
                    require(value in {"", self.comment.grant["text"]},
                            "Unexpected input content; no disclosure or overwrite authorized.", "comment_scope")
                    if not self.comment.filled:
                        ref = f"g{self.generation}.f{i}.e{len(refs)}"
                        refs[ref] = (handle, "Comment")
                        row["controls"].append({"ref": ref, "label": "Comment", "capability": "fill_comment",
                                                "allowed_text": self.comment.grant["text"]})
            result["frames"].append(row)
        # Labels and explicitly disclosed allowed input text get the same secret
        # check as page text. Arbitrary input values are never exposed.
        if any(s and s in json.dumps(result) for s in self.secrets):
            self.fail("sensitive_surface", "Sensitive control content appeared; handoff is restricted.", True)
        self.check()
        ambiguous = {label for label, count in labels.items() if count > 1}
        result["ambiguous_labels"] = sorted(ambiguous)
        return result, refs, frames, ambiguous

    async def validate_observation(self, generation):
        require(self.observed_state is not None and generation == self.generation,
                "Decision observation was superseded; reobserve.", "stale_ref")
        try:
            current, refs, frames, ambiguous = await self._snapshot()
            require(not ambiguous, "Duplicate navigation labels are unresolved; use distinct accessible labels.",
                    "ambiguous_navigation")
            require(frames == self.frame_list and [frame.url for frame in frames] == self.observed_urls
                    and current == self.observed_state,
                    "Observed page or frames changed during decision; reobserve.", "stale_ref")
            for document in self.documents:
                require(document is not None and await document.evaluate("el=>el===document.body"),
                        "Observed document was replaced; reobserve.", "stale_ref")
            for ref, (handle, _) in self.refs.items():
                require(await handle.evaluate("(el, current)=>el===current && el.isConnected", refs[ref][0]),
                        "Observed navigation control was replaced; reobserve.", "stale_ref")
        except ShowrunError:
            raise
        except Exception:
            raise ShowrunError("stale_ref", "Observed document detached; reobserve.") from None

    @staticmethod
    def visible(observation, expected):
        # Whitespace folding is mechanical, not a model declaration of success.
        expected = " ".join(expected.split())
        return any(expected in " ".join(f["text"].split()) for f in observation["frames"])

    async def matches(self, observation, step):
        if "visible_text" in step and not self.visible(observation, step["visible_text"]):
            return False
        for assertion in step.get("assertions", []):
            if assertion["kind"] == "control":
                found = any(c["label"] == assertion["label"] for f in observation["frames"]
                            for c in f.get("visible_controls", []))
                if found != assertion["visible"]:
                    return False
            elif assertion["kind"] == "review_panel":
                if not observation.get("review_panel" if assertion["visible"] else "review_panel_hidden"):
                    return False
            elif assertion["kind"] == "retained_comment":
                if (not self.comment or not self.visible(observation, self.comment.grant["text"])
                        or not await self.comment.verify()):
                    return False
        return True

    def capability(self, ref):
        return next((c.get("capability") for f in self.observed_state["frames"]
                     for c in f["controls"] if c["ref"] == ref), None)

    async def validate_comment_control(self, action):
        ref = action.get("ref")
        require(ref in self.refs and self.comment, "No observed comment authority.", "comment_scope")
        capability = self.capability(ref)
        handle, _ = self.refs[ref]
        expected_id = {"open_comment": "overall", "fill_comment": "comment", "submit_comment": "send"}.get(capability)
        require(expected_id and await handle.get_attribute("id") == expected_id
                and await handle.owner_frame() == self.page.main_frame
                and await handle.evaluate(VISIBLE) and await handle.is_enabled(),
                "Comment control left its observed scope.", "comment_scope")
        if capability == "fill_comment":
            obj(action, {"action", "ref", "text"}, {"action", "ref", "text"})
            require(action["action"] == "fill" and action["text"] == self.comment.grant["text"]
                    and self.comment.opened and not self.comment.filled,
                    "Only exact granted text may be filled once.", "comment_scope")
        else:
            obj(action, {"action", "ref"}, {"action", "ref"})
            require(action["action"] == "click", "Comment buttons require click.", "comment_scope")
        if capability in {"fill_comment", "submit_comment"}:
            require(await self.page.locator("#composer").get_attribute("data-anchor") == '{"kind":"story"}',
                    "Comment anchor is not the whole authorized story.", "comment_scope")
        if capability == "submit_comment":
            require(self.comment.filled and not self.comment.submitting
                    and await self.page.locator("#comment").input_value() == self.comment.grant["text"],
                    "Submission text changed or was already attempted.", "comment_scope")
        return capability

    @staticmethod
    def evidence(observation):
        return {"method": "visible DOM text, viewport and center-point occlusion checks",
                "sha256": hashlib.sha256(json.dumps(observation, sort_keys=True).encode()).hexdigest(),
                "generation": observation["generation"],
                "limits": "DOM observation is not pixel-level readability or backend correctness proof."}

    async def validate_action(self, action, generation=None):
        self.check()
        name = action.get("action")
        if name in {"click", "key", "fill"}:
            await self.validate_observation(self.generation if generation is None else generation)
        if name in {"click", "fill"} and self.comment and self.capability(action.get("ref")):
            return await self.validate_comment_control(action)
        if name == "click":
            obj(action, {"action", "ref"}, {"action", "ref"})
            require(action["ref"] in self.refs, "Stale or unknown element ref; reobserve.", "stale_ref")
            handle, label = self.refs[action["ref"]]
            # Labels are all the current observation can disambiguate. A model
            # choosing the first duplicate ref does not resolve the ambiguity.
            require(label.casefold() not in self.ambiguous,
                    "Duplicate navigation labels are unresolved; give controls distinct accessible labels.",
                    "ambiguous_navigation")
            try:
                current = (await handle.get_attribute("aria-label") or await handle.inner_text()).strip()
                require(current == label and await handle.evaluate("el=>el.isConnected")
                        and await handle.evaluate(VISIBLE) and await handle.is_enabled(),
                        "Navigation control changed; reobserve.", "stale_ref")
                href = await handle.get_attribute("href")
                require((not href or href.startswith("#")) and await handle.get_attribute("type") != "submit"
                        and not await handle.evaluate("el=>!!el.form"),
                        "Navigation control authority changed; reobserve.", "stale_ref")
                matches = 0
                for frame in await self.frames():
                    for candidate in await frame.query_selector_all("button,[role=button],a"):
                        candidate_label = (await candidate.get_attribute("aria-label")
                                           or await candidate.inner_text()).strip()
                        if candidate_label.casefold() == label.casefold() and await candidate.evaluate(VISIBLE):
                            matches += 1
                require(matches == 1, "Duplicate navigation labels appeared; use distinct accessible labels.",
                        "ambiguous_navigation")
            except ShowrunError:
                raise
            except Exception:
                raise ShowrunError("stale_ref", "Navigation reference detached; reobserve.") from None
        elif name == "key":
            require(not self.ambiguous, "Duplicate navigation labels make key navigation ambiguous.",
                    "ambiguous_navigation")
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

    async def act(self, action, before_dispatch=None, generation=None):
        name = await self.validate_action(action, generation)
        # No awaited precondition remains after this point. The synchronous
        # callback durably reserves an attempt BEFORE any Playwright dispatch.
        # Failures before it are known not-dispatched; after it remain uncertain.
        if before_dispatch:
            before_dispatch(name)
        if name in {"open_comment", "fill_comment", "submit_comment"}:
            self.comment.dispatch(name)
        if name in {"click", "open_comment", "submit_comment"}:
            await self.refs[action["ref"]][0].click(timeout=3000)
        elif name == "fill_comment":
            await self.refs[action["ref"]][0].fill(action["text"], timeout=3000)
        elif name == "key":
            # Body focus is not arbitrary model code or a content edit.
            await self.documents[action["frame"]].press(action["key"])
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