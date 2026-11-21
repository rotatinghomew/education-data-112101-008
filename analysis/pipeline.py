"""核心分析管线：从数据集到结构化、可序列化的分析结果。

设计原则：
- 缺答不等于没有改善：不可用值不计入变化，但对应家庭保留在分母并列明去向；
- 未参与后续测量不从分母消失：每个分层都给出 报名数 → 各去向 → 配对数 的核算；
- 不把所有人当作可比：按需求范围 × 参与程度 × 服务分层，并标记量表版本差异；
- 观察关联不等于因果：结果仅描述观察到的变化与不确定性。
"""
from __future__ import annotations

import statistics
from collections import Counter

from .model import Dataset, Observation, Participant

RULES = [
    "可用观测值：response_state=answered 且 value 可解析为数值；其余记录保留在审计清单，不计入变化计算。",
    "配对：同一家庭、同一构念下，取最早与最晚的可用观测；变化 = 随访值 − 基线值。",
    "分母：分层内全部报名家庭；未完成随访者保留在分母并单列去向，不从分母剔除。",
    "量表版本：观测日期须落在量表有效期窗内，否则标记“窗外”；基线与随访使用不同版本标记“跨版本”。",
    "服务归属：报名日期落入服务记录有效期窗，确定该家庭对应的服务记录版本。",
    "行政随访状态：同一家庭有多条随访记录时，按到期日最新的一条计；全部记录条数另列。",
    "不确定度：均值变化 ±1.96×标准误（正态近似）；配对数 <2 记为“样本不足”。",
    "敏感性边界：对“有基线、缺随访”的家庭，以该构念观测到的最小/最大值推算最佳与最差情形均值；边界不是插补。",
    "被取代（supersedes 指向）的观测不参与当前分析，仅保留在审计清单。",
]

DISPOSITION_LABELS = {
    "paired": "已配对",
    "followup_missing": "有基线、随访缺可用值",
    "single_observation": "仅一次可用观测",
    "no_usable_observation": "有观测记录但无可用值",
    "no_observation": "无观测记录",
}

READING_LABELS = {
    "insufficient": "样本不足",
    "positive": "均值变化为正，区间不含零",
    "negative": "均值变化为负，区间不含零",
    "spans_zero": "区间跨零",
}

Z_95 = 1.96


def usable_value(obs: Observation) -> float | None:
    """按口径判定观测是否提供可用数值。缺答/退出均不产生数值，也不视为零。"""
    if obs.response_state != "answered":
        return None
    if obs.raw_value == "":
        return None
    try:
        return float(obs.raw_value)
    except ValueError:
        return None


def _reading(n: int, ci_lo: float | None, ci_hi: float | None) -> str:
    if n < 2 or ci_lo is None or ci_hi is None:
        return "insufficient"
    if ci_lo > 0:
        return "positive"
    if ci_hi < 0:
        return "negative"
    return "spans_zero"


def _round(x: float | None, digits: int = 3) -> float | None:
    return None if x is None else round(x, digits)


def _iso(d) -> str | None:
    return None if d is None else d.isoformat()


def _flag(flags: list[dict], kind: str, ref: str, detail: str) -> None:
    flags.append({"type": kind, "ref": ref, "detail": detail})


def _resolve_service(participant: Participant, services_by_ref, flags) -> None:
    records = services_by_ref.get(participant.service_ref, [])
    if not records:
        _flag(flags, "service_record_missing", participant.ref,
              f"服务 {participant.service_ref} 没有任何服务记录")
        return
    covering = [r for r in records if r.covers(participant.enrolled_at)]
    if not covering:
        _flag(flags, "service_window_unmatched", participant.ref,
              f"报名日期 {_iso(participant.enrolled_at)} 不在服务 "
              f"{participant.service_ref} 任何记录的有效窗内")
    elif len(covering) > 1:
        _flag(flags, "service_window_ambiguous", participant.ref,
              f"报名日期同时落入服务 {participant.service_ref} 的多条记录有效窗")


def _latest_followups(followups) -> dict:
    """每户取到期日最新的一条行政随访记录（无到期日的排最后）。"""
    latest: dict[str, tuple] = {}
    for f in followups:
        key = (f.due_at is not None, f.due_at, f.ref)
        current = latest.get(f.participant_ref)
        if current is None or key > current[0]:
            latest[f.participant_ref] = (key, f)
    return {ref: pair[1] for ref, pair in latest.items()}


