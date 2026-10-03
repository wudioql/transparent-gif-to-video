"""黑底归一化（Phase 4）：NumPy 实现，等价旧命令的 premultiply 滤镜链。

旧命令（仅 VP8/VP9 链路验证过，见 SKILL.md §3.0）：

    -vf "format=rgba,premultiply=inplace=1:planes=0x7,setparams=alpha_mode=straight"

本实现的对应关系与有意差异：

- ``premultiply_rgb`` 等价 ``premultiply=inplace=1:planes=0x7``：
  RGB 三平面乘以 alpha（uint16 中间量，a=255 时逐像素精确还原，a=0 时归零），
  alpha 平面不动。二值 alpha 下与「合成到黑底」逐像素等价（SKILL.md §3.7 的
  语义）；非二值 alpha 为真预乘。
- **不实现** ``setparams=alpha_mode=straight``：它只写帧元数据，不影响像素；
  Matroska 的 ``AlphaMode=1`` 由复用器在写 WebM alpha 时自动声明。差异在
  回归中断言像素级等价。
- 黑色归一化**只改变忽略 alpha 的播放端所显示的那层 RGB**，不修复播放器的
  alpha 兼容性——口径与旧 skill 完全一致。
- 仅允许用于 VP8/VP9 链路（vp9 / vp9-lossless / vp8）；MOV 类路径维持
  「需另行验证，不得直接套用」的口径。mp4-black 路径的「合成到黑」也复用
  本函数（alpha 在 yuv420p 转换时丢弃）。
"""

from __future__ import annotations

import av
import numpy as np


def premultiply_rgb(rgba: np.ndarray) -> np.ndarray:
    """RGB 乘以 alpha、alpha 保持不变。输入/输出均为 HxWx4 uint8。

    a=255：RGB 逐像素精确不变；a=0：RGB 归零。
    """
    if rgba.ndim != 3 or rgba.shape[2] != 4 or rgba.dtype != np.uint8:
        raise ValueError(f"期望 HxWx4 uint8 数组，收到 {rgba.shape} {rgba.dtype}")
    alpha = rgba[..., 3:4].astype(np.uint16)
    out = rgba.copy()
    out[..., :3] = ((rgba[..., :3].astype(np.uint16) * alpha) // 255).astype(np.uint8)
    return out


def apply_black_background(frame: av.VideoFrame) -> av.VideoFrame:
    """对一帧执行黑底归一化，返回新的 rgba 帧（pts/time_base 由调用方设置）。

    注意：返回帧为 rgba；调用方随后按目标像素格式 reformat（yuva420p 保留
    alpha / yuv420p 丢弃 alpha）。
    """
    arr = frame.to_ndarray(format="rgba")
    arr = premultiply_rgb(arr)
    return av.VideoFrame.from_ndarray(arr, format="rgba")
