"""Fixed private line protocol, executed with -I by the selected Stories interpreter.

No caller-provided imports or code. stdout is a private pipe containing the
dashboard access fragment; it must never be logged or included in receipts.
"""

import importlib.metadata
import json
import os
import sys
import time
from pathlib import Path


def exited(pid):
    try:
        # Linux-only MVP: a zombie has exited even before its unrelated reaper runs.
        return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()[0] == "Z"
    except FileNotFoundError:
        return True


def main():
    api, service, storage = None, None, None
    before = set()

    def stop():
        if not api or not storage:
            return True
        candidates = [service] if service else [
            {"service_id": p.stem} for p in storage.glob("viewer_*.json") if p not in before
        ]
        all_stopped = True
        for item in candidates:
            path = storage / (item["service_id"] + ".json")
            try:
                saved = json.loads(path.read_text())
                pid = saved.get("pid")
                api.stop_dashboard(item["service_id"])
                deadline = time.monotonic() + 5
                while pid and not exited(pid) and time.monotonic() < deadline:
                    time.sleep(.05)
                all_stopped = all_stopped and bool(pid and exited(pid))
            except Exception:
                all_stopped = False
        return all_stopped

    try:
        request = json.loads(sys.stdin.readline())
        if importlib.metadata.version("amplifier-smart-tool-stories") != "0.1.0":
            raise ValueError("unsupported_version")
        from amplifier_smart_tool_stories import Stories

        storage = Path(request["storage"]).resolve()
        if not storage.is_dir():
            raise ValueError("missing_fixture_store")
        before = set(storage.glob("viewer_*.json"))
        api = Stories(storage, model_env=False, execution="queued")
        revision = api.get_revision(request["story_id"], request["revision_id"])
        if revision["id"] != request["revision_id"]:
            raise ValueError("revision_mismatch")
        service = api.start_dashboard(request["story_id"], request["revision_id"])
        # Record the public handle immediately; never persist the access fragment here.
        owner = Path(request["ownership_path"])
        fd = os.open(owner, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump({"service_id": service["service_id"], "revision_id": revision["id"]}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps({"status": "ready", "service_id": service["service_id"],
                          "url": service["url"], "revision_id": revision["id"]}), flush=True)
        # EOF (including owner death) also stops the owned dashboard.
        sys.stdin.readline()
        ok = stop()
        service = None
        api = None
        print(json.dumps({"status": "stopped" if ok else "cleanup_failed"}), flush=True)
    except Exception:
        print(json.dumps({"status": "failed", "code": "stories_lifecycle_failed"}), flush=True)
    finally:
        stop()


if __name__ == "__main__":
    main()