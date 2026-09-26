# 实现说明与维护边界

本文件记录实现层的理由和维护原则；执行时优先使用 `SKILL.md` 和脚本入口。

## 1. 为什么使用统一脚本

转换、检查和校验必须共享同一套参数和数据模型。将命令散落在文档中会产生以下风险：

- GIF disposal 和逐帧 duration 被忽略；
- 尺寸、帧数和时基由人工填写；
- VP9 alpha 的像素格式或 `auto-alt-ref` 参数遗漏；
- 转换命令与校验命令不一致；
- 时间基被 ffmpeg 静默量化而没人发现；
- 临时文件或失败输出没有清理。

因此 `scripts/transparent_gif_to_video.py` 是唯一操作入口，文档不维护第二套转换命令。

## 2. 转换流水线

```text
输入素材
  → Pillow 逐帧读取并处理 GIF disposal
  → 统计 alpha 与透明边缘色
  → 透明模式保留 RGBA；不透明模式先 alpha composite
  → 写入临时 PNG 帧和 ffconcat 清单
  → 以毫秒 timebase 生成显式 PTS
  → ffmpeg 编码
  → ffprobe + 实际解码校验
```

临时 PNG 的作用是统一 GIF、APNG、静态 PNG 和图片序列的输入行为，同时让背景合成和 alpha 统计使用同一组帧。

## 3. 时间轴（本 skill 最容易出错的部分）

两个独立的时间基都会背刺你：

1. **concat demuxer** 对图片列表硬编码 1/25 时间基，30ms 会被量化成 40ms。
2. **编码器** 的默认时间基同样是 1/25。只在 filter 里写 `settb=1/1000` 无效 —— 时间戳会在编码阶段被再次量化，PTS 碰撞后 `-fps_mode vfr` 还会把"重复"帧丢掉。一段 95 帧 / 30ms 的真实 GIF 曾因此输出成 73 帧 / 25fps。

因此脚本：

- 总是传 `-enc_time_base 1/1000`（需要 ffmpeg 6.0+；缺失时降级并明确警告）；
- 默认走 **精确 CFR**：取所有帧时长的 gcd 作为网格，用 `image2 -framerate 1000/g` 输入，必要时按 `d/g` 重复帧（硬链接，几乎不占磁盘；帧间编码下重复帧近乎免费）。时间戳与总时长都是精确值；
- 仅当网格会导致帧率 > 200fps 或帧数膨胀过大时，才回退到 concat/VFR：此时把 duration 膨胀 40 倍（使 1/25 量化对整毫秒无损），再用一个常数大小的 `setpts=PTS/40` 除回来。**已知限制**：该路径无法把最后一帧的时长写入容器，`verify` 会据此调整时长预期并在 `notes` 中说明；
- 逐帧 PTS 不再用逐帧嵌套的 `if(eq(N,i),...)` 表达式 —— 时间戳是数据，不应该编码进一个深度等于帧数的表达式里。

`verify` 因此对照的是 `timing.plan.output_frames`，而不是源帧数。

## 4. 透明边缘色

`auto-edge` 读取透明像素自身的隐藏 RGB，只采样与可见像素接触的 8 邻域边缘。所有帧必须得到同一个精确 RGB 才能继续。

它不做全局主色推断，也不把可见主体颜色外扩到透明区域。透明视频和不透明合成视频是不同问题：不透明模式在编码前已经完成 alpha composite，不需要播放器继续解释脏 RGB。

## 5. 模块划分

单文件在长到 1535 行、关注点增至 8 个（新增"背景协商"与"边缘外扩"）之后已经超过了原先设定的拆包触发条件，现已拆为包，入口脚本保持不变：

```text
scripts/transparent_gif_to_video.py   薄入口，只负责把 scripts/ 加入 sys.path 并调用 tgv.cli.main
scripts/tgv/
  common.py        错误、告警（去重）、工具查找、颜色解析、依赖检查

NumPy 是硬依赖。曾经每处 NumPy 实现都配一份纯 Pillow 回退，结果是两份缺陷面加一次静默退化事故；
现在 `common.py` 在导入期检查依赖并给出安装命令——缺依赖要响亮地失败，而不是悄悄换一条慢且不等价的路。
  sources.py       文件/目录 → RGBA 帧 + 时长；自然排序；不猜时长
  analysis.py      alpha 统计、透明边缘证据、内容包围盒、JSON 报告
  background.py    背景证据排序、提问话术、预览图、交互/非交互协商
  staging.py       边缘外扩、背景合成、PNG 落帧、CFR 展开、concat 清单
  timing.py        帧时长 → 时间戳（CFR 网格 / VFR 回退）
  encoding.py      编码器策略与 ffmpeg 命令构建
  convert.py       convert 命令的编排
  verification.py  ffprobe + 实解码校验
  cli.py           参数解析与分发
```

依赖方向是单向的：`cli → convert → {background, staging, encoding, analysis} → {sources, timing} → common`。唯一一处需要留意的是 `analysis` 依赖 `timing`（报告里要给出 `timing.plan`），这是刻意的：让 `verify` 能用同一套计划去对照输出帧数，而不是各算各的。

测试同样拆开：`tests/support.py`（夹具与扁平门面）、`tests/test_units.py`（不需要 ffmpeg）、`tests/test_integration.py`（需要 ffmpeg，缺失时自动跳过）。

测试留在 skill 内部是刻意的：这个 skill 的价值就是一组**无法靠阅读代码确认**的不变量（时间基、帧序、帧数、alpha），
它们只有真跑一遍 ffmpeg 才能证明。同时它对执行期上下文的成本是零 —— `SKILL.md` 不引用 `tests/`，
正常执行任务的 agent 永远不会读到它。代价是测试必须跟着代码走：过期的测试比没有测试更糟，
因此 `tests/README.md` 写明了维护契约。若要把 skill 打包成最小投放物，`tests/` 是唯一可以安全剔除的目录。

## 6. 文件边界

最终 skill 保留：

- `SKILL.md`：执行层入口与约束；
- `scripts/`：运行代码；
- `references/`：需要时读取的决策、原理和排错资料；
- `tests/`：开发维护用的自动化测试，运行时不读取；边界与维护契约见 `tests/README.md`。

不应提交：

- 用户上传素材；
- 生成的 WebM/MP4 测试输出；
- 一次性审计日志；
- 缓存、`__pycache__`、临时 PNG 帧。
