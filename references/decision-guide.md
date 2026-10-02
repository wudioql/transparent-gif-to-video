# 格式选择

未指定格式时，推荐 **VP9 WebM CRF 30**，但必须先展示计划并等待确认。WebM 侧 CRF 30 是唯一默认质量档位——更高 CRF（如 40）实测会让 alpha 平面产生大量半透明边缘、PSNR 同步下降，仅在用户明确接受画质折损时才讨论（`references/sizing.md`）。

**CRF 数值不可跨平台类比**：VP9 的 30 与 x264 的 20 处于相近的画质区间，两者数字不互通；黑底 MP4 固定用 CRF 20。同为 libvpx 家族也不可比——实测 VP8 CRF 30 的 PSNR 比 VP9 CRF 30 低约 7.6 dB。

| 选择 | 推荐场景 | 代价 |
|---|---|---|
| VP9 CRF 30 | 网页、体积优先 | 播放端必须支持 WebM alpha |
| VP9 lossless | 仍需 WebM、避免量化 | 大；`yuva420p` 仍有 4:2:0 表示限制 |
| VP8 | 明确的旧 WebM 兼容需求 | 实测画质与 alpha 保真明显劣于 VP9 CRF 30（PSNR 低约 7.6 dB，半透明像素约 13 倍），非默认 |
| ProRes 4444 | 剪辑/合成母版 | 高码率；8-bit alpha 不是低码率视频 |
| HAP Alpha MOV | 实时播放、VJ、部分剪辑软件 | 尺寸有约束；不保证比 FFV1/PNG 小，普及度不如 ProRes |
| PNG-in-MOV | 无损交换 | 通常很大 |
| qtrle | QuickTime Animation 遗留流程 | 体积较大 |
| FFV1 | 开源无损归档 | 消费端兼容性弱 |
| 黑底 H.264 MP4 | 明确不需要透明 | 永久失去 alpha；偶数宽高；只在用户明确选择后执行 |

VP8/VP9 **默认保留源 GIF 的透明区底层 RGB**。只有用户明确要求黑底时才加 `format=rgba,premultiply=inplace=1:planes=0x7,setparams=alpha_mode=straight`：透明像素底层 RGB 变黑，支持 alpha 的浏览器仍透明，不支持 alpha 的播放器显示黑底。这不是“修复播放器透明兼容性”，也不改变 alpha 本身。

注意该 filter 目前仅在 VP8/VP9 链路验证过；ProRes 4444 / HAP Alpha 等 MOV 路径要求黑底时需另行验证。

ProRes 4444 使用 `-alpha_bits 8 -profile:v 4444 -pix_fmt yuva444p10le`。它只降低部分 alpha 数据成本，仍定位为高码率编辑母版。

黑底 MP4 使用 CRF 20、preset slow、`+faststart` 作为起点。若用户最终只需要它，直接从 GIF 输出，不能先经过 WebM 二次转码。
