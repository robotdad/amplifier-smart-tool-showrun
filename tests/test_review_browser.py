"""Real browser checks for standalone and official independent MCP AppBridge surfaces."""

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from test_review import _take

from amplifier_smart_tool_showrun import ReviewStore
from amplifier_smart_tool_showrun.review_server import ReviewService

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def browser_root(tmp_path):
    _take(tmp_path, "take-a", "red")
    _take(tmp_path, "take-b", "blue")
    return tmp_path


async def _loaded(page, selector="#player"):
    await page.locator(selector).evaluate(
        """video => video.readyState >= 1 ? true : new Promise(resolve =>
          video.addEventListener('loadedmetadata', () => resolve(true), {once:true}))"""
    )


def test_standalone_review_plays_seeks_and_preserves_selection_draft(browser_root):
    playwright = pytest.importorskip("playwright.async_api")

    async def run():
        service = ReviewService(
            ReviewStore(browser_root),
            port=0,
            authorized_workspaces={"default": None},
        )
        info = service.start()
        try:
            async with playwright.async_playwright() as pw:
                browser = await pw.chromium.launch()
                page = await browser.new_page(viewport={"width": 1100, "height": 900})

                await page.goto(info["url"])
                await page.locator(".tree-clip").first.click()
                await _loaded(page)
                player = page.locator("#player")
                assert await player.evaluate("video => video.videoWidth") == 160
                await player.evaluate("video => video.play()")
                await page.wait_for_timeout(500)
                playing_time = await player.evaluate("video => video.currentTime")
                assert playing_time > 0
                await player.evaluate("video => { video.pause(); video.currentTime = 0.65; }")
                await page.locator("#steps button").first.click()
                await page.wait_for_function("() => document.querySelector('#note-step').value === 'opening'")
                jumped = await player.evaluate("video => video.currentTime")
                assert jumped == pytest.approx(0.25, abs=0.15)
                await player.evaluate("video => video.play()")
                await page.wait_for_timeout(250)
                active_time = await player.evaluate("video => video.currentTime")
                await page.locator("#note-text").fill("Typing must survive an arriving take.")
                _take(browser_root, "take-c", "green")
                service.store.sync()
                await page.locator("#refresh").click()
                assert await page.locator("#note-text").input_value() == "Typing must survive an arriving take."
                assert await player.is_visible()
                assert await player.evaluate("video => !video.paused")
                assert await player.evaluate("video => video.currentTime") >= active_time
                assert await page.locator("#selected-status").inner_text()
                await page.locator("#theme-dark").click()
                await page.wait_for_function("() => document.documentElement.dataset.theme === 'dark'")
                assert await page.locator("html").get_attribute("data-theme") == "dark"
                await page.locator("#theme-system").click()
                await page.emulate_media(color_scheme="dark")
                await page.wait_for_timeout(100)
                assert await page.locator("html").get_attribute("data-theme") == "dark"
                await browser.close()
        finally:
            service.stop()

    asyncio.run(run())


def test_standalone_review_keeps_note_target_and_restores_anchors(browser_root):
    playwright = pytest.importorskip("playwright.async_api")

    async def run():
        service = ReviewService(
            ReviewStore(browser_root),
            port=0,
            authorized_workspaces={"default": None},
        )
        info = service.start()
        try:
            async with playwright.async_playwright() as pw:
                browser = await pw.chromium.launch()
                page = await browser.new_page(viewport={"width": 1100, "height": 900})
                await page.goto(info["url"])
                clips = await page.locator(".tree-clip").all()
                await clips[0].click()
                await _loaded(page)
                await page.locator("#steps button").first.click()
                await page.wait_for_function("() => document.querySelector('#note-step').value === 'opening'")
                await page.locator("#note-text").fill("Anchored A")
                await page.locator(".note-timing summary").click()
                await page.locator("#note-time").fill("0.4")
                await page.locator("#note-range-end").fill("0.9")
                await page.locator("#save-draft").click()
                await page.wait_for_function(
                    "() => document.querySelector('#draft-state').textContent.includes('Saved draft')"
                )
                await page.reload()
                await page.locator("#note-text").wait_for()
                assert await page.locator("#note-text").input_value() == "Anchored A"
                assert await page.locator("#note-step").input_value() == "opening"
                assert await page.locator("#note-time").input_value() == "0.4"
                assert await page.locator("#note-range-end").input_value() == "0.9"
                await page.locator("#note-text").fill("Anchored A dirty")

                state = service.store.workspace("default")
                clip_b = next(
                    clip for demo in state["demos"] for take in demo["takes"] for clip in take["clips"]
                    if clip["take_id"] == "take-b"
                )
                service.store.select_clip(
                    "default", clip_b["demo_id"], clip_b["take_id"], clip_b["clip_id"],
                    state["version"], "external-selection-b",
                )
                await page.locator("#refresh").click()
                await page.wait_for_function(
                    "() => document.querySelector('#selected-status').textContent.includes('take-b')"
                )
                assert await page.locator("#note-text").input_value() == "Anchored A dirty"
                assert await page.locator("#submit-note").is_disabled()
                assert "reselect" in (await page.locator("#draft-state").inner_text()).lower()
                assert service.store.notes("default") == []
                await browser.close()
        finally:
            service.stop()

    asyncio.run(run())


