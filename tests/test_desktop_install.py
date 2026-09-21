import hashlib
import io
import zipfile

import pytest

from amplifier_smart_tool_showrun import desktop_install as installer
from amplifier_smart_tool_showrun.errors import ShowrunError


def test_download_checksum_before_write(tmp_path, monkeypatch):
    monkeypatch.setattr(installer.urllib.request, 'urlopen', lambda *a, **kw: io.BytesIO(b'wrong'))
    destination = tmp_path / 'download.zip'
    with pytest.raises(ShowrunError, match='checksum mismatch'):
        installer.fetch(destination)
    assert not destination.exists()


def test_verified_download(tmp_path, monkeypatch):
    data = b'verified archive'
    monkeypatch.setattr(installer, 'SHA256', hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(installer.urllib.request, 'urlopen', lambda *a, **kw: io.BytesIO(data))
    destination = tmp_path / 'download.zip'
    installer.fetch(destination)
    assert destination.read_bytes() == data


@pytest.mark.parametrize('name,mode', [('Showrun Desktop.app/../../escape', 0),
                                     ('/absolute', 0), ('Showrun Desktop.app/link', 0o120777)])
def test_reject_unsafe_archive(tmp_path, name, mode):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        entry = zipfile.ZipInfo(name)
        entry.external_attr = mode << 16
        z.writestr(entry, 'bad')
    with pytest.raises(ShowrunError, match='Unsafe'):
        installer.unpack(archive, tmp_path / 'out')
