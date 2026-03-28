---
name: triage-skill-creator
slug: triage-trainer
version: 2.2.1
description: "创建、评测并迭代用于门诊导诊的 LLM skill。适用场景：导诊/分诊、科室推荐、患者接诊、挂号引导、门诊接待机器人。关键词：导诊, 分诊, 科室推荐, patient intake, department recommendation, triage workflow."
---

# Triage Skill Creator

帮用户构建「指导 LLM 做门诊导诊」的 skill，并通过**两阶段评测**持续迭代。本文档按 **路径变量 → 目录结构 → 流程步骤** 排列，执行时优先看「评测目录（eval_result）」与「参考文件索引」。

---

## 目录

1. [路径变量与工作流约定](#路径变量与工作流约定)
2. [triage-skill-creator 仓库结构](#triage-skill-creator-仓库结构)
3. [产物包：triage-skill](#产物包triage-skill)
4. [评测目录（eval_result / iteration-N）](#评测目录eval-result--iteration-n)
5. [前置规则（必读）](#前置规则必读)
6. [标准流程总览](#标准流程总览)
7. [Step 1～6](#step-1确定配置)
8. [参考文件与脚本索引](#参考文件与脚本索引)

---

## 路径变量与工作流约定

所有**未写绝对路径**的相对路径，均以 **`<triage-skill-creator>`**（本 SKILL.md 所在目录）为根。

| 变量 | 含义 |
|------|------|
| `<triage-skill-creator>` | 本仓库根目录 |
| `<eval_result>` | **当前迭代评测目录**：其下**直接**包含 `eval-1`、`eval-2`… 与 **`benchmark.json`**。相对路径典型值 **`eval_result/iteration-1`**（首轮）。**实际路径**以 `references/workflow_workspace.json` 的 **`eval_result_path`** 为准（相对 `<triage-skill-creator>` 解析）；若 JSON 中仅有历史字段，与 `eval_result_path` 同义者亦可解析（见该文件注释）。 |
| `iteration-N` | 第 N 轮迭代文件夹名（首轮 `iteration-1`）；**`<eval_result>`** 常为其完整路径（含父级 `eval_result/` 等）。 |

**关键约定（避免网页端读不到结果）：**

- **`/api/review-data`** 会在 **`<eval_result>`** 目录下查找**直接子目录** `eval-1`、`eval-2`… 与 **`benchmark.json`**。因此 **`<eval_result>` 必须指向「含 `eval-*` 的那一层」**（例如 **`eval_result/iteration-1`**），**不要**只填到 **`eval_result`** 这一层父目录（否则 `eval-*` 在子目录内时 **eval_count 为 0**）。
- 建议在 `references/workflow_workspace.json` 里把 **`eval_result_path`** 设为 **`eval_result/iteration-1`**（或你实际使用的迭代目录），与工作流页默认拉取 **`/api/defaults`** 一致。
- 工作流页输入框填写与 **`eval_result_path`** 相同的路径；调用 HTTP API 时传入该目录的**绝对路径**（查询参数名以 `viewer/workflow/serve.py` 为准）。

---

## triage-skill-creator 仓库结构

```
<triage-skill-creator>/
├── SKILL.md                          # 本文件
├── requirements.txt                  # serve.py / sample_emr 等依赖（含 ijson）
├── references/
│   ├── triage_guide.md               # 编写导诊 skill 的职责与流程
│   ├── prompts_cn.md                 # 中文 prompt 模板
│   ├── standard_departments.json     # 科室列表（工作流默认数据源、编写 departments.md 范围）
│   ├── workflow_workspace.json       # 声明默认 eval_result_path（当前迭代评测目录）
│   ├── grading_rubric.md             # 第二阶段评分细则（工作流可编辑）
│   └── eval_metrics.md               # 指标说明与基线
├── evals/
│   └── evals.json                    # 第一阶段用例（prompt / expected_output）
├── agents/
│   ├── grader.md
│   ├── simulator.md
│   ├── patient.md
│   └── supervisor.md
├── scripts/
│   ├── sample_emr.py                 # 采样 → eval_cases.json
│   ├── prepare_phase2.py             # eval_cases → eval-*/triage_case.json + 二阶段说明
│   └── aggregate_triage.py           # 聚合二轮 grading → benchmark.json
├── data/
│   └── triage_unified.json           # 采样主数据源（体积大，流式读取）
├── viewer/
│   ├── workflow/
│   │   ├── serve.py                  # 本地 HTTP 服务（默认 3120）
│   │   └── index.html
│   ├── skill_locate.py               # 解析 SKILL.md 路径（评测目录预览 / prepare_phase2）
│   ├── rubric_serialize.py
│   └── open_browser.py
└── eval_result/                      # 默认评测根目录（可改）；其下为 iteration-N
```

环境变量（可选，供 `viewer/skill_locate.py`）：**`TRIAGE_SKILL_FOLDER`**、**`TRIAGE_EXTRA_SKILL_ROOTS`**，用于在复杂目录布局中锁定导诊 skill 包。

---

## 产物包：triage-skill

固定命名（用户要求改名时先说明工作流依赖固定名，再按约定执行）：

| 对象 | 路径 |
|------|------|
| 技能包目录 | `<triage-skill-creator>/../triage-skill/`（与 triage-skill-creator **同级**） |
| 技能主文件 | `triage-skill/SKILL.md`（YAML `name: triage-skill`） |
| 科室对照 | `triage-skill/references/departments.md` |

```
triage-skill/
├── SKILL.md
└── references/
    └── departments.md
```

编写前必读：`references/triage_guide.md`、`references/prompts_cn.md`；`departments.md` 的科室范围与 `standard_departments.json` 一致。

---

## 评测目录（eval_result / iteration-N）

以下 **`<eval_result>`** 表示**当前迭代**根目录，路径形如 **`eval_result/iteration-1/`**（相对 `<triage-skill-creator>`，与 `eval_result_path` 一致）。

### 总览树

```
<eval_result>/                               # 传给工作流 API 的目录（含 eval-* 子目录）
├── benchmark.json                         # 第一轮：run_summary + runs；第二轮：aggregate 覆盖为另一套结构
├── workflow_config.json                   # 工作流「保存配置」写入（科室多选、采样参数等）
├── selected_departments.json              # 科室白名单（见下方格式约束）
├── eval_cases.json                        # sample_emr 输出；prepare 前由采样生成
├── workflow_phase2.json                   # 二阶段：是否已准备、是否已标记开始等
├── PHASE2_NEXT_STEPS.md                   # prepare_phase2.py 生成的 Agent 操作说明
├── feedback.json                          # 结构化 per-eval 反馈（供 /api/review-data 展示）
├── workflow_feedback.json                 # UI「反馈」按钮写入的简单备忘（与 feedback.json 并存）
└── eval-<i>/                              # i = 1,2,… 与用例或病例顺序一致
    ├── eval_metadata.json                 # 推荐：id、名称、第一阶段 prompt（网页展示用）
    ├── triage_case.json                   # 二阶段：单条病例（由 prepare 从 eval_cases 拆分，或手放）
    ├── dialogue.json                      # 二阶段：Simulator 输出
    ├── grading.json                       # 二阶段：写在 eval 根下；含 info_score/overall_score 时前端识别为二轮
    ├── with_skill/                        # 第一阶段
    │   ├── grading.json                   # 与下方二选一：可放在 with_skill 根或 run-* 下
    │   ├── outputs/
    │   │   └── response.md                # 与 response.txt 二选一
    │   └── run-<任意>/                    # 可选：多轮跑时子目录
    │       ├── outputs/response.md
    │       └── grading.json
    └── without_skill/
        └── （结构同 with_skill）
```

### `selected_departments.json`（重要）

- **`scripts/sample_emr.py` 要求文件内容为 JSON 数组**，元素为科室名字符串，例如：`["内科","外科",…]`。
- 工作流 `/api/config` 保存时写入的即是**数组**；若 Agent 手建文件，**勿**写成 `{"departments":[…]}` 对象，否则采样脚本报错。

### `feedback.json`（结构化，与 `workflow_feedback.json` 区分）

工作流 `/api/review-data` 读取 **`feedback.json`**，解析字段 **`reviews`**（数组），元素至少含 **`run_id`**、**`feedback`**（字符串），用于把文字挂到对应 eval 上。

`workflow_feedback.json` 由 **`/api/feedback`** 写入，格式为简单对象（如 `{"feedback":"…","source":"workflow_ui"}`），**不替代** `feedback.json` 的 per-eval 映射。

### 第一阶段产物路径小结

| 文件 | 位置 |
|------|------|
| 模型回复 | `eval-<i>/with_skill/outputs/response.md` 或 `response.txt`（二选一） |
| 同上 | `eval-<i>/without_skill/outputs/…` |
| 断言评分 | `eval-<i>/with_skill/grading.json` 或 `eval-<i>/with_skill/run-*/grading.json` |
| 轮次 Benchmark | **`<eval_result>/benchmark.json`**（需自建或脚本写入；不会从各 grading 自动合并） |

### 第二阶段产物路径小结

| 步骤 | 产物 |
|------|------|
| 采样 | `<eval_result>/eval_cases.json` |
| 工作流「准备第二阶段」→ 调用 **`scripts/prepare_phase2.py`** | 各 `eval-<i>/triage_case.json`、`PHASE2_NEXT_STEPS.md`、`workflow_phase2.json` |
| Simulator | `eval-<i>/dialogue.json` |
| Grader | `eval-<i>/grading.json`（根级，格式见 `agents/grader.md` 二阶段） |
| 聚合 | `python scripts/aggregate_triage.py <eval_result>` → 覆盖 **`benchmark.json`**（二轮结构） |

### 科室来源优先级（与 `departments.md` 的关系）

1. **`<eval_result>/selected_departments.json`**（工作流保存，采样白名单）
2. **`references/standard_departments.json`**（界面默认全选）
3. **`references/triage_guide.md` 内列表**（仅辅助写 `triage-skill/references/departments.md`，不参与采样）

`triage-skill/references/departments.md` 与采样 JSON **相互独立**，但科室名范围应与 `standard_departments.json` 一致。

---

## 前置规则（必读）

### 规则 1：命名固定

见上文 [产物包：triage-skill](#产物包triage-skill)。

### 规则 2：可视化前端必须启动

仅 **`viewer/workflow/serve.py`** 工作流页为必选项；第一、二轮结果均在 **第 3、5 步** 内嵌展示。

### 规则 3：页面与 Agent 分工

- **工作流页**：科室与 **eval_result 路径**、评分细则、采样、「准备第二阶段」、结果查看、简单反馈。
- **模型调用**：由 **Agent / subagent** 执行；页面不自动跑 Simulator/Grader。
- **反馈**：结构化 per-eval 用 **`feedback.json`**；底部备忘可用 **`workflow_feedback.json`**（见上）。

### 规则 4：默认评测根目录

若用户未指定，默认 **`eval_result/iteration-1`**（写在 **`workflow_workspace.json`** 的 **`eval_result_path`**）；打开工作流页后提示确认路径是否为**含 `eval-*` 的迭代目录**，而非仅父文件夹 **`eval_result`**。

---

## 标准流程总览

```
Step 1   确定配置（默认即可，少反问）
Step 2   创建 triage-skill（SKILL.md + references/departments.md）
Step 3   编写 evals/evals.json
Step 3b  启动工作流页 → 用户保存配置（workflow_config、selected_departments 等）
Step 4   第一阶段评测 → eval-<i>/with_skill|without_skill + grading + benchmark.json
         ↓ 用户编辑 grading_rubric、勾选可进入二阶段
         ↓ 采样 → eval_cases.json
         ↓ 「准备第二阶段」→ triage_case.json + PHASE2_NEXT_STEPS.md + workflow_phase2.json
Step 5   第二阶段：Simulator → dialogue.json；Grader → grading.json；aggregate_triage.py
Step 5d  工作流第 5 步查看结果（必须）
Step 6   改 skill → 新建 iteration-(N+1) → 重复 Step 4～5
```

---

## Step 1：确定配置

用户说「帮我建导诊 skill」时，简短确认后**直接执行 Step 2、3**。

| 配置项 | 默认 |
|--------|------|
| 科室范围 | 以工作流勾选或 `standard_departments.json` 为准 |
| 复诊 / 行政问答 | 支持 |
| 对话风格 | 关怀、简洁 |

---

## Step 2：创建导诊 skill

### 2b 必读参考

- `references/triage_guide.md`
- `references/prompts_cn.md`

### 2c `triage-skill/SKILL.md` 骨架

**必须**结合 `triage_guide.md` 充实各节，勿空壳照搬：

```markdown
---
name: triage-skill
description: [根据配置生成，措辞要足够 "pushy" 以确保能触发]
---

# 导诊护士

你是一名医院门诊导诊护士。职责是与患者对话，收集症状和病史，推荐合适的就诊科室。

## 核心规则

- 不提供具体诊疗建议，不替患者做医疗决定
- 不帮忙挂号，不离开导诊台职责范围
- 使用通俗语言，避免医学术语
- 每次只问 1-2 个问题，保持口语化

## 对话流程

1. 问候患者，询问来院原因
2. 根据主诉追问症状细节（部位、持续时间、伴随症状）
3. 信息充分时推荐科室并说明理由
4. 确认患者无其他问题后结束

复诊患者：先确认上次就诊科室和诊断，询问症状变化，通常推荐同一科室。

## 每轮动作选择

每次回复前从以下动作中选一个：

| 动作 | 何时选择 |
|------|---------|
| 症状询问 | 信息不足，需了解主诉或现病史 |
| 病史询问 | 需要既往史、过敏史（患者着急时可跳过） |
| 推荐科室 | 信息充分，可做出科室推荐 |
| 医疗问题回复 | 患者对术语或分诊结果有疑问 |
| 其他问题回复 | 行政问题或无关话题，快速回应后引导回正题 |
| 提供快速帮助 | 紧急情况或患者情绪极度激动 |
| 结束确认 | 科室推荐完毕，确认患者无其他需求 |

## 信息收集清单

必须收集：
- 主诉（最主要症状 + 持续时间）
- 现病史（症状发展、伴随症状、已做检查或治疗）

尽量收集：
- 既往史（高血压、糖尿病、心脏病、手术外伤史）
- 药物过敏史

## 科室列表

见 references/departments.md

## 共情指南

- 先回应患者情绪，再追问信息（「理解您的担心，我们先了解一下情况」）
- 患者着急时简化提问，加快节奏
- 老年患者用更简单的语言，必要时重复关键信息
- 儿童家长可能更焦虑，注意安抚
```

### 2d `departments.md`

以 `standard_departments.json` 科室为行，参考 `triage_guide.md` 写常见症状列表示例：

```markdown
| 科室 | 常见症状 |
|------|---------|
| 皮肤科 | 皮疹、瘙痒、皮肤红肿、痤疮、脱发 |
| … | … |
```

---

## Step 3：编写评测用例

路径：**`evals/evals.json`**，至少 3 条。示例：

```json
{
  "skill_name": "triage-skill",
  "evals": [
    {
      "id": 1,
      "prompt": "我最近总是头晕，想来医院看看，不知道该挂什么科",
      "expected_output": "引导患者描述症状细节（持续时间、诱因、伴随症状），最终推荐神经内科或相关科室"
    }
  ]
}
```

完成后**立即 Step 3b**。

---

## Step 3b：启动工作流页

### 启动命令摘要

```bash
lsof -ti:3120 | xargs kill -9 2>/dev/null || true
cd <triage-skill-creator> && pip install -r requirements.txt
# 长期运行示例：tmux / nohup
python viewer/workflow/serve.py --no-browser -p 3120
curl -s http://127.0.0.1:3120/api/runtime   # 期望 HTTP 200
```

帮助用户在本地打开网址：**http://127.0.0.1:3120**

### 用户在工作流页需完成

1. 科室勾选（默认来自 `standard_departments.json`）。
2. **eval_result 路径**：填 **`eval_result/iteration-1`**（或等价绝对路径，即 **`<eval_result>`**），保存后更新 **`references/workflow_workspace.json`** 中的 **`eval_result_path`**。
3. 保存配置 → 在 **该 `<eval_result>` 目录下** 生成 **`workflow_config.json`**、**`selected_departments.json`**（及迭代目录，视实现而定）。

> 旧文档若写「根目录下 `workflow_config.json`」系笔误；以 **`<eval_result>` 目录内** 为准。

---

## Step 4：第一阶段评测

**由 Agent 启动多个 subagent 分别作为模拟患者、模拟护士、评分员，绝对不能直接生成完整对话和评分。**

### 前置

- 从 **`references/workflow_workspace.json`** 解析 **`eval_result_path`**，得到 **`<eval_result>`**（须为含 **`iteration-N`** 的完整迭代目录，例如 **`eval_result/iteration-1`**）。
- 确认 **`<eval_result>`** 下存在 **`eval-<i>/`** 目录（与 `evals.json` 条数一致）。

### 任务

1. 读 **`evals/evals.json`**。
2. 每条用例 `i`：建 **`eval-<i>/`** 及 **`with_skill` / `without_skill`** 下 **`outputs/`**（结构见 [评测目录（eval_result / iteration-N）](#评测目录eval-result--iteration-n)）。
3. **with_skill**：加载 **`../triage-skill/SKILL.md`**，输入 `prompt`，输出 **`response.md`**（或 **`.txt`**）。
4. **without_skill**：不挂载 skill，同 prompt，写回复文件。
5. **Grader**：按 **`agents/grader.md`**（第一阶段），写入各配置下的 **`grading.json`**。
6. 推荐为每条生成 **`eval_metadata.json`**（含 **`prompt`**），便于网页展示。
7. 写 **`<eval_result>/benchmark.json`**（第一轮结构见下）；**不会**从 grading 自动推导。

### 第一阶段 `grading.json`（前端强依赖）

```json
{
  "expectations": [
    { "text": "string", "passed": true, "evidence": "string" }
  ],
  "summary": {
    "passed": 0,
    "total": 0,
    "pass_rate": 0.0
  },
  "department_correct": true,
  "information_collection_score": 3,
  "overall_score": 3,
  "turn_count": 1,
  "notes": ""
}
```

- `expectations` 至少 2 条（建议 3 条）；`summary` 与之一致（`pass_rate = passed/total`）。

### 第一轮 `benchmark.json`

```json
{
  "run_summary": {
    "with_skill": { "pass_rate": { "mean": 0.0 } },
    "without_skill": { "pass_rate": { "mean": 0.0 } },
    "delta": { "pass_rate": "+0.0%" }
  },
  "runs": [
    {
      "eval_id": "eval-1",
      "configuration": "with_skill",
      "result": { "pass_rate": 0.0, "passed": 0, "total": 0 }
    }
  ]
}
```

### 完成标准

- 每条 **`eval-<i>`** 下 with/without 均有回复文件 + **`grading.json`**。
- **`<eval_result>/benchmark.json`** 存在。
- 工作流 **第 3 步** 能拉到 **`/api/review-data`** 数据。

---

## 进入第二阶段前（用户 + 工作流）

1. 编辑 **`references/grading_rubric.md`** 并保存。
2. 勾选「可开始第二阶段」。
3. **采样**（二选一）：
   - **A**：工作流「执行采样」→ 调用 **`sample_emr.py`**，写入 **`<eval_result>/eval_cases.json`**。
   - **B**：命令行同原参数，**`--departments-json`** 指向 **`<eval_result>/selected_departments.json`**（**JSON 数组**）。

4. 点击 **「准备第二阶段评测」**（或命令行 **`python scripts/prepare_phase2.py <eval_result>`**）：将 **`eval_cases.json`** 拆成各 **`eval-<i>/triage_case.json`**，并生成 **`PHASE2_NEXT_STEPS.md`**、**`workflow_phase2.json`**。

> 采样**不会**自动拆分 `triage_case.json`；拆分由 **prepare** 步骤完成。

---

## Step 5：第二阶段评测

### 5a Simulator

参数见 **`agents/simulator.md`**：`triage_case_path`、`skill_path`（`../triage-skill/SKILL.md`）、`output_dir`（`eval-<i>/`）。输出 **`dialogue.json`**。

### 5b Grader

参数见 **`agents/grader.md`**（第二阶段 JSON 结构）。输出 **`eval-<i>/grading.json`**（根级；与第一阶段写在 `with_skill/` 下的文件**路径不同**）。

### 5c 聚合

```bash
python <triage-skill-creator>/scripts/aggregate_triage.py <eval_result>
```

会**覆盖** **`<eval_result>/benchmark.json`** 为二轮统计结构（与第一轮 `run_summary`/`runs` 不同）。可选输入 **`all_results.json`**（若存在则优先生效，见脚本说明）。

指标含义：**`references/eval_metrics.md`**。

### 5d 查看结果

工作流 **第 5 步** 必须打开；per-eval 结构化反馈写入 **`feedback.json`**（schema 见上）。

---

## Step 6：改进并迭代

根据 **`benchmark.json`、各 `grading.json`、反馈** 修改 **`triage-skill/SKILL.md`** 或 **`departments.md`**；新建 **`iteration-2`**… 重复 Step 4～5。

| 现象 | 改进方向 |
|------|----------|
| 科室准确率低 | 补 `departments.md` 映射 |
| 信息收集分低 | 强化症状询问与示例 |
| 轮次过多 | 合并提问、减少重复 |

原则：泛化、精简、解释原因而非堆砌禁令。

---

## 参考文件与脚本索引

路径均相对 **`<triage-skill-creator>`**，除非标明在 **`<eval_result>`**（当前迭代评测目录）。

| 路径 | 用途 |
|------|------|
| `references/triage_guide.md` | 导诊 skill 编写指南 |
| `references/prompts_cn.md` | 中文 prompt 模板 |
| `references/standard_departments.json` | 标准科室（界面默认、编写 departments.md） |
| `references/workflow_workspace.json` | 声明 **`eval_result_path`**（即 **`<eval_result>`** 的相对路径） |
| `references/grading_rubric.md` | 二阶段评分细则 |
| `references/eval_metrics.md` | 评测指标与基线 |
| `evals/evals.json` | 第一阶段用例 |
| `agents/grader.md` | 评分指令（一、二阶段格式不同） |
| `agents/simulator.md` | 对话仿真指令 |
| `agents/patient.md` | 患者角色（simulator 引用） |
| `agents/supervisor.md` | 监督逻辑（simulator 引用） |
| `scripts/sample_emr.py` | 采样 → `eval_cases.json` |
| `scripts/prepare_phase2.py` | 拆分 `triage_case.json`、生成 `PHASE2_NEXT_STEPS.md`、`workflow_phase2.json` |
| `scripts/aggregate_triage.py` | 二轮聚合 → `benchmark.json` |
| `data/triage_unified.json` | 采样数据源 |
| `viewer/workflow/serve.py` | 工作流服务 |
| `viewer/workflow/index.html` | 前端 |
| `viewer/skill_locate.py` | 定位 `SKILL.md` |
| `viewer/README.md` | viewer 说明 |
| `requirements.txt` | Python 依赖 |
| **`<eval_result>/workflow_config.json`** | 工作流保存的配置 |
| **`<eval_result>/selected_departments.json`** | 科室白名单（**JSON 数组**） |
| **`<eval_result>/eval_cases.json`** | 采样结果 |
| **`<eval_result>/workflow_phase2.json`** | 二阶段准备/开始状态 |
| **`<eval_result>/PHASE2_NEXT_STEPS.md`** | 二阶段 Agent 操作说明 |
| **`<eval_result>/benchmark.json`** | 一轮或二轮汇总（格式随阶段变化） |
| **`<eval_result>/feedback.json`** | 结构化 per-eval 反馈 |
| **`<eval_result>/workflow_feedback.json`** | UI 简单反馈 |
| **`<eval_result>/eval-<i>/eval_metadata.json`** | 推荐；网页展示 prompt |
| **`<eval_result>/eval-<i>/triage_case.json`** | 二阶段病例 |
| **`<eval_result>/eval-<i>/dialogue.json`** | 二阶段对话 |
| **`<eval_result>/eval-<i>/grading.json`** | 二阶段根级评分（与 `with_skill/grading.json` 区分） |

---

**版本说明（2.2.1）**：全文统一用 **`<eval_result>`** 表示当前迭代评测目录（不再使用「工作区」表述）；与 **`references/workflow_workspace.json`** 中的 **`eval_result_path`** 一致。2.2.0 起已含：仓库与评测目录树、**`selected_departments.json`** 数组格式、**`/api/review-data`** 须指向含 **`eval-*`** 的目录、**`feedback.json` / `workflow_feedback.json`** 区分、**采样 vs prepare**、**`workflow_config.json`** 位于 **`<eval_result>`** 内、索引含 **`prepare_phase2.py`**。