def analyze(dataset: Dataset) -> dict:
    measures = {m.ref: m for m in dataset.measures}
    services_by_ref: dict[str, list] = {}
    for record in dataset.services:
        services_by_ref.setdefault(record.service_ref, []).append(record)

    flags: list[dict] = []

    # --- 被取代观测退出当前分析，保留审计 ---
    superseded_refs = {o.supersedes for o in dataset.observations if o.supersedes}
    current_obs = [o for o in dataset.observations if o.ref not in superseded_refs]
    superseded_obs = [o for o in dataset.observations if o.ref in superseded_refs]

    # --- 观测级数据质量检查与可用值 ---
    values: dict[str, float] = {}
    for obs in current_obs:
        has_value = obs.raw_value != ""
        if obs.response_state == "answered" and not has_value:
            _flag(flags, "obs_answered_without_value", obs.ref,
                  "response_state=answered 但 value 为空，按口径不计入变化")
        if obs.response_state != "answered" and has_value:
            _flag(flags, "obs_value_without_answered_state", obs.ref,
                  f"response_state={obs.response_state} 但 value={obs.raw_value}，按口径不计入变化")
        measure = measures.get(obs.measure_ref)
        if measure is None:
            _flag(flags, "obs_unknown_measure", obs.ref,
                  f"量表 {obs.measure_ref} 不在量表定义中")
        elif measure.in_window(obs.observed_at) is False:
            _flag(flags, "obs_out_of_window", obs.ref,
                  f"观测日期 {_iso(obs.observed_at)} 不在量表 {measure.ref}"
                  f"（{measure.instrument_ref} v{measure.version}）有效期"
                  f" {_iso(measure.valid_from)}~{_iso(measure.valid_to)} 内")
        value = usable_value(obs)
        if value is not None:
            values[obs.ref] = value

    # --- 各构念观测到的取值范围（用于敏感性边界） ---
    scale: dict[str, list[float]] = {}
    for obs in current_obs:
        value = values.get(obs.ref)
        measure = measures.get(obs.measure_ref)
        if value is None or measure is None:
            continue
        lo_hi = scale.setdefault(measure.construct, [value, value])
        lo_hi[0] = min(lo_hi[0], value)
        lo_hi[1] = max(lo_hi[1], value)

    # --- 行政随访记录检查 ---
    latest_followup = _latest_followups(dataset.followups)
    followup_rows = Counter(f.participant_ref for f in dataset.followups)
    for f in dataset.followups:
        if f.state == "complete" and f.completed_at is None:
            _flag(flags, "followup_complete_without_date", f.ref,
                  "随访状态为 complete 但 completed_at 为空")
        if f.state != "complete" and f.completed_at is not None:
            _flag(flags, "followup_date_without_complete", f.ref,
                  f"随访状态为 {f.state} 但 completed_at={_iso(f.completed_at)}")

    # --- 服务归属检查 ---
    for p in dataset.participants:
        _resolve_service(p, services_by_ref, flags)

    # --- 按家庭 × 构念整理观测并配对 ---
    obs_by_pc: dict[tuple[str, str], list[Observation]] = {}
    for obs in current_obs:
        measure = measures.get(obs.measure_ref)
        if measure is None:
            continue
        obs_by_pc.setdefault((obs.participant_ref, measure.construct), []).append(obs)

    constructs: list[str] = []
    for m in dataset.measures:
        if m.construct not in constructs:
            constructs.append(m.construct)

    def endpoint_info(obs: Observation) -> dict:
        measure = measures[obs.measure_ref]
        return {
            "observation_ref": obs.ref,
            "measure_ref": measure.ref,
            "instrument_ref": measure.instrument_ref,
            "measure_version": measure.version,
            "observed_at": _iso(obs.observed_at),
            "value": values[obs.ref],
            "in_window": measure.in_window(obs.observed_at),
        }

    def sort_key(obs: Observation):
        return (obs.observed_at is None, obs.observed_at, obs.ref)

    def disposition_for(p: Participant, construct: str) -> tuple[str, dict]:
        """返回 (去向, 详情)。详情按去向携带基线/配对信息。"""
        all_records = obs_by_pc.get((p.ref, construct), [])
        usable = sorted((o for o in all_records if o.ref in values), key=sort_key)
        if not usable:
            return ("no_usable_observation" if all_records else "no_observation"), {}
        if len(usable) == 1:
            single = usable[0]
            latest_any = max(all_records, key=sort_key)
            if latest_any.ref != single.ref:
                # 存在更晚的观测记录但不可用 → 有基线、随访缺可用值
                return "followup_missing", {"baseline": endpoint_info(single)}
            return "single_observation", {"single": endpoint_info(single)}
        baseline, followup = usable[0], usable[-1]
        in_b = measures[baseline.measure_ref].in_window(baseline.observed_at)
        in_f = measures[followup.measure_ref].in_window(followup.observed_at)
        return "paired", {
            "baseline": endpoint_info(baseline),
            "followup": endpoint_info(followup),
            "change": values[followup.ref] - values[baseline.ref],
            "same_version": baseline.measure_ref == followup.measure_ref,
            "out_of_window": (in_b is False) or (in_f is False),
        }

    # --- 分层汇总（服务 × 需求范围 × 参与程度 × 构念） ---
    strata: dict[tuple[str, str, str, str], list[dict]] = {}
    for p in dataset.participants:
        for construct in constructs:
            key = (p.service_ref, p.baseline_band, p.participation_state, construct)
            disposition, detail = disposition_for(p, construct)
            follow = latest_followup.get(p.ref)
            member = {
                "participant_ref": p.ref,
                "referral_type": p.referral_type,
                "disposition": disposition,
                "followup_admin_state": follow.state if follow else "no_record",
                "followup_rows": followup_rows.get(p.ref, 0),
            }
            member.update(detail)
            strata.setdefault(key, []).append(member)

    stratum_results = []
    for key in sorted(strata):
        members = strata[key]
        service_ref, band, state, construct = key
        n_enrolled = len(members)
        disp_counts = Counter(m["disposition"] for m in members)
        paired = [m for m in members if m["disposition"] == "paired"]
        changes = [m["change"] for m in paired]
        n_paired = len(changes)

        mean_change = sd = se = ci_lo = ci_hi = None
        if n_paired >= 1:
            mean_change = statistics.fmean(changes)
        if n_paired >= 2:
            sd = statistics.stdev(changes)
            se = sd / (n_paired ** 0.5)
            ci_lo, ci_hi = mean_change - Z_95 * se, mean_change + Z_95 * se

        version_mix = {
            "same_version": sum(1 for m in paired if m["same_version"]),
            "cross_version": sum(1 for m in paired if not m["same_version"]),
            "out_of_window": sum(1 for m in paired if m["out_of_window"]),
        }

        # 敏感性边界：有基线、缺随访者按量表观测极差推算最佳/最差情形
        lo, hi = scale.get(construct, [None, None])
        bound_bases = [m["baseline"]["value"] for m in members
                       if m["disposition"] == "followup_missing"]
        sensitivity = None
        if lo is not None and (n_paired + len(bound_bases)) > 0:
            total = n_paired + len(bound_bases)
            best = (sum(changes) + sum(hi - b for b in bound_bases)) / total
            worst = (sum(changes) + sum(lo - b for b in bound_bases)) / total
            sensitivity = {
                "n_bounded": len(bound_bases),
                "best_mean_change": _round(best),
                "worst_mean_change": _round(worst),
                "n_unbounded": n_enrolled - total,
            }

        admin = Counter(m["followup_admin_state"] for m in members)
        stratum_results.append({
            "key": "|".join(key),
            "service_ref": service_ref,
            "baseline_band": band,
            "participation_state": state,
            "construct": construct,
            "n_enrolled": n_enrolled,
            "dispositions": {k: disp_counts.get(k, 0) for k in DISPOSITION_LABELS},
            "paired": {
                "n": n_paired,
                "mean_change": _round(mean_change),
                "sd": _round(sd),
                "ci_lo": _round(ci_lo),
                "ci_hi": _round(ci_hi),
                "reading": _reading(n_paired, ci_lo, ci_hi),
                "version_mix": version_mix,
            },
            "sensitivity": sensitivity,
            "followup_admin": {s: admin.get(s, 0)
                               for s in ("complete", "overdue", "withdrawn", "no_record")},
            "members": members,
        })

    # --- 样本构成 ---
    def count_by(attr):
        return dict(sorted(Counter(getattr(p, attr) for p in dataset.participants).items()))

    composition = {
        "by_service": count_by("service_ref"),
        "by_referral": count_by("referral_type"),
        "by_baseline_band": count_by("baseline_band"),
        "by_participation_state": count_by("participation_state"),
    }

    flag_counts = dict(sorted(Counter(f["type"] for f in flags).items()))

    return {
        "rules": RULES,
        "constructs": constructs,
        "scale_range": {c: [_round(v[0]), _round(v[1])] for c, v in scale.items()},
        "n_participants": len(dataset.participants),
        "n_observations_current": len(current_obs),
        "n_observations_superseded": len(superseded_obs),
        "superseded_refs": sorted(o.ref for o in superseded_obs),
        "composition": composition,
        "strata": stratum_results,
        "flags": flags,
        "flag_counts": flag_counts,
        "measures": [{
            "measure_ref": m.ref,
            "instrument_ref": m.instrument_ref,
            "version": m.version,
            "construct": m.construct,
            "valid_from": _iso(m.valid_from),
            "valid_to": _iso(m.valid_to),
        } for m in dataset.measures],
        "services": [{
            "service_ref": r.service_ref,
            "service_type": r.service_type,
            "valid_from": _iso(r.valid_from),
            "valid_to": _iso(r.valid_to),
            "description": r.description,
        } for r in dataset.services],
    }
