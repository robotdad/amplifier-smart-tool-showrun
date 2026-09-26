"""Saved web sign-in sessions. The person signs in; Showrun never sees credentials.

`prepare` opens a visible, fresh browser for the person to sign in to one origin,
keeps only that site's cookies and storage, verifies them in a separate headless
browser and stores them privately, outside the take root. Takes reference a profile
by name. Receipts, observations and model input never contain session values.
"""

import asyncio
import json
import os
import stat
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .errors import ShowrunError, require
from .schema import ident, origin

SCHEMA = 1


def default_root():
    return Path(os.environ.get('XDG_STATE_HOME', '~/.local/state')).expanduser() / 'showrun-auth'


def _host_matches(domain, host):
    domain = domain.lstrip('.').lower()
    host = host.lower()
    return host == domain or host.endswith('.' + domain)


def scoped_state(state, url):
    """Only the target host's cookies and the exact origin's storage are kept."""
    host, exact = urlsplit(url).hostname, origin(url)
    cookies = [c for c in state.get('cookies', []) if _host_matches(c.get('domain', ''), host)]
    origins = [o for o in state.get('origins', []) if o.get('origin') == exact]
    return {'cookies': cookies, 'origins': origins}


def secret_values(state):
    """Session values that must never appear on screen or reach the model."""
    values = [c.get('value', '') for c in state.get('cookies', [])]
    for item in state.get('origins', []):
        values += [entry.get('value', '') for entry in item.get('localStorage', [])]
    return [v for v in values if isinstance(v, str) and len(v) >= 8]


def expiry(state):
    """Earliest cookie expiry (Unix seconds), or None for browser-session cookies only."""
    stamps = [c['expires'] for c in state.get('cookies', [])
              if isinstance(c.get('expires'), (int, float)) and c['expires'] > 0]
    return min(stamps) if stamps else None


def _entry_url(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path or '/', parts.query, ''))


