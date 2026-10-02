---
name: transparent-gif-to-video
description: 使用系统 ffmpeg 将单个带透明通道的 GIF 转换为保留 alpha 的 VP9/VP8 WebM、ProRes 4444、PNG-in-MOV、qtrle 或 FFV1 视频。转换前必须先确认格式、质量、输出路径和覆盖行为。触发词：透明 GIF 转视频、保留透明通道、alpha 视频、GIF 转透明 WebM、GIF 转 ProRes 4444。
agent_created: true
---

# 单个透明 GIF → Alpha 视频

## 目标与边界

本 skill 每次只处理一个 GIF 文件，并只生成保留 alpha 的视频。

- 输入：一个带透明通道的 `.gif`。
- 默认推荐：有损 VP9 WebM，`CRF 30`。
- 其他输出：VP9 lossless、VP8 WebM、ProRes 4444、PNG-in-MOV、qtrle、FFV1。
- 运行时唯一媒体依赖：系统 `ffmpeg`。
- 不支持：不透明 MP4/H.264、背景合成、PNG/APNG、图片序列、批量转换、缩放或裁切。
- 视频只保存 GIF 的一个动画周期；循环播放由播放器或网页控制。

## 硬规则：确认后才能转换

能力检查和读取媒体信息属于只读操作，可以在确认前执行。任何会创建、覆盖或删除文件的命令，都必须等用户明确确认。

转换前必须向用户给出一份简短计划，至少包括：

1. 输入 GIF 的绝对路径；
2. 选定格式及用途；
3. 有损或无损；
4. 输出绝对路径；
5. 输出已存在时是否覆盖；
6. 即将执行的关键 ffmpeg 参数。

如果用户没有偏好，推荐 **VP9 WebM，有损 CRF 30**，但仍要问用户是否接受，不能直接执行。示例：

```text
建议输出 VP9 WebM（保留透明，CRF 30，适合网页，体积通常小于无损格式）。
输入：C:\assets\logo.gif
输出：C:\assets\logo.webm
输出不存在，不涉及覆盖。是否按此方案开始转换？
```

只有用户明确回复“确认”“开始”“按这个方案执行”等同意语句后，才能执行转换。若确认后参数发生变化，必须重新确认变化后的计划。

## 1. 只读预检

### 1.1 检查 FFmpeg

```powershell
ffmpeg -version
ffmpeg -hide_banner -encoders
```

推荐 Windows 环境为 BtbN FFmpeg 9.0 GPL static release branch：

```powershell
winget install --id BtbN.FFmpeg.GPL.9.0 --exact
```

不要因为 `ffmpeg.exe` 存在就假定编码器也存在。根据目标格式确认下表中的 encoder：

| 输出 | 必需 encoder |
|---|---|
| VP9 WebM | `libvpx-vp9` |
| VP8 WebM | `libvpx` |
| ProRes 4444 MOV | `prores_ks` |
| PNG-in-MOV | `png` |
| qtrle MOV | `qtrle` |
| FFV1 MKV | `ffv1` |

缺少目标 encoder 时停止并说明原因；不得静默改成另一种格式。

### 1.2 检查输入

确认路径存在、扩展名为 `.gif`，然后只读解码一次：

```powershell
ffmpeg -hide_banner -v error -ignore_loop 1 -i "C:\path\input.gif" -map 0:v:0 -f null NUL
```

失败时停止，不创建输出。`-ignore_loop 1` 明确只读取一个 GIF 动画周期，避免无限循环 GIF 导致转换不结束。

如果只读信息表明输入不是 GIF、没有视频流或不能解码，直接报告，不尝试修复或转用其他输入流程。

## 2. 格式选择

| 选择 | 容器 | Alpha | 有损 | 典型用途 |
|---|---|---:|---:|---|
| VP9（默认推荐） | WebM | 是 | 是 | 网页、现代 Chromium/Firefox，体积优先 |
| VP9 lossless | WebM | 是 | 否 | 仍需 WebM，但不希望 VP9 量化 |
| VP8 | WebM | 是 | 是 | 旧 WebM 环境兼容 |
| ProRes 4444 | MOV | 是 | 视觉无损 | 剪辑、合成、后期母版 |
| PNG-in-MOV | MOV | 是 | 否 | 无损交换，体积较大 |
| qtrle | MOV | 是 | 否 | QuickTime Animation 兼容 |
| FFV1 | MKV | 是 | 否 | 开源无损归档 |

说明：

- H.264/普通 MP4 没有这里所需的 alpha 语义，不提供该选项。
- VP9 lossless 配合 `yuva420p` 不等于原始 GIF 的 RGBA 字节逐像素完全相同；它表示 VP9 不做有损量化，但色度表示仍是 4:2:0。
- Safari/iOS 对 WebM alpha 的支持取决于具体系统和播放端；后期工作优先考虑 ProRes 4444。

## 3. 转换命令模板

把 `<INPUT>` 和 `<OUTPUT>` 替换为已确认的绝对路径。Windows PowerShell/CMD 中始终用双引号包裹路径。

公共参数：

```text
-hide_banner -ignore_loop 1 -i "<INPUT>" -map 0:v:0 -an -fps_mode passthrough -enc_time_base demux
```

