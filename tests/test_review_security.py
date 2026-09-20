"""Backend security regressions for the retained capture-review boundary."""

import io
import json
import multiprocessing
import threading
import urllib.error
import urllib.request
import zipfile
from http.cookiejar import CookieJar
from pathlib import Path

import pytest
from test_review import _clips, _media, _take
from test_showrun import MODEL, request

from amplifier_smart_tool_showrun import ReviewStore, ShowrunError
from amplifier_smart_tool_showrun.cli import main
from amplifier_smart_tool_showrun.review_server import ReviewService
from amplifier_smart_tool_showrun.store import Store


def _public_read_worker(root, queue, count):
    try:
        store = ReviewStore(root)
        for _ in range(count):
            store.workspace("default")
            store.notes("default")
        queue.put(None)
    except Exception as error:  # pragma: no cover - reported to the parent
        queue.put(repr(error))


@pytest.fixture
def security_root(tmp_path):
    _take(tmp_path, "take-a", "red")
    _take(tmp_path, "take-b", "blue")
    return tmp_path


def _select(store, workspace_id, clip, request_id):
    state = store.workspace(workspace_id)
    return store.select_clip(
        workspace_id,
        clip["demo_id"],
        clip["take_id"],
        clip["clip_id"],
        state["version"],
        request_id,
    )


def _intent_payload(clip, expected_version, text, save_request_id, submit_request_id=None):
    return {
        "target": {
            "demo_id": clip["demo_id"],
            "take_id": clip["take_id"],
            "clip_id": clip["clip_id"],
            "content_sha256": clip["content_sha256"],
        },
        "payload": {
            "text": text,
            "anchor": {},
            "expected_version": expected_version,
            "request_id": save_request_id if submit_request_id is None else None,
            "save_request_id": save_request_id if submit_request_id is not None else None,
            "submit_request_id": submit_request_id,
        },
    }


