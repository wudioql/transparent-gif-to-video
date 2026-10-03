# 去系统 ffmpeg 重构：可行性分析与实施规划

- 日期：2026-10-03
- 分支：`arena/01a1006f-transparent-gif-to-video`（基线 `73503b6`）
- 状态：**分析与规划完成，未开始构建**（应要求先出结论，不动运行时代码）
- 证据：[`docs/probes/2026-10-03-pystack-probe.py`](probes/2026-10-03-pystack-probe.py) +
  [`docs/probes/2026-10-03-pystack-probe.output.txt`](probes/2026-10-03-pystack-probe.output.txt)
  （在**无系统 ffmpeg** 的 Linux x86_64 / Python 3.11 沙箱实测，49 PASS / 2 FAIL，两个 FAIL 均为已定性的 HAP 缺口）

---

## 结论摘要（TL;DR）

1. **可行，但不能是"零 ffmpeg"**：VP8/VP9 只有 libvpx 一个编码器实现，ProRes/HAP/qtrle/FFV1/H.264 也只存在于 FFmpeg 的 C 代码里；纯 Python 生态没有任何一个能产出这些格式（更不用说 WebM 的 `BlockAdditional` alpha 机制在 libavcodec 内部完成）。放弃 ffmpeg 等于放弃整个输出格式矩阵。
2. **"无需系统 ffmpeg"有完全成立的解读**：用 pip wheel 携带 FFmpeg。实测两条路都通——
   - **PyAV 18.1.0**（wheel 捆绑 FFmpeg 8.x 代系 + libvpx 12 + libx264）：**9 条路径里 8 条完整闭环**，GIF 解码语义与 ffmpeg CLI 逐项一致（含 disposal 合成数值、变帧时长 PTS、解码器陷阱复现）；
   - **imageio-ffmpeg 0.6.0**（wheel 捆贝 ffmpeg 7.0.2 静态二进制）：README 默认命令与 §4.2 验证命令**原样执行通过**。
   - 两者共同的唯一缺口：**HAP encoder 不在 wheel 里**（PyAV 有 HAP 解码器、无编码器；7.0.2 二进制未编入）。
3. **推荐路线 A2：PyAV 库级管线 + NumPy 验证**，理由见 §4；HAP 缺口的三个处理选项见 §5。
4. 这次重构**反转仓库的一个根本设计决定**（"运行时不使用 Python"），但 skill 的行为不变量（alpha 保真、逐帧时长、不缩放不裁切、`-n` 保护、计划确认流程、缺 encoder 停止不偷换）**全部可以且必须原样保留**——迁移映射表见 §6.3。

---

## 1. 现状分析：这个仓库到底依赖 ffmpeg 做什么

### 1.1 仓库形态

本仓库是**文档型 skill**：没有运行时代码。`SKILL.md` 指导 agent 在目标机器上直接调用系统 `ffmpeg` CLI；`tests/`（make_fixtures.py + run_matrix.py）是开发期回归，不参与运行。运行时唯一依赖被反复强调为"系统 ffmpeg，没有 Python"。

### 1.2 ffmpeg 在运行时承担的六类职责

| # | 职责 | 具体依赖点 |
|---|---|---|
| 1 | GIF demux/decode | `-ignore_loop 1`、局部帧/disposal 2/3 合成、变帧时长 PTS、透明索引 → alpha |
| 2 | 编码器矩阵（8 个） | `libvpx-vp9`、`libvpx`、`prores_ks`、`hap`、`png`、`qtrle`、`ffv1`、`libx264` |
| 3 | 容器复用 | WebM/Matroska（`BlockAdditional` + `AlphaMode=1`）、MOV、MP4（`+faststart`）、MKV |
| 4 | 滤镜链 | `format`、`premultiply=inplace=1:planes=0x7`、`setparams=alpha_mode=straight`（黑底归一化） |
| 5 | 时间戳透传 | `-fps_mode passthrough -enc_time_base demux`，禁固定 `-r` |
| 6 | 验证管线 | 完整解码、`alphaextract,format=gray,signalstats,metadata=print` 全帧 YMIN/YMAX、`showinfo` PTS 提取、rawvideo 逐像素导出 |

### 1.3 必须原样保留的行为不变量（迁移验收基准）

来自 SKILL.md / references / 回归报告的硬规则，任何实现替换都不能变：

1. 默认 VP9 WebM CRF 30，保留 alpha，保留源 GIF 透明区底层 RGB（不预乘）；
2. 黑底归一化仅限用户显式要求，且仅 VP8/VP9 链路（MOV 路径维持"需另行验证"口径）；
3. 逐帧时长原样搬运（变时长不被平均成 CFR）；无限循环只出一个周期；
4. 画布不缩放、不裁切；HAP 要求宽高均为 4 的倍数、MP4 要求宽高均为偶数——不满足就**停止并报可识别错误**，不绕过；
5. 默认拒绝覆盖输出（`-n` 语义），确认后才允许；任何写文件操作先经计划确认；
6. 缺 encoder 就停止，不静默换格式；
7. 验证必须全帧扫描（不只首帧）、断言至少一帧 alpha YMIN<255、半透明像素区分轻重（卡 `32<=alpha<=223` 占比）；
8. 一次一个 GIF；不批量、不图片序列、不背景推断。

---

## 2. 根本约束：零 ffmpeg 与输出格式矩阵不可兼得

### 2.1 为什么"纯 Python、彻底不用 ffmpeg"做不到

- **VP8/VP9**：FFmpeg 自身**没有原生 VP8/VP9 编码器**，只有对 libvpx 的封装。WebM alpha 的实现方式是 libavcodec 的 libvpx 封装在内部**同时编码两条 VPx 流**（RGB 一条、alpha 平面一条），把 alpha 流作为 `AV_PKT_DATA_MATROSKA_BLOCKADDITIONAL` side data 挂包，再由 Matroska muxer 写成 `BlockAdditional` 块并声明 `AlphaMode=1`。纯 Python 生态不存在 VP8/VP9 编码器，也没有等价于这套双流+side data 机制的东西。
- **ProRes 4444 / qtrle / FFV1 / HAP / PNG-in-MOV**：这些编码器都是 FFmpeg 内部的 C 实现，Python 生态没有对应物（HAP 理论上可自研，见 §5.3）。
- **H.264**：有 pip 可装的编码库（如 openh264 系），但只做 YUV420 不透明输出——恰好本 skill 的 MP4 路径本来就是黑底不透明，然而其余 7 条 alpha 路径仍然全部无解。
- **纯 Python 下唯一能保 alpha 的产出**是动画 WebP / APNG / PNG 序列（Pillow），它们不是本 skill 契约里的"视频"，等于改产品定位。

