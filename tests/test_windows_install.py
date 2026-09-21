import asyncio
import hashlib
import io
import zipfile

import pytest

from amplifier_smart_tool_showrun import windows_install as installer
from amplifier_smart_tool_showrun.errors import ShowrunError


def archive(name='ShowrunDesktop.exe', data=b'MZverified', mode=0):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as bundle:
        info = zipfile.ZipInfo(name)
        info.external_attr = mode << 16
        bundle.writestr(info, data)
    return stream.getvalue()


def download(monkeypatch, data):
    monkeypatch.setattr(installer, 'SHA256', hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(installer.urllib.request, 'urlopen', lambda *a, **kw: io.BytesIO(data))


@pytest.mark.parametrize('name,data,mode', [('..\\escape.exe', b'MZbad', 0),
                                          ('../escape.exe', b'MZbad', 0),
                                          ('ShowrunDesktop.exe', b'MZbad', 0o120777),
                                          ('ShowrunDesktop.exe', b'not PE', 0)])
def test_reject_bad_windows_archive(tmp_path, monkeypatch, name, data, mode):
    download(monkeypatch, archive(name, data, mode))
    target = tmp_path / 'ShowrunDesktop.exe'
    with pytest.raises(ShowrunError):
        installer.stage(target)
    assert not target.exists()


def test_windows_checksum_failure_preserves_install(tmp_path, monkeypatch):
    target = tmp_path / 'ShowrunDesktop.exe'
    target.write_bytes(b'old executable')
    monkeypatch.setattr(installer.sys, 'platform', 'win32')
    monkeypatch.setattr(installer.platform, 'machine', lambda: 'AMD64')
    monkeypatch.setattr(installer, 'helper_path', lambda: target)
    monkeypatch.setattr(installer.urllib.request, 'urlopen', lambda *a, **kw: io.BytesIO(b'bad'))
    with pytest.raises(ShowrunError) as error:
        asyncio.run(installer.install())
    assert error.value.code == 'desktop_checksum_mismatch'
    assert target.read_bytes() == b'old executable'


def test_windows_verified_install(tmp_path, monkeypatch):
    target = tmp_path / 'ShowrunDesktop.exe'
    target.write_bytes(b'old executable')
    monkeypatch.setattr(installer.sys, 'platform', 'win32')
    monkeypatch.setattr(installer.platform, 'machine', lambda: 'AMD64')
    monkeypatch.setattr(installer, 'helper_path', lambda: target)
    download(monkeypatch, archive())
    async def allow(path):
        assert path.read_bytes() == b'MZverified'
    monkeypatch.setattr(installer, 'allow_interactive_launch', allow)
    result = asyncio.run(installer.install())
    assert result['model_calls'] == 0
    assert target.read_bytes() == b'MZverified'
    assert list(tmp_path.iterdir()) == [target]


def test_launch_permission_failure_preserves_previous_binary(tmp_path, monkeypatch):
    target = tmp_path / 'ShowrunDesktop.exe'
    target.write_bytes(b'old executable')
    monkeypatch.setattr(installer.sys, 'platform', 'win32')
    monkeypatch.setattr(installer.platform, 'machine', lambda: 'AMD64')
    monkeypatch.setattr(installer, 'helper_path', lambda: target)
    download(monkeypatch, archive())
    async def denied(path):
        raise OSError('ACL denied')
    monkeypatch.setattr(installer, 'allow_interactive_launch', denied)
    with pytest.raises(ShowrunError) as error:
        asyncio.run(installer.install())
    assert error.value.code == 'desktop_install_failed'
    assert target.read_bytes() == b'old executable'
