# 故障排查

通用排查入口：先跑 `tgtv probe --require <KEY> --size WxH`（能力与尺寸预检），再跑 `tgtv verify <输出> --source <源GIF>`（逐项断言，退出码 0/1）。

## alpha 与验证

| 症状 | 原因/检查 | 处理 |
|---|---|---|
| VP9/VP8 alpha 看似丢失 | 原生 decoder 只给 `yuv420p`（丢 alpha 平面） | `tgtv verify` 按容器声明的 codec 显式选 `libvpx-vp9`/`libvpx`，不按扩展名猜；`verify` 的「显式 libvpx 解码且保留 alpha 平面」检查专防此事 |
| 有 alpha 平面但素材全不透明 | alpha 存在 ≠ 有透明像素 | `verify` 全帧扫描 alpha_min，断言至少一帧 < 255；首帧不透明素材首帧 alpha_min=255，全帧扫描才见 0 |
| 画质断言看起来正常但肉眼很差 | 只测了首帧；污染从第 2 帧起随帧间预测累积 | `verify --source` 逐帧统计（可见区 MAD/PSNR、晕环占比），结构性覆盖全部帧 |
| 有损 WebM 边缘出现羽化/晕环 | CRF 过高量化了 alpha 平面 | `verify` 区分轻微取整（1–31/224–254，忽略）与可见晕环（32–223，卡 <0.5%）；回到 VP8/VP9 CRF 30，不用 CRF 40 换体积 |
| `verify` 报「透明区 RGB 保持源 GIF 底色」FAIL 且透明区均值接近黑 | 意外预乘（历史上 out.webm 黑底 bug 正是这类） | 若产物确为 `--black`/mp4-black 转换，验证时传 `--black` 期望；否则这是真缺陷，重新转换 |
| 黑底 MP4 验证报「无 alpha」 | 该路径预期永久丢 alpha | `verify --black` 将「无 alpha 平面」判为正确证据并检查透明区黑；不是错误 |
| 普通播放器白底/黑底 | 播放端忽略 WebM `BlockAdditional`（`AlphaMode=1` 只是声明） | 换支持 alpha 的播放器/格式；黑色归一化只提供黑色回退，不制造透明兼容性 |
| ProRes 编辑器不认 | 软件兼容性 | `verify` 已断言解码侧（yuva444p12le）；导入目标编辑器实测 |

## 尺寸与格式

| 症状 | 原因/检查 | 处理 |
|---|---|---|
| HAP 请求被拒 | PyAV wheel 不含 hap encoder（Phase 0 决策移除） | 如实说明并停止；需要 HAP 时用含 hap encoder 的外部工具，不偷换格式 |
| 黑底 MP4 报「宽/高不是偶数」 | `yuv420p` 的 2×2 色度抽样要求偶数，不是 x264 的任意限制 | 预检阶段即拒绝且零产出；不缩放、不裁切、不补边。旧栈省略 `format=yuv420p` 静默产出 yuv444p（High 4:4:4）的绕过路径在新栈结构性不存在 |
| `--black` 用在非 VP8/VP9 格式 | 黑底仅在该链路验证 | 报「仅支持 vp9/vp9-lossless/vp8」；MOV 类格式要求黑底需另行验证，不得直接套用 |
| 拿 WebM 的 CRF 30 套到 MP4 | CRF 是逐编码器相对刻度 | x264 固定 CRF 20 / preset slow；同为 CRF 30 的 VP8 也比 VP9 低约 7 dB。数值不可跨平台类比 |
| 未知格式 KEY / 输入不是 GIF | 计划阶段校验 | 报错并列出可用 KEY；输入按内容探测（改扩展名无效） |

## 流程与覆盖

| 症状 | 原因/检查 | 处理 |
|---|---|---|
| 输出覆盖旧文件 | 覆盖保护生效（`-n` 语义） | 只有用户确认覆盖**该具体路径**后才 `--overwrite`；确认执行计划（`--yes`）不包含覆盖授权 |
| 非交互执行被拒 | 确认闸设计：管道喂 `yes` 无效 | 先 `--dry-run` 展示计划给用户，获得确认后 `--yes` |
| 转换卡住不结束 | 旧栈 `-ignore_loop 0` 事故 | 新栈读取层固定 `ignore_loop=1`，无取反入口；无限循环素材只出一个周期 |
| 变帧时长被平均成 CFR | — | 新栈 PTS 原样透传，`verify` 断言逐帧一致与总时长一致（含最后一帧）；不存在 `-r` 入口 |

## PyAV / 新栈特有

| 症状 | 原因/检查 | 处理 |
|---|---|---|
| `av.AVError` 不存在 | PyAV 的异常类型是 `av.error.FFmpegError`（旧名 `av.AVError` 已移除） | 捕获 `av.FFmpegError` 或具体异常转成可读错误 |
| 末帧只显示一瞬（容器时长短一截） | libvpx/x264 把帧缓冲到 flush 才吐包且包 `duration=0`，Matroska 只记末帧 pts+1 tick | 已在 `convert` 修复：按 pts 建「源帧→duration」映射、mux 前写回包上；`verify` 的「总时长与源一致」检查专防复发 |
| `to_ndarray` 对 planar yuva 格式报 ValueError | PyAV 只支持部分布局的 numpy 转换（如 `yuva444p` 不支持） | 按平面提取（`frame.planes[i]` + line_size 步进），见 `verify._yuva444p_planes` |
| 帧对象 API 混用 | `GifFrame`（读取层）没有 `to_ndarray`；`av.VideoFrame` 没有 `.rgba` | 统一走 `verify._rgba`（两者都接受）；或显式 `gf.rgba` / `fr.to_ndarray(format="rgba")` |
| ExternalError / 编码器打开失败 | wheel 缺该 encoder（如 hap） | `probe` 先行报告能力，convert 不应走到这一步；如实停止 |

路径含空格/中文/括号/方括号：CLI 直接传参即可（无 shell 引号问题）；回归矩阵分组 8 覆盖。