> 结论：**路线 B（零 ffmpeg）= 重新定义产品**，不在本次重构范围内（除非用户明确选择，见 §11 问题 1）。

### 2.2 "无需系统 ffmpeg"的成立解读

"系统 ffmpeg"指需要用户在 OS 层安装、PATH 可见的 `ffmpeg`。把它换成 **pip 依赖自动携带的 FFmpeg**（编译好的库或二进制装进 site-packages），就达成了"只靠 Python 环境及相关依赖工作"：`pip install` 即得、不碰系统包管理器、版本随 pyproject 锁定。两条已验证的携带方式：

| 方式 | 携带物 | 实测版本 |
|---|---|---|
| **PyAV**（`av`） | FFmpeg **库**（libavcodec/format/filter/util/swscale，见 `av.libs/`），进程内 API 调用 | av 18.1.0 = libavcodec 62.28.102（FFmpeg 8.x 代系），含 libvpx 12.0.0、libx264 165 |
| **imageio-ffmpeg** | FFmpeg **静态二进制**，仍走 subprocess | 0.6.0 = ffmpeg 7.0.2（johnvansickle GPL static） |

（`static-ffmpeg` 是第三种：**首次运行时从 GitHub 下载**二进制——不捆在 wheel 里，运行期要网络，沙箱实测下载被拦截，不推荐作默认依赖。）

---

## 3. 实测证据（2026-10-03，无系统 ffmpeg 环境）

全部探测可由 `python docs/probes/2026-10-03-pystack-probe.py` 复现，明细见 [`2026-10-03-pystack-probe.output.txt`](probes/2026-10-03-pystack-probe.output.txt)（注：产物扩展名为 `.output.txt` 而非 `.log`，因仓库 `.gitignore` 忽略 `*.log`）。要点：

### 3.1 PyAV 18.1.0（推荐栈）

| 探测项 | 结果 |
|---|---|
| 8 个必需 encoder | **7/8 可用**（libvpx-vp9、libvpx、prores_ks、png、qtrle、ffv1、libx264）；hap **无编码器**（有解码器） |
| GIF 解码（ignore_loop=1） | 5 帧透明 GIF，PTS `[0, 0.03, 0.04, 0.09, 0.19]` **逐帧保留**；loop=0 素材单周期 |
| disposal 合成 | **与 ffmpeg CLI 回归数值逐项一致**：disposal=1 累积 `[6060, 6860, 7410]`；disposal=2/3 后续帧仅剩 `1600`（对照 test-reports §15） |
| **默认路径闭环**（GIF→VP9 yuva420p WebM→验证） | 显式 libvpx-vp9 解码得 `yuva420p`、alpha_min=0、PTS 逐帧相等；EBML 含 `AlphaMode(0x53C0)` 与 `BlockAdditional(0x75A1)` |
| 解码器陷阱复现 | 不指定 decoder → `yuv420p`（"alpha 看似丢失"的假象）——**文档 §4.2 的坑在 PyAV 下原样存在，知识直接迁移** |
| 其余路径 | VP8 CRF30 / VP9 lossless / ProRes 4444（解出 `yuva444p12le`，与文档一致）/ PNG-MOV / qtrle / FFV1 yuva444p / libx264 MP4 全部编码+解码+alpha 闭环通过 |
| 尺寸约束 | VP9 999×999 成功（WebM 不设偶数约束，与文档一致）；libx264 999×999 yuv420p 被拒（**硬失败保留**，但报错是 `ExternalError` 不含 `width/height not divisible by 2` 文案 → 需预检层生成可读报错） |
| 黑底归一化 filter 链 | `premultiply=inplace=1:planes=0x7` + `setparams=alpha_mode=straight` 在其 FFmpeg 8.x 代系上**完整可用**（透明区 RGB→0，可见区保持） |
| Windows | `av-18.1.0-cp311-abi3-win_amd64.whl` 存在（最新 av 19.0.1 为 cp312-abi3 → Python ≥3.12；18.x 为 ≥3.11） |

### 3.2 imageio-ffmpeg 0.6.0（对照路线）

| 探测项 | 结果 |
|---|---|
| 8 个必需 encoder | 同样 **7/8**，缺 hap |
| README 默认转换命令 | **原样执行 exit=0**（仅替换可执行文件路径） |
| SKILL §4.2 验证命令 | **原样执行**，YMIN 采样 5 帧、min=0，PASS |
| 黑底链 | `setparams=alpha_mode` **在 7.0.2 上不存在**（`Option not found`）——版本兼容缺口；`premultiply` 单独可用且黑底结果正确 |
| Windows | `imageio_ffmpeg-0.6.0-py3-none-win_amd64.whl` 存在 |

### 3.3 证据汇总

```
49 PASS / 2 FAIL（两个 FAIL 均为 hap encoder 缺失，已在 §5 定性）
```

### 3.4 av 19.0.1 静态核验（Python ≥ 3.12 路线的预检，2026-10-03 补充）

沙箱仅有 Python 3.11（av 19.0.1 为 cp312-abi3，装不上），且 GitHub release 资产下载被网络策略拦截（无法引入独立 Python 3.12，见附录 B.3）。因此改用**wheel 解剖 + 字符串标记**做静态核验，覆盖 av 19.0.1 的 Linux 与 Windows 两个 wheel：

| 检查项 | 方法 | 结果 |
|---|---|---|
| 捆绑库 | 解包 wheel 列 `av.libs/` | 双平台均含 **libavcodec 63.1.102（FFmpeg 8.1 代系）**、libvpx 12.1.0、libx264-165、libavfilter 12.1.102 |
| HAP encoder | 先用 av 18.1.0（运行时已确认"有 HAP 解码器、无编码器"）标定 encoder 特有字符串（`hap_alpha` / `hap_q` / `Hap QA`），再对 av 19 的 libavcodec 检查 | **三个 wheel（av18-lin / av19-lin / av19-win）中 encoder 标记均为 0**；解码器标记 `Vidvox Hap` 均在 → **av 19 同样无 HAP 编码器** |
| §3.0 / §4.2 滤镜 | 对 av 19 的 libavfilter 检查 `alpha_mode` / `premultiply` / `inplace` / `alphaextract` / `signalstats` / `YMIN` | 双平台全部在 |

