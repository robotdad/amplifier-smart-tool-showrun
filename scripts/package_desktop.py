"""Package an explicitly prepared companion; run with the Showrun environment."""
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from amplifier_smart_tool_showrun import Showrun

output = Path(sys.argv[1]).resolve()
output.mkdir(parents=True, exist_ok=True)
if sys.platform == 'win32':
    if platform.machine().lower() not in {'amd64', 'x86_64'}:
        raise SystemExit('Windows release packaging currently requires x64.')
    project = Path(__file__).resolve().parents[1] / 'src/amplifier_smart_tool_showrun/native/windows/ShowrunDesktop.csproj'
    archive = output / 'showrun-desktop-windows-x64.zip'
    with tempfile.TemporaryDirectory(prefix='showrun-release-') as temporary:
        folder = Path(temporary)
        subprocess.run(['dotnet', 'publish', str(project), '-c', 'Release', '-r', 'win-x64',
                        '--self-contained', 'true', '-p:PublishSingleFile=true',
                        '-p:IncludeNativeLibrariesForSelfExtract=true', '-p:EnableCompressionInSingleFile=true',
                        '-p:BaseIntermediateOutputPath=' + str(folder / 'obj') + '/',
                        '-o', str(folder / 'publish')], check=True)
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(folder / 'publish/ShowrunDesktop.exe', 'ShowrunDesktop.exe')
    metadata = {'platform': 'windows', 'architecture': 'x64', 'signing': 'unsigned',
                'runtime': '.NET 8 Desktop, self-contained', 'tested_os': 'Windows 11'}
else:
    prepared = Showrun.prepare_desktop(build=True)
    app = Path(prepared['app'])
    archive = output / f'showrun-desktop-macos-{platform.machine()}.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(app.rglob('*')):
            if path.is_file():
                bundle.write(path, path.relative_to(app.parent))
    metadata = {'bundle_id': prepared['bundle_id'], 'platform': 'macos', 'minimum_os': '14.0',
                'architecture': platform.machine(), 'signing': 'ad-hoc', 'notarized': False}
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(output / 'SHA256SUMS').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
(output / 'companion.json').write_text(json.dumps(dict(metadata, protocol=1, asset=archive.name,
                                                     sha256=digest), indent=2) + '\n', encoding='utf-8')
print(digest)
