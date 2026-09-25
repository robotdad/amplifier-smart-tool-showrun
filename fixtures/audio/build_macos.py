"""Build the ad-hoc signed audio verification fixture: python fixtures/audio/build_macos.py <out-dir>."""
import plistlib
import subprocess
import sys
from pathlib import Path

output = Path(sys.argv[1]).resolve() / 'ShowrunToneFixture.app'
executable = output / 'Contents' / 'MacOS' / 'tone-fixture'
executable.parent.mkdir(parents=True, exist_ok=True)
source = Path(__file__).resolve().parent / 'macos' / 'ToneFixture.swift'
subprocess.run(['xcrun', 'swiftc', '-O', str(source), '-o', str(executable)], check=True)
(output / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps({
    'CFBundleIdentifier': 'org.showrun.ToneFixture', 'CFBundleName': 'Showrun Tone Fixture',
    'CFBundleExecutable': 'tone-fixture', 'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1',
    'LSMinimumSystemVersion': '14.0'}))
subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(output)], check=True)
print(output)
