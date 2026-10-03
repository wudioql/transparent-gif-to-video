# 架构与维护边界

## 运行模型

本仓库的运行时是 **Python 包 `tgtv`**（`src/tgtv/`）。agent 读取 `SKILL.md`，通过 CLI 驱动三层流程，全程不调用外部 ffmpeg：

```text
单个 GIF → GifSource（PyAV gif demux/decoder，ignore_loop=1，PTS/逐帧时长透传）
         → convert（注册表驱动的通用 writer：8 条路径同一执行流）
         → verify（PyAV 解码 + NumPy 逐帧断言）
```

CLI 子命令：`tgtv probe`（只读能力探测）、`tgtv convert`（计划 → 确认 → 执行）、`tgtv verify`（转换后验证）。

运行时不使用系统 ffmpeg、ffprobe、Pillow、临时 PNG、图片序列或 ffconcat。开发阶段额外使用 pytest、Pillow（夹具生成）与 imageio-ffmpeg（迁移期等价性对照），均在 dev extras 中，不进入运行时依赖。

## 模块划分

| 模块 | 职责 |
|---|---|
| `formats.py` | 8 条输出路径的单一事实来源（FormatSpec 注册表：codec/pix_fmt/选项/尺寸约束） |
| `probe.py` | 只读能力探测（encoder 打开验证、显式解码器存在性、尺寸约束） |
| `source.py` | GIF 读取层（GifSource/GifFrame/GifInfo；ignore_loop、PTS、disposal 合成） |
| `filters.py` | 透明 RGB 黑色归一化（NumPy，uint16 中间量；等价旧 `premultiply=inplace=1:planes=0x7`） |
| `convert.py` | 计划构建/展示/执行（预检、两段确认、pts 透传、末帧时长映射） |
| `verify.py` | 转换后验证（旧 SKILL §4 全部规则的 NumPy 结构化实现） |
| `cli.py` | 三个子命令的参数与退出码 |

「probe 通过 ⇒ writer 可用」不漂移：两者用同一份注册表配置做打开验证。

## 设计不变量

- 无限循环 GIF 只处理一个周期：读取层固定 `ignore_loop=1`（不依赖 demuxer 默认值）。
- 逐帧 PTS 与时长（含最后一帧）原样透传；不重采样、不加固定帧率。
- 一个请求只有一个 GIF；没有批量和背景推断。
- 两段确认：任何会创建/覆盖文件的操作在用户明确确认后执行；输出已存在默认拒绝（`-n` 语义），确认执行计划（`--yes`）≠ 确认覆盖（`--overwrite`）。
- 画布不缩放、不裁切；H.264 偶数尺寸（`yuv420p` 的 2×2 色度抽样要求）不满足就在预检阶段停止，零文件产出。
- 缺 encoder 就停止（HAP 即此类：PyAV wheel 不含 hap encoder，如实报缺，不偷换格式）。

## VP8/VP9 黑色回退（可选，非默认）

**默认路径不做任何 RGB 处理**，直接沿用源 GIF 的透明区底层 RGB。只有用户明确要求「黑底」时才加 `--black`（仅 VP8/VP9 链路；mp4-black 的「合成到黑」复用同一函数）。

GIF alpha 在本 skill 输入假设下是二值。`filters.premultiply_rgb` 让 alpha=0 的 RGB 变黑、alpha=255 逐像素保持。它是透明 RGB 黑色归一化，不是背景猜测，也不是播放器 alpha 兼容性修复。

与旧命令的两处**有意**差异（均有像素级回归覆盖）：不再设置 `setparams=alpha_mode=straight`（仅帧元数据；Matroska `AlphaMode=1` 由复用器自动声明）；黑底检查由 `tgtv verify --black` 承载（透明区均值、alpha 阈值化一致、可见区 MAD、显式 libvpx 解码 `yuva420p`）。

## 黑底 MP4 是明确例外

只有用户明确选择黑底 MP4 才使用 `mp4-black`（premultiply 后 `libx264/yuv420p`，CRF 20 / preset slow / +faststart）。这是从 GIF 直接输出的收敛功能，不恢复旧的背景分析系统；如果最终只要 MP4，不先转 WebM。奇数宽或高在预检阶段拒绝——旧栈「省略 `format=yuv420p` 静默产出 yuv444p」的绕过路径在此结构性不存在。
