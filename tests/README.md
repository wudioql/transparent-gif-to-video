# 开发回归测试要求

本目录不参与 skill 运行，也不引入 Python 依赖。修改 `SKILL.md` 中的时间戳、编码器或像素格式参数前，必须在目标 Windows/BtbN FFmpeg 环境中用真实夹具验证。

## 必测夹具

1. 5 帧、每帧 30ms 的透明 GIF；
2. 至少三种不同帧时长的透明 GIF；
3. 带局部帧和 disposal 2/3 的 GIF；
4. 无限循环 GIF；
5. 首帧不透明、后续帧透明的 GIF；
6. 透明主体接触画布边缘的 GIF；
7. 文件名和目录含空格、中文及括号的 Windows 路径。

## 每个编码路径的断言

- 转换命令正常退出；
- 无限循环输入只输出一个周期；
- 输出能完整解码；
- `alphaextract` 成功；
- 首、中、末画面顺序正确；
- 30ms 时间戳没有变成 40ms；
- 变时长没有被平均为固定帧率；
- `-n` 拒绝覆盖，明确改用 `-y` 后才覆盖。

## 格式矩阵

至少验证：

- VP9 lossy WebM；
- VP9 lossless WebM；
- VP8 WebM；
- ProRes 4444 MOV；
- PNG-in-MOV；
- qtrle MOV；
- FFV1 MKV。

开发测试可以使用额外工具读取 PTS 和逐帧像素，但这些工具不得成为 skill 的运行时依赖。
