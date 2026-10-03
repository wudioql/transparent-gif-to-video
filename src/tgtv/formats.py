"""输出格式注册表：能力探测、计划展示、编码执行三处共用的单一事实来源。

对应旧 SKILL.md 的「格式矩阵」（§2/§3 命令模板）。每条 FormatSpec 的
writer_options / muxer_options 与旧 ffmpeg 命令模板逐项对应：

    旧命令                                              →  本注册表
    ------------------------------------------------------------------
    -c:v libvpx-vp9 -pix_fmt yuva420p -auto-alt-ref 0
      -b:v 0 -crf 30 -deadline good -cpu-used 2 -row-mt 1 →  vp9
    -c:v libvpx-vp9 ... -lossless 1                      →  vp9-lossless
    -c:v libvpx -pix_fmt yuva420p -auto-alt-ref 0 ...    →  vp8
    -c:v prores_ks -profile:v 4444 -alpha_bits 8
      -pix_fmt yuva444p10le                              →  prores4444
    -c:v png -pix_fmt rgba（MOV 容器）                    →  png-mov
    -c:v qtrle -pix_fmt argb（MOV 容器）                  →  qtrle
    -c:v ffv1 -level 3 -coder 1 -context 1 -g 1
      -slicecrc 1 -pix_fmt yuva444p（MKV 容器）           →  ffv1
    -c:v libx264 -crf 20 -preset slow -movflags +faststart
      -pix_fmt yuv420p（唯一不透明路径）                   →  mp4-black
    -c:v hap -format hap_alpha ...                       →  hap（已移除，见下）

HAP 说明（Phase 0 决策，docs/decisions.md）：
PyAV wheel 不含 hap encoder（2026-10-03 实测 av 18.1.0，§3.4 静态核验 av 19.0.1
同样缺失），HAP 已从 Python 栈矩阵移除。probe 对 HAP 请求如实报缺并停止，
不偷换格式（维持旧 skill「缺 encoder 就停止」的硬规则）。
"""

from dataclasses import dataclass, field
from fractions import Fraction


@dataclass(frozen=True)
class FormatSpec:
    """一条输出路径的完整定义。

    Attributes:
        key:            CLI / API 使用的短名（如 ``vp9``）。
        label:          人类可读名称（含默认档位与定位说明）。
        codec:          libavcodec encoder 名。
        pix_fmt:        编码像素格式。
        ext:            输出扩展名（含点）。
        muxer:          av.open(..., format=) 的复用器名。
        alpha:          是否保留 alpha。
        lossy:          是否有损。
        writer_options: encoder 私有选项（open 阶段传入；Phase 3 writers 直接复用）。
        muxer_options:  复用器私有选项（如 mp4 的 +faststart）。
        note:           计划展示时需要说明的口径（默认/非默认/永久丢 alpha 等）。
    """

    key: str
    label: str
    codec: str
    pix_fmt: str
    ext: str
    muxer: str
    alpha: bool
    lossy: bool
    writer_options: dict = field(default_factory=dict)
    muxer_options: dict = field(default_factory=dict)
    note: str = ""


