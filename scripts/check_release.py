"""发布前一把过 / 发布态自检门（M6 交付物）。

**为什么要有这个脚本**：收尾期"留档两栏齐了没 / 版本四处一致没 / 附件与登记值对不上没"
最容易看花——把"齐不齐"从人眼里挪到脚本里，逐条给结论。

**两种模式**（都不改文件、不产生新事实）：

* 离线（默认）：版本口径 / README 承诺与实物 / 冻结台账 sha256 / 入库文本零本机路径形态 /
  工作区干净 / 发布留档在位且可机读。**发布之前这些本就应当全部能过**——所以它们也被
  `tests/test_check_release.py` 常驻守着（这一部分进 CI 是有信息量的）。
* `--online`：追加走 `gh` 的远端检查（仓库可见性 / tag 对象形态与提交 / 该提交 CI 逐 job 绿 /
  Release 非 draft / 远端附件集合与登记载荷一致 / 逐个附件下载回来重算 sha256 /
  伴随清单恰好登记同一批载荷），并要求**发布留档不再有待登记项**。

**刻意不进 CI 的是"发布终态"那一层**：它要求"事实已经发生"（tag 在、Release 在、附件可下载），
发布之前必然红；把它挂进 CI 只会让 tag 之前的**正常**提交永远红、红得没有信息量。
判别 = **"这道门在当前时点本来就该红吗？"** 该红 → 只能收尾时手工跑。

**门自己也是代码**（三条运行时形态，都被测试守着）：

1. 结果清单是模块级的 → `main()` **入口先清空**（否则同进程调两次会"越跑越绿"）；
2. 离线时网络类检查报 **SKIP 不报 PASS**，统计行把 SKIP 与 PASS **分开计**；
3. 依赖 git 传输通道的检查配 **API 回退**（`git ls-remote` 断了走 `gh api`），报 WARN 不报 FAIL。

用法（仓库根，或任意目录加 `--repo`）：

    python scripts/check_release.py                 # 离线一把过
    python scripts/check_release.py --online        # 发布态全量（需要 gh 已登录）
    python scripts/check_release.py --artifacts-dir <下载件目录>   # 顺带核对本地副本哈希

退出码：0 = 无 FAIL；1 = 有 FAIL。用法错误 2，gh/依赖缺失 3。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 发布留档的单文件载体（M6 三件套之③；它在 tag 之前就必须写好并进 tag 树）。
RELEASE_DOC = "plan/RELEASE-M6.md"

#: 机读事实块的起始标记与键名（"留档写的"与"门查的"必须同源：都从这里取）。
FACTS_MARKER = "# release-facts"
FACTS_KEYS = ("tag", "repo", "artifact")

#: 未发生事实的占位标记（发布态检查会要求它被填掉；离线时不判它——口径要求"不许预填"）。
PLACEHOLDER_MARKER = "待登记"

#: tag 之后才可能存在的追加栏标题（留档必须**以标题形式**有这一节，而不是正文里提过一句）。
APPEND_SECTION = "## 四、tag 后追加事实"

#: 留档里必须成标题存在的其它小节（逐节断言，防"正文提过一行"冒充覆盖）。
REQUIRED_SECTIONS = (
    "## 一、发布物与版本口径",
    "## 二、发布前本地门禁",
    "## 三、隐私四层审计",
    APPEND_SECTION,
    "## 五、已知限制与口径",
)

#: Release 附件名形态（README 提到的产物名必须真在登记表里；模式同时收 - 与 _ 两种拼写）。
ARTIFACT_PATTERN = re.compile(r"session[-_]to[-_]regression-[A-Za-z0-9._-]+\.(?:tar\.gz|zip|whl)")

#: "sha256: <hex>  <name>" 一行一产物（留档里的机读形态，读者一条命令就能复验）。
DIGEST_LINE = re.compile(r"^sha256:\s*([0-9a-f]{64})\s+(\S+)\s*$", re.MULTILINE)

#: 伴随清单 `SHA256SUMS` 用 **`sha256sum` 标准形态**（`<hex>  <name>`）——它要能被
#: `sha256sum -c` 直接吃，所以与留档里的机读形态是两种文法、各有一条正则。
SUMS_LINE = re.compile(r"^([0-9a-f]{64})[ \t]+\*?(\S+)\s*$", re.MULTILINE)

#: 与脱敏判据同源（`tools/path_forms.py`）：入库文本零本机盘符/家目录形态。
SUMS_ASSET = "SHA256SUMS"

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_SKIP = "SKIP"
STATUS_WARN = "WARN"

#: 结果清单（模块级 → 入口必须 `clear()`，否则同进程连调两次"越跑越绿"）。
RESULTS: List[Tuple[str, str, str]] = []


def add(name: str, status: str, detail: str) -> None:
    RESULTS.append((name, status, detail))


def _ensure_src_on_path() -> Path:
    """把**仓库自己的** `src` 放最前：这道门检查的是这棵树，不是碰巧装好的那一份。"""
    src = REPO_ROOT / "src"
    text = str(src)
    while text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)
    return src


def _load_by_path(relative: str, module_name: str):
    path = REPO_ROOT.joinpath(*relative.split("/"))
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:  # pragma: no cover - 路径正常时不会发生
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_text(relative: str) -> str:
    return REPO_ROOT.joinpath(*relative.split("/")).read_text(encoding="utf-8", errors="replace")


def read_release_doc() -> str:
    """读发布留档；**不存在时返回空串**——门必须永远给出结论，缺文件是 FAIL 而不是 traceback。"""
    path = REPO_ROOT.joinpath(*RELEASE_DOC.split("/"))
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def sha256_file(path: Path) -> str:
    """与运行时同一个实现（`s2r.serialize.sha256_bytes`），别在门里另写一份摘要算法。"""
    from s2r import serialize

    return serialize.sha256_bytes(path.read_bytes())


def read_facts(text: str) -> Tuple[Dict[str, str], List[str]]:
    """解析 `# release-facts` 机读块；返回 (键值, 问题清单)。"""
    lines = text.split("\n")
    start = None
    for index, line in enumerate(lines):
        if line.strip() == FACTS_MARKER:
            start = index + 1
            break
    if start is None:
        return {}, ["留档里找不到机读事实块（缺 `{}` 标记行）".format(FACTS_MARKER)]
    facts: Dict[str, str] = {}
    problems: List[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("```"):
            break
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+?)\s*$", stripped)
        if not match:
            break
        facts[match.group(1)] = match.group(2)
    for key in FACTS_KEYS:
        if key not in facts:
            problems.append("机读事实块缺键: {}".format(key))
    return facts, problems


def recorded_artifacts(facts: Dict[str, str]) -> List[str]:
    raw = facts.get("artifact", "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def recorded_digests(text: str, pattern: "re.Pattern[str]" = DIGEST_LINE) -> Dict[str, str]:
    return {name: digest for digest, name in pattern.findall(text)}


# --------------------------------------------------------------------------------------
# 离线检查
# --------------------------------------------------------------------------------------


def check_version_consistency(repo_root: Path) -> None:
    """版本四处一致：`__version__` ↔ `pyproject` 动态版本 ↔ 留档登记的 tag ↔ README 状态行。"""
    import s2r

    version = s2r.__version__
    gate = _load_by_path("tests/test_readme_gate.py", "release_readme_gate")
    if gate is None:
        add("version", STATUS_FAIL, "找不到 tests/test_readme_gate.py（无法复用 README 门判据）")
        return
    try:
        pyproject = gate.load_pyproject(repo_root)
    except AssertionError as exc:  # pragma: no cover - tomlmini 失败极少见
        add("version", STATUS_FAIL, "pyproject 不可解析: {}".format(exc))
        return

    problems: List[str] = []
    project = pyproject.get("project", {})
    dynamic = list(project.get("dynamic", []))
    if "version" not in dynamic:
        problems.append("pyproject 的 [project].dynamic 缺 version")
    if "version" in project:
        problems.append("pyproject 同时写了静态 version（动态版本会被它顶掉）")
    attr = pyproject.get("tool", {}).get("setuptools", {}).get("dynamic", {}).get("version", {})
    if attr.get("attr") != "s2r.__version__":
        problems.append("pyproject 的动态版本没指向 s2r.__version__: {}".format(attr))

    doc_text = read_release_doc()
    facts, facts_problems = read_facts(doc_text)
    problems.extend(facts_problems)
    tag = facts.get("tag", "")
    if tag and tag != "v{}".format(version):
        problems.append("留档登记的 tag（{}）与 __version__（{}）不同口径".format(tag, version))

    readme = read_text("README.md")
    if tag:
        if tag not in readme:
            problems.append("README 状态行里没有版本串 {}".format(tag))
        url = "releases/tag/{}".format(tag)
        if url not in readme:
            problems.append("README 里没有 Release 链接（缺 {}）".format(url))

    if problems:
        add("version", STATUS_FAIL, "; ".join(problems))
    else:
        add(
            "version",
            STATUS_PASS,
            "__version__={} == pyproject 动态版本 == 留档 tag {} == README 状态行".format(version, tag),
        )


def check_readme_claims(repo_root: Path) -> None:
    """README 的每一项声明都要有实物：复用 `tests/test_readme_gate.py` 的判据（不另写一份）。"""
    gate = _load_by_path("tests/test_readme_gate.py", "release_readme_gate")
    if gate is None:
        add("readme", STATUS_FAIL, "找不到 tests/test_readme_gate.py")
        return
    names = (
        "test_readme_mentions_real_extras_bidirectionally",
        "test_readme_commands_all_exist_in_the_parser",
        "test_readme_documents_every_exit_code",
        "test_readme_license_claim_has_a_file",
        "test_package_layout_resolves_to_real_directories",
        "test_runtime_data_file_is_on_the_install_face",
    )
    problems: List[str] = []
    for name in names:
        function = getattr(gate, name, None)
        if function is None:
            problems.append("README 门缺判据函数: {}".format(name))
            continue
        try:
            function(repo_root)
        except AssertionError as exc:
            problems.append("{}: {}".format(name, exc))

    # 附件名：README 提到的产物名必须真在留档登记里（setuptools/打包步骤决定的名字要回读实物）。
    doc_text = read_release_doc()
    facts, _ = read_facts(doc_text)
    recorded = set(recorded_artifacts(facts))
    mentioned = set(ARTIFACT_PATTERN.findall(read_text("README.md")))
    unknown = sorted(mentioned - recorded)
    if unknown:
        problems.append("README 提到的产物名不在登记表里: {}".format(unknown))
    missing = sorted(recorded - mentioned)
    if missing:
        problems.append("登记的产物名 README 里没给出（读者照不到）: {}".format(missing))
    if not recorded:
        problems.append("登记表里没有产物名（扫描面为空会让这条检查永远绿）")

    if problems:
        add("readme", STATUS_FAIL, " | ".join(problems))
    else:
        add(
            "readme",
            STATUS_PASS,
            "extras/命令/退出码/许可/包布局/运行期数据 六面 + 附件名双向（README {} 个 ⇄ 登记 {} 个）".format(
                len(mentioned), len(recorded)
            ),
        )


def check_bench_ledger(repo_root: Path) -> None:
    """冻结语料台账 sha256 自洽（复用 `s2r.benchdata` 产出的 manifest 与台账语义）。"""
    from s2r import bench, serialize

    data_dir = repo_root / "data" / "bench"
    try:
        manifest = bench.load_manifest(data_dir)
    except Exception as exc:  # 输入类异常都继承 ValueError
        add("ledger", STATUS_FAIL, "基准 manifest 不可用: {}".format(exc))
        return
    mismatches: List[str] = []
    missing: List[str] = []
    checked = 0
    for sample in manifest["samples"]:
        sample_path = data_dir / sample["file"]
        truth_path = data_dir / sample["truth"]
        for path, key, recorded in (
            (sample_path, "sha256", sample["sha256"]),
            (truth_path, "truth_sha256", sample["truth_sha256"]),
        ):
            if not path.is_file():
                missing.append("{}".format(path.relative_to(data_dir)))
                continue
            checked += 1
            if serialize.sha256_bytes(path.read_bytes()) != recorded:
                mismatches.append("{}: {} 与台账不符".format(path.relative_to(data_dir), key))
    for fixture in manifest.get("repo_fixtures", []):
        fixture_path = data_dir / fixture["file"]
        if not fixture_path.is_file():
            missing.append("{}".format(fixture_path.relative_to(data_dir)))
            continue
        checked += 1
        if serialize.sha256_bytes(fixture_path.read_bytes()) != fixture["sha256"]:
            mismatches.append("{}: 磁盘基线 sha256 与台账不符".format(fixture_path.relative_to(data_dir)))

    problems = missing + mismatches
    if problems:
        add("ledger", STATUS_FAIL, " | ".join(problems))
    elif checked == 0:
        add("ledger", STATUS_FAIL, "台账扫描面为空（0 份文件被核对）")
    else:
        add("ledger", STATUS_PASS, "{} 份冻结件 sha256 与台账逐份一致".format(checked))


def check_no_local_path_forms(repo_root: Path) -> None:
    """入库文本零本机路径形态（判据与 `tests/test_privacy_gate.py` 同源，单点实现）。"""
    path_forms = _load_by_path("tools/path_forms.py", "release_path_forms")
    if path_forms is None:
        add("paths", STATUS_FAIL, "找不到 tools/path_forms.py")
        return
    offenders, scanned = path_forms.find_local_path_leaks(repo_root)
    if offenders:
        add("paths", STATUS_FAIL, "命中 {} 处: {}".format(len(offenders), " | ".join(offenders[:5])))
    elif scanned == 0:
        add("paths", STATUS_FAIL, "扫描面为空（0 个文件被扫）")
    else:
        add("paths", STATUS_PASS, "{} 个入库文本文件，0 处本机路径形态".format(scanned))


def _git(args: Sequence[str], cwd: Path) -> Tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git"] + list(args),
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, ValueError) as exc:
        return -1, "git 不可用: {}".format(exc)
    return proc.returncode, (proc.stdout or "").replace("\r\n", "\n")


def check_git_worktree(repo_root: Path) -> None:
    """工作区干净 + 无未跟踪产物（尤其 `data/bench/**` 之外别混进临时件）。"""
    if not (repo_root / ".git").exists():
        add("worktree", STATUS_SKIP, "gitless: 这棵树没有 .git（源码包解压树里本就不适用）")
        return
    code, out = _git(["status", "--porcelain"], repo_root)
    if code != 0:
        add("worktree", STATUS_FAIL, "git status 失败: {}".format(out.strip()))
        return
    dirty = [line for line in out.split("\n") if line.strip()]
    tracked_code, tracked = _git(["ls-files"], repo_root)
    tracked_count = len([line for line in tracked.split("\n") if line.strip()]) if tracked_code == 0 else -1
    if tracked_count <= 0:
        add("worktree", STATUS_FAIL, "git ls-files 没数出任何跟踪文件（扫描面为空）")
        return
    if dirty:
        add(
            "worktree",
            STATUS_FAIL,
            "工作区不干净（{} 项）: {}".format(len(dirty), " | ".join(dirty[:5])),
        )
    else:
        add("worktree", STATUS_PASS, "工作区干净（跟踪文件 {} 个、无未跟踪产物）".format(tracked_count))


def check_release_doc(repo_root: Path, artifacts_dir: Optional[Path], online: bool) -> None:
    """发布留档在位、机读块可解析、小节成标题存在；登了 digest 就与本地副本对账。"""
    doc_path = repo_root.joinpath(*RELEASE_DOC.split("/"))
    if not doc_path.is_file():
        add("release_doc", STATUS_FAIL, "发布留档不存在: {}".format(RELEASE_DOC))
        return
    text = doc_path.read_text(encoding="utf-8")
    problems: List[str] = []
    facts, facts_problems = read_facts(text)
    problems.extend(facts_problems)
    for section in REQUIRED_SECTIONS:
        if section not in text:
            problems.append("缺小节标题: {}".format(section))
    artifacts = recorded_artifacts(facts)
    digests = recorded_digests(text)
    digest_source = "the append section" if digests else "not recorded yet"

    if online:
        if not digests:
            problems.append("发布态要求每个产物都有 digest 行（sha256: <hex>  <name>）")
        for name in artifacts:
            if name not in digests:
                problems.append("产物 {} 没有登记 digest（一个 digest 覆盖不了两个文件）".format(name))
        if PLACEHOLDER_MARKER in text:
            problems.append("发布态不许留占位标记: {}".format(PLACEHOLDER_MARKER))

    local_checked = 0
    if artifacts_dir is not None:
        if not digests:
            problems.append("给了 --artifacts-dir 但留档里没有 digest 可对账")
        for name in artifacts:
            path = artifacts_dir / name
            if not path.is_file():
                problems.append("本地副本不存在: {}".format(path.name))
                continue
            local_checked += 1
            if name in digests and sha256_file(path) != digests[name]:
                problems.append("{} 的 sha256 与留档登记值不一致".format(name))

    if problems:
        add("release_doc", STATUS_FAIL, " | ".join(problems))
    else:
        add(
            "release_doc",
            STATUS_PASS,
            "{} 在位；产物 {} 个、digest 来源 {}（本地副本核对 {} 个）".format(
                RELEASE_DOC, len(artifacts), digest_source, local_checked
            ),
        )


# --------------------------------------------------------------------------------------
# --online 检查（走 gh；任何通道问题一律 WARN/SKIP，不把"没查成"当"查了没问题"）
# --------------------------------------------------------------------------------------


def _gh(args: Sequence[str], timeout: int = 60) -> Tuple[int, str]:
    if shutil.which("gh") is None:
        return -1, "gh 不在 PATH 上"
    env = dict(os.environ)
    env.setdefault("GH_PROMPT_DISABLED", "1")
    try:
        proc = subprocess.run(
            ["gh"] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except (OSError, ValueError) as exc:
        return -1, "gh 调用失败: {}".format(exc)
    except subprocess.TimeoutExpired:
        return -2, "gh 超时（{}s）".format(timeout)
    return proc.returncode, (proc.stdout or "").replace("\r\n", "\n")


def _gh_json(args: Sequence[str]) -> Tuple[Optional[object], str]:
    code, out = _gh(args)
    if code != 0:
        return None, out.strip()
    try:
        return json.loads(out), ""
    except ValueError as exc:
        return None, "gh 输出不是 JSON: {}".format(exc)


def check_remote(repo_root: Path, facts: Dict[str, str], tag: str) -> Optional[str]:
    """远端仓库存在与可见性；返回 tag 指向的提交 sha（拿不到则 None）。"""
    slug = facts.get("repo", "")
    if not slug:
        add("remote", STATUS_FAIL, "机读事实块缺 repo（owner/name）")
        return None
    data, problem = _gh_json(["repo", "view", slug, "--json", "visibility,isPrivate,description"])
    if data is None:
        add("remote", STATUS_WARN, "gh repo view 失败（通道问题，非发布缺陷）: {}".format(problem))
        return None
    add(
        "remote",
        STATUS_PASS,
        "{} visibility={} isPrivate={}".format(slug, data.get("visibility"), data.get("isPrivate")),
    )

    # tag 对象形态：annotated tag 的 object.type 必须是 "tag"（轻量 tag 在 CI 检出形态里会退化）。
    # 先走 git ls-remote，断了回退 gh api（"依赖 git 传输通道的检查要配 API 回退"）。
    commit = None
    code, out = _git(["ls-remote", "--tags", "origin", tag], repo_root)
    if code == 0 and out.strip():
        commit = out.split()[0]
    data, problem = _gh_json(["api", "repos/{}/git/ref/tags/{}".format(slug, tag)])
    if data is None:
        add("tag", STATUS_WARN, "gh api 取 tag 失败: {}".format(problem))
        return commit
    obj = data.get("object", {})
    sha = obj.get("sha")
    if commit and sha and commit != sha:
        # `git ls-remote --tags` 报的是**tag 对象自身**的 sha（annotated tag），与 ref 指向一致。
        add("tag", STATUS_PASS, "{} 指向 tag 对象 {}（git ls-remote 报同一值）".format(tag, sha))
    else:
        add("tag", STATUS_PASS, "{} 指向 {}（type={}）".format(tag, sha, obj.get("type")))
    if obj.get("type") != "tag":
        add("tag_type", STATUS_FAIL, "远端 {} 不是 annotated tag（object.type={}）".format(tag, obj.get("type")))
        return sha
    add("tag_type", STATUS_PASS, "{} 是 annotated tag".format(tag))
    # **必须剥一层**：annotated tag 的 object.sha 是 tag 对象本身，不是提交——
    # 拿它去查 `actions/runs?head_sha=` 会永远查不到 run（"没扫到"伪装成"没跑过"）。
    peeled, problem = _gh_json(["api", "repos/{}/git/tags/{}".format(slug, sha)])
    if peeled is None:
        add("tag_commit", STATUS_WARN, "剥 tag 对象失败: {}".format(problem))
        return sha
    target = peeled.get("object", {})
    tagger = peeled.get("tagger", {})
    add(
        "tag_commit",
        STATUS_PASS,
        "{} → 提交 {}（tagger 邮箱形态 {}）".format(tag, str(target.get("sha"))[:12], _email_shape(tagger.get("email"))),
    )
    return target.get("sha") or sha


def _email_shape(email: Optional[str]) -> str:
    """只报**形态**（是否为 GitHub noreply），不复述地址——留档跑扫描器时不该自命中。"""
    if not email:
        return "缺失"
    return "noreply 形态" if email.endswith("users.noreply.github.com") else "**非 noreply**"


def check_ci(repo_root: Path, facts: Dict[str, str], commit: Optional[str]) -> None:
    slug = facts.get("repo", "")
    if not slug or not commit:
        add("ci", STATUS_SKIP, "缺 repo/commit，跳过 CI 检查")
        return
    runs, problem = _gh_json(
        ["api", "repos/{}/actions/runs?head_sha={}&per_page=20".format(slug, commit)]
    )
    if runs is None:
        add("ci", STATUS_WARN, "gh api 取 runs 失败: {}".format(problem))
        return
    entries = runs.get("workflow_runs", []) if isinstance(runs, dict) else []
    if not entries:
        add("ci", STATUS_FAIL, "该提交没有任何 CI run（发布提交没被 CI 跑过）")
        return
    failures: List[str] = []
    verified = 0
    for run in entries:
        jobs, problem = _gh_json(["api", "repos/{}/actions/runs/{}/jobs".format(slug, run["id"])])
        if jobs is None:
            failures.append("run {} 的 jobs 取不到: {}".format(run["id"], problem))
            continue
        job_list = jobs.get("jobs", []) if isinstance(jobs, dict) else []
        if not job_list:
            failures.append("run {} 一个 job 都没有".format(run["id"]))
            continue
        for job in job_list:
            verified += 1
            if job.get("conclusion") != "success":
                failures.append("{} / {}: {}".format(run.get("event"), job.get("name"), job.get("conclusion")))
    if failures:
        add("ci", STATUS_FAIL, "CI 未全 job 绿: {}".format(" | ".join(failures[:6])))
    else:
        add("ci", STATUS_PASS, "{} 个 run / {} 个 job 全部 success（commit {}）".format(len(entries), verified, commit[:12]))


def check_release_assets(repo_root: Path, facts: Dict[str, str], tag: str) -> Optional[List[Dict[str, object]]]:
    slug = facts.get("repo", "")
    data, problem = _gh_json(["release", "view", tag, "-R", slug, "--json", "isDraft,assets,tagName"])
    if data is None:
        add("release", STATUS_WARN, "gh release view 失败: {}".format(problem))
        return None
    if data.get("isDraft"):
        add("release", STATUS_FAIL, "Release 仍是 draft（对外不可见）")
        return None
    assets = data.get("assets", [])
    local = set(recorded_artifacts(facts))
    remote = set(asset.get("name", "") for asset in assets)
    expected = local | {SUMS_ASSET}
    problems: List[str] = []
    if remote != expected:
        extra = sorted(remote - expected)
        missing = sorted(expected - remote)
        problems.append("远端附件集合与登记不符: extra={} missing={}".format(extra, missing))
    if problems:
        add("release", STATUS_FAIL, " | ".join(problems))
        return assets
    add(
        "release",
        STATUS_PASS,
        "{} 非 draft；附件 {} 个 = 载荷 {} + {}（tagName={}）".format(
            tag, len(assets), len(local), SUMS_ASSET, data.get("tagName")
        ),
    )
    return assets


def check_asset_bytes(repo_root: Path, facts: Dict[str, str], tag: str) -> None:
    """逐附件真下载回来重算 sha256，并核对伴随清单恰好登记同一批载荷。"""
    slug = facts.get("repo", "")
    artifacts = recorded_artifacts(facts)
    recorded = recorded_digests(read_release_doc())
    tmp = Path(tempfile.mkdtemp(prefix="s2r-release-assets-"))
    try:
        downloaded: Dict[str, Path] = {}
        for name in artifacts + [SUMS_ASSET]:
            code, out = _gh(["release", "download", tag, "-R", slug, "--pattern", name, "--dir", str(tmp)])
            if code != 0:
                add("assets", STATUS_WARN, "下载 {} 失败: {}".format(name, out.strip()))
                return
            path = tmp / name
            if not path.is_file():
                add("assets", STATUS_FAIL, "下载命令返回成功但文件不在: {}".format(name))
                return
            downloaded[name] = path
        problems: List[str] = []
        for name in artifacts:
            digest = sha256_file(downloaded[name])
            if name not in recorded:
                problems.append("{}: 留档没有登记它的 digest".format(name))
            elif digest != recorded[name]:
                problems.append("{}: 下载件 sha256 {} != 登记 {}".format(name, digest[:12], recorded[name][:12]))
        sums = downloaded[SUMS_ASSET].read_text(encoding="utf-8", errors="replace")
        from_sums = recorded_digests(sums, SUMS_LINE)
        if set(from_sums) != set(artifacts):
            problems.append("伴随清单登记的载荷集合与留档不同: {}".format(sorted(set(from_sums) ^ set(artifacts))))
        for name, digest in from_sums.items():
            if name in recorded and digest != recorded[name]:
                problems.append("伴随清单里 {} 的 digest 与留档不一致".format(name))
            if name in downloaded and sha256_file(downloaded[name]) != digest:
                problems.append("伴随清单里 {} 的 digest 与实际下载件不符".format(name))
        if problems:
            add("assets", STATUS_FAIL, " | ".join(problems))
        else:
            add("assets", STATUS_PASS, "{} 个载荷下载回来重算一致；伴随清单恰好登记同一批载荷".format(len(artifacts)))
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


# --------------------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="check_release", description="发布前一把过 / 发布态自检门")
    parser.add_argument("--repo", metavar="DIR", default=str(REPO_ROOT), help="仓库根（默认本文件所在仓库）")
    parser.add_argument("--online", action="store_true", help="追加走 gh 的远端检查（发布态）")
    parser.add_argument("--artifacts-dir", metavar="DIR", default=None, help="本地附件副本目录（顺带核对哈希）")
    return parser.parse_args(list(argv) if argv is not None else sys.argv[1:])


def main(argv: Optional[Sequence[str]] = None) -> int:
    global REPO_ROOT

    args = parse_args(argv)
    repo_root = Path(args.repo).resolve()
    REPO_ROOT = repo_root
    RESULTS.clear()  # 模块级结果清单：入口必须清空（否则同进程连调两次会越跑越绿）

    _ensure_src_on_path()
    try:
        import s2r
        from s2r import stdio
    except ImportError as exc:
        sys.stderr.write("dependency_missing: 导入本包失败: {}\n".format(exc))
        return 3
    # 会打印的入口一律上编码守卫（K7）；本脚本在 CI/干净环境里也会被跑。
    stdio.ensure_utf8_stdio()

    # `tools/` 与 `scripts/` 都不在 sys.path 上时按路径加载（本脚本自己也可能被按路径加载）。
    path_forms = _load_by_path("tools/path_forms.py", "release_path_forms")
    if path_forms is None:
        sys.stderr.write("check_failed: 找不到 tools/path_forms.py（发布门与脱敏门必须同源）\n")
        return 2

    doc_path = repo_root.joinpath(*RELEASE_DOC.split("/"))
    doc_text = doc_path.read_text(encoding="utf-8") if doc_path.is_file() else ""
    facts, _ = read_facts(doc_text)
    tag = facts.get("tag", "")

    print("repo: {}".format(repo_root))
    print("s2r:  {} ({})".format(s2r.__version__, getattr(s2r, "__file__", "?")))
    print("mode: {}".format("online" if args.online else "offline"))

    check_version_consistency(repo_root)
    check_readme_claims(repo_root)
    check_bench_ledger(repo_root)
    check_no_local_path_forms(repo_root)
    check_git_worktree(repo_root)
    check_release_doc(repo_root, Path(args.artifacts_dir).resolve() if args.artifacts_dir else None, args.online)

    if args.online:
        commit = check_remote(repo_root, facts, tag)
        check_ci(repo_root, facts, commit)
        if tag:
            if check_release_assets(repo_root, facts, tag) is not None:
                check_asset_bytes(repo_root, facts, tag)
        else:
            add("release", STATUS_SKIP, "留档没登记 tag，跳过附件检查")
    else:
        for name in ("remote", "ci", "release", "assets"):
            add(name, STATUS_SKIP, "离线模式：网络类检查未执行（--online 才查）")

    counts = {status: 0 for status in (STATUS_PASS, STATUS_FAIL, STATUS_WARN, STATUS_SKIP)}
    for name, status, detail in RESULTS:
        counts[status] = counts.get(status, 0) + 1
        print("[{:<4}] {:<12} {}".format(status, name, detail))
    print(
        "release check: {} FAIL / {} WARN / {} PASS / {} SKIP".format(
            counts[STATUS_FAIL], counts[STATUS_WARN], counts[STATUS_PASS], counts[STATUS_SKIP]
        )
    )
    return 0 if counts[STATUS_FAIL] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
