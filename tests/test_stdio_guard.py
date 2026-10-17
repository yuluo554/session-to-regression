"""编码守卫：非 UTF-8 管道上打印中文不得崩（本机跑得出 CI 才会红的缺陷）。"""

from __future__ import annotations

import io

from s2r import stdio


def test_cp1252_pipe_gets_reconfigured_and_can_print_chinese():
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    stdio.ensure_utf8_stdio(stdout=stream, stderr=stream)
    assert stream.encoding.lower().replace("-", "") in ("utf8", "utf-8")
    stream.write("中文输出\n")
    stream.flush()
    assert "中文输出".encode("utf-8") in raw.getvalue()


def test_utf8_pipe_is_left_alone():
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    stdio.ensure_utf8_stdio(stdout=stream, stderr=stream)
    assert stream.encoding.lower().replace("-", "") in ("utf8", "utf-8")


def test_cjk_console_encoding_is_preserved():
    """中文控制台（cp936）保持原样：改成 UTF-8 会把用户终端打乱。"""
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp936")
    stdio.ensure_utf8_stdio(stdout=stream, stderr=stream)
    assert stream.encoding.lower().replace("-", "") == "cp936"


def test_streams_without_reconfigure_are_tolerated():
    """StringIO 没有 reconfigure：守卫是加固而不是功能前提，不得抛错。"""
    plain = io.StringIO()
    stdio.ensure_utf8_stdio(stdout=plain, stderr=plain)
    plain.write("ok\n")


def test_none_streams_are_skipped():
    stdio.ensure_utf8_stdio(stdout=None, stderr=None)
