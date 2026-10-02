# 故障排查

| 症状 | 原因/检查 | 处理 |
|---|---|---|
| 找不到 ffmpeg/encoder | 安装或 build 不完整 | 使用 BtbN FFmpeg 9 GPL static；按所选格式检查 encoder，不静默换格式 |
| VP9/VP8 alpha 看似丢失 | 原生 decoder 可能只给 `yuv420p` | VP9 显式 `-c:v libvpx-vp9`，VP8 显式 `-c:v libvpx` |
| 普通播放器白底/黑底 | 忽略 WebM BlockAdditional | 浏览器和显式 libvpx 验证；换支持 alpha 的播放器/格式。黑色归一化只提供黑色回退，不制造透明兼容性 |
| alphaextract 成功但素材全不透明 | 只有 alpha 平面不等于有透明像素 | `alphaextract,format=gray,signalstats` 扫描全部帧，断言至少一个 YMIN < 255；**不可加 `-v error`**（会使采样数为 0、断言被静默跳过），**不可省 `format=gray`**（>8bit alpha 会让 8 位阈值误判 FAIL） |
| 有损 WebM 边缘出现羽化/晕环 | CRF 过高量化了 alpha 平面 | 统计半透明像素数（0<alpha<255）；二值 GIF 源应接近 0。回到 VP8/VP9 CRF 30，不用 CRF 40 换体积 |
| 拿 WebM 的 CRF 30 套到 MP4 | CRF 是逐编码器相对刻度 | x264 用 CRF 20；同为 CRF 30 的 VP8 也比 VP9 低约 7.6 dB。数值不可跨平台类比 |
| 画质断言看起来正常但肉眼很差 | 只测了首帧；污染从第 2 帧起累积 | 逐帧统计 RGB 误差与 PSNR，覆盖首、中、末，至少包含中后段帧 |
| VP8/VP9 黑色 filter 结果不符 | filter 被版本行为或后续 unpremultiply 改写 | 在目标 FFmpeg 9 逐项验证 alpha、透明 RGB、可见 RGB、浏览器、libvpx；失败就暂停发布并替换已验证 filter |
| ProRes alphaextract 失败/编辑器不认 | 编码器或软件兼容性 | 检查 `-profile:v 4444 -alpha_bits 8 -pix_fmt yuva444p10le`；完整解码并在目标编辑器实测 |
| HAP encoder 不存在 | build 未包含 HAP | 停止并说明；不要改成别的格式 |
| HAP 编码尺寸报错 | 宽/高不满足 encoder 约束 | 在目标版本确认约束；拒绝奇数/不满足尺寸，不缩放、不裁切 |
| MP4 有透明需求却没有 alpha | H.264/yuv420p 天生是不透明输出 | 仅用户明确选择黑底 MP4；计划告知永久丢 alpha；奇数尺寸拒绝 |
| 黑底 MP4 报 `width/height not divisible by 2` | `yuv420p` 的 2×2 色度抽样要求偶数尺寸 | 宽高**任一**为奇数都会失败；停止并报告，不缩放/裁切/补边。改用 `yuv444p` 虽可编码，但兼容性差，不作绕过手段 |
| 黑底 MP4 只转了一半 | GIF loop 或时间轴参数错误 | `-ignore_loop 1 -fps_mode passthrough -enc_time_base demux`，不加 `-r` |
| 输出覆盖旧文件 | `-n` 保护生效 | 只有用户确认具体覆盖后才用 `-y` |
| 彩边/画质差 | 4:2:0 或有损编码 | 换 ProRes/PNG/FFV1 或用更低 CRF，需重新确认；不要通过调高 CRF 换体积 |

Windows 路径始终用双引号，特别是含空格、中文和括号的路径。不要按 `*.webm` 通配分配 decoder。