#: 受支持的 8 条输出路径（顺序即文档矩阵顺序；vp9 为默认）。
FORMATS: dict[str, FormatSpec] = {
    spec.key: spec
    for spec in (
        FormatSpec(
            key="vp9",
            label="VP9 WebM CRF 30（默认）",
            codec="libvpx-vp9",
            pix_fmt="yuva420p",
            ext=".webm",
            muxer="webm",
            alpha=True,
            lossy=True,
            writer_options={
                "auto-alt-ref": "0",
                "b": "0",
                "crf": "30",
                "deadline": "good",
                "cpu-used": "2",
                "row-mt": "1",
            },
            note="WebM 侧唯一默认档位；CRF 不得主动上调（见 references/sizing.md）。",
        ),
        FormatSpec(
            key="vp9-lossless",
            label="VP9 lossless WebM",
            codec="libvpx-vp9",
            pix_fmt="yuva420p",
            ext=".webm",
            muxer="webm",
            alpha=True,
            lossy=False,
            writer_options={
                "auto-alt-ref": "0",
                "lossless": "1",
                "deadline": "good",
                "cpu-used": "2",
                "row-mt": "1",
            },
            note="无量化；yuva420p 仍不是原始 RGBA 字节逐点保证（4:2:0 表示限制）。",
        ),
        FormatSpec(
            key="vp8",
            label="VP8 WebM（非默认）",
            codec="libvpx",
            pix_fmt="yuva420p",
            ext=".webm",
            muxer="webm",
            alpha=True,
            lossy=True,
            writer_options={
                "auto-alt-ref": "0",
                "b": "0",
                "crf": "30",
                "deadline": "good",
                "cpu-used": "2",
            },
            note="实测画质与 alpha 保真明显劣于 VP9 CRF 30，仅明确兼容需求时使用。",
        ),
        FormatSpec(
            key="prores4444",
            label="ProRes 4444 MOV",
            codec="prores_ks",
            pix_fmt="yuva444p10le",
            ext=".mov",
            muxer="mov",
            alpha=True,
            lossy=False,
            writer_options={"profile": "4444", "alpha_bits": "8"},
            note="视觉无损、高码率编辑母版；-alpha_bits 8 只降低部分 alpha 成本。",
        ),
        FormatSpec(
            key="png-mov",
            label="PNG-in-MOV（无损）",
            codec="png",
            pix_fmt="rgba",
            ext=".mov",
            muxer="mov",
            alpha=True,
            lossy=False,
            writer_options={},
            note="无损交换；体积通常很大。",
        ),
        FormatSpec(
            key="qtrle",
            label="qtrle MOV（无损）",
            codec="qtrle",
            pix_fmt="argb",
            ext=".mov",
            muxer="mov",
            alpha=True,
            lossy=False,
            writer_options={},
            note="QuickTime Animation 遗留流程。",
        ),
        FormatSpec(
            key="ffv1",
            label="FFV1 MKV（无损）",
            codec="ffv1",
            pix_fmt="yuva444p",
            ext=".mkv",
            muxer="matroska",
            alpha=True,
            lossy=False,
            writer_options={
                "level": "3",
                "coder": "1",
                "context": "1",
                "g": "1",
                "slicecrc": "1",
            },
            note="开源无损归档；同域（yuva444p）逐像素无损。",
        ),
        FormatSpec(
            key="mp4-black",
            label="黑底 H.264 MP4（唯一不透明例外）",
            codec="libx264",
            pix_fmt="yuv420p",
            ext=".mp4",
            muxer="mp4",
            alpha=False,
            lossy=True,
            writer_options={"crf": "20", "preset": "slow"},
            muxer_options={"movflags": "+faststart"},
            note="仅用户明确选择；永久丢失 alpha；宽高必须均为偶数（yuv420p 色度抽样要求）。",
        ),
    )
}

#: 已移除的路径：不在 FORMATS 中、不参与转换，但 probe 如实报告原因。
REMOVED_FORMATS: dict[str, FormatSpec] = {
    spec.key: spec
    for spec in (
        FormatSpec(
            key="hap",
            label="HAP Alpha MOV（已移除）",
            codec="hap",
            pix_fmt="rgba",
            ext=".mov",
            muxer="mov",
            alpha=True,
            lossy=True,
            writer_options={"format": "hap_alpha", "compressor": "snappy"},
            note=(
                "已从 Python 栈移除（Phase 0 决策）：PyAV wheel 不含 hap encoder。"
                "如需 HAP，请使用含 hap encoder 的外部 ffmpeg；本工具不偷换格式。"
            ),
        ),
    )
}


#: probe / writer 打开 encoder 时的通用时间基：与 GIF demuxer 一致（等价旧命令
#: 的 -enc_time_base demux；逐帧 PTS 由 source 层原样透传，等价 -fps_mode passthrough）。
OPEN_TIME_BASE = Fraction(1, 100)
