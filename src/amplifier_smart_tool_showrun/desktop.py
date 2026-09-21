"""First native backend: a prepared macOS window, AX actions, sampled window PNGs.

The bridge supplies observations and executes validated actions. The Showrun
library and Navigator retain decisions, budgets, recording and outcome checks.
"""

import asyncio
import base64
import hashlib
import json
import platform
import plistlib
import secrets
import shutil
import sys
import tempfile
import time
from importlib.resources import files
from pathlib import Path

from .capture import Capture, command
from .errors import ShowrunError, require
from .schema import obj

BUNDLE_ID = 'org.showrun.desktop'


def app_path():
    return Path.home() / 'Applications' / 'Showrun Desktop.app'


def helper_path():
    return app_path() / 'Contents' / 'MacOS' / 'showrun-desktop'


async def prepare():
    require(sys.platform == 'darwin', 'Native desktop currently requires macOS 14+.', 'desktop_unsupported')
    require(shutil.which('xcrun'), 'Install Apple Command Line Tools before prepare-desktop.',
            'desktop_dependency_missing')
    source = files(__package__).joinpath('native/macos.swift')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    destination = app_path()
    info = destination / 'Contents' / 'Info.plist'
    if destination.exists():
        try:
            metadata = plistlib.loads(info.read_bytes())
        except (OSError, ValueError):
            metadata = {}
        require(metadata.get('CFBundleIdentifier') == BUNDLE_ID,
                'The installation path contains another application; move it before preparing Showrun.',
                'desktop_install_conflict')
        if (metadata.get('ShowrunSourceSHA256') == digest
                and metadata.get('ShowrunBuildTarget') == platform.machine() + '-apple-macos14.0'
                and helper_path().is_file()):
            return _prepared()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Build and sign before touching an existing installation. Keep its path stable.
    with tempfile.TemporaryDirectory(prefix='.showrun-build-', dir=destination.parent) as temporary:
        staged = Path(temporary) / destination.name
        executable = staged / 'Contents' / 'MacOS' / 'showrun-desktop'
        executable.parent.mkdir(parents=True)
        await command('xcrun', 'swiftc', '-target', platform.machine() + '-apple-macos14.0',
                      '-parse-as-library', str(source), '-o', str(executable), timeout=120)
        metadata = {'CFBundleIdentifier': BUNDLE_ID, 'CFBundleName': 'Showrun Desktop',
                    'CFBundleDisplayName': 'Showrun Desktop', 'CFBundleExecutable': 'showrun-desktop',
                    'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1',
                    'CFBundleShortVersionString': '0.1', 'LSMinimumSystemVersion': '14.0',
                    'LSUIElement': True, 'ShowrunSourceSHA256': digest,
                    'ShowrunBuildTarget': platform.machine() + '-apple-macos14.0'}
        (staged / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps(metadata))
        await command('/usr/bin/codesign', '--force', '--sign', '-', str(staged), timeout=30)
        await command('/usr/bin/codesign', '--verify', '--strict', str(staged), timeout=30)
        backup = Path(temporary) / 'previous.app'
        if destination.exists():
            destination.rename(backup)
        try:
            staged.rename(destination)
        except BaseException:
            if backup.exists():
                backup.rename(destination)
            raise
    return _prepared()


def _prepared():
    return {'status': 'prepared', 'app': str(app_path()), 'helper': str(helper_path()),
            'bundle_id': BUNDLE_ID, 'signing': 'ad-hoc', 'model_calls': 0,
            'notice': 'Grant Screen Recording and Accessibility to Showrun Desktop.app in macOS settings. '
                      'Local builds use ad-hoc signing; updates may require granting permissions again.'}


