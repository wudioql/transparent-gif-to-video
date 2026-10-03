# 新栈 vs ffmpeg CLI 等价性对照（Phase 6 迁移期）

- 日期：2026-10-03
- 新栈：tgtv（PyAV 18.1.0 + NumPy，注册表驱动 writer）
- 基准：ffmpeg version 7.0.2-static https://johnvansickle.com/ffmpeg/  Copyright (c) 2000-2024 the FFmpeg developers（imageio-ffmpeg 捆带）
- 方法：CLI 命令由 `formats.FORMATS.writer_options` 逐项生成——两栈编码参数相同，唯一差异是执行引擎；同夹具、同解码器（显式 libvpx*）读回比对。

| 组 | 结果 |
|---|---|
| small5 × vp9 | ✅ 等价 |
| small5 × vp9-lossless | ✅ 等价 |
| small5 × vp8 | ✅ 等价 |
| small5 × prores4444 | ✅ 等价 |
| small5 × png-mov | ✅ 等价 |
| small5 × qtrle | ✅ 等价 |
| small5 × ffv1 | ✅ 等价 |
| small5 × mp4-black | ✅ 等价 |
| binary × vp9 | ✅ 等价 |
| binary × vp9 --black | ✅ 等价 |
| binary × mp4-black | ✅ 等价 |
| vardur × vp9 | ✅ 等价 |
| partial_real × vp9 | ✅ 等价 |
| first_opaque × vp9 | ✅ 等价 |
| edge × vp9 | ✅ 等价 |
| odd_999 × vp9 | ✅ 等价 |
| disposal2 × vp9 | ✅ 等价 |

逐项检查：137/137 通过。

## 阈值校准记录

- `THRESH_TRANS_KEPT_MAD`（verify.py 透明区底色检查）由 12 调整为 40：VP9 CRF30 在 disposal2/3 夹具上透明区 RGB 存在 15–18 的固有漂移（CLI 18.45/15.02，tgtv 15.83/15.92——两栈同档等价，均非回归），而意外预乘的漂移量级 ≥100（白底拉黑）。

- 全部维度通过，无失败项。