def test_delete_confirmation_result_is_durable_across_new_request_ids(security_root, monkeypatch):
    store = ReviewStore(security_root)
    clip = _clips(store.workspace())[0]
    confirmation = store.prepare_delete("clip", clip["clip_id"], clip["version"], "prepare-delete")
    original_unlink = Path.unlink

    def fail_media_unlink(path, *args, **kwargs):
        if path.name == "capture.mp4":
            raise PermissionError("simulated cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_media_unlink)
    first = store.delete(confirmation["confirmation_token"], "commit-one")
    assert first["status"] == "cleanup_incomplete"
    assert first["media_removed"] is False
    assert (security_root / clip["take_id"] / "capture.mp4").is_file()

    # A new UI request identity cannot turn the recorded incomplete cleanup into
    # a success, and the dict convenience path is equally bound to the intent.
    assert store.delete(confirmation["confirmation_token"], "commit-two") == first
    assert store.delete(confirmation, "commit-three") == first


def test_delete_recovery_does_not_infer_success_from_missing_media(security_root):
    store = ReviewStore(security_root)
    clip = _clips(store.workspace())[0]
    confirmation = store.prepare_delete("clip", clip["clip_id"], clip["version"], "prepare-crash")
    with store._connect() as db:
        intent = db.execute(
            "SELECT * FROM delete_intents WHERE token=?",
            (confirmation["confirmation_token"],),
        ).fetchone()
    snapshot = store._admit_deletion(intent, None)
    assert snapshot["clip_ids"] == [clip["clip_id"]]
    (security_root / clip["take_id"] / "capture.mp4").unlink()

    uncertain = store.delete(confirmation["confirmation_token"], "recover-after-effect")
    assert uncertain["status"] == "uncertain"
    assert uncertain["recovery_uncertain"] is True
    assert store.delete(confirmation["confirmation_token"], "recover-again") == uncertain


def test_deletion_revokes_note_text_in_all_workspaces_and_operation_receipts(security_root):
    store = ReviewStore(security_root)
    clips = _clips(store.workspace())
    clip_a, clip_b = clips

    selected_a = _select(store, "default", clip_a, "select-a")
    draft = store.save_draft(
        "default",
        clip_a["clip_id"],
        "SECRET-A-DRAFT",
        expected_version=selected_a["version"],
        request_id="draft-a",
    )
    note_a = store.submit_note(
        "default",
        clip_a["clip_id"],
        expected_version=draft["version"],
        request_id="note-a",
    )
    _select(store, "other", clip_a, "select-a-other")
    note_a_other = store.submit_note(
        "other",
        clip_a["clip_id"],
        text="SECRET-A-OTHER",
        expected_version=store.workspace("other")["version"],
        request_id="note-a-other",
    )
    selected_b = _select(store, "other", clip_b, "select-b-other")
    note_b = store.submit_note(
        "other",
        clip_b["clip_id"],
        text="SIBLING-NOTE",
        expected_version=selected_b["version"],
        request_id="note-b",
    )
    assert note_a["note"]["text"] == "SECRET-A-DRAFT"
    assert note_a_other["note"]["text"] == "SECRET-A-OTHER"
    assert note_b["note"]["text"] == "SIBLING-NOTE"

    confirmation = store.prepare_delete("clip", clip_a["clip_id"], clip_a["version"], "prepare-revoke")
    store.delete(confirmation["confirmation_token"], "commit-revoke")

    assert all(note["clip_id"] != clip_a["clip_id"] for note in store.notes("default"))
    assert all(note["clip_id"] != clip_a["clip_id"] for note in store.notes("other"))
    assert any(note["text"] == "SIBLING-NOTE" for note in store.notes("other"))
    state = store.workspace("default")
    assert "SECRET-A" not in json.dumps(state)
    with store._connect() as db:
        operation_text = "\n".join(
            row["result_json"]
            for row in db.execute(
                "SELECT result_json FROM operations WHERE request_id IN ('draft-a','note-a','note-a-other')"
            )
        )
    assert "SECRET-A" not in operation_text

    zip_data, _ = store.download_zip("default", clip_a["demo_id"])
    assert b"SECRET-A" not in zip_data


def test_workspace_scope_covers_public_reads_mutations_counts_cli_and_service(security_root, capsys):
    unrestricted = ReviewStore(security_root)
    clips = _clips(unrestricted.workspace())
    clip_a, clip_b = clips
    scope = {"only-a": {clip_a["demo_id"]}}
    store = ReviewStore(security_root, workspace_scopes=scope)

    assert [demo["demo_id"] for demo in store.workspace("only-a")["demos"]] == [clip_a["demo_id"]]
    assert store.workspace("only-a")["new_take_count"] == 0
    for operation in (
        lambda: store.get_demo(clip_b["demo_id"], workspace_id="only-a"),
        lambda: store.get_take(clip_b["take_id"], workspace_id="only-a"),
        lambda: store.get_clip(clip_b["clip_id"], workspace_id="only-a"),
        lambda: store.rename("clip", clip_b["clip_id"], "outside", clip_b["version"], "rename-outside",
                             workspace_id="only-a"),
        lambda: store.prepare_delete("clip", clip_b["clip_id"], clip_b["version"], "prepare-outside",
                                     workspace_id="only-a"),
        lambda: store.download_zip("only-a", clip_b["demo_id"]),
    ):
        with pytest.raises(ShowrunError) as failure:
            operation()
        assert failure.value.code == "scope_denied"
    with pytest.raises(ShowrunError) as failure:
        store.workspace("other")
    assert failure.value.code == "scope_denied"

    assert main([
        "--storage", str(security_root), "review", "demo", clip_b["demo_id"],
        "--workspace", "only-a", "--demo", clip_a["demo_id"],
    ]) == 1
    assert "scope_denied" in capsys.readouterr().out

    service = ReviewService(
        ReviewStore(security_root),
        port=0,
        authorized_workspaces=scope,
    )
    try:
        with pytest.raises(ShowrunError) as failure:
            service.call("workspace", {"workspace_id": "other"})
        assert failure.value.code == "scope_denied"
        assert service.call("workspace", {"workspace_id": "only-a"})["demos"]
    finally:
        service.stop()


def test_draft_and_appearance_use_real_workspace_cas_and_exact_retries(security_root):
    store = ReviewStore(security_root)
    clip = _clips(store.workspace())[0]
    selected = _select(store, "default", clip, "cas-select")
    first = store.save_draft(
        "default", clip["clip_id"], "first", expected_version=selected["version"], request_id="cas-draft"
    )
    with pytest.raises(ShowrunError) as failure:
        store.save_draft(
            "default", clip["clip_id"], "second", expected_version=selected["version"], request_id="cas-stale"
        )
    assert failure.value.code == "review_conflict"
    assert store.save_draft(
        "default", clip["clip_id"], "first", expected_version=selected["version"], request_id="cas-draft"
    ) == first

    appearance = store.set_appearance("default", "dark", first["version"], "cas-appearance")
    with pytest.raises(ShowrunError) as failure:
        store.set_appearance("default", "light", first["version"], "cas-stale-appearance")
    assert failure.value.code == "review_conflict"
    assert store.set_appearance("default", "dark", first["version"], "cas-appearance") == appearance
    assert store.workspace("default")["version"] == appearance["version"]


def test_zip_snapshot_rejects_media_replaced_after_initial_hash(security_root, monkeypatch):
    store = ReviewStore(security_root)
    clip = _clips(store.workspace())[0]
    original_read_bytes = Path.read_bytes
    raced = False

    def replace_after_initial_hash(path):
        nonlocal raced
        if path.name == "capture.mp4" and not raced:
            raced = True
            path.write_bytes(b"substituted-media")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", replace_after_initial_hash)
    _, inventory = store.download_zip("default", clip["demo_id"])
    assert inventory["complete"] is False
    media_entry = next(entry for entry in inventory["entries"] if entry.get("clip_id") == clip["clip_id"])
    assert media_entry["status"] == "changed"


def test_zip_snapshot_scope_is_pinned_before_new_take_arrival(security_root):
    store = ReviewStore(security_root)
    clip = _clips(store.workspace())[0]
    data, inventory, scope_token = store.prepare_zip("default", clip["demo_id"])
    assert inventory["scope_token"] == scope_token
    _take(security_root, "take-c", "green")
    store.sync()
    grouped = store.get_demo(clip["demo_id"])
    store.attach_take(clip["demo_id"], "take-c", grouped["version"])
    with pytest.raises(ShowrunError) as failure:
        store.assert_zip_scope("default", clip["demo_id"], scope_token)
    assert failure.value.code == "transfer_expired"
    assert "clip-take-c" not in json.dumps(inventory)
    assert data


def test_review_intent_recovery_is_exact_and_same_text_can_be_new(security_root):
    store = ReviewStore(security_root)
    clip = _clips(store.workspace())[0]
    selected = _select(store, "default", clip, "intent-select")
    intent_payload = {
        "workspace_id": "default",
        "demo_id": clip["demo_id"],
        "take_id": clip["take_id"],
        "clip_id": clip["clip_id"],
        "content_sha256": clip["content_sha256"],
        "text": "intent text",
        "anchor": {},
        "expected_version": selected["version"],
        "save_request_id": "intent-save",
        "submit_request_id": "intent-submit",
    }
    store.begin_intent("default", "intent-one", "submit_note", intent_payload)
    assert ReviewStore(security_root).workspace()["review_intents"][0]["state"] == "pending"
    saved = store.save_draft(
        "default", clip["clip_id"], "intent text", expected_version=selected["version"],
        request_id="intent-save", intent_id="intent-one",
    )
    assert ReviewStore(security_root).workspace()["review_intents"][0]["state"] == "prepared"
    submitted = store.submit_note(
        "default", clip["clip_id"], text="intent text", expected_version=saved["version"],
        request_id="intent-submit", intent_id="intent-one",
    )
    fresh = ReviewStore(security_root)
    assert fresh.submit_note(
        "default", clip["clip_id"], text="intent text", expected_version=saved["version"],
        request_id="intent-submit", intent_id="intent-one",
    ) == submitted
    assert len(fresh.notes()) == 1

    # Identical text is a deliberate new operation when it receives a new intent/request ID.
    current = fresh.workspace()
    second = fresh.select_clip(
        "default", clip["demo_id"], clip["take_id"], clip["clip_id"], current["version"], "intent-select-two"
    )
    fresh.begin_intent(
        "default", "intent-two", "save_draft",
        {**intent_payload, "expected_version": second["version"], "request_id": "intent-save-two"},
    )
    fresh.save_draft(
        "default", clip["clip_id"], "intent text", expected_version=second["version"],
        request_id="intent-save-two", intent_id="intent-two",
    )
    assert len(fresh.notes()) == 1


@pytest.mark.parametrize(
    ("effect_done", "acknowledged"),
    [(False, False), (True, False), (True, True)],
)
def test_review_intent_lifecycle_matrix(security_root, effect_done, acknowledged):
    store = ReviewStore(security_root)
    clip = _clips(store.workspace())[0]
    workspace_id = f"matrix-{int(effect_done)}-{int(acknowledged)}"
    selected = _select(store, workspace_id, clip, f"matrix-select-{effect_done}-{acknowledged}")
    payload = _intent_payload(
        clip, selected["version"], "matrix note", "matrix-save", "matrix-submit"
    )
    began = store.begin_intent(workspace_id, "matrix-intent", "submit_note", payload)
    assert began["payload"]["schema_version"] == 1
    assert began["payload"]["target"]["clip_id"] == clip["clip_id"]
    assert began["state"] == "pending"

    if effect_done:
        saved = store.save_draft(
            workspace_id,
            clip["clip_id"],
            "matrix note",
            expected_version=selected["version"],
            request_id="matrix-save",
            intent_id="matrix-intent",
        )
        submitted = store.submit_note(
            workspace_id,
            clip["clip_id"],
            text="matrix note",
            expected_version=saved["version"],
            request_id="matrix-submit",
            intent_id="matrix-intent",
        )
        assert submitted["status"] == "note_submitted"
        if acknowledged:
            assert store.ack_intent(workspace_id, "matrix-intent")["state"] == "acknowledged"

    fresh = ReviewStore(security_root)
    intent = next(
        item for item in fresh.workspace(workspace_id)["review_intents"]
        if item["intent_id"] == "matrix-intent"
    )
    expected_state = (
        "acknowledged" if acknowledged else "completed_unacknowledged" if effect_done else "pending"
    )
    assert intent["state"] == expected_state
    if effect_done:
        replay = fresh.submit_note(
            workspace_id,
            clip["clip_id"],
            text="matrix note",
            expected_version=fresh.workspace(workspace_id)["version"],
            request_id="matrix-submit",
            intent_id="matrix-intent",
        )
        assert replay == submitted
        assert len(fresh.notes(workspace_id)) == 1
        if not acknowledged:
            assert fresh.ack_intent(workspace_id, "matrix-intent")["state"] == "acknowledged"
    else:
        saved = fresh.save_draft(
            workspace_id,
            clip["clip_id"],
            "matrix note",
            expected_version=selected["version"],
            request_id="matrix-save",
            intent_id="matrix-intent",
        )
        assert saved["status"] == "draft_saved"
        assert fresh.workspace(workspace_id)["review_intents"][0]["state"] == "prepared"
        submitted = fresh.submit_note(
            workspace_id,
            clip["clip_id"],
            text="matrix note",
            expected_version=saved["version"],
            request_id="matrix-submit",
            intent_id="matrix-intent",
        )
        assert submitted["status"] == "note_submitted"
        assert fresh.ack_intent(workspace_id, "matrix-intent")["state"] == "acknowledged"

    if acknowledged:
        current = fresh.workspace(workspace_id)
        selected_again = fresh.select_clip(
            workspace_id,
            clip["demo_id"],
            clip["take_id"],
            clip["clip_id"],
            current["version"],
            "matrix-select-new",
        )
        new_payload = _intent_payload(
            clip, selected_again["version"], "matrix note", "matrix-save-new"
        )
        fresh.begin_intent(workspace_id, "matrix-intent-new", "save_draft", new_payload)
        new_draft = fresh.save_draft(
            workspace_id,
            clip["clip_id"],
            "matrix note",
            expected_version=selected_again["version"],
            request_id="matrix-save-new",
            intent_id="matrix-intent-new",
        )
        assert new_draft["draft"]["version"] > saved["draft"]["version"]


def test_intent_admission_and_deletion_revoke_all_recovery_surfaces(security_root):
    store = ReviewStore(security_root)
    clip_a, clip_b = _clips(store.workspace())
    selected_default = _select(store, "default", clip_a, "revoke-select-default")
    selected_other = _select(store, "other", clip_a, "revoke-select-other")
    pending_payload = _intent_payload(
        clip_a, selected_default["version"], "SECRET-PENDING", "revoke-pending-save"
    )
    completed_payload = _intent_payload(
        clip_a, selected_other["version"], "SECRET-COMPLETED", "revoke-save", "revoke-submit"
    )
    store.begin_intent("default", "revoke-pending", "save_draft", pending_payload)
    store.begin_intent("other", "revoke-completed", "submit_note", completed_payload)
    saved = store.save_draft(
        "other", clip_a["clip_id"], "SECRET-COMPLETED",
        expected_version=selected_other["version"], request_id="revoke-save", intent_id="revoke-completed",
    )
    store.submit_note(
        "other", clip_a["clip_id"], text="SECRET-COMPLETED",
        expected_version=saved["version"], request_id="revoke-submit", intent_id="revoke-completed",
    )
    assert store.ack_intent("other", "revoke-completed")["state"] == "acknowledged"

    # A caller cannot admit a foreign target into a workspace scope before persistence.
    scoped = ReviewStore(security_root, workspace_scopes={"only-a": {clip_a["demo_id"]}})
    with pytest.raises(ShowrunError) as failure:
        scoped.begin_intent(
            "only-a",
            "foreign-intent",
            "save_draft",
            _intent_payload(clip_b, 0, "FOREIGN", "foreign-save"),
        )
    assert failure.value.code == "scope_denied"

    confirmation = store.prepare_delete(
        "clip", clip_a["clip_id"], clip_a["version"], "revoke-delete", workspace_id="default"
    )
    store.delete(confirmation["confirmation_token"], "revoke-delete-commit", workspace_id="default")
    for workspace_id, intent_id in (("default", "revoke-pending"), ("other", "revoke-completed")):
        public = next(
            item for item in store.workspace(workspace_id)["review_intents"]
            if item["intent_id"] == intent_id
        )
        assert public["state"] == "revoked"
        assert public["payload"] is None
        assert public["result"] == {
            "status": "revoked", "intent_id": intent_id, "reason": "review_deleted"
        }
        assert "SECRET-" not in json.dumps(public)
        with pytest.raises(ShowrunError) as failure:
            if intent_id == "revoke-pending":
                store.save_draft(
                    workspace_id,
                    clip_a["clip_id"],
                    "SECRET-CALL",
                    expected_version=store.workspace(workspace_id)["version"],
                    request_id=f"{intent_id}-retry",
                    intent_id=intent_id,
                )
            else:
                store.submit_note(
                    workspace_id,
                    clip_a["clip_id"],
                    text="SECRET-CALL",
                    expected_version=store.workspace(workspace_id)["version"],
                    request_id=f"{intent_id}-retry",
                    intent_id=intent_id,
                )
        assert failure.value.code == "intent_revoked"
    assert store.notes("other") == []
    assert store.workspace("default")["selection"] is None
    assert clip_b["clip_id"] in {note["clip_id"] for note in store.notes("other")} or not store.notes("other")


def test_concurrent_delete_has_one_cleanup_executor_in_threads_and_processes(security_root, monkeypatch):
    store = ReviewStore(security_root)
    clip = _clips(store.workspace())[0]
    confirmation = store.prepare_delete("clip", clip["clip_id"], clip["version"], "prepare-race")
    original_unlink = Path.unlink
    barrier = threading.Barrier(2)
    calls = []
    calls_lock = threading.Lock()

    def counted_unlink(path, *args, **kwargs):
        if path.name == "capture.mp4":
            with calls_lock:
                calls.append(str(path))
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", counted_unlink)
    results = []

    def thread_delete(request_id):
        barrier.wait()
        results.append(ReviewStore(security_root).delete(confirmation["confirmation_token"], request_id))

    threads = [threading.Thread(target=thread_delete, args=(f"thread-{n}",)) for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    final = ReviewStore(security_root).delete(confirmation["confirmation_token"], "thread-final")
    assert final["status"] == "deleted"
    assert len(calls) == 1
    assert all(item["status"] in {"deleted", "pending"} for item in results)

    # A second process must observe the durable admission rather than retrying an
    # already-started external unlink.  A second unlink would surface as cleanup_incomplete.
    root = security_root.parent / "process-delete"
    root.mkdir()
    _take(root, "take-a", "red")
    process_store = ReviewStore(root)
    process_clip = _clips(process_store.workspace())[0]
    process_confirmation = process_store.prepare_delete(
        "clip", process_clip["clip_id"], process_clip["version"], "prepare-process-race"
    )
    ready = multiprocessing.Event()
    queue = multiprocessing.Queue()

    def process_delete(request_id):
        ready.wait()
        try:
            queue.put(ReviewStore(root).delete(process_confirmation["confirmation_token"], request_id))
        except Exception as error:  # pragma: no cover - reported to the parent
            queue.put({"error": repr(error)})

    workers = [
        multiprocessing.Process(target=process_delete, args=(f"process-{n}",))
        for n in range(2)
    ]
    for worker in workers:
        worker.start()
    ready.set()
    process_results = [queue.get(timeout=30) for _ in workers]
    for worker in workers:
        worker.join(timeout=30)
    process_final = ReviewStore(root).delete(process_confirmation["confirmation_token"], "process-final")
    assert process_final["status"] == "deleted"
    assert all(item.get("status") in {"deleted", "pending"} for item in process_results)
    assert not (root / "take-a" / "capture.mp4").exists()


def test_http_bootstrap_is_one_time_and_cookie_mutations_need_csrf(security_root):
    service = ReviewService(
        ReviewStore(security_root),
        port=0,
        authorized_workspaces={"default": None},
    )
    info = service.start()
    base = f"http://{info['host']}:{info['port']}"
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        page = opener.open(info["url"])
        assert page.status == 200 and "?token=" not in page.geturl()
        with pytest.raises(urllib.error.HTTPError) as replay:
            opener.open(info["url"])
        assert replay.value.code == 401

        csrf = next(cookie.value for cookie in jar if cookie.name == "showrun_review_csrf")
        body = json.dumps({"operation": "workspace", "arguments": {"workspace_id": "default"}}).encode()

        def post(headers):
            request_value = urllib.request.Request(
                f"{base}/api/call",
                data=body,
                method="POST",
                headers={"Content-Type": "application/json", **headers},
            )
            try:
                return opener.open(request_value)
            except urllib.error.HTTPError as error:
                return error

        assert post({}).code == 400
        assert post({"Origin": f"{base}:1", "X-Showrun-CSRF": csrf}).code == 400
        assert post({"Origin": base}).code == 400
        assert post({"Origin": base, "X-Showrun-CSRF": csrf}).status == 200
        assert post({"Origin": base, "X-Showrun-CSRF": csrf, "Content-Type": "text/plain"}).code == 400

        bearer_request = urllib.request.Request(
            f"{base}/api/call",
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {service.token}", "Content-Type": "application/json"},
        )
        assert urllib.request.urlopen(bearer_request).status == 200
    finally:
        service.stop()


def test_http_zip_transfer_does_not_retarget_after_new_take(security_root):
    service = ReviewService(
        ReviewStore(security_root),
        port=0,
        authorized_workspaces={"default": None},
    )
    info = service.start()
    base = f"http://{info['host']}:{info['port']}"
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        opener.open(info["url"])
        csrf = next(cookie.value for cookie in jar if cookie.name == "showrun_review_csrf")
        clip = _clips(service.store.workspace())[0]
        body = json.dumps({
            "operation": "prepare_zip",
            "arguments": {"workspace_id": "default", "demo_id": clip["demo_id"]},
        }).encode()
        prepared = opener.open(urllib.request.Request(
            f"{base}/api/call",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Origin": base, "X-Showrun-CSRF": csrf},
        ))
        transfer = json.loads(prepared.read())
        assert transfer["status"] == "zip_inventory"
        _take(security_root, "take-c", "green")
        service.store.sync()
        grouped = service.store.get_demo(clip["demo_id"], workspace_id="default")
        source = service.store.get_demo("demo-take-c", workspace_id="default")
        workspace = service.store.workspace("default")
        service.store.attach_take(
            clip["demo_id"], "take-c", grouped["version"], source["version"],
            workspace_id="default", workspace_expected_version=workspace["version"],
        )
        url = (
            f"{base}/download/zip?workspace_id=default&demo_id={clip['demo_id']}"
            f"&transfer_id={transfer['transfer_id']}"
        )
        downloaded = opener.open(url).read()
        with zipfile.ZipFile(io.BytesIO(downloaded)) as archive:
            names = archive.namelist()
        assert not any("take-c" in name for name in names)
        assert transfer["scope_members"]
        assert all(member["id"] != "take-c" for member in transfer["scope_members"])
    finally:
        service.stop()


def test_concurrent_out_of_process_public_reads_do_not_lock_database(security_root):
    queue = multiprocessing.Queue()
    workers = [
        multiprocessing.Process(target=_public_read_worker, args=(security_root, queue, 30))
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    outcomes = [queue.get(timeout=60) for _ in workers]
    for worker in workers:
        worker.join(timeout=60)
    assert outcomes == [None, None]
    assert all(worker.exitcode == 0 for worker in workers)


def test_review_delete_preserves_capture_store_reservation(security_root, tmp_path):
    capture_root = tmp_path / "captured"
    capture_root.mkdir()
    capture_store = Store(capture_root)
    capture_request = request(identity="take-replay")
    initial_receipt = {"request_id": "take-replay", "status": "running"}
    assert capture_store.reserve(capture_request, MODEL, initial_receipt) is None
    take_dir = capture_root / "take-replay"
    media = _media(take_dir / "capture.mp4", "red")
    receipt = {
        "schema_version": 1,
        "request_id": "take-replay",
        "take_id": "take-replay",
        "status": "succeeded",
        "partial": False,
        "steps": [],
        "media": media,
        "error": None,
        "cleanup": "verified",
        "restricted": False,
    }
    capture_store.save(receipt)

    review = ReviewStore(capture_root)
    clip = _clips(review.workspace())[0]
    confirmation = review.prepare_delete("clip", clip["clip_id"], clip["version"], "prepare-capture")
    result = review.delete(confirmation["confirmation_token"], "commit-capture")
    assert result["status"] == "deleted"
    assert not (take_dir / "capture.mp4").exists()
    retry = capture_store.reserve(capture_request, MODEL, initial_receipt)
    assert retry is not None
    assert capture_store.status("take-replay")["status"] == "succeeded"