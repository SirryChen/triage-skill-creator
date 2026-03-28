"""在工作区及常见「skill 包」目录布局中定位 SKILL.md（与具体 IDE 无关）。

支持：
- <工作区>/triage-skill/SKILL.md、<工作区>/SKILL.md
- 各层目录下常见 skill 包根：``.cursor/skills``、``.openclaw/skills``、``openclaw/skills`` 等下的 ``<包名>/SKILL.md``
- 与 triage-skill-creator 同位于 ``…/skills/<name>/`` 下的其它 skill 包
- 环境变量 ``TRIAGE_EXTRA_SKILL_ROOTS``（路径列表，与 PATH 相同分隔符）
- 环境变量 ``TRIAGE_SKILL_FOLDER``：优先选用该文件夹名
"""

from __future__ import annotations

import os
from pathlib import Path

# 相对某「搜索锚点」目录尝试的常见 skill 容器路径（可按需扩展）
_SKILL_PACK_REL_PARTS: tuple[tuple[str, ...], ...] = (
    (".cursor", "skills"),
    (".openclaw", "skills"),
    ("openclaw", "skills"),
)


def find_skill_md(workspace: Path, creator_root: Path) -> tuple[Path | None, str]:
    """
    :param workspace: 界面中的工作区路径（**可以尚未创建**；仍会从 triage-skill-creator 同级 skill 等位置查找）
    :param creator_root: triage-skill-creator 仓库根目录
    :return: (SKILL.md 路径, 未找到时的简短说明)
    """
    w = workspace.resolve()

    inferred_pref_names: list[str] = []
    # 例如 /path/triage-skill-workspace/iteration-1 -> triage-skill
    for p in (w, w.parent, w.parent.parent if w.parent != w else w):
        name = p.name
        if name.endswith("-workspace") and len(name) > len("-workspace"):
            inferred_pref_names.append(name[: -len("-workspace")])

    candidates: list[Path] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        try:
            key = str(p.resolve())
        except OSError:
            return
        if key in seen:
            return
        if p.is_file():
            seen.add(key)
            candidates.append(p)

    def collect_from_skills_dir(skills_dir: Path) -> None:
        if not skills_dir.is_dir():
            return
        for child in sorted(skills_dir.iterdir()):
            if child.is_dir():
                add(child / "SKILL.md")

    def collect_skill_packs_under(anchor: Path) -> None:
        for parts in _SKILL_PACK_REL_PARTS:
            collect_from_skills_dir(anchor.joinpath(*parts))

    # 1) 工作区根下常见布局（路径已是目录时）
    if w.is_dir():
        for p in (w / "triage-skill" / "SKILL.md", w / "SKILL.md"):
            if p.is_file():
                return p, ""

    # 2) 工作区内的 skill 包目录
    if w.is_dir():
        collect_skill_packs_under(w)

    # 3) 从声明的工作区路径向上遍历祖先
    cur: Path | None = w
    for _ in range(16):
        if cur is None:
            break
        collect_skill_packs_under(cur)
        if cur.parent == cur:
            break
        cur = cur.parent

    # 4) 与 triage-skill-creator 同级的其它 skill（…/skills/<name>/SKILL.md）
    try:
        parent = creator_root.resolve().parent
    except OSError:
        parent = creator_root.parent
    if parent.is_dir() and parent.name == "skills":
        for child in sorted(parent.iterdir()):
            if not child.is_dir():
                continue
            try:
                if child.resolve() == creator_root.resolve():
                    continue
            except OSError:
                continue
            add(child / "SKILL.md")

    # 5) 额外搜索根（用户自定义）
    extra = os.environ.get("TRIAGE_EXTRA_SKILL_ROOTS", "")
    for part in extra.split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        try:
            er = Path(part).expanduser().resolve()
            if er.is_dir():
                collect_skill_packs_under(er)
                add(er / "triage-skill" / "SKILL.md")
                add(er / "SKILL.md")
        except OSError:
            continue

    pref = os.environ.get("TRIAGE_SKILL_FOLDER", "").strip()
    if pref:
        for c in candidates:
            try:
                if c.parent.name == pref:
                    return c, ""
            except OSError:
                continue

    # 若 workspace 名包含 "<skill>-workspace"，优先回选同名 skill 包
    for inferred in inferred_pref_names:
        for c in candidates:
            try:
                if c.parent.name == inferred:
                    return c, ""
            except OSError:
                continue

    # triage-skill 场景下的常见目录名优先
    for name in ("triage-skill", "triage_skill"):
        for c in candidates:
            if c.parent.name == name:
                return c, ""

    for name in ("triage-nurse", "triage_nurse"):
        for c in candidates:
            if c.parent.name == name:
                return c, ""

    if candidates:
        return candidates[0], ""

    return (
        None,
        "未找到 SKILL.md。可放在工作区 triage-skill/ 下，或 skill 包目录（如 .cursor/skills、.openclaw/skills）下；也可设置 TRIAGE_SKILL_FOLDER。",
    )
