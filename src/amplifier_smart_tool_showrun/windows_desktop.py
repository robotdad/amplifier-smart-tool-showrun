"""Experimental Windows companion: interactive-session UIA and PrintWindow."""
import asyncio
import base64
import json
import os
import secrets
import shutil
import sys
import uuid
from importlib.resources import files
from pathlib import Path

from .capture import command
from .desktop import MacBridge
from .errors import ShowrunError, require


def helper_path():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Showrun' / 'Desktop' / 'ShowrunDesktop.exe'


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


async def powershell(script):
    encoded = base64.b64encode(("$ErrorActionPreference='Stop'; " + script).encode('utf-16-le')).decode()
    return await command('powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded, timeout=30)


async def prepare():
    require(sys.platform == 'win32', 'Build the Windows companion on Windows.', 'desktop_unsupported')
    require(shutil.which('dotnet'), 'Install the .NET 8 SDK.', 'desktop_dependency_missing')
    project = files(__package__).joinpath('native/windows/ShowrunDesktop.csproj')
    destination = helper_path().parent
    await command('dotnet', 'publish', str(project), '-c', 'Release', '-o', str(destination),
                  '-p:BaseIntermediateOutputPath=' + str(destination.parent / 'obj') + os.sep, timeout=180)
    return {'status': 'prepared', 'helper': str(helper_path()), 'model_calls': 0,
            'notice': 'Experimental Windows UIA/PrintWindow. Requires .NET 8 Desktop runtime and an unlocked login session.'}


async def preflight():
    require(sys.platform == 'win32', 'Windows native recording requires Windows.', 'desktop_unsupported')
    require(helper_path().is_file(), 'Run showrun prepare-desktop --build on Windows.', 'desktop_not_prepared')
    require(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Install FFmpeg/ffprobe.', 'capture_dependency_missing')
    require(b'libx264' in await command('ffmpeg', '-v', 'error', '-encoders'),
            'FFmpeg needs libx264.', 'capture_dependency_missing')


class WindowsBridge(MacBridge):
    def __init__(self):
        super().__init__()
        self.task_name = None

    async def start(self, target=None):
        require(sys.platform == 'win32', 'Windows companion requires Windows.', 'desktop_unsupported')
        require(helper_path().is_file(), 'Run showrun prepare-desktop.', 'desktop_not_prepared')
        token = secrets.token_hex(32)
        self.connection = asyncio.get_running_loop().create_future()

        async def accept(reader, writer):
            try:
                hello = json.loads(await asyncio.wait_for(reader.readline(), 5))
                if hello != {'token': token} or self.connection.done():
                    writer.close()
                    return
                self.connection.set_result((reader, writer))
            except (ValueError, TimeoutError):
                writer.close()

        self.server = await asyncio.start_server(accept, '127.0.0.1', 0, limit=34 * 1024 * 1024)
        port = self.server.sockets[0].getsockname()[1]
        self.task_name = 'Showrun-Desktop-' + uuid.uuid4().hex
        arguments = f'--port {port} --token {token}'
        script = (
            f'$a=New-ScheduledTaskAction -Execute {quote(helper_path())} -Argument {quote(arguments)}; '
            '$p=New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) '
            '-LogonType Interactive -RunLevel Limited; '
            '$s=New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 32) '
            '-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; '
            f'Register-ScheduledTask -TaskName {quote(self.task_name)} -Action $a -Principal $p -Settings $s | Out-Null; '
            f'Start-ScheduledTask -TaskName {quote(self.task_name)}')
        try:
            await powershell(script)
            self.reader, self.writer = await asyncio.wait_for(self.connection, 20)
            self.server.close()
        except BaseException as exc:
            await self.close()
            if isinstance(exc, TimeoutError):
                raise ShowrunError('desktop_session_unavailable', 'No interactive companion connection.',
                                   'Sign into an unlocked Windows desktop as the SSH/caller user.') from None
            raise
        if target is None:
            return await self.call('permissions')
        return await self.call('bind', pid=target['pid'], window_title=target['window_title'])

    async def close(self):
        try:
            await super().close()
        finally:
            if self.task_name:
                name, self.task_name = self.task_name, None
                await powershell(
                    f'$t=Get-ScheduledTask -TaskName {quote(name)} -ErrorAction SilentlyContinue; '
                    'if ($t) { '
                    f'Stop-ScheduledTask -TaskName {quote(name)}; '
                    f'Unregister-ScheduledTask -TaskName {quote(name)} -Confirm:$false' + ' }')