def test_standalone_review_retries_lost_submit_without_duplicate_note(browser_root):
    playwright = pytest.importorskip("playwright.async_api")

    async def run():
        service = ReviewService(
            ReviewStore(browser_root),
            port=0,
            authorized_workspaces={"default": None},
        )
        info = service.start()
        dropped = False
        try:
            async with playwright.async_playwright() as pw:
                browser = await pw.chromium.launch()
                page = await browser.new_page(viewport={"width": 1100, "height": 900})

                async def drop_submit_response(route):
                    nonlocal dropped
                    payload = json.loads(route.request.post_data or "{}")
                    if payload.get("operation") == "submit_note" and not dropped:
                        dropped = True
                        await route.fetch()
                        await route.abort("failed")
                    else:
                        await route.continue_()

                await page.route("**/api/call", drop_submit_response)
                await page.goto(info["url"])
                await page.locator(".tree-clip").first.click()
                await _loaded(page)
                await page.locator("#note-text").fill("Lost acknowledgement must retry once")
                await page.locator("#submit-note").click()
                await page.wait_for_function(
                    "() => document.querySelector('#draft-state').textContent.includes('Submission pending')"
                )
                await page.reload()
                await page.locator("#note-text").wait_for()
                await page.wait_for_function(
                    "() => document.querySelector('#draft-state').textContent.includes('Submission pending')"
                )
                await page.unroute("**/api/call", drop_submit_response)
                await page.locator("#submit-note").click()
                await page.wait_for_function(
                    "() => document.querySelector('#notice').textContent.includes('Note submitted')"
                )
                notes = service.store.notes("default")
                assert len(notes) == 1
                assert notes[0]["text"] == "Lost acknowledgement must retry once"
                await browser.close()
        finally:
            service.stop()

    asyncio.run(run())


def test_standalone_review_releases_definite_invalid_range_for_corrected_save(browser_root):
    playwright = pytest.importorskip("playwright.async_api")

    async def run():
        service = ReviewService(
            ReviewStore(browser_root),
            port=0,
            authorized_workspaces={"default": None},
        )
        info = service.start()
        try:
            async with playwright.async_playwright() as pw:
                browser = await pw.chromium.launch()
                page = await browser.new_page(viewport={"width": 1100, "height": 900})
                await page.goto(info["url"])
                await page.locator(".tree-clip").first.click()
                await _loaded(page)
                await page.locator("#note-text").fill("Correct after validation")
                await page.locator(".note-timing summary").click()
                await page.locator("#note-time").fill("1.5")
                await page.locator("#note-range-end").fill("0.5")
                await page.locator("#save-draft").click()
                await page.wait_for_function(
                    "() => document.querySelector('#notice').textContent.includes('ordered start')"
                )
                await page.locator("#note-range-end").fill("1.9")
                await page.locator("#save-draft").click()
                await page.wait_for_function(
                    "() => document.querySelector('#draft-state').textContent.includes('Saved draft')"
                )
                assert service.store.workspace("default")["draft"]["text"] == "Correct after validation"
                await browser.close()
        finally:
            service.stop()

    asyncio.run(run())


