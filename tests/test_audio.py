"""Opt-in native audio with a scripted companion: validation, mux, signal and permission gaps.

No OS grants, model or real ScreenCaptureKit stream; the companion's placement core
is covered separately by its compile-time self-test on macOS hosts.
"""
import asyncio
import copy
import json
import math
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_desktop import Bridge, native_runtime, request  # noqa: F401
from test_showrun import MODEL

from amplifier_smart_tool_showrun import Showrun, audio, desktop
from amplifier_smart_tool_showrun.errors import ShowrunError
from amplifier_smart_tool_showrun.schema import validate

RATE = 48000


def tone(seconds, channels=2, frequency=440.0, amplitude=.5, silent_prefix=0.0, rate=RATE):
    frames = []
    for i in range(int(seconds * rate)):
        t = i / rate
        value = 0 if t < silent_prefix else int(amplitude * 32767 * math.sin(2 * math.pi * frequency * t))
        frames.append(struct.pack('<' + 'h' * channels, *([value] * channels)))
    return b''.join(frames)


class AudioBridge(Bridge):
    """Scripted companion that records a tone (or silence) into the Showrun-created directory."""
    capabilities = ['permissions', 'bind', 'observe', 'screenshot', 'click', 'fill',
                    'audio_output', 'audio_microphone', 'request_microphone']
    grants = {'screen_recording': True, 'accessibility': True, 'microphone': 'authorized'}
    amplitude = .5
    interrupted = None

    def __init__(self):
        super().__init__()
        self.companion = {'version': 'desktop-v0.6.0', 'protocol': 1, 'capabilities': list(self.capabilities)}
        self.calls = []

    async def call(self, operation, _timeout=10, **payload):
        self.calls.append(operation)
        if operation == 'permissions':
            return dict(self.grants, bundle_id='org.showrun.desktop')
        if operation == 'request_microphone':
            assert _timeout >= 60  # the person answers a macOS prompt
            return {'microphone': 'authorized'}
        if operation == 'audio_start':
            self.audio = payload
            directory = Path(payload['directory'])
            assert directory.is_absolute() and directory.stat().st_mode & 0o077 == 0
            self.anchor = time.time()
            return {'status': 'recording', 'anchor_unix': self.anchor, 'scope': payload['output'],
                    'sources': ['output'] + (['microphone'] if payload['microphone'] else []),
                    'applications': ['org.showrun.fixture'],
                    **({'microphone_device': {'name': 'Test Input', 'unique_id': 'test-uid',
                                              'selection': 'explicit'}} if payload['microphone'] else {})}
        if operation == 'audio_stop':
            stop = time.time()
            seconds = stop - self.anchor
            directory = Path(self.audio['directory'])
            sources = []
            for kind, channels in [('output', 2)] + ([('microphone', 1)] if self.audio['microphone'] else []):
                data = tone(seconds, channels, 440 if kind == 'output' else 660, self.amplitude)
                (directory / f'{kind}.s16le').write_bytes(data)
                frames = len(data) // (2 * channels)
                sources.append({'kind': kind, 'file': f'{kind}.s16le', 'encoding': 's16le',
                                'sample_rate': RATE, 'channels': channels,
                                'buffers': frames // 1024, 'frames': frames, 'captured_frames': frames,
                                'gap_frames': 0, 'dropped_frames': 0,
                                'peak_dbfs': -6.0 if self.amplitude else None, 'rms_dbfs': None,
                                'first_buffer_seconds': .02, 'timestamp_basis': 'host_clock'})
            result = {'status': 'stopped', 'anchor_unix': self.anchor, 'stop_unix': stop,
                      'scope': self.audio['output'], 'sources': sources,
                      'applications': ['org.showrun.fixture', 'org.showrun.fixture.helper'], 'filter_updates': 1}
            if self.interrupted:
                result['interrupted'] = self.interrupted
            return result
        return await super().call(operation, **payload)


def audio_request(**audio_options):
    value = request()
    value['request_id'] = 'native-audio'
    value['capture']['audio'] = {'output': 'application', **audio_options}
    return value


def ffprobe(path):
    return json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(path)],
                                     check=True, capture_output=True).stdout)['streams']


@pytest.fixture
def audio_runtime(native_runtime, monkeypatch):  # noqa: F811
    monkeypatch.setattr(desktop, 'MacBridge', AudioBridge)
    AudioBridge.instances = []
    AudioBridge.amplitude = .5
    AudioBridge.interrupted = None
    AudioBridge.grants = {'screen_recording': True, 'accessibility': True, 'microphone': 'authorized'}


