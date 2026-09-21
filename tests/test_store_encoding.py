"""Receipts must not depend on the host's default text encoding."""
import json
from pathlib import Path

from amplifier_smart_tool_showrun.store import atomic_json


def test_receipt_unicode_with_windows_default_encoding(tmp_path, monkeypatch):
    original = Path.open

    def windows_open(self, mode='r', buffering=-1, encoding=None, **kwargs):
        return original(self, mode, buffering=buffering,
                        encoding=(encoding or 'cp1252') if 'b' not in mode else None, **kwargs)

    monkeypatch.setattr(Path, 'open', windows_open)
    receipt = {'text': '\u202aBook1\u202c — 日本語 📊'}
    path = tmp_path / 'receipt.json'
    atomic_json(path, receipt)
    assert json.loads(path.read_bytes().decode('utf-8')) == receipt
