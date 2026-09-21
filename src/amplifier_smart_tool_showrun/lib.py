"""Public synchronous facade. The caller process owns execution; cancellation is external."""

import asyncio
import copy
import json
import os
import time
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .errors import ShowrunError, require
from .schema import ident, validate, validate_new
from .store import Store

CAPABILITIES = {
    "manifest": ("deterministic", "Describe installed capabilities and prerequisites."),
    "validate": ("deterministic", "Validate request structure without providers or target access."),
    "record": ("model-backed", "Perform an observed UI demo under explicit action and target-session authority."),
    "status": ("deterministic", "Read a retained request; never execute or replay it."),
    "inspect": ("deterministic", "Verify retained artifact hashes and decode delivered media."),
    "cancel": ("deterministic", "Request cooperative cancellation; acknowledgment is not cleanup."),
    "prepare-runtime": ("deterministic", "Explicitly prepare local Agent modules; may download code, no inference."),
    "prepare-desktop": ("deterministic", "Install the pinned native companion release; --build is for developers."),
    "desktop-status": ("deterministic", "Check companion permissions without inspecting target apps."),
    "prepare-fixture": ("deterministic", "Import supplied presentation into a fresh isolated Stories fixture."),
    "review": ("deterministic", "Browse and manage retained demos, clips, notes and downloads without capture or models."),
}


def public_request(request):
    result = copy.deepcopy(request)
    target = result["target"]
    if target["kind"] == "url":
        parsed = urlsplit(target["url"])
        target["url"] = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    elif target["kind"] == "stories":
        target.pop("python")
        target.pop("storage")
    return result