# ---- request boundary ----

def test_audio_is_opt_in_and_absent_by_default():
    effective = validate(request(), MODEL)
    assert 'audio' not in effective['capture']
    assert validate(audio_request(), MODEL)['capture']['audio'] == {'output': 'application'}


@pytest.mark.parametrize('target', [
    {'kind': 'url', 'url': 'http://127.0.0.1:8080/', 'origins': ['http://127.0.0.1:8080']},
    {'kind': 'windows', 'pid': 10, 'window_title': 'Fixture'},
])
def test_audio_on_unsupported_surfaces_fails_explicitly(target):
    value = audio_request()
    value['target'] = target
    if target['kind'] == 'url':
        value['authority'].update(disclose_dom=True)
        value['authority'].pop('disclose_accessibility')
        value['authority'].pop('disclose_screenshots')
    with pytest.raises(ShowrunError) as caught:
        validate(value, MODEL)
    assert caught.value.code == 'audio_unsupported'
    assert 'silent' in caught.value.message


@pytest.mark.parametrize('options', [
    {'output': 'speakers'},
    {'output': 'system', 'include_bundle_ids': ['com.example.Helper']},
    {'output': 'application', 'include_bundle_ids': []},
    {'output': 'application', 'include_bundle_ids': ['bad id']},
    {'output': 'application', 'microphone_device': 'BlackHole 2ch'},
    {'output': 'application', 'microphone': 'yes'},
    {'output': 'application', 'require_signal': 1},
    {'output': 'application', 'volume': 3},
    {'microphone': True},
])
def test_audio_options_are_bounded(options):
    value = request()
    value['capture']['audio'] = options
    with pytest.raises(ShowrunError) as caught:
        validate(value, MODEL)
    assert caught.value.code == 'invalid_request'


# ---- recorded lifecycle ----

def test_record_with_application_audio_muxes_verified_track(tmp_path, audio_runtime):
    api = Showrun(tmp_path, MODEL)
    result = api.record(audio_request(include_bundle_ids=['com.microsoft.edgemac']))
    assert result['status'] == 'succeeded', result
    bridge = AudioBridge.instances[0]
    # Audio starts before the first sample and stops after the last one.
    assert bridge.calls.index('audio_start') < bridge.calls.index('screenshot')
    assert bridge.calls[-1] == 'audio_stop' or 'audio_stop' in bridge.calls
    assert bridge.audio['include_bundle_ids'] == ['com.microsoft.edgemac']
    report = result['audio']
    assert report['status'] == 'captured' and report['scope'] == 'application'
    assert report['permissions'] == {'screen_recording': True, 'microphone': 'not_requested'}
    [source] = report['sources']
    assert source['kind'] == 'output' and source['sample_rate'] == RATE and source['channels'] == 2
    assert source['signal'] == 'present' and source['max_volume_db'] > -10
    assert report['track']['signal'] == 'present' and report['track']['sample_rate'] == RATE
    assert report['sync']['precision_seconds'] == .3 and report['sync']['audio_trim_seconds'] >= 0
    assert 'org.showrun.fixture.helper' in report['applications']
    media = result['media']
    assert media['audio'] == 'aac' and media['audio_stream']['channels'] == 2
    take = tmp_path / 'native-audio'
    streams = ffprobe(take / 'capture.mp4')
    assert sorted(s['codec_type'] for s in streams) == ['audio', 'video']
    assert not (take / 'audio').exists() and not (take / 'capture.audio.mp4').exists()
    assert any('receipt.audio' in item for item in result['limitations'])
    inspected = api.inspect('native-audio')['inspection']
    assert inspected['audio'] == 'aac' and inspected['sha256'] == media['sha256']


def test_microphone_is_mixed_and_reported(tmp_path, audio_runtime):
    result = Showrun(tmp_path, MODEL).record(audio_request(microphone=True, microphone_device='Test Input'))
    assert result['status'] == 'succeeded', result
    report = result['audio']
    assert [s['kind'] for s in report['sources']] == ['output', 'microphone']
    assert report['sources'][1]['channels'] == 1 and report['sources'][1]['signal'] == 'present'
    assert report['microphone_device'] == {'name': 'Test Input', 'unique_id': 'test-uid', 'selection': 'explicit'}
    assert report['permissions']['microphone'] == 'authorized'
    assert AudioBridge.instances[0].audio['microphone_device'] == 'Test Input'


