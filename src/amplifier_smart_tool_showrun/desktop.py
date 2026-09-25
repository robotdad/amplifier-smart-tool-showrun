"""First native backend: a prepared macOS window, AX actions, sampled window PNGs.

The bridge supplies observations and executes validated actions. The Showrun
library and Navigator retain decisions, budgets, recording and outcome checks.
"""

import asyncio
import base64
import hashlib
import json
import math
import os
import platform
import plistlib
import re
import secrets
import shutil
import sys
import tempfile
import time
from importlib.resources import files
from pathlib import Path

from . import audio, diagnostics
from .capture import Capture, command, inspect_media
from .errors import ShowrunError, require
from .schema import obj

BUNDLE_ID = 'org.showrun.desktop'
COMPANION_VERSION = 'desktop-v0.6.0'
MICROPHONE_STATES = {'authorized', 'denied', 'restricted', 'not_determined'}


def companion_info(hello):
    """Missing legacy handshake metadata is unknown, never inferred from the pin."""
    info = hello.get('companion')
    if not isinstance(info, dict):
        info = {}
    version, protocol, capabilities = info.get('version'), info.get('protocol'), info.get('capabilities')
    return {
        'version': version if isinstance(version, str) and re.fullmatch(r'[a-zA-Z0-9._+-]{1,64}', version)
        else 'unknown',
        'protocol': 'unknown' if 'protocol' not in info else
        protocol if type(protocol) is int and 1 <= protocol <= 1000 else 'invalid',
        'capabilities': [c for c in capabilities[:32]
                         if isinstance(c, str) and re.fullmatch(r'[a-z0-9_]{1,64}', c)]
        if isinstance(capabilities, list) else 'unknown',
    }


def window_diagnostics(result):
    """Keep only bounded numeric metadata for the requested app, never window text."""
    source = result.get('diagnostics', {})
    if not isinstance(source, dict):
        return {}
    clean = {}
    for key in ('candidate_count', 'match_count', 'ax_window_count', 'ax_match_count'):
        value = source.get(key)
        if type(value) is int and 0 <= value <= 100000:
            clean[key] = value
    candidates = source.get('candidates')
    if isinstance(candidates, list):
        clean['candidates'] = []
        for candidate in candidates[:8]:
            if not isinstance(candidate, dict):
                continue
            row = {}
            for key in ('window_id', 'width', 'height'):
                value = candidate.get(key)
                if type(value) is int and 0 <= value <= 2**32 - 1:
                    row[key] = value
            if type(candidate.get('title_matches')) is bool:
                row['title_matches'] = candidate['title_matches']
            clean['candidates'].append(row)
    return clean


def audio_diagnostics(result):
    """Bounded audio failure details: reasons, permission states and counts only."""
    source = result.get('diagnostics', {})
    if not isinstance(source, dict):
        return {}
    clean = {}
    if isinstance(source.get('reason'), str) and re.fullmatch(r'[a-z0-9_]{1,64}', source['reason']):
        clean['reason'] = source['reason']
    if source.get('microphone') in MICROPHONE_STATES:
        clean['microphone'] = source['microphone']
    if source.get('screen_recording') is False:
        clean['screen_recording'] = False
    for key in ('match_count', 'device_count', 'code'):
        if type(source.get(key)) is int and -100000 <= source[key] <= 100000:
            clean[key] = source[key]
    return clean


