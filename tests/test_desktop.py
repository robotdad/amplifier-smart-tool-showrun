"""Native boundary and full recorded lifecycle with a scripted bridge, no OS grants/model."""
import asyncio
import base64
import copy
import struct
import zlib

import pytest
from test_showrun import MODEL

from amplifier_smart_tool_showrun import Showrun, desktop
from amplifier_smart_tool_showrun.errors import ShowrunError
from amplifier_smart_tool_showrun.schema import validate


def request():
    return {'request_id': 'native-demo',
            'target': {'kind': 'macos', 'bundle_id': 'org.showrun.fixture', 'window_title': 'Fixture'},
            'starting_state': 'Prepared', 'capture': {'width': 640, 'height': 360},
            'steps': [{'id': 'save', 'instruction': 'Press Save', 'visible_text': 'Saved'}],
            'authority': {'navigation_only': False, 'disclose_dom': False,
                          'disclose_accessibility': True, 'disclose_screenshots': True,
                          'max_seconds': 20, 'max_model_calls': 3, 'max_actions': 3,
                          'ui': {'actions': ['click', 'fill'], 'allowed_values': ['Example'],
                                 'target_effects': 'all_in_session'}}}


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    data = b'\x89PNG\r\n\x1a\n'
    data += chunk(b'IHDR', struct.pack('>IIBBBBB', 640, 360, 8, 2, 0, 0, 0))
    data += chunk(b'IDAT', zlib.compress((b'\x00' + b'\xff\xff\xff' * 640) * 360))
    return base64.b64encode(data + chunk(b'IEND', b'')).decode()


class Bridge:
    instances = []

    def __init__(self):
        self.saved = False
        self.closed = False
        self.generation = 0
        self.actions = []
        self.image = png()
        self.instances.append(self)

    async def start(self, target):
        self.target = target

    async def call(self, operation, **payload):
        if operation == 'observe':
            self.generation += 1
            return {'generation': self.generation, 'text': 'Saved' if self.saved else 'Prepared',
                    'controls': [{'ref': f'g{self.generation}.e0', 'label': 'Save',
                                  'actions': ['click'], 'value': '', 'enabled': True}]}
        if operation == 'screenshot':
            return {'png': self.image, 'width': 640, 'height': 360}
        if operation == 'act':
            assert payload['generation'] == self.generation
            self.actions.append(payload)
            self.saved = True
            return {'status': 'returned'}
        raise AssertionError(operation)

    async def close(self):
        self.closed = True


class Navigator:
    def __init__(self, model):
        pass

    async def start(self):
        pass

    async def decide(self, step, observation, context, remaining):
        assert observation['screenshot_png']
        return {'action': 'click', 'ref': observation['frames'][0]['controls'][0]['ref']}

    async def close(self):
        pass


@pytest.fixture
def native_runtime(monkeypatch):
    async def preflight():
        pass
    monkeypatch.setattr(desktop, 'MacBridge', Bridge)
    monkeypatch.setattr(desktop, 'preflight', preflight)
    monkeypatch.setattr('amplifier_smart_tool_showrun.agent.Navigator', Navigator)
    Bridge.instances = []


def test_record_native_and_exact_retry(tmp_path, native_runtime):
    api = Showrun(tmp_path, MODEL)
    result = api.record(request())
    assert result['status'] == 'succeeded', result
    assert result['media']['decoded']
    assert result['media']['timebase']['precision_seconds'] == .5
    assert result['steps'][0]['evidence']['method'] == 'macOS Accessibility window observation'
    assert Bridge.instances[0].closed and len(Bridge.instances[0].actions) == 1
    assert api.record(request())['status'] == 'succeeded'
    assert len(Bridge.instances) == 1
    assert api.inspect('native-demo')['inspection']['decoded']


@pytest.mark.parametrize('field', ['disclose_accessibility', 'disclose_screenshots'])
def test_native_requires_disclosure(field):
    value = request()
    value['authority'][field] = False
    with pytest.raises(ShowrunError):
        validate(value, MODEL)


def test_native_rejects_coordinate_keyboard_and_checked_claims():
    value = request()
    value['authority']['ui']['actions'].append('key')
    with pytest.raises(ShowrunError):
        validate(value, MODEL)
    value = request()
    value['steps'][0]['assertions'] = [{'kind': 'field', 'label': 'Task', 'checked': True}]
    with pytest.raises(ShowrunError):
        validate(value, MODEL)


def test_native_geometry_failure_precedes_actions(tmp_path, native_runtime):
    value = request()
    value['capture']['width'] = 800
    result = Showrun(tmp_path, MODEL).record(value)
    assert result['status'] == 'failed' and result['error']['code'] == 'capture_geometry'
    assert Bridge.instances[0].closed and not Bridge.instances[0].actions