class Showrun:
    def __init__(self, storage=None, model=None):
        self.storage = Path(storage or Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser()
                            / "showrun").expanduser().resolve()
        self.model = copy.deepcopy(model)

    def _store(self, readonly=False):
        return Store(self.storage, readonly=readonly)

    def review_store(self, metadata_root=None, readonly=False, workspace_scopes=None):
        """Return the provider-free retained review store over this configured take root."""
        from .review import ReviewStore

        return ReviewStore(
            self.storage,
            metadata_root=metadata_root,
            readonly=readonly,
            workspace_scopes=workspace_scopes,
        )

    def review_workspace(self, workspace_id="default"):
        """Read versioned retained review state; no target, browser or provider is started."""
        return self.review_store().workspace(workspace_id)

    review = review_workspace

    def review_demos(self, workspace_id="default", offset=0, limit=100):
        return self.review_store().list_demos(workspace_id, offset, limit)

    def review_server(self, host="127.0.0.1", port=0, token=None, workspace_id="default", demo_ids=None):
        """Start the authenticated local review service; capture lifecycle is unaffected."""
        from .review_server import ReviewService

        scopes = {workspace_id: None if demo_ids is None else set(demo_ids)}
        service = ReviewService(
            self.review_store(workspace_scopes=scopes),
            host=host,
            port=port,
            token=token,
            authorized_workspaces=scopes,
        )
        service.start()
        return service

    def validate(self, request):
        """Return effective defaults and validation; no provider, browser, or target startup."""
        effective = validate(request, self.model)
        validate_new(effective)
        return {"status": "valid", "request": public_request(effective)}

    @staticmethod
    def prepare_fixture(presentation, destination, python):
        """Explicit public import into a fresh destination; no browser or model."""
        from .fixture import prepare

        return prepare(presentation, destination, python)

    def status(self, request_id):
        """Read retained status using the same store and request identity."""
        ident(request_id)
        return self._store(readonly=True).status(request_id)

    def cancel(self, request_id):
        """Acknowledge a durable stop request; poll status for terminal cleanup evidence."""
        ident(request_id)
        return self._store().cancel(request_id)

    def inspect(self, request_id):
        """Model-free verification of a retained, unrestricted handoff."""
        from .capture import inspect_media

        result = self.status(request_id)
        if result.get("restricted"):
            return {"request_id": request_id, "status": "restricted",
                    "notice": "Sensitive material is withheld. Owner-directed release/deletion is not automated."}
        if result.get("media"):
            path = self.storage / request_id / result["media"]["path"]
            require(result["media"]["path"] == "capture.mp4" and not path.is_symlink()
                    and path.resolve().parent == (self.storage / request_id).resolve(),
                    "Artifact reference left its retained take.", "artifact_scope")
            require(path.is_file(), "Retained media is missing.", "artifact_missing")
            actual = asyncio.run(inspect_media(path))
            require(actual["sha256"] == result["media"]["sha256"], "Media hash differs from receipt.",
                    "artifact_changed")
            result["inspection"] = actual
        return result

    @staticmethod
    def prepare_runtime():
        """Explicit module installation/setup; never invoked implicitly by record."""
        from .agent import prepare_runtime

        try:
            return asyncio.run(prepare_runtime())
        except ShowrunError:
            raise
        except Exception:
            raise ShowrunError("runtime_prepare_failed", "Agent runtime preparation failed.",
                               "Check installation and setup network access; no model was called.") from None

    @staticmethod
    def prepare_desktop(build=False):
        import sys

        from .desktop import prepare
        from .desktop_install import install
        if sys.platform == 'win32':
            from .windows_desktop import prepare as prepare_windows
            from .windows_install import install as install_windows
            return asyncio.run(prepare_windows() if build else install_windows())
        return asyncio.run(prepare() if build else install())

    @staticmethod
    def desktop_status():
        from .desktop import MacBridge

        async def check():
            import sys
            if sys.platform == 'win32':
                from .windows_desktop import WindowsBridge
                bridge = WindowsBridge()
            else:
                bridge = MacBridge()
            try:
                result = await bridge.start()
                if 'screen_recording' in result:
                    result['ready'] = result['screen_recording'] and result['accessibility']
                result['model_calls'] = 0
                return result
            finally:
                await bridge.close()
        return asyncio.run(check())

    def record(self, request):
        """Synchronous caller-owned execution. Exact retries only inspect; no resumption/replay."""
        effective = validate(request, self.model)
        require(self.model is not None, "Configure an explicit provider and model before recording.", "provider_missing")
        receipt = {
            "schema_version": 1, "request_id": effective["request_id"], "take_id": effective["request_id"],
            "status": "running", "partial": True, "request": public_request(effective),
            "model": self.model, "usage": {"model_calls": 0, "actions": 0},
            "steps": [{"id": s["id"], "status": "unattempted", "requested": s} for s in effective["steps"]],
            "media": None, "restricted": False, "resources": {}, "cleanup": "pending",
            "limitations": [
                ("Generic UI grants authorize target-session effects; narrower backend limits require target enforcement."
                 if effective["authority"].get("ui") else
                 "Legacy navigation or explicitly granted prepared comments; no target model use."),
                            "No audio, native desktop, popup or arbitrary-origin capture.",
                            "Visible DOM checks do not prove human readability; independent video review remains required.",
                            "Synchronous process lifetime. Crashed/uncertain work never resumes automatically."],
        }
        if effective['target']['kind'] in {'macos', 'windows'}:
            receipt['limitations'] = [
                'Prepared native window; explicitly granted control or terminal input. Caller owns the app and its session effects.',
                'Background window screenshots sampled at up to 5 Hz, plus paced-entry character samples; no audio or cursor, and transient states may be missed.',
                'Accessibility checks do not prove persisted state or human readability.',
                'Platform permissions and an interactive desktop are required. This is not an isolated desktop; app effects may affect user focus.',
                'Synchronous execution; uncertain actions are never replayed automatically.']
        if effective['target']['kind'] == 'windows':
            receipt['limitations'].append('Experimental PrintWindow capture depends on app rendering; inspect footage. No elevated apps, secure desktop or separate windows.')
        store = self._store()
        existing = store.reserve(effective, self.model, receipt)
        if existing is not None:
            return store.status(effective["request_id"])
        store.save(receipt)
        return asyncio.run(self._record(effective, receipt, store))

    async def _record(self, request, receipt, store):
        from .agent import Navigator
        from .browser import Browser
        from .capture import preflight, validate_interval
        from .target import Target

        native = request['target']['kind'] in {'macos', 'windows'}
        surface_resource = 'desktop_bridge' if native else 'browser'
        if native:
            from .desktop import Desktop, preflight

            if request['target']['kind'] == 'windows':
                from .windows_desktop import preflight
            Browser = Desktop

        started = time.monotonic()
        deadline = started + request["authority"]["max_seconds"]
        folder = store.directory(request["request_id"])
        navigator = browser = target = comment = None
        current = None

        def persist():
            receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
            store.save(receipt)

        def check():
            if store.cancelled(request["request_id"]):
                raise ShowrunError("cancelled", "Cancellation requested; no further demo actions are permitted.")
            if time.monotonic() >= deadline:
                raise ShowrunError("elapsed_limit", "The authorized elapsed limit was exhausted.")
            if browser:
                browser.check()

        async def perform():
            nonlocal navigator, browser, target, current, comment
            check()
            if request["target"]["kind"] == "stories":
                from .fixture import validate_fixture

                receipt["fixture"] = await validate_fixture(request["target"])
                persist()
            if "stories_comment" in request["authority"]:
                from .comment import Comment

                receipt["comment_effects"] = []
                comment = Comment(request["target"], request["authority"]["stories_comment"],
                                  receipt["comment_effects"], persist)
                await comment.preflight()
            await preflight()
            navigator = Navigator(self.model)  # no launch until configured local runtime is verified
            await navigator.start()
            check()
            target = Target(request["target"], folder)
            receipt["resources"] = target.ownership
            persist()  # pending acquisition is visible before launch
            url = await target.start()
            persist()
            credential_env = self.model.get("credential_env") or {
                "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}[self.model["provider"]]
            browser = Browser(target, folder, request["capture"], [os.environ.get(credential_env, "")])
            browser.ui = request["authority"].get("ui")
            browser.comment = comment
            receipt["resources"][surface_resource] = "acquiring"
            persist()
            await browser.start(url)
            receipt["resources"][surface_resource] = "owned"
            # DOM readiness, not a fixed socket delay.
            readiness_deadline = min(deadline, time.monotonic() + 15)
            observation = await browser.observe()
            while not browser.visible(observation, request["starting_state"]):
                check()
                require(time.monotonic() < readiness_deadline, "Expected starting state is not visible.",
                        "target_not_ready")
                await asyncio.sleep(.1)
                observation = await browser.observe()
            receipt["readiness"] = browser.evidence(observation)
            persist()
            for step, row in zip(request["steps"], receipt["steps"]):
                if request["target"]["kind"] in {"macos", "windows"}:
                    browser.text_entry = step.get("text_entry", "immediate")
                    browser.last_fill = None
                current = row
                row.update(status="in_progress", started_seconds=browser.capture.now(),
                           first_interaction_seconds=None, events=[])
                persist()
                while True:
                    check()
                    observation = await browser.observe()
                    if step.get("wait_for_result", False) and not await browser.matches(observation, step):
                        # Observe long-running target work without inference or UI effects.
                        await asyncio.sleep(.5)
                        continue
                    if await browser.matches(observation, step):
                        observed = browser.capture.now()
                        row["visible_result_seconds"] = observed
                        row["evidence"] = browser.evidence(observation)
                        if comment and comment.confirmed:
                            row["evidence"]["retained_comment"] = comment.confirmed
                        hold_start = time.monotonic()
                        # Extra .16 s covers 25 fps quantization and stated .08 s precision at each edge.
                        while time.monotonic() - hold_start < step["hold_seconds"] + .16:
                            check()
                            await asyncio.sleep(.1)
                            observation = await browser.observe()
                            require(await browser.matches(observation, step),
                                    "Required result did not stay visible through its hold.", "hold_failed")
                        ended = browser.capture.now()
                        row.update(status="completed", ended_seconds=ended,
                                   hold={"start_seconds": observed, "end_seconds": ended,
                                         "requested_seconds": step["hold_seconds"],
                                         "method": getattr(browser, 'hold_method',
                                             "Visible DOM assertion sampled every 100ms; no interaction during hold.")})
                        row["interval"] = {
                            "start_seconds": row["first_interaction_seconds"]
                            if row["first_interaction_seconds"] is not None else observed,
                            "end_seconds": row["ended_seconds"],
                            "start_basis": "first UI interaction" if row["first_interaction_seconds"] is not None
                            else "visible result observation; no UI interaction needed",
                            "precision_seconds": getattr(browser, 'timing_precision', .08),
                        }
                        persist()
                        break
                    require(receipt["usage"]["model_calls"] < request["authority"]["max_model_calls"],
                            "Authorized model call limit exhausted.", "model_limit")
                    receipt["usage"]["model_calls"] += 1
                    thinking = {"kind": "model_deliberation", "start_seconds": browser.capture.now()}
                    row["events"].append(thinking)
                    persist()  # reserve model spend before dispatch
                    action = await navigator.decide(step, observation, request["context"], deadline - time.monotonic())
                    thinking["end_seconds"] = browser.capture.now()
                    check()
                    event = None

                    def dispatch(action_kind):
                        nonlocal event
                        check()
                        require(receipt["usage"]["actions"] < request["authority"]["max_actions"],
                                "Authorized action limit exhausted.", "action_limit")
                        receipt["usage"]["actions"] += 1
                        stamp = browser.capture.now()
                        event = {"kind": "application_wait" if action_kind == "wait" else "interaction",
                                 "action": action, "dispatch_seconds": stamp, "state": "dispatched",
                                 "observation_generation": observation["generation"]}
                        row["events"].append(event)
                        if row["first_interaction_seconds"] is None and action_kind != "wait":
                            row["first_interaction_seconds"] = stamp
                        persist()  # crash after here is uncertain, never replayed

                    try:
                        action_kind = await browser.validate_action(action, observation["generation"])
                        await browser.act(action, before_dispatch=dispatch, generation=observation["generation"])
                    except ShowrunError as exc:
                        if event is not None:
                            raise  # an actual reserved attempt must never be called not_dispatched
                        row["events"].append({"kind": "stale_observation" if exc.code == "stale_ref"
                                              else "action_rejected", "state": "not_dispatched",
                                              "action": {"action": action.get("action"), "details": "withheld"}
                                              if exc.code != "stale_ref" else action, "code": exc.code,
                                              "observed_seconds": browser.capture.now()})
                        persist()
                        if exc.code != "stale_ref":
                            raise
                        continue  # bounded by the same model/elapsed grant, no blind click
                    event.update(state="returned", returned_seconds=browser.capture.now())
                    persist()
                    if action_kind != "wait":
                        # Give asynchronous UI rendering a bounded observation window
                        # before asking for another action. No new inference/spend.
                        wait = {"kind": "application_wait", "start_seconds": browser.capture.now()}
                        row["events"].append(wait)
                        until = min(deadline, time.monotonic() + 2)
                        while time.monotonic() < until:
                            check()
                            observed_after = await browser.observe()
                            if await browser.matches(observed_after, step):
                                break
                            await asyncio.sleep(.1)
                        wait["end_seconds"] = browser.capture.now()
                        persist()
            check()
            receipt["status"] = "succeeded"
            current = None

        task = asyncio.create_task(perform())
        try:
            while not task.done():
                check()
                await asyncio.wait({task}, timeout=.1)
            await task
        except BaseException as exc:
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 3)
            except (TimeoutError, asyncio.CancelledError):
                pass
            error = exc if isinstance(exc, ShowrunError) else ShowrunError(
                "execution_failed", "Execution stopped before all requirements were verified.",
                "Check prerequisites, prepared target and selected provider. No automatic retry was made.")
            receipt["error"] = error.public()
            receipt["status"] = "cancelled" if error.code == "cancelled" else "failed"
            if current:
                uncertain = (any(e.get("state") == "dispatched" for e in current.get("events", []))
                             or bool(comment and comment.submitting and not comment.confirmed))
                current["status"] = "uncertain" if uncertain else "failed"
                current["error"] = error.public()
        finally:
            cleanup_errors = []
            if browser:
                receipt["restricted"] = browser.restricted
                try:
                    media = await asyncio.wait_for(browser.capture.finish(), 40)
                    if not receipt["restricted"]:
                        receipt["media"] = media
                        if media and media.get("timing", {}).get("verified") is False:
                            receipt["limitations"].append(media["timing"]["warning"])
                        if media and media.get('capture_interrupted'):
                            receipt['capture_error'] = media['capture_interrupted']
                            if receipt['status'] == 'succeeded':
                                receipt['status'] = 'failed'
                except Exception:
                    receipt["capture_error"] = "Media finalization/decoding did not verify; no playable artifact advertised."
                    receipt.setdefault("error", ShowrunError(
                        "capture_failed", "Media finalization or decoding did not verify.",
                        "Check FFmpeg and capture prerequisites; retained raw frames are not a verified handoff.").public())
                    receipt["status"] = "failed" if receipt["status"] == "succeeded" else receipt["status"]
                try:
                    await asyncio.wait_for(browser.close(), 16)
                    receipt["resources"][surface_resource] = "verified_closed"
                except Exception:
                    cleanup_errors.append(surface_resource)
                if receipt["restricted"]:
                    restricted = folder / "restricted"
                    restricted.mkdir(mode=0o700, exist_ok=True)
                    for name in ("capture.mp4", "frames", "capture.ffconcat"):
                        path = folder / name
                        if path.exists():
                            path.rename(restricted / name)
                    receipt["media"] = None
                    receipt["notice"] = "Sensitive material restricted; release or deletion requires owner direction."
            if target:
                try:
                    await asyncio.wait_for(target.close(), 18)
                except Exception:
                    cleanup_errors.append("dashboard")
            if navigator:
                try:
                    await asyncio.wait_for(navigator.close(), 5)
                except Exception:
                    cleanup_errors.append("agent")
            receipt["cleanup"] = "verified" if not cleanup_errors else "failed_or_uncertain"
            receipt["cleanup_errors"] = cleanup_errors
            if cleanup_errors and receipt["status"] == "succeeded":
                receipt["status"] = "failed"
                receipt["error"] = ShowrunError(
                    "cleanup_failed", "Owned resource cleanup was not fully verified.",
                    "Inspect cleanup_errors and owned-dashboard.json; do not stop caller-owned services.").public()
            if store.cancelled(request["request_id"]) and receipt["status"] == "succeeded":
                receipt["status"] = "cancelled"
            if receipt["status"] == "succeeded" and (not receipt["media"] or receipt["restricted"]):
                receipt["status"] = "failed"
            if receipt["media"]:
                duration = receipt["media"]["duration_seconds"]
                for row in receipt["steps"]:
                    if row.get("ended_seconds", 0) > duration + .08:
                        receipt.setdefault("timing_warnings", []).append(
                            {"step_id": row["id"], "message": "Step interval exceeds decoded media duration."})
                    if row["status"] == "completed":
                        try:
                            # Duration drift is advisory; internally contradictory
                            # action/hold evidence is still a failed receipt.
                            validate_interval(row, max(duration, row.get('ended_seconds', 0)))
                        except ShowrunError as exc:
                            receipt["status"] = "failed"
                            receipt["capture_error"] = exc.public()
            receipt["partial"] = receipt["status"] != "succeeded"
            persist()
        return receipt

    @staticmethod
    def manifest():
        """Read the canonical installed manifest, without provider imports."""
        import yaml

        _, header, body = files(__package__).joinpath("SMART_TOOL.md").read_text().split("---", 2)
        return {**yaml.safe_load(header), "body": body.strip(), "capabilities": [
            {"name": name, "intelligence": intelligence, "description": description}
            for name, (intelligence, description) in CAPABILITIES.items()
        ]}

    @staticmethod
    def skill(capability=None):
        """Installed operating skill; --help is exactly this library-owned text."""
        require(capability is None or capability in CAPABILITIES, "Unknown capability.")
        if capability:
            guide = json.loads(files(__package__).joinpath("capabilities.json").read_text())[capability]
            intelligence, description = CAPABILITIES[capability]
            arguments = "\n".join(
                f"- `{name}` — {detail}" for name, detail in guide["arguments"].items()
            ) or "No capability-specific arguments."
            body = (
                f"# showrun {capability}\n\n## When to use\n\n{description}\n\n"
                f"## Execution and prerequisites\n\n{intelligence}. {guide['guidance']}\n\n"
                f"## Arguments\n\n{arguments}\n\n"
                "Global options precede the command. CLI request inputs are JSON filenames; "
                "library calls accept dictionaries. Use `-h` for the CLI flag reference.\n\n"
                f"## Example\n\n```sh\n{guide['example']}\n```\n\n"
                f"## Result\n\n{guide['result']}\n\n"
                f"## Failures and recovery\n\n{guide['failures']}\n\n"
                "CLI results and domain errors are JSON on stdout. Exit 0 indicates success; "
                "exit 1 indicates an operation/input failure or failed, cancelled, uncertain or "
                "restricted result; argparse usage errors exit 2.\n\n"
                "## Further guidance\n\nProvider-free installation smoke: `showrun manifest`. "
                "Use `showrun --help` for the full operating guide and capability index. "
                "Read the packaged `SMART_TOOL.md` for detailed request shapes and examples.\n"
            )
        else:
            body = Showrun.manifest()["body"]
            body += "\n\n## Capabilities\nEach has its own skill: `showrun <capability> --help`.\n"
            body += "\n".join(f"- `{n}` [{i}] — {d}" for n, (i, d) in CAPABILITIES.items())
        return (f'<skill_content name="showrun{("-" + capability) if capability else ""}">\n'
                f'Skill directory: {files(__package__)}\n'
                'Repository: https://github.com/robotdad/amplifier-smart-tool-showrun\n'
                'Relative paths are relative to the skill directory.\n\n' + body +
                '\n<skill_resources>\n<file>SMART_TOOL.md</file>\n<file>capabilities.json</file>\n'
                '<file>lib.py</file>\n</skill_resources>\n</skill_content>')
