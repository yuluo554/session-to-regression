"""`regression/manifest.json` 的 schema 与约束：三态枚举、计数自洽、覆盖度门槛下发、证据字段。

为什么单列一份测试：manifest 是交付物里唯一"给别人复核的产物"——它的字段集、枚举与计数如果
只在实现里成立（没有守门），下次改动就会悄悄把"没验证过"折成"验证过了"，而读者看不出来。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from s2r import cli, config, errors, generate, readers, serialize, verify

CLAUDE_ROOT = "data/bench/claude"
CODEX_ROOT = "data/bench/codex"
SEED = Path("data/bench/repo/multi-bug-1")

#: manifest 每条必备的键（少了哪一项，"这条结论凭什么"就断了一环）
ENTRY_KEYS = {
    "episode_id",
    "session_id",
    "source",
    "source_file",
    "generated_at",
    "generator_version",
    "episode_status",
    "generation_status",
    "generation_reason",
    "assumed_convention",
    "assumed_parts",
    "test_path",
    "written",
    "status",
    "reason",
    "files",
    "red",
    "green",
}

STEP_KEYS = {"expectation", "exit_code", "outcome", "excerpt", "truncated"}


@pytest.fixture(scope="module")
def manifest(tmp_path_factory, repo_root):
    """真跑一次 `s2r run`（全链路），拿它的 stdout 作为被测 manifest（不手工拼一份假的）。"""
    from s2r import cli as cli_mod

    target = tmp_path_factory.mktemp("manifest-target") / "target"
    source = repo_root / SEED
    for path in sorted(source.rglob("*")):
        if path.is_file():
            destination = target / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(path.read_bytes())

    code = cli_mod.main(["run", "--target", str(target), "--claude-root", str(repo_root / CLAUDE_ROOT), "--codex-root", str(repo_root / CODEX_ROOT)])
    assert code == errors.EXIT_OK, "manifest 用例需要一次成功的全链路（至少一条 verified）"
    text = (target / "regression" / "manifest.json").read_text(encoding="utf-8")
    return target, json.loads(text), text


def test_manifest_schema_and_entry_keys(manifest):
    target, payload, _text = manifest
    assert payload["schema_version"] == verify.SCHEMA_VERSION
    assert set(payload) >= {
        "schema_version", "generator", "generator_version", "target", "manifest_path",
        "sources", "verify", "coverage", "counts", "entries", "skipped", "failed",
    }
    assert payload["manifest_path"] == config.generate_params(config.load_config(Path("config") / "scoring.toml"))["manifest_path"]
    assert payload["entries"], "空 manifest 不算交付物"
    for entry in payload["entries"]:
        assert set(entry) == ENTRY_KEYS, entry["episode_id"]
        for step in (entry["red"], entry["green"]):
            assert step is None or set(step) == STEP_KEYS
        assert entry["generator_version"] == payload["generator_version"]


def test_status_and_reason_are_constrained_enums(manifest):
    _target, payload, _text = manifest
    for entry in payload["entries"]:
        assert entry["status"] in verify.STATUSES
        if entry["status"] == verify.STATUS_VERIFIED:
            assert entry["reason"] == verify.REASON_NONE, "verified 不许带原因码"
            assert entry["red"]["exit_code"] == 1 and entry["green"]["exit_code"] == 0
        elif entry["status"] == verify.STATUS_UNVERIFIED:
            assert entry["reason"] in verify.UNVERIFIED_REASONS, entry
        else:
            assert entry["status"] == verify.STATUS_NEEDS_MANUAL
            assert entry["reason"], "needs_manual 必须带生成层的原因码（不许无理由降级）"
            assert entry["red"] is None and entry["green"] is None, "没跑就不许有退出码"


def test_counts_are_consistent_with_entries_and_writes(manifest):
    target, payload, _text = manifest
    counts = payload["counts"]
    entries = payload["entries"]
    assert counts["entries"] == len(entries)
    assert counts["verified"] + counts["unverified"] + counts["needs_manual"] == len(entries)
    written = [entry for entry in entries if entry["written"]]
    assert counts["written"] == len(written)
    assert all(entry["status"] == verify.STATUS_VERIFIED for entry in written), "写进正式目录的只能是 verified"
    for entry in written:
        assert (target / entry["test_path"]).is_file(), entry["test_path"]


def test_generated_at_comes_from_the_session_log(manifest, repo_root):
    """时间戳落法（禁时钟）：取会话日志自身的 `end_ts`，可用 `s2r scan` 复算。"""
    _target, payload, _text = manifest
    loaded = config.load_config(repo_root / "config" / "scoring.toml")
    limits = readers.limits_from_config(loaded)
    expected = {}
    for entry in payload["entries"]:
        key = entry["source_file"]
        if key not in expected:
            file_read = readers.read_file(Path(key), limits)
            entities = generate.split_episodes(file_read, loaded)
            expected[key] = {episode_id: entity.end_ts for episode_id, entity in entities.items()}
        assert entry["generated_at"] == expected[key][entry["episode_id"]], entry["episode_id"]
        assert entry["generated_at"] != "unknown", "日志里有时间戳就不该落 unknown"


def test_coverage_thresholds_are_dispatched_and_observed(manifest, repo_root):
    from s2r import bench

    _target, payload, _text = manifest
    bench_manifest = bench.load_manifest(repo_root / "data" / "bench")
    coverage = payload["coverage"]
    assert coverage["min_count_by_type"] == {key: int(value) for key, value in bench_manifest["coverage"]["min_count_by_type"].items()}
    assert coverage["source"] == "data/bench/manifest.json"
    assert coverage["observed"] == {status: payload["counts"][status] for status in verify.STATUSES}


def test_verify_block_records_the_config_ritual(manifest, repo_root):
    """复核者要能看出"这份结论是按哪一套判据跑出来的"——判据值必须逐项可见。"""
    _target, payload, _text = manifest
    loaded = config.load_config(repo_root / "config" / "scoring.toml")
    params = config.verify_params(loaded)
    block = payload["verify"]
    assert block["pytest_command"] == [str(item) for item in params["pytest_command"]]
    assert block["interpreter_token"] == str(params["interpreter_token"])
    assert block["timeout_seconds"] == int(params["timeout_seconds"])
    assert block["red_expectation"] == str(params["red_expectation"])
    assert block["green_expectation"] == str(params["green_expectation"])
    for entry in payload["entries"]:
        if entry["red"] is not None:
            assert entry["red"]["expectation"] == block["red_expectation"]
        if entry["green"] is not None:
            assert entry["green"]["expectation"] == block["green_expectation"]


def test_manifest_bytes_follow_the_serialize_contract(manifest):
    """写盘单点（K6）：LF + 结尾换行 + 排序键 + 无 CR。"""
    _target, payload, text = manifest
    assert text == serialize.dumps_json(payload)
    assert "\r" not in text
    assert text.endswith("\n")


def test_manifest_is_removable_no_private_bytes(manifest):
    """manifest 里不许出现骨架源码（它是证据链，不是产物副本）；源码在 `test_path` 指的文件里。"""
    _target, payload, _text = manifest
    for entry in payload["entries"]:
        assert "def test_" not in json.dumps(entry, ensure_ascii=False)
