"""Exact-capability and installed-dashboard tests; no provider calls."""

import asyncio
import copy
from types import SimpleNamespace

import pytest
from test_showrun import MODEL, request

from amplifier_smart_tool_showrun import Showrun, ShowrunError
from amplifier_smart_tool_showrun.browser import Browser
from amplifier_smart_tool_showrun.comment import Comment
from amplifier_smart_tool_showrun.fixture import helper, validate_fixture
from amplifier_smart_tool_showrun.schema import validate
from amplifier_smart_tool_showrun.target import Target

pytest_plugins = ["test_hardening"]

TEXT = "Showrun demo check: review panel restored after navigating three slides."
TITLES = ["Prepared deck", "First advance", "Second advance",
          "Let it run out of sight. Review the finished take."]
PRESENTATION = {
    "title": TITLES[0],
    "html": '<html><head><style>body{margin:0} .slide{padding:100px}</style></head><body>'
            + "".join(f'<section class="slide"><h1>{t}</h1></section>' for t in TITLES)
            + "</body></html>",
}


def comment_request(target):
    value = request(identity="review-comment")
    value.update(target=target, steps=[
        {"id": "hidden", "instruction": "Hide the review surface, not the presentation.",
         "assertions": [{"kind": "control", "label": "Show review", "visible": True},
                        {"kind": "review_panel", "visible": False}]},
        *[{"id": f"advance-{i}", "instruction": "Advance exactly one slide with review hidden.",
           "visible_text": title, "assertions": [{"kind": "review_panel", "visible": False}]}
          for i, title in enumerate(TITLES[1:], 1)],
        {"id": "restored", "instruction": "Restore the review surface.",
         "assertions": [{"kind": "control", "label": "Hide review", "visible": True},
                        {"kind": "review_panel", "visible": True}]},
        {"id": "comment", "instruction": "Open Comment on story, enter the exact authorized comment, "
                                       "then Send it. A saved draft is not completion.",
         "assertions": [{"kind": "retained_comment"}]},
    ])
    value["authority"].update(navigation_only=False, max_seconds=180, max_model_calls=12, max_actions=30,
                              stories_comment={"story_id": target["story_id"],
                                               "revision_id": target["revision_id"], "text": TEXT})
    return value


class ReviewNavigator:
    """Offline stand-in chooses actual observation refs, never invokes an API."""
    calls = 0

    def __init__(self, config):
        pass

    async def start(self):
        pass

    async def close(self):
        pass

    async def decide(self, step, observation, context, remaining):
        type(self).calls += 1
        controls = [c for f in observation["frames"] for c in f["controls"]]
        if step["id"] == "hidden":
            label = "Hide review"
        elif step["id"] == "restored":
            label = "Show review"
        elif step["id"].startswith("advance"):
            label = "Next slide"
        else:
            for capability in ("open_comment", "fill_comment", "submit_comment"):
                candidates = [c for c in controls if c.get("capability") == capability]
                if candidates:
                    c = candidates[0]
                    if capability == "fill_comment":
                        return {"action": "fill", "ref": c["ref"], "text": c["allowed_text"]}
                    return {"action": "click", "ref": c["ref"]}
            return {"action": "wait"}
        return {"action": "click", "ref": next(c["ref"] for c in controls if c["label"] == label)}


