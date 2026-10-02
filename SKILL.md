---
name: transparent-gif-to-video
description: 使用系统 ffmpeg 将单个透明 GIF 转换为带 alpha 的 VP9/VP8 WebM、ProRes 4444、HAP Alpha MOV、PNG-in-MOV、qtrle 或 FFV1；也可在用户明确选择黑底 MP4 后输出不透明 H.264。转换前必须展示计划并等待明确确认。
agent_created: true
---

# 单个透明 GIF → 视频

## 边界与硬规则

本 skill 每次只处理一个 `.gif`，运行时唯一依赖是系统 `ffmpeg`。不恢复 Python、Pillow、NumPy、图片序列、批量转换、背景分析、`auto-edge` 或 `suggest-background`。

- 默认推荐 **VP9 WebM CRF 30**，保留 alpha。体积远小于无损格式，且 alpha 与 RGB 保真度明显优于 VP8（实测 PSNR 高约 7.6 dB，见 `test-reports/2026-10-02-matrix.md` §12/§13）。
- **VP8/VP9 WebM 的 CRF 30 是唯一默认质量档位，不得主动上调。** 实测 CRF 40 会让 alpha 平面新增约 29 万个半透明像素（源为二值 alpha），且 VP8 上调至 CRF 40 仅再省 0.5% 体积。只有用户明确要求更小体积并接受画质折损时才讨论，见 `references/sizing.md`。
- VP8/VP9 **默认保留源 GIF 的透明区底层 RGB**（不加任何 premultiply），最忠实于素材本身。**透明 RGB 黑色归一化必须经用户显式要求「黑底」才加入**，不得作为默认值。它只改变不支持 alpha 的播放端所显示的那层像素，不修复播放器兼容性。
- 常见用途（读取逐帧内容）只需 alpha WebM 一条路径；ProRes / HAP / PNG-in-MOV / qtrle / FFV1 / 黑底 MP4 均为按需选择，不作为默认推荐。
- GIF 原始帧时长必须保留；不得添加固定 `-r`。
- 每次转换前必须展示计划并等待用户明确确认；能力检查和媒体读取是只读操作。
- 默认 `-n` 拒绝覆盖；只有用户明确确认覆盖该具体路径，才使用 `-y`。
- 不猜测背景色，不静默缩放或裁切；尺寸约束不满足时停止并说明。
- `-ignore_loop 1` 只转换一个 GIF 动画周期。

计划至少列出：输入绝对路径、格式/用途、有损或无损、输出绝对路径、覆盖行为，以及关键参数。计划必须明确写出**底色策略**：默认「保留源 GIF 底色」；只有用户明确要求黑底时，才写「透明 RGB 黑色归一化（黑色回退）」并注明它不会修复播放器的 alpha 兼容性。

## 1. 只读预检

```powershell
ffmpeg -version
ffmpeg -hide_banner -encoders
ffmpeg -hide_banner -v error -ignore_loop 1 -i "C:\path\input.gif" -map 0:v:0 -f null NUL
```

推荐 Windows 目标环境：BtbN FFmpeg 9.0 GPL static。按用户选择检查 encoder，不存在就停止，不偷偷换格式：

| 输出 | 必需 encoder | 容器 |
|---|---|---|
| VP9 / VP9 lossless | `libvpx-vp9` | WebM |
| VP8 | `libvpx` | WebM |
| ProRes 4444 | `prores_ks` | MOV |
| HAP Alpha | `hap` | MOV |
| PNG-in-MOV | `png` | MOV |
| qtrle | `qtrle` | MOV |
| FFV1 | `ffv1` | MKV |
| 明确黑底 MP4 | `libx264` | MP4 |

HAP 还要检查目标尺寸是否满足当前 FFmpeg HAP encoder 的约束；目标 BtbN/FFmpeg 9 按宽、高均须为 4 的倍数处理，并用实际 encoder 错误确认该约束。不满足时拒绝，不缩放、不裁切。

## 2. 格式矩阵

| 选择 | Alpha | 有损 | 用途与限制 |
|---|---:|---:|---|
| **VP9 WebM CRF 30（默认）** | 是 | 是 | 网页与读帧场景首选；现代浏览器需支持 WebM alpha |
| VP9 lossless WebM | 是 | 否 | WebM 无量化；`yuva420p` 仍不是原始 RGBA 字节逐点保证 |
| VP8 WebM | 是 | 是 | 体积可更小，但实测画质与 alpha 保真明显劣于 VP9 CRF 30，非默认 |
| ProRes 4444 MOV | 是 | 视觉无损 | 剪辑/合成高码率母版；使用 8-bit alpha |
| HAP Alpha MOV | 是 | 是/编码器相关 | 实时播放、VJ、部分剪辑软件；不保证比 FFV1/PNG 更小，也不如 ProRes 4444 普遍 |
| PNG-in-MOV | 是 | 否 | 无损交换，通常很大 |
| qtrle MOV | 是 | 否 | QuickTime Animation 工作流 |
| FFV1 MKV | 是 | 否 | 开源无损归档 |
| **黑底 H.264 MP4** | **否** | 是 | 仅用户明确选择；永久丢失 alpha |