def test_standalone_acknowledged_submit_allows_new_explicit_intent_after_reopen(browser_root):
    playwright = pytest.importorskip("playwright.async_api")

    async def run():
        service = ReviewService(
            ReviewStore(browser_root),
            port=0,
            authorized_workspaces={"default": None},
        )
        info = service.start()
        try:
            async with playwright.async_playwright() as pw:
                browser = await pw.chromium.launch()
                page = await browser.new_page(viewport={"width": 1100, "height": 900})
                await page.goto(info["url"])
                await page.locator(".tree-clip").first.click()
                await _loaded(page)
                await page.locator("#note-text").fill("First acknowledged note")
                await page.locator("#submit-note").click()
                await page.wait_for_function(
                    "() => document.querySelector('#notice').textContent.includes('Note submitted')"
                )

                await page.reload()
                await page.locator("#note-text").wait_for()
                await page.locator("#note-text").fill("Second explicit note")
                await page.locator("#submit-note").click()
                await page.wait_for_function(
                    "() => document.querySelector('#notice').textContent.includes('Note submitted')"
                )
                notes = service.store.notes("default")
                assert [note["text"] for note in notes] == [
                    "First acknowledged note", "Second explicit note"
                ]
                assert len({note["id"] for note in notes}) == 2
                await browser.close()
        finally:
            service.stop()

    asyncio.run(run())


def test_standalone_pending_save_remount_restores_canonical_retry_snapshot(browser_root):
    playwright = pytest.importorskip("playwright.async_api")

    async def run():
        service = ReviewService(
            ReviewStore(browser_root),
            port=0,
            authorized_workspaces={"default": None},
        )
        info = service.start()
        dropped = False
        try:
            async with playwright.async_playwright() as pw:
                browser = await pw.chromium.launch()
                page = await browser.new_page(viewport={"width": 1100, "height": 900})

                async def drop_save_response(route):
                    nonlocal dropped
                    payload = json.loads(route.request.post_data or "{}")
                    if payload.get("operation") == "save_draft" and not dropped:
                        dropped = True
                        await route.abort("failed")
                    else:
                        await route.continue_()

                await page.route("**/api/call", drop_save_response)
                await page.goto(info["url"])
                await page.locator(".tree-clip").first.click()
                await _loaded(page)
                await page.locator("#steps button").first.click()
                await page.wait_for_function("() => document.querySelector('#note-step').value === 'opening'")
                await page.locator("#note-text").fill("Pending canonical draft")
                await page.locator(".note-timing summary").click()
                await page.locator("#note-time").fill("0.4")
                await page.locator("#note-range-end").fill("0.9")
                await page.locator("#save-draft").click()
                await page.wait_for_function(
                    "() => document.querySelector('#draft-state').textContent.includes('pending')"
                )

                await page.reload()
                await page.locator("#note-text").wait_for()
                await page.wait_for_function(
                    "() => document.querySelector('#draft-state').textContent.includes('pending')"
                )
                assert await page.locator("#note-text").input_value() == "Pending canonical draft"
                assert await page.locator("#note-step").input_value() == "opening"
                assert await page.locator("#note-time").input_value() == "0.4"
                assert await page.locator("#note-range-end").input_value() == "0.9"
                assert await page.locator("#save-draft").is_enabled()

                await page.unroute("**/api/call", drop_save_response)
                await page.locator("#save-draft").click()
                await page.wait_for_function(
                    "() => document.querySelector('#draft-state').textContent.includes('Saved draft')"
                )
                draft = service.store.workspace("default")["draft"]
                assert draft["text"] == "Pending canonical draft"
                assert draft["anchor"] == {
                    "step_id": "opening", "range_start_seconds": 0.4, "range_end_seconds": 0.9
                }
                await browser.close()
        finally:
            service.stop()

    asyncio.run(run())