def test_native_lost_action_ack_is_uncertain(tmp_path, native_runtime, monkeypatch):
    original = Bridge.call
    async def call(self, operation, **payload):
        result = await original(self, operation, **payload)
        if operation == 'act':
            raise ShowrunError('desktop_disconnected', 'Lost acknowledgment')
        return result
    monkeypatch.setattr(Bridge, 'call', call)
    api = Showrun(tmp_path, MODEL)
    result = api.record(request())
    assert result['steps'][0]['status'] == 'uncertain'
    assert len(Bridge.instances[0].actions) == 1
    api.record(request())
    assert len(Bridge.instances) == 1


def test_desktop_action_grants_and_stale_observations(tmp_path):
    async def run():
        from types import SimpleNamespace
        driver = desktop.Desktop(SimpleNamespace(config=request()['target']), tmp_path,
                                 request()['capture'], bridge=Bridge())
        driver.ui = copy.deepcopy(request()['authority']['ui'])
        observed = await driver.observe()
        with pytest.raises(ShowrunError):
            await driver.validate_action({'action': 'click', 'ref': 'old'}, observed['generation'])
        with pytest.raises(ShowrunError):
            await driver.validate_action({'action': 'key', 'key': 'Enter'}, observed['generation'])
    asyncio.run(run())


def test_native_model_receives_image_and_scoped_controls():
    from types import SimpleNamespace

    from amplifier_smart_tool_showrun.agent import Navigator as RealNavigator

    navigator = RealNavigator.__new__(RealNavigator)
    navigator.config = MODEL
    navigator.response_tokens = 2048
    navigator.reasoning_effort = None
    calls = []

    async def complete(value):
        calls.append(value)
        return SimpleNamespace(tool_calls=[], content='{"action":"wait"}')

    navigator.provider = SimpleNamespace(complete=complete)
    result = asyncio.run(navigator.decide(
        {'instruction': 'Save'}, {'desktop': True, 'screenshot_png': png(),
                                'ui_authority': request()['authority']['ui'], 'frames': []}, 'Demo', 10))
    assert result == {'action': 'wait'} and len(calls) == 1
    parts = calls[0].messages[1].content
    assert parts[0].type == 'text' and 'screenshot_png' not in parts[0].text
    assert parts[1].type == 'image' and parts[1].source['media_type'] == 'image/png'


def test_native_sensitive_observation_restricts_handoff(tmp_path, native_runtime, monkeypatch):
    original = Bridge.call
    async def call(self, operation, **payload):
        if operation == 'observe' and self.saved:
            raise ShowrunError('sensitive_surface', 'Secure field appeared')
        return await original(self, operation, **payload)
    monkeypatch.setattr(Bridge, 'call', call)
    result = Showrun(tmp_path, MODEL).record(request())
    assert result['status'] == 'failed' and result['restricted']
    assert result['media'] is None
    assert (tmp_path / 'native-demo' / 'restricted' / 'capture.mp4').is_file()


def test_native_duration_drift_preserves_decoded_media(tmp_path, native_runtime, monkeypatch):
    original = desktop.DesktopCapture.finish
    async def finish(self):
        media = await original(self)
        media['duration_seconds'] = .1
        media['timing'] = {'verified': False, 'warning': 'Test drift: timestamps are approximate.'}
        return media
    monkeypatch.setattr(desktop.DesktopCapture, 'finish', finish)
    result = Showrun(tmp_path, MODEL).record(request())
    assert result['status'] == 'succeeded' and result['media']['decoded']
    assert result['timing_warnings'][0]['step_id'] == 'save'
    assert any('Test drift' in item for item in result['limitations'])


def test_prepare_preserves_existing_app_on_build_failure(tmp_path, monkeypatch):
    import plistlib

    app = tmp_path / 'Showrun Desktop.app'
    executable = app / 'Contents' / 'MacOS' / 'showrun-desktop'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'previous build')
    (app / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps(
        {'CFBundleIdentifier': desktop.BUNDLE_ID, 'ShowrunSourceSHA256': 'old'}))
    monkeypatch.setattr(desktop, 'app_path', lambda: app)
    monkeypatch.setattr(desktop.sys, 'platform', 'darwin')
    monkeypatch.setattr(desktop.shutil, 'which', lambda name: '/usr/bin/' + name)

    async def fail(*args, **kwargs):
        raise RuntimeError('compiler failed')

    monkeypatch.setattr(desktop, 'command', fail)
    with pytest.raises(RuntimeError, match='compiler failed'):
        asyncio.run(desktop.prepare())
    assert executable.read_bytes() == b'previous build'


