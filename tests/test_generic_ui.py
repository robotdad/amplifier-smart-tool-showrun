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


def test_observe_settles_when_the_page_rerenders_mid_snapshot(tmp_path):
    """A post-click re-render detaches handles mid-read; observation retries, never raw driver errors."""
    from playwright.async_api import Error as DriverError
    from playwright.async_api import async_playwright

    async def run():
        browser = Browser(SimpleNamespace(config={'kind': 'url'}, revision=None), tmp_path, {'width': 1280, 'height': 720})
        browser.ui = copy.deepcopy(UI)
        browser.allowed_origin = 'http://example.test'
        async with async_playwright() as pw:
            browser.browser = await pw.chromium.launch()
            browser.context = await browser.browser.new_context()
            browser.page = await browser.context.new_page()
            # Rows are replaced continuously for a while (like a library re-render after Open),
            # then the requested view appears and the page settles.
            await browser.page.route('**/*', lambda r: r.fulfill(content_type='text/html', body='''
              <main id="rows"></main><script>
                const rows = document.getElementById('rows'); let n = 0;
                const paint = () => rows.replaceChildren(...Array.from({length: 60}, (_, i) => {
                  const b = document.createElement('button'); b.textContent = 'Open ' + i + '.' + n; return b; }));
                const timer = setInterval(() => { n++; paint(); }, 1);
                setTimeout(() => { clearInterval(timer); rows.replaceChildren(); rows.append('Recipe steps'); }, 1500);
              </script>'''))
            await browser.page.goto(browser.allowed_origin)
            for _ in range(200):
                observation = await browser.observe()  # must not raise a raw driver error
                if 'Recipe steps' in observation['frames'][0]['text']:
                    break
            assert 'Recipe steps' in observation['frames'][0]['text']

            # Deterministic accounting: transient errors are retried, a page that never
            # settles reports a named Showrun error, and a closed page is not masked.
            real, calls = browser._snapshot, []
            async def flaky():
                calls.append(1)
                if len(calls) < 3:
                    raise DriverError('ElementHandle.is_enabled: Element is not attached to the DOM')
                return await real()
            browser._snapshot = flaky
            await browser.observe()
            assert len(calls) == 3
            async def unstable():
                raise DriverError('Execution context was destroyed')
            browser._snapshot = unstable
            with pytest.raises(ShowrunError) as caught:
                await browser.observe()
            assert caught.value.code == 'observation_unstable'
            browser._snapshot = real
            await browser.browser.close()
            with pytest.raises(Exception) as closed:
                await browser.observe()
            assert not isinstance(closed.value, ShowrunError) or closed.value.code != 'observation_unstable'
    asyncio.run(run())