结论：**av 19.0.1 与 18.1.0 的能力面一致**（唯一缺口同为 HAP encoder，编码器/滤镜集合相同）。Python ≥ 3.12 + av 19.x 路线除"运行时复测"外无已知风险；若要求零校准成本，则用 Python ≥ 3.11 + av 18.x（本文件全部结论的运行时实测基线）。

---

## 4. 路线对比与推荐

| | A1：imageio-ffmpeg（CLI 二进制） | **A2：PyAV（库级 API）【推荐】** | B：零 ffmpeg |
|---|---|---|---|
| 形态 | 换二进制路径，命令、文档、坑位知识 1:1 保留 | Python API 重写管线；文档知识保留、命令重述 | 只能改输出格式（WebP/APNG） |
| ffmpeg 版本 | 7.0.2（与文档基线 BtbN 9.0.1 有代差） | 8.x 代系（较近，仍需回归校准） | — |
| HAP | 缺 | 缺（解码器在） | 缺 |
| 黑底链 | `setparams=alpha_mode` 缺失，需改命令或换二进制 | avfilter 完整可用；亦可用 NumPy 等价实现 | NumPy |
| 验证管线 | 沿用 signalstats（含其历史坑） | **NumPy 原生重写**，顺带消灭三个历史坑 | NumPy |
| 错误处理 | 继续解析 stderr 文本 | 结构化异常（但需预检层补可读文案） | — |
| 依赖 | imageio-ffmpeg（GPL 二进制） | av + numpy（av wheel 因含 x264 为 GPL 构建） | pillow |
| 迁移工作量 | 最小 | 中 | 大（且改产品） |
| 与 skill 的交互模型契合 | 高（agent 继续跑命令） | 高（agent 改跑 `python -m tgtv ...`） | — |

**推荐 A2**，理由：

1. 它真正回答了用户的问题——"只用 Python 环境及相关依赖"：进程内调用，无 subprocess、无 stderr 解析、无路径/引号/编码问题（文档里"Windows 路径加双引号"这类坑整类消失）；
2. GIF 解码仍由同一个 libavcodec 完成，disposal/PTS 语义**零漂移**（§3.1 实测与 CLI 数值一致）；
3. 验证管线 NumPy 化能结构性消灭 signalstats 的三个历史坑（§6.4）；
4. 唯一硬缺口 HAP 与 A1 相同，不是 A2 独有损失。

A1 建议保留为**迁移期的对照工具**：回归时用 imageio-ffmpeg 二进制跑旧命令，把新栈产物与旧命令产物做等价性比对（同夹具、同断言），完成迁移后再降级为可选 dev 依赖。

---

## 5. HAP 缺口的三个处理选项

HAP Alpha（`-c:v hap -format hap_alpha`）是 9 条路径中唯一 wheel 覆盖不了的：

| 选项 | 内容 | 代价 |
|---|---|---|
| **5.1 从 Python 栈矩阵中移除 HAP**（推荐默认） | 文档改口径："HAP 需要含 hap encoder 的外部 ffmpeg，Python 栈不提供"；预检探测到请求 HAP 且无能力时停止（遵守"缺 encoder 停止不偷换"） | 用户失去一条小众路径（VJ/实时播放） |
| 5.2 可选外部依赖 | 提供可选 extra（如 `tgtv[hap]`）：`static-ffmpeg` 首次运行下载 BtbN 系二进制（其是否含 hap 未能在本沙箱验证——下载被网络策略拦截）；或允许"若系统恰有含 hap 的 ffmpeg 则用之" | 引入运行期网络依赖或重新依赖系统 ffmpeg，与本次目标相悖 |
| 5.3 纯 Python 自研 HAP 编码器 | hap_alpha = RGB→DXT1 + alpha→DXT5 + snappy 分块 + 自写 MOV 封装（cramjam 提供 snappy wheel；DXT 编码可用 NumPy 实现 4×4 块端点拟合；本仓库已有手写 GIF 编码器的先例与文化） | 一个货真价实的子项目；朴素 DXT 编码质量待验证；且 HAP 本来就要求"在目标 VJ/剪辑软件实测"，验证成本高 |

建议 Phase 0 先按 5.1 定版，5.3 作为远期可选阶段（见 §7 Phase 8）。

---

## 6. 目标架构设计（路线 A2）

### 6.1 形态与依赖

```
运行时依赖：av（pin >=18,<19 起步）、numpy        # 全部有 win_amd64 wheel
开发依赖  ：pillow（夹具生成）、pytest（可选）
Python    ：>= 3.11（av 18.x 为 cp311-abi3；若需支持更低 Python，须降 av 版本并接受更旧的内置 FFmpeg）
```

仓库从"纯文档 skill"变为"Python 包 + skill 文档"：`pyproject.toml` + `src/` 布局，CLI 入口（示意名 `tgtv`，最终名待 Phase 0 定）。

### 6.2 模块划分（示意）

```
src/tgtv/
  cli.py        # 计划展示 → 确认 → 执行；-n/--overwrite 语义；错误文案
  probe.py      # 能力探测：encoder 可用性、尺寸约束预检（替代 ffmpeg -encoders 预检）
  source.py     # GifSource：av.open(options={"ignore_loop":"1"})，逐帧 pts/尺寸/帧数/loop 元数据
  writers/
    base.py     # 输出存在性保护、pts 透传（time_base=源 demuxer）、缺 encoder 即停
    vp9.py  vp8.py  prores.py  pngmov.py  qtrle.py  ffv1.py  x264.py  (hap.py?)
  filters.py    # 黑底归一化（NumPy，仅 VP8/VP9）
  verify.py     # §6.4 全套断言
tests/
  make_fixtures.py   # 保留；validate_handcrafted() 从调系统 ffmpeg 改为 PyAV 判定
  run_matrix.py      # 断言不变，执行层换新栈；迁移期增加与 imageio-ffmpeg 旧命令的等价性对照
docs/probes/         # 本次分析证据（已入库）
```

### 6.3 现有命令语义 → 新实现映射表（不变量保真的关键）

