"""Frame durations -> video timestamps.

The one place that knows why a 30 ms GIF used to become a 25 fps video.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Sequence

from .common import fail, warn

# The concat demuxer hardcodes a 1/25 time base for image lists, so every
# duration is rounded to a 40 ms grid. Inflating durations by this factor
# makes the rounding lossless for whole-millisecond timings, and a single
# constant-size setpts divides it back out.
CONCAT_TIMEBASE_DEN = 25
CONCAT_INFLATE = 1000 // CONCAT_TIMEBASE_DEN  # 40

@dataclass
class TimingPlan:
    """How a list of frame durations becomes video timestamps.

    Containers and encoders are much better at constant frame rate than at
    variable, so the plan prefers CFR and only falls back to VFR when CFR
    would explode the frame count:

    cfr - pick the millisecond grid g = gcd(durations) and emit each source
          frame ceil(d/g) times at an exact rational framerate 1000/g. A
          repeated frame costs almost nothing in any inter-frame codec, and
          every timestamp *and the total duration* are exact.
    vfr - pathological timings only. The concat demuxer quantises to its
          hardcoded 1/25 grid, so durations are inflated by 40 (lossless for
          whole milliseconds) and one constant-size `setpts=PTS/40` divides it
          back out. Known limitation: the final frame's duration is not
          carried into the container, so the reported duration is short by
          that frame.

    In both cases the encoder time base must be pinned to 1/1000 with
    `-enc_time_base`; otherwise ffmpeg re-quantises to 1/25 at the encoder and
    a 30 ms GIF silently becomes a 40 ms (25 fps) video.
    """

    mode: str
    durations_ms: list[int]
    framerate: Fraction | None = None
    repeats: list[int] = field(default_factory=list)

    @property
    def total_ms(self) -> int:
        return sum(self.durations_ms)

    @property
    def output_frames(self) -> int:
        return sum(self.repeats) if self.mode == "cfr" else len(self.durations_ms)

    @property
    def exact_duration(self) -> bool:
        return self.mode == "cfr"

    def to_json(self) -> dict:
        return {
            "mode": self.mode,
            "source_frames": len(self.durations_ms),
            "output_frames": self.output_frames,
            "framerate": f"{self.framerate.numerator}/{self.framerate.denominator}" if self.framerate else None,
            "total_ms": self.total_ms,
            "duration_is_exact": self.exact_duration,
        }


# Guards on the CFR expansion: a 1 ms grid at 1000 fps would be technically
# exact and practically useless, so fall back to VFR beyond these limits.
MAX_CFR_FPS = 200
MAX_CFR_FRAME_MULTIPLIER = 5
MAX_CFR_FRAMES = 3000


def plan_timing(durations: Sequence[float]) -> TimingPlan:
    if not durations:
        fail("没有帧时长，无法生成时间轴。")
    durations_ms = [max(1, round(d * 1000.0)) for d in durations]
    for original, rounded in zip(durations, durations_ms):
        if abs(original * 1000.0 - rounded) > 0.5:  # pragma: no cover - defensive
            warn(f"帧时长 {original * 1000.0:.3f}ms 被取整为 {rounded}ms（时间基为 1/1000）。")

    grid = durations_ms[0]
    for value in durations_ms[1:]:
        grid = math.gcd(grid, value)
    repeats = [value // grid for value in durations_ms]
    output_frames = sum(repeats)
    fits = (
        1000 / grid <= MAX_CFR_FPS
        and output_frames <= max(MAX_CFR_FRAME_MULTIPLIER * len(durations_ms), 120)
        and output_frames <= MAX_CFR_FRAMES
    )
    if fits:
        return TimingPlan("cfr", durations_ms, Fraction(1000, grid), repeats)
    warn(
        "帧时长无法在合理帧率下对齐到统一网格，改用 VFR（concat）路径；"
        "该路径无法把最后一帧的时长写入容器，输出时长会短一帧。"
    )
    return TimingPlan("vfr", durations_ms)
