# 选型决策指南

实际转换统一走 `scripts/transparent_gif_to_video.py`；这里回答“选哪条路径”，不维护容易过期的手写 ffmpeg 命令。

## 1. 第一性原理

视频输出首先是一个语义选择：

- **需要透明**：输出必须携带 alpha，播放器/剪辑软件还要理解该 alpha。
- **不需要透明**：先把 alpha 与已知背景合成，再输出普通视频；这样可以使用 H.264，兼容性最高。

alpha 保真、兼容性、体积之间存在取舍。编码器参数只能在选对语义之后优化，不能弥补错误的容器选择。

## 2. 决策树

```text
最终需要透明吗？
├─ 否 → suggest-background → 问用户 → --background '#RRGGBB' → 默认 H.264 MP4
│      ├─ 背景未知时必须先询问，不能默认黑色
│      └─ 若透明区只是留白（content_bbox 小于画布），优先考虑裁切而不是填色
└─ 是
   ├─ 网页/现代 Chromium/Firefox → --keep-alpha --codec vp9
   ├─ 后期制作 → --keep-alpha --codec prores4444
   ├─ 开源无损归档 → --keep-alpha --codec ffv1
   └─ Safari/iOS → 不要只交付 VP9 alpha，考虑 WebP/APNG/双轨/canvas
```

## 3. 输入体检

运行 `inspect` 后重点检查：

| 项目 | 决定什么 |
|---|---|
| 宽高 | 编码器是否接受尺寸；是否需要在转换前明确缩放 |
| 帧数与总时长 | 是否丢帧、循环是否需要由播放器另行控制 |
| 帧时长是否可变 | 决定走精确 CFR 网格还是 VFR 回退；看 `timing.plan` |
| alpha 分布 | 二值 alpha 通常适合有损；连续半透明更应降低 CRF 或无损 |
| 透明边缘色 | 能否使用 `auto-edge`；多色或 `suspicious_reason` 非空时必须显式背景 |
| `background_candidates` | 各证据是否互相印证；矛盾时说明只能由投放端决定 |
| 半透明比例 + 投放背景 | 是否值得开 `--bleed-edges`（浅色背景 + 半透明才有明显收益） |
| 画面复杂度 | 决定有损/无损体积，比单看分辨率更重要 |

## 4. 格式矩阵

| 路径 | 默认入口 | Alpha | 典型用途 |
|---|---|---:|---|
| VP9 WebM | `--keep-alpha --codec vp9` | 是 | 网页透明、有损或无损 |
| VP8 WebM | `--keep-alpha --codec vp8` | 是 | 旧环境兼容 |
| ProRes 4444 MOV | `--keep-alpha --codec prores4444` | 是 | 后期母版 |
| PNG-in-MOV | `--keep-alpha --codec png` | 是 | 无损交换 |
| qtrle MOV | `--keep-alpha --codec qtrle` | 是 | QuickTime 动画 |
| FFV1 MKV | `--keep-alpha --codec ffv1` | 是 | 开源无损归档 |
| H.264 MP4 | `--background '#RRGGBB' --codec h264` | 否 | 通用投放 |

## 5. 何时不该做视频

- 只需要网页动画且不需要视频语义：动画 WebP 或 APNG 可能更简单。
- 素材本来是矢量动画：Lottie/SVG 通常更适合。
- 需要精确交互或逐帧控制：canvas/WebGL 更灵活。
- 主要投放 iOS 且必须透明：先解决载体兼容性，再谈编码器。