| 现有 ffmpeg 语义 | 新实现 | 实测依据 |
|---|---|---|
| `-ignore_loop 1`（必须显式，虽然 demuxer 默认 true） | `av.open(gif, options={"ignore_loop": "1"})`，显式传，不依赖默认值 | C1/C3 |
| `-fps_mode passthrough -enc_time_base demux`、禁 `-r` | writer 的 `time_base` 取源 demuxer（1/100），`frame.pts` 原样透传，绝不重采样 | D3：PTS 逐帧相等 |
| `-map 0:v:0 -an` | 只解 video 流（GIF 本无音频） | — |
| `-n` / 确认后 `-y` | 输出存在性预检：默认拒绝并退出；用户确认该具体路径后才允许覆盖 | — |
| `-auto-alt-ref 0`（alpha 必需）、`-b:v 0 -crf 30 …` | `cc.options` 字典原样传 libvpx 私有选项 | D1/E 组 |
| HAP 宽高 %4、MP4 宽高偶数 → 停止并报原文案 | probe.py 预检，输出文档化的错误文案（libx264 底层报错是裸 ExternalError，**必须**前置校验） | F2 |
| 缺 encoder 停止不偷换 | probe.py 探测（`CodecContext.create(name,"w")`），缺失即报"该 wheel 未提供 encoder" | B 组 hap |
| 黑底 `-vf format=rgba,premultiply…,setparams…` | 首选 NumPy：二值 alpha 下 `RGB[a==0]=0`，语义等价、可逐像素断言；备选 avfilter 图（已验证可用） | G1 |
| `alphaextract` + 显式 `-c:v libvpx-vp9` 解码 | verify.py：解码时显式 `CodecContext.create("libvpx-vp9","r")`（陷阱依旧存在，规则保留） | D2/D5 |
| Windows 路径双引号/括号/中文 | 整类消失（无 shell 拼接）；特殊路径夹具继续回归 | 回归分组 8 |

### 6.4 验证管线 NumPy 化（结构性消灭三个历史坑）

| signalstats 时代的历史坑 | NumPy 实现后 |
|---|---|
| `-v error` 把采样数清零、断言静默跳过 | 不存在日志级别问题；循环解码逐帧统计，零帧=显式异常 |
| >8bit alpha（`yuva444p12le`）需 `format=gray` 归一 | `to_ndarray(format="rgba")` 统一归一到 8 位域 |
| 只看首帧会误判（首帧关键帧天然干净） | 逐帧统计 alpha_min/半透明分布/可见区 RGB 差/PSNR，首中末覆盖由循环结构保证 |
| metadata=print 的 stderr 正则解析 | 直接拿数值 |

断言集直接沿用 run_matrix.py 现有口径：`YMIN<255` 至少一帧、半透明区分轻重（卡 `32<=a<=223` 占比 <0.5%）、PTS 逐帧一致、尺寸不变、透明区底层 RGB 保源色（默认）/归黑（黑底）、可见区 RGB 无剧烈失真。

### 6.5 建议明确"不迁移"的东西

- Pillow 不进运行时依赖（PyAV 解 GIF 语义与 CLI 一致；Pillow 只留开发侧造夹具）；
- 不引入图片序列、批量、背景推断（这些禁令与本重构正交，继续有效）；
- 不做 `auto-edge`/`suggest-background` 的复活。

---

## 7. 分阶段实施计划（每阶段可独立验收）

| Phase | 内容 | 验收标准 |
|---|---|---|
| **0 决策固化** | 回答 §11 四个问题：路线确认、HAP 策略、包名/CLI 形态、Python 下限 | 决策记录写入本文件附录 |
| **1 包骨架** | pyproject（av pin、numpy、dev extras）、src 布局、CLI 入口骨架、probe.py 能力探测 | 在无系统 ffmpeg 的干净 venv 里 `pip install -e .` 成功；probe 能报告 8 encoder 可用性（hap 如实报缺） |
| **2 GIF 读取层** | source.py；make_fixtures 的 `validate_handcrafted()` 改用 PyAV | 现有全部夹具（含手写 GIF、变时长、无限循环、disposal 2/3、局部帧、奇数尺寸、特殊路径）在新读取层下帧数/PTS/合成数值与回归报告一致 |
| **3 编写器矩阵** | writers 8 条路径 + base 预检/存在性保护/pts 透传；黑底 MP4 直出 | 每条路径小夹具转换成功且 §4 断言通过；HAP 尺寸/MP4 偶数预检产出文档化错误文案；输出已存在时默认拒绝 |
| **4 黑底归一化** | filters.py（NumPy，仅 VP8/VP9） | 黑底路径五项断言：alpha 掩码不变、透明区 RGB=0、可见区不变、浏览器透明、显式 libvpx 解码 yuva420p；默认路径断言透明区保源色 |
| **5 验证模块** | verify.py 全套 | 对已知"坏产物"（如 CRF40 或 VP8 输出）能按预期报警，对好产物全绿 |
| **6 回归迁移** | run_matrix.py 执行层换新栈；迁移期对照：同夹具下新栈产物 vs imageio-ffmpeg 旧命令产物等价性比对 | 原 59 项断言全绿；等价性对照（alpha 掩码、PTS、尺寸一致；有损路径指标在同档）记录进新的 test-reports |
| **7 质量口径校准** | 在新栈重测 sizing.md 关键数字（VP9 CRF30 PSNR/半透明、VP8 差距、CRF40 反例、体积占比） | sizing.md 数字更新或加注"基线为 PyAV 18.1.0 / libavcodec 62.28" |
| **8 文档重写** | SKILL.md、README、references×5、tests/README 全量口径反转 | 见 §9 清单逐项过检；SKILL.md 的 agent 指令改为调用新 CLI 并保留计划确认流程 |
| （远期可选）HAP 自研 | §5.3 | 另立评估 |

顺序依据：2→3 是主链路；4、5 可与 3 并行；6 依赖 2–5；7、8 收尾。总体量级估计：核心代码 ~600–900 行 + 测试改造，属于"一个中等 PR 序列"，不是小补丁。

---

## 8. 风险登记表

| 风险 | 等级 | 缓解 |
|---|---|---|
| PyAV 内置 FFmpeg 随版本漂移（升级 av=升级 ffmpeg，行为可能变） | 高 | pyproject pin av 区间；probe.py 启动自检能力与关键行为 |
| PyAV API 变化（本次探测就遇到 `add_stream` 签名与 encoder 刷新两种版本差异） | 中 | pin + writers/base 隔离封装；回归兜底 |
| HAP 缺失 | 中 | §5 决策；预检如实报缺并停止 |
| 尺寸/约束报错文案与文档不一致（ExternalError 无原文案） | 低 | probe.py 预检生成文档化文案（SKILL 本就要求预检） |
| NumPy 黑底与 premultiply filter 的细微差异（非二值 alpha 素材） | 低 | skill 输入假设即二值 alpha；filters 单测 + 文档保持"仅 VP8/VP9、MOV 需另行验证"口径 |
| 质量"实测数字"基于 BtbN 9.0.1，libvpx 版本不同可能有偏差 | 中 | Phase 7 重测校准 |
| GPL 组件：av wheel 含 x264（GPL 构建）、imageio-ffmpeg 为 GPL 二进制 | 低（不 vendor 则无传染） | 见 §10 |
| Python 下限：av 18.x 需 ≥3.11、19.x 需 ≥3.12；旧 Python 只能配旧 av（更旧 FFmpeg） | 中 | Phase 0 定下限；文档明示 |
| PyAV demux GIF 的 packet 粒度与 CLI 不同（存在头包，packet 计数≠帧数） | 低 | 统一走 `decode()` 计帧（探测已确认） |
| 目标机器网络受限时 `pip install` 需离线方案 | 低 | wheel 均可 `pip download` 离线安装 |