@pytest.mark.parametrize("lose_response", [False, True])
def test_installed_stories_real_ui_comment_and_exact_retry(tmp_path, stories_python, monkeypatch, lose_response):
    from amplifier_smart_tool_showrun import agent
    monkeypatch.setattr(agent, "Navigator", ReviewNavigator)
    if lose_response:
        original = Browser.act

        async def interrupt(self, action, **kwargs):
            submitting = action.get("ref") and self.capability(action["ref"]) == "submit_comment"
            await original(self, action, **kwargs)
            if submitting:
                # Observe actual UI completion but simulate losing the caller
                # acknowledgment before the take can verify/mark its attempt.
                await self.page.wait_for_function(
                    "() => document.querySelector('#outcome').textContent.includes('waiting for model authority')")
                raise ShowrunError("lost_reply", "Simulated lost acknowledgment after dispatch.")
        monkeypatch.setattr(Browser, "act", interrupt)
    ReviewNavigator.calls = 0
    target = Showrun.prepare_fixture(PRESENTATION, tmp_path / "fixture", stories_python)["target"]
    value = comment_request(target)
    api = Showrun(tmp_path / "takes", MODEL)
    result = api.record(value)
    assert result["status"] == ("failed" if lose_response else "succeeded"), result
    assert result["cleanup"] == "verified"
    assert result["usage"]["actions"] == 8 and 8 <= result["usage"]["model_calls"] <= 12
    calls = ReviewNavigator.calls
    if lose_response:
        assert result["steps"][-1]["status"] == "uncertain"
        assert any(e.get("state") == "dispatched" for e in result["steps"][-1]["events"])
    else:
        assert all(s["status"] == "completed" for s in result["steps"])
        evidence = result["steps"][-1]["evidence"]["retained_comment"]
        assert evidence["status"] == "awaiting_authority" and evidence["count"] == 1
        assert evidence["revision_id"] == target["revision_id"]
        assert evidence["operation_id"] is None and evidence["text"] == TEXT
    effects = result["comment_effects"]
    assert sum(e.get("route") == "add-comment" for e in effects) == 1
    assert 3 <= sum(e.get("route") == "save-draft" for e in effects) <= 8
    assert api.record(value) == api.status(value["request_id"])
    assert ReviewNavigator.calls == calls
    assert asyncio.run(validate_fixture(target))["status"] == "valid"
    state = asyncio.run(helper({**target, "operation": "review"}))
    assert len(state["annotations"]) == 1 and not state["feedback_grant"]
    assert all(d["text"] == "" for d in state["drafts"].values())
    # New identity cannot silently duplicate the retained comment.
    again = copy.deepcopy(value)
    again["request_id"] = "another-take"
    denied = api.record(again)
    assert denied["error"]["code"] == "comment_precondition"
    assert denied["usage"] == {"actions": 0, "model_calls": 0}
    assert ReviewNavigator.calls == calls


def policy():
    grant = {"story_id": "story_one", "revision_id": "rev_one", "text": TEXT}
    events, saves = [], []
    p = Comment({}, grant, events, lambda: saves.append(copy.deepcopy(events)))
    draft = {"revision_id": "rev_one", "text": "", "anchor": {"kind": "story"},
             "draft_id": 'review-00000000-0000-0000-0000-000000000000-rev_one-["story",null,null,null]',
             "sequence": 1}
    submit = {"revision_id": "rev_one", "text": TEXT, "anchor": {"kind": "story"},
              "request_id": "11111111-1111-1111-1111-111111111111"}
    return p, draft, submit, events, saves


@pytest.mark.parametrize("change", [
    lambda p: p.update(story_id="other"),
    lambda p: p.update(revision_id="rev_other"),
    lambda p: p.update(text="Something else"),
    lambda p: p.update(anchor={"kind": "element", "element": "one"}),
    lambda p: p.update(author="agent"),
    lambda p: p.update(grant={"max_operations": 1}),
    lambda p: p.update(request_id=""),
])
def test_transport_denies_wrong_fields_or_target(change):
    p, draft, submit, events, _ = policy()
    p.dispatch("open_comment")
    p.authorize("save-draft", draft)
    p.dispatch("fill_comment")
    p.dispatch("submit_comment")
    change(submit)
    with pytest.raises(ShowrunError):
        p.authorize("add-comment", submit)
    assert len(events) == 1 and not p.sent


def test_durable_single_submit_and_bounded_autosaves():
    p, draft, submit, events, saves = policy()
    with pytest.raises(ShowrunError):
        p.authorize("save-draft", draft)
    p.dispatch("open_comment")
    p.authorize("save-draft", draft)
    p.dispatch("fill_comment")
    draft.update(sequence=2, text=TEXT)
    p.authorize("save-draft", draft)
    with pytest.raises(ShowrunError):
        p.authorize("add-comment", submit)
    p.dispatch("submit_comment")
    p.authorize("add-comment", submit)
    assert saves[-1][-1]["state"] == "dispatched"  # before caller can send anything
    with pytest.raises(ShowrunError):
        p.authorize("add-comment", submit)
    with pytest.raises(ShowrunError):
        p.dispatch("submit_comment")
    for sequence in range(3, 9):
        p.authorize("save-draft", {**draft, "sequence": sequence, "text": ""})
    with pytest.raises(ShowrunError):
        p.authorize("save-draft", {**draft, "sequence": 9})
    assert len(events) == 9