def test_silent_audio_fails_by_default_but_keeps_labeled_footage(tmp_path, audio_runtime):
    AudioBridge.amplitude = 0
    result = Showrun(tmp_path, MODEL).record(audio_request())
    assert result['status'] == 'failed' and result['error']['code'] == 'audio_no_signal'
    assert result['audio']['sources'][0]['signal'] == 'silent'
    assert result['audio']['track']['signal'] == 'silent'
    assert result['media']['audio'] == 'aac' and result['partial'] is True


def test_expected_silence_can_be_waived_and_is_still_reported(tmp_path, audio_runtime):
    AudioBridge.amplitude = 0
    result = Showrun(tmp_path, MODEL).record(audio_request(require_signal=False))
    assert result['status'] == 'succeeded', result
    assert result['audio']['track']['signal'] == 'silent'


def test_mid_take_stream_loss_is_reported(tmp_path, audio_runtime):
    AudioBridge.interrupted = 'audio_stream_stopped'
    result = Showrun(tmp_path, MODEL).record(audio_request())
    assert result['status'] == 'failed' and result['error']['code'] == 'audio_interrupted'
    assert result['audio']['interrupted'] == 'audio_stream_stopped'
    assert result['media']['audio'] == 'aac'


def test_old_companion_without_audio_fails_before_any_input(tmp_path, native_runtime):  # noqa: F811
    result = Showrun(tmp_path, MODEL).record(audio_request())
    assert result['status'] == 'failed' and result['error']['code'] == 'audio_unavailable'
    assert result['error']['diagnostics']['missing_capabilities'] == ['audio_output']
    assert result['audio']['status'] == 'unavailable'
    assert result['media'] is None and not Bridge.instances[0].actions


@pytest.mark.parametrize('grants,missing', [
    ({'screen_recording': False, 'accessibility': True, 'microphone': 'authorized'}, ['screen_recording']),
    ({'screen_recording': True, 'accessibility': True, 'microphone': 'not_determined'}, ['microphone']),
])
def test_permission_gaps_fail_before_any_input(tmp_path, audio_runtime, grants, missing):
    AudioBridge.grants = grants
    result = Showrun(tmp_path, MODEL).record(audio_request(microphone=True))
    assert result['status'] == 'failed' and result['error']['code'] == 'audio_permission_missing'
    assert result['error']['diagnostics']['missing_permissions'] == missing
    assert 'request-microphone' in result['error']['remedy']
    bridge = AudioBridge.instances[0]
    assert 'audio_start' not in bridge.calls and not bridge.actions and result['media'] is None


def test_mux_failure_keeps_video_only_media_and_fails(tmp_path, audio_runtime, monkeypatch):
    async def broken(*args, **kwargs):
        raise ShowrunError('media_invalid', 'Audio processing failed.')
    monkeypatch.setattr(audio, 'mux', broken)
    result = Showrun(tmp_path, MODEL).record(audio_request())
    assert result['status'] == 'failed' and result['error']['code'] == 'audio_failed'
    assert result['media']['audio'] == 'none' and result['audio']['status'] == 'failed'
    assert (tmp_path / 'native-audio' / 'audio' / 'output.s16le').is_file()


# ---- mux alignment, independent of the recorded lifecycle ----

def test_mux_trims_audio_to_the_video_origin(tmp_path):
    video = tmp_path / 'capture.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=320x240:r=25:d=2',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], check=True)
    (tmp_path / 'audio').mkdir()
    # One second of silence, then the tone: the video origin is exactly at the tone onset.
    (tmp_path / 'audio' / 'output.s16le').write_bytes(tone(3, silent_prefix=1.0))
    frames = 3 * RATE
    stopped = {'scope': 'system', 'stop_unix': 1003.0, 'applications': [], 'filter_updates': 0,
               'sources': [{'kind': 'output', 'file': 'output.s16le', 'sample_rate': RATE, 'channels': 2,
                            'captured_frames': frames, 'frames': frames}]}

    def first_window(origin):
        report, output = asyncio.run(audio.mux(tmp_path, video, {'anchor_unix': 1000.0}, stopped, origin, 2.0))
        head = asyncio.run(audio.levels(['-t', '0.4', '-i', str(output)]))
        duration = float(json.loads(subprocess.run(
            ['ffprobe', '-v', 'error', '-show_format', '-of', 'json', str(output)],
            capture_output=True, check=True).stdout)['format']['duration'])
        assert abs(duration - 2.0) < .1
        return report, head
    report, head = first_window(1001.0)
    assert report['sync']['audio_trim_seconds'] == 1.0 and head['max_volume_db'] > -10
    _, head = first_window(1000.0)
    assert head['max_volume_db'] is None or head['max_volume_db'] < -60


