"""External Windows UIA/capture trial. Run inside the logged-in desktop, no model."""
import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

from amplifier_smart_tool_showrun.desktop import Desktop
from amplifier_smart_tool_showrun.errors import ShowrunError
from amplifier_smart_tool_showrun.target import Target


async def trial(output, build):
    root = Path(__file__).resolve().parents[1]
    run = output / 'fixture'
    output.mkdir(parents=True, exist_ok=False)
    log = (output / 'launcher.log').open('w')
    launcher = subprocess.Popen([sys.executable, str(root / 'harness.py'), 'launch',
                                 '--build', str(build), '--run', str(run), '--scenario', 'save-task'],
                                stdout=log, stderr=log)
    desktop = None
    ready = None
    try:
        deadline = time.monotonic() + 30
        while not (run / 'ready.json').exists():
            if launcher.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('Fixture failed to start')
            await asyncio.sleep(.1)
        ready = json.loads((run / 'ready.json').read_text(encoding='utf-8-sig'))
        target = Target({'kind': 'windows', 'pid': ready['pid'], 'window_title': ready['window_title'],
                         'resize_to_capture': True}, output)
        desktop = Desktop(target, output, {'width': 800, 'height': 600})
        desktop.ui = {'actions': ['click', 'fill'], 'allowed_values': ['Example task'],
                      'target_effects': 'all_in_session'}
        await desktop.start()
        await asyncio.sleep(1)

        async def act(label, action, text=None):
            observation = await desktop.observe()
            controls = observation['frames'][0]['controls']
            matches = [c for c in controls if c['label'] == label and action in c['actions']]
            if len(matches) != 1:
                (output / 'observation.json').write_text(json.dumps(observation, indent=2))
                raise RuntimeError(f'Expected one accessible {label}: {len(matches)}')
            payload = {'action': action, 'ref': matches[0]['ref']}
            if text is not None:
                payload['text'] = text
            await desktop.act(payload, lambda name: None, observation['generation'])
            await asyncio.sleep(.4)
            return payload, observation['generation']

        await act('Task name', 'fill', 'Example task')
        old = await desktop.observe()
        stale = next(c['ref'] for c in old['frames'][0]['controls'] if c['label'] == 'Save task')
        await act('Move Save button', 'click')
        try:
            await desktop.bridge.call('act', generation=old['generation'], action='click', ref=stale)
            raise AssertionError('Stale control was accepted')
        except ShowrunError as error:
            assert error.code == 'stale_ref', error
        await act('Save task', 'click')
        observation = await desktop.observe()
        assert desktop.visible(observation, 'Saved task: Example task'), observation
        await asyncio.sleep(3)
        media = await desktop.capture.finish()
        desktop.capture.task = None
        await desktop.close()
        desktop = None
        verify = subprocess.run([sys.executable, str(root / 'harness.py'), 'verify', '--run', str(run),
                                 '--scenario', 'save-task'], capture_output=True, text=True, check=True)
        result = {'status': 'passed', 'independent_state': json.loads(verify.stdout), 'media': media,
                  'checks': ['UIA fill', 'UIA invoke', 'stale reference rejected', 'window resize',
                             'decoded PrintWindow recording'],
                  'limitations': ['No live model or third-party application was exercised.']}
        (output / 'result.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)
    finally:
        if desktop:
            if desktop.capture.task:
                await desktop.capture.finish()
            await desktop.close()
        if ready and launcher.poll() is None:
            import win32con
            import win32gui
            import win32process
            def close(hwnd, unused):
                if (win32process.GetWindowThreadProcessId(hwnd)[1] == ready['pid']
                        and win32gui.GetWindowText(hwnd) == ready['window_title']):
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            win32gui.EnumWindows(close, None)
        try:
            launcher.wait(timeout=10)
        except subprocess.TimeoutExpired:
            launcher.terminate()
            launcher.wait(timeout=5)
        log.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--build', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(trial(args.output.resolve(), args.build.resolve()))
