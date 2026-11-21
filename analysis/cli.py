"""命令行入口。

用法：
    python3 -m analysis run [--data DIR] [--out DIR] [--label TEXT]
    python3 -m analysis list [--out DIR]
    python3 -m analysis show RUN_ID [--out DIR]
    python3 -m analysis diff RUN_A RUN_B [--out DIR]
    python3 -m analysis trace RUN_ID [KEY] [--out DIR]
"""
from __future__ import annotations

import argparse
import sys

from .model import load_dataset
from .pipeline import analyze
from .provenance import (build_manifest, diff_results, list_runs, load_run,
                         render_diff, render_trace, save_run)
from .report import render_report


def _cmd_run(args) -> int:
    dataset = load_dataset(args.data)
    result = analyze(dataset)
    manifest = build_manifest(dataset, label=args.label)
    report = render_report(result, manifest)
    run_dir = save_run(args.out, manifest, result, report)
    print(f"运行完成：{manifest['run_id']}")
    print(f"报告：{run_dir / 'report.md'}")
    n_flag = sum(result["flag_counts"].values())
    print(f"报名 {result['n_participants']} 户；数据质量标记 {n_flag} 条；"
          f"分层比较 {len(result['strata'])} 个。")
    print("提示：数据补正或追加随访后再次运行本命令，"
          "并用 `python3 -m analysis diff <旧运行> <新运行>` 查看结论变化。")
    return 0


def _cmd_list(args) -> int:
    manifests = list_runs(args.out)
    if not manifests:
        print("尚无运行记录。")
        return 0
    for m in manifests:
        label = f"（{m['label']}）" if m.get("label") else ""
        print(f"{m['run_id']}  {m['created_at']}  {m['data_dir']} {label}")
    return 0


def _cmd_show(args) -> int:
    _, _, report = load_run(args.out, args.run_id)
    print(report)
    return 0


def _cmd_diff(args) -> int:
    manifest_a, result_a, _ = load_run(args.out, args.run_a)
    manifest_b, result_b, _ = load_run(args.out, args.run_b)
    changes = diff_results(result_a, result_b)
    print(render_diff(manifest_a, manifest_b, changes))
    return 0


def _cmd_trace(args) -> int:
    manifest, result, _ = load_run(args.out, args.run_id)
    if args.key is None:
        print("本次运行包含的比较（以 KEY 标识，可用 trace RUN KEY 追查）：")
        for s in result["strata"]:
            print(f"- {s['key']}")
        return 0
    print(render_trace(manifest, result, args.key))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m analysis",
        description="家庭教育服务结果比较分析（合成数据，观察性关联，非因果结论）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="读取数据目录并运行分析，保存报告与清单")
    p_run.add_argument("--data", default="data", help="数据目录（默认 data/）")
    p_run.add_argument("--out", default="output", help="运行结果保存目录（默认 output/）")
    p_run.add_argument("--label", default="", help="本次运行的备注标签")
    p_run.set_defaults(func=_cmd_run)

    p_list = sub.add_parser("list", help="列出历史运行")
    p_list.add_argument("--out", default="output")
    p_list.set_defaults(func=_cmd_list)

    p_show = sub.add_parser("show", help="打印某次运行的报告（RUN_ID 可用 latest）")
    p_show.add_argument("run_id")
    p_show.add_argument("--out", default="output")
    p_show.set_defaults(func=_cmd_show)

    p_diff = sub.add_parser("diff", help="比较两次运行，解释哪些结论改变")
    p_diff.add_argument("run_a", help="旧运行 ID（可用 latest）")
    p_diff.add_argument("run_b", help="新运行 ID（可用 latest）")
    p_diff.add_argument("--out", default="output")
    p_diff.set_defaults(func=_cmd_diff)

    p_trace = sub.add_parser("trace", help="追查一个比较采用的量表定义、服务记录与纳入情况")
    p_trace.add_argument("run_id", help="运行 ID（可用 latest）")
    p_trace.add_argument("key", nargs="?", default=None,
                         help="比较 KEY，形如 S-1|high_need|completed|family_confidence；缺省时列出全部")
    p_trace.add_argument("--out", default="output")
    p_trace.set_defaults(func=_cmd_trace)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
