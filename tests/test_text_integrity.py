"""行尾卫生工具本身的行为：`check_bytes` / `normalize` / 遍历规则。

门（"全仓 LF + 结尾一个换行"）与修复器是同一份实现，测试打在它的判定面与修复面上——
只测"跑完没问题"证明不了它认得出问题（那样的门在规则坏掉时照样全绿）。
"""

from __future__ import annotations

from _tools import load_tool

FIX_EOL = load_tool("fix_eol")


def test_clean_bytes_have_no_problems():
    assert FIX_EOL.check_bytes("a\nb\n".encode("utf-8")) == []


def test_crlf_and_bare_cr_are_detected():
    assert any("CRLF" in detail for detail in FIX_EOL.check_bytes(b"a\r\nb\r\n"))
    assert any("裸 CR" in detail for detail in FIX_EOL.check_bytes(b"a\rb\n"))


def test_missing_and_extra_trailing_newline_are_detected():
    assert any("缺结尾换行" in detail for detail in FIX_EOL.check_bytes(b"a"))
    assert any("多余空行" in detail for detail in FIX_EOL.check_bytes(b"a\n\n"))


def test_normalize_fixes_every_problem_it_reports():
    for payload in (b"a\r\nb", b"a\rb", b"a", b"a\n\n\n"):
        fixed = FIX_EOL.normalize(payload)
        assert FIX_EOL.check_bytes(fixed) == [], (payload, fixed)


def test_normalize_is_idempotent():
    payload = b"x\r\ny"
    once = FIX_EOL.normalize(payload)
    assert FIX_EOL.normalize(once) == once


def test_binary_and_cache_paths_are_skipped(tmp_path):
    assert FIX_EOL.is_skipped(tmp_path / "a.pyc")
    assert FIX_EOL.is_skipped(tmp_path / "logo.png")
    assert FIX_EOL.is_skipped(tmp_path / ".venv")
    assert not FIX_EOL.is_skipped(tmp_path / "a.py")


def test_iter_text_files_skips_binaries_and_keeps_stable_order(tmp_path):
    (tmp_path / "b.txt").write_text("b\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "y.py").write_text("y\n", encoding="utf-8")
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / ".pytest_cache" / "z.py").write_text("z\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "w.py").write_text("w\n", encoding="utf-8")

    found = [path.name for path in FIX_EOL.iter_text_files(tmp_path)]
    assert found == ["a.txt", "b.txt"], found  # 排序稳定 + 跳过二进制与缓存/虚拟环境目录


def test_iter_text_files_scans_the_repository(repo_root):
    names = {path.name for path in FIX_EOL.iter_text_files(repo_root)}
    assert {"pyproject.toml", "README.md", "scoring.toml", "ci_smoke.py"} <= names