def test_prepare_reuses_unchanged_app(tmp_path, monkeypatch):
    import hashlib
    import plistlib

    app = tmp_path / 'Showrun Desktop.app'
    executable = app / 'Contents' / 'MacOS' / 'showrun-desktop'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'existing build')
    source = desktop.files(desktop.__package__).joinpath('native/macos.swift').read_bytes()
    (app / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps(
        {'CFBundleIdentifier': desktop.BUNDLE_ID,
         'ShowrunBuildTarget': desktop.platform.machine() + '-apple-macos14.0',
         'ShowrunSourceSHA256': hashlib.sha256(source).hexdigest()}))
    monkeypatch.setattr(desktop, 'app_path', lambda: app)
    monkeypatch.setattr(desktop.sys, 'platform', 'darwin')
    monkeypatch.setattr(desktop.shutil, 'which', lambda name: '/usr/bin/' + name)

    async def unexpected(*args, **kwargs):
        pytest.fail('Unchanged app should not be rebuilt or signed')

    monkeypatch.setattr(desktop, 'command', unexpected)
    result = asyncio.run(desktop.prepare())
    assert result['app'] == str(app)
    assert executable.read_bytes() == b'existing build'


@pytest.mark.parametrize('actual', [(640, 360), (800, 600)])
def test_resize_before_capture_and_actions(tmp_path, native_runtime, monkeypatch, actual):
    events = []
    original = Bridge.call

    async def call(self, operation, **payload):
        events.append(operation)
        if operation == 'resize':
            assert payload == {'width': 640, 'height': 360}
            return dict(zip(('width', 'height'), actual))
        return await original(self, operation, **payload)

    monkeypatch.setattr(Bridge, 'call', call)
    value = request()
    value['target']['resize_to_capture'] = True
    result = Showrun(tmp_path, MODEL).record(value)
    assert events[:2] == ['observe', 'resize']
    if actual == (640, 360):
        assert result['status'] == 'succeeded', result
        assert events.index('resize') < events.index('screenshot') < events.index('act')
    else:
        assert result['error']['code'] == 'capture_geometry'
        assert '800x600' in result['error']['message']
        assert 'screenshot' not in events and 'act' not in events
    assert Bridge.instances[0].closed


def test_resize_requires_explicit_boolean():
    value = request()
    value['target']['resize_to_capture'] = 'true'
    with pytest.raises(ShowrunError, match='must be boolean'):
        validate(value, MODEL)


def test_launch_services_private_connection_and_cleanup(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(desktop.sys, 'platform', 'darwin')
    helper = tmp_path / 'helper'
    helper.touch()
    monkeypatch.setattr(desktop, 'helper_path', lambda: helper)
    tasks = []
    sockets = []

    async def launch(*args, **kwargs):
        assert args[:4] == ('/usr/bin/open', '-n', '-g', '-a')
        socket_path = args[args.index('--socket') + 1]
        token = args[args.index('--token') + 1]
        sockets.append(socket_path)
        from pathlib import Path
        assert Path(socket_path).parent.stat().st_mode & 0o777 == 0o700

        async def companion():
            # A connection without the nonce must not claim the session.
            reader, writer = await asyncio.open_unix_connection(socket_path)
            writer.write(b'{"token":"wrong"}\n')
            await writer.drain()
            assert await reader.read() == b''
            writer.close()
            await writer.wait_closed()
            reader, writer = await asyncio.open_unix_connection(socket_path)
            writer.write((json.dumps({'token': token}) + '\n').encode())
            await writer.drain()
            request = json.loads(await reader.readline())
            assert request['operation'] == 'bind'
            writer.write(b'{"status":"ready"}\n')
            await writer.drain()
            assert await reader.read() == b''
            writer.close()
            await writer.wait_closed()
        tasks.append(asyncio.create_task(companion()))

    monkeypatch.setattr(desktop, 'command', launch)

    async def run():
        bridge = desktop.MacBridge()
        assert await bridge.start(request()['target']) == {'status': 'ready'}
        await bridge.close()
        await asyncio.gather(*tasks)
        from pathlib import Path
        assert not Path(sockets[0]).parent.exists()

    asyncio.run(run())


def test_wait_for_result_records_without_inference(tmp_path, native_runtime):
    value = request()
    value['authority']['max_seconds'] = 900
    value['steps'] = [{'id': 'wait', 'instruction': 'Wait for the prepared state.',
                       'visible_text': 'Prepared', 'wait_for_result': True, 'hold_seconds': 3}]
    result = Showrun(tmp_path, MODEL).record(value)
    assert result['status'] == 'succeeded', result
    assert result['usage'] == {'actions': 0, 'model_calls': 0}
    assert result['media']['decoded']


def test_wait_for_result_timeout_never_calls_model(tmp_path, native_runtime):
    value = request()
    value['authority']['max_seconds'] = 4
    value['steps'] = [{'id': 'wait', 'instruction': 'Wait for a state that never arrives.',
                       'visible_text': 'Not ready', 'wait_for_result': True, 'hold_seconds': 3}]
    result = Showrun(tmp_path, MODEL).record(value)
    assert result['status'] == 'failed'
    assert result['usage'] == {'actions': 0, 'model_calls': 0}
    assert Bridge.instances[0].closed