def test_comment_schema_does_not_widen_old_requests():
    old = request()
    effective = validate(old, MODEL)
    assert "stories_comment" not in effective["authority"]
    assert all("assertions" not in s for s in effective["steps"])
    target = {"kind": "stories", "python": "/bin/python", "storage": "/tmp/prepared",
              "story_id": "story_one", "revision_id": "rev_one", "fixture_sha256": "1" * 64}
    value = comment_request(target)
    assert validate(value, MODEL)["authority"]["stories_comment"]["text"] == TEXT
    for edit in [
        lambda r: r["authority"].update(navigation_only=True),
        lambda r: r["authority"].update(stories_comment=None),
        lambda r: r["authority"]["stories_comment"].update(story_id="other"),
        lambda r: r["authority"]["stories_comment"].update(revision_id="other"),
        lambda r: r.update(target=old["target"]),
        lambda r: r.update(steps=old["steps"]),
    ]:
        changed = copy.deepcopy(value)
        edit(changed)
        with pytest.raises(ShowrunError):
            validate(changed, MODEL)


def test_draft_and_input_are_not_retained_comment(tmp_path, monkeypatch):
    from amplifier_smart_tool_showrun import comment as module
    p, draft, submit, _, _ = policy()
    p.sent = True

    async def read(_):
        return {"annotations": [], "drafts": {"one": {"text": TEXT}}, "feedback_grant": None}

    monkeypatch.setattr(module, "helper", read)
    assert asyncio.run(p.verify()) is None
    b = Browser(SimpleNamespace(config={"kind": "url"}), tmp_path, {"width": 800, "height": 600})
    b.comment = p
    observation = {"frames": [{"text": TEXT, "controls": []}], "generation": 1}
    assert not asyncio.run(b.matches(observation, {"assertions": [{"kind": "retained_comment"}]}))
    assert not asyncio.run(b.matches(observation, {
        "assertions": [{"kind": "control", "label": "Show review", "visible": True}]}))


def test_installed_controls_reject_wrong_fill_and_send_without_grant(tmp_path, stories_python):
    target_config = Showrun.prepare_fixture(PRESENTATION, tmp_path / "fixture", stories_python)["target"]

    async def run():
        folder = tmp_path / "browser"
        folder.mkdir()
        target = Target(target_config, folder)
        browser = Browser(target, folder, {"width": 1920, "height": 1080})
        try:
            await browser.start(await target.start())
            observation = await browser.observe()
            assert all(c["label"] not in {"Comment", "Send", "Comment on story"}
                       for f in observation["frames"] for c in f["controls"])
            with pytest.raises(ShowrunError):
                await browser.act({"action": "fill", "ref": "invented", "text": TEXT})
            # Attach a caller-approved capability for the same isolated target,
            # then interact solely via observation-bound real UI controls.
            p = Comment(target_config, comment_request(target_config)["authority"]["stories_comment"], [], lambda: None)
            await p.preflight()
            browser.comment = p

            async def perform(capability):
                # The real dashboard may finish rendering during a decision.
                # Like record(), reobserve only known pre-dispatch stale refs.
                for attempt in range(4):
                    observation = await browser.observe()
                    ref = next(c["ref"] for f in observation["frames"] for c in f["controls"]
                               if c.get("capability") == capability)
                    action = ({"action": "fill", "ref": ref, "text": TEXT} if capability == "fill_comment"
                              else {"action": "click", "ref": ref})
                    reserved = []
                    try:
                        await browser.act(action, before_dispatch=reserved.append)
                        return
                    except ShowrunError as exc:
                        assert exc.code == "stale_ref" and not reserved and attempt < 3
            await perform("open_comment")
            await browser.page.wait_for_function("() => document.querySelector('#saved').textContent === 'Draft saved'")
            observation = await browser.observe()
            ref = next(c["ref"] for f in observation["frames"] for c in f["controls"]
                       if c.get("capability") == "fill_comment")
            for action in [{"action": "fill", "ref": ref, "text": "Wrong"},
                           {"action": "click", "ref": ref}]:
                with pytest.raises(ShowrunError):
                    await browser.act(action)
            assert await browser.page.locator("#comment").input_value() == ""
            await perform("fill_comment")
            await browser.page.wait_for_function("() => document.querySelector('#saved').textContent === 'Draft saved'")
            assert await p.verify() is None
            assert (await helper({**target_config, "operation": "review"}))["annotations"] == []
            assert (await validate_fixture(target_config))["status"] == "valid"
        finally:
            await browser.capture.finish()
            await browser.close()
            await target.close()
    asyncio.run(run())


@pytest.mark.parametrize("route_name", ["save-draft", "add-comment", "accept-revision", "generate",
                                       "delete-story", "provider-settings-update", "grant-feedback"])
