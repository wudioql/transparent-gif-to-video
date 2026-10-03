"""GIF 读取层（Phase 2）：把旧命令的 GIF demux/decode 语义封装成显式 API。

与旧命令的语义映射：

    旧 ffmpeg 语义                          →  本模块
    -------------------------------------------------------------------------
    -ignore_loop 1（显式，不依赖默认值）      →  av.open(..., options={"ignore_loop": "1"})
    -fps_mode passthrough                   →  逐帧原始 pts 原样暴露，由 writer 透传
    -enc_time_base demux                    →  time_base 沿用 demuxer 的 1/100
    disposal 1/2/3 合成、局部帧偏移、        →  libavcodec gif 解码器原生完成
      透明索引 → alpha                          （与 CLI 同源；2026-10-03 实测与
                                               ffmpeg CLI 回归数值逐项一致）

设计要点：

- ``GifSource.info`` 的一次只读扫描覆盖**全部帧**的 alpha 统计——「首帧不透明、
  后续透明」的素材不会再被首帧误判（旧 skill 用 signalstats 全帧扫描 +
  "不能加 -v error" 来防这个坑，这里由循环结构直接消灭）。
- ``iter_frames()`` 每次调用独立打开输入（av 解码只前进）：info 扫描与
  编码迭代互不干扰，不依赖 seek。
- 输入校验：必须真实为 GIF（按内容探测，不看扩展名）；垃圾字节/非 GIF
  均抛 :class:`GifSourceError`，由调用方停止并报告。
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Iterator

import av
import numpy as np


class GifSourceError(Exception):
    """GIF 读取失败（文件不存在 / 不是 GIF / 无视频帧 / 解码错误）。"""


@dataclass
class GifFrame:
    """一帧解码结果。

    Attributes:
        index: 帧序号（0 起）。
        pts: demuxer 时基（GIF 为 1/100）下的**原始** pts；writer 必须原样透传
            （等价旧命令的 ``-fps_mode passthrough``），不得重采样。
        time_base: 该帧时基（Fraction，GIF 恒 1/100）。
        frame: 解码原始帧（GIF 解码恒为 ``bgra``）；writer 按目标像素格式 reformat。
    """

    index: int
    pts: int
    time_base: Fraction
    frame: av.VideoFrame
    _rgba: np.ndarray | None = None

    @property
    def pts_seconds(self) -> float:
        return float(self.pts * self.time_base)

    @property
    def rgba(self) -> np.ndarray:
        """HxWx4 uint8（懒转换、缓存）；统计/验证用，编码路径用 ``frame`` 直接 reformat。"""
        if self._rgba is None:
            self._rgba = self.frame.to_ndarray(format="rgba")
        return self._rgba


@dataclass(frozen=True)
class GifInfo:
    """一次只读全帧扫描的结果（计划展示 + 预检数据源）。"""

    width: int
    height: int
    frame_count: int
    time_base: Fraction
    pts_list: tuple[int, ...]
    pts_seconds_list: tuple[float, ...]
    duration_seconds: float | None
    alpha_min: int
    has_transparent_pixels: bool
    decoded_pix_fmt: str


class GifSource:
    """单个透明 GIF 的读取入口。一次只处理一个 GIF（skill 硬边界）。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise GifSourceError(f"输入不存在：{self.path}")
        self._info: GifInfo | None = None

    # ------------------------------------------------------------------ 打开

    def _open(self) -> av.container.InputContainer:
        try:
            container = av.open(str(self.path), options={"ignore_loop": "1"})
        except Exception as e:  # noqa: BLE001 —— 统一转成可读错误
            raise GifSourceError(
                f"无法打开输入（可能不是有效媒体文件）：{self.path} —— {type(e).__name__}: {e}"
            ) from e
        if container.format.name != "gif":
            name = container.format.name
            container.close()
            raise GifSourceError(f"输入不是 GIF（按内容探测为 '{name}'）：{self.path}")
        return container

    # ------------------------------------------------------------------ 信息

    @property
    def info(self) -> GifInfo:
        """只读全帧扫描（首次访问时执行，之后缓存）。"""
        if self._info is None:
            self._scan()
        return self._info

    def _scan(self) -> None:
        container = self._open()
        try:
            pts_list: list[int] = []
            width = height = 0
            decoded_pix_fmt = ""
            alpha_min = 255
            time_base = Fraction(1, 100)
            for fr in container.decode(video=0):
                if width == 0:
                    width, height, decoded_pix_fmt = fr.width, fr.height, fr.format.name
                time_base = Fraction(fr.time_base)
                pts_list.append(int(fr.pts))
                alpha = fr.to_ndarray(format="rgba")[..., 3]
                alpha_min = min(alpha_min, int(alpha.min()))
            if not pts_list:
                raise GifSourceError(f"GIF 没有可解码的视频帧：{self.path}")
            duration = (
                container.duration / 1_000_000.0 if container.duration and container.duration > 0 else None
            )
        finally:
            container.close()
        self._info = GifInfo(
            width=width,
            height=height,
            frame_count=len(pts_list),
            time_base=time_base,
            pts_list=tuple(pts_list),
            pts_seconds_list=tuple(float(p * time_base) for p in pts_list),
            duration_seconds=duration,
            alpha_min=alpha_min,
            has_transparent_pixels=alpha_min < 255,
            decoded_pix_fmt=decoded_pix_fmt,
        )

    # ------------------------------------------------------------------ 帧

    def iter_frames(self) -> Iterator[GifFrame]:
        """流式逐帧解码。每次调用独立打开输入，可重复调用。

        返回的 ``GifFrame.pts`` 是 demuxer 时基下的原始值——writer 透传它，
        逐帧时长（含变帧时长）即得以保留，不会被平均成 CFR。
        """
        container = self._open()
        try:
            index = 0
            for fr in container.decode(video=0):
                yield GifFrame(index=index, pts=int(fr.pts), time_base=Fraction(fr.time_base), frame=fr)
                index += 1
        finally:
            container.close()

    # ------------------------------------------------------------------ 其他

    def __repr__(self) -> str:  # pragma: no cover —— 调试便利
        return f"GifSource({str(self.path)!r})"
