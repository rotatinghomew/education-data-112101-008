"""运行清单、结果存取与两次运行的差异解释。

每次运行保存：输入文件哈希、代码版本哈希、分析口径、完整结构化结果与报告。
补正数据后重跑，可用 diff 解释哪些既有结论因此改变。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .pipeline import DISPOSITION_LABELS, READING_LABELS

RUNS_DIRNAME = "runs"


def code_hash() -> str:
    """分析包源码的哈希，用于标记产生结果的代码版本。"""
    pkg = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(pkg.glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def build_manifest(dataset, label: str = "") -> dict:
    created = datetime.now(timezone.utc).astimezone()
    short = hashlib.sha256(
        "".join(dataset.file_hashes.values()).encode("utf-8")
    ).hexdigest()[:6]
    run_id = f"{created.strftime('%Y%m%dT%H%M%S')}_{short}"
    return {
        "run_id": run_id,
        "created_at": created.isoformat(timespec="seconds"),
        "label": label,
        "data_dir": dataset.data_dir,
        "input_hashes": dataset.file_hashes,
        "code_hash": code_hash(),
        "package_version": __version__,
    }


def save_run(out_dir: str | Path, manifest: dict, result: dict, report: str) -> Path:
    run_dir = Path(out_dir) / RUNS_DIRNAME / manifest["run_id"]
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    latest = Path(out_dir) / RUNS_DIRNAME / "LATEST"
    latest.write_text(manifest["run_id"], encoding="utf-8")
    return run_dir


def list_runs(out_dir: str | Path) -> list[dict]:
    runs_root = Path(out_dir) / RUNS_DIRNAME
    if not runs_root.is_dir():
        return []
    manifests = []
    for run_dir in sorted(runs_root.iterdir()):
        manifest_path = run_dir / "manifest.json"
        if manifest_path.is_file():
            manifests.append(json.loads(manifest_path.read_text(encoding="utf-8")))
    return manifests


def load_run(out_dir: str | Path, run_id: str) -> tuple[dict, dict, str]:
    if run_id == "latest":
        run_id = (Path(out_dir) / RUNS_DIRNAME / "LATEST").read_text(encoding="utf-8").strip()
    run_dir = Path(out_dir) / RUNS_DIRNAME / run_id
    if not run_dir.is_dir():
        raise FileNotFoundError(f"找不到运行 {run_id}（目录 {run_dir}）")
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    return manifest, result, report


def _strata_by_key(result: dict) -> dict:
    return {s["key"]: s for s in result["strata"]}


def diff_results(before: dict, after: dict) -> dict:
    """比较两次运行的结构化结果，返回机器可读的差异。"""
    changes = {"readings": [], "estimates": [], "added": [], "removed": [],
               "flags": [], "scale": [], "composition": []}
    a, b = _strata_by_key(before), _strata_by_key(after)
    for key in sorted(set(a) | set(b)):
        if key not in a:
            changes["added"].append(key)
            continue
        if key not in b:
            changes["removed"].append(key)
            continue
        pa, pb = a[key]["paired"], b[key]["paired"]
        if pa["reading"] != pb["reading"]:
            changes["readings"].append({
                "key": key,
                "before": pa["reading"], "after": pb["reading"],
                "before_n": pa["n"], "after_n": pb["n"],
            })
        estimate_changed = (
            pa["n"] != pb["n"]
            or pa["mean_change"] != pb["mean_change"]
            or pa["ci_lo"] != pb["ci_lo"] or pa["ci_hi"] != pb["ci_hi"]
            or a[key]["dispositions"] != b[key]["dispositions"]
            or pa["version_mix"] != pb["version_mix"]
        )
        if estimate_changed:
            changes["estimates"].append({
                "key": key,
                "before": {"n_enrolled": a[key]["n_enrolled"], "paired": pa,
                           "dispositions": a[key]["dispositions"]},
                "after": {"n_enrolled": b[key]["n_enrolled"], "paired": pb,
                          "dispositions": b[key]["dispositions"]},
            })
    fa, fb = before["flag_counts"], after["flag_counts"]
    for kind in sorted(set(fa) | set(fb)):
        if fa.get(kind, 0) != fb.get(kind, 0):
            changes["flags"].append({"type": kind, "before": fa.get(kind, 0),
                                     "after": fb.get(kind, 0)})
    if before.get("scale_range") != after.get("scale_range"):
        changes["scale"].append({"before": before.get("scale_range"),
                                 "after": after.get("scale_range")})
    if before.get("composition") != after.get("composition"):
        changes["composition"].append({"before": before.get("composition"),
                                       "after": after.get("composition")})
    return changes


def render_diff(before_manifest: dict, after_manifest: dict, changes: dict) -> str:
    lines = [
        "# 两次运行的差异解释",
        "",
        f"- 旧运行：`{before_manifest['run_id']}`（{before_manifest['created_at']}）",
        f"- 新运行：`{after_manifest['run_id']}`（{after_manifest['created_at']}）",
        "",
        "## 输入文件变化",
        "",
    ]
    ha, hb = before_manifest["input_hashes"], after_manifest["input_hashes"]
    changed_files = [name for name in sorted(set(ha) | set(hb)) if ha.get(name) != hb.get(name)]
    if changed_files:
        for name in changed_files:
            lines.append(f"- `{name}.csv` 内容已变化")
    else:
        lines.append("- 输入文件内容一致（差异可能来自代码版本）")
    if before_manifest["code_hash"] != after_manifest["code_hash"]:
        lines.append("- 分析代码版本不同，口径可能已调整")
    lines.append("")

    lines.append("## 既有结论的改变")
    lines.append("")
    if changes["readings"]:
        for r in changes["readings"]:
            lines.append(
                f"- **{r['key']}**：解读由「{READING_LABELS[r['before']]}」变为"
                f"「{READING_LABELS[r['after']]}」（配对 {r['before_n']} → {r['after_n']} 户）")
    else:
        lines.append("- 没有任何分层的解读类别发生改变。")
    lines.append("")

    if changes["added"] or changes["removed"]:
        lines.append("## 分层的新增与消失")
        lines.append("")
        for key in changes["added"]:
            lines.append(f"- 新增分层：{key}")
        for key in changes["removed"]:
            lines.append(f"- 消失分层：{key}")
        lines.append("")

    if changes["estimates"]:
        lines.append("## 估计与分母的变化（解读类别未变者也在此列出）")
        lines.append("")
        for e in changes["estimates"]:
            pa, pb = e["before"]["paired"], e["after"]["paired"]
            disp_a = e["before"]["dispositions"]
            disp_b = e["after"]["dispositions"]
            lines.append(f"- **{e['key']}**：报名 {e['before']['n_enrolled']} → "
                         f"{e['after']['n_enrolled']} 户；配对 {pa['n']} → {pb['n']} 户；"
                         f"均值变化 {pa['mean_change']} → {pb['mean_change']}；"
                         f"版本构成 {pa['version_mix']} → {pb['version_mix']}")
            if disp_a != disp_b:
                def fmt_disp(d):
                    return "、".join(f"{DISPOSITION_LABELS[k]} {d[k]}" for k in d if d[k])
                lines.append(f"  - 去向：{fmt_disp(disp_a)} → {fmt_disp(disp_b)}")
        lines.append("")

    if changes["flags"]:
        lines.append("## 数据质量标记的变化")
        lines.append("")
        for f in changes["flags"]:
            lines.append(f"- {f['type']}：{f['before']} → {f['after']} 条")
        lines.append("")

    if changes["scale"]:
        lines.append("## 量表观测取值范围的变化")
        lines.append("")
        for s in changes["scale"]:
            lines.append(f"- 旧：{s['before']}；新：{s['after']}")
        lines.append("")

    if changes["composition"]:
        lines.append("## 样本构成的变化")
        lines.append("")
        for c in changes["composition"]:
            lines.append(f"- 旧：{c['before']}")
            lines.append(f"- 新：{c['after']}")
        lines.append("")
    return "\n".join(lines)


def render_trace(manifest: dict, result: dict, key: str) -> str:
    """追查一个分层比较所采用的量表定义、服务记录与纳入情况。"""
    strata = _strata_by_key(result)
    stratum = strata.get(key)
    if stratum is None:
        available = "\n".join(f"- {k}" for k in sorted(strata))
        return f"找不到比较 `{key}`。本次运行包含的比较：\n{available}"
    lines = [
        f"# 比较来源追查：{key}",
        "",
        f"- 运行：`{manifest['run_id']}`（{manifest['created_at']}）",
        f"- 代码版本哈希：`{manifest['code_hash'][:12]}`",
        "",
        "## 纳入口径",
        "",
    ]
    lines += [f"- {rule}" for rule in result["rules"]]
    service_ref = stratum["service_ref"]
    measure_refs = set()
    for m in stratum["members"]:
        for endpoint in ("baseline", "followup", "single"):
            info = m.get(endpoint)
            if info:
                measure_refs.add(info["measure_ref"])
    lines += [
        "",
        "## 该比较涉及的量表定义",
        "",
        "| 量表 | 工具 | 版本 | 构念 | 有效期自 | 有效期至 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for m in result["measures"]:
        if m["measure_ref"] in measure_refs:
            lines.append(f"| {m['measure_ref']} | {m['instrument_ref']} | {m['version']} "
                         f"| {m['construct']} | {m['valid_from'] or '—'} | {m['valid_to'] or '（开放）'} |")
    lines += [
        "",
        "## 该比较涉及的服务记录",
        "",
        "| 服务 | 类型 | 有效期自 | 有效期至 | 说明 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in result["services"]:
        if r["service_ref"] == service_ref:
            lines.append(f"| {r['service_ref']} | {r['service_type']} | {r['valid_from'] or '—'} "
                         f"| {r['valid_to'] or '（开放）'} | {r['description']} |")
    lines += [
        "",
        "## 纳入情况（逐户）",
        "",
        "| 家庭 | 转介途径 | 去向 | 基线观测 | 随访观测 | 变化 | 行政随访状态 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for m in stratum["members"]:
        def fmt_ep(info):
            if not info:
                return "—"
            window = {"True": "窗内", "False": "窗外", "None": "未知"}[str(info["in_window"])]
            return (f"{info['observation_ref']}（{info['measure_ref']} "
                    f"v{info['measure_version']}，{window}，值 {info['value']:g}）")
        change = m.get("change")
        lines.append("| " + " | ".join([
            m["participant_ref"], m["referral_type"],
            DISPOSITION_LABELS[m["disposition"]],
            fmt_ep(m.get("baseline") or m.get("single")),
            fmt_ep(m.get("followup")),
            "—" if change is None else f"{change:+.2f}",
            m["followup_admin_state"],
        ]) + " |")
    lines += [
        "",
        "## 输入文件哈希",
        "",
    ]
    lines += [f"- `{name}.csv`：`{digest}`" for name, digest in manifest["input_hashes"].items()]
    lines.append("")
    return "\n".join(lines)
