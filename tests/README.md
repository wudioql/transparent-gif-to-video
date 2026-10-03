# 开发回归测试矩阵

本目录不参与 skill 分发，但**与运行时使用同一 Python 栈**：运行时依赖 `tgtv`（PyAV ≥18,<19 + NumPy），开发回归直接以 `tgtv` 为执行层，不再依赖系统 ffmpeg。迁移期对照工具 imageio-ffmpeg（dev extras）仅用于「新栈 vs ffmpeg CLI」等价性取证，不是运行时或回归的必要条件。

## 状态说明（2026-10-03，Phase 1–8 完成）

- 单元/集成回归：`pytest tests/` **89/89**（probe 24 / source 16 / convert 30 / verify 19）。
- 边界夹具矩阵：`python tests/run_matrix.py` **96 通过 / 0 失败 / 2 说明**（执行层即 tgtv；报告 [`../test-reports/2026-10-03-matrix-newstack.md`](../test-reports/2026-10-03-matrix-newstack.md)）。
- 迁移期等价性对照：`python tests/cross_check_vs_ffmpeg.py` **17/17 组、137/137 项**（同参数下 tgtv vs ffmpeg 7.0.2 CLI 产物；报告 [`../test-reports/2026-10-03-pyav-vs-ffmpeg-cli.md`](../test-reports/2026-10-03-pyav-vs-ffmpeg-cli.md)）。
- sizing 数字校准：`python tests/calibrate_sizing.py`（双栈；报告 [`../test-reports/2026-10-03-sizing-calibration.md`](../test-reports/2026-10-03-sizing-calibration.md)）。
- 旧栈数字基线（Windows BtbN FFmpeg 9.0.1，通过 59/0）已并入 `references/sizing.md` 的「历史基线」表；完整旧报告见 git 历史。

## 夹具（自动生成，全部覆盖）

夹具由 [`make_fixtures.py`](make_fixtures.py) 按需生成（GIF 被 `.gitignore` 忽略，不入库），回归由 [`run_matrix.py`](run_matrix.py) 执行。开发依赖：`pip install -e ".[dev]"`（pytest + Pillow + imageio-ffmpeg）。

```bash
pytest tests/                      # 单元/集成回归
python tests/run_matrix.py         # 边界夹具矩阵（自建夹具）
```

| # | 需求 | 夹具 | 覆盖结论 |
|---|---|---|---|
| 1 | 5 帧透明 GIF，30ms | `fx_01_small5_30ms.gif` | 5 帧 / 30ms 保留 |
| 2 | 三种以上不同帧时长 | `fx_02_vardur.gif` | 10/30/50/100/200ms 逐帧 PTS 与源一致，总 0.39s（含末帧时长），未被平均 |
| 3 | 局部帧 + disposal 2/3 | `fx_05b_partial_real.gif`、`fx_05c_partial_disp{2,3}.gif` | disposal=1 累积 / 2、3 每帧仅剩局部块，与读取层自身渲染逐项一致，且确实与 disposal=1 不同 |
| 4 | 无限循环 | `fx_03_loop_infinite.gif`（loop=0） | 只出一个周期（4 帧 / 0.4s）；危险对照 `ignore_loop=0` 以帧数上限法证无限重复 |
| 5 | 首帧不透明、后续透明 | `fx_06_first_opaque.gif` | 首帧 alpha_min=255、全帧 alpha_min=0 ——「必须扫全帧」的硬证据 |
| 6 | 透明主体接触边缘 | `fx_07_edge_touch.gif` | 贴边透明像素 98.86% 保持透明 |
| 7 | 含空格、中文、括号、方括号的路径 | `测试目录 (带有 空格) [括号]/输入 [1].gif` | CLI 端到端转换、alpha、帧数全部正常 |
| 8 | 奇数尺寸 GIF | `fx_09_odd_999x999.gif` 等 | WebM 侧 999×999 / 1000×999 / 999×1000 均正常且尺寸不变 |
| 9 | 不满足 HAP 尺寸约束 | `fx_09_{w,h}-only_*.gif`、`fx_09_not4_*` | HAP 随 Phase 0 决策移除（PyAV wheel 无 hap encoder）；该组下线，尺寸约束逻辑由 mp4-black 偶数校验承载 |
| 10 | 已知隐藏 RGB 的二值 alpha GIF | `fx_08_binary_known_rgb.gif` | 透明区保持 (255,255,255)，可见区 (66,74,71) 基本保持 |

