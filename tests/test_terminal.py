"""Terminal authority checks; no OS input or inference."""
import asyncio
from types import SimpleNamespace

import pytest
from test_desktop import Bridge, request
from test_showrun import MODEL

from amplifier_smart_tool_showrun import desktop
from amplifier_smart_tool_showrun.errors import ShowrunError
from amplifier_smart_tool_showrun.schema import validate


def terminal_request():
    r = request()
    r['target']['input_mode'] = 'terminal'
    r['authority']['ui'] = {'actions': ['type', 'key'], 'allowed_values': ['gh copilot'],
                            'allowed_keys': ['Enter'], 'target_effects': 'all_in_session'}
    return r


def test_terminal_requires_explicit_mode_and_keys():
    r = terminal_request()
    validate(r, MODEL)
    del r['target']['input_mode']
    with pytest.raises(ShowrunError):
        validate(r, MODEL)
    r = terminal_request()
    del r['authority']['ui']['allowed_keys']
    with pytest.raises(ShowrunError):
        validate(r, MODEL)


@pytest.mark.parametrize('value', ['gh copilot\n', '\x1b[31m', 'a\tb'])
def test_terminal_text_cannot_smuggle_control_keys(value):
    r = terminal_request()
    r['authority']['ui']['allowed_values'] = [value]
    with pytest.raises(ShowrunError):
        validate(r, MODEL)


@pytest.mark.parametrize('key', ['Command+Q', 'Control+D', 'arbitrary'])
def test_terminal_rejects_unknown_keys(key):
    r = terminal_request()
    r['authority']['ui']['allowed_keys'] = [key]
    with pytest.raises(ShowrunError):
        validate(r, MODEL)


@pytest.mark.parametrize('action', [
    {'action': 'type', 'ref': 'g1.terminal', 'text': 'unauthorized'},
    {'action': 'key', 'ref': 'g1.terminal', 'key': 'Control+C'},
    {'action': 'type', 'ref': 'g0.terminal', 'text': 'gh copilot'},
])
def test_terminal_rejections_precede_dispatch(tmp_path, action):
    r = terminal_request()
    d = desktop.Desktop(SimpleNamespace(config=r['target']), tmp_path,
                        {'width': 640, 'height': 360}, bridge=Bridge())
    d.ui = r['authority']['ui']
    d.observation = {'generation': 1, 'frames': [{'controls': [
        {'ref': 'g1.terminal', 'actions': ['type', 'key']}]}]}
    dispatched = []
    with pytest.raises(ShowrunError):
        asyncio.run(d.act(action, dispatched.append, 1))
    assert not dispatched


def test_terminal_type_and_submit_are_separate_actions(tmp_path):
    r = terminal_request()
    bridge = Bridge()
    bridge.generation = 1
    d = desktop.Desktop(SimpleNamespace(config=r['target']), tmp_path,
                        {'width': 640, 'height': 360}, bridge=bridge)
    d.ui = r['authority']['ui']
    d.observation = {'generation': 1, 'frames': [{'controls': [
        {'ref': 'g1.terminal', 'actions': ['type', 'key']}]}]}
    dispatched = []
    asyncio.run(d.act({'action': 'type', 'ref': 'g1.terminal', 'text': 'gh copilot'}, dispatched.append, 1))
    assert bridge.actions[0]['text'] == 'gh copilot'
    assert 'key' not in bridge.actions[0]
    asyncio.run(d.act({'action': 'key', 'ref': 'g1.terminal', 'key': 'Enter'}, dispatched.append, 1))
    assert bridge.actions[1]['key'] == 'Enter'
    assert dispatched == ['type', 'key']


def test_repeated_terminal_submission_is_rejected_before_dispatch(tmp_path):
    r = terminal_request()
    d = desktop.Desktop(SimpleNamespace(config=r['target']), tmp_path,
                        {'width': 640, 'height': 360}, bridge=Bridge())
    d.ui = r['authority']['ui']
    d.last_terminal_input = ('key', 'Enter')
    d.observation = {'generation': 2, 'frames': [{'controls': [
        {'ref': 'g2.terminal', 'actions': ['type', 'key']}]}]}
    dispatched = []
    with pytest.raises(ShowrunError) as error:
        asyncio.run(d.act({'action': 'key', 'ref': 'g2.terminal', 'key': 'Enter'}, dispatched.append, 2))
    assert error.value.code == 'desktop_no_progress'
    assert not dispatched
