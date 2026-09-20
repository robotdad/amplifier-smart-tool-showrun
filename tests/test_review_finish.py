"""Regressions for exact recovery, deletion scope and revoked download snapshots."""
import json

import pytest
from test_review import _clips, _take
from test_review_security import _intent_payload, _select

from amplifier_smart_tool_showrun import ReviewStore, ShowrunError


@pytest.fixture
def retained(tmp_path):
    _take(tmp_path, 'take-a', 'red')
    _take(tmp_path, 'take-b', 'blue')
    return ReviewStore(tmp_path)


def test_begin_retry_after_effect_keeps_original_receipt(retained):
    clip = _clips(retained.workspace())[0]
    selected = _select(retained, 'default', clip, 'select-a')
    payload = _intent_payload(clip, selected['version'], 'exact text', 'save-a')
    retained.begin_intent('default', 'intent-a', 'save_draft', payload)
    retained.save_draft('default', clip['clip_id'], 'exact text', expected_version=selected['version'],
                        request_id='save-a', intent_id='intent-a')
    assert retained.begin_intent('default', 'intent-a', 'save_draft', payload)['state'] == 'completed_unacknowledged'


def test_intent_cannot_be_submitted_against_another_clip(retained):
    a, b = _clips(retained.workspace())
    selected = _select(retained, 'default', a, 'select-a')
    retained.begin_intent('default', 'intent-a', 'submit_note',
                          _intent_payload(a, selected['version'], 'for A', 'save-a', 'submit-a'))
    selected = _select(retained, 'default', b, 'select-b')
    with pytest.raises(ShowrunError, match='target|clip'):
        retained.submit_note('default', b['clip_id'], text='for A', expected_version=selected['version'],
                             request_id='submit-a', intent_id='intent-a')
    assert retained.notes() == []


def test_delete_one_clip_preserves_other_take_intents(retained):
    a, b = _clips(retained.workspace())
    retained.attach_take(a['demo_id'], b['take_id'])
    a, b = _clips(retained.workspace())
    selected = _select(retained, 'default', b, 'select-b')
    retained.begin_intent('default', 'intent-b', 'save_draft',
                          _intent_payload(b, selected['version'], 'keep B', 'save-b'))
    confirmation = retained.prepare_delete('clip', a['clip_id'], a['version'], 'delete-a')
    retained.delete(confirmation)
    assert retained.workspace()['review_intents'][0]['state'] == 'pending'


def test_revoked_intent_cannot_be_read_from_another_workspace(retained):
    clip = _clips(retained.workspace())[0]
    selected = _select(retained, 'default', clip, 'select-a')
    payload = _intent_payload(clip, selected['version'], 'text', 'save-a')
    retained.begin_intent('default', 'intent-a', 'save_draft', payload)
    retained.delete(retained.prepare_delete('clip', clip['clip_id'], clip['version'], 'delete-a'))
    with pytest.raises(ShowrunError) as error:
        retained.begin_intent('other', 'intent-a', 'save_draft', payload)
    assert error.value.code == 'scope_denied'


@pytest.mark.parametrize('change', ['restricted', 'media'])
def test_prepared_zip_rechecks_retained_access(retained, change):
    clip = _clips(retained.workspace())[0]
    _, inventory, token = retained.prepare_zip('default', clip['demo_id'])
    folder = retained.takes_root / clip['take_id']
    if change == 'restricted':
        receipt = json.loads((folder / 'receipt.json').read_text())
        receipt['restricted'] = True
        (folder / 'receipt.json').write_text(json.dumps(receipt))
    else:
        (folder / 'capture.mp4').write_bytes(b'replaced')
    with pytest.raises(ShowrunError) as error:
        retained.assert_zip_scope('default', clip['demo_id'], token, inventory['scope_members'])
    assert error.value.code == 'transfer_expired'


def test_reselect_restores_acknowledged_draft_and_position(retained):
    a, b = _clips(retained.workspace())
    selected = _select(retained, 'default', a, 'select-a')
    retained.save_draft('default', a['clip_id'], 'saved A', expected_version=selected['version'], request_id='save-a')
    retained.set_playback('default', a['clip_id'], 0.75)
    _select(retained, 'default', b, 'select-b')
    _select(retained, 'default', a, 'select-a-again')
    state = retained.workspace()
    assert state['draft']['text'] == 'saved A'
    assert state['playback']['time_seconds'] == 0.75


def test_delete_rechecks_take_after_confirmation(retained):
    clip = _clips(retained.workspace())[0]
    confirmation = retained.prepare_delete('clip', clip['clip_id'], clip['version'], 'delete-a')
    receipt_path = retained.takes_root / clip['take_id'] / 'receipt.json'
    receipt = json.loads(receipt_path.read_text())
    receipt['status'] = 'running'
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ShowrunError):
        retained.delete(confirmation)
    assert (receipt_path.parent / 'capture.mp4').exists()


def test_rename_retry_cannot_bypass_narrowed_demo_scope(retained):
    a, b = _clips(retained.workspace())
    retained.rename('clip', b['clip_id'], 'private name', b['version'], 'rename-b')
    scoped = ReviewStore(retained.takes_root, workspace_scopes={'only-a': {a['demo_id']}})
    with pytest.raises(ShowrunError) as failure:
        scoped.rename('clip', b['clip_id'], 'private name', b['version'], 'rename-b', workspace_id='only-a')
    assert failure.value.code == 'scope_denied'


def test_cli_exports_do_not_overwrite_existing_files(retained, tmp_path, capsys):
    from amplifier_smart_tool_showrun.cli import main
    clip = _clips(retained.workspace())[0]
    _select(retained, 'default', clip, 'select-a')
    output = tmp_path / 'already-owned.mp4'
    output.write_bytes(b'keep original')
    assert main(['--storage', str(retained.takes_root), 'review', 'mp4', clip['clip_id'],
                 '--output', str(output)]) == 1
    assert output.read_bytes() == b'keep original'
    assert json.loads(capsys.readouterr().out)['status'] == 'failed'
