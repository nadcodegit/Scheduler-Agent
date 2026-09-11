from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def test_conversations_tab_lists_only_runs_with_a_conversation(tmp_path: Path, qapp):
    from desktop_app.conversations_tab import ConversationsTab

    outputs = tmp_path / "outputs"
    outputs.mkdir()
    history = outputs / "run_history.jsonl"
    records = [
        {"timestamp": "2026-10-01T00:00:00", "subject": "schedule email", "conversation": None},
        {
            "timestamp": "2026-10-02T00:00:00",
            "subject": "Coverage needed",
            "sender": "scheduler@glocco.com",
            "gmail_draft_id": "draft-1",
            "conversation": {
                "exchanges": [{"question": "Can you cover this?", "answer": "Yes"}],
                "draft": "Hi,\n\nI can cover it.\n\nBest,\nNadereh",
            },
        },
    ]
    history.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    tab = ConversationsTab(tmp_path)

    assert tab.conversation_list.count() == 1
    html_out = tab.transcript_view.toHtml()
    assert "Can you cover this?" in html_out
    assert "Nadereh" in html_out
    assert "real Gmail draft" in html_out


def test_conversations_tab_shows_placeholder_when_none_exist(tmp_path: Path, qapp):
    from desktop_app.conversations_tab import ConversationsTab

    tab = ConversationsTab(tmp_path)

    assert tab.conversation_list.count() == 0
    assert "No completed conversations" in tab.transcript_view.toHtml()