---

## 9. 文档迁移清单（Phase 8 的检查表）

| 文件 | 动作 |
|---|---|
| `SKILL.md` | 前置声明从"唯一依赖系统 ffmpeg、不恢复 Python"反转为"唯一依赖 Python（av+numpy），不依赖系统 ffmpeg"；§1 预检改为 CLI 能力探测；§3 命令模板改为 CLI 调用示例；§4 验证改为 `tgtv verify`；所有"实测于 BtbN 9.0.1"的表述加注或改基线 |
| `README.md` | 30 秒上手改为 pip install + 一条 CLI；"运行时依赖只有系统 ffmpeg"改为"只有 Python 栈" |
| `references/architecture.md` | 运行模型图改为 Python 管线；"不使用 Python/Pillow/NumPy"改为"不使用 Pillow（运行时）" |
| `references/pitfalls.md` | 保留 alpha/解码器陷阱/尺寸规则；删除 CLI 特有条目（`-v error`、路径引号）；新增 PyAV 特有条目（ExternalError、packet 计数） |
| `references/sizing.md` | 数字按 Phase 7 校准或加注 |
| `references/verification.md` | §4.2 命令式验证改为验证模块说明，三个历史坑改述为"已由 NumPy 管线结构性消除" |
| `tests/README.md` | 开发依赖与运行时依赖合并叙述；validate_handcrafted 的裁判从系统 ffmpeg 改为 PyAV |
| `test-reports/` | 新增 2026-10-XX 新栈回归报告，旧报告保留为历史基线 |

---

## 10. 许可与分发注意

- 本仓库 MIT。`av` 本体 LGPL，但其 wheel 捆绑 x264（GPL 构建）；`imageio-ffmpeg` 捆 GPL 静态二进制。**作为 pip 依赖引用、不 vendor 进仓库**时，仓库许可不受影响；若未来直接分发二进制或 vendor 库文件，需按 GPL 合规处理（内部使用无碍）。
- 夹具与产物仍走 `.gitignore` 现有规则，不入库。

---

## 11. 待决策问题（Phase 0 输入）

1. **"无需系统 ffmpeg"的严格度**：接受 pip wheel 捆带的 FFmpeg（本分析的前提，路线 A2），还是必须彻底零 ffmpeg（路线 B，只能改输出格式矩阵为 WebP/APNG）？
2. **HAP 策略**：§5 的 5.1（移除，推荐）/ 5.2（可选外部依赖）/ 5.3（纯 Python 自研）？
3. **形态**：Python 包 + CLI（`tgtv convert / verify`）+ 重写后的 SKILL.md 指导 agent 调 CLI——是否符合预期？还是希望保留"agent 直接跑命令"的纯文档形态（那就只剩路线 A1）？
4. **Python 下限**：3.11（配 av 18.x）是否可接受？目标机器的 Python 版本是什么？

---

## 附录 A：Phase 0 决策记录（2026-10-03，用户已确认）

| 决策点 | 结论 | 影响 |
|---|---|---|
| 路线 | **A2：PyAV（pip wheel 捆带 FFmpeg）+ NumPy 验证**，接受 wheel 携带的 FFmpeg，彻底放弃系统 ffmpeg 依赖 | §4 推荐获批；路线 A1（imageio-ffmpeg）降级为迁移期对照工具 |
| HAP | **从 Python 栈矩阵移除**（§5.1） | 输出矩阵由 9 条变 8 条；`probe.py` 对 HAP 请求如实报缺并停止（维持"缺 encoder 停止不偷换"）；纯 Python 自研（§5.3）列为远期可选项 |
| 形态 | **Python 包 + CLI + 重写 SKILL.md**（§6 架构获批） | 仓库从纯文档 skill 变为"Python 包 + skill 文档"；SKILL.md 的 agent 指令改为调用 CLI，计划确认/`-n` 语义等不变量落在 CLI 层 |
| Python 下限 | **≥ 3.12（av 19.x 代系）** | pyproject `requires-python >= 3.12`、`av >= 19,< 20` 起步 |

**遗留校准项（进入 Phase 1 的首个任务）**：本文全部实测基于 av 18.1.0 / Python 3.11（沙箱限制：无 3.12，GitHub release 下载被网络策略拦截，无法在沙箱装 3.12）。Phase 1 须在 Python 3.12 + av 19.0.1 上重跑 `docs/probes/2026-10-03-pystack-probe.py` 并核对：8 encoder 可用性（重点确认 hap 仍缺、其余不缺）、`setparams=alpha_mode`、尺寸约束行为。探测脚本已入库，可直接复用；如有差异，回写本文件的 §3 证据表。

---

## 附录 B：会话事件与版本出入记录（2026-10-03）

1. **沙箱重置事件**：决策问答暂停期间，工作区被平台重新置备（reflog 显示 2026-10-03 07:04:51 重新 clone），此前的 commit 与 `/tmp`（含探测用 venv）被清空；`docs/` 分析产物以未跟踪文件形式完整保留（含用户补充的附录 A）。恢复后已重新提交，证据脚本与日志无缺失。
2. **Python 下限的一处出入**：决策 UI 的选择为 **≥ 3.11**，而附录 A（用户直接写入本文件的决策记录）为 **≥ 3.12（av 19.x）**。经 §3.4 静态核验，两条版本线能力面一致（编码器/滤镜集合相同、均无 HAP encoder），**该出入不影响架构与计划内容，仅影响 pyproject 的两个数字**。定版规则：
   - Phase 1 若能在目标机器上用 Python 3.12 + av 19.0.1 重跑探测脚本并全绿 → 按附录 A 定版 `requires-python >= 3.12`、`av >= 19, < 20`；
   - 若要求零校准成本、直接沿用本文全部运行时实测 → 定版 `requires-python >= 3.11`、`av >= 18, < 19`。
   - 差异本质只是"基线数字来自静态核验还是运行时实测"。
