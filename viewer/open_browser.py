"""在默认浏览器中打开 URL。

macOS：依次尝试指定浏览器与系统默认；子进程捕获输出，避免 Launch Services
在受限环境下向终端刷屏。全部失败时返回 False，由调用方提示用户手动打开。
"""

from __future__ import annotations

import subprocess
import sys
from typing import Iterable


def _darwin_url_variants(url: str) -> list[str]:
    out = [url]
    if "127.0.0.1" in url:
        out.append(url.replace("127.0.0.1", "localhost", 1))
    return out


def _darwin_commands(url: str) -> Iterable[list[str]]:
    """先试显式 App，再试默认 open（部分环境默认 http 处理器损坏）。"""
    for u in _darwin_url_variants(url):
        yield ["open", "-a", "Safari", u]
        yield ["open", "-a", "Google Chrome", u]
        yield ["open", "-a", "Chromium", u]
        yield ["open", "-a", "Microsoft Edge", u]
        yield ["open", "-a", "Brave Browser", u]
        yield ["open", "-a", "Firefox", u]
        yield ["open", "-a", "Arc", u]
        yield ["open", u]


def open_browser(url: str) -> bool:
    """尝试打开浏览器。成功返回 True，失败返回 False（不抛异常）。"""
    if sys.platform == "darwin":
        for cmd in _darwin_commands(url):
            try:
                r = subprocess.run(
                    cmd,
                    check=False,
                    timeout=25,
                    capture_output=True,
                    text=True,
                )
                if r.returncode == 0:
                    return True
            except (OSError, subprocess.TimeoutExpired):
                continue
        return False

    if sys.platform == "win32":
        try:
            import os

            os.startfile(url)  # type: ignore[attr-defined]
            return True
        except OSError:
            return _webbrowser_open(url)

    return _webbrowser_open(url)


def _webbrowser_open(url: str) -> bool:
    import webbrowser

    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False
