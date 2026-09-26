"""Per-pixel statistics: alpha distribution, transparent-edge colour evidence,
content bounding box, and the JSON report every command is built on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .common import fail, format_rgb  # 先导入：common 负责依赖检查
import numpy as np
from .sources import iter_source_frames
from .timing import plan_timing

def alpha_counts(rgba: Image.Image) -> tuple[int, int, int]:
    """Return (transparent, opaque, semi-transparent) pixel counts."""
    alpha = np.asarray(rgba)[:, :, 3]
    transparent = int(np.count_nonzero(alpha == 0))
    opaque = int(np.count_nonzero(alpha == 255))
    return transparent, opaque, alpha.size - transparent - opaque


def edge_colour_counts(rgba: Image.Image) -> dict[tuple[int, int, int], int]:
    """Count exact RGB values of transparent pixels touching visible pixels.

    Strict by design: the sampled pixel must itself be alpha==0 and adjacent
    (8-neighbourhood) to alpha>0. Counts are returned so the caller can report
    a deterministic top-k instead of an arbitrary subset of a set.
    """
    arr = np.asarray(rgba)
    alpha = arr[:, :, 3]
    visible = alpha > 0
    touching = np.zeros_like(visible)
    touching[1:, :] |= visible[:-1, :]
    touching[:-1, :] |= visible[1:, :]
    touching[:, 1:] |= visible[:, :-1]
    touching[:, :-1] |= visible[:, 1:]
    touching[1:, 1:] |= visible[:-1, :-1]
    touching[:-1, :-1] |= visible[1:, 1:]
    touching[1:, :-1] |= visible[:-1, 1:]
    touching[:-1, 1:] |= visible[1:, :-1]
    samples = arr[(alpha == 0) & touching, :3]
    if samples.size == 0:
        return {}
    unique, counts = np.unique(samples, axis=0, return_counts=True)
    return {tuple(int(v) for v in row): int(n) for row, n in zip(unique, counts)}


def gif_palette_colours(path: Path) -> dict[str, tuple[int, int, int] | None]:
    """Authored colour hints that live in the GIF header, not in the pixels.

    * transparent index -> the RGB the author parked under transparent pixels.
    * screen background index -> the GIF's declared canvas colour. Most
      encoders write 0 without meaning it, so it is reported as weak evidence
      and never used on its own.
    """
    result: dict[str, tuple[int, int, int] | None] = {"transparent": None, "screen_background": None}
    if not path.is_file() or path.suffix.lower() != ".gif":
        return result
    try:
        with Image.open(path) as image:
            palette = image.getpalette()
            if not palette:
                return result
            for key, info_key in (("transparent", "transparency"), ("screen_background", "background")):
                index = image.info.get(info_key)
                if isinstance(index, int) and (index + 1) * 3 <= len(palette):
                    result[key] = tuple(int(v) for v in palette[index * 3 : index * 3 + 3])  # type: ignore[assignment]
    except Exception:
        pass
    return result


def _top(counts: dict[tuple[int, int, int], int], limit: int) -> list[tuple[int, int, int]]:
    return [rgb for rgb, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def visible_edge_colour_counts(rgba: Image.Image) -> dict[tuple[int, int, int], int]:
    """Count RGB of *visible* pixels that touch the transparent region.

    This is the artwork's own rim, not the background. It is useless as a
    backdrop guess but it is exactly the colour that should bleed outwards to
    stop lossy alpha codecs from dragging black into the edge.
    """
    arr = np.asarray(rgba)
    alpha = arr[:, :, 3]
    visible = alpha > 0
    transparent = ~visible
    touching = np.zeros_like(visible)
    touching[1:, :] |= transparent[:-1, :]
    touching[:-1, :] |= transparent[1:, :]
    touching[:, 1:] |= transparent[:, :-1]
    touching[:, :-1] |= transparent[:, 1:]
    samples = arr[visible & touching, :3]
    if samples.size == 0:
        return {}
    unique, counts = np.unique(samples, axis=0, return_counts=True)
    return {tuple(int(v) for v in row): int(n) for row, n in zip(unique, counts)}


@dataclass
class EdgeColourFinding:
    """What the transparent-edge scan concluded, and how much to trust it."""

    scanned: bool = False
    frames_with_samples: int = 0
    counts: dict[tuple[int, int, int], int] = field(default_factory=dict)
    visible_counts: dict[tuple[int, int, int], int] = field(default_factory=dict)
    palette_rgb: tuple[int, int, int] | None = None
    screen_background_rgb: tuple[int, int, int] | None = None

    @property
    def single_colour(self) -> bool:
        return len(self.counts) == 1

    @property
    def colour(self) -> tuple[int, int, int] | None:
        return next(iter(self.counts)) if self.single_colour else None

    @property
    def suspicious_reason(self) -> str | None:
        """Why a *successful* single-colour detection may still be wrong.

        Most encoders zero the RGB of fully transparent pixels, so a unanimous
        #000000 edge is far more likely to be a decoder artefact than the
        author's intended backdrop. Reporting that as a confident background
        would violate the skill's own rule against silently choosing black.
        """
        colour = self.colour
        if colour is None:
            return None
        if colour != (0, 0, 0):
            return None
        if self.palette_rgb is not None and self.palette_rgb != (0, 0, 0):
            return (
                "透明边缘 RGB 全为 #000000，但 GIF 调色板中透明索引的颜色是 "
                f"{format_rgb(self.palette_rgb)}；采样到的黑色几乎肯定是解码产物。"
            )
        return (
            "透明边缘 RGB 全为 #000000。多数编码器会把全透明像素的 RGB 清零，"
            "因此这个黑色很可能是解码产物，而不是素材作者的背景色。"
        )

    def top_colours(self, limit: int = 4) -> list[tuple[int, int, int]]:
        return _top(self.counts, limit)

    def visible_rim_colour(self) -> tuple[int, int, int] | None:
        top = _top(self.visible_counts, 1)
        return top[0] if top else None

    def candidates(self) -> list[dict]:
        """Every independent piece of evidence about the intended backdrop.

        Listed with an explicit trust level instead of being silently merged:
        merging incompatible evidence is how a guess becomes a wrong fact.
        """
        items: list[dict] = []
        if self.colour is not None:
            items.append({
                "source": "transparent_pixel_rgb",
                "colour": format_rgb(self.colour),
                "trust": "low" if self.suspicious_reason else "high",
                "note": self.suspicious_reason or "全素材透明像素边缘 RGB 严格一致。",
            })
        elif self.counts:
            items.append({
                "source": "transparent_pixel_rgb",
                "colour": None,
                "trust": "none",
                "note": f"透明边缘存在 {len(self.counts)} 种颜色，无法作为背景色。",
            })
        if self.palette_rgb is not None:
            items.append({
                "source": "gif_palette_transparent_index",
                "colour": format_rgb(self.palette_rgb),
                "trust": "medium",
                "note": "GIF 调色板中透明索引的颜色，属于作者写入的数据。",
            })
        if self.screen_background_rgb is not None:
            items.append({
                "source": "gif_screen_background_index",
                "colour": format_rgb(self.screen_background_rgb),
                "trust": "low",
                "note": "GIF 逻辑屏幕背景索引；多数编码器固定写 0，不能单独采信。",
            })
        rim = self.visible_rim_colour()
        if rim is not None:
            items.append({
                "source": "visible_rim_dominant",
                "colour": format_rgb(rim),
                "trust": "none",
                "note": "可见主体紧贴透明区的主色。它是画面自己的边缘，不是背景；仅用于边缘外扩。",
            })
        return items

    def to_json(self) -> dict:
        if not self.scanned:
            return {"scanned": False}
        return {
            "scanned": True,
            "frames_with_edge_samples": self.frames_with_samples,
            "single_colour": self.single_colour,
            "colour": format_rgb(self.colour) if self.colour else None,
            "distinct_colours": len(self.counts),
            "top_colours": [format_rgb(rgb) for rgb in self.top_colours()],
            "gif_palette_transparent_colour": format_rgb(self.palette_rgb) if self.palette_rgb else None,
            "visible_rim_dominant": format_rgb(self.visible_rim_colour()) if self.visible_rim_colour() else None,
            "suspicious_reason": self.suspicious_reason,
            "background_candidates": self.candidates(),
        }


@dataclass
class SourceStats:
    """Streaming accumulator so convert can analyse and stage in one pass."""

    path: Path
    scan_edges: bool = True
    frames: int = 0
    width: int | None = None
    height: int | None = None
    transparent: int = 0
    opaque: int = 0
    semi: int = 0
    durations: list[float] = field(default_factory=list)
    edge: EdgeColourFinding = field(default_factory=EdgeColourFinding)
    bbox: tuple[int, int, int, int] | None = None  # union of visible pixels

    def add(self, rgba: Image.Image, duration: float) -> None:
        self.frames += 1
        if self.width is None:
            self.width, self.height = rgba.size
        elif rgba.size != (self.width, self.height):
            fail(f"第 {self.frames} 帧尺寸 {rgba.size} 与首帧 {(self.width, self.height)} 不一致。")
        transparent, opaque, semi = alpha_counts(rgba)
        self.transparent += transparent
        self.opaque += opaque
        self.semi += semi
        self.durations.append(duration)
        frame_bbox = rgba.getbbox(alpha_only=True)
        if frame_bbox:
            self.bbox = frame_bbox if self.bbox is None else (
                min(self.bbox[0], frame_bbox[0]), min(self.bbox[1], frame_bbox[1]),
                max(self.bbox[2], frame_bbox[2]), max(self.bbox[3], frame_bbox[3]),
            )
        if self.scan_edges:
            self.edge.scanned = True
            counts = edge_colour_counts(rgba)
            if counts:
                self.edge.frames_with_samples += 1
                for rgb, n in counts.items():
                    self.edge.counts[rgb] = self.edge.counts.get(rgb, 0) + n
            for rgb, n in visible_edge_colour_counts(rgba).items():
                self.edge.visible_counts[rgb] = self.edge.visible_counts.get(rgb, 0) + n

    def report(self) -> dict:
        if not self.frames or self.width is None or self.height is None:
            fail(f"输入没有可读帧: {self.path}")
        if self.scan_edges and self.edge.palette_rgb is None:
            palette = gif_palette_colours(self.path)
            self.edge.palette_rgb = palette["transparent"]
            self.edge.screen_background_rgb = palette["screen_background"]
        total = self.transparent + self.opaque + self.semi
        duration = sum(self.durations)
        first = self.durations[0]
        variable = any(abs(d - first) > 1e-9 for d in self.durations[1:])
        return {
            "path": str(self.path),
            "format": self.path.suffix.lower().lstrip(".") if self.path.is_file() else "image-sequence",
            "width": self.width,
            "height": self.height,
            "frames": self.frames,
            "duration_seconds": round(duration, 6),
            "frame_duration_seconds": {
                "min": round(min(self.durations), 6),
                "max": round(max(self.durations), 6),
                "variable": variable,
            },
            "timing": {
                "source_mode": "vfr" if variable else "cfr",
                "average_fps": round(self.frames / duration, 6) if duration else None,
                "constant_fps": round(1 / first, 6) if not variable and first else None,
                # How this source would be laid out on a video timeline. verify
                # compares the output against this, not against a raw frame count.
                "plan": plan_timing(self.durations).to_json(),
            },
            "alpha": {
                "transparent_pct": round(self.transparent / total * 100, 4),
                "opaque_pct": round(self.opaque / total * 100, 4),
                "semi_transparent_pct": round(self.semi / total * 100, 4),
                "has_transparency": self.transparent > 0 or self.semi > 0,
                "binary": self.semi == 0,
            },
            # Union of every frame's visible area. When it is smaller than the
            # canvas, the transparent region is padding, and cropping is often
            # a better answer than picking a background colour.
            "content_bbox": (
                {
                    "x0": self.bbox[0], "y0": self.bbox[1], "x1": self.bbox[2], "y1": self.bbox[3],
                    "width": self.bbox[2] - self.bbox[0], "height": self.bbox[3] - self.bbox[1],
                    "covers_full_canvas": (
                        self.bbox[0] == 0 and self.bbox[1] == 0
                        and self.bbox[2] == self.width and self.bbox[3] == self.height
                    ),
                }
                if self.bbox
                else None
            ),
            "transparent_edge": self.edge.to_json(),
        }


def inspect_source(
    path: Path,
    sequence_duration_ms: float | None = None,
    scan_edges: bool = True,
) -> dict:
    """Inspect source frames without requiring ffmpeg."""
    if not path.is_file() and not path.is_dir():
        fail(f"输入文件或图片序列目录不存在: {path}")
    stats = SourceStats(path=path, scan_edges=scan_edges)
    for rgba, duration in iter_source_frames(path, sequence_duration_ms):
        stats.add(rgba, duration)
    return stats.report()


# --------------------------------------------------------------------------
