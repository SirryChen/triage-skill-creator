#!/usr/bin/env python3
"""将 eval_cases.json 展开为 eval-*/triage_case.json，并写入第二阶段说明与状态。

供工作流页「准备第二阶段评测」调用；Simulator/Grader 在本地 Agent 环境（OpenClaw、Claude Code、Cursor 等）中执行。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 与具体 IDE 解耦的通用说明文件名
PHASE2_README_NAME = "PHASE2_NEXT_STEPS.md"

# 被 serve 以 scripts/ 加入 path 导入时，需能引用 viewer
_CREATOR_ROOT = Path(__file__).resolve().parents[1]
if str(_CREATOR_ROOT / "viewer") not in sys.path:
    sys.path.insert(0, str(_CREATOR_ROOT / "viewer"))
from skill_locate import find_skill_md  # noqa: E402


def _load_cases(eval_cases_path: Path) -> list[dict[str, Any]]:
    raw = json.loads(eval_cases_path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and isinstance(raw.get("cases"), list):
        return raw["cases"]
    raise ValueError("eval_cases.json 须为病例数组，或包含 cases 数组")


def prepare_phase2_workspace(workspace: Path, creator_root: Path) -> dict[str, Any]:
    """
    :return: {"ok": bool, "error"?: str, "eval_count"?: int, "skill_path"?: str, "readme_file"?: str, ...}
    """
    w = workspace.resolve()
    w.mkdir(parents=True, exist_ok=True)
    ec = w / "eval_cases.json"
    if not ec.is_file():
        return {"ok": False, "error": "缺少 eval_cases.json，请先执行采样"}
    try:
        cases = _load_cases(ec)
    except (json.JSONDecodeError, OSError, ValueError) as e:
        return {"ok": False, "error": str(e)}
    if not cases:
        return {"ok": False, "error": "eval_cases.json 中没有病例"}

    for i, rec in enumerate(cases, start=1):
        ed = w / f"eval-{i}"
        ed.mkdir(parents=True, exist_ok=True)
        out = ed / "triage_case.json"
        out.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    skill_path, _hint = find_skill_md(w, creator_root.resolve())
    skill_str = str(skill_path.resolve()) if skill_path is not None else ""

    cr = creator_root.resolve()
    wp = str(w)
    lines = [
        "# 第二阶段评测：对话仿真与评分（在本地 Agent 环境中执行）\n",
        "\n",
        "本页 Web 控制台**不会**自动运行 Simulator/Grader。请在您使用的 **Agent 宿主**中启动 subagent，例如：\n",
        "- OpenClaw、Claude Code、Cursor、或其它支持按文件指令调用子任务的工具。\n",
        "\n",
        "工作区已生成各 `eval-<i>/triage_case.json`。请按 **triage-skill-creator** 仓库内 `agents/simulator.md`、`agents/grader.md` 的约定传入参数。\n",
        "\n",
        "## 导诊 SKILL 路径（skill_path）\n",
        "\n",
        f"`{skill_str or '（未自动解析到 SKILL.md，请手动填写）'}`\n",
        "\n",
        "## 对每条病例 i = 1 .. " + str(len(cases)) + "\n",
        "\n",
        "### Simulator（见 `agents/simulator.md`）\n",
        "\n",
        "- `triage_case_path`: `" + wp + "/eval-{i}/triage_case.json`（将 {i} 替换为数字）\n",
        "- `skill_path`: 同上 SKILL 路径\n",
        f"- `output_dir`: `{wp}/eval-{{i}}/`（与 dialogue.json 输出目录一致）\n",
        "\n",
        "### Grader（见 `agents/grader.md`，在 dialogue.json 生成后）\n",
        "\n",
        f"- `dialogue_path`: `{wp}/eval-{{i}}/dialogue.json`\n",
        "- `triage_case_path`: 同上\n",
        f"- `output_dir`: `{wp}/eval-{{i}}/`\n",
        "\n",
        "## 聚合\n",
        "\n",
        "```bash\n",
        f'python "{cr / "scripts" / "aggregate_triage.py"}" "{wp}"\n',
        "```\n",
        "\n",
        "## 结果查看\n",
        "\n",
        "评测结果统一在工作流页查看：`http://127.0.0.1:3120/`（第 3、5 步）。\n",
    ]
    readme_path = w / PHASE2_README_NAME
    readme_path.write_text("".join(lines), encoding="utf-8")

    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    status = {
        "prepared": True,
        "prepared_at": now,
        "eval_count": len(cases),
        "skill_path": skill_str,
        "readme_file": str(readme_path),
        "prompt_file": str(readme_path),
        "started": False,
    }
    (w / "workflow_phase2.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return {
        "ok": True,
        "eval_count": len(cases),
        "skill_path": skill_str,
        "readme_file": str(readme_path),
        "prompt_file": str(readme_path),
    }


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Prepare eval-* dirs from eval_cases.json")
    ap.add_argument("workspace", type=Path, help="工作区目录（含 eval_cases.json）")
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    r = prepare_phase2_workspace(args.workspace, root)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r.get("ok") else 1)


if __name__ == "__main__":
    main()
