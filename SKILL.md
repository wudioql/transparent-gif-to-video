---
name: transparent-gif-to-video
description: 将带透明通道的 GIF、PNG、APNG 或图片帧序列转换为保留 alpha 的视频，或合成指定实色背景的不透明视频；支持逐帧时长精确保留、透明边缘单色检测（含可疑结果拦截）、VP9 lossless 和解码级输出校验。触发词：透明 GIF 转视频、保留透明通道、alpha 视频、GIF 转 WebM 透明、透明动图转 MP4。
agent_created: true
---

# 透明素材 → 视频

## 执行入口

所有操作统一使用同一个脚本，文档不维护第二套 ffmpeg 命令：

```text
scripts/transparent_gif_to_video.py
```

默认运行目录为 skill 根目录；从别处运行时使用绝对路径。

## 依赖

实现位于 `scripts/tgv/` 包，入口脚本是薄壳；依赖 Pillow ≥ 9.2。

| 子命令 | Pillow | ffmpeg | ffprobe |
|---|---|---|---|
| `inspect` / `suggest-background` | 必需 | — | — |
| `convert` | 必需 | 必需（建议 6.0+，需要 `-enc_time_base`） | — |
| `verify` | 必需 | 必需 | 必需 |

NumPy 为必需依赖（逐像素统计与边缘外扩）。

## 执行前确认

1. 输出是否保留透明。
2. 输出用途 / 播放端。
3. 不保留透明时的背景色 —— **未确认前不得选择黑色或任何默认色**。
4. 是否要求无损。

第 3 条有专门的流程，不要靠猜（见 §3.1）：先 `suggest-background` 分析证据，再把它给出的问题原样问用户。`--background auto-edge` 不能替代确认：它只能发现素材里存在唯一的透明边缘色，且会主动拒绝最常见的假阳性。

## 1. 检查输入

```text
python scripts/transparent_gif_to_video.py inspect <input>
python scripts/transparent_gif_to_video.py inspect <frames-dir> --sequence-duration-ms <ms>
```

图片序列目录和单帧静态图片没有内置时长，必须显式给 `--sequence-duration-ms`；GIF/APNG 使用自身逐帧 duration，不受该参数影响。大素材可加 `--no-edge-scan` 跳过边缘扫描。

重点字段：

- `width` / `height` / `frames` / `duration_seconds`
- `frame_duration_seconds.variable`、`timing.source_mode`
- `timing.plan`：脚本将如何铺设时间轴（`mode`、`framerate`、`output_frames`、`duration_is_exact`）
- `alpha.has_transparency` / `alpha.binary`
- `transparent_edge.single_colour` / `colour` / `distinct_colours` / `suspicious_reason`

## 2. 保留 alpha

```text
python scripts/transparent_gif_to_video.py convert <input> <output> --keep-alpha --codec <codec> [--crf N] [--lossless]
```

| `--codec` | 容器 | 说明 |
|---|---|---|
| `vp9` | `.webm` | 网页透明默认；`--lossless` 仅此编码器可用 |
| `vp8` | `.webm` | 旧环境兼容 |
| `prores4444` | `.mov` | 后期母版 |
| `png` | `.mov` | 无损交换 |
| `qtrle` | `.mov` | QuickTime 动画 |
| `ffv1` | `.mkv` | 开源无损归档 |

`--codec auto`：`.mov` → ProRes 4444，`.mkv` → FFV1，其余 → VP9。像素格式与 `auto-alt-ref 0` 由脚本设置，不要手写。

## 3. 合成不透明视频

### 3.1 先分析，再确认，最后转换（不透明输出的默认流程）

```text
python scripts/transparent_gif_to_video.py suggest-background <input> [--preview options.png]
```

输出一份提案：`evidence`（各证据及信任度）、`options`（排序后的候选：素材内证据在前，常见投放背景在后）、`question`（可直接照着问用户的话术）、`auto_edge_usable`。`--preview` 会把中间帧在各候选背景上合成为一张对比图 —— 看图比读十六进制快得多。