class AuthStore:
    def __init__(self, root=None):
        self.root = Path(root or default_root()).expanduser()

    def folder(self, name):
        ident(name)
        return self.root / name

    def _check_private(self, path, directory):
        info = os.lstat(path)
        kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        require(kind and info.st_uid == os.getuid() and info.st_mode & 0o077 == 0,
                'Saved sign-in storage is not private to this account.', 'auth_insecure')

    def _write(self, path, data):
        temporary = path.with_name('.' + path.name + '.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        os.replace(temporary, path)

    def save(self, name, url, state, verification):
        folder = self.folder(name)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        folder.mkdir(mode=0o700, exist_ok=True)
        os.chmod(folder, 0o700)
        meta = {'schema_version': SCHEMA, 'name': name, 'origin': origin(url), 'entry_url': _entry_url(url),
                'created_at': round(time.time(), 3), 'expires_at': expiry(state),
                'cookie_count': len(state['cookies']),
                'storage_count': sum(len(o.get('localStorage', [])) for o in state['origins']),
                'verification': verification}
        self._write(folder / 'state.json', state)
        self._write(folder / 'profile.json', meta)
        return meta

    def exists(self, name):
        return (self.folder(name) / 'profile.json').exists()

    def meta(self, name):
        folder = self.folder(name)
        require((folder / 'profile.json').is_file(), f'No saved sign-in named {name!r}.', 'auth_missing')
        return json.loads((folder / 'profile.json').read_text())

    def list(self):
        if not self.root.is_dir():
            return []
        return [self.meta(p.name) for p in sorted(self.root.iterdir())
                if p.is_dir() and not p.is_symlink() and (p / 'profile.json').is_file()]

    def delete(self, name):
        folder = self.folder(name)
        require(folder.is_dir() and not folder.is_symlink(), f'No saved sign-in named {name!r}.', 'auth_missing')
        for child in folder.iterdir():
            child.unlink()
        folder.rmdir()
        return {'status': 'deleted', 'profile': name}

    def load(self, name, url):
        """Return (state, public receipt block) for a take; fails before any browser launch."""
        folder = self.folder(name)
        if not (folder / 'state.json').is_file():
            raise ShowrunError('auth_missing', f'No saved sign-in named {name!r}.',
                               f'Run `showrun auth prepare {name} --url <entry URL>` and sign in yourself first.')
        for path, directory in ((self.root, True), (folder, True), (folder / 'state.json', False)):
            self._check_private(path, directory)
        meta = self.meta(name)
        if meta.get('origin') != origin(url):
            raise ShowrunError('auth_scope', 'The saved sign-in belongs to a different origin than the target.',
                               'Prepare a sign-in for this exact origin (scheme, host and port).')
        expires = meta.get('expires_at')
        if expires is not None and expires <= time.time():
            raise ShowrunError('auth_expired', 'The saved sign-in has expired.',
                               f'Run `showrun auth prepare {name} --url {meta.get("entry_url")} --replace`, '
                               'sign in again, then retake with a new request_id.')
        state = json.loads((folder / 'state.json').read_text())
        return state, public(meta)


def public(meta):
    return {k: meta.get(k) for k in ('name', 'origin', 'created_at', 'expires_at', 'cookie_count', 'storage_count')}


async def signed_in(context, url):
    """A non-redirecting entry request proves the session is accepted, before any capture."""
    response = await context.request.get(_entry_url(url), max_redirects=0, timeout=15000)
    try:
        return response.status, 200 <= response.status < 300
    finally:
        await response.dispose()


async def _ready(page, url, ready_text):
    try:
        if page.is_closed() or origin(page.url) != origin(url):
            return False
        return await page.evaluate(
            """text => !document.querySelector('input[type="password"]')
                && (document.body?.innerText || '').includes(text)""", ready_text)
    except Exception:
        return False


async def prepare(root, name, url, ready_text=None, timeout=300, replace=False, headless=False, on_page=None):
    """Interactive sign-in by the person. `headless`/`on_page` exist only for tests."""
    from playwright.async_api import async_playwright

    store = AuthStore(root)
    store.folder(name)
    origin(url)
    require(0 < timeout <= 1800, 'Sign-in timeout must be 1-1800 seconds.')
    require(replace or not store.exists(name), f'A saved sign-in named {name!r} already exists.', 'auth_exists')
    snapshot, finished = None, 'timeout'
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        try:
            context = await browser.new_context(accept_downloads=False, no_viewport=not headless)
            page = await context.new_page()
            await page.goto(url, wait_until='domcontentloaded', timeout=30000)
            if on_page:
                await on_page(page)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if not browser.is_connected():
                    finished = 'window_closed'
                    break
                open_pages = [p for p in context.pages if not p.is_closed()]
                if ready_text and any([await _ready(p, url, ready_text) for p in open_pages]):
                    snapshot, finished = await context.storage_state(), 'ready_text'
                    break
                if not open_pages:
                    finished = 'window_closed'
                    break
                snapshot = await context.storage_state()
                await asyncio.sleep(.5)
            if browser.is_connected():
                try:
                    snapshot = await context.storage_state()
                except Exception:
                    pass
        finally:
            if browser.is_connected():
                await browser.close()
        state = scoped_state(snapshot or {}, url)
        status = None

        def not_signed_in():
            # Bounded, value-free diagnostics: how it ended and what the site accepted.
            return ShowrunError(
                'auth_not_signed_in', 'No working sign-in was captured; nothing was saved.',
                'Rerun auth prepare, finish signing in in the window Showrun opens, then close that window '
                '(or wait for --ready-text) once the signed-in app is showing.',
                diagnostics={'completed_by': finished, 'site_cookies_seen': len(state['cookies']),
                             'site_storage_seen': sum(len(o.get('localStorage', [])) for o in state['origins']),
                             'entry_status': status})
        if not (state['cookies'] or state['origins']):
            raise not_signed_in()
        # A timeout still saves a session only if the site verifiably accepts it.
        # Verify in a separate, headless browser from only the scoped state.
        checker = await pw.chromium.launch(headless=True)
        try:
            context = await checker.new_context(storage_state=state)
            status, accepted = await signed_in(context, url)
            if accepted and ready_text:
                page = await context.new_page()
                await page.goto(_entry_url(url), wait_until='domcontentloaded', timeout=30000)
                deadline = time.monotonic() + 15
                accepted = False
                while time.monotonic() < deadline and not accepted:
                    accepted = await _ready(page, url, ready_text)
                    await asyncio.sleep(.25)
        finally:
            await checker.close()
    if not accepted:
        raise not_signed_in()
    meta = store.save(name, url, state, {'method': 'headless entry request without redirects'
                                          + (' plus ready text' if ready_text else ''),
                                          'status': status, 'completed_by': finished})
    return {'status': 'prepared', 'profile': public(meta), 'model_calls': 0,
            'storage': str(store.folder(name)),
            'notice': 'This saved session works like a password for the site until it expires. '
                      f'Delete it with `showrun auth delete {name}`.'}
