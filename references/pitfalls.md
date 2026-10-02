# 故障排查

| 症状 | 常见原因 | 处理 |
|---|---|---|
| 找不到 `ffmpeg` | 未安装或 PATH 尚未刷新 | 重新打开终端，运行 `where.exe ffmpeg` |
| `Unknown encoder 'libvpx-vp9'` | FFmpeg build 不含 libvpx | 安装 BtbN GPL static release build |
| 转换一直不结束 | GIF 循环设置被执行 | 确认输入前使用 `-ignore_loop 1` |
| 动画变成 25fps 或节奏改变 | 时间戳被默认帧率/编码器时基量化 | 使用 `-fps_mode passthrough -enc_time_base demux`，不要添加 `-r` |
| WebM 看起来是黑底 | 播放器不支持 WebM alpha，或编码错误 | 用 libvpx + `alphaextract` 验证；再检查目标播放器 |
| ffmpeg 信息显示 `yuv420p` | 原生探测未展示附加 alpha | 不单凭该字段判断；显式用 libvpx 解码 |
| `alphaextract` 失败 | 输出没有可解码 alpha，或用了错误解码器 | VP8/VP9 验证时显式指定 `-c:v libvpx*` |
| 输出已存在而命令失败 | 模板默认 `-n` 防覆盖 | 只有用户明确确认后才改成 `-y` |
| Safari/iOS 不透明或无法播放 | 平台/版本不支持目标 WebM alpha | 改用目标软件支持的格式，如 ProRes 4444；不是调 CRF 能解决的问题 |
| 奇怪彩边或灰边 | 4:2:0 色度、有损量化或播放器合成 | 降低 CRF，或选 ProRes 4444/PNG/FFV1 |

## Windows 路径

始终给输入和输出路径加双引号：

```powershell
"C:\Users\Name\My Assets\logo.gif"
```

不要把 PowerShell 的反引号换行形式直接复制到 CMD。为避免两种 shell 的差异，agent 实际执行时优先使用单行命令。

## 多个 FFmpeg 安装

运行：

```powershell
where.exe ffmpeg
winget list --name FFmpeg
```

如果出现多个路径，先确认当前命中的版本。不要同时依赖 master 和 release branch 的 PATH 链接。
