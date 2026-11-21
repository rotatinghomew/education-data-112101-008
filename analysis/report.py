"""将分析结果渲染为 Markdown 报告（中文）。"""
from __future__ import annotations

from .pipeline import DISPOSITION_LABELS, READING_LABELS

BAND_LABELS = {
    "high_need": "高需求",
    "medium_need": "中需求",
    "low_need": "低需求",
}
STATE_LABELS = {
    "completed": "完成",
    "partial": "部分参与",
    "withdrawn": "退出",
}
REFERRAL_LABELS = {
    "self": "主动报名",
    "referral": "转介",
    "school": "学校转介",
}
ADMIN_LABELS = {
    "complete": "完成",
    "overdue": "逾期未访",
    "withdrawn": "退出随访",
    "no_record": "无随访记录",
}
FLAG_LABELS = {
    "obs_answered_without_value": "已答但值为空",
    "obs_value_without_answered_state": "有值但状态非已答",
    "obs_unknown_measure": "量表未定义",
    "obs_out_of_window": "量表窗外观测",
    "followup_complete_without_date": "随访完成但无完成时间",
    "followup_date_without_complete": "有完成时间但状态非完成",
    "service_record_missing": "服务无记录",
    "service_window_unmatched": "报名日期不在服务有效窗内",
    "service_window_ambiguous": "服务有效窗重叠",
}


def _lab(mapping: dict, key: str) -> str:
    return mapping.get(key, key or "（空）")


def _fmt(x, suffix: str = "") -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:+.2f}{suffix}"
    return f"{x}{suffix}"


def _count_table(title: str, counts: dict, mapping: dict) -> list[str]:
    lines = [f"**{title}**", "", "| 类别 | 户数 |", "| --- | ---: |"]
    for key, n in counts.items():
        lines.append(f"| {_lab(mapping, key)} | {n} |")
    lines.append("")
    return lines


