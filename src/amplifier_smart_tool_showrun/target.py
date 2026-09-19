"""Reusable Stories launch/stop over a private fixed helper protocol."""

import asyncio
import json
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from .errors import ShowrunError, require


class Target:
    def __init__(self, config, folder):
        self.config, self.folder = config, folder
        self.process = None
        self.ownership = {"dashboard": "caller" if config["kind"] == "url" else "showrun",
                          "startup": "not_started", "cleanup": "not_required"}

    async def start(self):
        if self.config["kind"] == "url":
            self.url = self.config["url"]
            self.revision = self.config.get("stories_revision")
            self.ownership["startup"] = "caller_supplied"
            return self.url
        require(Path(self.config["python"]).is_file(), "Selected Stories interpreter is missing.",
                "stories_missing")
        require(Path(self.config["storage"]).is_dir(), "Isolated Stories fixture store is missing.",
                "stories_missing")
        self.ownership.update(startup="starting", cleanup="pending")
        # A separate HOME removes provider credential caches as well as env secrets.
        home = self.folder / "target-home"
        home.mkdir(mode=0o700)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8",
               "PYTHONUNBUFFERED": "1"}
        self.process = await asyncio.create_subprocess_exec(
            self.config["python"], "-I", str(files(__package__).joinpath("stories_helper.py")),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=env,
        )
        payload = {**self.config, "ownership_path": str(self.folder / "owned-dashboard.json")}
        self.process.stdin.write((json.dumps(payload) + "\n").encode())
        await self.process.stdin.drain()
        line = await asyncio.wait_for(self.process.stdout.readline(), 25)
        try:
            response = json.loads(line)
        except ValueError:
            raise ShowrunError("stories_start_failed", "Stories helper exited without readiness.") from None
        require(response.get("status") == "ready", "Installed Stories could not start the requested revision.",
                "stories_start_failed")
        parsed = urlsplit(response["url"])
        require(parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port
                and not parsed.username and not parsed.password and parsed.path in {"", "/"} and not parsed.query,
                "Managed dashboard returned an unsupported endpoint.", "target_scope")
        require(response["revision_id"] == self.config["revision_id"], "Dashboard revision mismatch.",
                "target_not_ready")
        self.url, self.revision = response["url"], response["revision_id"]
        self.ownership.update(startup="launched", service_id=response["service_id"])
        return self.url

    async def close(self):
        if not self.process:
            return
        try:
            if self.process.returncode is None:
                self.process.stdin.close()
                lines = await asyncio.wait_for(self.process.stdout.read(), 15)
                await asyncio.wait_for(self.process.wait(), 2)
                replies = [json.loads(line) for line in lines.splitlines() if line]
                require(any(r.get("status") == "stopped" for r in replies),
                        "Owned Stories shutdown was not verified.", "cleanup_failed")
            else:
                raise ShowrunError("cleanup_failed", "Stories helper exited before cleanup confirmation.")
            self.ownership["cleanup"] = "verified_stopped"
        except Exception:
            self.ownership["cleanup"] = "failed_or_uncertain"
            self.ownership["remedy"] = (
                "Inspect the isolated Stories store using owned-dashboard.json; "
                "stop that service through Stories.stop_dashboard. Do not kill a process by port."
            )
            if self.process.returncode is None:
                self.process.kill()
                await self.process.wait()
            raise ShowrunError("cleanup_failed", "Owned Stories cleanup is uncertain.",
                               self.ownership["remedy"]) from None