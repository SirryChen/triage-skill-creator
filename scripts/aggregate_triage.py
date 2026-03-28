#!/usr/bin/env python3
"""聚合导诊评测结果，输出汇总统计。

Usage:
    python scripts/aggregate_triage.py eval_result/iteration-1
    python scripts/aggregate_triage.py eval_result/iteration-1 --output benchmark.json
"""

import argparse
import json
import sys
from pathlib import Path


def aggregate(results_dir: Path) -> dict:
    """从评测结果目录聚合统计数据。"""
    results_file = results_dir / "all_results.json"

    if results_file.exists():
        with open(results_file) as f:
            results = json.load(f)
    else:
        results = []
        for case_dir in sorted(results_dir.glob("eval-*")):
            grading_file = case_dir / "grading.json"
            if grading_file.exists():
                with open(grading_file) as f:
                    results.append(json.load(f))

    if not results:
        print(f"No results found in {results_dir}", file=sys.stderr)
        return {}

    n = len(results)
    correct = sum(1 for r in results if r.get("correct", False))
    info_scores = [r["info_score"] for r in results if r.get("info_score")]
    overall_scores = [r["overall_score"] for r in results if r.get("overall_score")]
    turn_counts = [r["turn_count"] for r in results if r.get("turn_count")]
    nurse_lengths = [r["avg_nurse_length"] for r in results if r.get("avg_nurse_length")]

    def mean(lst):
        return round(sum(lst) / len(lst), 2) if lst else 0

    summary = {
        "num_cases": n,
        "accuracy": round(correct / n, 4) if n > 0 else 0,
        "avg_info_score": mean(info_scores),
        "avg_overall_score": mean(overall_scores),
        "avg_turns": mean(turn_counts),
        "avg_turn_length": mean(nurse_lengths),
        "per_case": results,
    }

    return summary


def print_summary(summary: dict):
    """打印可读的汇总报告。"""
    if not summary:
        return

    print("=" * 50)
    print("  Triage Evaluation Summary")
    print("=" * 50)
    print(f"  Cases evaluated:    {summary['num_cases']}")
    print(f"  Department accuracy: {summary['accuracy']:.1%}")
    print(f"  Avg info score:     {summary['avg_info_score']:.2f} / 5")
    print(f"  Avg overall score:  {summary['avg_overall_score']:.2f} / 5")
    print(f"  Avg turn count:     {summary['avg_turns']:.1f}")
    print(f"  Avg nurse turn len: {summary['avg_turn_length']:.1f} chars")
    print("=" * 50)

    print("\nPer-case breakdown:")
    for r in summary.get("per_case", []):
        status = "OK" if r.get("correct") else "XX"
        print(f"  [{status}] id={r.get('case_id', '?'):>8} "
              f"pred={r.get('department_pred', '?'):　<12} "
              f"real={r.get('department_real', '?'):　<12} "
              f"info={r.get('info_score', '?')} overall={r.get('overall_score', '?')} "
              f"turns={r.get('turn_count', '?')}")


def main():
    parser = argparse.ArgumentParser(description="Aggregate triage evaluation results")
    parser.add_argument("results_dir", type=Path, help="Path to evaluation results directory")
    parser.add_argument("--output", "-o", type=Path, default=None, help="Output JSON path")
    args = parser.parse_args()

    if not args.results_dir.exists():
        print(f"Error: {args.results_dir} not found", file=sys.stderr)
        sys.exit(1)

    summary = aggregate(args.results_dir)

    if not summary:
        sys.exit(1)

    print_summary(summary)

    output_path = args.output or (args.results_dir / "benchmark.json")
    with open(output_path, "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\nBenchmark saved to {output_path}")


if __name__ == "__main__":
    main()
