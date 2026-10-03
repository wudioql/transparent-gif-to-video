# transparent-gif-to-video

> **把一个透明 GIF，变成真正带 alpha 的视频。**
> 面向 agent 的纯 Python 栈 skill：`pip install` 后一条 CLI 完成转换——运行时只依赖 **PyAV + NumPy**，不需要系统 ffmpeg、没有图片序列、没有批量管线。

透明 GIF 的麻烦不在于能不能转，而在于转完之后 **alpha 是不是还活着**。这个仓库把这件事变成一条可验证、可复现的命令：`tgtv convert` 出计划、确认后执行、`tgtv verify` 逐帧验证。

---

## 30 秒上手

```bash
pip install -e .            # 或 pip install -e ".[dev]" 带上回归工具
tgtv convert input.gif -o output.webm --dry-run    # 展示计划（只读）
tgtv convert input.gif -o output.webm --yes        # 确认后执行
tgtv verify output.webm --source input.gif         # 转换后验证
```

这是默认路径：**VP9 WebM CRF 30，保留 alpha，保留源 GIF 的透明区底色，逐帧时长（含最后一帧）原样搬运**。输出已存在时默认拒绝覆盖；确认覆盖该具体文件后追加 `--overwrite`。

skill 在真正执行前会先展示计划——输入、格式、质量、输出、覆盖行为、底色策略——并等你明确确认；非交互环境必须 `--yes`（确认责任在 agent 与用户之间完成，管道喂 `yes` 无效）。

## 它能输出什么

| 格式（`-f KEY`） | Alpha | 用途 | 关键限制 |
|---|---:|---|---|
| **vp9** | ✅ | **默认**；网页、读取逐帧素材 | 播放端需支持 WebM alpha |
| vp9-lossless | ✅ | 仍需 WebM 且不接受量化 | 体积大；`yuva420p` 有 4:2:0 表示限制 |
| vp8 | ✅ | 明确的旧 WebM 兼容需求 | 非默认：实测 PSNR 比 VP9 低约 7 dB、可见晕环多四个数量级 |
| prores4444 | ✅ | 剪辑/合成母版 | 高码率；8-bit alpha 只降低部分 alpha 成本 |
| png-mov / qtrle / ffv1 | ✅ | 无损交换、QuickTime 遗留流程、归档 | 体积或播放端兼容性成本较高 |
| mp4-black | ❌ | **唯一不透明例外** | 仅用户明确选择；永久丢失 alpha；宽高须为偶数（`yuv420p` 色度抽样要求，预检阶段拒绝） |

常见需求其实只需要第一行：**读取逐帧内容时，最小的那一档就是 VP9 WebM**。剪辑用途通常用原 GIF 本身更划算，不必绕道 ProRes。

HAP Alpha 已从矩阵移除：PyAV wheel 不含 hap encoder，工具如实报缺并停止，不偷换格式（需要 HAP 时请使用含 hap encoder 的外部工具）。

## 关于透明，有两件事值得先知道

**一、alpha 在不在，和看不看得出，是两回事。**

WebM 的 alpha 放在 Matroska `BlockAdditional` 里，容器里的 `AlphaMode=1` 只是一句声明。播放端如果忽略它，你看到的就是**透明像素底下垫着的那层 RGB**——它可能是白的、也可能是黑的，取决于源 GIF 和转换有没有预处理。

所以预览器里的白底或黑底**不是**判断透明的依据。请在真正支持 alpha 的播放端（如 mpv.net，显示透明棋盘格）或浏览器里验证。

**二、默认忠实于素材，黑底要主动开口。**

默认不加任何 RGB 处理，透明区保留源 GIF 原本的底层 RGB。只有当你明确要求「黑底」时才加 `--black`（仅 VP8/VP9 链路）：透明区 RGB 归黑。两者 alpha 掩码完全相同，只是前者在忽略 alpha 的播放端显原色、后者显黑。**它不能修复播放器的 alpha 支持。**

## 怎么验证 alpha 真的活下来了

```bash
tgtv verify output.webm --source input.gif
```

一条命令覆盖完整解码、显式 libvpx 解码（VP9/VP8 按容器 codec 选 decoder，不按扩展名猜）、全帧 alpha 扫描、半透明轻重区分、PTS/时长/尺寸/底色/可见区保真；png/qtrle 断言 RGBA 域逐像素一致，ffv1 断言原生 yuva444p 域逐像素一致。退出码 0/1，`--json` 给出全量数字。

旧 ffmpeg 管线时代踩过的三个验证坑（`-v error` 把采样清零、>8bit alpha 要 `format=gray`、首帧关键帧误判）在 NumPy 验证管线里**结构性消失**——不存在日志级别，逐帧显式循环统计，零帧即异常。历史叙述见 `references/verification.md`。

## 边界

一次只处理一个 GIF。不猜背景色，不提供 auto-edge / suggest-background，不缩放、不裁切、不批量。`CRF 30` 是 WebM 侧唯一默认档位——它是逐编码器的相对刻度，调到 CRF 40 会在 alpha 边缘产生大量可见晕环；而黑底 MP4 用的是 x264 CRF 20，两套数值不能类比。

## 维护与回归

完整流程、确认规则和验证规则见 [`SKILL.md`](SKILL.md)；开发回归见 [`tests/README.md`](tests/README.md)；分级参考见 `references/`。

- 新栈回归矩阵（96 项断言，无系统 ffmpeg）：[`test-reports/2026-10-03-matrix-newstack.md`](test-reports/2026-10-03-matrix-newstack.md)
- 迁移期等价性对照（17 组 × 8 维度，tgtv vs ffmpeg 7.0.2 CLI 同参数）：[`test-reports/2026-10-03-pyav-vs-ffmpeg-cli.md`](test-reports/2026-10-03-pyav-vs-ffmpeg-cli.md)
- sizing 数字新栈校准（双栈）：[`test-reports/2026-10-03-sizing-calibration.md`](test-reports/2026-10-03-sizing-calibration.md)
- 旧栈历史基线（Windows BtbN FFmpeg 9.0.1）：[`test-reports/2026-10-02-matrix.md`](test-reports/2026-10-02-matrix.md)

运行时依赖：Python ≥3.11、`av>=18,<19`（PyAV，自带 FFmpeg 库的 wheel）、`numpy>=1.26`。开发附加：pytest、Pillow（夹具）、imageio-ffmpeg（迁移期对照）。

## 许可

MIT