def test_network_denied_without_grant(tmp_path, route_name):
    class Route:
        request = SimpleNamespace(url=f"http://127.0.0.1:1234/api/{route_name}", method="POST")
        aborted = False
        continued = False

        async def abort(self):
            self.aborted = True

        async def continue_(self):
            self.continued = True
    browser = Browser(SimpleNamespace(config={"kind": "url"}), tmp_path, {"width": 800, "height": 600})
    browser.allowed_origin, browser.stories = "http://127.0.0.1:1234", True
    route = Route()
    asyncio.run(browser.route(route))
    assert route.aborted and not route.continued


@pytest.mark.parametrize("fault", ["frame", "redirect", "query", "wrong_revision", "wrong_body", "story_field"])
def test_granted_network_rejects_scope_before_dispatch(tmp_path, fault):
    p, draft, submit, events, _ = policy()
    p.dispatch("open_comment")
    p.authorize("save-draft", draft)
    p.dispatch("fill_comment")
    p.dispatch("submit_comment")
    main = object()
    data = copy.deepcopy(submit)
    if fault == "wrong_revision":
        data["revision_id"] = "rev_wrong"
    elif fault == "wrong_body":
        data["text"] = "wrong"
    elif fault == "story_field":
        data["story_id"] = "wrong"

    class Route:
        request = SimpleNamespace(url="http://127.0.0.1:1234/api/add-comment" + ("?x=1" if fault == "query" else ""),
                                  method="POST", post_data_json=data,
                                  frame=object() if fault == "frame" else main,
                                  redirected_from=object() if fault == "redirect" else None)
        aborted = False

        async def abort(self):
            self.aborted = True

        async def continue_(self):
            raise AssertionError("Unauthorized payload reached transport")
    browser = Browser(SimpleNamespace(config={"kind": "stories"}), tmp_path, {"width": 800, "height": 600})
    browser.allowed_origin, browser.stories = "http://127.0.0.1:1234", True
    browser.page, browser.comment = SimpleNamespace(main_frame=main), p
    route = Route()
    asyncio.run(browser.route(route))
    assert route.aborted and not p.sent and len(events) == 1


@pytest.mark.parametrize("surface", ["input", "label", "unrelated"])
def test_observation_never_discloses_input_values_or_known_control_secrets(
        tmp_path, monkeypatch, target_server, surface):
    import test_showrun
    secret = "synthetic-private-value"
    value = secret if surface == "input" else "unrelated-field-value"
    label = secret if surface == "label" else "Settings"
    monkeypatch.setattr(test_showrun, "HTML",
                        f'<html><body><input value="{value}"><button>{label}</button></body></html>'.encode())

    async def run():
        import json
        target = SimpleNamespace(config={"kind": "url"}, revision=None, ownership={})
        browser = Browser(target, tmp_path, {"width": 800, "height": 600}, [secret])
        try:
            await browser.start(target_server[0])
            if surface == "unrelated":
                observation = await browser.observe()
                assert value not in json.dumps(observation)
                assert observation["frames"][0]["controls"] == []
            else:
                with pytest.raises(ShowrunError) as exc:
                    await browser.observe()
                assert exc.value.code == "sensitive_surface" and browser.restricted
        finally:
            await browser.capture.finish()
            await browser.close()
    asyncio.run(run())


def test_v1_identity_keeps_original_hash_while_v2_excludes_only_review_text():
    from amplifier_smart_tool_showrun.stories_helper import content_identity, digest
    story = {"id": "story_one", "latest_revision": "rev_one", "selected_revision": "rev_one",
             "annotations": [], "drafts": {}, "feedback_grant": None}
    revision = {"id": "rev_one", "html": "original"}
    api = SimpleNamespace(list_stories=lambda: [{"id": "story_one"}],
                          get_story=lambda _: copy.deepcopy(story),
                          get_revision=lambda *_: copy.deepcopy(revision))
    original_v1 = content_identity(api, "story_one", "rev_one", 1)
    assert original_v1 == digest({"story": story, "revision": revision})
    original_v2 = content_identity(api, "story_one", "rev_one", 2)
    story["annotations"] = [{"text": TEXT}]
    story["drafts"] = {"one": {"text": TEXT}}
    assert content_identity(api, "story_one", "rev_one", 1) != original_v1
    assert content_identity(api, "story_one", "rev_one", 2) == original_v2
    story["feedback_grant"] = {"max_operations": 1}
    assert content_identity(api, "story_one", "rev_one", 2) != original_v2