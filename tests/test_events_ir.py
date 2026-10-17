"""事件流 IR：字段集契约、seq 语义、未知形态显式报错。"""

from __future__ import annotations

import pytest

from s2r import events, serialize
from s2r.errors import UnsupportedFormat


def make_event(**overrides):
    base = dict(source="claude", session_id="sess-demo-0001", seq=12, kind="tool_call", ts="2026-10-17T00:00:00Z", tool="Edit")
    base.update(overrides)
    return events.Event(**base)


def test_field_set_is_the_declared_contract():
    payload = make_event().to_dict()
    assert set(payload) == set(events.EVENT_FIELDS)
    assert len(events.EVENT_FIELDS) == len(set(events.EVENT_FIELDS))


def test_roundtrip_is_byte_stable():
    event = make_event(payload={"file_path": "/demo/alpha/src/calc.py", "old_string": "a"})
    assert event.to_json() == event.to_json()
    assert events.Event.from_json(event.to_json()).to_dict() == event.to_dict()
    assert events.Event.from_dict(event.to_dict()).to_json() == event.to_json()


def test_seq_is_a_source_line_number_not_an_array_index():
    """seq 必须 ≥1 且可反查源行；0（下标语义）必须被拒。"""
    with pytest.raises(UnsupportedFormat) as excinfo:
        make_event(seq=0)
    assert "seq" in str(excinfo.value)
    with pytest.raises(UnsupportedFormat):
        make_event(seq="12")


def test_unknown_kind_and_source_are_rejected():
    with pytest.raises(UnsupportedFormat):
        make_event(kind="tool_call_typo")
    with pytest.raises(UnsupportedFormat):
        make_event(source="some-other-agent")


@pytest.mark.parametrize("field", ["tool", "path"])
def test_empty_string_is_not_the_unknown_sentinel(field):
    with pytest.raises(UnsupportedFormat) as excinfo:
        make_event(**{field: ""})
    assert "unknown" in str(excinfo.value)


def test_missing_fields_on_deserialization_are_listed():
    payload = make_event().to_dict()
    del payload["path"]
    with pytest.raises(UnsupportedFormat) as excinfo:
        events.Event.from_dict(payload)
    assert excinfo.value.missing_keys == ("path",)


def test_unknown_values_are_carried_as_sentinel():
    """取不到就落 unknown：哨兵是 unknown 字符串，绝不是空串或 None。"""
    event = events.Event(source="claude", session_id="sess-demo-0001", seq=7, kind="meta")
    assert event.tool == events.UNKNOWN
    assert event.path == events.UNKNOWN
    assert event.ts == events.UNKNOWN
    assert event.payload == {}


def test_events_json_is_stable_for_a_list():
    session = [make_event(seq=index, kind="user_msg") for index in (3, 1, 2)]
    payload = [event.to_dict() for event in sorted(session, key=events.event_sort_key)]
    assert serialize.dumps_json(payload) == serialize.dumps_json([event.to_dict() for event in sorted(session, key=events.event_sort_key)])
    assert [row["seq"] for row in payload] == [1, 2, 3]


@pytest.mark.parametrize(
    "first_record, expected",
    [
        ({"type": "user", "message": {"role": "user"}}, "claude"),
        ({"type": "session_meta", "payload": {"id": "x"}}, "codex"),
    ],
)
def test_harness_detection_by_first_line_shape(first_record, expected):
    assert events.detect_harness(first_record) == expected


def test_harness_detection_rejects_unknown_and_ambiguous_shapes():
    with pytest.raises(UnsupportedFormat) as excinfo:
        events.detect_harness({"totally": "different"})
    assert excinfo.value.code == "unsupported_format"
    assert "totally" in excinfo.value.missing_keys

    with pytest.raises(UnsupportedFormat) as excinfo:
        events.detect_harness({"message": {}, "payload": {}})
    assert "多个 harness" in str(excinfo.value)

    with pytest.raises(UnsupportedFormat):
        events.detect_harness(["not", "an", "object"])  # type: ignore[arg-type]


def test_format_version_table_is_explicitly_empty_at_m0():
    """M0 不宣称任何版本指纹：检测一律显式报错（不是“宽松解析”）。"""
    assert set(events.FORMAT_VERSION_FINGERPRINTS) == {"claude", "codex"}
    assert all(not table for table in events.FORMAT_VERSION_FINGERPRINTS.values())
    with pytest.raises(UnsupportedFormat) as excinfo:
        events.detect_format_version("claude", {"type": "user", "message": {}})
    assert "M1" in str(excinfo.value)
