"""Generic mechanics: real browser, arbitrary labels, POST transport, bounded values."""
import asyncio
import copy
from types import SimpleNamespace

import pytest
from test_showrun import request

from amplifier_smart_tool_showrun.browser import Browser
from amplifier_smart_tool_showrun.errors import ShowrunError
from amplifier_smart_tool_showrun.schema import validate

UI = {'actions': ['click', 'fill', 'select', 'check', 'scroll', 'key'],
      'allowed_values': ['Milo', 'alex'], 'target_effects': 'all_in_session'}


def test_generic_grant_does_not_widen_legacy():
    brief = request()
    brief['authority'].update(navigation_only=False, ui=copy.deepcopy(UI))
    brief['target']['url'] += '/?workspace=demo'
    assert validate(brief, None)['authority']['ui'] == UI
    brief['authority']['ui']['target_effects'] = 'read_only'
    with pytest.raises(ShowrunError):
        validate(brief, None)
    del brief['authority']['ui']
    with pytest.raises(ShowrunError):
        validate(brief, None)


def test_generic_form_and_context(tmp_path):
    from playwright.async_api import async_playwright

    async def run():
        target = SimpleNamespace(config={'kind': 'url'}, revision=None)
        browser = Browser(target, tmp_path, {'width': 1280, 'height': 720})
        browser.ui = copy.deepcopy(UI)
        browser.allowed_origin = 'http://example.test'
        async with async_playwright() as pw:
            browser.browser = await pw.chromium.launch()
            browser.context = await browser.browser.new_context()
            browser.page = await browser.context.new_page()
            await browser.page.route('**/*', lambda r: r.fulfill(content_type='text/html', body='''
              <h1>Walking planner</h1><span id="clock">1</span>
              <details><summary>Care preferences</summary><label>Care note<input></label></details>
              <form onsubmit="event.preventDefault();document.querySelector('h1').textContent='Walk saved'">
                <label>Dog name<input></label><label>Walker<select><option value="">Choose</option><option value="alex">Alex</option></select></label>
                <button>Arrange this walk</button><button type="button">Walker</button>
              </form>
              <aside aria-label="Extra options" style="height:100px;overflow-y:auto"><div style="height:400px">More options below</div><button>Last option</button></aside>
              <section><h2>Morning</h2><button>Inspect</button></section>
              <section><h2>Evening</h2><button>Inspect</button></section>
            '''))
            await browser.page.goto(browser.allowed_origin)
            async def action(label, name, **args):
                obs = await browser.observe()
                control = next(c for c in obs['frames'][0]['controls'] if c['label'] == label)
                await browser.act({'action': name, 'ref': control['ref'], **args})
            obs = await browser.observe()
            assert not any(c['label'] == 'Care note' for c in obs['frames'][0]['controls'])
            await action('Extra options', 'scroll', direction='down')
            assert await browser.page.locator('aside').evaluate('el=>el.scrollTop') > 0
            await action('Care preferences', 'click')
            await action('Care note', 'fill', text='Milo')
            assert await browser.page.get_by_label('Care note').input_value() == 'Milo'
            await action('Dog name', 'fill', text='Milo')
            await action('Walker', 'select', value='alex')
            assert await browser.matches(await browser.observe(), {'assertions': [
                {'kind': 'field', 'label': 'Walker', 'value': 'alex'}]})
            obs = await browser.observe()
            ref = next(c['ref'] for c in obs['frames'][0]['controls'] if c['label'] == 'Arrange this walk')
            await browser.page.locator('#clock').evaluate("el=>el.textContent='2'")
            await browser.act({'action': 'click', 'ref': ref})
            assert await browser.page.locator('h1').inner_text() == 'Walk saved'
            obs = await browser.observe()
            ref = next(c['ref'] for c in obs['frames'][0]['controls'] if c['label'] == 'Inspect' and 'Evening' in c['context'])
            await browser.act({'action': 'click', 'ref': ref})
            with pytest.raises(ShowrunError, match='outside the grant'):
                await action('Dog name', 'fill', text='unauthorized')
            obs = await browser.observe()
            ref = next(c['ref'] for c in obs['frames'][0]['controls'] if c['label'] == 'Dog name')
            await browser.page.get_by_label('Dog name').evaluate('el=>el.replaceWith(el.cloneNode(true))')
            with pytest.raises(ShowrunError):
                await browser.act({'action': 'fill', 'ref': ref, 'text': 'Milo'})
            await browser.browser.close()
    asyncio.run(run())


def test_transport_allows_same_origin_post_and_denies_escape(tmp_path):
    async def run():
        browser = Browser(SimpleNamespace(config={'kind': 'url'}), tmp_path, {})
        browser.ui = UI
        browser.allowed_origin = 'http://example.test'
        class Route:
            def __init__(self, url):
                self.request = SimpleNamespace(url=url, method='POST')
                self.result = None
            async def abort(self):
                self.result = 'aborted'
            async def continue_(self):
                self.result = 'continued'
        route = Route('http://example.test/arbitrary-api')
        await browser.route(route)
        assert route.result == 'continued'
        route = Route('https://elsewhere.test/')
        await browser.route(route)
        assert route.result == 'aborted'
        assert browser.fault.code == 'network_scope'
    asyncio.run(run())