def test_standalone_review_reports_incomplete_delete_and_zip_status(browser_root, monkeypatch):
    playwright = pytest.importorskip("playwright.async_api")
    target = browser_root / "take-a" / "capture.mp4"
    original_unlink = Path.unlink

    def deny_target(path, *args, **kwargs):
        if Path(path) == target:
            raise OSError("simulated retained-media cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_target)

    async def run():
        service = ReviewService(
            ReviewStore(browser_root),
            port=0,
            authorized_workspaces={"default": None},
        )
        info = service.start()
        try:
            async with playwright.async_playwright() as pw:
                browser = await pw.chromium.launch()
                page = await browser.new_page(viewport={"width": 900, "height": 900})
                await page.goto(info["url"])
                await page.locator(".tree-clip").first.click()
                await _loaded(page)
                assert await page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
                await page.locator("#show-library").click()
                async with page.expect_download():
                    await page.get_by_role("button", name="Download demo ZIP:").first.click()
                await page.wait_for_function(
                    "() => document.querySelector('#download-status').textContent.includes('complete')"
                )
                await page.get_by_role("button", name="Delete clip:").first.click()
                await page.locator("#confirm-delete").click()
                await page.wait_for_function(
                    "() => document.querySelector('#notice').textContent.includes('cleanup is incomplete')"
                )
                assert "incomplete" in (await page.locator("#notice").inner_text()).lower()
                await page.reload()
                await page.locator("#review-deletion").click()
                await page.locator("#delete-dialog summary").click()
                await page.locator("#delete-preview").filter(has_text="cleanup_incomplete").wait_for()
                await browser.close()
        finally:
            service.stop()

    asyncio.run(run())


def test_mcp_app_uses_official_appbridge_and_same_shared_controls(browser_root):
    playwright = pytest.importorskip("playwright.async_api")
    pytest.importorskip("mcp")
    node_modules = Path(__file__).parents[1] / "mcp-app" / "node_modules"
    if not node_modules.is_dir():
        pytest.fail("Run npm install --prefix mcp-app before the independent AppBridge test.")
    root = Path(__file__).parents[1]
    host_bundle = subprocess.run(
        [
            str(node_modules / ".bin" / "esbuild"), str(root / "mcp-app" / "test-host.js"),
            "--bundle", "--format=iife", "--log-level=error",
        ],
        check=True, capture_output=True, text=True,
    ).stdout

    async def run():
        from mcp import Client

        from amplifier_smart_tool_showrun.mcp import create_server

        store = ReviewStore(browser_root)
        server = create_server(store, ["default"])
        async with Client(server) as client, playwright.async_playwright() as pw:
            listed = await client.list_resources()
            assert any(str(resource.uri) == "ui://showrun/capture-review" for resource in listed.resources)
            loaded = await client.read_resource("ui://showrun/capture-review")
            html = next(item.text for item in loaded.contents if getattr(item, "text", None))
            initial = await client.call_tool("showrun_review", {"workspace_id": "default"})
            page = await (await pw.chromium.launch()).new_page(viewport={"width": 1100, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            async def host_call(arguments):
                response = await client.call_tool(arguments["name"], arguments.get("arguments", {}))
                return response.model_dump(by_alias=True, exclude_none=True)

            async def host_read(arguments):
                response = await client.read_resource(arguments["uri"])
                return response.model_dump(by_alias=True, exclude_none=True)

            await page.expose_function("hostCall", host_call)
            await page.expose_function("hostRead", host_read)
            await page.goto("about:blank")
            await page.add_script_tag(content=host_bundle)
            await page.evaluate(
                "([html,initial]) => { window.mountShowrunReview(html, initial, {theme: 'dark', displayMode: 'inline'}); return true; }",
                [html, initial.model_dump(by_alias=True, exclude_none=True)],
            )
            frame = page.frame_locator("#app")
            await frame.locator(".tree-clip").first.wait_for(timeout=15000)
            assert await frame.locator("html").get_attribute("data-theme") == "dark"
            await frame.locator("#library-sort").select_option("name")
            assert await frame.locator("#library-sort").input_value() == "name"
            controls = await frame.locator("button").all_text_contents()
            assert "Save draft" in controls and "Submit note" in controls
            await frame.locator(".tree-clip").first.click()
            await frame.locator("#player").evaluate(
                """video => video.readyState >= 1 ? true : new Promise(resolve =>
                  video.addEventListener('loadedmetadata', () => resolve(true), {once:true}))"""
            )
            assert await frame.locator("#player").evaluate("video => video.videoWidth") == 160
            await frame.locator("#theme-light").click()
            await frame.locator("html").evaluate(
                "html => new Promise(resolve => html.dataset.theme === 'light' ? resolve(true) : setTimeout(() => resolve(false), 3000))"
            )
            assert await frame.locator("html").get_attribute("data-theme") == "light"
            await frame.locator("#theme-system").click()
            await frame.locator("html").evaluate(
                "html => new Promise(resolve => html.dataset.theme === 'dark' ? resolve(true) : setTimeout(() => resolve(false), 3000))"
            )
            assert await frame.locator("html").get_attribute("data-theme") == "dark"
            await page.evaluate("window.showrunBridge.setHostContext({theme: 'dark'})")
            await page.evaluate("window.showrunBridge.setHostContext({displayMode: 'fullscreen'})")
            await frame.locator("html").evaluate(
                """html => new Promise(resolve => {
                  if (html.dataset.theme === 'dark') return resolve(true);
                  const timer = setInterval(() => {
                    if (html.dataset.theme === 'dark') { clearInterval(timer); resolve(true); }
                  }, 25);
                  setTimeout(() => { clearInterval(timer); resolve(false); }, 3000);
                })"""
            )
            assert await frame.locator("html").get_attribute("data-theme") == "dark"
            # Exercise the same mutation and byte-delivery controls through AppBridge.
            import hashlib
            import zipfile
            await frame.locator("#note-text").fill("MCP review note")
            await frame.locator("#submit-note").click()
            await frame.locator("#notice").filter(has_text="Note submitted").wait_for()
            assert store.notes()[0]["text"] == "MCP review note"
            async with page.expect_download() as transfer:
                await frame.locator("#download-mp4").click()
            mp4_path = await (await transfer.value).path()
            assert hashlib.sha256(Path(mp4_path).read_bytes()).hexdigest() == store.workspace()["selection"]["content_sha256"]
            await frame.locator("#show-library").click()
            async with page.expect_download() as transfer:
                await frame.get_by_role("button", name="Download demo ZIP:").first.click()
            zip_path = await (await transfer.value).path()
            with zipfile.ZipFile(zip_path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                assert manifest["complete"]
                for entry in manifest["entries"]:
                    if entry["status"] == "included":
                        assert hashlib.sha256(archive.read(entry["path"])).hexdigest() == entry["sha256"]
            await frame.get_by_role("button", name="Rename clip:").first.click()
            await frame.get_by_role("textbox", name="New clip name").fill("Reviewed clip")
            await frame.get_by_role("textbox", name="New clip name").press("Enter")
            await frame.get_by_role("button", name="Rename clip: Reviewed clip", exact=True).wait_for()
            await frame.get_by_role("button", name="Delete clip:").first.click()
            await frame.locator("#confirm-delete").click()
            await frame.locator("#notice").filter(has_text="Deletion completed").wait_for()
            assert store.workspace()["selection"] is None
            assert await frame.locator("#player").get_attribute("src") is None
            assert not errors, errors

    asyncio.run(run())


def test_named_workspace_recovers_completed_note_after_newer_state(browser_root):
    playwright = pytest.importorskip("playwright.async_api")

    async def run():
        store = ReviewStore(browser_root)
        from test_review import _clips
        from test_review_security import _intent_payload, _select
        clip = _clips(store.workspace('editing'))[0]
        selected = _select(store, 'editing', clip, 'select-recover')
        store.begin_intent('editing', 'recover-note', 'submit_note',
                          _intent_payload(clip, selected['version'], 'Retained note', None, 'submit-recover'))
        result = store.submit_note('editing', clip['clip_id'], text='Retained note',
                                   expected_version=selected['version'], request_id='submit-recover',
                                   intent_id='recover-note')
        store.set_appearance('editing', 'dark', result['version'], 'newer-appearance')
        service = ReviewService(store, authorized_workspaces={'editing': None})
        info = service.start()
        try:
            async with playwright.async_playwright() as pw:
                browser = await pw.chromium.launch()
                page = await browser.new_page()
                await page.goto(info['url'])
                await page.locator('#draft-state').filter(has_text='Submission pending').wait_for()
                await page.locator('#submit-note').click()
                await page.wait_for_function("!document.querySelector('#draft-state').textContent.includes('pending')")
                assert len(store.notes('editing')) == 1
                assert store.workspace('editing')['review_intents'][0]['state'] == 'acknowledged'
                await page.locator('#note-text').fill('New explicit note')
                await page.locator('#submit-note').click()
                await page.wait_for_function("document.querySelector('#notice').textContent.includes('Note submitted')")
                assert len(store.notes('editing')) == 2
                await browser.close()
        finally:
            service.stop()

    asyncio.run(run())


def test_incomplete_zip_disclosure_precedes_download(browser_root):
    playwright = pytest.importorskip('playwright.async_api')
    (browser_root / 'take-a' / 'capture.mp4').unlink()

    async def run():
        service = ReviewService(ReviewStore(browser_root), authorized_workspaces={'default': None})
        info = service.start()
        try:
            async with playwright.async_playwright() as pw:
                browser = await pw.chromium.launch()
                page = await browser.new_page()
                downloads = []
                page.on('download', lambda download: downloads.append(download))
                await page.goto(info['url'])
                async def decline(dialog):
                    assert 'incomplete' in dialog.message
                    assert not downloads
                    await dialog.dismiss()
                page.once('dialog', decline)
                await page.get_by_role("button", name="Download demo ZIP:").first.click()
                await page.locator('#download-status').filter(has_text='incomplete').wait_for()
                assert not downloads
                page.once('dialog', lambda dialog: dialog.accept())
                async with page.expect_download():
                    await page.get_by_role("button", name="Download demo ZIP:").first.click()
                await browser.close()
        finally:
            service.stop()

    asyncio.run(run())


def test_lost_intermediate_save_and_newer_state_do_not_trap_submission(browser_root, monkeypatch):
    playwright = pytest.importorskip('playwright.async_api')
    service = ReviewService(ReviewStore(browser_root), authorized_workspaces={'default': None})
    original = service.call
    lost = False

    def lose_saved_ack(operation, args):
        nonlocal lost
        result = original(operation, args)
        if operation == 'save_draft' and not lost:
            lost = True
            service.store.set_appearance('default', 'dark', result['version'], 'concurrent-theme')
            raise OSError('lost draft acknowledgment')
        return result

    monkeypatch.setattr(service, 'call', lose_saved_ack)

    async def run():
        info = service.start()
        try:
            async with playwright.async_playwright() as pw:
                browser = await pw.chromium.launch()
                page = await browser.new_page()
                await page.goto(info['url'])
                await page.locator('.tree-clip').first.click()
                await page.locator('#note-text').fill('Exact pending note')
                await page.locator('#submit-note').click()
                await page.locator('#notice.error').wait_for()
                await page.locator('#refresh').click()
                await page.wait_for_function("document.documentElement.dataset.theme === 'dark'")
                await page.locator('#submit-note').click()
                await page.locator('#notice.error').filter(has_text='Review state changed').wait_for()
                await page.locator('#submit-note').click()
                await page.locator('#notice').filter(has_text='Note submitted').wait_for()
                assert len(service.store.notes()) == 1
                await browser.close()
        finally:
            service.stop()

    asyncio.run(run())


def test_recipe_step_notes_survive_switch_and_refresh(browser_root):
    receipt_path = browser_root / 'take-a' / 'receipt.json'
    receipt = json.loads(receipt_path.read_text())
    receipt['steps'].append({'id': 'unrecorded', 'status': 'unattempted', 'requested': {'instruction': 'Same label'}})
    receipt['steps'][0]['requested']['instruction'] = 'Same label'
    receipt_path.write_text(json.dumps(receipt))

    async def run():
        from playwright.async_api import async_playwright
        service = ReviewService(ReviewStore(browser_root), port=0, authorized_workspaces={'default': None})
        info = service.start()
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch()
                page = await browser.new_page(viewport={'width': 1500, 'height': 900})
                await page.goto(info['url'])
                await page.locator('.tree-clip').first.click()
                await _loaded(page)
                await page.locator('#note-text').fill('Whole clip feedback')
                await page.locator('#steps button').first.click()
                await page.wait_for_function("() => document.querySelector('#note-step').value === 'opening'")
                await page.reload()
                await page.wait_for_function("() => document.querySelector('#note-step').value === 'opening'")
                await page.locator('#note-text').fill('Keep the opening longer')
                await page.locator('#steps button').nth(1).click()
                await page.wait_for_function("() => document.querySelector('#note-step').value === 'unrecorded'")
                assert await page.locator('#note-text').input_value() == ''
                await page.locator('#note-text').fill('Please record this part')
                await page.locator('#steps button').first.click()
                await page.wait_for_function("() => document.querySelector('#note-text').value === 'Keep the opening longer'")
                await page.reload()
                await page.wait_for_function("() => document.querySelector('#note-text').value === 'Keep the opening longer'")
                await page.locator('#submit-note').click()
                await page.wait_for_function("() => document.querySelector('#step-notes').textContent.includes('Keep the opening longer')")
                note = service.store.notes()[0]
                assert note['anchor'] == {'step_id': 'opening'}
                await page.locator('#steps button').nth(1).click()
                await page.wait_for_function("() => document.querySelector('#note-text').value === 'Please record this part'")
                assert await page.locator('#player').is_visible()
                await page.locator('#whole-clip-note').click()
                await page.wait_for_function("() => document.querySelector('#note-text').value === 'Whole clip feedback'")
                await page.locator('#submit-note').click()
                await page.wait_for_function("() => document.querySelector('#step-notes').textContent.includes('Whole clip feedback')")
                assert service.store.notes()[-1]['anchor'] == {}
                await browser.close()
        finally:
            service.stop()
    asyncio.run(run())


def test_review_step_plays_changing_frames_and_keeps_video_visible(browser_root):
    """A moving playhead alone is insufficient: decode two visibly different frames."""
    import hashlib

    media_path = browser_root / "take-a" / "capture.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=red:s=160x90:r=25:d=1", "-f", "lavfi", "-i",
        "color=c=blue:s=160x90:r=25:d=1", "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0[out]", "-map", "[out]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(media_path),
    ], check=True)
    receipt_path = media_path.with_name("receipt.json")
    receipt = json.loads(receipt_path.read_text())
    receipt["media"].update(sha256=hashlib.sha256(media_path.read_bytes()).hexdigest(),
                            bytes=media_path.stat().st_size)
    receipt["steps"][0]["interval"]["end_seconds"] = 1.6
    receipt_path.write_text(json.dumps(receipt))

    async def run():
        from playwright.async_api import async_playwright
        service = ReviewService(ReviewStore(browser_root), authorized_workspaces={"default": None})
        info = service.start()
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch()
                page = await browser.new_page(viewport={"width": 870, "height": 874})
                await page.goto(info["url"])
                await page.locator(".tree-clip").first.click()
                await _loaded(page)
                assert not await page.locator(".browser-panel").is_visible()
                assert await page.locator(".library-actions").count() == 0
                assert await page.locator("select#note-step").count() == 0
                await page.locator("#steps button").first.click()
                await page.wait_for_function("!document.querySelector('#player').paused")
                pixel = """video => {
                    const canvas = document.createElement('canvas');
                    canvas.width = 160; canvas.height = 90;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(video, 0, 0);
                    return [...ctx.getImageData(80, 45, 1, 1).data];
                }"""
                red = await page.locator("#player").evaluate(pixel)
                assert red[0] > 200 and red[2] < 40
                await page.wait_for_function("document.querySelector('#player').currentTime > 1.15")
                blue = await page.locator("#player").evaluate(pixel)
                assert blue[2] > 200 and blue[0] < 40
                await page.wait_for_function("document.querySelector('#player').paused")
                assert await page.locator("#player").evaluate("v => v.currentTime") < 1.95
                await page.locator("#note-text").fill("Keep this draft while browsing.")
                await page.locator("#show-library").click()
                assert await page.get_by_role("button", name="Download demo ZIP:").first.is_visible()
                assert await page.locator(".tree-clip.selected").count() == 1
                await page.locator("#show-review").click()
                assert await page.locator("#note-text").input_value() == "Keep this draft while browsing."
                for width in (870, 600, 390):
                    await page.set_viewport_size({"width": width, "height": 874})
                    await page.locator("#note-text").scroll_into_view_if_needed()
                    box = await page.locator("#player").bounding_box()
                    assert box and box["y"] >= 0 and box["y"] + box["height"] < 874
                    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                await browser.close()
        finally:
            service.stop()

    asyncio.run(run())


def test_library_actions_target_their_card_and_inline_rename_is_cancelable(browser_root):
    async def run():
        from playwright.async_api import async_playwright
        service = ReviewService(ReviewStore(browser_root), authorized_workspaces={"default": None})
        info = service.start()
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch()
                page = await browser.new_page(viewport={"width": 870, "height": 874})
                await page.goto(info["url"])
                assert await page.title() == "Showrun"
                assert await page.locator(".view-switch button").all_text_contents() == ["Review", "Library"]
                assert await page.locator("select#theme").count() == 0
                await page.locator(".tree-clip").first.click()
                await _loaded(page)
                selected = service.store.workspace()["selection"]["clip_id"]
                await page.locator("#show-library").click()
                card = page.locator(".tree-group").nth(1)
                await card.get_by_role("button", name="Rename demo:").click()
                name = page.get_by_role("textbox", name="New demo name")
                await name.fill("Do not save")
                await name.press("Escape")
                assert "Do not save" not in await card.inner_text()
                await card.get_by_role("button", name="Rename demo:").click()
                await name.fill("Another demo")
                await name.press("Enter")
                card = page.locator(".tree-group").filter(has=page.get_by_role("button", name="Rename demo: Another demo", exact=True))
                await card.get_by_role("button", name="Rename demo: Another demo", exact=True).wait_for()
                assert service.store.workspace()["selection"]["clip_id"] == selected
                await card.get_by_role("button", name="Rename take:").click()
                await page.get_by_role("textbox", name="New take name").fill("Second take")
                await page.get_by_role("textbox", name="New take name").press("Tab")
                await card.get_by_role("button", name="Rename take: Second take", exact=True).wait_for()
                for scope, label in [("demo", "Another demo"), ("take", "Second take"), ("clip", "Clip 1 · Second take")]:
                    await card.get_by_role("button", name=f"Delete {scope}: {label}", exact=True).click()
                    await page.locator("#delete-dialog").wait_for()
                    snapshot = json.loads(await page.locator("#delete-preview").text_content())
                    assert snapshot["scope"] == scope
                    assert snapshot["take_ids"] == ["take-b"]
                    assert "cannot be undone" in await page.locator("#delete-description").inner_text()
                    await page.locator("#cancel-delete").click()
                assert (browser_root / "take-b" / "capture.mp4").is_file()
                await page.set_viewport_size({"width": 390, "height": 874})
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                await card.locator(".tree-clip").click()
                await page.wait_for_function("document.body.dataset.view === 'review'")
                assert await page.locator("#selected-name").inner_text() == "Another demo"
                assert await page.locator("#download-mp4").is_visible()
                await browser.close()
        finally:
            service.stop()

    asyncio.run(run())


def test_library_sort_preserves_selection_draft_and_preference(browser_root):
    async def run():
        from playwright.async_api import async_playwright
        store = ReviewStore(browser_root)
        for demo in store.workspace()["demos"]:
            name = "Zulu" if demo["takes"][0]["id"] == "take-a" else "Alpha"
            store.rename_demo(demo["id"], name, demo["version"], "sort-name-" + demo["id"])
        _take(browser_root, "take-c", "green")
        store.sync()
        demo = next(d for d in store.workspace()["demos"] if d["takes"][0]["id"] == "take-c")
        store.rename_demo(demo["id"], "Middle", demo["version"], "sort-name-c")
        service = ReviewService(store, authorized_workspaces={"default": None})
        info = service.start()
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch()
                page = await browser.new_page(viewport={"width": 870, "height": 874})
                await page.goto(info["url"])
                names = page.locator(".demo-heading .editable-name")
                await names.first.wait_for()
                assert await names.all_text_contents() == ["Middle", "Alpha", "Zulu"]
                await page.locator(".tree-clip").first.click()
                await _loaded(page)
                selected = store.workspace()["selection"]["clip_id"]
                await page.locator("#note-text").fill("Draft survives library sorting")
                await page.locator("#show-library").click()
                await page.locator("#library-sort").select_option("name")
                assert await names.all_text_contents() == ["Alpha", "Middle", "Zulu"]
                assert store.workspace()["selection"]["clip_id"] == selected
                await page.locator("#show-review").click()
                assert await page.locator("#note-text").input_value() == "Draft survives library sorting"
                await page.locator("#save-draft").click()
                await page.locator("#draft-state").filter(has_text="Saved draft").wait_for()
                await page.reload()
                await page.locator("#show-library").click()
                assert await page.locator("#library-sort").input_value() == "name"
                assert await names.all_text_contents() == ["Alpha", "Middle", "Zulu"]
                await page.locator("#library-sort").select_option("recent")
                assert await names.all_text_contents() == ["Middle", "Alpha", "Zulu"]
                await page.locator("#refresh").click()
                assert await names.all_text_contents() == ["Middle", "Alpha", "Zulu"]
                await page.set_viewport_size({"width": 390, "height": 874})
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                await browser.close()
        finally:
            service.stop()

    asyncio.run(run())
