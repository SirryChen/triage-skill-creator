# 第二阶段评测：对话仿真与评分（在本地 Agent 环境中执行）

本页 Web 控制台**不会**自动运行 Simulator/Grader。请在您使用的 **Agent 宿主**中启动 subagent，例如：
- OpenClaw、Claude Code、Cursor、或其它支持按文件指令调用子任务的工具。

工作区已生成各 `eval-<i>/triage_case.json`。请按 **triage-skill-creator** 仓库内 `agents/simulator.md`、`agents/grader.md` 的约定传入参数。

## 导诊 SKILL 路径（skill_path）

`/Users/sirry/.openclaw/workspace/skills/triage-skill/SKILL.md`

## 对每条病例 i = 1 .. 30

### Simulator（见 `agents/simulator.md`）

- `triage_case_path`: `/Users/sirry/.openclaw/workspace/skills/triage-skill-creator/eval_result/iteration-1/eval-{i}/triage_case.json`（将 {i} 替换为数字）
- `skill_path`: 同上 SKILL 路径
- `output_dir`: `/Users/sirry/.openclaw/workspace/skills/triage-skill-creator/eval_result/iteration-1/eval-{i}/`（与 dialogue.json 输出目录一致）

### Grader（见 `agents/grader.md`，在 dialogue.json 生成后）

- `dialogue_path`: `/Users/sirry/.openclaw/workspace/skills/triage-skill-creator/eval_result/iteration-1/eval-{i}/dialogue.json`
- `triage_case_path`: 同上
- `output_dir`: `/Users/sirry/.openclaw/workspace/skills/triage-skill-creator/eval_result/iteration-1/eval-{i}/`

## 聚合

```bash
python "/Users/sirry/.openclaw/workspace/skills/triage-skill-creator/scripts/aggregate_triage.py" "/Users/sirry/.openclaw/workspace/skills/triage-skill-creator/eval_result/iteration-1"
```

## 结果查看

评测结果统一在工作流页查看：`http://127.0.0.1:3120/`（第 3、5 步）。
