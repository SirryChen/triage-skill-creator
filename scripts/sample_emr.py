#!/usr/bin/env python3
"""从问诊病例数据集中随机采样指定数量的单条记录（供第二阶段仿真）。

默认数据源：`data/triage_unified.json`（统一问诊数据，体积大，使用流式解析 + 两遍扫描，避免整文件载入内存）。

旧版「扁平 JSON 数组」小文件仍支持：可直接 json.load。

Usage:
    pip install -r requirements.txt   # 需要 ijson（大文件）
    python scripts/sample_emr.py --data-path data/triage_unified.json --n 5
    python scripts/sample_emr.py --data-path data/triage_unified.json --n 5 --output eval_cases.json --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

try:
    import ijson
except ImportError:
    ijson = None


def normalize_unified_record(u: dict[str, Any]) -> dict[str, Any]:
    """将 triage_unified 单条转为仿真/评分用的扁平病例结构。"""
    ec = u.get("emr_compat") or {}
    dept = (ec.get("department") or u.get("department_standard") or "").strip() or "unknown"
    oid = ec.get("outpatient_number")
    if oid is None or oid == "":
        sid = u.get("source_id", "")
        try:
            oid = int(float(str(sid))) if sid else u.get("row_index", 0)
        except (TypeError, ValueError):
            oid = u.get("row_index", 0)
    return {
        "outpatient_number": oid,
        "chief_complaint": (ec.get("chief_complaint") or u.get("title") or "").strip(),
        "preliminary_diagnosis": (ec.get("preliminary_diagnosis") or "").strip(),
        "present_illness_history": (
            ec.get("present_illness_history") or u.get("user_description") or ""
        ).strip(),
        "past_history": ec.get("past_history") or "不详",
        "drug_allergy_history": ec.get("drug_allergy_history") or "不详",
        "department": dept,
        "age": (ec.get("age") or u.get("age") or "").strip(),
        "gender": (ec.get("gender") or u.get("gender") or "").strip(),
        "name": ec.get("name") or "患者",
        "visit_date": (u.get("visit_date") or ec.get("visit_date") or "").strip(),
        "patient_id": str(u.get("source_id", "")),
        "_provenance": "triage_unified",
        "_row_index": u.get("row_index"),
        "_label_1_word": u.get("label_1_word"),
        "_label_3_word": u.get("label_3_word"),
    }


def department_key(rec: dict[str, Any]) -> str:
    return (rec.get("department") or "unknown").strip() or "unknown"


def sample_emr_legacy(
    data: list[dict],
    n: int,
    seed: int | None = None,
    allowed_departments: frozenset[str] | None = None,
) -> list[dict]:
    """旧版：扁平 JSON 列表，分层采样。"""
    if seed is not None:
        random.seed(seed)

    if allowed_departments:
        data = [r for r in data if _allowed(department_key(r), allowed_departments)]

    if n >= len(data):
        return [dict(r) for r in data]

    dept_groups: dict[str, list[dict]] = {}
    for record in data:
        dept = department_key(record)
        dept_groups.setdefault(dept, []).append(record)

    sampled = []
    depts = list(dept_groups.keys())
    random.shuffle(depts)

    for dept in depts:
        if len(sampled) >= n:
            break
        record = random.choice(dept_groups[dept])
        sampled.append(dict(record))

    if len(sampled) < n:
        sampled_ids = {r.get("outpatient_number") for r in sampled}
        remaining = [r for r in data if r.get("outpatient_number") not in sampled_ids]
        extra = random.sample(remaining, min(n - len(sampled), len(remaining)))
        sampled.extend(dict(x) for x in extra)

    return sampled[:n]


def _reservoir_update(reservoir: list, item: dict, k: int, seen: int) -> None:
    """seen 为当前已见**合格**元素个数（含本条），reservoir 长度至多为 k。"""
    if k <= 0:
        return
    if len(reservoir) < k:
        reservoir.append(item)
        return
    j = random.randint(1, seen)
    if j <= k:
        reservoir[j - 1] = item


def _allowed(dept: str, allowed: frozenset[str] | None) -> bool:
    if not allowed:
        return True
    return dept in allowed


def sample_emr_unified_stream(
    path: Path,
    n: int,
    seed: int | None = None,
    allowed_departments: frozenset[str] | None = None,
) -> list[dict]:
    """triage_unified.json：流式两遍，按科室先各取 1 条再补足。allowed_departments 非空时只采这些标准科室。"""
    if ijson is None:
        print(
            "Error: 读取大型 triage_unified.json 需要安装 ijson：pip install ijson",
            file=sys.stderr,
        )
        sys.exit(1)

    if seed is not None:
        random.seed(seed)

    # Pass 1：各科一条 reservoir（均匀随机）
    dept_one: dict[str, dict] = {}
    dept_seen_index: dict[str, int] = {}

    with path.open("rb") as f:
        for obj in ijson.items(f, "item"):
            if not isinstance(obj, dict):
                continue
            rec = normalize_unified_record(obj)
            d = department_key(rec)
            if not _allowed(d, allowed_departments):
                continue
            dept_seen_index[d] = dept_seen_index.get(d, 0) + 1
            c = dept_seen_index[d]
            if c == 1:
                dept_one[d] = rec
            elif random.randint(1, c) == 1:
                dept_one[d] = rec

    depts = [d for d in dept_one if _allowed(d, allowed_departments)]
    random.shuffle(depts)

    sampled: list[dict] = []
    for d in depts:
        if len(sampled) >= n:
            break
        sampled.append(dict(dept_one[d]))

    sampled_ids = {r.get("outpatient_number") for r in sampled}

    if len(sampled) >= n:
        return [strip_provenance(r) for r in sampled[:n]]

    need = n - len(sampled)
    # Pass 2：从全量流中 reservoir 补足（排除已选 outpatient_number）
    pool: list[dict] = []
    seen_eligible = 0
    with path.open("rb") as f:
        for obj in ijson.items(f, "item"):
            if not isinstance(obj, dict):
                continue
            rec = normalize_unified_record(obj)
            if not _allowed(department_key(rec), allowed_departments):
                continue
            if rec.get("outpatient_number") in sampled_ids:
                continue
            seen_eligible += 1
            _reservoir_update(pool, rec, need, seen_eligible)

    for r in pool:
        if len(sampled) >= n:
            break
        rr = dict(r)
        if rr.get("outpatient_number") not in sampled_ids:
            sampled.append(rr)
            sampled_ids.add(rr.get("outpatient_number"))

    if len(sampled) < n:
        print(
            f"Warning: 只采到 {len(sampled)} 条（请求 {n} 条），数据或去重后不足。",
            file=sys.stderr,
        )

    return [strip_provenance(r) for r in sampled[:n]]


def strip_provenance(rec: dict) -> dict:
    """写入 triage_case.json 时可去掉调试字段；仿真仍保留临床字段。"""
    out = dict(rec)
    for k in ("_provenance", "_row_index", "_label_1_word", "_label_3_word"):
        out.pop(k, None)
    return out


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Sample triage cases for evaluation")
    parser.add_argument(
        "--data-path",
        type=Path,
        default=root / "data" / "triage_unified.json",
        help="数据源 JSON（triage_unified 大数组或旧版扁平列表）",
    )
    parser.add_argument("-n", type=int, default=5, help="Number of records to sample")
    parser.add_argument("--output", default=None, help="Output path (default: stdout)")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument(
        "--legacy-json-load",
        action="store_true",
        help="强制用 json.load 读入（仅适合小文件；大文件勿用）",
    )
    parser.add_argument(
        "--departments-json",
        type=Path,
        default=None,
        help="JSON 数组，标准科室名白名单；仅采样 department 落在其中的病例（与 workflow 多选一致）",
    )
    args = parser.parse_args()

    allowed: frozenset[str] | None = None
    if args.departments_json and args.departments_json.exists():
        raw = json.loads(args.departments_json.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            print("Error: --departments-json 必须是 JSON 数组", file=sys.stderr)
            sys.exit(1)
        allowed = frozenset(str(x).strip() for x in raw if str(x).strip())

    data_path: Path = args.data_path
    if not data_path.exists():
        print(f"Error: {data_path} not found", file=sys.stderr)
        sys.exit(1)

    size_b = data_path.stat().st_size
    # 超过约 4MB 一律流式，避免把 triage_unified 整文件载入内存
    use_stream = size_b >= 4 * 1024 * 1024 and not args.legacy_json_load

    if args.legacy_json_load and size_b >= 4 * 1024 * 1024:
        print(
            "Error: 文件过大，请去掉 --legacy-json-load，并安装 ijson：pip install ijson",
            file=sys.stderr,
        )
        sys.exit(1)

    if use_stream:
        if ijson is None:
            print(
                "Error: 大文件需要 ijson：pip install -r requirements.txt",
                file=sys.stderr,
            )
            sys.exit(1)
        sampled = sample_emr_unified_stream(data_path, args.n, args.seed, allowed)
    else:
        with open(data_path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            print("Error: 期望 JSON 数组", file=sys.stderr)
            sys.exit(1)
        if data and isinstance(data[0], dict) and "emr_compat" in data[0]:
            normalized = [normalize_unified_record(x) for x in data]
            sampled = sample_emr_legacy(normalized, args.n, args.seed, allowed)
        else:
            sampled = sample_emr_legacy(data, args.n, args.seed, allowed)

    result = json.dumps(sampled, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(result + "\n", encoding="utf-8")
        depts = {r.get("department") for r in sampled}
        print(
            f"Sampled {len(sampled)} records covering {len(depts)} departments -> {args.output}",
            file=sys.stderr,
        )
    else:
        print(result)


if __name__ == "__main__":
    main()
