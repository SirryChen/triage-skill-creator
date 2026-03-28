"""将 grading_rubric.md 解析为结构化数据，并序列化回磁盘格式。

界面只编辑「细则正文」，固定页头由代码注入，不向用户暴露源文件全文。
"""

from __future__ import annotations

import re
from typing import Any

RUBRIC_HEADER = """# 第二阶段评分细则（PIORS 风格，可经工作流界面编辑）

本文件由 **workflow 工作流界面** 保存；评测时 **Grader subagent** 应优先遵循此处与 `references/eval_metrics.md` 一致的口径。

---

"""


def _split_by_h2(text: str) -> dict[str, str]:
    """按二级标题切分；返回 {标题: 正文}，正文不含 ## 行。"""
    text = text.replace("\r\n", "\n").strip()
    first_h2 = re.search(r"^## ", text, re.MULTILINE)
    if not first_h2:
        return {}
    text = text[first_h2.start() :]
    parts = re.split(r"(?m)^## (.+)$", text)
    out: dict[str, str] = {}
    i = 1
    while i + 1 < len(parts):
        title = parts[i].strip()
        body = parts[i + 1].strip()
        out[title] = body
        i += 2
    return out


def _parse_score_table(body: str) -> list[dict[str, str]]:
    """解析 | 分 | 标准 | 表格为 [{score, criterion}, ...]。"""
    rows: list[dict[str, str]] = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        inner = line[1:].rstrip()
        if inner.endswith("|"):
            inner = inner[:-1].rstrip()
        cells = [c.strip() for c in inner.split("|")]
        if len(cells) < 2:
            continue
        score, criterion = cells[0], cells[1]
        if score in ("分", "-", "—") or re.match(r"^[-:]+$", score):
            continue
        if criterion == "标准" and score == "分":
            continue
        rows.append({"score": score, "criterion": criterion})
    return rows


def _table_to_md(rows: list[dict[str, str]]) -> str:
    if not rows:
        return ""
    lines = [
        "| 分 | 标准 |",
        "|----|------|",
    ]
    for r in rows:
        sc = str(r.get("score", "")).replace("|", "\\|")
        cr = str(r.get("criterion", "")).replace("|", "\\|")
        lines.append(f"| {sc} | {cr} |")
    return "\n".join(lines)


def pad_score_rows(rows: list[dict[str, str]], target: int = 5) -> list[dict[str, str]]:
    """保证 1..target 分行可编辑。"""
    by_score: dict[str, str] = {}
    for r in rows:
        sc = str(r.get("score", "")).strip()
        if sc:
            by_score[sc] = str(r.get("criterion", "")).strip()
    out: list[dict[str, str]] = []
    for i in range(1, target + 1):
        k = str(i)
        out.append({"score": k, "criterion": by_score.get(k, "")})
    return out


def parse_rubric_file(text: str) -> dict[str, Any]:
    """全文 -> 前端可用的结构化对象。"""
    sec = _split_by_h2(text)
    dept = sec.get("科室准确率", "").strip()
    info_body = sec.get("信息收集分（1–5）", "").strip()
    overall_body = sec.get("整体表现分（1–5）", "").strip()
    eff = sec.get("效率（记录用）", "").strip()
    custom = sec.get("自定义补充（可选）", "").strip()

    info_rows = _parse_score_table(info_body)
    overall_rows = _parse_score_table(overall_body)

    return {
        "dept_accuracy": dept,
        "info_collection": pad_score_rows(info_rows),
        "overall": pad_score_rows(overall_rows),
        "efficiency": eff,
        "custom": custom,
    }


def serialize_rubric_file(data: dict[str, Any]) -> str:
    """结构化对象 -> 写入 grading_rubric.md 的完整文本。"""
    dept = str(data.get("dept_accuracy", "")).strip()
    info_rows = data.get("info_collection") or []
    overall_rows = data.get("overall") or []
    eff = str(data.get("efficiency", "")).strip()
    custom = str(data.get("custom", "")).strip()

    if not isinstance(info_rows, list):
        info_rows = []
    if not isinstance(overall_rows, list):
        overall_rows = []

    def norm_row(r: Any) -> dict[str, str]:
        if isinstance(r, dict):
            return {
                "score": str(r.get("score", "")).strip(),
                "criterion": str(r.get("criterion", "")).strip(),
            }
        return {"score": "", "criterion": ""}

    info_rows = [norm_row(r) for r in info_rows]
    overall_rows = [norm_row(r) for r in overall_rows]
    info_rows = pad_score_rows(info_rows)
    overall_rows = pad_score_rows(overall_rows)

    # rstrip() 会去掉末尾换行，导致 --- 与 ## 粘连，故显式补回换行
    parts = [RUBRIC_HEADER.rstrip() + "\n\n"]

    parts.append("## 科室准确率\n\n")
    parts.append(dept if dept else "（请在此说明科室匹配规则。）")
    parts.append("\n\n")

    parts.append("## 信息收集分（1–5）\n\n")
    parts.append(_table_to_md(info_rows) or "| 分 | 标准 |\n|----|------|")
    parts.append("\n\n")

    parts.append("## 整体表现分（1–5）\n\n")
    parts.append(_table_to_md(overall_rows) or "| 分 | 标准 |\n|----|------|")
    parts.append("\n\n")

    parts.append("## 效率（记录用）\n\n")
    parts.append(eff if eff else "- 轮次数、护士平均回复长度：越少越好（在质量可接受前提下）。")
    parts.append("\n\n")

    parts.append("## 自定义补充（可选）\n\n")
    parts.append(custom if custom else "（在此追加你的额外扣分/加分规则。）")
    parts.append("\n")

    return "".join(parts)


def empty_structured() -> dict[str, Any]:
    """空模板（新文件）。"""
    return {
        "dept_accuracy": "",
        "info_collection": pad_score_rows([]),
        "overall": pad_score_rows([]),
        "efficiency": "",
        "custom": "",
    }
