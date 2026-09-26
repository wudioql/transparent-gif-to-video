"""Turning source frames into the PNG files ffmpeg will encode: optional edge
bleed, background compositing, CFR repetition, and the concat manifest.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Sequence

from PIL import Image

from typing import TYPE_CHECKING

from .common import fail, internal_error  # 先导入：common 负责依赖检查
import numpy as np
from .sources import check_path_safe_for_concat, iter_source_frames

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .analysis import SourceStats

def bleed_transparent_rgb(rgba: Image.Image, iterations: int) -> Image.Image:
    """Push visible colour outwards into transparent pixels, alpha untouched.

    Why this exists: with alpha kept, a lossy codec still encodes the RGB of
    fully transparent pixels. That RGB is almost always zeroed black, and
    yuva420p chroma subsampling plus quantisation smear it back across the
    boundary, producing a dark halo once the video is composited over a light
    page. Copying the nearest visible colour outwards makes that smear
    invisible because the colour on both sides of the edge now matches.

    It changes only pixels whose alpha is 0, so the composite result over any
    background is mathematically identical -- it only removes codec error.
    Not useful for opaque output (already composited) or lossless codecs.
    """
    if iterations <= 0:
        return rgba
    arr = np.asarray(rgba).astype(np.int16).copy()
    alpha = arr[:, :, 3]
    known = alpha > 0
    rgb = arr[:, :, :3]
    for _ in range(iterations):
        if known.all():
            break
        total = np.zeros(rgb.shape, dtype=np.int32)
        votes = np.zeros(alpha.shape, dtype=np.int32)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
            shifted_rgb = np.roll(np.roll(rgb, dy, axis=0), dx, axis=1)
            shifted_known = np.roll(np.roll(known, dy, axis=0), dx, axis=1)
            # np.roll wraps; blank the wrapped row/column so edges do not
            # inherit colour from the opposite side of the canvas.
            if dy:
                (shifted_known[0:1, :] if dy > 0 else shifted_known[-1:, :])[...] = False
            if dx:
                (shifted_known[:, 0:1] if dx > 0 else shifted_known[:, -1:])[...] = False
            total += shifted_rgb * shifted_known[:, :, None]
            votes += shifted_known
        fillable = (~known) & (votes > 0)
        if not fillable.any():
            break
        rgb[fillable] = (total[fillable] // votes[fillable][:, None]).astype(np.int16)
        known = known | fillable
    arr[:, :, :3] = rgb
    return Image.fromarray(arr.astype(np.uint8), mode="RGBA")


def default_bleed_iterations(keep_alpha: bool, codec: str, lossless: bool) -> int:
    """Off by default, because the measurement says so.

    Folklore says lossy alpha video needs colour bleed. Measured on a real
    1000x1000 binary-alpha GIF at VP9 crf 32 (mean absolute error in a 3px
    band outside the subject, against the exact composite):

        over white: 0.35 -> 0.23   (bleed helps)
        over black: 0.01 -> 0.13   (bleed hurts)
        file size:  +2.9%

    VP9 carries a full-resolution alpha plane, so a binary-alpha source has
    almost no halo to fix. Bleed is therefore opt-in via --bleed-edges, and
    worth it mainly for semi-transparent sources shown over a light backdrop.
    """
    return 0


def composite_on_background(rgba: Image.Image, background: tuple[int, int, int]) -> Image.Image:
    backdrop = Image.new("RGBA", rgba.size, background + (255,))
    return Image.alpha_composite(backdrop, rgba).convert("RGB")


def stage_frames(
    source: Path,
    directory: Path,
    keep_alpha: bool,
    background: tuple[int, int, int] | None,
    sequence_duration_ms: float | None = None,
    stats: "SourceStats | None" = None,
    bleed_iterations: int = 0,
) -> tuple[list[Path], list[float]]:
    """Write prepared PNG frames, optionally accumulating stats in the same pass."""
    if not keep_alpha and background is None:
        internal_error("不透明模式必须先解析出背景色。")
    frame_paths: list[Path] = []
    durations: list[float] = []
    for index, (rgba, duration) in enumerate(iter_source_frames(source, sequence_duration_ms)):
        if stats is not None:
            stats.add(rgba, duration)
        if keep_alpha:
            prepared = bleed_transparent_rgb(rgba, bleed_iterations)
        else:
            prepared = composite_on_background(rgba, background)  # type: ignore[arg-type]
        frame_path = directory / f"frame_{index:08d}.png"
        prepared.save(frame_path, format="PNG", optimize=False)
        frame_paths.append(frame_path)
        durations.append(duration)
    if not frame_paths:
        fail("没有可写出的帧。")
    return frame_paths, durations


def materialise_cfr_sequence(frame_paths: Sequence[Path], repeats: Sequence[int], directory: Path) -> Path:
    """Lay out a gap-free image2 sequence, repeating frames per the plan.

    Hard links keep a 5x expansion free on disk; a copy is the portable
    fallback (e.g. staging across filesystems).
    """
    sequence_dir = directory / "cfr"
    sequence_dir.mkdir(exist_ok=True)
    index = 0
    for frame_path, repeat in zip(frame_paths, repeats):
        for _ in range(repeat):
            target = sequence_dir / f"frame_{index:08d}.png"
            try:
                target.hardlink_to(frame_path)
            except OSError:  # pragma: no cover - filesystem dependent
                shutil.copyfile(frame_path, target)
            index += 1
    return sequence_dir


def concat_quote(path: Path) -> str:
    # ffconcat uses single-quoted paths; this is the documented escape form.
    return "'" + str(path).replace("'", "'\\''") + "'"


def write_concat_manifest(list_path: Path, frame_paths: Sequence[Path], durations_ms: Sequence[int]) -> Path:
    with list_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("ffconcat version 1.0\n")
        for frame_path, duration_ms in zip(frame_paths, durations_ms):
            check_path_safe_for_concat(frame_path)
            handle.write(f"file {concat_quote(frame_path)}\n")
            handle.write(f"duration {duration_ms / 1000.0:.6f}\n")
        # The concat demuxer only honours the final duration directive if the
        # last file is repeated; -frames:v trims the sentinel back off.
        handle.write(f"file {concat_quote(frame_paths[-1])}\n")
    return list_path


# --------------------------------------------------------------------------
