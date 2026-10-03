# 新栈回归矩阵（Phase 6 重写后首跑，2026-10-03）

- 执行层：tgtv 包本身（PyAV 18.1.0 + NumPy，无系统 ffmpeg）
- 脚本：`tests/run_matrix.py`（断言口径沿用旧 ffmpeg CLI 版，HAP 组随 Phase 0 决策下线）
- 等价性对照另见 [`2026-10-03-pyav-vs-ffmpeg-cli.md`](2026-10-03-pyav-vs-ffmpeg-cli.md)，数字校准见 [`2026-10-03-sizing-calibration.md`](2026-10-03-sizing-calibration.md)

```
夹具目录: /home/user/transparent-gif-to-video/tests/../.tmp-matrix/fixtures
============================================================================================================
分组 1：变帧时长
  [PASS] 变帧时长 / 转换成功                                              exit=0
  [PASS] 变帧时长 / 帧数一致                                              src=5 dst=5
  [PASS] 变帧时长 / PTS 逐帧一致（未被平均）                                    dst=[0.0, 0.01, 0.04, 0.09, 0.19]
  [PASS] 变帧时长 / 总时长正确                                             期望 0.39s 实际 0.390s
  [PASS] 变帧时长 / alpha 全帧扫描通过                                      全帧 alpha_min=0

分组 2：无限循环
  [PASS] 无限循环 / 转换成功                                              exit=0
  [PASS] 无限循环 / 只出一个周期（4 帧）                                       实际 4 帧
  [PASS] 无限循环 / 时长为一个周期 0.4s                                      实际 0.400s
  [PASS] 无限循环 / 对照：不传 ignore_loop 也是一个周期（默认 true）                 默认 options 读出 4 帧
  [PASS] 无限循环 / 危险对照：ignore_loop=0 会无限重复                          上限 12 帧读满 12（>1 周期即在重复；故 GifSource 显式 ignore_loop=1 不可省）

分组 3：disposal 2/3 与局部帧
  [PASS] disposal 2 / VP9 转换                                      exit=0
  [PASS] disposal 2 / 完整解码                                        6 帧全部解出
  [PASS] disposal 2 / 显式 libvpx 解码且保留 alpha 平面                    decoder=libvpx-vp9，像素格式=['yuva420p']
  [PASS] disposal 2 / 存在真实透明像素（全帧扫描 alpha_min<255）                全帧 alpha_min=0（首帧 alpha_min=0——首帧不透明素材只有全帧扫描能发现透明）
  [PASS] disposal 2 / 无可见晕环（32<=alpha<=223 占比 <0.5%）              137 px（0.285%）；轻微取整（不计）1583 px
  [PASS] disposal 2 / 帧数与源一致                                      输出 6 帧 / 源 6 帧
  [PASS] disposal 2 / 尺寸未缩放                                       输出 100×80 / 源 100×80
  [PASS] disposal 2 / PTS 逐帧一致（未被平均成 CFR）                         输出 [0.0, 0.12, 0.24, 0.36, 0.48, 0.6] / 源 [0.0, 0.12, 0.24, 0.36, 0.48, 0.6]
  [PASS] disposal 2 / 总时长与源一致                                     输出 0.720s / 源 0.720s
  [PASS] disposal 2 / 透明区 RGB 保持源 GIF 底色（无意外预乘）                   与源平均差 18.20；透明区均值 236.8
  [PASS] disposal 2 / 可见区 RGB 无剧烈失真                               平均差 2.46 / PSNR 27.8 dB（阈值 8.0）
  [PASS] disposal 2 / alpha 阈值化一致（取整不算失配）                         99.910%
  [PASS] disposal 3 / VP9 转换                                      exit=0
  [PASS] disposal 3 / 完整解码                                        6 帧全部解出
  [PASS] disposal 3 / 显式 libvpx 解码且保留 alpha 平面                    decoder=libvpx-vp9，像素格式=['yuva420p']
  [PASS] disposal 3 / 存在真实透明像素（全帧扫描 alpha_min<255）                全帧 alpha_min=0（首帧 alpha_min=0——首帧不透明素材只有全帧扫描能发现透明）
  [PASS] disposal 3 / 无可见晕环（32<=alpha<=223 占比 <0.5%）              124 px（0.258%）；轻微取整（不计）1592 px
  [PASS] disposal 3 / 帧数与源一致                                      输出 6 帧 / 源 6 帧
  [PASS] disposal 3 / 尺寸未缩放                                       输出 100×80 / 源 100×80
  [PASS] disposal 3 / PTS 逐帧一致（未被平均成 CFR）                         输出 [0.0, 0.12, 0.24, 0.36, 0.48, 0.6] / 源 [0.0, 0.12, 0.24, 0.36, 0.48, 0.6]
  [PASS] disposal 3 / 总时长与源一致                                     输出 0.720s / 源 0.720s
  [PASS] disposal 3 / 透明区 RGB 保持源 GIF 底色（无意外预乘）                   与源平均差 17.61；透明区均值 237.4
  [PASS] disposal 3 / 可见区 RGB 无剧烈失真                               平均差 2.46 / PSNR 27.8 dB（阈值 8.0）
  [PASS] disposal 3 / alpha 阈值化一致（取整不算失配）                         99.642%
  [PASS] 局部帧 / VP9 转换                                             exit=0
  [PASS] 局部帧 / 完整解码                                               3 帧全部解出
  [PASS] 局部帧 / 显式 libvpx 解码且保留 alpha 平面                           decoder=libvpx-vp9，像素格式=['yuva420p']
  [PASS] 局部帧 / 存在真实透明像素（全帧扫描 alpha_min<255）                       全帧 alpha_min=0（首帧 alpha_min=0——首帧不透明素材只有全帧扫描能发现透明）
  [PASS] 局部帧 / 无可见晕环（32<=alpha<=223 占比 <0.5%）                     0 px（0.000%）；轻微取整（不计）4435 px
  [PASS] 局部帧 / 帧数与源一致                                             输出 3 帧 / 源 3 帧
  [PASS] 局部帧 / 尺寸未缩放                                              输出 120×100 / 源 120×100
  [PASS] 局部帧 / PTS 逐帧一致（未被平均成 CFR）                                输出 [0.0, 0.1, 0.2] / 源 [0.0, 0.1, 0.2]
  [PASS] 局部帧 / 总时长与源一致                                            输出 0.300s / 源 0.300s
  [PASS] 局部帧 / 透明区 RGB 保持源 GIF 底色（无意外预乘）                          与源平均差 1.65；透明区均值 253.3
  [PASS] 局部帧 / 可见区 RGB 无剧烈失真                                      平均差 4.38 / PSNR 28.5 dB（阈值 8.0）
  [PASS] 局部帧 / alpha 阈值化一致（取整不算失配）                                99.978%
  [PASS] 局部帧+disposal2 / VP9 转换                                   exit=0
  [PASS] 局部帧+disposal2 / 完整解码                                     3 帧全部解出
  [PASS] 局部帧+disposal2 / 显式 libvpx 解码且保留 alpha 平面                 decoder=libvpx-vp9，像素格式=['yuva420p']
  [PASS] 局部帧+disposal2 / 存在真实透明像素（全帧扫描 alpha_min<255）             全帧 alpha_min=0（首帧 alpha_min=0——首帧不透明素材只有全帧扫描能发现透明）
  [PASS] 局部帧+disposal2 / 无可见晕环（32<=alpha<=223 占比 <0.5%）           0 px（0.000%）；轻微取整（不计）1955 px
  [PASS] 局部帧+disposal2 / 帧数与源一致                                   输出 3 帧 / 源 3 帧
  [PASS] 局部帧+disposal2 / 尺寸未缩放                                    输出 120×100 / 源 120×100
  [PASS] 局部帧+disposal2 / PTS 逐帧一致（未被平均成 CFR）                      输出 [0.0, 0.1, 0.2] / 源 [0.0, 0.1, 0.2]
  [PASS] 局部帧+disposal2 / 总时长与源一致                                  输出 0.300s / 源 0.300s
  [PASS] 局部帧+disposal2 / 透明区 RGB 保持源 GIF 底色（无意外预乘）                与源平均差 0.54；透明区均值 254.5
  [PASS] 局部帧+disposal2 / 可见区 RGB 无剧烈失真                            平均差 3.66 / PSNR 30.2 dB（阈值 8.0）
  [PASS] 局部帧+disposal2 / alpha 阈值化一致（取整不算失配）                      100.000%
  [PASS] 局部帧+disposal2 / 读取层确实按模式处置（与 disposal=1 不同）              与 disposal=1 的 alpha 平面均差 78.41
  [PASS] 局部帧+disposal3 / VP9 转换                                   exit=0
  [PASS] 局部帧+disposal3 / 完整解码                                     3 帧全部解出
  [PASS] 局部帧+disposal3 / 显式 libvpx 解码且保留 alpha 平面                 decoder=libvpx-vp9，像素格式=['yuva420p']
  [PASS] 局部帧+disposal3 / 存在真实透明像素（全帧扫描 alpha_min<255）             全帧 alpha_min=0（首帧 alpha_min=0——首帧不透明素材只有全帧扫描能发现透明）
  [PASS] 局部帧+disposal3 / 无可见晕环（32<=alpha<=223 占比 <0.5%）           0 px（0.000%）；轻微取整（不计）1955 px
  [PASS] 局部帧+disposal3 / 帧数与源一致                                   输出 3 帧 / 源 3 帧
  [PASS] 局部帧+disposal3 / 尺寸未缩放                                    输出 120×100 / 源 120×100
  [PASS] 局部帧+disposal3 / PTS 逐帧一致（未被平均成 CFR）                      输出 [0.0, 0.1, 0.2] / 源 [0.0, 0.1, 0.2]
  [PASS] 局部帧+disposal3 / 总时长与源一致                                  输出 0.300s / 源 0.300s
  [PASS] 局部帧+disposal3 / 透明区 RGB 保持源 GIF 底色（无意外预乘）                与源平均差 0.54；透明区均值 254.5
  [PASS] 局部帧+disposal3 / 可见区 RGB 无剧烈失真                            平均差 3.66 / PSNR 30.2 dB（阈值 8.0）
  [PASS] 局部帧+disposal3 / alpha 阈值化一致（取整不算失配）                      100.000%
  [PASS] 局部帧+disposal3 / 读取层确实按模式处置（与 disposal=1 不同）              与 disposal=1 的 alpha 平面均差 78.41

分组 4：首帧不透明（验证「必须扫全帧」）
  [PASS] 首帧不透明 / 转换成功                                             exit=0
  [PASS] 首帧不透明 / 全帧扫描能发现透明像素                                      全帧 alpha_min=0
  [PASS] 首帧不透明 / 反例：只看首帧会误判                                       首帧 alpha_min=255（≥255 即判定为无透明，正是误判来源）

分组 5：透明区底层 RGB 与贴边透明
  [PASS] 二值底RGB / 转换成功                                            verify 11/11 项通过
  [PASS] 二值底RGB / 透明区保持源 GIF 白色（未被涂黑）                             透明区均值 254.8
  [PASS] 二值底RGB / 可见区 RGB 接近已知值 (66,74,71)                        输出=(66.2, 72.8, 71.0)
  [PASS] 边缘透明 / 贴边透明像素仍为透明                                        98.86% 保持透明（共 88 个贴边透明像素）

分组 6：奇数 / 非 4 倍数尺寸
  [PASS] 奇数尺寸 999x999 / VP9 转换                                    输出 999x999
  [PASS] 奇数尺寸 999x999 / 尺寸未被缩放                                    输出 999x999
  [PASS] 奇数尺寸 999x999 / alpha 保留                                  全帧 alpha_min=0
  [PASS] 奇数尺寸 1000x999 / VP9 转换                                   输出 1000x999
  [PASS] 奇数尺寸 1000x999 / 尺寸未被缩放                                   输出 1000x999
  [PASS] 奇数尺寸 1000x999 / alpha 保留                                 全帧 alpha_min=0
  [PASS] 奇数尺寸 999x1000 / VP9 转换                                   输出 999x1000
  [PASS] 奇数尺寸 999x1000 / 尺寸未被缩放                                   输出 999x1000
  [PASS] 奇数尺寸 999x1000 / alpha 保留                                 全帧 alpha_min=0
  [PASS] 奇数尺寸 998x998 / VP9 转换                                    输出 998x998
  [PASS] 奇数尺寸 998x998 / 尺寸未被缩放                                    输出 998x998
  [PASS] 奇数尺寸 998x998 / alpha 保留                                  全帧 alpha_min=0
  [PASS] 奇数尺寸 / MP4 预检拒绝奇数尺寸                                      宽 999 不是偶数：yuv420p 的 2×2 色度抽样要求宽高均为偶数（黑底 MP4 路径）。不缩放、不裁切、不补边。
  [PASS] 奇数尺寸 / MP4 拒绝后零产出                                        输出文件 不存在
  [INFO] 奇数尺寸 / 危险对照已结构性消灭                                        旧栈省略 format=yuv420p 会静默产出 yuv444p（High 4:4:4）；新栈 build_plan 预检先行拒绝，不存在绕过路径
  [INFO] 分组 7：HAP 尺寸约束                                            随 Phase 0 决策（HAP 移除）下线，见 docs/python-only-refactor-analysis.md 附录 A

分组 8：含空格 / 中文 / 括号 / 方括号的路径
转换计划（未执行）：
  输入    : /home/user/transparent-gif-to-video/tests/../.tmp-matrix/fixtures/测试目录 (带有 空格) [括号]/输入 [1].gif（64×64 / 2 帧 / 0.2s）
  格式    : VP9 WebM CRF 30（默认）（codec=libvpx-vp9，pix_fmt=yuva420p，有损，保留 alpha）
  关键参数: auto-alt-ref=0 b=0 crf=30 deadline=good cpu-used=2 row-mt=1
  输出    : /home/user/transparent-gif-to-video/tests/../.tmp-matrix/fixtures/测试目录 (带有 空格) [括号]/输出 文件 [1].webm
  底色策略: 保留源 GIF 透明区底层 RGB（默认，不预乘）
  说明    : WebM 侧唯一默认档位；CRF 不得主动上调（见 references/sizing.md）。
  覆盖行为: 输出不存在，将创建

完成：/home/user/transparent-gif-to-video/tests/../.tmp-matrix/fixtures/测试目录 (带有 空格) [括号]/输出 文件 [1].webm（2 帧 / 0.7 KiB / alpha 保留）
提示：运行 `tgtv verify /home/user/transparent-gif-to-video/tests/../.tmp-matrix/fixtures/测试目录 (带有 空格) [括号]/输出 文件 [1].webm --source /home/user/transparent-gif-to-video/tests/../.tmp-matrix/fixtures/测试目录 (带有 空格) [括号]/输入 [1].gif` 做转换后验证。
  [PASS] 特殊路径 / tgtv convert（CLI 端到端）                             exit=0
  [PASS] 特殊路径 / alpha 保留                                          全帧 alpha_min=0
  [PASS] 特殊路径 / 帧数正确                                              2 帧

============================================================================================================
通过 96 / 失败 0 / 说明 2
  INFO: 奇数尺寸 / 危险对照已结构性消灭  旧栈省略 format=yuv420p 会静默产出 yuv444p（High 4:4:4）；新栈 build_plan 预检先行拒绝，不存在绕过路径
  INFO: 分组 7：HAP 尺寸约束  随 Phase 0 决策（HAP 移除）下线，见 docs/python-only-refactor-analysis.md 附录 A
```
