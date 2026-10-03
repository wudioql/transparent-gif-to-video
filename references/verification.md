# 验证规则

## 用户转换：`tgtv verify`

```bash
tgtv verify <输出> --source <源GIF>           # 完整断言（推荐）
tgtv verify <输出> --source <源GIF> --black   # --black / mp4-black 产物
tgtv verify <输出>                            # 无源模式（仅输出自身断言）
tgtv verify <输出> --json                     # 机读：checks + stats 全量数字
```

退出码 0=通过 / 1=失败。规则（全部内置于 `src/tgtv/verify.py`）：

1. **完整解码**：全部帧无错解出，帧数 > 0。
2. **显式 decoder**：WebM 的 VP9/VP8 按容器声明的 codec 用 `libvpx-vp9` / `libvpx` 解码，不按扩展名猜——原生解码器会把 `yuva420p` 悄悄读成 `yuv420p` 丢掉 alpha。
3. **alpha 存在 ≠ 有透明像素**：全帧扫描 alpha 最小值，断言至少一帧 < 255。
4. **半透明区分轻重**：轻微取整（1–31 / 224–254）与可见晕环（32–223）分开统计；有损路径卡晕环占比 < 0.5%，不要求「零半透明」。
5. **PTS 逐帧一致 + 总时长一致**（含最后一帧）：未被平均成 CFR。
6. **帧数 / 尺寸一致**：不缩放、不裁切。
7. **底色策略**：默认断言透明区 RGB 与源一致（意外预乘检测器）；`--black`/mp4-black 断言透明区归黑。
8. **可见区保真**：RGB MAD/PSNR；png-mov/qtrle 要求 RGBA 域逐像素一致；ffv1 在原生 yuva444p 域逐像素一致（RGBA 域差异是色彩空间往返假象）。
9. **黑底 MP4**：无 alpha 平面即正确证据，另断言透明区呈现黑。

## 三个历史坑：已由 NumPy 管线结构性消除

signalstats/ffmpeg-CLI 验证时代踩过的坑，在 `verify.py` 的结构中**不存在对应物**（而非靠小心避免）：

| 旧坑（ffmpeg CLI 管线） | 新栈（NumPy 管线） |
|---|---|
| `-v error` 把 signalstats 采样清零，「扫全帧」静默退化成零帧、断言被跳过 | 不存在日志级别问题；逐帧显式循环统计，**零帧即抛异常** |
| >8bit alpha（ProRes 解出 `yuva444p12le`）需 `format=gray` 归一，否则 8 位阈值误判 | `to_ndarray(format="rgba")` 统一归一到 8 位域再比较 |
| 只看首帧误判（首帧关键帧天然干净；首帧不透明素材更极端） | 全帧扫描由循环结构保证，无法只看一帧 |

历史叙述保留：2026-10-02 曾因只测首帧误判 VP8 与 VP9 画质相同，逐帧复测后 VP8 的 PSNR 实际低 7.6 dB；某夹具首帧 `YMIN=255`（判「无透明」）而全帧扫描 `YMIN=0`。

## 黑色归一化专项（可选路径，非默认）

默认输出不加 RGB 处理、保留源 GIF 透明区底层 RGB；`--black`（须用户显式要求）与 mp4-black 路径由 `verify` 分别断言：

- `--black` 路径：透明区 RGB 均值 < 10；alpha 阈值化一致 > 99%（取整不算失配）；可见区 MAD < 8；显式 libvpx 解码 `yuva420p`。
- 默认路径：透明区 RGB 与源平均差 < 40（VP9 CRF30 在高边缘密度素材上有 15–18 的固有漂移；意外预乘 ≥100，阈值 40 居中），并输出透明区均值供人工判读。
- mp4-black：无 alpha（预期）+ 透明区黑（yuv420p 往返阈值放宽到 20）。

## 开发测试

- 单元/集成回归：`pytest tests/`（89 项，Phase 1–5）。
- 边界夹具矩阵：`python tests/run_matrix.py`（96 项断言，执行层即 tgtv 本身，无系统 ffmpeg；报告见 `test-reports/2026-10-03-matrix-newstack.md`）。
- 迁移期等价性对照：`python tests/cross_check_vs_ffmpeg.py`（同参数下 tgtv vs ffmpeg 7.0.2 CLI 产物，17 组 × 8 维度）。
- sizing 校准：`python tests/calibrate_sizing.py`（双栈 CRF 数字）。

ProRes 建议另外导入至少一个目标编辑器实测；播放器兼容性问题（普通播放器白底/黑底）不属于转换缺陷。

## 兼容性边界

WebM alpha 放在 `BlockAdditional`；`AlphaMode=1` 只是声明。浏览器/libvpx 正常不保证普通播放器支持。MOV/MKV 中的其他 alpha 编码通常更易被剪辑软件识别。
