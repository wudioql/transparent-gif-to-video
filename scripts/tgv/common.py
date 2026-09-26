"""Errors, warnings, tool lookup and colour parsing — the vocabulary every
other module in this package speaks."""

from __future__ import annotations

import importlib
import shutil
import json
import sys
from pathlib import Path
from typing import NoReturn

from PIL import ImageColor

try:  # pragma: no cover - environment dependent
    # NumPy 是硬依赖。这里早退比让每个模块各自 ImportError 更早、更可读：
    # 曾经有一套纯 Pillow 回退，但两份实现等于两份缺陷面，且缺失时只会悄悄退化。
    importlib.import_module("numpy")
except ImportError:
    raise SystemExit(
        "缺少依赖 NumPy：本 skill 的逐像素分析与边缘外扩依赖它。\n"
        "  安装：python -m pip install numpy"
    ) from None


class SkillError(RuntimeError):
    """A user-facing, actionable error."""


def fail(message: str) -> NoReturn:
    raise SkillError(message)


def internal_error(message: str) -> NoReturn:
    """A broken invariant inside the script; users should never trigger it."""
    raise AssertionError(message)


_WARNED: set[str] = set()


def warn(message: str, once: bool = True) -> None:
    """Print a warning to stderr.

    Deduplicated by default: the auto-edge path decodes the source twice, and
    a warning repeated per pass reads like two different problems.
    """
    if once:
        if message in _WARNED:
            return
        _WARNED.add(message)
    print(f"警告: {message}", file=sys.stderr)


def reset_warnings() -> None:
    """For tests and for callers that run several conversions in one process."""
    _WARNED.clear()


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path and Path(name).is_file():
        path = name
    if not path:
        fail(
            f"找不到 {name}。convert 需要 ffmpeg，verify 需要 ffmpeg 和 ffprobe；"
            "inspect 只需要 Pillow。"
        )
    return path


def print_json(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def parse_rgb(value: str) -> tuple[int, int, int]:
    text = value.strip()
    if is_auto_edge(text):
        internal_error("parse_rgb 收到 auto-edge；调用方必须先经过 resolve_background。")
    candidate = text[1:] if text.startswith("#") else text
    if len(candidate) == 6:
        try:
            return tuple(int(candidate[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
        except ValueError:
            pass
    try:
        rgb = ImageColor.getrgb(text)
    except ValueError:
        rgb = None
    if rgb is not None and len(rgb) >= 3:
        return tuple(int(v) for v in rgb[:3])  # type: ignore[return-value]
    fail(f"无法解析背景色 {value!r}；请使用 #RRGGBB 或 CSS 颜色名。")


def format_rgb(rgb: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{v:02x}" for v in rgb)


def is_auto_edge(value: str) -> bool:
    return value.strip().lower() in {"auto", "auto-edge", "edge"}


# --------------------------------------------------------------------------
