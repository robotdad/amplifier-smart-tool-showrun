"""Positive, explicit managed-demo preparation; supplied content is the authority."""

import asyncio
import json
import os
import tempfile
from importlib.resources import files
from pathlib import Path

from .errors import ShowrunError, require
from .stories_helper import digest, fixture_marker, fixture_path


async def helper(request):
    require(Path(request["python"]).is_file(), "Selected Stories interpreter is missing.", "stories_missing")
    with tempfile.TemporaryDirectory(prefix="showrun-fixture-") as home:
        proc = await asyncio.create_subprocess_exec(
            request["python"], "-I", str(files(__package__).joinpath("stories_helper.py")),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "HOME": home, "LANG": "C.UTF-8", "PYTHONUNBUFFERED": "1"},
        )
        try:
            output, _ = await asyncio.wait_for(proc.communicate((json.dumps(request) + "\n").encode()), 25)
            response = json.loads(output)
            require(response.get("status") in {"prepared", "valid"},
                    "Stories fixture preparation or exact-content validation failed.", "fixture_invalid")
            return response
        except (TimeoutError, ValueError):
            raise ShowrunError("fixture_invalid", "Stories fixture helper did not confirm a valid fixture.",
                               "Preserve any partial import and check the installed Stories interpreter.") from None
        finally:
            if proc.returncode is None:
                # asyncio's child transport owns an unreaped child, not a persisted PID.
                proc.kill()
                await proc.wait()


def prepare(presentation, destination, python):
    """Only an absent or empty owned directory. Never imports from a live store."""
    require(Path(python).is_absolute() and Path(python).is_file(),
            "Select an absolute installed Stories interpreter.", "stories_missing")
    destination = Path(destination).expanduser()
    require(destination.is_absolute(), "Fixture destination must be absolute.", "fixture_invalid")
    require(not any(p.is_symlink() for p in (destination, *destination.parents)),
            "Fixture paths must not use symlinks.", "fixture_invalid")
    # Refuse conventional live stores even when absent. Positive marker/emptiness
    # below, not this deny-list, is the authority boundary.
    live = Path(os.environ.get("STORIES_STORE", Path.home() / ".local/share/stories")).expanduser()
    require(destination.resolve() != live.resolve(), "Do not prepare a live Stories store.", "fixture_invalid")
    supplied_hash = digest(presentation)
    if not destination.exists():
        destination.mkdir(mode=0o700)  # caller chooses/creates the parent; no discovery
    try:
        storage = fixture_path(destination)
        require(not any(storage.iterdir()), "Fixture destination must be empty; existing stores are refused.",
                "fixture_invalid")
    except (OSError, ValueError):
        raise ShowrunError("fixture_invalid", "Fixture must be an empty owned directory without symlinks.") from None
    result = asyncio.run(helper({"operation": "prepare", "python": str(python), "storage": str(storage),
                                 "presentation": presentation, "supplied_sha256": supplied_hash}))
    return {"status": "prepared", "supplied_sha256": supplied_hash,
            "target": {"kind": "stories", "python": str(python), "storage": str(storage),
                       **{k: result[k] for k in ("story_id", "revision_id", "fixture_sha256")}}}


async def validate_fixture(target):
    try:
        fixture_marker(target)
    except (OSError, ValueError, KeyError, TypeError):
        raise ShowrunError("fixture_invalid", "Managed target is not the exact prepared Showrun fixture.",
                           "Use prepare-fixture with supplied exported content and a fresh empty destination.") from None
    return await helper({**target, "operation": "validate"})