3. **网络环境备注**：github.com 主站可达（HTTP 200），但 release 资产域名（objects.githubusercontent.com）在 SSL 层被拦截——这是 static-ffmpeg 探测失败与沙箱无法引入 Python 3.12 的共同原因，不影响 PyPI wheel 路线（§3.4 的 wheel 下载全部来自 PyPI）。

---

## 附录 C：实施进度

| Phase | 状态 | 备注 |
|---|---|---|
| 0 决策固化 | ✅ 2026-10-03 | 附录 A |
| 1 包骨架 + 能力探测 | ✅ 2026-10-03 | 验收记录见下 |
| 2 GIF 读取层（source.py） | ✅ 2026-10-03 | 验收记录见下 |
| 3 编写器矩阵（writers/） | ✅ 2026-10-03 | 验收记录见下（实现简化为注册表驱动的单一通用 writer） |
| 4 黑底归一化（filters.py） | ✅ 2026-10-03 | 验收记录见下 |
| 5 验证模块（verify.py） | ✅ 2026-10-03 | 验收记录见下（顺带发现并修复末帧时长回归） |
| 6 回归迁移（run_matrix.py） | ✅ 2026-10-03 | 验收记录见下（96 项断言全绿 + 17 组等价性对照通过） |
| 7 质量口径校准（sizing 数字） | ⬜ | |
| 8 文档重写（SKILL/README/references） | ⬜ | |

### Phase 1 验收记录（2026-10-03）

交付物：

- `pyproject.toml`：hatchling 构建；包名/命令名 `tgtv`（Phase 0 未定名前的默认，改名成本为零）；`[project.scripts] tgtv = "tgtv.cli:main"`；dev extras = pytest + pillow（夹具）+ imageio-ffmpeg（Phase 6 迁移期对照工具）。
- `src/tgtv/formats.py`：格式注册表（单一事实来源）。8 条受支持路径 + hap（已移除），`writer_options`/`muxer_options` 与旧 SKILL.md §3 命令模板逐项对应。
- `src/tgtv/probe.py`：能力探测（只读）。用 writer 的确切配置逐条 open 验证 encoder（probe 通过 ≈ writer 可用）；验证解码器（libvpx-vp9/libvpx/gif）；尺寸约束预检（mp4 偶数、hap 4 倍数）；环境信息（av 版本、内置 FFmpeg 库、avfilter、系统 ffmpeg 仅为诊断字段）。
- `src/tgtv/cli.py`：`tgtv probe [--json] [--require KEY...] [--size WxH]`。退出码：0 通过 / 1 预检失败 / 2 参数错误。
- `tests/test_probe.py`：24 项验收测试。

版本定版（按附录 B.2 零校准规则）：`requires-python >= 3.11`、`av >= 18,<19`、`numpy >= 1.26`。升级 av 19 前须在目标机重跑 `docs/probes/2026-10-03-pystack-probe.py`（§3.4 已静态核验能力面一致）。

验收结果（无系统 ffmpeg 的 Linux 沙箱）：

1. 干净 venv `pip install -e .` 成功；纯运行时安装仅引入 av + numpy（无 pillow/pytest/imageio），`tgtv probe --require vp9` 退出码 0；
2. `tgtv probe` 报告 **8/8 受支持路径可用**（ffv1 的 level/coder/context/g/slicecrc、prores 的 profile 4444/alpha_bits 8、vp9 全套 CRF 选项均在 open 阶段验证通过）；hap 如实报「已移除：wheel 不含 hap encoder」，`--require hap` 退出码 1，不偷换格式；
3. 尺寸约束：`--require mp4-black --size 999x999` 拒绝并输出文档化文案（宽 999 不是偶数……）；`--require vp9 --size 999x999` 通过（WebM 侧无偶数约束）；
4. `pytest tests/test_probe.py` 24/24 通过。

### Phase 2 验收记录（2026-10-03）

交付物：

- `src/tgtv/source.py`：`GifSource`（输入校验：按内容探测必须是 GIF）、`GifFrame`（原始 pts + 原始 VideoFrame + 懒缓存 rgba）、`GifInfo`（只读全帧扫描：尺寸/帧数/PTS 表/总时长/alpha_min）。语义映射：`ignore_loop=1` 显式传入；`iter_frames()` 每次调用独立打开输入（info 扫描与编码迭代互不干扰）；disposal/局部帧合成由 libavcodec 原生完成（与 CLI 同源）。
- `tests/make_fixtures.py`：`validate_handcrafted()` 裁判改为「优先 tgtv 读取层、回退系统 ffmpeg」，手写 GIF 夹具在无系统 ffmpeg 环境可自检。
- `tests/test_source.py`：16 项验收测试。

验收结果（无系统 ffmpeg 沙箱，基准 = 2026-10-02 ffmpeg CLI 回归数值）：

1. 变帧时长 10/30/50/100/200ms → PTS `(0, 0.01, 0.04, 0.09, 0.19)`、总时长 0.39s，**未被平均成 CFR**；
2. 无限循环 GIF 只出一个周期（4 帧 / 0.4s）；
3. 局部帧合成与 CLI 回归数值**逐项一致**：disposal=1 累积 `[6060, 6860, 7410]`，disposal=2/3 首帧全幅后仅剩 `[1600, 1600]`，且两组确实不同（证明 disposal 真正生效）；
4. 首帧不透明素材：首帧 alpha_min=255（单帧会误判），`info` 全帧扫描 alpha_min=0——**首帧误判坑被结构性消灭**；
5. 奇数尺寸 999×999 原样读取；含空格/中文/括号路径正常；透明区底层 RGB 保持源 GIF 白色（未被预乘）；
6. 输入校验：PNG 字节冠 .gif 名被拒（按内容探测）、垃圾字节被拒、缺文件报可读错误；
7. `pytest tests/` 40/40 通过（Phase 1 + Phase 2 合计）。

### Phase 3+4 验收记录（2026-10-03）

交付物：