**手写 GIF 编码器（`make_fixtures.py` 内的 `write_gif`）**：Pillow 只写全帧，无法产出带偏移的局部更新帧，所以局部帧/disposal 夹具由内置的最小 GIF89a 编码器生成。它自带 `validate_handcrafted()` 自检——裁判优先用 **tgtv 读取层**（PyAV，与运行时同解码器；未安装 tgtv 的旧环境回退系统 ffmpeg）——因为编码器出错时会产出「能解码但全透明」的坏流，若不自检，逐像素比对会拿两个全透明画面对比得到毫无意义的 0.00 差（此坑已真实踩过）。其 LZW 位宽必须在 `next_code == 2^code_size + 1` 时增长，写成 `2^code_size` 会产出无法正确还原的流。

## 通用断言（NumPy 管线口径）

- 转换成功且帧数与源一致；完整解码零帧即异常；
- 无限循环只输出一个周期；PTS 逐帧一致（含末帧）与总时长一致，未被平均成 CFR；
- 全帧 alpha 扫描：至少一帧 alpha_min < 255（首帧不透明素材靠全帧扫描发现透明）；
- 半透明区分轻重：32–223 可见晕环占比 < 0.5%，1–31 / 224–254 取整不计；
- 底色策略：默认路径透明区 RGB 与源一致（意外预乘检测）；`--black`/mp4-black 归黑；
- 尺寸不缩放；黑底 MP4 奇数尺寸在预检阶段拒绝且零产出；
- 覆盖保护：输出已存在默认拒绝且原文件不动；`--overwrite` 才覆盖；输出=输入拒绝；
- 确认闸：非交互（含管道）必须 `--yes`；`--dry-run` 零写入；
- 无损路径按域断言：png/qtrle RGBA 域逐像素一致；ffv1 yuva444p 原生域逐像素一致。

signalstats 时代的三个坑（`-v error` 清零采样、>8bit alpha 需 `format=gray`、首帧误判）在 NumPy 管线中结构性消除，见 `references/verification.md`。

## 格式矩阵

| 路径 | 编码/关键验证 |
|---|---|
| vp9（默认） | `libvpx-vp9` CRF30；显式 libvpx-vp9 解码得 `yuva420p` 且全帧 alpha_min=0 |
| vp9-lossless | 同上；`yuva420p` 表示域限制（非 RGBA 逐点保证） |
| vp8 | `libvpx` CRF30；显式 libvpx 解码 |
| prores4444 | `prores_ks` profile 4444 + 8-bit alpha；解码 `yuva444p12le`；建议目标编辑器实测 |
| png-mov | rgba、RGBA 域逐像素无损 |
| qtrle | argb、RGBA 域逐像素无损 |
| ffv1 | yuva444p、原生域逐像素无损 |
| mp4-black | 仅明确选择；偶数尺寸预检拒绝；GIF→libx264/yuv420p 直出；无 alpha 即正确证据 |

## 黑色归一化专项（可选路径，非默认）

默认输出不做 RGB 处理、保留源 GIF 透明区底层 RGB；`--black`（须用户显式要求，仅 VP8/VP9）与 mp4-black 两条路径分别执行并验证：

1. 透明区 RGB：黑底路径均值 < 10；默认路径与源平均差 < 40（VP9 CRF30 固有漂移 15–18，意外预乘 ≥100）；
2. alpha 阈值化一致 > 99%（取整不算失配）；
3. 可见区 RGB MAD < 8（PSNR 一并报告）；
4. 显式 libvpx-vp9 解码得到 `yuva420p`。

实现为 `filters.premultiply_rgb`（NumPy uint16 中间量），等价旧 `premultiply=inplace=1:planes=0x7`；`setparams=alpha_mode=straight` 有意不实现（Matroska `AlphaMode=1` 由复用器自动声明，差异以像素级回归覆盖）。

## 迁移期对照脚本

- [`cross_check_vs_ffmpeg.py`](cross_check_vs_ffmpeg.py)：CLI 命令由 `formats.FORMATS` 选项逐项生成（两栈编码参数相同，唯一差异是执行引擎），17 组 × 8 维度（帧数/画布/PTS/时长/像素格式/alpha 一致/可见区与透明区同档 + 按域逐像素精确）。
- [`calibrate_sizing.py`](calibrate_sizing.py)：CRF 关键数字双栈校准（高边缘密度合成素材；边缘密度是 VP8 alpha 损伤的驱动因素，纯几何素材落差仅 2.3 dB 不具代表性）。
