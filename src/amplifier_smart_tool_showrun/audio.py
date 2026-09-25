"""Opt-in native audio: companion raw PCM becomes one verified AAC track in capture.mp4.

The companion writes host-clock-placed s16le PCM per source. This module maps its
anchor onto the video origin, mixes and trims to the video duration, and measures
each raw source and the delivered track independently with FFmpeg. The model
never receives audio.
"""

import asyncio
import os
import re
import shutil

from . import diagnostics
from .errors import ShowrunError

SILENCE_DBFS = -60.0
SYNC_PRECISION_SECONDS = .3  # measured worst case 0.25 s with 5 Hz window samples
SYNC_METHOD = ('Companion host-clock buffer timestamps mapped to Unix time at stream start; '
               'video origin is the first window sample completion time.')
SYNC_LIMITATIONS = ('Window samples are stamped when each screenshot completes, so picture may trail '
                    'sound by up to one sample latency. Audio is continuous; video is sampled at up to 5 Hz.')


async def _stderr(*args, timeout=60):
    """Run FFmpeg and return stderr (volumedetect reports there); failures raise."""
    try:
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL,
                                                    stderr=asyncio.subprocess.PIPE)
    except OSError:
        raise diagnostics.media_error(args[0], 'executable could not be started; check installation and PATH.') from None
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    if proc.returncode != 0:
        raise ShowrunError('media_invalid', 'Audio processing failed.')
    return err.decode(errors='replace')


def _volumes(text):
    found = {}
    for key in ('max_volume', 'mean_volume'):
        match = re.search(key + r':\s*(-?inf|-?[0-9.]+) dB', text)
        if match:
            value = match.group(1)
            found[key + '_db'] = None if 'inf' in value else float(value)
    return found


def raw_input(source, path):
    return ['-f', 's16le', '-ar', str(source['sample_rate']), '-ac', str(source['channels']), '-i', str(path)]


async def levels(input_args):
    """Independent FFmpeg volumedetect of the first audio stream."""
    text = await _stderr('ffmpeg', '-hide_banner', '-nostats', *input_args,
                         '-map', '0:a:0', '-af', 'volumedetect', '-f', 'null', '-')
    return _volumes(text)


def classify(captured_frames, max_db):
    if not captured_frames:
        return 'none_received'
    if max_db is None or max_db <= SILENCE_DBFS:
        return 'silent'
    return 'present'


def clean_source(source):
    """Keep only bounded companion statistics; never paths or device lists."""
    keep = {}
    for key in ('kind', 'sample_rate', 'channels', 'buffers', 'frames', 'captured_frames', 'gap_frames',
                'dropped_frames', 'peak_dbfs', 'rms_dbfs', 'first_buffer_seconds', 'timestamp_basis', 'error',
                'encoding'):
        if key not in source:
            continue
        value = source[key]
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and abs(value) < 2**53 or value is None:
            keep[key] = value
        elif isinstance(value, str) and re.fullmatch(r'[a-z0-9_]{1,64}', value):
            keep[key] = value
    return keep


def _chain(index, offset):
    if offset >= 0:
        head = f'atrim=start={offset:.6f},asetpts=PTS-STARTPTS'
    else:
        head = f'adelay=delays={int(round(-offset * 1000))}:all=1'
    return (f'[{index}:a]{head},aresample=48000,'
            f'aformat=sample_fmts=fltp:channel_layouts=stereo[a{index}]')


