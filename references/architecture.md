# 架构与维护边界

## 运行模型

本仓库没有运行时代码。agent 读取 `SKILL.md`，做只读预检，展示计划并等待明确确认，再直接调用系统 `ffmpeg`：

```text
单个 GIF → ffmpeg GIF demux/decoder → 保留 demux 时间戳 → 选定 encoder → ffmpeg 解码验证
```

运行时不使用 Python、Pillow、NumPy、ffprobe、临时 PNG、图片序列或 ffconcat。开发阶段可使用额外工具检查 PTS、像素和容器结构，但不能成为 skill 依赖。

## 设计不变量

- `-ignore_loop 1`：无限循环 GIF 只处理一个周期。
- `-fps_mode passthrough -enc_time_base demux`：保留逐帧时长，不加 `-r`。
- 一个请求只有一个 GIF；没有批量和背景推断。
- 任何会创建/覆盖文件的命令都在用户明确确认后执行，默认 `-n`。
- 画布不缩放、不裁切；H.264 偶数尺寸和 HAP 尺寸约束不满足就停止。

## VP8/VP9 黑色回退

GIF alpha 在本 skill 输入假设下是二值。`format=rgba,premultiply=inplace=1:planes=0x7,setparams=alpha_mode=straight` 应让 alpha=0 的 RGB 变黑，而 alpha=255 保持。它是透明 RGB 黑色归一化，不是背景猜测，也不是播放器 alpha 兼容性修复。

该 filter 必须在 BtbN FFmpeg 9 动态验证：alpha 掩码前后一致、透明 RGB 为黑、可见 RGB 未非预期变化、浏览器仍透明、显式 libvpx 解码得到 `yuva420p`。若 FFmpeg 后续自动 unpremultiply 或改变 alpha，必须换成经验证的纯 FFmpeg filter，而不是保留未经验证的命令。

## 黑底 MP4 是明确例外

只有用户明确选择黑底 MP4 才使用 premultiply 后 `libx264/yuv420p`。这是从 GIF 直接输出的收敛功能，不恢复旧的背景分析系统；如果最终只要 MP4，不先转 WebM。
