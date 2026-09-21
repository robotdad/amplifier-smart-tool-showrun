"""Package an explicitly prepared companion; run with the Showrun environment."""
import hashlib
import json
import platform
import sys
import zipfile
from pathlib import Path

from amplifier_smart_tool_showrun import Showrun

output = Path(sys.argv[1]).resolve()
output.mkdir(parents=True, exist_ok=True)
prepared = Showrun.prepare_desktop(build=True)
app = Path(prepared['app'])
archive = output / f'showrun-desktop-macos-{platform.machine()}.zip'
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
    for path in sorted(app.rglob('*')):
        if path.is_file():
            bundle.write(path, path.relative_to(app.parent))
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(output / 'SHA256SUMS').write_text(f'{digest}  {archive.name}\n')
(output / 'companion.json').write_text(json.dumps({
    'bundle_id': prepared['bundle_id'], 'platform': 'macos', 'minimum_os': '14.0',
    'architecture': platform.machine(), 'signing': 'ad-hoc', 'notarized': False,
    'protocol': 1, 'asset': archive.name, 'sha256': digest,
}, indent=2) + '\n')
print(digest)