async def mux(folder, video, started, stopped, origin, duration):
    """Return (report, output_path) after writing capture.audio.mp4 next to the video."""
    raw_dir = folder / 'audio'
    anchor = started['anchor_unix']
    offset = origin - anchor
    sources, inputs, chains = [], [], []
    for source in stopped.get('sources', []):
        row = clean_source(source)
        name = source.get('file')
        path = raw_dir / name if isinstance(name, str) and re.fullmatch(r'[a-z]+\.s16le', name) else None
        row['bytes'] = path.stat().st_size if path and path.is_file() and not path.is_symlink() else 0
        if row['bytes'] and row.get('captured_frames'):
            row.update(await levels(raw_input(row, path)))
            inputs += raw_input(row, path)
            chains.append(_chain(len(chains) + 1, offset))
        row['signal'] = classify(row.get('captured_frames') if row['bytes'] else 0, row.get('max_volume_db'))
        sources.append(row)
    if chains:
        labels = ''.join(f'[a{i + 1}]' for i in range(len(chains)))
        mixed = f'{labels}amix=inputs={len(chains)}:normalize=0:duration=longest[m]' if len(chains) > 1 \
            else f'{labels}anull[m]'
        graph = ';'.join(chains + [mixed, f'[m]apad,atrim=end={duration:.6f}[out]'])
    else:
        # No buffers at all: keep an explicitly silent, declared track; the receipt says so.
        inputs += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
        graph = f'[1:a]atrim=end={duration:.6f}[out]'
    output = folder / 'capture.audio.mp4'
    await _stderr('ffmpeg', '-v', 'error', '-y', '-i', str(video), *inputs, '-filter_complex', graph,
                  '-map', '0:v:0', '-map', '[out]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                  '-ar', '48000', '-ac', '2', '-movflags', '+faststart', '-t', f'{duration:.6f}', str(output),
                  timeout=120)
    track = await levels(['-i', str(output)])
    track.update(codec='aac', sample_rate=48000, channels=2)
    track['signal'] = classify(1, track.get('max_volume_db'))
    report = {
        'status': 'captured', 'scope': stopped.get('scope'), 'sources': sources, 'track': track,
        'applications': [a for a in stopped.get('applications', [])[:32]
                         if isinstance(a, str) and re.fullmatch(r'[A-Za-z0-9.-]{1,200}', a)],
        'filter_updates': stopped.get('filter_updates', 0) if type(stopped.get('filter_updates')) is int else 0,
        'sync': {'audio_trim_seconds': round(offset, 6), 'precision_seconds': SYNC_PRECISION_SECONDS,
                 'method': SYNC_METHOD, 'limitations': SYNC_LIMITATIONS,
                 'captured_seconds': round(stopped.get('stop_unix', anchor) - anchor, 3)},
    }
    return report, output


def replace_media(folder, candidate):
    """Atomically deliver the muxed file, then discard redundant private raw PCM."""
    os.replace(candidate, folder / 'capture.mp4')
    shutil.rmtree(folder / 'audio', ignore_errors=True)


def signal_failures(report, require_signal):
    """Enabled sources and the delivered track must carry signal unless the caller waived it."""
    if not require_signal:
        return []
    failing = [s['kind'] for s in report.get('sources', []) if s.get('signal') != 'present']
    if report.get('track', {}).get('signal') != 'present' and 'track' not in failing:
        failing.append('track')
    return failing


def outcome(report, require_signal=True):
    """The take-level audio failure, if any. Footage stays retained and labeled."""
    status = report.get('status')
    if status != 'captured':
        error = report.get('error') or {}
        return ShowrunError('audio_failed', 'Requested audio was not delivered (' + str(status) + ').',
                            error.get('remedy', 'Inspect the receipt audio block; use a new request_id after correction.'))
    if report.get('interrupted'):
        return ShowrunError('audio_interrupted', 'Audio capture stopped during the take: '
                            + report['interrupted'] + '.',
                            'Check Screen & System Audio Recording permission and the target app; retake with a new request_id.')
    broken = [s['kind'] + ':' + s['error'] for s in report.get('sources', []) if s.get('error')]
    if broken:
        return ShowrunError('audio_interrupted', 'Audio source stopped during the take: ' + ', '.join(broken) + '.',
                            'Inspect the receipt audio sources; retake with a new request_id.')
    failing = signal_failures(report, require_signal)
    if failing:
        return ShowrunError(
            'audio_no_signal', 'Requested audio carried no signal: ' + ', '.join(failing) + '.',
            'Confirm the target actually played sound during the take. For browsers or web apps try '
            'include_bundle_ids or output: system; for microphone check the selected input device. '
            'Set require_signal: false only when silence is expected.')
    return None
