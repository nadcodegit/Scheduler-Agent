from __future__ import annotations

from scheduler_agents.tools.llm_json import strip_code_fence


def test_strip_code_fence_removes_markdown_json_fence():
    wrapped = '```json\n{"a": 1}\n```'
    assert strip_code_fence(wrapped) == '{"a": 1}'


def test_strip_code_fence_passes_through_plain_json():
    plain = '{"a": 1}'
    assert strip_code_fence(plain) == plain