拿到用户答复后再执行 `convert --background '#RRGGBB'`。

`--background ask` 把这一步内联：有终端时交互式提问；**非交互环境（agent、CI）会打印同样的提案并以非零码退出，绝不自行取值**。

提案还会检查 `content_bbox`：如果所有帧的可见内容并集小于画布，说明透明区只是留白，此时"裁切"往往比"填色"更正确，`question.context` 会提示这一点。


```text
python scripts/transparent_gif_to_video.py convert <input> <output.mp4> --background '#RRGGBB' [--codec h264] [--crf N]
python scripts/transparent_gif_to_video.py convert <input> <output.mp4> --background auto-edge
```

不透明模式可用 `h264`（默认）、`vp9`、`vp8`。

`inspect` 的 `transparent_edge.background_candidates` 会列出所有可用证据及信任度：透明像素 RGB、GIF 调色板透明索引（中）、GIF 逻辑屏幕背景索引（低）、可见主体贴边主色（仅供外扩，不可当背景）。脚本只报告证据，不替你合并证据。

`auto-edge` 成功的条件：透明像素 alpha=0、与可见像素 8 邻域接触、全素材采样到的 RGB 严格相同，**且该结果不可疑**。检测到多色、无样本、或结果为 `#000000`（绝大多数编码器会把全透明像素 RGB 清零，因此它通常是解码产物而非作者意图；GIF 会再与调色板透明索引交叉验证）时命令失败。确有把握时可加 `--allow-suspicious-edge-colour`，但正确做法通常是显式 `--background '#RRGGBB'`。

### 透明边缘外扩（可选）

```text
python scripts/transparent_gif_to_video.py convert <input> <output.webm> --keep-alpha --bleed-edges 2
```

把可见颜色向透明区外扩 N 轮，只改 alpha=0 像素的 RGB，任何背景下的合成结果都不变，只减少有损编码把黑色拖过边界造成的光晕。**默认 0**：实测 1000×1000 二值 alpha 素材、VP9 crf 32，边缘带平均误差白底 0.35→0.23、黑底 0.01→0.13，体积 +2.9%。适用场景是半透明素材 + 浅色投放背景；无损或母版编码无意义。

## 4. 硬约束

- H.264 没有 alpha 语义，必须先合成背景。
- `--lossless` 只用于 VP9。
- VP8/VP9/H.264 要求偶数宽高；奇数尺寸直接报错，不自动缩放或裁切。
- `--crf` 默认按编码器取值（VP8/VP9 = 30，H.264 = 20）；对 ProRes/PNG/qtrle/FFV1 无效并会警告。
- 帧时长来自素材本身，不假设 30 fps；单帧素材不会被猜一个时长。
- 时间轴：统一或可对齐到合理网格的时长 → 精确 CFR（必要时按 gcd 重复帧，时间戳与总时长精确）；极端不规则 → concat/VFR 回退，此时容器无法记录最后一帧时长。编码器时基固定为 1/1000。
- 临时帧文件在转换结束后自动清理。

## 5. 校验输出

```text
python scripts/transparent_gif_to_video.py verify <input> <output> --expect alpha|opaque [--sample-frames N]
```

校验尺寸、帧数（对照 `timing.plan.output_frames`）、时长、像素格式，以及**实际解码**的首/中/末帧 alpha；WebM 自动走 libvpx 解码路径，不以 `ffprobe pix_fmt` 单独判断 alpha。`checks.all_pass` 为 `true` 才算通过。

## 6. 参考资料

- `references/architecture.md`：脚本架构、时间轴设计与维护边界
- `references/decision-guide.md`：格式选择和投放端决策
- `references/flatten-alpha.md`：alpha 合成、背景色与 auto-edge 的可信度
- `references/pitfalls.md`：错误与兼容性排查
- `references/sizing.md`：体积与质量参数
- `references/verification.md`：校验规则和限制