class MacBridge:
    def __init__(self):
        self.reader = self.writer = self.server = self.connection = None
        self.socket_dir = None
        self.lock = asyncio.Lock()

    async def start(self, target=None):
        require(sys.platform == 'darwin', 'Native desktop currently requires macOS.', 'desktop_unsupported')
        require(helper_path().is_file(), 'Run showrun prepare-desktop first.', 'desktop_not_prepared')
        # LaunchServices owns the application, not the caller's process ancestry.
        # A private, per-run socket and nonce bind it to this one caller session.
        self.socket_dir = tempfile.TemporaryDirectory(prefix='showrun-', dir='/tmp')
        socket_path = str(Path(self.socket_dir.name) / 'bridge.sock')
        token = secrets.token_hex(32)
        self.connection = asyncio.get_running_loop().create_future()

        async def accept(reader, writer):
            try:
                hello = json.loads(await asyncio.wait_for(reader.readline(), 5))
                if hello != {'token': token} or self.connection.done():
                    writer.close()
                    return
                self.connection.set_result((reader, writer))
            except (ValueError, TimeoutError):
                writer.close()

        self.server = await asyncio.start_unix_server(accept, socket_path, limit=34 * 1024 * 1024)
        try:
            await command('/usr/bin/open', '-n', '-g', '-a', str(app_path()), '--args',
                          '--socket', socket_path, '--token', token, timeout=10)
            self.reader, self.writer = await asyncio.wait_for(self.connection, 15)
            self.server.close()
        except TimeoutError:
            await self.close()
            raise ShowrunError('desktop_launch_failed', 'The companion did not connect.',
                               'Check macOS Privacy & Security for a blocked launch, then run desktop-status.') from None
        except BaseException:
            await self.close()
            raise
        if target is None:
            return await self.call('permissions')
        return await self.call('bind', bundle_id=target['bundle_id'], window_title=target['window_title'])

    async def call(self, operation, **payload):
        async with self.lock:
            require(self.writer is not None and not self.writer.is_closing(),
                    'Desktop connection is unavailable.', 'desktop_disconnected')
            self.writer.write((json.dumps({'operation': operation, **payload}) + '\n').encode())
            await self.writer.drain()
            try:
                line = await asyncio.wait_for(self.reader.readline(), 10)
                result = json.loads(line)
            except asyncio.CancelledError:
                self.writer.close()
                raise
            except (TimeoutError, ValueError):
                # Do not issue another RPC after losing request/response alignment.
                self.writer.close()
                raise ShowrunError('desktop_disconnected', 'Desktop acknowledgment was lost; do not replay.') from None
            require(isinstance(result, dict), 'Invalid desktop response.', 'desktop_disconnected')
            if 'error' in result:
                code = result['error']
                if code not in {'desktop_permission_missing', 'desktop_window_ambiguous', 'desktop_surface_changed',
                                'desktop_observation_limit', 'sensitive_surface', 'desktop_capture_failed',
                                'stale_ref', 'desktop_action_uncertain', 'invalid_action', 'desktop_resize_unavailable', 'desktop_session_unavailable'}:
                    code = 'desktop_bridge_failed'
                raise ShowrunError(code, 'Desktop bridge stopped: ' + code + '.',
                                   'Check the prepared window and macOS Screen Recording/Accessibility permissions.')
            return result

    async def close(self):
        if self.server:
            self.server.close()
        if self.connection and not self.connection.done():
            self.connection.cancel()
        if self.writer:
            self.writer.close()  # EOF ends this app instance, never the target app.
            try:
                await asyncio.wait_for(self.writer.wait_closed(), 5)
            except (TimeoutError, OSError):
                pass
        if self.server:
            await asyncio.wait_for(self.server.wait_closed(), 6)
        if self.socket_dir:
            self.socket_dir.cleanup()
            self.socket_dir = None