默认输出**保留源 GIF 的透明区底层 RGB**（通常接近白），与素材一致；只有用户明确要求「黑底」时才改用 §3.0 的黑色归一化。两者 alpha 掩码与可见像素完全相同，在正确合成 alpha 的播放端视觉一致，差别仅体现在忽略 alpha 的播放端。

VP8/VP9 的 alpha 位于 WebM/Matroska `BlockAdditional`。`AlphaMode=1` 只声明 alpha，不能使不支持 `BlockAdditional` 的播放器显示透明。浏览器正确和显式 libvpx 解码正确，也不代表普通桌面播放器支持。

## 3. 命令模板

公共参数：

```text
-hide_banner -n -ignore_loop 1 -i "<INPUT>" -map 0:v:0 -an -fps_mode passthrough -enc_time_base demux
```

确认覆盖后才把 `-n` 改为 `-y`。

### 3.0 底色策略（默认保留 / 可选黑底）

**默认命令不加任何 RGB 处理 filter**——直接沿用源 GIF 的透明区底层 RGB。

**可选：透明 RGB 黑色归一化（黑色回退）**。只有用户明确要求「黑底」时，才在 VP8/VP9 命令中插入：

```text
-vf "format=rgba,premultiply=inplace=1:planes=0x7,setparams=alpha_mode=straight"
```

对二值 alpha：alpha=0 的 RGB 归零，alpha=255 保持；`setparams` 将元数据标为 straight。alpha 掩码与可见像素不受影响。**它不是播放器兼容性修复**，只是让忽略 alpha 的播放端显示黑而非原 GIF 底色（本素材为白）。是否黑底必须写进计划并等待确认。

> 注意一致性：黑色归一化目前只适用于 VP8/VP9。若用户对 MOV 类格式（ProRes 4444、HAP Alpha）也要求黑底，需另行验证 filter 在该链路的行为，不得直接套用。

### 3.1 VP9 WebM（默认）

```powershell
ffmpeg -hide_banner -n -ignore_loop 1 -i "<INPUT>" -map 0:v:0 -an -fps_mode passthrough -enc_time_base demux -c:v libvpx-vp9 -pix_fmt yuva420p -auto-alt-ref 0 -b:v 0 -crf 30 -deadline good -cpu-used 2 -row-mt 1 "<OUTPUT>.webm"
```

要求黑底时，在 `-map 0:v:0 -an` 之后插入 §3.0 的 premultiply `-vf`。

**注意：CRF 是逐编码器的相对刻度，不能跨平台类比。** VP9 的 30 不等于 x264 的 30。黑底 MP4（libx264）使用 CRF 20，不要因为 WebM 默认 30 就把 MP4 也改高。同一 libvpx 家族内部也不可类比：实测 VP8 CRF 30 的 PSNR 比 VP9 CRF 30 低约 7.6 dB。

### 3.2 VP9 lossless WebM

同上（默认无 `-vf`；要求黑底时同样插入 §3.0 的 `-vf`），编码段改为：

```text
-c:v libvpx-vp9 -pix_fmt yuva420p -auto-alt-ref 0 -lossless 1 -deadline good -cpu-used 2 -row-mt 1
```

### 3.3 VP8 WebM

体积可能小于 VP9，但实测同一素材上 VP8 CRF 30 的可见区 PSNR 比 VP9 CRF 30 低约 7.6 dB，且误差随帧序累积（第 2 帧平均差 3.49 → 第 60 帧 9.63，最大差达 223）。用户明确要求 VP8 或追求极小体积时才使用。

```powershell
ffmpeg -hide_banner -n -ignore_loop 1 -i "<INPUT>" -map 0:v:0 -an -fps_mode passthrough -enc_time_base demux -c:v libvpx -pix_fmt yuva420p -auto-alt-ref 0 -b:v 0 -crf 30 -deadline good -cpu-used 2 "<OUTPUT>.webm"
```

要求黑底时，在 `-map 0:v:0 -an` 之后插入 §3.0 的 premultiply `-vf`。

### 3.4 ProRes 4444 MOV

```powershell
ffmpeg -hide_banner -n -ignore_loop 1 -i "<INPUT>" -map 0:v:0 -an -fps_mode passthrough -enc_time_base demux -c:v prores_ks -profile:v 4444 -alpha_bits 8 -pix_fmt yuva444p10le "<OUTPUT>.mov"
```

`-alpha_bits 8` 只降低部分 alpha 数据成本；ProRes 4444 仍是高码率编辑母版。

### 3.5 HAP Alpha MOV

先确认 `hap` encoder，再确认尺寸约束；确认后：

```powershell
ffmpeg -hide_banner -n -ignore_loop 1 -i "<INPUT>" -map 0:v:0 -an -fps_mode passthrough -enc_time_base demux -c:v hap -format hap_alpha -compressor snappy -pix_fmt rgba "<OUTPUT>.mov"
```

