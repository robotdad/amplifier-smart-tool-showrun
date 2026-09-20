"""First native backend: a prepared macOS window, AX actions, sampled window PNGs.

The bridge supplies observations and executes validated actions. The Showrun
library and Navigator retain decisions, budgets, recording and outcome checks.
"""

import asyncio
import base64
import hashlib
import json
import os
import shutil
import sys
import time
from importlib.resources import files
from pathlib import Path

from .capture import Capture, command
from .errors import ShowrunError, require
from .schema import obj


def helper_path():
    source = files(__package__).joinpath('native/macos.swift').read_bytes()
    digest = hashlib.sha256(source).hexdigest()[:20]
    return Path('~/Library/Caches/showrun').expanduser() / digest / 'showrun-desktop'


async def prepare():
    require(sys.platform == 'darwin', 'Native desktop currently requires macOS 14+.', 'desktop_unsupported')
    require(shutil.which('xcrun'), 'Install Apple Command Line Tools before prepare-desktop.',
            'desktop_dependency_missing')
    destination = helper_path()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_name(f'build-{os.getpid()}')
    try:
        await command('xcrun', 'swiftc', '-parse-as-library',
                      str(files(__package__).joinpath('native/macos.swift')), '-o', str(temporary), timeout=120)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {'status': 'prepared', 'helper': str(destination), 'model_calls': 0,
            'notice': 'Grant Screen Recording and Accessibility to this helper in macOS settings before capture.'}


class MacBridge:
    def __init__(self):
        self.process = None
        self.lock = asyncio.Lock()

    async def start(self, target):
        require(sys.platform == 'darwin', 'Native desktop currently requires macOS.', 'desktop_unsupported')
        require(helper_path().is_file(), 'Run showrun prepare-desktop first.', 'desktop_not_prepared')
        self.process = await asyncio.create_subprocess_exec(
            str(helper_path()), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=34 * 1024 * 1024,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(Path.home())})
        return await self.call('bind', bundle_id=target['bundle_id'], window_title=target['window_title'])

    async def call(self, operation, **payload):
        async with self.lock:
            require(self.process is not None and self.process.returncode is None,
                    'Desktop connection is unavailable.', 'desktop_disconnected')
            self.process.stdin.write((json.dumps({'operation': operation, **payload}) + '\n').encode())
            await self.process.stdin.drain()
            try:
                line = await asyncio.wait_for(self.process.stdout.readline(), 10)
                result = json.loads(line)
            except asyncio.CancelledError:
                self.process.stdin.close()
                raise
            except (TimeoutError, ValueError):
                # Do not issue another RPC after losing request/response alignment.
                self.process.stdin.close()
                raise ShowrunError('desktop_disconnected', 'Desktop acknowledgment was lost; do not replay.') from None
            require(isinstance(result, dict), 'Invalid desktop response.', 'desktop_disconnected')
            if 'error' in result:
                code = result['error']
                if code not in {'desktop_permission_missing', 'desktop_window_ambiguous', 'desktop_surface_changed',
                                'desktop_observation_limit', 'sensitive_surface', 'desktop_capture_failed',
                                'stale_ref', 'desktop_action_uncertain', 'invalid_action'}:
                    code = 'desktop_bridge_failed'
                raise ShowrunError(code, 'Desktop bridge stopped: ' + code + '.',
                                   'Check the prepared window and macOS Screen Recording/Accessibility permissions.')
            return result

    async def close(self):
        if self.process and self.process.returncode is None:
            self.process.stdin.close()
            try:
                # Drain a possible final screenshot so the helper cannot block
                # on a full pipe while trying to observe EOF.
                await asyncio.wait_for(self.process.stdout.read(), 5)
                await asyncio.wait_for(self.process.wait(), 2)
            except TimeoutError:
                # This is our unreaped asyncio child, never a retained PID/app.
                if self.process.returncode is None:
                    self.process.kill()
                    await asyncio.wait_for(self.process.wait(), 2)


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
            limitations='Window samples at up to 5 Hz; transient states and cursor are not captured. Timing is approximate.')
        if error:
            media['capture_interrupted'] = error.public()
        return media


class Desktop:
    hold_method = 'Visible accessibility text/state sampled during hold; not independent backend persistence.'
    timing_precision = .5

    def __init__(self, target, folder, geometry, secrets=(), bridge=None):
        self.target = target
        self.bridge = bridge or MacBridge()
        self.capture = DesktopCapture(folder, geometry, self)
        self.ui = None
        self.comment = None
        self.restricted = False
        self.secrets = [s for s in secrets if s]
        self.observation = None

    async def start(self, unused=None):
        await self.bridge.start(self.target.config)
        await self.observe()  # Catch secure fields before the first capture sample.
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

    @staticmethod
    def evidence(observation):
        return {'method': 'macOS Accessibility window observation',
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
        return name

    async def act(self, action, before_dispatch, generation):
        name = await self.validate_action(action, generation)
        if name == 'fail':
            raise ShowrunError('navigation_failed', 'Model could not resolve the desktop step.')
        before_dispatch(name)
        if name == 'wait':
            await asyncio.sleep(.2)
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