class DesktopCapture(Capture):
    def __init__(self, folder, geometry, owner):
        super().__init__(folder, geometry)
        self.owner = owner
        self.task = None
        self.latest = None

    async def sample(self):
        result = await self.owner.bridge.call('screenshot')
        stamp = time.monotonic() + self.epoch_offset
        require((result.get('width'), result.get('height')) == (self.geometry['width'], self.geometry['height']),
                'Window pixel dimensions differ from capture; resize the prepared window explicitly.',
                'capture_geometry')
        data = base64.b64decode(result['png'], validate=True)
        require(data.startswith(b'\x89PNG\r\n\x1a\n'), 'Expected a native PNG.', 'capture_invalid')
        self.bytes += len(data)
        require(self.bytes <= 512 * 1024 * 1024, 'Capture reached its 512 MiB frame limit.', 'storage_limit')
        if self.origin is None:
            self.origin = stamp
        path = self.frame_dir / f'{len(self.frames):06d}.png'
        path.write_bytes(data)
        self.frames.append((stamp, path))
        self.latest = result['png']

    async def start(self):
        self.frame_dir = self.folder / 'frames'
        self.frame_dir.mkdir(mode=0o700)
        await self.sample()

        async def poll():
            try:
                while True:
                    await asyncio.sleep(.2)
                    await self.sample()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.error = exc if isinstance(exc, ShowrunError) else ShowrunError(
                    'capture_invalid', 'Native capture stopped.')
                if self.error.code == 'sensitive_surface':
                    self.owner.restricted = True

        self.task = asyncio.create_task(poll())

    async def finish(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if not self.frames:
            return None
        # Preserve decodable partial footage even when a later sample failed.
        error, self.error = self.error, None
        try:
            media = await self.finalize_frames(time.monotonic() + self.epoch_offset)
        finally:
            self.error = error
        media['timebase'].update(
            origin='first native window sample', precision_seconds=.5,
            method='local monotonic screenshot completion timestamps',
            limitations='Background window samples at up to 5 Hz plus paced-entry character samples; transient states and cursor may be missed. Timing is approximate.')
        if error:
            media['capture_interrupted'] = error.public()
        return media


class Desktop:
    hold_method = 'Visible accessibility text/state sampled during hold; not independent backend persistence.'
    timing_precision = .5

    def __init__(self, target, folder, geometry, secrets=(), bridge=None):
        self.target = target
        if target.config['kind'] == 'windows' and bridge is None:
            from .windows_desktop import WindowsBridge
            bridge = WindowsBridge()
        self.bridge = bridge or MacBridge()
        self.capture = DesktopCapture(folder, geometry, self)
        self.ui = None
        self.comment = None
        self.restricted = False
        self.secrets = [s for s in secrets if s]
        self.observation = None
        self.text_entry = "immediate"
        self.last_fill = None

    async def start(self, unused=None):
        await self.bridge.start(self.target.config)
        await self.observe()  # Catch secure fields before the first capture sample.
        if self.target.config.get('resize_to_capture', False):
            measured = await self.bridge.call('resize', **self.capture.geometry)
            actual = (measured.get('width'), measured.get('height'))
            requested = (self.capture.geometry['width'], self.capture.geometry['height'])
            require(actual == requested,
                    f'Requested {requested[0]}x{requested[1]} capture pixels; '
                    f'the window provides {actual[0]}x{actual[1]} after resizing. '
                    'The window remains at its resulting size.', 'capture_geometry')
            await self.observe()
        await self.capture.start()

    def check(self):
        if self.capture.error:
            raise self.capture.error

    async def observe(self):
        self.check()
        try:
            result = await self.bridge.call('observe')
        except ShowrunError as exc:
            self.restricted |= exc.code == 'sensitive_surface'
            raise
        if any(secret in json.dumps(result) for secret in self.secrets):
            self.restricted = True
            raise ShowrunError('sensitive_surface', 'Credential text appeared; footage is restricted.')
        controls = result['controls']
        for control in controls:
            control['actions'] = [a for a in control['actions'] if a in self.ui['actions']]
        result = {'generation': result['generation'], 'desktop': True, 'ui_authority': self.ui,
                  'frames': [{'frame': 0, 'text': result['text'], 'controls': controls}]}
        if self.capture.latest:
            result['screenshot_png'] = self.capture.latest
        self.observation = result
        return result

    @staticmethod
    def visible(observation, text):
        return text in observation['frames'][0]['text']

    async def matches(self, observation, step):
        if 'visible_text' in step and not self.visible(observation, step['visible_text']):
            return False
        controls = observation['frames'][0]['controls']
        for assertion in step.get('assertions', []):
            matches = [c for c in controls if c['label'] == assertion['label']]
            if assertion['kind'] == 'control':
                if bool(matches) != assertion['visible']:
                    return False
            elif assertion['kind'] == 'field':
                if len(matches) != 1 or matches[0].get('value') != assertion.get('value'):
                    return False
            else:
                return False
        return True

    def evidence(self, observation):
        return {'method': ('Windows UI Automation window observation' if self.target.config['kind'] == 'windows'
                           else 'macOS Accessibility window observation'),
                'generation': observation['generation'],
                'text': observation['frames'][0]['text'],
                'limitations': 'Visible accessibility state; does not prove persisted application state.'}

    async def validate_action(self, action, generation=None):
        self.check()
        name = action.get('action')
        if name in {'wait', 'fail'}:
            obj(action, {'action'}, {'action'})
            return name
        require(self.observation is not None and generation == self.observation['generation'],
                'Observation was superseded.', 'stale_ref')
        require(name in self.ui['actions'], 'Action outside desktop grant.', 'invalid_action')
        fields = {'action', 'ref'} | ({'text'} if name == 'fill' else set())
        obj(action, fields, fields)
        controls = self.observation['frames'][0]['controls']
        require(any(c['ref'] == action['ref'] and name in c['actions'] for c in controls),
                'Action requires a current accessible control.', 'stale_ref')
        if name == 'fill':
            require(action['text'] in self.ui['allowed_values'], 'Text is outside permitted values.', 'invalid_action')
            control = next(c for c in controls if c['ref'] == action['ref'])
            if control.get('fill_method') in {'focused_grid_keyboard', 'focused_text_keyboard'}:
                require(self.text_entry == 'immediate' and action['text'] and
                        not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in action['text']),
                        'Keyboard entry currently requires immediate, nonempty single-line text.', 'invalid_action')
            identity = (control.get('identity', control.get('label')), control.get('role'))
            if self.last_fill and self.last_fill[:3] == (*identity, action['text']):
                require(control.get('value') not in (self.last_fill[3], action['text']),
                        'Repeated fill would not advance the step; inspect the input path and outcome assertion.',
                        'desktop_no_progress')
        return name

    async def act(self, action, before_dispatch, generation):
        name = await self.validate_action(action, generation)
        if name == 'fail':
            raise ShowrunError('navigation_failed', 'Model could not resolve the desktop step.')
        before_dispatch(name)
        if name == 'fill':
            control = next(c for c in self.observation['frames'][0]['controls'] if c['ref'] == action['ref'])
            self.last_fill = (control.get('identity', control.get('label')), control.get('role'), action['text'], control.get('value'))
        elif name != 'wait':
            self.last_fill = None
        if name == 'wait':
            await asyncio.sleep(.2)
        elif name == 'fill' and self.text_entry in {'paced', 'fast_imperfect'}:
            require(self.target.config['kind'] == 'windows', 'Paced entry requires Windows.', 'invalid_action')
            result = await self.bridge.call('act', generation=generation, **action, incremental=True)
            await asyncio.sleep(.4)
            index = 0
            while result.get('status') == 'typing':
                self.check()
                result = await self.bridge.call('type_next')
                # Capture each displayed character before advancing, even at fast cadence.
                await self.capture.sample()
                index += 1
                character = result.get('character', '')
                delay = .22 if character in {'.', ',', ';', ':', '!', '?', '\n', '\r', '\r\n'} else .065 + (index % 3) * .02
                if result.get('status') == 'typing':
                    if self.text_entry == 'fast_imperfect':
                        delay = .12 if delay == .22 else .01
                        if index in {6, 23}:
                            await self.bridge.call('type_preview', suffix='x' if index == 6 else 'r')
                            await self.capture.sample()
                            await asyncio.sleep(.32)
                            await self.bridge.call('type_preview', suffix='')
                            await self.capture.sample()
                            await asyncio.sleep(.08)
                    await asyncio.sleep(delay)
        else:
            await self.bridge.call('act', generation=generation, **action)

    async def close(self):
        await self.bridge.close()  # Never close the caller's application/window.


async def preflight():
    require(sys.platform == 'darwin', 'Native desktop currently requires macOS.', 'desktop_unsupported')
    require(helper_path().is_file(), 'Run showrun prepare-desktop first.', 'desktop_not_prepared')
    require(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Install FFmpeg/ffprobe.', 'capture_dependency_missing')
    encoders = await command('ffmpeg', '-v', 'error', '-encoders', timeout=5)
    require(b'libx264' in encoders, 'FFmpeg needs libx264.', 'capture_dependency_missing')
