# viewer（可视化前端，必须配合使用）

本目录提供工作流本地页面，由 Python 内置 HTTP 提供，**无需单独构建 npm**。

## 1. 工作流 `workflow/`

- **启动**：在 `triage-skill-creator` 根目录执行  
  `python viewer/workflow/serve.py`（默认端口 3120）
- **内容**：科室多选、工作区配置、保存 `references/grading_rubric.md`、执行采样生成 `eval_cases.json`、工作流反馈

## 2. 评测结果展示

- **方式**：统一在工作流页内展示（第 3 步第一轮 / 第 5 步第二轮）
- **数据源**：`/api/review-data?workspace=...`
