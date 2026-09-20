"""Generic observed UI operation; old restricted requests retain their original policy.

No application labels or selectors belong here. Legacy request behavior is isolated
in legacy_browser so retained fingerprints never acquire broader authority.
"""
import asyncio
import json
from urllib.parse import parse_qs, urljoin, urlsplit

from .errors import ShowrunError, require
from .legacy_browser import OBSERVE, VISIBLE
from .legacy_browser import Browser as LegacyBrowser
from .schema import obj, origin

CONTROL = r"""el => ({
 label: (el.getAttribute('aria-label') || (el.getAttribute('aria-labelledby') || '').split(/\s+/).map(id=>document.getElementById(id)?.innerText || '').join(' ').trim() || [...(el.labels || [])].map(x=>{const c=x.cloneNode(true);c.querySelectorAll('input,select,textarea').forEach(n=>n.remove());return c.innerText || c.textContent;}).join(' ') || el.innerText || el.getAttribute('placeholder') || el.getAttribute('title') || '').trim().slice(0,500),
 tag: el.tagName.toLowerCase(), type: el.getAttribute('type') || '', role: el.getAttribute('role') || '',
 context: (el.closest('fieldset,article,section,form,[role=group],li,tr')?.innerText || '').trim().slice(0,1000),
 href: el.getAttribute('href'), value: el.value ?? null, checked: el.checked ?? null,
 options: el.tagName === 'SELECT' ? [...el.options].map(x=>({label:x.label,value:x.value,disabled:x.disabled})) : [],
 editable: el.isContentEditable, readonly: !!el.readOnly
})"""
SELECTOR = 'button,a,input:not([type=hidden]),textarea,select,[role=button],[role=checkbox],[role=tab],[contenteditable=true]'
UI_KEYS = {'Tab', 'Shift+Tab', 'Enter', 'Escape', 'Space', 'ArrowRight', 'ArrowLeft', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageDown', 'PageUp'}


class Browser(LegacyBrowser):
    ui = None

    async def start(self, url):
        # Authentication fragments are access configuration, never model context.
        self.secrets.extend(v for values in parse_qs(urlsplit(url).fragment).values() for v in values)
        await super().start(url)

    async def route(self, route):
        if not self.ui:
            return await super().route(route)
        request = route.request
        parsed = urlsplit(request.url)
        allowed = parsed.scheme in {'http', 'https'} and origin(request.url) == self.allowed_origin
        if not allowed:
            self.fail('network_scope', 'Target attempted traffic outside its approved origin.')
            await route.abort()
        else:
            # Target-session effects are explicitly granted. HTTP verbs are not
            # read/write proof. Backend restrictions belong to the target session.
            await route.continue_()

    async def _snapshot(self):
        if not self.ui:
            return await super()._snapshot()
        self.check()
        frames, refs = await self.frames(), {}
        result = {'generation': self.generation, 'frames': [], 'ui_authority': self.ui}
        for i, frame in enumerate(frames):
            data = await frame.evaluate(OBSERVE)
            if data['sensitive'] or any(s and s in json.dumps(data) for s in self.secrets):
                self.fail('sensitive_surface', 'Sensitive content appeared; handoff is restricted.', True)
                self.check()
            row = {'frame': i, 'text': data['text'], 'headings': data['headings'],
                   'controls': [], 'visible_controls': []}
            for handle in await frame.query_selector_all(SELECTOR):
                if not await handle.evaluate(VISIBLE):
                    continue
                detail = await handle.evaluate(CONTROL)
                enabled = await handle.is_enabled()
                row['visible_controls'].append({'label': detail['label'], 'enabled': enabled})
                actions = []
                if enabled and detail['type'] not in {'file', 'password'}:
                    actions = ['click']
                    if detail['tag'] in {'input', 'textarea'} or detail['editable']:
                        if not detail['readonly'] and detail['type'] not in {'checkbox', 'radio', 'submit', 'button'}:
                            actions.append('fill')
                    if detail['tag'] == 'select':
                        actions.append('select')
                    if detail['type'] in {'checkbox', 'radio'} or detail['role'] == 'checkbox':
                        actions.append('check')
                    if detail['href']:
                        try:
                            if origin(urljoin(frame.url, detail['href'])) != self.allowed_origin:
                                actions = []
                        except ShowrunError:
                            actions = []
                    if await handle.get_attribute('download') is not None:
                        actions = []
                actions = [a for a in actions if a in self.ui['actions']]
                if not actions:
                    continue
                ref = f'g{self.generation}.f{i}.e{len(refs)}'
                refs[ref] = (handle, detail)
                # URL access details remain private. Only label/context and state
                # needed for decisions are disclosed, under the DOM grant.
                public = {k: v for k, v in detail.items() if k != 'href'}
                row['controls'].append({'ref': ref, **public, 'actions': actions})
            result['frames'].append(row)
        if any(s and s in json.dumps(result) for s in self.secrets):
            self.fail('sensitive_surface', 'Sensitive control content appeared; handoff is restricted.', True)
        self.check()
        return result, refs, frames, set()

    async def matches(self, observation, step):
        if not await super().matches(observation, step):
            return False
        for assertion in step.get('assertions', []):
            if assertion['kind'] == 'field':
                controls = [c for f in observation['frames'] for c in f['controls']
                            if c['label'] == assertion['label']]
                key = 'value' if 'value' in assertion else 'checked'
                if len(controls) != 1 or controls[0].get(key) != assertion[key]:
                    return False
        return True

    async def validate_observation(self, generation):
        if not self.ui:
            return await super().validate_observation(generation)
        require(self.observed_state is not None and generation == self.generation,
                'Observation was superseded; reobserve.', 'stale_ref')
        frames = await self.frames()
        require(frames == self.frame_list and [f.url for f in frames] == self.observed_urls,
                'Observed surface changed; reobserve.', 'stale_ref')
        for document in self.documents:
            require(document is not None and await document.evaluate('el=>el===document.body'),
                    'Observed document was replaced; reobserve.', 'stale_ref')

    async def validate_action(self, action, generation=None):
        if not self.ui:
            return await super().validate_action(action, generation)
        self.check()
        name = action.get('action')
        if name in {'wait', 'fail'}:
            return await super().validate_action(action, generation)
        require(name in self.ui['actions'], 'UI action is outside the explicit grant.', 'invalid_action')
        await self.validate_observation(self.generation if generation is None else generation)
        if name in {'click', 'fill', 'select', 'check'}:
            fields = {'action', 'ref'} | ({'text'} if name == 'fill' else {'value'} if name == 'select'
                                         else {'checked'} if name == 'check' else set())
            obj(action, fields, fields)
            ref = action['ref']
            require(isinstance(ref, str) and ref in self.refs, 'Unknown or stale control.', 'stale_ref')
            handle, previous = self.refs[ref]
            observed = next(c for f in self.observed_state['frames'] for c in f['controls'] if c['ref'] == ref)
            require(name in observed['actions'], 'Action is not supported by the observed control.', 'invalid_action')
            try:
                current = await handle.evaluate(CONTROL)
                require(await handle.evaluate('el=>el.isConnected') and await handle.evaluate(VISIBLE)
                        and await handle.is_enabled() and current == previous,
                        'Control or its local context changed; reobserve.', 'stale_ref')
            except ShowrunError:
                raise
            except Exception:
                raise ShowrunError('stale_ref', 'Control detached; reobserve.') from None
            # Same labels are resolvable through observed local context and frame.
            peers = 0
            for frame in self.frame_list:
                for candidate in await frame.query_selector_all(SELECTOR):
                    if not await candidate.evaluate(VISIBLE) or not await candidate.is_enabled():
                        continue
                    detail = await candidate.evaluate(CONTROL)
                    if all(detail[k] == observed[k] for k in ('label', 'context', 'tag')):
                        peers += 1
            require(peers == 1, 'Control has indistinguishable observed peers.', 'ambiguous_navigation')
            if name == 'fill':
                require(action['text'] in self.ui['allowed_values'], 'Input value is outside the grant.', 'invalid_action')
            if name == 'select':
                require(action['value'] in self.ui['allowed_values'] and any(
                    o['value'] == action['value'] and not o['disabled'] for o in previous['options']),
                    'Selection value is outside the grant or observed choices.', 'invalid_action')
            if name == 'check':
                require(type(action['checked']) is bool, 'Check state must be boolean.', 'invalid_action')
        elif name in {'key', 'scroll'}:
            fields = {'action', 'frame', 'key' if name == 'key' else 'direction'}
            obj(action, fields, fields)
            require(type(action['frame']) is int and 0 <= action['frame'] < len(self.frame_list),
                    'Unknown frame.', 'invalid_action')
            require(action.get('key') in UI_KEYS if name == 'key' else action.get('direction') in {'up', 'down'},
                    'Unsupported key or scroll direction.', 'invalid_action')
        else:
            raise ShowrunError('invalid_action', 'Unsupported UI action.')
        return name

    async def act(self, action, before_dispatch=None, generation=None):
        if not self.ui:
            return await super().act(action, before_dispatch, generation)
        name = await self.validate_action(action, generation)
        if before_dispatch:
            before_dispatch(name)
        if name == 'click':
            await self.refs[action['ref']][0].click(timeout=3000)
        elif name == 'fill':
            await self.refs[action['ref']][0].fill(action['text'], timeout=3000)
        elif name == 'select':
            await self.refs[action['ref']][0].select_option(value=action['value'], timeout=3000)
        elif name == 'check':
            await self.refs[action['ref']][0].set_checked(action['checked'], timeout=3000)
        elif name == 'key':
            # Preserve observed focus; keys do not insert arbitrary strings.
            await self.frame_list[action['frame']].locator(':focus').press(action['key'], timeout=3000)
        elif name == 'scroll':
            await self.frame_list[action['frame']].evaluate(
                'direction=>window.scrollBy(0, direction * innerHeight * 0.65)',
                1 if action['direction'] == 'down' else -1)
        elif name == 'wait':
            await asyncio.sleep(.25)
        self.check()
