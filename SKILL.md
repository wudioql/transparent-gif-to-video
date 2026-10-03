---
name: transparent-gif-to-video
description: 使用纯 Python 栈（tgtv 包：PyAV + NumPy，不依赖系统 ffmpeg）将单个透明 GIF 转换为带 alpha 的 VP9/VP8 WebM、ProRes 4444、PNG-in-MOV、qtrle 或 FFV1；也可在用户明确选择黑底 MP4 后输出不透明 H.264。转换前必须展示计划并等待明确确认，转换后用 tgtv verify 验证。
agent_created: true
---

# 单个透明 GIF → 视频

## 边界与硬规则

本 skill 每次只处理一个 `.gif`。运行时唯一依赖是 **Python 栈**：`tgtv` 包（`av` 即 PyAV ≥18,<19 + `NumPy`，Python ≥3.11）。**不依赖系统 ffmpeg**，不使用 Pillow、图片序列、批量转换、背景分析、`auto-edge` 或 `suggest-background`。

- 一切操作通过 `tgtv` CLI 完成：`probe`（只读预检）→ `convert`（计划→确认→执行）→ `verify`（转换后验证）。
- 默认推荐 **VP9 WebM CRF 30**，保留 alpha。体积远小于无损格式，且 alpha 与 RGB 保真度明显优于 VP8（实测 PSNR 高约 7 dB、可见晕环少四个数量级，见 `references/sizing.md`）。
- **VP8/VP9 WebM 的 CRF 30 是唯一默认质量档位，不得主动上调。** 实测 CRF 40 省 40% 体积的代价是 alpha 平面新增约 168 万个半透明像素（源为二值 alpha 时即边缘晕环）；VP8 上调 CRF 体积一点不变。只有用户明确要求更小体积并接受画质折损时才讨论，见 `references/sizing.md`。
- VP8/VP9 **默认保留源 GIF 的透明区底层 RGB**（不做任何预乘），最忠实于素材本身。**透明 RGB 黑色归一化（`--black`）必须经用户显式要求「黑底」才加入**，不得作为默认值。它只改变不支持 alpha 的播放端所显示的那层像素，不修复播放器兼容性。
- 常见用途（读取逐帧内容）只需 alpha WebM 一条路径；ProRes / PNG-in-MOV / qtrle / FFV1 / 黑底 MP4 均为按需选择，不作为默认推荐。
- GIF 原始帧时长必须逐帧保留（含最后一帧），不重采样成 CFR。
- 每次转换前必须向用户展示计划并等待明确确认；`probe` 与 `--dry-run` 是只读操作。
- **两段确认**：输出已存在时默认拒绝覆盖；确认执行计划（`--yes`）≠ 确认覆盖（`--overwrite`），两件事分开确认。
- 不猜测背景色，不静默缩放或裁切；尺寸约束不满足时在**预检阶段**停止并说明，零文件产出。
- 无限循环 GIF 只转换一个周期（读取层固定 `ignore_loop=1`，不存在取反风险）。
- HAP 已从矩阵移除（PyAV wheel 不含 hap encoder）；用户要求 HAP 时如实说明并停止，**不偷换格式**。

计划（`tgtv convert --dry-run` 的输出）至少包含：输入绝对路径与画布/帧数/时长、格式/用途、有损或无损、输出绝对路径、覆盖行为、底色策略与关键参数。计划必须明确写出**底色策略**：默认「保留源 GIF 底色」；只有用户明确要求黑底时才写「透明 RGB 黑色归一化（黑色回退）」并注明它不会修复播放器的 alpha 兼容性。

## 1. 只读预检

```bash
tgtv probe                          # 全部格式能力 + 验证解码器 + 环境信息
tgtv probe --require vp9            # 单格式预检，不满足则退出码 1
tgtv probe --require mp4-black --size 999x999   # 尺寸约束预检（偶数校验）
tgtv probe --json                   # 机读
```

encoder 不存在就停止，不偷偷换格式。黑底 MP4 的偶数尺寸约束在 `probe --require ... --size WxH` 与 `convert` 的计划阶段都会检查：宽或高任一为奇数即拒绝（`yuv420p` 的 2×2 色度抽样要求），不缩放、不裁切、不补边。旧栈「省略 `format=yuv420p` 静默产出 yuv444p」的绕过路径在新栈**结构性不存在**（预检先行拒绝）。

## 2. 格式矩阵

| 选择（`-f KEY`） | Alpha | 有损 | 用途与限制 |
|---|---:|---:|---|
| **vp9（默认）** | 是 | 是 | 网页与读帧场景首选；现代浏览器需支持 WebM alpha |
| vp9-lossless | 是 | 否 | WebM 无量化；`yuva420p` 仍不是原始 RGBA 字节逐点保证（4:2:0 表示限制） |
| vp8 | 是 | 是 | 体积可更小，但实测画质与 alpha 保真明显劣于 VP9（PSNR 低约 7 dB，可见晕环高四个数量级），非默认 |
| prores4444 | 是 | 视觉无损 | 剪辑/合成高码率母版；8-bit alpha |
| png-mov | 是 | 否 | 无损交换，通常很大 |
| qtrle | 是 | 否 | QuickTime Animation 工作流 |
| ffv1 | 是 | 否 | 开源无损归档；yuva444p 同域逐像素无损 |
| **mp4-black** | **否** | 是 | 仅用户明确选择；永久丢失 alpha；宽高必须均为偶数 |

默认输出**保留源 GIF 的透明区底层 RGB**（通常接近白）；黑底（`--black` / `mp4-black`）只改变忽略 alpha 的播放端显示的那层像素。

