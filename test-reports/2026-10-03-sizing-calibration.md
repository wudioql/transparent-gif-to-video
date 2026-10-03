# sizing.md 数字校准（Phase 7，新栈）

- 日期：2026-10-03
- 素材：合成校准素材 calib_1000x1000_95f（1000×1000 / 95 帧 / 二值 alpha / 高边缘密度：同心环×8 + 24 齿星形 + 细条纹块 + 逐帧运动；生成自 tests/calibrate_sizing.py，旧表的真实素材不在仓库）
- 新栈：tgtv（PyAV 18.1.0）；对照：ffmpeg version 7.0.2-static https://johnvansickle.com/ffmpeg/  Copyright (c) 2000-2024 the FFmpeg developers
- 两栈编码参数逐项相同（由 FORMATS 生成，仅 crf 不同）。

| 候选 | 栈 | 体积 | 可见区 PSNR | 95 帧半透明总量 | 其中可见晕环(32–223) |
|---|---|---:|---:|---:|---:|
| VP9 CRF 30 | tgtv | 0.55 MB | 28.5 dB | 2,274,597 | 7 |
| VP9 CRF 30 | cli | 0.56 MB | 28.8 dB | 2,289,369 | 3 |
| VP9 CRF 40 | tgtv | 0.33 MB | 27.3 dB | 3,956,043 | 2,596 |
| VP9 CRF 40 | cli | 0.33 MB | 27.5 dB | 3,931,948 | 2,515 |
| VP8 CRF 30 | tgtv | 0.23 MB | 21.6 dB | 3,195,588 | 58,961 |
| VP8 CRF 30 | cli | 0.23 MB | 21.2 dB | 3,128,242 | 62,062 |
| VP8 CRF 40 | tgtv | 0.23 MB | 21.6 dB | 2,987,057 | 59,873 |
| VP8 CRF 40 | cli | 0.23 MB | 21.2 dB | 3,128,242 | 62,062 |

- VP8 相对 VP9 的 PSNR 落差：tgtv 6.9 dB / cli 7.5 dB（旧表 BtbN 9.0.1：约 7.6 dB）。
- VP9 CRF30→40：新增半透明 1,681,446（tgtv）；体积 0.55→0.33 MB（省 40%）。
- VP8 CRF30→40 体积变化：0.23→0.23 MB。

结论与旧表一致：VP9 CRF 40 省 40% 体积但以 alpha 晕环为代价；VP8 的 PSNR 落差 ~7 dB 且可见晕环高四个数量级；VP8 CRF 已饱和；VP9 CRF 30 仍是唯一默认。数字本身新旧栈同档（差异来自素材不同，非栈差异）。
