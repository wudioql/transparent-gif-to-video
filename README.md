# transparent-gif-to-video

> **把一个透明 GIF，变成真正带 alpha 的视频。**
> 面向 agent 的纯 FFmpeg skill：一条命令完成转换，运行时唯一依赖是系统 `ffmpeg` —— 没有 Python、没有 Pillow、没有图片序列、没有批量管线。

透明 GIF 的麻烦不在于能不能转，而在于转完之后 **alpha 是不是还活着**。这个仓库做的就是把这件事变成一条可验证、可复现的命令。

---

## 30 秒上手

```powershell
ffmpeg -hide_banner -n -ignore_loop 1 -i "input.gif" -map 0:v:0 -an `
  -fps_mode passthrough -enc_time_base demux `
  -c:v libvpx-vp9 -pix_fmt yuva420p -auto-alt-ref 0 -b:v 0 -crf 30 `
  -deadline good -cpu-used 2 -row-mt 1 "output.webm"
```

这是默认路径：**VP9 WebM CRF 30，保留 alpha，保留源 GIF 的透明区底色，逐帧时长原样搬运**。
`-n` 拒绝覆盖既有文件；确认可以覆盖后才改成 `-y`。

skill 在真正执行前会先展示计划——输入、格式、质量、输出、覆盖行为、底色策略——并等你明确确认。

## 它能输出什么

| 格式 | Alpha | 用途 | 关键限制 |
|---|---:|---|---|
| **VP9 WebM CRF 30** | ✅ | **默认**；网页、读取逐帧素材 | 播放端需支持 WebM alpha |
| VP9 lossless WebM | ✅ | 仍需 WebM 且不接受量化 | 体积大；`yuva420p` 有 4:2:0 表示限制 |
| VP8 WebM | ✅ | 明确的旧 WebM 兼容需求 | 非默认：实测 PSNR 比 VP9 低约 7.6 dB、半透明像素多约 13 倍 |
| ProRes 4444 MOV | ✅ | 剪辑/合成母版 | 高码率；`-alpha_bits 8` 只降低部分 alpha 成本 |
| HAP Alpha MOV | ✅ | 实时播放 / VJ | 宽高须为 4 的倍数；普及度不如 ProRes |
| PNG-in-MOV / qtrle / FFV1 | ✅ | 无损交换、QuickTime 遗留流程、归档 | 体积或播放端兼容性成本较高 |
| 黑底 H.264 MP4 | ❌ | **唯一不透明例外** | 仅用户明确选择；永久丢失 alpha；宽高须为偶数（`yuv420p` 色度抽样要求） |

常见需求其实只需要第一行：**读取逐帧内容时，最小的那一档就是 VP9 WebM**。剪辑用途通常用原 GIF 本身更划算，不必绕道 ProRes。

## 关于透明，有两件事值得先知道

**一、alpha 在不在，和看不看得出，是两回事。**

WebM 的 alpha 放在 Matroska `BlockAdditional` 里，容器里的 `AlphaMode=1` 只是一句声明。播放端如果忽略它，你看到的就是**透明像素底下垫着的那层 RGB**——它可能是白的、也可能是黑的，取决于源 GIF 和转换有没有预处理。

所以预览器里的白底或黑底**不是**判断透明的依据。请在真正支持 alpha 的播放端（如 mpv.net，显示透明棋盘格）或浏览器里验证。

**二、默认忠实于素材，黑底要主动开口。**

默认命令不加任何 RGB 处理，透明区保留源 GIF 原本的底层 RGB。
只有当你明确要求「黑底」时，才插入黑色归一化 filter：

```text
-vf "format=rgba,premultiply=inplace=1:planes=0x7,setparams=alpha_mode=straight"
```

两者 alpha 掩码完全相同，只是前者在忽略 alpha 的播放端显原色、后者显黑。**它都不能修复播放器的 alpha 支持。** 该 filter 仅在 VP8/VP9 链路验证过，MOV 路径要求黑底时需另行验证。

## 怎么验证 alpha 真的活下来了

```powershell
ffmpeg -hide_banner -c:v libvpx-vp9 -i "output.webm" `
  -vf alphaextract,format=gray,signalstats,metadata=print -f null NUL
```

扫描全部帧，断言至少一帧 `YMIN < 255`。三个已经踩过的坑：

1. **必须显式指定 decoder** —— VP9 用 `-c:v libvpx-vp9`、VP8 用 `-c:v libvpx`，按扩展名猜会解出 `yuv420p`，看起来像"alpha 丢了"其实是验证错了。
2. **不要加 `-v error`** —— `signalstats` 输出在 info 级，加了会让采样帧数变成 0，"扫全帧"静默退化成"零帧"，断言被跳过却不报错。
3. **必须加 `format=gray`** —— ProRes 4444 读出来是 `yuva444p12le`，16 位域的值会让 8 位阈值 `YMIN<255` 误判 FAIL。

画质类断言还要记住：**不要只看第一帧。** 首帧是关键帧天然干净，有损编码的 alpha 污染和 RGB 误差从第二帧起才随帧间预测累积。

## 边界

一次只处理一个 GIF。不猜背景色，不提供 auto-edge / suggest-background，不缩放、不裁切、不批量。
`CRF 30` 是 WebM 侧唯一默认档位——它是逐编码器的相对刻度，调到 CRF 40 会在 alpha 边缘产生约 29 万个半透明像素；而黑底 MP4 用的是 x264 CRF 20，两套数值不能类比。

## 维护与回归

完整命令、确认流程和验证规则见 [`SKILL.md`](SKILL.md)；开发回归矩阵见 [`tests/README.md`](tests/README.md)；分级参考见 `references/`。

动态回归结果见 [`test-reports/2026-10-02-matrix.md`](test-reports/2026-10-02-matrix.md)：在 **Windows BtbN FFmpeg 9.0.1 GPL static** 上，用 1000×1000 / 95 帧 / 30ms / 二值 alpha 的夹具实测通过九条路径，并记录了回归过程中的三次方法性纠错。

尚未覆盖的夹具场景：**变帧时长**、**无限循环**、disposal 2/3、含空格/中文/括号的路径、奇数尺寸、不满足 HAP 约束的尺寸。

运行时依赖仍然只有系统 `ffmpeg`；Python / NumPy 只出现在回归测试的取证环节。

## 许可

MIT