VP8/VP9 的 alpha 位于 WebM/Matroska `BlockAdditional`，`AlphaMode=1` 由复用器自动声明。它不能使不支持 `BlockAdditional` 的播放器显示透明；浏览器与显式 libvpx 解码正确，也不代表普通桌面播放器支持。

## 3. 命令模板（agent 工作流）

### 3.0 标准流程：计划 → 确认 → 执行 → 验证

```bash
# 1) 构建并展示计划（只读，不写任何文件）
tgtv convert "input.gif" -o "output.webm" --dry-run

# 2) 向用户展示计划，等待明确确认

# 3) 确认后执行（非交互环境必须 --yes：计划已另行展示确认）
tgtv convert "input.gif" -o "output.webm" --yes

# 4) 转换后验证（convert 的完成提示会给出可直接复制的命令）
tgtv verify "output.webm" --source "input.gif"
```

输出已存在时：默认拒绝且原文件不动；用户确认覆盖**该具体路径**后追加 `--overwrite`。交互终端会提示输入 `yes`；管道喂 `yes` 无效（设计如此——非交互环境的确认责任在 agent 与用户之间完成）。

### 3.1 各格式

```bash
tgtv convert in.gif -o out.webm                          # VP9 CRF30（默认）
tgtv convert in.gif -o out.webm -f vp9-lossless
tgtv convert in.gif -o out.webm -f vp8
tgtv convert in.gif -o out.mov -f prores4444
tgtv convert in.gif -o out.mov -f png-mov
tgtv convert in.gif -o out.mov -f qtrle
tgtv convert in.gif -o out.mkv  -f ffv1
tgtv convert in.gif -o out.mp4  -f mp4-black             # 唯一不透明路径
```

黑底归一化（仅 VP8/VP9 链路）：`--black`。对二值 alpha 等价于「alpha=0 的 RGB 归零、alpha=255 保持」；alpha 掩码与可见像素不受影响。**它不是播放器兼容性修复**。是否黑底必须写进计划并等待确认。

**CRF 是逐编码器的相对刻度，不能跨平台类比。** VP9 的 30 不等于 x264 的 30；黑底 MP4（libx264）固定 CRF 20 / preset slow。同一 libvpx 家族内部也不可类比：VP8 CRF 30 的 PSNR 比 VP9 CRF 30 低约 7 dB。

### 3.2 计划的机读输出

`tgtv convert ... --dry-run --json` 输出结构化计划（输入信息、格式、参数、底色策略、覆盖行为），供 agent 转述给用户。

## 4. 转换后验证

失败时保留错误信息，并把输出视为可能不完整；不报告成功。

```bash
tgtv verify out.webm --source in.gif              # 默认路径：完整断言
tgtv verify out.webm --source in.gif --black      # --black 产物：透明区归黑是预期
tgtv verify out.mp4 --source in.gif --black       # mp4-black：无 alpha 即正确证据
tgtv verify out.webm                              # 无源模式：仅输出自身断言
tgtv verify out.webm --json                       # 机读（checks + stats 全量数字）
```

退出码 0=通过 / 1=失败。验证规则（全部内置于 verify，不需要手写 ffmpeg 管线）：

1. **完整解码**：全部帧无错解出，帧数 > 0。
2. **显式 decoder**：WebM 的 VP9/VP8 按容器声明的 codec 显式用 `libvpx-vp9` / `libvpx` 解码（不按扩展名猜——原生解码器会把 `yuva420p` 悄悄读成 `yuv420p` 丢掉 alpha）。
3. **alpha 存在 ≠ 有透明像素**：全帧扫描 alpha 最小值，断言至少一帧 < 255。首帧不透明的素材只有全帧扫描能发现透明。
4. **半透明区分轻重**：轻微取整（1–31/224–254）与可见晕环（32–223）分开统计；有损路径卡晕环占比 < 0.5%。
5. **PTS 逐帧一致 + 总时长一致**（含最后一帧）：未被平均成 CFR。
6. **底色策略**（提供 `--source` 时）：默认断言透明区 RGB 与源一致——这是**意外预乘检测器**（历史上 out.webm 黑底 bug 正是这一类，透明区被拉黑、默认期望下 verify 报 FAIL 并提示改用 `--black` 期望）；`--black`/mp4-black 路径断言透明区归黑。
7. **可见区保真**：RGB 平均绝对差与 PSNR；png-mov/qtrle 要求 RGBA 域逐像素一致；ffv1 在其原生 yuva444p 域逐像素一致（RGBA 域差异是色彩空间往返假象）。
8. **黑底 MP4**：断言解码无 alpha（预期）且透明区呈现黑；「无 alpha 平面」本身就是该路径的正确证据。

ProRes 建议另外在目标编辑器实测；播放器兼容性问题（普通播放器白底/黑底）不属于转换缺陷，见 `references/pitfalls.md`。

## 5. 禁止事项

不确认就编码、静默覆盖、缺 encoder 偷换格式、背景推断、auto-edge、suggest-background、固定帧率重采样、批量转换、图片序列、缩放/裁切，或把「黑色回退」描述成播放器透明兼容性修复。

## 6. 参考

- `references/architecture.md`：运行模型与维护边界
- `references/decision-guide.md`：选择格式与黑底 MP4
- `references/pitfalls.md`：alpha、解码器陷阱、尺寸与故障排查（含 PyAV 特有条目）
- `references/sizing.md`：质量与体积（新栈校准数字）
- `references/verification.md`：验证规则与开发测试

安装与开发：`pip install -e ".[dev]"`；回归见 `tests/README.md` 与 `test-reports/`（新栈矩阵、CLI 等价性对照、sizing 校准）。
