"""Saved web sign-in: scoped capture, private storage, preflight before capture, leak detection.

A local fake app mimics Amplifier Unified's login: a CSRF cookie on /login, a
password form, an HttpOnly session cookie and 307 redirects when signed out.
Tests sign in through the real login page in headless Chromium; no model is called.
"""
import asyncio
import json
import os
import secrets
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_showrun import MODEL, request, scripted  # noqa: F401

from amplifier_smart_tool_showrun import Showrun, auth
from amplifier_smart_tool_showrun.errors import ShowrunError
from amplifier_smart_tool_showrun.schema import validate

PASSWORD = 'correct horse battery staple'
LOGIN = b"""<!doctype html><html><body><h1>Sign in</h1><form method="post" action="/login">
<label>Username<input name="username"></label><label>Password<input name="password" type="password"></label>
<button>Sign in</button></form></body></html>"""


@pytest.fixture
def app_server():
    class Handler(BaseHTTPRequestHandler):
        sessions = set()
        leak = False

        def log_message(self, *args):
            pass

        def cookie(self, name):
            for part in self.headers.get('Cookie', '').split(';'):
                key, _, value = part.strip().partition('=')
                if key == name:
                    return value
            return None

        def do_GET(self):
            if self.path.startswith('/login'):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Set-Cookie', 'demo_csrf=' + secrets.token_urlsafe(16) + '; HttpOnly; Path=/')
                self.end_headers()
                self.wfile.write(LOGIN)
                return
            session = self.cookie('demo_session')
            if session not in type(self).sessions:
                self.send_response(307)
                self.send_header('Location', '/login?next=/')
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            body = '<h1>Welcome back</h1><p>Your chats</p>'
            if type(self).leak:
                body += f'<p>{session}</p>'
            self.wfile.write(body.encode())

        def do_POST(self):
            length = int(self.headers.get('Content-Length', 0))
            form = dict(p.split('=', 1) for p in self.rfile.read(length).decode().split('&') if '=' in p)
            from urllib.parse import unquote_plus
            if unquote_plus(form.get('password', '')) != PASSWORD:
                self.send_response(403)
                self.end_headers()
                return
            session = secrets.token_urlsafe(32)
            type(self).sessions.add(session)
            self.send_response(303)
            self.send_header('Set-Cookie', f'demo_session={session}; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800')
            self.send_header('Location', '/')
            self.end_headers()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', Handler
    server.shutdown()
    server.server_close()
    thread.join()


async def sign_in(page):
    """Stands in for the person: extra cookies for another site must not be kept."""
    await page.context.add_cookies([{'name': 'elsewhere', 'value': 'unrelated-secret-value',
                                     'domain': 'example.com', 'path': '/'}])
    await page.fill('input[name=username]', 'marc')
    await page.fill('input[name=password]', PASSWORD)
    await page.click('button')


def prepare(root, url, **options):
    options.setdefault('ready_text', 'Welcome back')
    options.setdefault('on_page', sign_in)
    options.setdefault('timeout', 20)
    return asyncio.run(auth.prepare(root, 'demo', url + '/', headless=True, **options))


def take(url, identity='auth-take'):
    value = request(url, identity)
    value['target']['auth'] = 'demo'
    value['starting_state'] = 'Welcome back'
    value['steps'] = [{'id': 'home', 'instruction': 'Show the signed-in home', 'visible_text': 'Your chats'}]
    return value


def test_prepare_keeps_only_verified_target_state_privately(tmp_path, app_server):
    url, handler = app_server
    root = tmp_path / 'auth'
    result = prepare(root, url)
    assert result['status'] == 'prepared' and result['model_calls'] == 0
    profile = result['profile']
    assert profile['origin'] == url and profile['cookie_count'] == 2  # session + csrf, same host
    assert profile['expires_at'] > time.time() + 6 * 86400
    serialized = json.dumps(result)
    assert not any(s in serialized for s in handler.sessions)
    state = json.loads((root / 'demo' / 'state.json').read_text())
    assert {c['name'] for c in state['cookies']} == {'demo_session', 'demo_csrf'}
    assert 'unrelated-secret-value' not in json.dumps(state)
    for path in (root, root / 'demo'):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o700
    for path in (root / 'demo' / 'state.json', root / 'demo' / 'profile.json'):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert not any(s in (root / 'demo' / 'profile.json').read_text() for s in handler.sessions)
    with pytest.raises(ShowrunError) as caught:
        prepare(root, url)
    assert caught.value.code == 'auth_exists'
    assert prepare(root, url, replace=True)['status'] == 'prepared'


def test_closing_the_window_without_signing_in_saves_nothing(tmp_path, app_server):
    url, _ = app_server

    async def give_up(page):
        await page.close()  # the login page already set a CSRF cookie
    with pytest.raises(ShowrunError) as caught:
        prepare(tmp_path / 'auth', url, ready_text=None, on_page=give_up)
    assert caught.value.code == 'auth_not_signed_in'
    assert caught.value.diagnostics == {'completed_by': 'window_closed', 'site_cookies_seen': 1,
                                        'site_storage_seen': 0, 'entry_status': 307}
    assert not (tmp_path / 'auth' / 'demo').exists()


