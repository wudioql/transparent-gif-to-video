# alpha 与不透明输出校验

所有校验由 `scripts/transparent_gif_to_video.py verify` 执行，不再维护一份可能和实际转换命令漂移的手写脚本。

## 1. 用法

透明输出：

```text
python scripts/transparent_gif_to_video.py verify source.gif output.webm --expect alpha
```

不透明输出：

```text
python scripts/transparent_gif_to_video.py verify source.gif output.mp4 --expect opaque
```

## 2. 校验内容

脚本会：

1. 用 Pillow 读取源的尺寸、帧数和 alpha 状态。
2. 用 ffprobe 读取输出视频流、编码器、尺寸、像素格式、帧数字段和时长。
3. 用 ffmpeg 实际解码抽样帧（默认首/中/末三帧，`--sample-frames N` 可调）为 RGBA；VP8/VP9 自动指定 libvpx，避免 WebM BlockAdditional 被原生路径忽略。
4. 对 alpha 输出检查像素格式/解码 alpha 是否存在；对不透明输出检查像素格式无 alpha 且抽样帧 alpha 全为 255。
5. 帧数对照 `timing.plan.output_frames`（CFR 网格展开后的预期帧数），时长按该 plan 是否精确调整容差。

输出 JSON 的 `checks.all_pass` 必须为 `true` 才算通过。

## 3. 边界

- 抽样校验能发现 alpha 整体丢失或中段丢失，但不是逐帧像素级差异证明。
- 源本身不透明时，`--expect alpha` 只能证明容器/像素格式支持 alpha，`notes` 会写明这一点。
- VFR 回退路径下容器不记录最后一帧时长，时长校验会按缺少最后一帧处理并在 `notes` 说明。
- 对严格无损需求，使用 `--lossless`、PNG-in-MOV 或 FFV1，并另行做逐帧 hash/alpha 平面比对。
- 播放器兼容性不等于文件编码正确；Safari/iOS 等平台不支持某些 alpha 视频时，verify 通过也不能改变平台能力。
- 若环境没有 ffmpeg/ffprobe，`inspect` 仍可运行，但 `convert`/`verify` 会明确报依赖缺失，不会伪造成功。
