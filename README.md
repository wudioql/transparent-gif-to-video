# transparent-gif-to-video

把带透明通道的 GIF / PNG / APNG / 图片序列，转换成**保留 alpha 的视频**，或**合成指定实色背景的不透明视频**。

这是一个 agent skill：`SKILL.md` 是执行入口，`scripts/` 是实现，`references/` 是按需查阅的决策与排错资料。

## 它解决什么

多数"GIF 转透明视频"的做法会在三个地方静默出错，而且**看代码发现不了**：

- **帧时长**：ffmpeg 的 concat demuxer 与编码器都默认 1/25 时间基，30ms 的 GIF 会变成 25fps；时间戳碰撞后重复帧还会被丢弃（实测一段 95 帧素材输出只剩 73 帧）。
- **背景色**：编码器普遍把全透明像素的 RGB 清零，于是"自动识别透明边缘色"会非常自信地返回 `#000000` —— 而它通常只是解码产物。
- **校验**：`ffprobe` 读 WebM 时不展示 alpha 平面，`pix_fmt=yuv420p` 不代表 alpha 丢了。

本 skill 对这三件事的处理分别是：精确 CFR 网格 + `-enc_time_base 1/1000`；把背景色变成一次**带证据的提问**而不是一次猜测；用 libvpx 实际解码抽样帧来校验。

## 安装

仓库根目录就是 skill 目录，克隆到你的 skills 目录即可：

```bash
git clone https://github.com/wudioql/transparent-gif-to-video.git ~/.claude/skills/transparent-gif-to-video
```

依赖：Python 3.10+、Pillow ≥ 9.2、NumPy；`convert` 需要 ffmpeg ≥ 6.0，`verify` 另需 ffprobe。

## 快速开始

```bash
# 1. 体检：帧数、时长、alpha 分布、透明边缘证据
python scripts/transparent_gif_to_video.py inspect input.gif

# 2. 保留透明 → VP9 WebM
python scripts/transparent_gif_to_video.py convert input.gif out.webm --keep-alpha

# 3. 要不透明：先拿到带信任度的候选并与用户确认，再转
python scripts/transparent_gif_to_video.py suggest-background input.gif --preview options.png
python scripts/transparent_gif_to_video.py convert input.gif out.mp4 --background '#000000'

# 4. 校验（解码级，不只看元数据）
python scripts/transparent_gif_to_video.py verify input.gif out.webm --expect alpha
```

完整用法见 [`SKILL.md`](SKILL.md)。

## 开发

```bash
python -m unittest discover -s tests -t tests
```

`test_units.py` 只需 Pillow；`test_integration.py` 需要 ffmpeg/ffprobe，缺失时自动跳过 —— 但改动时间轴、帧序或 alpha 相关代码后必须在有 ffmpeg 的环境跑一次。维护契约见 [`tests/README.md`](tests/README.md)。

## 许可

MIT