`-fps_mode passthrough` 保留 GIF demuxer 给出的逐帧时间戳；`-enc_time_base demux` 避免编码器用默认帧率的粗时间基重新量化时间戳。不要添加 `-r`，也不要把 GIF 强制转换为 25/30/60 fps。

### 3.1 VP9 WebM（默认推荐）

```powershell
ffmpeg -hide_banner -n -ignore_loop 1 -i "<INPUT>" -map 0:v:0 -an -fps_mode passthrough -enc_time_base demux -c:v libvpx-vp9 -pix_fmt yuva420p -auto-alt-ref 0 -b:v 0 -crf 30 -deadline good -cpu-used 2 -row-mt 1 "<OUTPUT>.webm"
```

### 3.2 VP9 lossless WebM

```powershell
ffmpeg -hide_banner -n -ignore_loop 1 -i "<INPUT>" -map 0:v:0 -an -fps_mode passthrough -enc_time_base demux -c:v libvpx-vp9 -pix_fmt yuva420p -auto-alt-ref 0 -lossless 1 -deadline good -cpu-used 2 -row-mt 1 "<OUTPUT>.webm"
```

### 3.3 VP8 WebM

```powershell
ffmpeg -hide_banner -n -ignore_loop 1 -i "<INPUT>" -map 0:v:0 -an -fps_mode passthrough -enc_time_base demux -c:v libvpx -pix_fmt yuva420p -auto-alt-ref 0 -b:v 0 -crf 30 -deadline good -cpu-used 2 "<OUTPUT>.webm"
```

### 3.4 ProRes 4444 MOV

```powershell
ffmpeg -hide_banner -n -ignore_loop 1 -i "<INPUT>" -map 0:v:0 -an -fps_mode passthrough -enc_time_base demux -c:v prores_ks -profile:v 4444 -pix_fmt yuva444p10le "<OUTPUT>.mov"
```

### 3.5 PNG-in-MOV

```powershell
ffmpeg -hide_banner -n -ignore_loop 1 -i "<INPUT>" -map 0:v:0 -an -fps_mode passthrough -enc_time_base demux -c:v png -pix_fmt rgba "<OUTPUT>.mov"
```

### 3.6 qtrle MOV

```powershell
ffmpeg -hide_banner -n -ignore_loop 1 -i "<INPUT>" -map 0:v:0 -an -fps_mode passthrough -enc_time_base demux -c:v qtrle -pix_fmt argb "<OUTPUT>.mov"
```

### 3.7 FFV1 MKV

```powershell
ffmpeg -hide_banner -n -ignore_loop 1 -i "<INPUT>" -map 0:v:0 -an -fps_mode passthrough -enc_time_base demux -c:v ffv1 -level 3 -coder 1 -context 1 -g 1 -slicecrc 1 -pix_fmt yuva444p "<OUTPUT>.mkv"
```

### 覆盖规则

模板默认使用 `-n`，如果输出已存在则拒绝覆盖。只有用户明确确认覆盖该具体路径后，才把 `-n` 改为 `-y`。不得先删除旧文件来规避确认。

转换失败时保留 ffmpeg 的错误信息，并明确指出输出可能不完整；不要把部分文件报告为成功。

## 4. 转换后验证

转换完成不等于任务完成。必须使用 ffmpeg 实际解码输出。

### 4.1 完整解码

```powershell
ffmpeg -hide_banner -v error -i "<OUTPUT>" -map 0:v:0 -f null NUL
```

命令必须以 0 退出。

### 4.2 检查 alpha 平面

VP9：

```powershell
ffmpeg -hide_banner -v error -c:v libvpx-vp9 -i "<OUTPUT>.webm" -vf alphaextract -frames:v 1 -f null NUL
```

VP8：

```powershell
ffmpeg -hide_banner -v error -c:v libvpx -i "<OUTPUT>.webm" -vf alphaextract -frames:v 1 -f null NUL
```

其他格式：

```powershell
ffmpeg -hide_banner -v error -i "<OUTPUT>" -vf alphaextract -frames:v 1 -f null NUL
```

WebM 必须显式使用 libvpx 解码器；原生路径可能忽略 WebM BlockAdditional 中的 alpha。`alphaextract` 成功证明输出可解码出 alpha 平面，但不是逐像素无损证明。

只有完整解码和 alpha 检查都成功，才向用户报告完成。报告应包含：输出路径、格式、有损/无损选择和文件大小（可读取时）。

## 5. 禁止的自动行为

- 不确认就开始编码；
- 用户未选择时静默采用默认格式；
- 静默覆盖已有文件；
- 发现 encoder 缺失后偷偷换 codec；
- 添加固定 `-r` 或猜测帧率；
- 将输出改成不透明 MP4；
- 猜背景色、缩放、裁切或改变画布；
- 一次处理多个 GIF。

## 6. 参考资料

- `references/architecture.md`：为何改成纯 ffmpeg 单文件流程
- `references/decision-guide.md`：输出格式选择
- `references/pitfalls.md`：Windows、时间戳和 alpha 排错
- `references/sizing.md`：质量与体积
- `references/verification.md`：只用 ffmpeg 的验证边界
