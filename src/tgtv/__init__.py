"""tgtv —— 透明 GIF → 带 alpha 的视频（纯 Python 栈）。

运行时依赖只有 Python 包：PyAV（wheel 捆带 FFmpeg 库）+ NumPy。
不依赖系统 ffmpeg、不依赖 Pillow（后者仅开发期生成夹具用）。

设计与决策记录见 docs/decisions.md。
"""

__version__ = "0.1.0"