若 FFmpeg 9/BtbN 对目标尺寸或像素格式报错，停止并报告原始错误；不得自动缩放或裁切。

### 3.6 PNG-in-MOV、qtrle、FFV1

```text
PNG:   -c:v png   -pix_fmt rgba       <OUTPUT>.mov
qtrle: -c:v qtrle -pix_fmt argb       <OUTPUT>.mov
FFV1:  -c:v ffv1 -level 3 -coder 1 -context 1 -g 1 -slicecrc 1 -pix_fmt yuva444p <OUTPUT>.mkv
```

均接公共参数，且不加入固定 `-r`。

### 3.7 明确黑底 H.264 MP4（唯一不透明例外）

只有用户明确选择“黑底 MP4”后才允许此路径。计划必须说明输出将永久失去 alpha。不要先转 WebM：用户最终只要黑底 MP4 时，应直接从 GIF 输出。

**宽高必须均为偶数**，原因是 `yuv420p` 的 2×2 色度抽样，不是 x264 的任意限制：宽或高任一为奇数都会被编码器硬拒，报错原文为 `width not divisible by 2` / `height not divisible by 2`（2026-10-02 在 999×999 / 999×1000 / 1000×999 三种组合上实测确认）。奇数尺寸停止并报告，**不得自动缩放、裁切或补边**。

例外仅作解释用：`yuv444p` 没有色度抽样，999×999 可以编码成功；但 High 4:4:4 profile 在多数播放器与平台上不被支持，**不得把它当作绕过尺码约束的手段**。

```powershell
ffmpeg -hide_banner -n -ignore_loop 1 -i "<INPUT>" -map 0:v:0 -an -vf "format=rgba,premultiply=inplace=1:planes=0x7,format=yuv420p" -fps_mode passthrough -enc_time_base demux -c:v libx264 -crf 20 -preset slow -movflags +faststart "<OUTPUT>.mp4"
```

该 premultiply 对二值 alpha 等价于合成到黑色；不得把它推广为任意背景功能。

## 4. 转换后验证

失败时保留错误信息，并把输出视为可能不完整；不报告成功。

### 4.1 所有输出：完整解码

```powershell
ffmpeg -hide_banner -v error -i "<OUTPUT>" -map 0:v:0 -f null NUL
```

### 4.2 Alpha 与真实透明像素

VP9 必须显式 `-c:v libvpx-vp9`，VP8 必须显式 `-c:v libvpx`；不要按 `*.webm` 猜 decoder。

两条命令约束，缺一不可：

1. **不能加 `-v error`。** `signalstats` 与 `metadata=print` 输出在 info 级；加 `-v error` 会导致 YMIN/YMAX 采样数为 0，"扫描全部帧"静默退化成"零帧"，断言被跳过却不报错（实测确认）。
2. **必须加 `format=gray`。** ProRes 4444 读取为 `yuva444p12le` 时 `alphaextract` 输出 gray16，`signalstats` 在 16 位域报值，8 位阈值 `YMIN<255` 会误判 FAIL（实测某次报 `YMIN=256`，而 alpha 实际逐像素正确）。

```powershell
ffmpeg -hide_banner -c:v libvpx-vp9 -i "<VP9.webm>" -vf alphaextract,format=gray,signalstats,metadata=print -f null NUL
ffmpeg -hide_banner -c:v libvpx     -i "<VP8.webm>" -vf alphaextract,format=gray,signalstats,metadata=print -f null NUL
```

其他 alpha 格式使用 `-i` 后接同样的 `-vf`。`alphaextract` 成功只证明存在 alpha 平面，不证明存在透明像素；必须扫描全部帧，读取 signalstats 的 YMIN/YMAX，并断言至少一帧 `YMIN < 255`。

**不要只用第一帧下画质结论。** 首帧是关键帧，天然干净；有损编码的 alpha 污染与 RGB 误差从第 2 帧起才随帧间预测显现并累积。回归测试必须逐帧统计半透明像素数与可见区 RGB 误差（见 `test-reports/2026-10-02-matrix.md` §12/§13 的反例）。

黑底 MP4 应完整解码并确认输出为 `yuv420p`/无 alpha；`alphaextract` 对它失败是预期的“不透明”证据，而不是错误。另用 `signalstats` 或抽样帧确认黑色透明区，不把“无 alpha 平面”误称为像素内容验证。

ProRes 必须另外执行 alphaextract，并在目标编辑器实测；HAP 也必须在目标实时播放/剪辑软件实测。

## 5. 禁止事项

不确认就编码、静默覆盖、缺 encoder 偷换、背景推断、auto-edge、suggest-background、固定 `-r`、批量转换、图片序列、缩放/裁切，或把“黑色回退”描述成播放器透明兼容性修复。

## 6. 参考

- `references/architecture.md`：运行模型与维护边界
- `references/decision-guide.md`：选择格式与黑底 MP4
- `references/pitfalls.md`：alpha、WebM decoder、尺寸与故障排查
- `references/sizing.md`：质量与体积
- `references/verification.md`：逐帧验证和开发测试限制