def test_no_buffers_produce_declared_silent_track(tmp_path):
    video = tmp_path / 'capture.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=320x240:r=25:d=1',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], check=True)
    (tmp_path / 'audio').mkdir()
    (tmp_path / 'audio' / 'output.s16le').write_bytes(b'')
    stopped = {'scope': 'application', 'stop_unix': 1.0, 'applications': ['x'],
               'sources': [{'kind': 'output', 'file': 'output.s16le', 'sample_rate': RATE, 'channels': 2,
                            'captured_frames': 0, 'frames': 0, 'buffers': 0}]}
    report, output = asyncio.run(audio.mux(tmp_path, video, {'anchor_unix': 0.0}, stopped, 0.1, 1.0))
    assert report['sources'][0]['signal'] == 'none_received'
    assert audio.outcome(dict(report, status='captured')).code == 'audio_no_signal'
    assert [s['codec_type'] for s in ffprobe(output)].count('audio') == 1


def test_source_statistics_are_bounded():
    cleaned = audio.clean_source({'kind': 'output', 'sample_rate': RATE, 'file': '/private/path',
                                  'error': 'Some Raw Text', 'peak_dbfs': -3.2, 'device': 'x', 'buffers': True})
    assert cleaned == {'kind': 'output', 'sample_rate': RATE, 'peak_dbfs': -3.2}


# ---- setup surface ----

def test_desktop_status_reports_audio_and_explicit_microphone_request(monkeypatch):
    if sys.platform != 'darwin':
        pytest.skip('Audio readiness is reported by the macOS companion.')

    class StatusBridge(AudioBridge):
        async def start(self, target=None):
            return {**await self.call('permissions'), 'companion': self.companion}
    monkeypatch.setattr(desktop, 'MacBridge', StatusBridge)
    AudioBridge.grants = {'screen_recording': True, 'accessibility': True, 'microphone': 'not_determined'}
    AudioBridge.instances = []
    status = Showrun.desktop_status()
    assert status['audio'] == {'output_ready': True, 'microphone_ready': False, 'microphone_requested': False}
    assert 'request_microphone' not in AudioBridge.instances[-1].calls
    status = Showrun.desktop_status(request_microphone=True)
    assert status['microphone'] == 'authorized' and status['audio']['microphone_ready'] is True
    assert 'request_microphone' in AudioBridge.instances[-1].calls


def test_companion_plist_declares_microphone_use_and_version():
    source = (Path(desktop.__file__).parent / 'native' / 'macos.swift').read_text()
    assert f'"version": "{desktop.COMPANION_VERSION}"' in source
    assert '"audio_output"' in source and 'excludesCurrentProcessAudio = true' in source
    assert 'NSMicrophoneUsageDescription' in Path(desktop.__file__).read_text()


@pytest.mark.skipif(sys.platform != 'darwin' or not shutil.which('xcrun'), reason='Needs macOS swiftc.')
def test_companion_audio_placement_self_test(tmp_path):
    source = Path(desktop.__file__).parent / 'native' / 'macos.swift'
    binary = tmp_path / 'selftest'
    subprocess.run(['xcrun', 'swiftc', '-parse-as-library', '-D', 'SHOWRUN_SELFTEST', str(source),
                    '-o', str(binary)], check=True, capture_output=True, timeout=300)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and json.loads(result.stdout) == {'failures': [], 'passed': True}


def test_restricted_take_moves_audio_evidence(tmp_path, audio_runtime, monkeypatch):
    class Sensitive(AudioBridge):
        async def call(self, operation, _timeout=10, **payload):
            if operation == 'observe' and self.generation >= 2:
                raise ShowrunError('sensitive_surface', 'secure field')
            return await super().call(operation, **payload)
    monkeypatch.setattr(desktop, 'MacBridge', Sensitive)
    result = Showrun(tmp_path, MODEL).record(copy.deepcopy(audio_request()))
    assert result['restricted'] is True and result['media'] is None
    take = tmp_path / 'native-audio'
    assert not (take / 'capture.mp4').exists() and not (take / 'audio').exists()
