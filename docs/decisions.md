# 决策记录与升级清单

2026-10-03，本仓库从「系统 ffmpeg skill」重构为「纯 Python 栈 skill」：
运行时 = `tgtv` 包（PyAV ≥18,<19 + NumPy，Python ≥3.11），不依赖系统 ffmpeg。
规划、映射与逐 Phase 验收过程见 git 历史（`73503b6` 之后的重构提交序列）；
本文只保留有长期效力的决策溯源与版本升级前置条件。

## Phase 0 决策（2026-10-03，用户已确认）

| 决策点 | 结论 | 影响 |
|---|---|---|
| 路线 | **A2：PyAV（pip wheel 捆带 FFmpeg）+ NumPy 验证** | 接受 wheel 携带的 FFmpeg 库，彻底放弃系统 ffmpeg 依赖；imageio-ffmpeg 降级为开发侧迁移期对照工具（dev extras） |
| HAP | **从矩阵移除** | PyAV wheel 不含 hap encoder（18.1.0 运行时实测 + 19.0.1 静态核验一致）；`probe` 对 HAP 请求如实报缺并停止，不偷换格式；纯 Python 自研列为远期可选项 |
| 形态 | **Python 包 + CLI + 重写 SKILL.md** | 仓库从纯文档 skill 变为「Python 包 + skill 文档」；计划确认 / 覆盖保护等不变量落在 CLI 层 |
| Python 下限 | **定版 ≥3.11 + av ≥18,<19（零校准规则）** | 见下 |

## 版本下限的出入与定版规则

决策记录曾有两处出入：问答 UI 选了 **≥3.11**，用户手写的附录 A 为 **≥3.12（av 19.x 代系）**。
静态核验确认两条版本线能力面一致（编码器/滤镜集合相同、均无 HAP encoder），
差异仅是「基线数字来自静态核验还是运行时实测」。定版：沿用全部运行时实测的
**`requires-python >= 3.11`、`av >= 18, < 19`**（零校准成本）。

备注：沙箱网络策略拦截 GitHub release 资产域名（SSL 层），故无法在沙箱引入
Python 3.12 做运行时核验；PyPI wheel 下载不受影响。

## 升级清单（升级 Python 3.12 + av 19 前必做）

1. 在目标机器重跑 `docs/probes/2026-10-03-pystack-probe.py`，全绿（重点：hap 仍缺、
   其余 8 encoder 不缺、`setparams=alpha_mode`、尺寸约束行为）；
   基线证据见同目录 `.output.txt`（av 18.1.0 / libavcodec 62.28）。
2. `pytest tests/` 全绿（当前基线 89 项）。
3. `python tests/run_matrix.py` 全绿（当前基线 96 通过 / 0 失败）。
4. （可选，迁移期对照）`python tests/cross_check_vs_ffmpeg.py` 等价组 17/17。
5. 全绿后再改 `pyproject.toml` 的 `requires-python` 与 `av` pin，并重新校准
   `references/sizing.md` 的数字基线注解。
