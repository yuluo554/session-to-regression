"""确定性写盘：字节形态由我们决定，不由默认值决定。"""

from __future__ import annotations

import json

from s2r import serialize


def test_dumps_json_shape_and_determinism():
    payload = {"b": 1, "a": ["中文", "x"], "nested": {"k": None}}
    first = serialize.dumps_json(payload)
    second = serialize.dumps_json(payload)
    assert first == second
    assert first.endswith("\n") and not first.endswith("\n\n")
    assert "\r" not in first
    assert "中文" in first, "ensure_ascii=False：中文不得被转成 \\uXXXX"
    assert first.index('"a"') < first.index('"b"') < first.index('"nested"'), "键序必须稳定排序"
    assert json.loads(first) == payload


def test_dumps_json_escapes_control_characters_and_emits_no_raw_cr():
    """JSON 里的 CR/LF 由 json 转义处理——产物中不得出现裸 CR 字节，且值可原样还原。"""
    text = serialize.dumps_json({"text": "a\r\nb"})
    assert "\r" not in text
    assert "\\r\\n" in text
    assert json.loads(text)["text"] == "a\r\nb"


def test_write_text_atomic_is_byte_stable_and_leaves_no_temp_files(tmp_path):
    first = serialize.write_text_atomic(tmp_path / "out" / "one.json", serialize.dumps_json({"k": "值"}))
    second = serialize.write_text_atomic(tmp_path / "out" / "two.json", serialize.dumps_json({"k": "值"}))
    assert serialize.sha256_file(first) == serialize.sha256_file(second)
    assert serialize.read_text(first) == serialize.read_text(second)
    leftovers = [path.name for path in first.parent.iterdir() if path.name.endswith(".tmp")]
    assert leftovers == [], leftovers
    assert first.read_bytes().endswith(b"\n")


def test_write_atomic_normalizes_crlf_input(tmp_path):
    target = serialize.write_text_atomic(tmp_path / "crlf.json", "a\r\nb\r\n")
    assert target.read_bytes() == b"a\nb\n"


def test_read_text_is_face_value_and_normalized_is_not(tmp_path):
    target = tmp_path / "crlf.txt"
    target.write_bytes(b"line1\r\nline2\r\n")
    assert "\r\n" in serialize.read_text(target)
    assert "\r\n" not in serialize.read_text_normalized(target)


def test_write_json_atomic_roundtrip(tmp_path):
    payload = {"episodes": [], "notes": [{"code": "x", "count": 2}]}
    target = serialize.write_json_atomic(tmp_path / "a" / "b.json", payload)
    assert json.loads(target.read_text(encoding="utf-8")) == payload


def test_sha_helpers_are_consistent(tmp_path):
    target = tmp_path / "f.txt"
    target.write_bytes("内容\n".encode("utf-8"))
    assert serialize.sha256_file(target) == serialize.sha256_text("内容\n")
    assert serialize.sha256_text("内容\n") == serialize.sha256_bytes("内容\n".encode("utf-8"))


def test_tree_digest_is_order_insensitive_and_content_sensitive(tmp_path):
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b\n", encoding="utf-8")
    one = serialize.tree_digest(tmp_path, ["a.txt", "b.txt"])
    two = serialize.tree_digest(tmp_path, ["b.txt", "a.txt"])
    assert one == two, "目录摘要必须与传入顺序无关"
    (tmp_path / "b.txt").write_text("b2\n", encoding="utf-8")
    assert serialize.tree_digest(tmp_path, ["a.txt", "b.txt"]) != one, "内容变化必须改变摘要"
