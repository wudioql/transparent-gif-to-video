"""能力探测（只读）：替代旧流程的 ``ffmpeg -encoders`` 预检。

职责：

1. **encoder 可用性** —— 对每条 FormatSpec 用 writer 将要使用的确切配置
   （codec + pix_fmt + writer_options）尝试创建并打开 encoder。
   probe 通过 ≈ writer 可用；不存在「probe 过了 writer 挂」的配置差异。
   缺 encoder 即报缺，不静默换格式（维持旧 skill 硬规则）。
2. **验证解码器** —— 显式 libvpx-vp9（VP9）/ libvpx（VP8）/ gif（源）
   是否可用；旧 SKILL.md §4.2 的「不可按扩展名猜 decoder」规则由此承载。
3. **尺寸约束预检** —— 黑底 MP4 的偶数规则（yuv420p 的 2×2 色度抽样）、
   HAP 的 4 倍数规则（仅对已移除的 hap 作说明性报告）。
   约束不满足时返回可读错误文案，由调用方停止并报告，不缩放、不裁切。
4. **环境信息** —— av 版本、内置 FFmpeg 库版本、avfilter 是否可用、
   系统 ffmpeg 是否存在（仅诊断：本工具不依赖它）。

本模块不写任何文件、不修改任何状态。
"""

import platform
import shutil
from dataclasses import asdict, dataclass

import av
from av.codec.codec import UnknownCodecError

from .formats import FORMATS, OPEN_TIME_BASE, REMOVED_FORMATS, FormatSpec

#: 探测用的画布尺寸：对全部约束（偶数、4 倍数）都合法的最小值。
_PROBE_SIZE = 16

#: 验证管线需要显式指定的解码器（旧 SKILL.md §4.2：不可按扩展名猜 decoder）。
VERIFICATION_DECODERS: tuple[str, ...] = ("libvpx-vp9", "libvpx", "gif")


@dataclass
class FormatStatus:
    """一条输出路径的探测结果。

    Attributes:
        key / label / codec / pix_fmt: 同 FormatSpec。
        offered: 该路径是否在受支持矩阵中（hap 为 False）。
        available: encoder 是否可用（已移除路径也可能测得可用性事实）。
        reason: 不可用 / 移除原因（可读文案；可用时为空串）。
        removed_note: 仅已移除路径非空：移除决策说明。
    """

    key: str
    label: str
    codec: str
    pix_fmt: str
    offered: bool
    available: bool
    reason: str = ""
    removed_note: str = ""


def _try_open_encoder(spec: FormatSpec) -> tuple[bool, str]:
    """用 writer 的确切配置尝试创建并打开 encoder，返回 (可用, 失败原因)。"""
    try:
        cc = av.codec.CodecContext.create(spec.codec, "w")
    except UnknownCodecError:
        return False, f"encoder 不存在：本环境的 PyAV wheel 未提供 '{spec.codec}'"
    except Exception as e:  # noqa: BLE001 —— 探测要如实报告一切失败形态
        return False, f"encoder 创建失败：{type(e).__name__}: {e}"
    try:
        cc.width = cc.height = _PROBE_SIZE
        cc.pix_fmt = spec.pix_fmt
        cc.time_base = OPEN_TIME_BASE
        cc.options = dict(spec.writer_options)
        cc.open()
    except Exception as e:  # noqa: BLE001
        return False, f"encoder 拒绝配置（pix_fmt={spec.pix_fmt}）：{type(e).__name__}: {e}"
    return True, ""


def probe_format(spec: FormatSpec, *, offered: bool = True) -> FormatStatus:
    """探测一条输出路径。offered=False 表示已移除路径（仍如实测能力）。"""
    available, reason = _try_open_encoder(spec)
    status = FormatStatus(
        key=spec.key,
        label=spec.label,
        codec=spec.codec,
        pix_fmt=spec.pix_fmt,
        offered=offered,
        available=available,
        reason=reason,
    )
    if not offered:
        status.removed_note = spec.note
        if available:
            # 某个未来 wheel 若真的编入了 hap encoder，如实提示可重新评估，
            # 但移除是决策而非能力问题，默认不自动恢复。
            status.removed_note += "（探测到 encoder 现已存在：可重新评估移除决策）"
    return status


def probe_all_formats() -> list[FormatStatus]:
    """按文档矩阵顺序探测全部受支持路径 + 已移除路径。"""
    statuses = [probe_format(spec, offered=True) for spec in FORMATS.values()]
    statuses += [probe_format(spec, offered=False) for spec in REMOVED_FORMATS.values()]
    return statuses


def probe_decoders() -> dict[str, bool]:
    """验证管线依赖的解码器是否可用（显式 libvpx-vp9/libvpx 是 §4.2 硬规则）。"""
    out: dict[str, bool] = {}
    for name in VERIFICATION_DECODERS:
        try:
            av.codec.CodecContext.create(name, "r")
            out[name] = True
        except Exception:  # noqa: BLE001
            out[name] = False
    return out


def size_error(spec: FormatSpec, width: int, height: int) -> str | None:
    """尺寸约束预检：返回可读错误文案；``None`` 表示通过。

    对应旧 SKILL.md 的硬规则：约束不满足就停止并报告，不缩放、不裁切、不补边。
    """
    if spec.key == "mp4-black":
        if width % 2 or height % 2:
            bad = "宽" if width % 2 else "高"
            val = width if width % 2 else height
            return (
                f"{bad} {val} 不是偶数：yuv420p 的 2×2 色度抽样要求宽高均为偶数"
                "（黑底 MP4 路径）。不缩放、不裁切、不补边。"
            )
        return None
    if spec.key == "hap":
        # HAP 已移除，此分支仅在显式查询时给出约束说明。
        if width % 4 or height % 4:
            return f"Video size {width}x{height} is not multiple of 4.（HAP 要求宽高均为 4 的倍数）"
        return None
    # WebM 侧（vp9/vp9-lossless/vp8）与 MOV 无损路径无偶数/倍数约束（实测 999×999 正常）。
    return None


def _fmt_lib_versions() -> dict[str, str]:
    return {k: ".".join(str(x) for x in v) for k, v in av.library_versions.items()}


def probe_environment() -> dict:
    """环境信息（只读）。system_ffmpeg 仅作诊断展示，本工具不依赖它。"""
    try:
        import av.filter  # noqa: F401

        avfilter = True
    except Exception:  # noqa: BLE001
        avfilter = False
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "av_version": av.__version__,
        "ffmpeg_libraries": _fmt_lib_versions(),
        "avfilter_available": avfilter,
        "system_ffmpeg": shutil.which("ffmpeg"),  # 诊断用；预期为 null
    }


def probe_all() -> dict:
    """一次跑完全部探测，返回可直接 json.dumps 的字典。"""
    return {
        "environment": probe_environment(),
        "formats": [asdict(s) for s in probe_all_formats()],
        "decoders": probe_decoders(),
    }
