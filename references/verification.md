# 验证规则

## 用户转换

1. 完整解码所有帧：`ffmpeg -v error -i OUTPUT -map 0:v:0 -f null NUL`。
2. alpha 输出用 `alphaextract,format=gray,signalstats`，扫描全部帧；至少覆盖首、中、末，不只查第一帧。
3. 断言至少一帧 alpha 的 `YMIN < 255`。`alphaextract` 成功本身只证明有 alpha 平面，不证明存在透明像素。
   - **不能加 `-v error`**：`signalstats` 与 `metadata=print` 输出在 info 级；加上后采样数为 0，"扫全帧"静默退化成"零帧"，断言跳过却不报错（2026-10-02 实测确认）。
   - **必须加 `format=gray`**：ProRes 4444 读取为 `yuva444p12le` 时 `alphaextract` 输出 gray16，`signalstats` 在 16 位域报值，8 位阈值 `YMIN<255` 必然误判 FAIL（实测曾报 `YMIN=256`，而 alpha 实际逐像素正确）。
4. VP9 显式 `-c:v libvpx-vp9`，VP8 显式 `-c:v libvpx`；不可按扩展名盲猜。
5. 黑底 MP4 应完整解码并确认 `yuv420p`/没有 alpha；alphaextract 失败是预期，另检查黑底像素。
6. 有损输出额外统计**半透明像素数**（0 < alpha < 255）。二值 GIF 源该值应为 0 或极小；数量突增说明 alpha 平面被量化，会在边缘产生可见晕环。

## 开发测试

逐步取样在计算 hash 前必须先断言输出字节数非零；否则空输出会产生相同的空 hash，造成假性通过。至少比较首、中、末，最好扫描所有帧。开发工具可以使用 Python、ImageMagick 或其它工具，但这些不是运行时依赖。

**只看首帧会得出错误结论。** 首帧是关键帧，天然干净；有损编码的 alpha 污染与 RGB 误差从第 2 帧起才随帧间预测显现并累积。2026-10-02 回归中曾因只测第 0 帧误判 VP8 与 VP9 画质相同，逐帧复测后 VP8 的 PSNR 实际低 7.6 dB。回归测试必须逐帧统计半透明像素数与可见区 RGB 误差。

对黑色归一化必须分别检查：

- alpha 掩码转换前后完全一致；
- alpha=0 区域 RGB 为 `(0,0,0)`；
- alpha=255 区域 RGB 没有非预期变化；
- 浏览器仍正确透明；
- 显式 libvpx-vp9 解码得到 `yuva420p`。

对 ProRes：完整解码、alphaextract 及目标编辑器导入。对 HAP：encoder、尺寸约束、完整解码、alphaextract 及目标实时播放/VJ/剪辑软件。

## 兼容性边界

WebM alpha 放在 `BlockAdditional`；`AlphaMode=1` 只是声明。浏览器/libvpx 正常不保证普通播放器支持。MOV/MKV 中的其他 alpha 编码通常更易被剪辑软件识别。
