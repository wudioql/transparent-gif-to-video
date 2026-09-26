# tests/ — 开发维护用，执行任务时不要读

**执行转换任务的 agent 不需要、也不应该读取本目录。** 入口是 `SKILL.md`，实现是 `scripts/`，
排错资料是 `references/`。本目录只在**修改这个 skill 本身**时使用。

## 运行

```text
python -m unittest discover -s tests -t tests
```

`test_units.py` 只需要 Pillow；`test_integration.py` 需要 ffmpeg/ffprobe，缺失时自动跳过
（跳过不等于通过：改动时间轴、帧序或 alpha 相关代码后，必须在有 ffmpeg 的环境跑一次）。

## 准入标准

一条测试要留下来，必须同时满足：

1. 它守的缺陷**可能真实发生**（最好是已经发生过）；
2. 该缺陷**无法靠阅读代码发现**；
3. 同一个不变量**只在最接近用户可观测行为的那一层**守一次。

按这个标准清理掉过的：断言常量等于自己的（`DEFAULT_CRF["h264"] == 20`）、
给纯函数再包一层而端到端已覆盖的（自然排序 key）、以及同一不变量的第二、三份副本。
测试数量从 40 降到 27，**没有减少任何一条历史缺陷的覆盖**。

## 这些测试守的是什么

它们不是覆盖率产物，而是这个 skill 全部已知教训的可执行形式：

| 测试 | 锁住的教训 |
|---|---|
| `test_uniform_30ms_gif_keeps_30ms_timestamps`（集成） | concat 与编码器两处默认 1/25 时间基，会把 30ms GIF 变成 25fps 并丢掉重复时间戳的帧（真实素材 95 帧 → 73 帧） |
| `test_h264_opaque_frame_count_matches_source`（集成） | 曾靠"MP4 封装会自己丢掉哨兵帧"的经验假设多写一帧 |
| `test_variable_timing_roundtrip_and_alpha_verify`（集成） | 可变时长要落在精确网格上，且 alpha 要能真解码出来 |
| `test_image_sequence_frame_order_survives_encoding`（集成） | 排序错误会让序列顺序静默颠倒 |
| `test_directory_sequence_is_read_in_natural_order` | 同一缺陷的最小复现：`frame_10` 不得排在 `frame_2` 前 |
| `test_cfr_command_uses_image2_without_a_filter` | 缺 `-enc_time_base` 时时间戳会被编码器重新量化 |
| `test_vfr_fallback_uses_a_constant_size_setpts` | 旧实现用深度等于帧数的嵌套 `if()` 表达式 |
| `test_all_zero_edge_colour_is_suspicious_but_still_offered` | auto-edge 会把解码器归零的 RGB 当作者意图，自信返回黑色 |
| `test_missing_durations_are_never_invented` | 单帧素材曾被静默按 100ms 处理 |
| `test_bleed_touches_only_transparent_rgb_and_never_wraps` | 外扩必须不改 alpha、不改可见像素、合成结果逐像素不变，且不得绕回画布对侧 |
| `test_ask_mode_without_a_tty_emits_a_proposal_and_fails` | 非交互环境下绝不允许自行取背景色 |
| `test_preview_does_not_materialise_every_frame` | 预览取一帧曾把整段动画读进内存（95×1000×1000 ≈ 380MB） |
| `test_missing_numpy_fails_loudly_with_an_install_hint` | NumPy 缺失时功能曾静默退化（边缘证据消失、`--bleed-edges` 变空操作）；现在必须在导入期报错并给出安装命令 |
| `test_codec_specific_options_are_reported_as_ignored` | 曾用"和字面默认值比较"判断用户是否传过参数 |
| `test_warnings_are_deduplicated_within_a_run` | auto-edge 走两遍解码，同一条告警会打印两次 |

## 维护契约

改动以下任一处，必须同时增/改测试，否则视为未完成：

1. 帧时长 → 时间戳的映射（`timing.py`、`encoding.py` 的时间基参数）；
2. 帧顺序与帧数（`sources.py`、CFR 展开、`-frames:v`）；
3. alpha 的保留与合成（`staging.py`）；
4. 背景色的判定与协商（`background.py`）——尤其是任何放宽"拒绝"的改动。

新增"脚本可以自动决定某件事"的能力时，先写一条证明它**在证据不足时会拒绝**的测试。

反过来也成立：如果一条测试失败时你的第一反应是"改测试"而不是"改代码"，它多半不该存在。
