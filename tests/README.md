# 开发回归测试矩阵

本目录不参与 skill 运行。skill 运行时仍只依赖系统 `ffmpeg`；开发阶段可以使用额外工具解析 PTS、逐帧像素和容器结构。目标环境是 Windows BtbN FFmpeg 9.0 GPL static。

## 状态说明

维护环境为 Windows **BtbN FFmpeg 9.0.1 GPL static**（n9.0.1，`Lavf63.1.101`），已具备 `libvpx-vp9`、`libvpx`、`prores_ks`、`hap`、`png`、`qtrle`、`ffv1`、`libx264`。

2026-10-02 在本环境完成动态回归，结果见 [`../test-reports/2026-10-02-matrix.md`](../test-reports/2026-10-02-matrix.md)。夹具：1000×1000、95 帧、30ms/帧、2.85s、二值 alpha（972,000 透明 / 28,000 不透明 / 0 半透明）、透明区隐藏 RGB 为白。

已通过：VP9 CRF30 / VP9 lossless / VP8（均含黑色归一化）、ProRes 4444 `-alpha_bits 8`、HAP Alpha、PNG-in-MOV、qtrle、FFV1、黑底 H.264 MP4。**尚未覆盖**的夹具需求见下节第 4、7、8、9 项。

## 夹具

至少准备：

1. 5 帧透明 GIF，30ms；
2. 三种以上不同帧时长；
3. 局部帧和 disposal 2/3；
4. 无限循环；
5. 首帧不透明、后续透明；
6. 透明主体接触边缘；
7. 含空格、中文、括号的路径；
8. 奇数尺寸 GIF；
9. 不满足 HAP 尺寸约束的 GIF；
10. alpha=0 隐藏 RGB 为白色、alpha=255 可见 RGB 已知的二值 GIF。

## 通用断言

- 编码退出码为 0；
- `-ignore_loop 1` 对无限循环只输出一个周期；
- 完整解码成功；
- alphaextract 成功（alpha 输出）；
- alphaextract + `format=gray` + signalstats 扫描全部帧，首/中/末均覆盖；
  - 不可加 `-v error`：signalstats 输出在 info 级，加了会静默退化成零帧而断言被跳过；
  - 不可省 `format=gray`：>8bit alpha（如 `yuva444p12le`）会让 8 位阈值误判 FAIL；
- 至少一帧 `YMIN < 255`；
- **有损路径**：统计半透明像素数（0 < alpha < 255），二值 GIF 源应接近 0；
- **必须逐帧**，不得只测首帧——首帧是关键帧，污染从第 2 帧起才累积；
- 首、中、末帧顺序正确；
- 逐帧取样在 hash 前先断言原始输出字节数非零；
- 30ms 未变成 40ms，变时长未被平均为 CFR；
- `-n` 拒绝覆盖，明确确认后 `-y` 才覆盖；
- 不存在 Python/Pillow/NumPy/图片序列运行时依赖。

## 格式矩阵

| 路径 | 编码/关键验证 |
|---|---|
| VP9 CRF30 | `libvpx-vp9`；黑色归一化；显式 libvpx-vp9 解码得到 `yuva420p` |
| VP9 lossless | 同上；检查 alpha 与时间戳 |
| VP8 CRF30 | `libvpx`；不要用 VP9 decoder |
| ProRes 4444 | `prores_ks -profile:v 4444 -alpha_bits 8 -pix_fmt yuva444p10le`；alphaextract + 目标编辑器 |
| HAP Alpha | `hap -format hap_alpha -compressor snappy`；先检查 encoder 和尺寸，不缩放/裁切 |
| PNG-in-MOV | rgba、逐帧无损 |
| qtrle | argb、完整解码 |
| FFV1 | yuva444p、同域无损比较 |
| 黑底 MP4 | 仅明确选择；偶数尺寸；直接 GIF→libx264/yuv420p；完整解码且无 alpha |

## 黑色归一化专项

在目标 BtbN FFmpeg 9 上对转换前后的帧：

1. 比较 alpha 掩码，必须相同；
2. 断言 alpha=0 RGB 全为黑；
3. 断言 alpha=255 RGB 未非预期改变；
4. 浏览器显示仍透明；
5. 显式 `-c:v libvpx-vp9` 解码得到 `yuva420p`。

候选 filter 为：

```text
format=rgba,premultiply=inplace=1:planes=0x7,setparams=alpha_mode=straight
```

若测试发现后续自动 unpremultiply 或 alpha 改变，必须更换为已在 FFmpeg 9 验证的纯 FFmpeg filter，并更新命令与报告。

## ProRes、HAP、MP4 专项

- ProRes：验证 `alphaextract`，导入至少一个目标编辑器；确认 8-bit alpha 足够二值 GIF。
- HAP：目标 BtbN/FFmpeg 9 按宽、高均为 4 的倍数检查，并用 encoder 帮助/实际编码确认；不满足时必须失败；验证 MOV 完整解码和目标 VJ/实时播放软件。
- 黑底 MP4：奇数宽或高必须拒绝；检查 `yuv420p`、完整解码、黑色透明区；不允许把没有 alpha 平面误称为“alpha 验证”。
