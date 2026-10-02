# 架构与维护边界

## 1. 运行模型

本 skill 没有运行时代码。agent 阅读 `SKILL.md`，完成只读预检，向用户展示计划并等待确认，然后直接调用系统 `ffmpeg`。

```text
单个透明 GIF
  → ffmpeg GIF demux/decoder（只读一个动画周期）
  → 保留 demuxer 时间戳
  → 用户选定的 alpha 编码器
  → ffmpeg 完整解码 + alphaextract 验证
```

不再使用 Python、Pillow、NumPy、临时 PNG、图片序列或 ffconcat 清单。

## 2. 为什么可以移除 Python

旧实现需要 Python，主要因为它同时承担了：多输入类型、任意图片序列排序、背景色分析、不透明合成、像素统计、边缘外扩和结构化验证。当前产品边界只有“单个透明 GIF → alpha 视频”，这些工作都不再需要。

GIF disposal、帧解码和时间戳由 ffmpeg 自己处理。输出端使用：

```text
-fps_mode passthrough -enc_time_base demux
```

避免把动画强制变成固定 25/30/60 fps。

## 3. 为什么不用 shell 包装器

shell/PowerShell 包装器会重新引入参数解析、路径转义、错误处理和跨平台维护，同时容易把 `grep`、`awk`、`sort` 等工具变成隐式依赖。对于每次只转换一个文件的 agent skill，直接生成一条可审查的 ffmpeg 命令更简单，也更符合“确认后执行”的工作流。

## 4. 维护边界

支持的输入只有 GIF；支持的输出只有 `SKILL.md` 格式矩阵中的 alpha 编码。以下需求应被视为另一个工具，而不是继续扩展本 skill：

- 背景合成或普通 MP4；
- APNG/静态图片；
- 图片序列和批量任务；
- 缩放、裁切或画布重排；
- 背景色推断；
- GUI。

## 5. 时间轴不变量

任何命令调整都必须守住：

1. 不添加固定 `-r`；
2. 不让 ffmpeg 自动复制/丢弃帧以凑 CFR；
3. 编码器时间基来自 demuxer；
4. 无限循环 GIF 只编码一个周期；
5. 30ms GIF 不得退化成 40ms/25fps；
6. 可变帧时长不得被平均化。