def test_signed_in_but_window_left_open_is_saved_only_if_accepted(tmp_path, app_server):
    url, _ = app_server
    result = prepare(tmp_path / 'auth', url, ready_text=None, timeout=4)
    meta = json.loads((tmp_path / 'auth' / 'demo' / 'profile.json').read_text())
    assert result['status'] == 'prepared' and meta['verification']['completed_by'] == 'timeout'


def test_window_closed_after_sign_in_is_verified_and_saved(tmp_path, app_server):
    url, _ = app_server

    async def sign_in_then_close(page):
        await sign_in(page)
        await page.wait_for_selector('text=Welcome back')
        await page.close()
    result = prepare(tmp_path / 'auth', url, ready_text=None, on_page=sign_in_then_close)
    assert result['status'] == 'prepared'
    meta = json.loads((tmp_path / 'auth' / 'demo' / 'profile.json').read_text())
    assert meta['verification']['completed_by'] == 'window_closed' and meta['verification']['status'] == 200


def test_record_uses_saved_sign_in_without_disclosing_it(tmp_path, app_server, scripted):  # noqa: F811
    url, handler = app_server
    prepare(tmp_path / 'auth', url)
    api = Showrun(tmp_path / 'takes', MODEL, auth_root=tmp_path / 'auth')
    result = api.record(take(url))
    assert result['status'] == 'succeeded', result
    assert result['auth']['name'] == 'demo' and result['auth']['origin'] == url
    assert result['auth']['entry_check'] == {'status': 200, 'accepted': True}
    assert result['media']['decoded'] and scripted.calls == 0
    retained = (tmp_path / 'takes' / 'auth-take' / 'receipt.json').read_text()
    assert not any(s in retained for s in handler.sessions)


def test_rejected_session_fails_before_capture(tmp_path, app_server, scripted):  # noqa: F811
    url, handler = app_server
    prepare(tmp_path / 'auth', url)
    handler.sessions.clear()  # e.g. the server's session secret rotated
    result = Showrun(tmp_path / 'takes', MODEL, auth_root=tmp_path / 'auth').record(take(url))
    assert result['status'] == 'failed' and result['error']['code'] == 'auth_expired'
    assert 'auth prepare' in result['error']['remedy']
    assert result['media'] is None
    assert not (tmp_path / 'takes' / 'auth-take' / 'capture.mp4').exists()
    assert not (tmp_path / 'takes' / 'auth-take' / 'frames').exists()


@pytest.mark.parametrize('damage,code', [
    ('missing', 'auth_missing'), ('expired', 'auth_expired'), ('origin', 'auth_scope'), ('mode', 'auth_insecure'),
])
def test_unusable_profiles_fail_before_any_launch(tmp_path, app_server, scripted, damage, code):  # noqa: F811
    url, _ = app_server
    root = tmp_path / 'auth'
    if damage != 'missing':
        prepare(root, url)
        profile = root / 'demo' / 'profile.json'
        meta = json.loads(profile.read_text())
        if damage == 'expired':
            meta['expires_at'] = time.time() - 1
        elif damage == 'origin':
            meta['origin'] = 'http://127.0.0.1:1'
        profile.write_text(json.dumps(meta))
        if damage == 'mode':
            os.chmod(root / 'demo' / 'state.json', 0o644)
    result = Showrun(tmp_path / 'takes', MODEL, auth_root=root).record(take(url))
    assert result['status'] == 'failed' and result['error']['code'] == code
    assert result['media'] is None and result['resources'].get('browser') is None


def test_session_value_on_screen_restricts_footage(tmp_path, app_server, scripted):  # noqa: F811
    url, handler = app_server
    prepare(tmp_path / 'auth', url)
    handler.leak = True
    result = Showrun(tmp_path / 'takes', MODEL, auth_root=tmp_path / 'auth').record(take(url))
    assert result['restricted'] is True and result['media'] is None
    assert result['error']['code'] == 'sensitive_surface'


def test_auth_request_boundary():
    value = take('http://127.0.0.1:8080')
    assert validate(value, MODEL)['target']['auth'] == 'demo'
    for bad in ('../demo', '', 'a b'):
        value['target']['auth'] = bad
        with pytest.raises(ShowrunError):
            validate(value, MODEL)
    value['target']['auth'] = 'demo'
    value['target']['stories_revision'] = 'rev1'
    with pytest.raises(ShowrunError):
        validate(value, MODEL)
    assert 'auth' not in validate(request('http://127.0.0.1:8080'), MODEL)['target']


def test_list_and_delete(tmp_path, app_server):
    url, _ = app_server
    api = Showrun(tmp_path / 'takes', MODEL, auth_root=tmp_path / 'auth')
    assert api.auth('list')['profiles'] == []
    prepare(tmp_path / 'auth', url)
    listed = api.auth('list')['profiles']
    assert [p['name'] for p in listed] == ['demo'] and 'cookies' not in listed[0]
    assert api.auth('delete', 'demo') == {'status': 'deleted', 'profile': 'demo'}
    assert api.auth('list')['profiles'] == []
    with pytest.raises(ShowrunError) as caught:
        api.auth('delete', 'demo')
    assert caught.value.code == 'auth_missing'