AUDIO_REMEDIES = {
    'audio_permission_missing': 'Grant Showrun Desktop Screen & System Audio Recording (output audio) and, for '
                                'microphone capture, Microphone via `showrun desktop-status --request-microphone`; '
                                'then recheck desktop-status. No UI input was sent.',
    'audio_unavailable': 'Output audio needs companion desktop-v0.6.0+; microphone capture also needs macOS 15+. '
                         'Run showrun prepare-desktop, re-grant permissions and recheck desktop-status.',
    'audio_device_not_found': 'Name exactly one audio input by its device name or unique ID, or omit '
                              'microphone_device to use the system default input.',
    'audio_start_failed': 'Check that the target app is running and that the take directory is writable; '
                          'for browser helpers, consider output: system. No UI input was sent.',
    'audio_not_running': 'Audio was not running; inspect the receipt audio block.',
}


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
                and metadata.get('ShowrunCompanionVersion') == COMPANION_VERSION
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
                    'CFBundlePackageType': 'APPL', 'CFBundleVersion': COMPANION_VERSION.removeprefix('desktop-v'),
                    'CFBundleShortVersionString': COMPANION_VERSION.removeprefix('desktop-v'),
                    'ShowrunCompanionVersion': COMPANION_VERSION, 'LSMinimumSystemVersion': '14.0',
                    'LSUIElement': True, 'ShowrunSourceSHA256': digest,
                    'NSMicrophoneUsageDescription': 'Showrun records the microphone only when a take explicitly '
                                                    'requests capture.audio.microphone.',
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
        self.companion = companion_info({})

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
                if (not isinstance(hello, dict) or not isinstance(hello.get('token'), str)
                        or not re.fullmatch(r'[0-9a-f]{64}', hello['token'])
                        or not secrets.compare_digest(hello['token'], token) or self.connection.done()):
                    writer.close()
                    return
                self.connection.set_result((reader, writer, companion_info(hello)))
            except (ValueError, TimeoutError):
                writer.close()

        self.server = await asyncio.start_unix_server(accept, socket_path, limit=34 * 1024 * 1024)
        try:
            await command('/usr/bin/open', '-n', '-g', '-a', str(app_path()), '--args',
                          '--socket', socket_path, '--token', token, timeout=10)
            self.reader, self.writer, self.companion = await asyncio.wait_for(self.connection, 15)
            self.server.close()
            if self.companion['protocol'] not in {1, 'unknown'}:
                raise ShowrunError(
                    'desktop_protocol_unsupported',
                    'Companion protocol is incompatible with this Showrun installation.',
                    'Align the companion and Python installation versions, then rerun desktop-status. '
                    'No target command was sent.',
                    diagnostics={'companion': self.companion})
        except TimeoutError:
            await self.close()
            raise ShowrunError('desktop_launch_failed', 'The companion did not connect.',
                               'Check macOS Privacy & Security for a blocked launch, then run desktop-status.') from None
        except BaseException:
            await self.close()
            raise
        if target is None:
            return {**await self.call('permissions'), 'companion': self.companion}
        return await self.call('bind', bundle_id=target['bundle_id'], window_title=target.get('window_title'),
                               input_mode=target.get('input_mode', 'controls'))

    async def call(self, operation, _timeout=10, **payload):
        async with self.lock:
            require(self.writer is not None and not self.writer.is_closing(),
                    'Desktop connection is unavailable.', 'desktop_disconnected')
            self.writer.write((json.dumps({'operation': operation, **payload}) + '\n').encode())
            await self.writer.drain()
            try:
                line = await asyncio.wait_for(self.reader.readline(), _timeout)
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
                if not isinstance(code, str) or code not in {
                                'desktop_permission_missing', 'desktop_window_ambiguous', 'desktop_surface_changed',
                                'desktop_window_not_found', 'desktop_ax_window_not_found', 'desktop_ax_window_ambiguous',
                                'desktop_observation_limit', 'sensitive_surface', 'desktop_capture_failed',
                                'stale_ref', 'desktop_action_uncertain', 'invalid_action', 'desktop_resize_unavailable', 'desktop_session_unavailable',
                                'audio_permission_missing', 'audio_unavailable', 'audio_device_not_found',
                                'audio_start_failed', 'audio_not_running'}:
                    code = 'desktop_bridge_failed'
                remedies = {
                    'desktop_window_not_found': 'Open an eligible on-screen window in the requested app; '
                                                'check the exact initial title or omit it only for a single window.',
                    'desktop_window_ambiguous': 'Select an exact unique initial title or leave only one eligible '
                                                'window in the requested app.',
                    'desktop_ax_window_not_found': 'The capture window has no matching Accessibility window. '
                                                   'Check Accessibility permission and app accessibility support.',
                    'desktop_ax_window_ambiguous': 'The capture window matches multiple Accessibility windows. '
                                                   'Prepare a unique title in the requested app; no input was sent.',
                }
                if code in AUDIO_REMEDIES:
                    raise ShowrunError(code, 'Desktop audio stopped: ' + code + '.', AUDIO_REMEDIES[code],
                                       diagnostics=audio_diagnostics(result))
                raise ShowrunError(code, 'Desktop bridge stopped: ' + code + '.',
                                   remedies.get(code, 'Check the prepared window and macOS '
                                                'Screen Recording/Accessibility permissions.'),
                                   diagnostics=window_diagnostics(result) if code in remedies else None)
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
        self.audio = geometry.get('audio')
        self.audio_started = None
        self.audio_report = {'requested': dict(self.audio), 'status': 'not_started'} if self.audio else None

    async def preflight_audio(self):
        """Before window changes or UI input: missing capability or permission fails explicitly."""
        if not self.audio:
            return
        companion = getattr(self.owner.bridge, 'companion', None) or {}
        capabilities = companion.get('capabilities')
        needed = ['audio_output'] + (['audio_microphone'] if self.audio.get('microphone') else [])
        missing = [c for c in needed if not isinstance(capabilities, list) or c not in capabilities]
        if missing:
            error = ShowrunError(
                'audio_unavailable', 'The desktop companion cannot capture the requested audio.',
                AUDIO_REMEDIES['audio_unavailable'],
                diagnostics={'missing_capabilities': missing, 'companion_version': companion.get('version', 'unknown')})
            self.audio_report.update(status='unavailable', error=error.public())
            raise error
        permissions = await self.owner.bridge.call('permissions')
        microphone = permissions.get('microphone')
        grants = {'screen_recording': permissions.get('screen_recording') is True,
                  'microphone': (microphone if microphone in MICROPHONE_STATES else 'unknown')
                  if self.audio.get('microphone') else 'not_requested'}
        self.audio_report['permissions'] = grants
        gaps = ([] if grants['screen_recording'] else ['screen_recording']) + (
            ['microphone'] if self.audio.get('microphone') and grants['microphone'] != 'authorized' else [])
        if gaps:
            error = ShowrunError('audio_permission_missing', 'Audio capture lacks macOS permission: '
                                 + ', '.join(gaps) + '.', AUDIO_REMEDIES['audio_permission_missing'],
                                 diagnostics={'missing_permissions': gaps, **grants})
            self.audio_report.update(status='unavailable', error=error.public())
            raise error

    async def start_audio(self):
        directory = self.folder / 'audio'
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)
        payload = {'directory': str(directory.resolve()), 'output': self.audio['output'],
                   'microphone': self.audio.get('microphone', False)}
        for key in ('include_bundle_ids', 'microphone_device'):
            if key in self.audio:
                payload[key] = self.audio[key]
        try:
            started = await self.owner.bridge.call('audio_start', **payload)
        except ShowrunError as exc:
            self.audio_report.update(status='unavailable', error=exc.public())
            raise
        anchor = started.get('anchor_unix')
        if type(anchor) not in {int, float} or not math.isfinite(anchor):
            error = ShowrunError('audio_start_failed', 'Companion returned an invalid audio anchor.',
                                 AUDIO_REMEDIES['audio_start_failed'])
            self.audio_report.update(status='unavailable', error=error.public())
            raise error
        self.audio_started = started
        device = started.get('microphone_device')
        if isinstance(device, dict):
            self.audio_report['microphone_device'] = {
                k: device[k][:300] for k in ('name', 'unique_id', 'selection') if isinstance(device.get(k), str)}
        self.audio_report['status'] = 'recording'

    async def stop_audio(self):
        if not self.audio_started:
            return None
        try:
            return await self.owner.bridge.call('audio_stop')
        except ShowrunError as exc:
            self.audio_report.update(status='failed', error=exc.public())
            return None

    async def attach_audio(self, media, stopped):
        """Mux verified audio into the delivered MP4; on failure keep video-only media and say so."""
        candidate = self.folder / 'capture.audio.mp4'
        try:
            report, candidate = await audio.mux(self.folder, self.folder / 'capture.mp4', self.audio_started,
                                                stopped, self.origin, media['duration_seconds'])
            inspected = await inspect_media(candidate)
            require(inspected['audio'] == 'aac' and (inspected['width'], inspected['height'])
                    == (media['width'], media['height'])
                    and abs(inspected['duration_seconds'] - media['duration_seconds']) <= .15,
                    'Muxed audio media did not verify.', 'media_invalid')
            audio.replace_media(self.folder, candidate)
            for key in ('sha256', 'bytes', 'container', 'duration_seconds', 'audio', 'audio_stream'):
                media[key] = inspected[key]
            timing = media.get('timing')
            if timing:
                timing['duration_delta_seconds'] = media['duration_seconds'] - timing['captured_seconds']
            self.audio_report.update(report)
            if isinstance(stopped.get('interrupted'), str) and re.fullmatch(r'[a-z0-9_]{1,64}', stopped['interrupted']):
                self.audio_report['interrupted'] = stopped['interrupted']
        except ShowrunError as exc:
            candidate.unlink(missing_ok=True)
            self.audio_report.update(status='failed', error=exc.public(),
                                     notice='Delivered footage has no audio track; raw PCM retained privately.')

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
        if self.audio:
            await self.start_audio()  # before the first sample, so audio covers media time zero
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
        end = time.monotonic() + self.epoch_offset
        stopped = await self.stop_audio()  # stops after the video end, so audio covers it
        if not self.frames:
            if stopped is not None:
                shutil.rmtree(self.folder / 'audio', ignore_errors=True)
                self.audio_report['status'] = 'discarded_without_footage'
            return None
        # Preserve decodable partial footage even when a later sample failed.
        error, self.error = self.error, None
        try:
            media = await self.finalize_frames(end)
        finally:
            self.error = error
        media['timebase'].update(
            origin='first native window sample', precision_seconds=.5,
            method='local monotonic screenshot completion timestamps',
            limitations='Background window samples at up to 5 Hz plus paced-entry character samples; transient states and cursor may be missed. Timing is approximate.')
        if error:
            media['capture_interrupted'] = error.public()
        if stopped is not None:
            await self.attach_audio(media, stopped)
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
        self.last_terminal_input = None

    async def start(self, unused=None):
        await self.bridge.start(self.target.config)
        await self.capture.preflight_audio()  # explicit audio gaps fail before any window change
        await self.observe()  # Catch secure fields before the first capture sample.
        if self.target.config.get('resize_to_capture', False):
            measured = await self.bridge.call('resize', width=self.capture.geometry['width'],
                                              height=self.capture.geometry['height'])
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
        result = {'generation': result['generation'], 'desktop': True, 'terminal': self.target.config.get('input_mode') == 'terminal', 'ui_authority': self.ui,
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
        fields = {'action', 'ref'} | ({'text'} if name in {'fill', 'type'} else {'key'} if name == 'key' else set())
        obj(action, fields, fields)
        controls = self.observation['frames'][0]['controls']
        require(any(c['ref'] == action['ref'] and name in c['actions'] for c in controls),
                'Action requires a current accessible control.', 'stale_ref')
        if name in {'type', 'key'}:
            require(self.target.config.get('input_mode') == 'terminal',
                    'Terminal input requires explicit target mode.', 'invalid_action')
            require(self.last_terminal_input != (name, action.get('text', action.get('key'))),
                    'Repeated terminal input requires an intervening different input; inspect the result.',
                    'desktop_no_progress')
            if name == 'type':
                require(action['text'] in self.ui['allowed_values'] and
                        not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in action['text']),
                        'Terminal text is outside the exact input grant.', 'invalid_action')
            else:
                require(action['key'] in self.ui['allowed_keys'],
                        'Terminal key is outside the explicit key grant.', 'invalid_action')
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
        if name in {'type', 'key'}:
            self.last_terminal_input = (name, action.get('text', action.get('key')))
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
    await diagnostics.preflight("macos")
