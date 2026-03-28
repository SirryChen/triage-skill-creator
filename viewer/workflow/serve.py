#!/usr/bin/env python3
"""本地工作流控制台：科室多选、保存评分细则、执行采样；第 3、5 步内嵌评测结果（/api/review-data）。

用法（示例，请按你的 skill 包路径调整 cd）：

  cd /path/to/triage-skill-creator
  python viewer/workflow/serve.py
  python viewer/workflow/serve.py -p 3120
  python viewer/workflow/serve.py --no-browser

启动后会尝试自动打开默认浏览器到本服务 URL。

默认评测目录见 ``references/workflow_workspace.json`` 的 ``eval_result_path``（绝对路径）；兼容旧字段 ``workspace``。启动或 ``GET /api/defaults`` 时会扫描 ``eval_result/iteration-*``，若磁盘上存在更大 N 的迭代目录则自动升级并写回声明。
SKILL 预览除工作区外，会在常见 skill 包目录（``.cursor/skills``、``.openclaw/skills`` 等）及 triage-skill-creator 同级包中查找；可用环境变量 TRIAGE_SKILL_FOLDER、TRIAGE_EXTRA_SKILL_ROOTS 约束。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

_VIEWER = Path(__file__).resolve().parents[1]
if str(_VIEWER) not in sys.path:
    sys.path.insert(0, str(_VIEWER))
from open_browser import open_browser  # noqa: E402
from rubric_serialize import empty_structured, parse_rubric_file, serialize_rubric_file  # noqa: E402
from skill_locate import find_skill_md  # noqa: E402


_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from prepare_phase2 import prepare_phase2_workspace  # noqa: E402


# serve.py 位于 viewer/workflow/，项目根为 triage-skill-creator
ROOT = Path(__file__).resolve().parents[2]
RUBRIC_PATH = ROOT / "references" / "grading_rubric.md"
DEPT_PATH = ROOT / "references" / "standard_departments.json"
WORKSPACE_DECL_PATH = ROOT / "references" / "workflow_workspace.json"
SAMPLE_SCRIPT = ROOT / "scripts" / "sample_emr.py"
DATA_DEFAULT = ROOT / "data" / "triage_unified.json"
VENDOR_PATH = Path(__file__).resolve().parent / "_vendor"
PID_FILE = ROOT / ".workflow_serve.pid"

if VENDOR_PATH.is_dir() and str(VENDOR_PATH) not in sys.path:
    sys.path.insert(0, str(VENDOR_PATH))


def detect_runtime() -> dict:
    """检测宿主环境，供前端决定是否展示 OpenClaw 专属 UI。"""
    cwd = str(Path.cwd())
    root = str(ROOT)
    env_keys = {k.upper() for k in os.environ.keys()}
    env_markers = ("OPENCLAW", "OPENCLAW_API", "OPENCLAW_URL", "OPENCLAW_HOST")
    env_has_marker = any(k.startswith(env_markers) for k in env_keys)
    path_has_marker = ".openclaw" in cwd or ".openclaw" in root
    is_openclaw = bool(env_has_marker or path_has_marker)
    return {
        "is_openclaw": is_openclaw,
        "runtime": "openclaw" if is_openclaw else "generic",
    }


def _extract_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item.strip())
            elif isinstance(item, dict):
                txt = item.get("text")
                if isinstance(txt, str) and txt.strip():
                    parts.append(txt.strip())
                elif item.get("type") == "text" and isinstance(item.get("content"), str):
                    parts.append(item["content"].strip())
        return "\n".join([p for p in parts if p]).strip()
    if isinstance(content, dict):
        for key in ("text", "content", "message"):
            v = content.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _load_openclaw_gateway_settings() -> dict:
    ws_url = os.environ.get("OPENCLAW_GATEWAY_WS_URL", "").strip()
    token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "").strip()
    password = os.environ.get("OPENCLAW_GATEWAY_PASSWORD", "").strip()
    device_token = os.environ.get("OPENCLAW_GATEWAY_DEVICE_TOKEN", "").strip()
    session_key = os.environ.get("OPENCLAW_CHAT_SESSION_KEY", "agent:main:main").strip() or "agent:main:main"
    if ws_url and token:
        return {
            "ok": True,
            "ws_url": ws_url,
            "token": token,
            "password": password or None,
            "device_token": device_token or None,
            "session_key": session_key,
        }
    cfg_path = Path.home() / ".openclaw" / "openclaw.json"
    if not cfg_path.is_file():
        return {"ok": False, "error": "未找到 OpenClaw 配置；请设置 OPENCLAW_GATEWAY_WS_URL 与 OPENCLAW_GATEWAY_TOKEN。"}
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {"ok": False, "error": f"读取 OpenClaw 配置失败：{e}"}
    gateway = cfg.get("gateway") or {}
    auth = gateway.get("auth") or {}
    port = gateway.get("port", 18789)
    bind = (gateway.get("bind") or "loopback").strip().lower()
    host = "127.0.0.1" if bind in ("loopback", "localhost") else "localhost"
    token = token or str(auth.get("token") or "").strip()
    if not token:
        return {"ok": False, "error": "OpenClaw gateway token 为空。"}
    if not ws_url:
        ws_url = f"ws://{host}:{port}"
    return {
        "ok": True,
        "ws_url": ws_url,
        "token": token,
        "password": password or None,
        "device_token": device_token or None,
        "session_key": session_key,
    }


def _openclaw_chat(message: str, session_key: str | None = None, timeout_s: int = 45) -> dict:
    try:
        from websocket import create_connection  # type: ignore
    except Exception:
        return {"ok": False, "error": "缺少 websocket-client 依赖，无法连接 OpenClaw 网关。"}
    if not message.strip():
        return {"ok": False, "error": "消息不能为空。"}
    cfg = _load_openclaw_gateway_settings()
    if not cfg.get("ok"):
        return cfg
    sk = (session_key or cfg["session_key"]).strip() or cfg["session_key"]
    ws = None
    try:
        ws = create_connection(cfg["ws_url"], timeout=10)

        def request(method: str, params: dict) -> dict:
            rid = str(uuid.uuid4())
            ws.send(json.dumps({"type": "req", "id": rid, "method": method, "params": params}, ensure_ascii=False))
            while True:
                raw = ws.recv()
                resp = json.loads(raw)
                if resp.get("type") == "event":
                    continue
                if resp.get("type") == "res" and resp.get("id") == rid:
                    if resp.get("ok"):
                        return resp.get("payload") or {}
                    err = resp.get("error") or {}
                    raise RuntimeError(err.get("message") or "gateway request failed")

        connect_payload = {
            "minProtocol": 3,
            "maxProtocol": 3,
            "client": {
                "id": "webchat-ui",
                "version": "triage-workflow",
                "platform": "python",
                "mode": "webchat",
            },
            "role": "operator",
            "scopes": ["operator.admin", "operator.approvals", "operator.pairing"],
            "caps": ["tool-events"],
            "auth": {"token": cfg["token"]},
        }
        if cfg.get("password"):
            connect_payload["auth"]["password"] = cfg["password"]
        if cfg.get("device_token"):
            connect_payload["auth"]["deviceToken"] = cfg["device_token"]
        request("connect", connect_payload)
        sent_at = int(time.time() * 1000)
        request(
            "chat.send",
            {
                "sessionKey": sk,
                "message": message,
                "deliver": False,
                "idempotencyKey": str(uuid.uuid4()),
            },
        )
        deadline = time.time() + timeout_s
        collected: list[dict] = []
        seen: set[str] = set()
        first_reply_at: float | None = None
        settle_seconds = 1.5
        while time.time() < deadline:
            hist = request("chat.history", {"sessionKey": sk, "limit": 80})
            msgs = hist.get("messages") if isinstance(hist, dict) else []
            if isinstance(msgs, list):
                for m in msgs:
                    if not isinstance(m, dict):
                        continue
                    if (m.get("role") or "").lower() != "assistant":
                        continue
                    ts = m.get("timestamp")
                    if isinstance(ts, (int, float)) and ts < sent_at:
                        continue
                    text = _extract_text(m.get("content") if "content" in m else m.get("text"))
                    if text:
                        mid = str(m.get("id") or "")
                        key = f"{mid}|{ts}|{text}"
                        if key in seen:
                            continue
                        seen.add(key)
                        collected.append({"text": text, "timestamp": ts})
            if collected:
                if first_reply_at is None:
                    first_reply_at = time.time()
                # 首条回复出现后再短暂等待，允许 agent 连续发多条。
                if time.time() - first_reply_at >= settle_seconds:
                    replies = [x.get("text") or "" for x in collected if (x.get("text") or "").strip()]
                    if replies:
                        return {
                            "ok": True,
                            "reply": replies[-1],
                            "replies": replies,
                            "session_key": sk,
                        }
            time.sleep(1.0)
        return {"ok": False, "error": "等待 OpenClaw 回复超时，请稍后重试。"}
    except Exception as e:
        msg = str(e)
        if "missing scope: operator.write" in msg:
            # 网关只读时自动降级到本机 CLI，保证页面内可继续对话。
            fallback = _openclaw_cli_chat(message)
            if fallback.get("ok"):
                fallback["bridge"] = "cli-fallback"
                return fallback
            msg = (
                "当前网关凭证缺少 operator.write，且 CLI 兜底失败。"
                "请设置 OPENCLAW_GATEWAY_DEVICE_TOKEN/OPENCLAW_GATEWAY_PASSWORD，或检查 openclaw CLI。"
            )
        return {"ok": False, "error": f"OpenClaw chat 失败：{msg}"}
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def _openclaw_chat_history(session_key: str | None = None, limit: int = 60) -> dict:
    """读取 OpenClaw 会话历史，供右侧聊天卡片初始化显示。"""
    try:
        from websocket import create_connection  # type: ignore
    except Exception:
        return {"ok": False, "error": "缺少 websocket-client 依赖，无法连接 OpenClaw 网关。", "messages": []}
    cfg = _load_openclaw_gateway_settings()
    if not cfg.get("ok"):
        return {"ok": False, "error": cfg.get("error") or "未能读取网关配置。", "messages": []}
    sk = (session_key or cfg["session_key"]).strip() or cfg["session_key"]
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 60
    lim = max(1, min(200, lim))
    ws = None
    try:
        ws = create_connection(cfg["ws_url"], timeout=10)

        def request(method: str, params: dict) -> dict:
            rid = str(uuid.uuid4())
            ws.send(json.dumps({"type": "req", "id": rid, "method": method, "params": params}, ensure_ascii=False))
            while True:
                raw = ws.recv()
                resp = json.loads(raw)
                if resp.get("type") == "event":
                    continue
                if resp.get("type") == "res" and resp.get("id") == rid:
                    if resp.get("ok"):
                        return resp.get("payload") or {}
                    err = resp.get("error") or {}
                    raise RuntimeError(err.get("message") or "gateway request failed")

        connect_payload = {
            "minProtocol": 3,
            "maxProtocol": 3,
            "client": {
                "id": "webchat-ui",
                "version": "triage-workflow",
                "platform": "python",
                "mode": "webchat",
            },
            "role": "operator",
            "scopes": ["operator.admin", "operator.approvals", "operator.pairing"],
            "caps": ["tool-events"],
            "auth": {"token": cfg["token"]},
        }
        if cfg.get("password"):
            connect_payload["auth"]["password"] = cfg["password"]
        if cfg.get("device_token"):
            connect_payload["auth"]["deviceToken"] = cfg["device_token"]
        request("connect", connect_payload)
        hist = request("chat.history", {"sessionKey": sk, "limit": lim})
        msgs = hist.get("messages") if isinstance(hist, dict) else []
        out = []
        if isinstance(msgs, list):
            for m in msgs:
                if not isinstance(m, dict):
                    continue
                role = (m.get("role") or "").lower()
                if role not in ("user", "assistant"):
                    continue
                text = _extract_text(m.get("content") if "content" in m else m.get("text"))
                if not text:
                    continue
                out.append(
                    {
                        "role": role,
                        "text": text,
                        "timestamp": m.get("timestamp"),
                    }
                )
        return {"ok": True, "messages": out, "session_key": sk}
    except Exception as e:
        msg = str(e)
        if "missing scope: operator.read" in msg:
            # 某些本地网关凭证仅允许写不允许读，回退到本地 session 日志读取。
            fallback = _openclaw_local_history(limit=lim)
            if fallback.get("ok"):
                fallback["session_key"] = sk
                fallback["bridge"] = "local-session-fallback"
                return fallback
        return {"ok": False, "error": f"OpenClaw history 失败：{e}", "messages": [], "session_key": sk}
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def _openclaw_local_history(limit: int = 60) -> dict:
    """从本机 OpenClaw session 日志回溯 user/assistant 历史消息。"""
    base = Path.home() / ".openclaw" / "agents" / "main" / "sessions"
    if not base.is_dir():
        return {"ok": False, "error": "本地 session 目录不存在。", "messages": []}
    files = sorted(base.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {"ok": False, "error": "未找到本地 session 日志。", "messages": []}
    target = files[0]
    rows: list[dict] = []
    try:
        for raw in target.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (obj.get("type") or "").lower() != "message":
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            role = (msg.get("role") or "").lower()
            if role not in ("user", "assistant"):
                continue
            text = _extract_text(msg.get("content"))
            if not text:
                continue
            rows.append({"role": role, "text": text, "timestamp": msg.get("timestamp")})
        if not rows:
            return {"ok": True, "messages": []}
        lim = max(1, min(int(limit), 200))
        return {"ok": True, "messages": rows[-lim:]}
    except OSError as e:
        return {"ok": False, "error": f"读取本地 session 失败：{e}", "messages": []}


EVAL_RESULT_ROOT = ROOT / "eval_result"
_WORKSPACE_DECL_COMMENT = (
    "当前迭代评测目录（eval_result/iteration-N）的绝对路径。"
    "由工作流 UI 保存配置或 GET /api/defaults 自动探测最新 iteration 时更新。"
)


def _fallback_workspace_path() -> Path:
    """无声明且无迭代目录时的默认路径（评测产物统一在 eval_result 下）。"""
    return (EVAL_RESULT_ROOT / "iteration-1").resolve()


def _iteration_dir_num(name: str) -> int | None:
    m = re.match(r"^iteration-(\d+)$", name)
    return int(m.group(1)) if m else None


def discover_latest_eval_iteration_dir() -> Path | None:
    """在 ``ROOT/eval_result`` 下查找 ``iteration-N`` 子目录，返回 N 最大者。"""
    if not EVAL_RESULT_ROOT.is_dir():
        return None
    best: tuple[int, Path] | None = None
    try:
        for child in EVAL_RESULT_ROOT.iterdir():
            if not child.is_dir():
                continue
            n = _iteration_dir_num(child.name)
            if n is None:
                continue
            if best is None or n > best[0]:
                best = (n, child)
    except OSError:
        return None
    return best[1] if best else None


def _read_workspace_decl() -> dict:
    if not WORKSPACE_DECL_PATH.is_file():
        return {}
    try:
        data = json.loads(WORKSPACE_DECL_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _resolve_eval_path_raw(raw: str) -> Path:
    s = (raw or "").strip()
    if not s:
        return _fallback_workspace_path()
    p = Path(s).expanduser()
    return p.resolve() if p.is_absolute() else (ROOT / p).resolve()


def _is_under_eval_result(p: Path) -> bool:
    try:
        p.resolve().relative_to(EVAL_RESULT_ROOT.resolve())
        return True
    except ValueError:
        return False


def write_eval_result_declaration(eval_abs: Path) -> None:
    """写入 ``eval_result_path``（绝对路径），不再写入顶层 ``workspace`` 键。"""
    eval_abs = eval_abs.resolve()
    payload = {
        "eval_result_path": str(eval_abs),
        "comment": _WORKSPACE_DECL_COMMENT,
    }
    WORKSPACE_DECL_PATH.parent.mkdir(parents=True, exist_ok=True)
    WORKSPACE_DECL_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def effective_eval_result_directory(*, persist: bool) -> Path:
    """当前应使用的评测根目录（含 eval-* 的 iteration 文件夹），绝对路径。

    - 扫描 ``eval_result/iteration-*``：若存在比声明路径更大 N 的目录，则选用最新迭代（自动升级）。
    - 声明路径在 ``eval_result`` 外时，不自动替换。
    - ``persist=True`` 时若选用路径与文件中规范化后的绝对路径不一致，则写回 ``workflow_workspace.json``。
    """
    data = _read_workspace_decl()
    raw = (data.get("eval_result_path") or data.get("workspace") or "").strip()
    declared: Path | None = _resolve_eval_path_raw(raw) if raw else None

    latest = discover_latest_eval_iteration_dir()

    chosen: Path
    if latest is not None:
        if declared is None or not declared.is_dir():
            chosen = latest
        elif _is_under_eval_result(declared):
            dn = _iteration_dir_num(declared.name) or 0
            ln = _iteration_dir_num(latest.name) or 0
            chosen = latest if ln > dn else declared
        else:
            chosen = declared
    else:
        chosen = declared if declared is not None else _fallback_workspace_path()

    chosen = chosen.resolve()

    if persist:
        stored_abs: Path | None = None
        if raw:
            stored_abs = _resolve_eval_path_raw(raw)
        if stored_abs is None or str(stored_abs.resolve()) != str(chosen):
            write_eval_result_declaration(chosen)

    return chosen


def _ensure_port_available(port: int) -> None:
    """启动前清理目标端口的监听进程，避免旧实例导致启动失败或请求挂死。"""
    current_pid = os.getpid()
    pids: list[int] = []
    try:
        # 仅匹配 LISTEN，避免误伤已建立连接的客户端进程。
        r = subprocess.run(
            ["lsof", "-nP", "-tiTCP:" + str(port), "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        r = None
    if r and r.returncode == 0:
        for raw in r.stdout.splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                pid = int(raw)
            except ValueError:
                continue
            if pid == current_pid:
                continue
            pids.append(pid)

    for pid in pids:
        try:
            os.kill(pid, 15)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    # 给 SIGTERM 一点退出时间；若仍占用则再发 SIGKILL。
    if pids:
        time.sleep(0.35)
        for pid in pids:
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, OSError):
                continue
            try:
                os.kill(pid, 9)
            except (ProcessLookupError, PermissionError, OSError):
                pass
    try:
        PID_FILE.write_text(
            json.dumps({"pid": current_pid, "port": port}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _openclaw_cli_chat(message: str) -> dict:
    """兜底：通过本机 openclaw agent 命令执行一轮对话。"""
    cmd = [
        "openclaw",
        "agent",
        "--agent",
        "main",
        "--message",
        message,
        "--json",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "CLI 兜底超时。"}
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "openclaw agent failed").strip()
        return {"ok": False, "error": f"CLI 兜底失败：{err}"}
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "CLI 返回不是合法 JSON。"}
    payloads = (((data.get("result") or {}).get("payloads")) or [])
    parts: list[str] = []
    if isinstance(payloads, list):
        for p in payloads:
            if isinstance(p, dict):
                txt = p.get("text")
                if isinstance(txt, str) and txt.strip():
                    parts.append(txt.strip())
    reply = "\n".join(parts).strip()
    if not reply:
        return {"ok": False, "error": "CLI 未返回可显示文本。"}
    return {"ok": True, "reply": reply, "session_key": "agent:main:main"}


def declared_workspace_path() -> Path:
    """评测目录（只读解析，不写回 JSON）。HTTP 与页面默认值请用 ``effective_eval_result_directory(persist=True)``。"""
    return effective_eval_result_directory(persist=False)


def _find_eval_dirs(workspace: Path) -> list[Path]:
    if not workspace.exists() or not workspace.is_dir():
        return []
    out: list[Path] = []
    for child in sorted(workspace.iterdir()):
        if child.is_dir() and child.name.startswith("eval-"):
            out.append(child)
    return out


def _find_run_dir(config_dir: Path) -> Optional[Path]:
    for child in sorted(config_dir.iterdir()):
        if child.is_dir() and child.name.startswith("run-"):
            return child
    if (config_dir / "outputs").is_dir():
        return config_dir
    return None


def _load_eval(eval_dir: Path) -> dict:
    result: dict = {"id": eval_dir.name, "name": eval_dir.name, "prompt": ""}
    meta_path = eval_dir / "eval_metadata.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            result["eval_id"] = meta.get("eval_id")
            result["name"] = meta.get("eval_name", eval_dir.name)
            result["prompt"] = meta.get("prompt", "")
        except (json.JSONDecodeError, OSError):
            pass
    for config in ("with_skill", "without_skill"):
        config_dir = eval_dir / config
        if not config_dir.is_dir():
            continue
        run_dir = _find_run_dir(config_dir)
        if not run_dir:
            continue
        entry: dict = {}
        for fname in ("response.md", "response.txt"):
            p = run_dir / "outputs" / fname
            if p.exists():
                try:
                    entry["response"] = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    entry["response"] = ""
                break
        for gp in (run_dir / "grading.json", config_dir / "grading.json"):
            if gp.exists():
                try:
                    entry["grading"] = json.loads(gp.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass
                break
        if entry:
            result[config] = entry
    dlg_path = eval_dir / "dialogue.json"
    if dlg_path.exists():
        try:
            result["dialogue"] = json.loads(dlg_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    tg_path = eval_dir / "grading.json"
    if tg_path.exists():
        try:
            tg = json.loads(tg_path.read_text(encoding="utf-8"))
            if "info_score" in tg or "overall_score" in tg:
                result["triage_grading"] = tg
        except (json.JSONDecodeError, OSError):
            pass
    return result


def _load_benchmark(workspace: Path):
    p = workspace / "benchmark.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _load_feedback(workspace: Path) -> dict:
    p = workspace / "feedback.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {r["run_id"]: r["feedback"] for r in data.get("reviews", []) if r.get("feedback", "").strip()}
    except (json.JSONDecodeError, OSError, KeyError):
        return {}


def build_review_api_payload(workspace: Path, skill_name=None) -> dict:
    eval_dirs = _find_eval_dirs(workspace)
    evals = [_load_eval(d) for d in eval_dirs]
    benchmark = _load_benchmark(workspace)
    feedback = _load_feedback(workspace)
    sn = skill_name or workspace.parent.name.replace("-workspace", "")
    iteration = workspace.name
    has_stage2 = any((e.get("dialogue") or e.get("triage_grading")) for e in evals)
    return {
        "ok": True,
        "skill_name": sn,
        "evals": evals,
        "benchmark": benchmark,
        "feedback": feedback,
        "iteration": iteration,
        "workspace": str(workspace),
        "eval_count": len(evals),
        "has_stage2": has_stage2,
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            html = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
            self._send(200, html.encode("utf-8"), "text/html")
            return

        if path == "/api/departments":
            data = json.loads(DEPT_PATH.read_text(encoding="utf-8"))
            self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/defaults":
            p = effective_eval_result_directory(persist=True)
            self._send(
                200,
                json.dumps(
                    {"eval_result_path": str(p), "workspace": str(p)},
                    ensure_ascii=False,
                ).encode("utf-8"),
            )
            return

        if path == "/api/runtime":
            rt = detect_runtime()
            rt["chat_bridge"] = "available" if _load_openclaw_gateway_settings().get("ok") else "unavailable"
            self._send(200, json.dumps(rt, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/chat-history":
            qs = parse_qs(parsed.query)
            sk = (qs.get("sessionKey") or [""])[0].strip() or None
            lim_raw = (qs.get("limit") or ["60"])[0].strip()
            try:
                lim = int(lim_raw)
            except ValueError:
                lim = 60
            out = _openclaw_chat_history(sk, lim)
            code = 200 if out.get("ok") else 500
            self._send(code, json.dumps(out, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/rubric":
            if RUBRIC_PATH.exists():
                text = RUBRIC_PATH.read_text(encoding="utf-8")
            else:
                text = ""
            if text.strip():
                structured = parse_rubric_file(text)
            else:
                structured = empty_structured()
            self._send(
                200,
                json.dumps({"structured": structured}, ensure_ascii=False).encode("utf-8"),
            )
            return

        if path == "/api/workspace-skill":
            qs = parse_qs(parsed.query)
            ws = (qs.get("workspace") or [""])[0].strip()
            if not ws:
                self._send(
                    400,
                    json.dumps({"ok": False, "error": "需要工作区路径"}, ensure_ascii=False).encode("utf-8"),
                )
                return
            try:
                wpath = Path(ws).expanduser().resolve()
            except (OSError, RuntimeError):
                self._send(
                    400,
                    json.dumps({"ok": False, "error": "路径无效"}, ensure_ascii=False).encode("utf-8"),
                )
                return
            workspace_exists = wpath.is_dir()
            found, not_found_hint = find_skill_md(wpath, ROOT)
            if found is not None:
                try:
                    text = found.read_text(encoding="utf-8")
                except OSError as e:
                    self._send(
                        500,
                        json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False).encode("utf-8"),
                    )
                    return
                self._send(
                    200,
                    json.dumps(
                        {
                            "ok": True,
                            "content": text,
                            "skillFolder": found.parent.name,
                            "workspaceExists": workspace_exists,
                        },
                        ensure_ascii=False,
                    ).encode("utf-8"),
                )
                return
            self._send(
                200,
                json.dumps(
                    {
                        "ok": True,
                        "content": "",
                        "hint": not_found_hint,
                    },
                    ensure_ascii=False,
                ).encode("utf-8"),
            )
            return

        if path == "/api/phase2-status":
            qs = parse_qs(parsed.query)
            ws = (qs.get("workspace") or [""])[0].strip()
            if not ws:
                self._send(
                    400,
                    json.dumps({"ok": False, "error": "需要工作区路径"}, ensure_ascii=False).encode("utf-8"),
                )
                return
            try:
                wpath = Path(ws).expanduser().resolve()
            except (OSError, RuntimeError):
                self._send(
                    400,
                    json.dumps({"ok": False, "error": "路径无效"}, ensure_ascii=False).encode("utf-8"),
                )
                return
            fp = wpath / "workflow_phase2.json"
            if fp.is_file():
                try:
                    data = json.loads(fp.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    data = {"prepared": False}
            else:
                data = {"prepared": False}
            data["has_eval_cases"] = (wpath / "eval_cases.json").is_file()
            self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/review-data":
            qs = parse_qs(parsed.query)
            ws = (qs.get("workspace") or [""])[0].strip()
            if not ws:
                self._send(
                    400,
                    json.dumps({"ok": False, "error": "需要工作区路径"}, ensure_ascii=False).encode("utf-8"),
                )
                return
            try:
                wpath = Path(ws).expanduser().resolve()
            except (OSError, RuntimeError):
                self._send(
                    400,
                    json.dumps({"ok": False, "error": "路径无效"}, ensure_ascii=False).encode("utf-8"),
                )
                return
            payload = build_review_api_payload(wpath)
            self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            return

        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        ln = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(ln) if ln else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send(400, json.dumps({"ok": False, "error": "invalid json"}).encode())
            return

        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/rubric":
            structured = payload.get("structured")
            content = payload.get("content")
            if structured is not None:
                if not isinstance(structured, dict):
                    self._send(400, json.dumps({"ok": False, "error": "structured must be object"}).encode())
                    return
                text = serialize_rubric_file(structured)
            elif isinstance(content, str):
                text = content
            else:
                self._send(
                    400,
                    json.dumps({"ok": False, "error": "need structured or content"}, ensure_ascii=False).encode(),
                )
                return
            RUBRIC_PATH.parent.mkdir(parents=True, exist_ok=True)
            RUBRIC_PATH.write_text(text, encoding="utf-8")
            self._send(200, json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/config":
            ws = payload.get("workspace", "").strip()
            if not ws:
                self._send(400, json.dumps({"ok": False, "error": "workspace required"}).encode())
                return
            wpath = Path(ws).expanduser().resolve()
            depts = payload.get("departments")
            if not isinstance(depts, list):
                self._send(400, json.dumps({"ok": False, "error": "departments array"}).encode())
                return
            n = int(payload.get("n", 5))
            seed = payload.get("seed")
            wpath.mkdir(parents=True, exist_ok=True)
            cfg = {
                "workspace": str(wpath),
                "departments": depts,
                "sample_n": n,
                "seed": seed,
                "stage1_done": bool(payload.get("stage1_done")),
            }
            (wpath / "workflow_config.json").write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            sel = wpath / "selected_departments.json"
            sel.write_text(json.dumps(depts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            write_eval_result_declaration(wpath)
            self._send(200, json.dumps({"ok": True, "config": str(wpath / "workflow_config.json")}).encode())
            return

        if path == "/api/sample":
            ws = payload.get("workspace", "").strip()
            if not ws:
                self._send(400, json.dumps({"ok": False, "error": "workspace"}).encode())
                return
            wpath = Path(ws).expanduser().resolve()
            sel = wpath / "selected_departments.json"
            if not sel.exists():
                self._send(400, json.dumps({"ok": False, "error": "先保存科室配置"}).encode())
                return
            n = int(payload.get("n", 5))
            seed = payload.get("seed")
            out = wpath / "eval_cases.json"
            dp = DATA_DEFAULT
            if not dp.exists():
                self._send(400, json.dumps({"ok": False, "error": f"缺少数据文件 {dp}"}).encode())
                return
            cmd = [
                sys.executable,
                str(SAMPLE_SCRIPT),
                "--data-path",
                str(dp),
                "-n",
                str(n),
                "--departments-json",
                str(sel),
                "--output",
                str(out),
            ]
            if seed is not None:
                cmd.extend(["--seed", str(seed)])
            try:
                r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=600)
            except subprocess.TimeoutExpired:
                self._send(500, json.dumps({"ok": False, "error": "timeout"}).encode())
                return
            if r.returncode != 0:
                self._send(
                    500,
                    json.dumps(
                        {"ok": False, "error": r.stderr or r.stdout or "sample failed"},
                        ensure_ascii=False,
                    ).encode(),
                )
                return
            self._send(
                200,
                json.dumps({"ok": True, "output": str(out), "log": r.stderr}, ensure_ascii=False).encode(),
            )
            return

        if path == "/api/phase2-prepare":
            ws = payload.get("workspace", "").strip()
            if not ws:
                self._send(400, json.dumps({"ok": False, "error": "workspace required"}, ensure_ascii=False).encode())
                return
            try:
                wpath = Path(ws).expanduser().resolve()
            except (OSError, RuntimeError):
                self._send(400, json.dumps({"ok": False, "error": "路径无效"}, ensure_ascii=False).encode())
                return
            result = prepare_phase2_workspace(wpath, ROOT)
            code = 200 if result.get("ok") else 400
            self._send(code, json.dumps(result, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/phase2-start":
            ws = payload.get("workspace", "").strip()
            if not ws:
                self._send(400, json.dumps({"ok": False, "error": "workspace required"}, ensure_ascii=False).encode())
                return
            try:
                wpath = Path(ws).expanduser().resolve()
            except (OSError, RuntimeError):
                self._send(400, json.dumps({"ok": False, "error": "路径无效"}, ensure_ascii=False).encode())
                return
            fp = wpath / "workflow_phase2.json"
            if not fp.is_file():
                self._send(
                    400,
                    json.dumps({"ok": False, "error": "请先点击「准备第二阶段评测」"}, ensure_ascii=False).encode(),
                )
                return
            try:
                st = json.loads(fp.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._send(500, json.dumps({"ok": False, "error": "状态文件损坏"}, ensure_ascii=False).encode())
                return
            st["started"] = True
            st["started_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            fp.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self._send(200, json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/feedback":
            ws = payload.get("workspace", "").strip()
            text = payload.get("text", "")
            if not ws:
                self._send(400, json.dumps({"ok": False}).encode())
                return
            wpath = Path(ws).expanduser().resolve()
            fb = {"feedback": text, "source": "workflow_ui"}
            (wpath / "workflow_feedback.json").write_text(
                json.dumps(fb, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self._send(200, json.dumps({"ok": True}).encode())
            return

        if path == "/api/triage-feedback":
            parsed_q = urlparse(self.path)
            qs_fb = parse_qs(parsed_q.query)
            ws = (qs_fb.get("workspace") or [""])[0].strip()
            if not ws:
                self._send(400, json.dumps({"ok": False, "error": "workspace required"}).encode())
                return
            try:
                wpath = Path(ws).expanduser().resolve()
            except (OSError, RuntimeError):
                self._send(400, json.dumps({"ok": False, "error": "路径无效"}).encode())
                return
            feedback_path = wpath / "feedback.json"
            try:
                feedback_path.parent.mkdir(parents=True, exist_ok=True)
                feedback_path.write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                self._send(200, json.dumps({"ok": True}, ensure_ascii=False).encode())
            except (OSError, TypeError, ValueError) as e:
                self._send(
                    500,
                    json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False).encode(),
                )
            return

        if path == "/api/chat":
            msg = str(payload.get("message") or "").strip()
            sk = str(payload.get("sessionKey") or "").strip() or None
            out = _openclaw_chat(msg, sk)
            code = 200 if out.get("ok") else 500
            self._send(code, json.dumps(out, ensure_ascii=False).encode("utf-8"))
            return

        self._send(404, b"{}")

    def log_message(self, format: str, *args) -> None:
        pass


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="导诊评测工作流控制台（端口默认 3120，评测结果内嵌展示）")
    ap.add_argument("--port", "-p", type=int, default=3120, help="HTTP 端口（默认 3120）")
    ap.add_argument(
        "--no-browser",
        action="store_true",
        help="不自动打开浏览器，仅打印 URL",
    )
    args = ap.parse_args()
    port = args.port
    _ensure_port_available(port)
    httpd = HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    dw = effective_eval_result_directory(persist=True)
    sep = "─" * 35
    print(f"\n  Triage Workflow UI")
    print(f"  {sep}")
    print(f"  URL:              {url}")
    print(f"  Default workspace: {dw}")
    print(f"  Rubric file:      {RUBRIC_PATH}")
    print(f"  评测结果:         统一在本页第 3、5 步展示（/api/review-data）")
    print(f"\n  Press Ctrl+C to stop.\n")
    if not args.no_browser:
        if not open_browser(url):
            print("  未能自动打开浏览器，请手动复制上方 URL 到浏览器访问。\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        httpd.server_close()


if __name__ == "__main__":
    main()
