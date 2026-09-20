"""Provider-free retained review tests over generated real MP4 bytes."""

import hashlib
import json
import subprocess
import urllib.error
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from amplifier_smart_tool_showrun import ReviewStore, ShowrunError
from amplifier_smart_tool_showrun.review_server import ReviewService


def _media(path: Path, color: str) -> dict:
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=25",
            "-t", "2", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(path),
        ],
        check=True,
    )
    data = path.read_bytes()
    return {
        "path": "capture.mp4", "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
        "container": "mp4", "width": 160, "height": 90, "duration_seconds": 2.0,
        "sample_aspect_ratio": "1:1", "display_aspect_ratio": "16:9", "audio": "none", "decoded": True,
    }


def _take(root: Path, take_id: str, color: str, status: str = "succeeded") -> None:
    folder = root / take_id
    folder.mkdir()
    media = _media(folder / "capture.mp4", color)
    steps = [
        {
            "id": "opening", "status": "completed", "requested": {"id": "opening", "hold_seconds": 1},
            "interval": {"start_seconds": 0.25, "end_seconds": 1.25, "precision_seconds": 0.08,
                         "start_basis": "visible result observation; no UI interaction needed"},
        }
    ]
    receipt = {
        "schema_version": 1, "request_id": take_id, "take_id": take_id, "status": status,
        "partial": status != "succeeded", "steps": steps, "media": media, "error": None,
        "cleanup": "verified", "request": {"context": "generated review fixture", "steps": steps},
        "restricted": False,
    }
    (folder / "receipt.json").write_text(json.dumps(receipt))


@pytest.fixture
def review_root(tmp_path):
    _take(tmp_path, "take-a", "red")
    _take(tmp_path, "take-b", "blue", "failed")
    return tmp_path


def _clips(state):
    return [clip for demo in state["demos"] for take in demo["takes"] for clip in take["clips"]]


def test_review_groups_explicitly_and_selection_is_fresh_public_state(review_root):
    store = ReviewStore(review_root)
    state = store.workspace("workspace-a")
    assert len(state["demos"]) == 2  # similar fixture shape does not merge legacy takes
    clip_a = next(clip for clip in _clips(state) if clip["take_id"] == "take-a")
    selected = store.select_clip(
        "workspace-a", clip_a["demo_id"], clip_a["take_id"], clip_a["clip_id"], state["version"], "select-a"
    )
    assert selected["selection"]["media_id"] == clip_a["clip_id"]
    assert selected["selection"]["content_sha256"] == clip_a["content_sha256"]

    # A completely fresh library object reads the same durable identity.
    fresh = ReviewStore(review_root).workspace("workspace-a")
    assert fresh["selection"]["demo_id"] == clip_a["demo_id"]
    assert fresh["selection"]["take_id"] == "take-a"
    assert fresh["selection"]["clip_id"] == clip_a["clip_id"]
    assert fresh["selected_clip"]["content_sha256"] == clip_a["content_sha256"]

    _take(review_root, "take-c", "green")
    fresh_store = ReviewStore(review_root)
    after_arrival = fresh_store.workspace("workspace-a")
    assert after_arrival["selection"]["clip_id"] == clip_a["clip_id"]
    assert len(after_arrival["demos"]) == 3
    # Explicit grouping, rather than a name heuristic, joins a second take.
    grouped = fresh_store.register_demo("One explicit demonstration", "demo-grouped")
    fresh_store.attach_take("demo-grouped", "take-c", grouped["version"])
    grouped = fresh_store.get_demo("demo-grouped")
    assert [take["id"] for take in grouped["takes"]] == ["take-c"]


def test_rename_cas_and_lost_ack_retry_are_stable(review_root):
    store = ReviewStore(review_root)
    state = store.workspace()
    clip = _clips(state)[0]
    first = store.rename_clip(clip["clip_id"], "Opening clip", clip["version"], "rename-1")
    assert store.rename_clip(clip["clip_id"], "Opening clip", clip["version"], "rename-1") == first
    with pytest.raises(ShowrunError) as failure:
        store.rename_clip(clip["clip_id"], "Redirected", clip["version"], "rename-2")
    assert failure.value.code == "review_conflict"
    assert store.get_clip(clip["clip_id"])["name"] == "Opening clip"


