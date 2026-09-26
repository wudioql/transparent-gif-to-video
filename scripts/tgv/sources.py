"""Reading frames out of GIF/APNG/PNG files and image-sequence directories.

Knows nothing about video: it yields composited RGBA frames and their source
durations, and refuses to invent a duration it was not given.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Iterator

from PIL import Image

from .common import fail, warn

# GIF convention: a missing or zero delay is displayed as ~100 ms.
DEFAULT_GIF_DURATION_MS = 100.0
SEQUENCE_IMAGE_SUFFIXES = {".png", ".apng", ".webp", ".gif", ".tif", ".tiff", ".bmp"}
# Accepted, but they cannot carry alpha; warn so a mistake is visible.
SEQUENCE_OPAQUE_SUFFIXES = {".jpg", ".jpeg"}

def natural_sort_key(name: str) -> list:
    """Split digit runs so frame_2 sorts before frame_10.

    The regex must be r"(\\d+)". An earlier version used an extra backslash,
    which silently matched a literal "\\d" and degraded to lexicographic
    order, reversing image sequences without any error.
    """
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", name)]


def sequence_files(path: Path) -> list[Path]:
    if not path.is_dir():
        return [path]
    allowed = SEQUENCE_IMAGE_SUFFIXES | SEQUENCE_OPAQUE_SUFFIXES
    files = [child for child in path.iterdir() if child.is_file() and child.suffix.lower() in allowed]
    if not files:
        fail(f"图片序列目录为空，或没有支持的图片文件: {path}")
    files.sort(key=lambda item: natural_sort_key(item.name))
    opaque = [f.name for f in files if f.suffix.lower() in SEQUENCE_OPAQUE_SUFFIXES]
    if opaque:
        warn(
            f"图片序列包含无 alpha 通道的文件（{', '.join(opaque[:3])}{'…' if len(opaque) > 3 else ''}）；"
            "这些帧会被当作全不透明处理。"
        )
    return files


def check_path_safe_for_concat(path: Path) -> None:
    if "\n" in str(path) or "\r" in str(path):
        fail(f"路径包含换行符，无法写入 ffconcat 清单: {path!r}")


def iter_source_frames(
    path: Path,
    sequence_duration_ms: float | None = None,
) -> Iterator[tuple[Image.Image, float]]:
    """Yield fully composited RGBA frames and their durations in seconds.

    Pillow applies GIF disposal while seeking, so each frame is the composited
    canvas rather than a partial update; a copy is taken before the handle
    advances. A directory is a naturally sorted image sequence, and still
    images have no portable per-file timing, so the caller must supply one.
    """
    if path.is_dir():
        if sequence_duration_ms is None or not math.isfinite(sequence_duration_ms) or sequence_duration_ms <= 0:
            fail("图片序列没有内置帧时长，必须显式传入 --sequence-duration-ms，且数值必须大于 0。")
        for child in sequence_files(path):
            yield from _iter_file_frames(child, sequence_duration_ms, in_sequence=True)
        return
    yield from _iter_file_frames(path, sequence_duration_ms, in_sequence=False)


def _iter_file_frames(
    path: Path,
    sequence_duration_ms: float | None,
    in_sequence: bool,
) -> Iterator[tuple[Image.Image, float]]:
    try:
        image = Image.open(path)
    except Exception as exc:
        fail(f"无法打开输入文件 {path}: {exc}")

    try:
        count = int(getattr(image, "n_frames", 1))
        if in_sequence and count > 1:
            warn(
                f"图片序列中的 {path.name} 自带 {count} 帧动画，将使用它自己的帧时长，"
                "与 --sequence-duration-ms 混在同一条时间轴上。"
            )
        for index in range(count):
            try:
                image.seek(index)
                rgba = image.convert("RGBA").copy()
            except Exception as exc:
                fail(f"读取 {path} 第 {index + 1} 帧失败: {exc}")
            yield rgba, _frame_duration_seconds(image, path, count, sequence_duration_ms, in_sequence)
    finally:
        image.close()


def _frame_duration_seconds(
    image: Image.Image,
    path: Path,
    frame_count: int,
    sequence_duration_ms: float | None,
    in_sequence: bool,
) -> float:
    if frame_count == 1 and (in_sequence or "duration" not in image.info):
        if sequence_duration_ms is None:
            fail(
                f"{path} 是单帧素材，没有内置帧时长。请用 --sequence-duration-ms 显式指定每帧毫秒数；"
                "脚本不替你猜时长，正如它不替你猜背景色。"
            )
        return sequence_duration_ms / 1000.0

    raw = image.info.get("duration", DEFAULT_GIF_DURATION_MS)
    try:
        duration_ms = float(raw)
    except (TypeError, ValueError):
        duration_ms = DEFAULT_GIF_DURATION_MS
    if not math.isfinite(duration_ms) or duration_ms <= 0:
        # Legal in GIF, meaningless as a video timestamp; browsers show ~100 ms.
        duration_ms = DEFAULT_GIF_DURATION_MS
    return duration_ms / 1000.0


# --------------------------------------------------------------------------
