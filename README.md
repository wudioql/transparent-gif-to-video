# transparent-gif-to-video

一个面向 agent 的轻量 skill：把**单个带透明通道的 GIF**转换为保留 alpha 的视频。

运行时不需要 Python、Pillow、NumPy 或图片序列工具，只调用系统中的 `ffmpeg`。默认推荐输出 VP9 WebM，同时支持 VP8 WebM、ProRes 4444、PNG-in-MOV、qtrle 和 FFV1。

## 设计边界

支持：

- 每次转换一个 GIF；
- 保留 GIF 画布尺寸、帧顺序和逐帧时间戳；
- 输出多种带 alpha 的视频编码；
- 转换前先说明选型、输出路径和覆盖行为，并等待用户确认；
- 转换后使用 ffmpeg 实际解码输出并检查 alpha 平面。

不支持：

- MP4/H.264 或任何合成背景的不透明输出；
- PNG/APNG、图片序列和批量转换；
- 缩放、裁切、补帧或背景色推断。

## Windows 环境

推荐使用 BtbN 的 FFmpeg 9.0 GPL static release branch：

```powershell
winget install --id BtbN.FFmpeg.GPL.9.0 --exact
```

确认 VP9 编码器存在：

```powershell
ffmpeg -hide_banner -encoders | findstr /I "libvpx-vp9"
```

完整工作流、格式选择和命令模板见 [`SKILL.md`](SKILL.md)。

## 开发验证

本仓库不含运行时代码。时间戳和 alpha 的回归测试要求见 [`tests/README.md`](tests/README.md)。

## 许可

MIT