def test_targeted_draft_cannot_submit_against_new_selection(review_root):
    store = ReviewStore(review_root)
    state = store.workspace()
    clips = _clips(state)
    clip_a, clip_b = clips
    first = store.select_clip("default", clip_a["demo_id"], clip_a["take_id"], clip_a["clip_id"],
                              state["version"], "select-a")
    draft = store.save_draft("default", clip_a["clip_id"], "Review exact moment", step_id="opening",
                             time_seconds=0.5, expected_version=first["version"], request_id="draft-a")
    assert draft["submitted"] is False
    second = store.select_clip("default", clip_b["demo_id"], clip_b["take_id"], clip_b["clip_id"],
                               draft["version"], "select-b")
    with pytest.raises(ShowrunError) as failure:
        store.submit_note("default", clip_a["clip_id"], expected_version=second["version"], request_id="submit-a")
    assert failure.value.code == "selection_conflict"
    third = store.select_clip("default", clip_a["demo_id"], clip_a["take_id"], clip_a["clip_id"],
                              second["version"], "select-a-again")
    note = store.submit_note("default", clip_a["clip_id"], expected_version=third["version"], request_id="submit-a-2")
    assert note["submitted"] is True and note["note"]["anchor"]["step_id"] == "opening"
    assert store.submit_note("default", clip_a["clip_id"], expected_version=third["version"],
                             request_id="submit-a-2") == note


def test_delete_confirmation_scope_media_hash_zip_and_invalidation(review_root, tmp_path):
    store = ReviewStore(review_root)
    state = store.workspace()
    clip = _clips(state)[0]
    selected = store.select_clip("default", clip["demo_id"], clip["take_id"], clip["clip_id"],
                                 state["version"], "select")
    data, info = store.download_mp4("default", clip["clip_id"])
    assert hashlib.sha256(data).hexdigest() == info["sha256"]
    zip_data, inventory = store.download_zip("default", clip["demo_id"])
    assert inventory["complete"] is True
    with zipfile.ZipFile(BytesIO(zip_data)) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
        assert f"media/{clip['clip_id']}.mp4" in names
        assert hashlib.sha256(archive.read(f"media/{clip['clip_id']}.mp4")).hexdigest() == clip["content_sha256"]
        assert manifest["complete"] is True

    confirmation = store.prepare_delete("clip", clip["clip_id"], clip["version"], "delete-prepare")
    assert confirmation["snapshot"]["target_id"] == clip["clip_id"]
    deleted = store.delete(confirmation["confirmation_token"], "delete-commit")
    assert deleted["status"] == "deleted"
    assert (review_root / clip["take_id"] / "receipt.json").is_file()
    assert not (review_root / clip["take_id"] / "capture.mp4").exists()
    with store._connect() as db:
        assert db.execute("SELECT 1 FROM request_tombstones WHERE request_id=?", ("delete-commit",)).fetchone()
    after = store.workspace()
    assert after["selection"] is None
    assert after["invalidated_selection"]["clip_id"] == clip["clip_id"]
    with pytest.raises(ShowrunError) as failure:
        store.describe_media("default", clip["clip_id"])
    assert failure.value.code == "selection_conflict"
    # A moved/extracted ZIP is self-describing and does not need the private store.
    extract = tmp_path / "extracted"
    extract.mkdir()
    with zipfile.ZipFile(BytesIO(zip_data)) as archive:
        archive.extractall(extract)
    assert (extract / "receipts" / f"{clip['take_id']}.json").is_file()
    assert selected["selection"]["clip_id"] == clip["clip_id"]


def test_demo_delete_confirmation_is_invalidated_by_new_take(review_root):
    store = ReviewStore(review_root)
    demo = store.register_demo("explicit demo", "demo-explicit")
    store.attach_take("demo-explicit", "take-a", demo["version"])
    current = store.get_demo("demo-explicit")
    confirmation = store.prepare_delete("demo", "demo-explicit", current["version"], "prepare-demo")
    _take(review_root, "take-c", "green")
    store.sync()
    store.attach_take("demo-explicit", "take-c", current["version"])
    with pytest.raises(ShowrunError) as failure:
        store.delete(confirmation["confirmation_token"], "commit-demo")
    assert failure.value.code == "review_conflict"


def test_service_requires_auth_and_serves_actual_mp4_ranges(review_root):
    service = ReviewService(
        ReviewStore(review_root),
        port=0,
        authorized_workspaces={"default": None},
    )
    info = service.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as unauthorized:
            urllib.request.urlopen(f"http://{info['host']}:{info['port']}/")
        assert unauthorized.value.code == 401
        state = ReviewStore(review_root).workspace()
        clip = _clips(state)[0]
        service.store.select_clip("default", clip["demo_id"], clip["take_id"], clip["clip_id"],
                                 state["version"], "service-select")
        bearer = {"Authorization": f"Bearer {service.token}"}
        page = urllib.request.urlopen(
            urllib.request.Request(
                f"http://{info['host']}:{info['port']}/?workspace_id=default",
                headers=bearer,
            )
        )
        assert page.status == 200 and b"Showrun" in page.read()
        media = urllib.request.Request(
            f"http://{info['host']}:{info['port']}/media/default/{clip['clip_id']}",
            headers={**bearer,
                     "Range": "bytes=0-1023"},
        )
        response = urllib.request.urlopen(media)
        assert response.status == 206 and len(response.read()) == 1024
    finally:
        service.stop()
