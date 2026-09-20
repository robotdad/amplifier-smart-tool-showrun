"""Fixed private line protocol, executed with -I by the selected Stories interpreter.

No caller-provided imports or code. stdout is a private pipe containing the
dashboard access fragment; it must never be logged or included in receipts.
"""

import hashlib
import importlib.metadata
import importlib.util
import json
import os
import signal
import sys
import time
from pathlib import Path


def native_processes():
    # The isolated Stories interpreter need not have Showrun installed. Load only
    # this packaged sibling, never a request-provided module or import path.
    spec = importlib.util.spec_from_file_location(
        '_showrun_process_platform', Path(__file__).with_name('process_platform.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def process_identity(pid, allow_exited=False):
    """OS creation identity; retain the historical Linux receipt format."""
    if sys.platform != 'linux':
        return native_processes().identity(pid, allow_exited)
    stat = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
    if stat[0] == "Z" and not allow_exited:
        raise ProcessLookupError(pid)
    return {"pid": pid, "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "start_ticks": int(stat[19])}


def same_process(identity):
    try:
        return bool(identity and process_identity(identity["pid"]) == identity)
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return False


def signal_owned(identity, sig=None):
    """Bind signal delivery to a pidfd, then recheck; never signal a reused PID."""
    if sys.platform == 'win32':
        return bool(identity and sig is None and native_processes().terminate_windows(identity))
    # macOS has no pidfd equivalent here. Never replace it with a racy kill(pid).
    if not identity or not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        return False
    try:
        fd = os.pidfd_open(identity["pid"])
        try:
            if not same_process(identity):
                return False
            signal.pidfd_send_signal(fd, signal.SIGKILL if sig is None else sig)
            return True
        finally:
            os.close(fd)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def exited(pid):
    if sys.platform != 'linux':
        return native_processes().exited(pid)
    try:
        # Linux-only MVP: a zombie has exited even before its unrelated reaper runs.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[0] == "Z"
    except FileNotFoundError:
        return True


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def fixture_path(value):
    path = Path(value)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("fixture_symlink")
    path = path.resolve()
    if not path.is_dir() or path.stat().st_uid != os.getuid():
        raise ValueError("fixture_owner")
    if any(p.is_symlink() for p in path.rglob("*")):
        raise ValueError("fixture_symlink")
    return path


def fixture_marker(request):
    """Positive authorization, checked before even constructing the target API."""
    storage = fixture_path(request["storage"])
    marker = json.loads((storage / "showrun-fixture.json").read_text())
    expected = (str(storage), storage.stat().st_dev, storage.stat().st_ino,
                request["story_id"], request["revision_id"], request["fixture_sha256"])
    actual = tuple(marker[k] for k in ("storage", "device", "inode", "story_id", "revision_id", "fixture_sha256"))
    if marker.get("schema_version") not in {1, 2} or actual != expected:
        raise ValueError("fixture_identity")
    return storage, marker


def content_identity(api, story_id, revision_id, version=2):
    listed = api.list_stories()
    if len(listed) != 1 or listed[0]["id"] != story_id:
        raise ValueError("fixture_scope")
    story = api.get_story(story_id)
    if story["latest_revision"] != revision_id or story["selected_revision"] != revision_id:
        raise ValueError("fixture_revision")
    if version == 2:
        # Only review text is mutable under this slice. Grants, acceptance,
        # directions, revision material and all other state remain hash-bound.
        story.pop("annotations", None)
        story.pop("drafts", None)
    return digest({"story": story, "revision": api.get_revision(story_id, revision_id)})


def validate_fixture(api, request, marker):
    if content_identity(api, request["story_id"], request["revision_id"],
                        marker["schema_version"]) != marker["fixture_sha256"]:
        raise ValueError("fixture_changed")


def stop_service(api, storage, service, identity):
    """No call to even the public shutdown API with uncertain process ownership."""
    saved = json.loads((storage / (service["service_id"] + ".json")).read_text())
    if not identity or saved.get("pid") != identity["pid"] or not same_process(identity):
        return False
    api.stop_dashboard(service["service_id"])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        # A disappeared process is stopped only on the same boot. A recycled
        # zombie PID is not evidence that OUR process was observed exiting.
        if sys.platform == 'linux' and Path("/proc/sys/kernel/random/boot_id").read_text().strip() != identity["boot_id"]:
            return False
        if sys.platform == 'darwin' and native_processes().mac_boot_id() != identity.get('boot_id'):
            return False
        try:
            if process_identity(identity["pid"], allow_exited=True) != identity:
                return False
        except (FileNotFoundError, ProcessLookupError):
            return True
        if exited(identity["pid"]):
            return True
        time.sleep(.05)
    return False


def main():
    api, service, storage = None, None, None
    identity = None

    def stop():
        if not api or not storage or not service:
            return True
        try:
            return stop_service(api, storage, service, identity)
        except Exception:
            return False

    try:
        request = json.loads(sys.stdin.readline())
        operation = request.get("operation", "launch")
        if operation == "prepare":
            storage = fixture_path(request["storage"])
            if any(storage.iterdir()):
                raise ValueError("fixture_not_empty")
        else:
            storage, marker = fixture_marker(request)
        if importlib.metadata.version("amplifier-smart-tool-stories") != "0.1.0":
            raise ValueError("unsupported_version")
        from amplifier_smart_tool_stories import Stories

        api = Stories(storage, model_env=False, execution="queued")
        if operation == "prepare":
            supplied = request["presentation"]
            if not isinstance(supplied, dict) or set(supplied) - {"title", "html", "sources", "purpose", "audience"}:
                raise ValueError("fixture_input")
            # Retained exports contain hashes/metadata that are not public import arguments.
            supplied["sources"] = [{k: v for k, v in source.items()
                                    if k in {"id", "name", "content", "kind", "attribution"}}
                                   for source in supplied.get("sources", [])]
            created = api.create_story(**supplied, request_id="showrun-fixture-import")
            story_id, revision_id = created["story_id"], created["revision_id"]
            marker = {"schema_version": 2, "storage": str(storage), "device": storage.stat().st_dev,
                      "inode": storage.stat().st_ino, "story_id": story_id, "revision_id": revision_id,
                      "supplied_sha256": request["supplied_sha256"],
                      "fixture_sha256": content_identity(api, story_id, revision_id)}
            fd = os.open(storage / "showrun-fixture.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(marker, stream)
                stream.flush()
                os.fsync(stream.fileno())
            print(json.dumps({"status": "prepared", **marker}), flush=True)
            return
        validate_fixture(api, request, marker)
        if operation == "review":
            story = api.get_story(request["story_id"])
            print(json.dumps({"status": "valid", "annotations": story["annotations"],
                              "drafts": story["drafts"], "feedback_grant": story["feedback_grant"],
                              "view": api.get_review_view(request["story_id"])}), flush=True)
            return
        if operation == "validate":
            print(json.dumps({"status": "valid", "fixture_sha256": marker["fixture_sha256"]}), flush=True)
            return
        revision = api.get_revision(request["story_id"], request["revision_id"])
        if revision["id"] != request["revision_id"]:
            raise ValueError("revision_mismatch")
        service = api.start_dashboard(request["story_id"], request["revision_id"])
        saved = json.loads((storage / (service["service_id"] + ".json")).read_text())
        identity = process_identity(saved["pid"])
        # Record the public handle immediately; never persist the access fragment here.
        owner = Path(request["ownership_path"])
        fd = os.open(owner, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump({"service_id": service["service_id"], "revision_id": revision["id"],
                       "process": identity, "fixture_sha256": marker["fixture_sha256"]}, stream)
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