- `src/tgtv/verify.py`：`verify(output, source=None, expected_black=False) → VerifyReport` + `render_report`。规则全集 = 旧 SKILL.md §4：完整解码；VP9/VP8 **显式 libvpx 解码**（`EXPLICIT_DECODERS`，不按扩展名猜——§4.2 陷阱结构性规避）；像素格式确有 alpha 平面；全帧扫描 alpha_min<255（首帧不透明素材也能发现透明）；半透明轻重区分（32–223 可见晕环占比 <0.5%，1–31/224–254 轻微取整忽略）；提供源时：帧数/尺寸/PTS 逐帧/总时长一致、底色策略（默认「透明区 RGB 与源一致」= 意外预乘检测器；`expected_black`/mp4-black 归黑）、可见区 MAD/PSNR；png-mov/qtrle RGBA 域逐像素一致；ffv1 yuva444p **原生域**逐像素一致（按平面提取，规避 PyAV `to_ndarray` 不支持 planar yuva 的问题）。格式身份按容器 codec + 编码器→解码器名映射（libx264→h264 等）识别，不用扩展名。
- `src/tgtv/cli.py`：`tgtv verify <output> [--source GIF] [--black] [--json]`，退出码 0/1；convert 完成提示改为可直接复制的 verify 命令。
- `tests/test_verify.py`：19 项；`tests/test_convert.py` 增末帧时长回归 2 项。

验收结果（89/89 = Phase 1–5 合计）：

1. **正路径**：vp9 默认 11/11 通过（decoder=libvpx-vp9、yuva420p、全帧 alpha_min=0、PTS/时长/底色/保真全过）；vp9-lossless/vp8/prores4444/png-mov/qtrle/ffv1 全部带源通过；png-mov 含「RGBA 域逐像素一致」、ffv1 含「yuva444p 原生域逐像素一致」专项。
2. **底色语义双向**：`--black` 产物 + `--black` 期望通过（透明区均值 0.1、可见区 MAD 0.60）；**同一产物不带 `--black` 则失败**并在 detail 提示修正期望——这正是 2026-10-02 报告 §9 out.webm 意外预乘 bug 的自动检测器；mp4-black 通过（无 alpha 判为预期、透明区黑、不适用项自动跳过）。
3. **无源模式**：仅做输出自身断言（完整解码/解码器/alpha），不含比对项。
4. **CLI**：通过 exit 0、失败 exit 1、`--json` 可解析且含 stats 全量数字。
5. **顺带发现并修复真回归——末帧时长**：`tgtv verify` 的「总时长与源一致」发现 3×100ms 素材的 WebM 容器 Duration=0.21s（CLI 基准 0.30s）。根因：libvpx/x264 将帧缓冲到 flush 才吐包且包 duration=0，Matroska 的 BlockDuration/Duration 元素只记末帧 pts+1tick。修复：convert 按 pts 建「源帧→duration」映射，mux 前写回包上（转换中循环内拿不到包，此前对 packet 的赋值从未执行）。修复后 WebM/MP4 容器时长与源一致（0.300s），修复前产物被 verify 精确报警（"输出 0.210s / 源 0.300s"）。回归测试 `test_last_frame_duration_preserved` 钉住。


**设计简化**：原计划的 `writers/` 八个模块收敛为 `convert.py` 一个**注册表驱动的通用 writer**——8 条路径的执行流程完全同构（add_stream → 配置 → 逐帧 reformat + pts 透传 → encode/mux → flush），差异全部落在 `formats.py` 注册表字段上；Phase 1 的 probe 用同一份配置做过 open 验证，「probe 通过 ⇒ writer 可用」不漂移。

交付物：

- `src/tgtv/convert.py`：`build_plan`（只读预检：格式存在/尺寸约束/黑底链路边界/encoder 可用/输出路径，全过才成计划）、`render_plan`（输入/格式/关键参数/输出/底色策略/覆盖行为）、`execute`（-n 覆盖保护、逐帧 pts 透传、帧数一致性校验、失败保留可读错误且输出视为可能不完整）。
- `src/tgtv/filters.py`（Phase 4）：`premultiply_rgb`（uint16 中间量，a=255 逐像素精确、a=0 归零）等价旧 `premultiply=inplace=1:planes=0x7`；**有意不实现** `setparams=alpha_mode=straight`（仅帧元数据，Matroska `AlphaMode=1` 由复用器自动声明；差异以像素级回归覆盖）。黑底仅限 VP8/VP9 链路（`BLACK_ALLOWED_KEYS`），mp4-black 的「合成到黑」复用同一函数。
- `src/tgtv/cli.py`：`tgtv convert <input> [-f KEY] [-o PATH] [--black] [--overwrite] [--yes] [--dry-run] [--json]`。**确认闸**：交互终端提示 yes；非交互（含管道）必须 `--yes`；**覆盖保护前移**：输出已存在且无 `--overwrite` 时在确认闸之前即拒绝（确认执行计划 ≠ 确认覆盖，两件事分开确认——对应旧 `-n`/`-y` 语义）。
- `tests/test_convert.py`：28 项验收测试。

验收结果（无系统 ffmpeg 沙箱，68/68 = Phase 1–4 合计）：

1. **8/8 路径转换闭环**：VP9（默认）/ VP9 lossless / VP8 显式 libvpx 解码均得 `yuva420p` 且 alpha_min=0；ProRes 4444 解码 `yuva444p12le`（与旧栈一致）；PNG-in-MOV 在 RGBA 域**逐像素无损**（`np.array_equal`）；qtrle `argb`、FFV1 `yuva444p`、黑底 MP4 `yuv420p`（无 alpha，预期）全部符合。
2. **PTS 透传**：变时长 10/30/50/100/200ms 转换后仍为 `[0, 0.01, 0.04, 0.09, 0.19]`，未被平均。
3. **底色语义**：默认路径透明区保持源 GIF 白色（>245）；`--black` 透明区 RGB≈0、alpha 阈值化一致 >99%、可见区均差 <8；mp4-black 透明区呈现黑（<20）、可见区不劣化。
4. **硬边界**：hap 请求报「已移除」不偷换；未知格式报错；黑底请求 prores 报「仅支持 vp9/vp9-lossless/vp8」；999×999 的 mp4-black 在**预检阶段**拒绝且零文件产出；999×999 的 vp9 正常且画布不变。
5. **覆盖/确认不变量**：已存在输出默认拒绝且原文件未被触碰；`--overwrite` 明确确认后成功；输出=输入拒绝；`--dry-run` 零写入；非交互无 `--yes` 拒绝（含管道喂 yes）；交互 yes/no 路径正确。

### Phase 5 验收记录（2026-10-03）

交付物：

