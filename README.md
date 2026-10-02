# transparent-gif-to-video

面向 agent 的纯 FFmpeg skill：每次将**一个透明 GIF**转换为保留 alpha 的视频。运行时只依赖系统 `ffmpeg`，不恢复 Python、Pillow、NumPy、图片序列或批量转换。

默认推荐 VP9 WebM CRF 30，并支持 VP9 lossless、VP8、ProRes 4444、HAP Alpha MOV、PNG-in-MOV、qtrle、FFV1。CRF 数值是逐编码器的相对刻度，WebM 侧不要超过 30；黑底 MP4（libx264）另用 CRF 20。另有一个非常收敛的例外：用户明确选择“黑底 MP4”时，直接从 GIF 输出 libx264/yuv420p，不经过 WebM；该输出永久没有 alpha。

## 产品边界

- 转换前必须展示输入、格式、质量、输出、覆盖行为和关键参数，并等待明确确认。
- 不猜背景色，不提供 auto-edge/suggest-background，不缩放、不裁切、不批量处理。
- 保留 GIF 帧时长：使用 `-ignore_loop 1 -fps_mode passthrough -enc_time_base demux`，不加固定 `-r`。
- VP8/VP9 默认执行**透明 RGB 黑色归一化（黑色回退）**：alpha=0 的隐藏 RGB 归零，alpha=255 保持。这只影响不支持 alpha 的播放器显示为黑色，不能修复该播放器的透明支持。
- Windows 目标环境为 BtbN FFmpeg 9.0 GPL static；转换前检查所选 encoder。

## 选择格式

| 格式 | 用途 | 重要限制 |
|---|---|---|
| VP9 WebM CRF 30 | 默认网页输出 | WebM alpha 依赖播放端支持 |
| VP8 WebM | 较旧 WebM 环境，非默认 | 同上；实测 PSNR 比 VP9 CRF 30 低约 7.6 dB、半透明像素约 13 倍 |
| ProRes 4444 | 剪辑/合成母版 | 高码率；`-alpha_bits 8` 只降低部分 alpha 成本 |
| HAP Alpha MOV | 实时播放/VJ/部分剪辑 | 不保证比 FFV1/PNG 更小，兼容性不如 ProRes 普遍；需满足尺寸约束 |
| PNG-in-MOV / qtrle / FFV1 | 无损交换、遗留流程、归档 | 文件或播放器兼容性成本较高 |
| 黑底 H.264 MP4 | 用户明确不需要透明 | 永久丢 alpha；宽高必须为偶数 |

WebM alpha 位于 Matroska/WebM `BlockAdditional`。验证 VP9/VP8 时必须分别显式使用 `libvpx-vp9`/`libvpx`，不能按扩展名盲猜 decoder；普通桌面播放器可能忽略 alpha。

## 环境与开发验证

```powershell
ffmpeg -version
ffmpeg -hide_banner -encoders
```

开发回归矩阵见 [`tests/README.md`](tests/README.md)，参考资料见 `references/`。

动态回归结果见 [`test-reports/2026-10-02-matrix.md`](test-reports/2026-10-02-matrix.md)：在 **Windows BtbN FFmpeg 9.0.1 GPL static** 上，用 1000×1000 / 95 帧 / 30ms / 二值 alpha 的夹具实测通过了全部九条路径——VP9 CRF 30、VP9 lossless、VP8（含黑色归一化）、ProRes 4444 `-alpha_bits 8`、HAP Alpha、PNG-in-MOV、qtrle、FFV1、黑底 H.264 MP4。

运行时依赖仍然只有系统 `ffmpeg`；Python / NumPy 只出现在上述回归测试的取证环节，不是 skill 依赖。

尚未覆盖的夹具场景：无限循环之外的**变帧时长**、含空格/中文/括号的路径、奇数尺寸、不满足 HAP 约束的尺寸（见 `tests/README.md`）。

## 许可

MIT