def render_report(result: dict, manifest: dict) -> str:
    out: list[str] = []
    out.append("# 家庭教育服务结果比较报告")
    out.append("")
    out.append(f"- 运行编号：`{manifest['run_id']}`，生成时间：{manifest['created_at']}")
    out.append(f"- 数据目录：`{manifest['data_dir']}`；代码版本哈希：`{manifest['code_hash'][:12]}`")
    out.append(f"- 报名家庭 {result['n_participants']} 户；当前观测 "
               f"{result['n_observations_current']} 条"
               + (f"，另有 {result['n_observations_superseded']} 条被取代观测已退出分析"
                  if result["n_observations_superseded"] else ""))
    out.append("")
    out.append("> 本报告基于合成数据。参加者并非随机进入各类服务，以下均为**观察性关联**，"
               "不能据此断定服务因果效果；区间不含零也不构成因果证据。")
    out.append("")

    # 1. 口径
    out.append("## 1. 分析口径")
    out.append("")
    for rule in result["rules"]:
        out.append(f"- {rule}")
    out.append("")

    # 2. 样本构成
    out.append("## 2. 样本构成（报名时的分层依据）")
    out.append("")
    comp = result["composition"]
    out += _count_table("按服务", comp["by_service"], {})
    out += _count_table("按转介途径", comp["by_referral"], REFERRAL_LABELS)
    out += _count_table("按需求范围", comp["by_baseline_band"], BAND_LABELS)
    out += _count_table("按参与程度", comp["by_participation_state"], STATE_LABELS)
    out.append("各分层户数不均、且转介途径与需求范围不同，说明层间均值不可直接互比；"
               "本报告仅在层内描述变化。")
    out.append("")

    # 3. 分母核算与失访
    out.append("## 3. 随访完成与分母核算")
    out.append("")
    out.append("未完成随访的家庭保留在分母中，下表给出每户去向；"
               "“行政状态”来自随访记录，“分析去向”来自观测值可用性，两者不一致时以第 6 节清单为准。")
    out.append("")
    header = ("| 服务 | 需求范围 | 参与程度 | 构念 | 报名 | "
              + " | ".join(DISPOSITION_LABELS.values())
              + " | 行政:完成 | 行政:逾期 | 行政:退出 | 行政:无记录 |")
    out.append(header)
    out.append("|" + " --- |" * (5 + len(DISPOSITION_LABELS) + 4))
    for s in result["strata"]:
        d = s["dispositions"]
        a = s["followup_admin"]
        row = [s["service_ref"], _lab(BAND_LABELS, s["baseline_band"]),
               _lab(STATE_LABELS, s["participation_state"]), s["construct"],
               str(s["n_enrolled"])]
        row += [str(d[k]) for k in DISPOSITION_LABELS]
        row += [str(a["complete"]), str(a["overdue"]), str(a["withdrawn"]), str(a["no_record"])]
        out.append("| " + " | ".join(row) + " |")
    out.append("")

    # 4. 分层观察变化
    out.append("## 4. 分层观察变化")
    out.append("")
    for construct in result["constructs"]:
        strata = [s for s in result["strata"] if s["construct"] == construct]
        if not strata:
            continue
        out.append(f"### 构念：{construct}")
        out.append("")
        has_any_obs = any(
            s["dispositions"]["no_observation"] < s["n_enrolled"] for s in strata)
        if not has_any_obs:
            out.append("该构念在当前数据中没有任何观测记录，无法计算变化；"
                       "其量表定义见第 5 节。分母中这些家庭仍计入第 3 节核算。")
            out.append("")
            continue
        lo_hi = result["scale_range"].get(construct)
        if lo_hi:
            out.append(f"该构念在当前数据中观测到的取值范围为 {lo_hi[0]:g}～{lo_hi[1]:g}，"
                       "敏感性边界据此推算。")
            out.append("")
        out.append("| 服务 | 需求范围 | 参与程度 | 报名 | 配对 | 均值变化 | 95% 区间 | "
                   "敏感性边界(最差~最佳) | 版本构成 | 解读 |")
        out.append("| --- | --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |")
        for s in strata:
            p = s["paired"]
            vm = p["version_mix"]
            version_txt = f"同版本 {vm['same_version']} / 跨版本 {vm['cross_version']} / 含窗外 {vm['out_of_window']}"
            ci = "—" if p["ci_lo"] is None else f"{p['ci_lo']:+.2f} ~ {p['ci_hi']:+.2f}"
            sens = s["sensitivity"]
            if sens and sens["n_bounded"]:
                sens_txt = f"{sens['worst_mean_change']:+.2f} ~ {sens['best_mean_change']:+.2f}"
                if sens["n_unbounded"]:
                    sens_txt += f"（另 {sens['n_unbounded']} 户无基线无法界定）"
            else:
                sens_txt = "—"
            out.append("| " + " | ".join([
                s["service_ref"], _lab(BAND_LABELS, s["baseline_band"]),
                _lab(STATE_LABELS, s["participation_state"]),
                str(s["n_enrolled"]), str(p["n"]), _fmt(p["mean_change"]),
                ci, sens_txt, version_txt, READING_LABELS[p["reading"]],
            ]) + " |")
        out.append("")
        out.append("变化 = 随访值 − 基线值；正值表示随访时得分更高。"
                   "“跨版本”表示基线与随访使用了不同版本量表，其变化可能混入版本差异；"
                   "“含窗外”表示至少一端观测落在量表有效期窗外。")
        out.append("")

    # 5. 量表与服务版本
    out.append("## 5. 量表定义与服务记录（本次运行所依据的版本）")
    out.append("")
    out.append("**量表定义**")
    out.append("")
    out.append("| 量表 | 工具 | 版本 | 构念 | 有效期自 | 有效期至 |")
    out.append("| --- | --- | --- | --- | --- | --- |")
    for m in result["measures"]:
        out.append(f"| {m['measure_ref']} | {m['instrument_ref']} | {m['version']} "
                   f"| {m['construct']} | {m['valid_from'] or '—'} | {m['valid_to'] or '（开放）'} |")
    out.append("")
    out.append("**服务记录**")
    out.append("")
    out.append("| 服务 | 类型 | 有效期自 | 有效期至 | 说明 |")
    out.append("| --- | --- | --- | --- | --- |")
    for r in result["services"]:
        out.append(f"| {r['service_ref']} | {r['service_type']} | {r['valid_from'] or '—'} "
                   f"| {r['valid_to'] or '（开放）'} | {r['description']} |")
    out.append("")
    out.append("量表定义或服务记录补正后，重新运行 `python3 -m analysis run` 并用 "
               "`python3 -m analysis diff <旧运行> <新运行>` 查看哪些结论因此改变。")
    out.append("")

    # 6. 数据质量
    out.append("## 6. 数据质量与不一致清单")
    out.append("")
    if not result["flags"]:
        out.append("本次运行未发现不一致记录。")
    else:
        counts = result["flag_counts"]
        out.append("| 类型 | 条数 |")
        out.append("| --- | ---: |")
        for kind, n in counts.items():
            out.append(f"| {_lab(FLAG_LABELS, kind)} | {n} |")
        out.append("")
        out.append("<details><summary>逐条明细</summary>")
        out.append("")
        for f in result["flags"]:
            out.append(f"- `{f['ref']}`（{_lab(FLAG_LABELS, f['type'])}）：{f['detail']}")
        out.append("")
        out.append("</details>")
    out.append("")
    if result["superseded_refs"]:
        out.append("被取代而未参与分析的观测：" + "、".join(f"`{r}`" for r in result["superseded_refs"]))
        out.append("")

    # 7. 结论与适用边界
    out.append("## 7. 结论与适用边界")
    out.append("")
    for s in result["strata"]:
        p = s["paired"]
        if p["n"] == 0:
            continue
        label = (f"{s['service_ref']} × {_lab(BAND_LABELS, s['baseline_band'])} × "
                 f"{_lab(STATE_LABELS, s['participation_state'])}（{s['construct']}）")
        missed = s["n_enrolled"] - p["n"]
        out.append(f"- **{label}**：报名 {s['n_enrolled']} 户中 {p['n']} 户有配对观测，"
                   f"观察到均值变化 {_fmt(p['mean_change'])}"
                   + (f"，95% 区间 {p['ci_lo']:+.2f} ~ {p['ci_hi']:+.2f}"
                      if p["ci_lo"] is not None else "")
                   + f"，{READING_LABELS[p['reading']]}。"
                   + (f"其余 {missed} 户未完成可比测量，结论仅适用于已配对的 "
                      f"{p['n']}/{s['n_enrolled']} 户。" if missed else "该层全部户均已配对。"))
    out.append("")
    out.append("**解释边界**：以上为观察性关联。参与者构成（转介途径、需求范围）、"
               "失访、量表版本更替都可能造成层间或层内差异；本报告不提供因果结论。"
               "保留、调整或进一步评估某项服务时，建议结合分母核算（第 3 节）与"
               "敏感性边界（第 4 节）判断结论的适用范围。")
    out.append("")
    return "\n".join(out)