- `src/tgtv/verify.py`：`verify(output, source=None, expected_black=False) → VerifyReport` + `render_report`。规则全集 = 旧 SKILL.md §4：完整解码；VP9/VP8 **显式 libvpx 解码**（`EXPLICIT_DECODERS`，不按扩展名猜——§4.2 陷阱结构性规避）；像素格式确有 alpha 平面；全帧扫描 alpha_min<255（首帧不透明素材也能发现透明）；半透明轻重区分（32–223 可见晕环占比 <0.5%，1–31/224–254 轻微取整忽略）；提供源时：帧数/尺寸/PTS 逐帧/总时长一致、底色策略（默认「透明区 RGB 与源一致」= 意外预乘检测器；`expected_black`/mp4-black 归黑）、可见区 MAD/PSNR；png-mov/qtrle RGBA 域逐像素一致；ffv1 yuva444p **原生域**逐像素一致（按平面提取，规避 PyAV `to_ndarray` 不支持 planar yuva 的问题）。格式身份按容器 codec + 编码器→解码器名映射（libx264→h264、libvpx-vp9→vp9、libvpx→vp8、prores_ks→prores）识别，不用扩展名。
- `src/tgtv/cli.py`：`tgtv verify <output> [--source GIF] [--black] [--json]`，退出码 0/1；convert 完成提示改为可直接复制的 verify 命令。
- `tests/test_verify.py`：19 项；`tests/test_convert.py` 增末帧时长回归 2 项。

验收结果（89/89 = Phase 1–5 合计）：

1. **正路径**：vp9 默认 11/11 通过（decoder=libvpx-vp9、yuva420p、全帧 alpha_min=0、PTS/时长/底色/保真全过）；vp9-lossless/vp8/prores4444/png-mov/qtrle/ffv1 全部带源通过；png-mov 含「RGBA 域逐像素一致」、ffv1 含「yuva444p 原生域逐像素一致」专项。
2. **底色语义双向**：`--black` 产物 + `--black` 期望通过（透明区均值 0.1、可见区 MAD 0.60）；**同一产物不带 `--black` 则失败**并在 detail 提示修正期望——这正是 2026-10-02 报告 §9 out.webm 意外预乘 bug 的自动检测器；mp4-black 通过（无 alpha 判为预期、透明区黑、不适用项自动跳过）。
3. **无源模式**：仅做输出自身断言（完整解码/解码器/alpha），不含比对项。
4. **CLI**：通过 exit 0、失败 exit 1、`--json` 可解析且含 stats 全量数字。
5. **顺带发现并修复真回归——末帧时长**：`tgtv verify` 的「总时长与源一致」发现 3×100ms 素材的 WebM 容器 Duration=0.21s（ffmpeg CLI 基准 0.30s）。根因：libvpx/x264 将帧缓冲到 flush 才吐包且包 duration=0，Matroska 的 BlockDuration/容器 Duration 只记到末帧 pts+1tick；且转换循环内拿不到包，此前对 packet 的赋值从未执行。修复：convert 按 pts 建「源帧→duration」映射，在 mux 前写回包上（帧侧同时设置 frame.duration）。修复后 WebM/MP4 容器时长与源一致（0.300s），修复前产物被 verify 精确报警（"输出 0.210s / 源 0.300s"）。回归测试 `test_last_frame_duration_preserved`（webm+mp4）钉住。

### Phase 6 验收记录（2026-10-03）

交付物：

- `tests/run_matrix.py`（重写，执行层换新栈）：断言分组与口径沿用旧 ffmpeg CLI 版（旧版见 git 历史），执行层 = tgtv 包本身，**不再依赖系统 ffmpeg**。对照组改写为新读取层上的等价形式：默认 options 单周期对照；`ignore_loop=0` 危险对照改为帧数上限法（读到 >1 周期即证，不再依赖 8 秒超时挂起）。逐项检查直接复用 `tgtv verify` 的 Check 列表（fold_verify）。HAP 组随 Phase 0 决策下线；「MP4 省略 format=yuv420p 静默产出 yuv444p」的危险对照被预检结构性消灭（记录为 INFO）。
- `tests/cross_check_vs_ffmpeg.py`（新增，迁移期对照）：CLI 命令由 `formats.FORMATS` 的 writer_options/muxer_options **逐项生成**——两栈编码参数相同、唯一差异是执行引擎（ffmpeg 7.0.2 CLI 管线 vs PyAV 18.1.0 管线）。17 组（small5×8 格式 + binary×{vp9, vp9 --black, mp4-black} + vardur/partial_real/first_opaque/edge/odd_999/disposal2×vp9），每组 8 个维度：帧数/画布/PTS/容器时长/解码像素格式/alpha 阈值化一致/可见区与透明区 RGB 同档；逐像素精确断言沿用 verify.py 的域规则（png/qtrle RGBA 域、ffv1 yuva444p 原生域；vp9-lossless/prores4444 因表示域限制只做同档指标——这正是域规则的体现，见 formats.py note）。
- `test-reports/2026-10-03-pyav-vs-ffmpeg-cli.md`：对照报告（基线 ffmpeg 7.0.2-static，imageio-ffmpeg 捆带）。
- `src/tgtv/verify.py`：`THRESH_TRANS_KEPT_MAD` 12→40（依据见下）；stats 新增 `transparent_rgb_mean`。

验收结果：

1. **run_matrix 新栈版 96 通过 / 0 失败 / 2 说明**（旧 59 项断言的超集——展开 verify 逐项检查后为 96 项）。
2. **等价性对照 17/17 组、137/137 项通过**：全部夹具 × 全部格式下，两栈产物的帧数/画布/PTS/时长/像素格式完全一致；有损路径 alpha 阈值化一致 ≥99.86%、可见区 MAD ≤0.57、透明区 MAD ≤2.74（同参数同档）；png-mov/qtrle 两产物均对源 RGBA 逐像素一致；ffv1 两产物 yuva444p 原生域逐像素一致。
3. **阈值校准（唯一发现的口径问题，非回归）**：VP9 CRF30 在 disposal2/3 夹具上透明区 RGB 有 15–18 的固有漂移（CLI 18.45/15.02 vs tgtv 15.83/15.92，两栈同档），Phase 5 定的 `THRESH_TRANS_KEPT_MAD=12` 在这类夹具上会误报；意外预乘的真实量级 ≥100（白底拉黑），故阈值调整为 40，兼顾两头。
4. `pytest tests/` 89/89 通过（Phase 1–5 回归不受影响）。

---

*本文件为分析规划产物（Phase 1 起兼作实施进度记录）；`docs/probes/` 为可复现证据，`src/tgtv/` 为逐步落地的运行时实现。*
