"""Independent check of a Tone Fixture recording: python fixtures/audio/analyze.py capture.mp4

Uses only FFmpeg: ffprobe for streams, volumedetect for level, silencedetect for sound
boundaries and signalstats luma for white/black picture boundaries. Prints JSON with
the picture-to-sound offset at each matched boundary (positive = sound after picture).
"""
import json
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, check=True)


def main(path):
    streams = json.loads(run('ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json',
                             path).stdout)
    audio = [s for s in streams['streams'] if s['codec_type'] == 'audio']
    result = {'path': Path(path).name, 'duration_seconds': float(streams['format']['duration']),
              'audio_streams': [{k: s.get(k) for k in ('codec_name', 'sample_rate', 'channels', 'duration')}
                                for s in audio]}
    if not audio:
        print(json.dumps(dict(result, verdict='no_audio_stream'), indent=2))
        return 1
    level = run('ffmpeg', '-hide_banner', '-nostats', '-i', path, '-map', '0:a:0', '-af', 'volumedetect',
                '-f', 'null', '-').stderr
    result['max_volume_db'] = float(re.search(r'max_volume: (-?[0-9.]+) dB', level).group(1))
    result['mean_volume_db'] = float(re.search(r'mean_volume: (-?[0-9.]+) dB', level).group(1))
    silence = run('ffmpeg', '-hide_banner', '-nostats', '-i', path, '-map', '0:a:0',
                  '-af', 'silencedetect=noise=-45dB:d=0.25', '-f', 'null', '-').stderr
    sound = []  # (time, 'on'|'off')
    for kind, value in re.findall(r'silence_(start|end): (-?[0-9.]+)', silence):
        sound.append((float(value), 'off' if kind == 'start' else 'on'))
    with tempfile.TemporaryDirectory() as folder:
        stats = Path(folder) / 'yavg.txt'
        run('ffmpeg', '-hide_banner', '-nostats', '-i', path, '-map', '0:v:0', '-vf',
            f'signalstats,metadata=print:key=lavfi.signalstats.YAVG:file={stats}', '-f', 'null', '-')
        samples, stamp = [], None
        for line in stats.read_text().splitlines():
            if line.startswith('frame:'):
                stamp = float(re.search(r'pts_time:([0-9.]+)', line).group(1))
            elif 'YAVG=' in line and stamp is not None:
                samples.append((stamp, float(line.split('=')[1])))
    picture, previous = [], None
    for stamp, luma in samples:
        state = 'on' if luma > 128 else 'off'
        if previous is not None and state != previous:
            picture.append((stamp, state))
        previous = state
    offsets = []
    for stamp, state in picture:
        matches = [t for t, s in sound if s == state and abs(t - stamp) < .75]
        if matches:
            offsets.append(round(min(matches, key=lambda t: abs(t - stamp)) - stamp, 3))
    result.update(sound_boundaries=len(sound), picture_boundaries=len(picture), matched=len(offsets),
                  offsets_seconds=offsets,
                  median_offset_seconds=statistics.median(offsets) if offsets else None)
    result['verdict'] = 'signal_present' if result['max_volume_db'] > -60 else 'silent'
    print(json.dumps(result, indent=2))
    return 0 if result['verdict'] == 'signal_present' else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
