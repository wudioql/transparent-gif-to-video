"""转换核心（Phase 3）：计划构建 → 展示/确认 → 编码执行。

设计：formats.py 的 FormatSpec 注册表驱动**单一通用 writer**，而不是每种
格式一个 writer 模块——8 条路径的执行流程完全同构（add_stream → 配置 →
逐帧 reformat + pts 透传 → encode/mux → flush），差异全部落在注册表的
codec / pix_fmt / muxer / options 字段上。Phase 1 的 probe 用**同一份配置**
做过 open 验证，因此「probe 通过 ⇒ writer 可用」。

旧命令语义映射：

    -n（默认拒绝覆盖）/ -y           →  输出存在且未获 --overwrite 时拒绝执行
    计划展示并等待明确确认            →  render_plan + CLI 的确认闸（--yes / 交互 y）
    -ignore_loop 1                   →  GifSource
    -map 0:v:0 -an                   →  只解 video 流（GIF 无音频）
    -fps_mode passthrough            →  GifFrame.pts 原样写入输出帧
    -enc_time_base demux             →  encoder time_base 取源 demuxer 的 1/100
    缺 encoder / 尺寸约束不满足       →  build_plan 阶段即停止，不偷换格式、不缩放
    黑底（仅 VP8/VP9，且须显式要求）  →  filters.apply_black_background
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import av

from . import filters, probe
from .formats import FORMATS, REMOVED_FORMATS, FormatSpec
from .source import GifInfo, GifSource, GifSourceError

#: 允许黑色归一化的路径（旧 skill 口径：仅在 VP8/VP9 链路验证过）。
BLACK_ALLOWED_KEYS = frozenset({"vp9", "vp9-lossless", "vp8"})


class ConvertError(Exception):
    """转换失败（预检不过 / 覆盖保护 / 编码错误）。输出文件可能不完整。"""


@dataclass
class ConvertPlan:
    """已通过全部预检的转换计划（render_plan 的数据源）。"""

    input_path: Path
    output_path: Path
    spec: FormatSpec
    info: GifInfo
    black_background: bool
    overwrite: bool
    output_exists: bool

    @property
    def background_strategy(self) -> str:
        if self.spec.key == "mp4-black":
            return "黑底（合成到黑后丢弃 alpha，永久失去透明）"
        if self.black_background:
            return "透明 RGB 黑色归一化（黑色回退；不修复播放器 alpha 兼容性）"
        return "保留源 GIF 透明区底层 RGB（默认，不预乘）"


@dataclass
class ConvertResult:
    """执行结果摘要。"""

    output_path: Path
    output_size_bytes: int
    frames_written: int
    source_duration_seconds: float | None
    alpha_preserved: bool


# ------------------------------------------------------------------ 计划 --


def build_plan(
    input_path: str | Path,
    output_path: str | Path | None = None,
    format_key: str = "vp9",
    *,
    black_background: bool = False,
    overwrite: bool = False,
) -> ConvertPlan:
    """只读构建转换计划：全部预检在这里完成，失败即抛 ConvertError。"""
    if format_key in REMOVED_FORMATS:
        raise ConvertError(REMOVED_FORMATS[format_key].note)
    if format_key not in FORMATS:
        raise ConvertError(
            f"未知格式 '{format_key}'。可用：{', '.join(FORMATS)}；"
            f"已移除：{', '.join(REMOVED_FORMATS)}"
        )
    spec = FORMATS[format_key]

    src = GifSource(input_path)  # 文件不存在等 → GifSourceError
    info = src.info  # 只读全帧扫描

    # 尺寸约束（不缩放、不裁切、不补边）
    size_err = probe.size_error(spec, info.width, info.height)
    if size_err:
        raise ConvertError(size_err)

    # 黑底策略的硬边界
    if black_background and spec.key not in BLACK_ALLOWED_KEYS:
        raise ConvertError(
            f"黑色归一化仅支持 {'/'.join(sorted(BLACK_ALLOWED_KEYS))} 链路"
            "（旧 skill 口径：仅在 VP8/VP9 验证过，MOV 类需另行验证，不得直接套用）。"
            f"请求的格式是 {spec.key}。"
        )

    # encoder 可用性（用 writer 的确切配置验证；缺即停，不偷换）
    status = probe.probe_format(spec)
    if not status.available:
        raise ConvertError(f"格式 {spec.key} 不可用：{status.reason}")

    # 输出路径
    in_path = Path(input_path)
    out_path = Path(output_path) if output_path else in_path.with_suffix(spec.ext)
    if not out_path.parent.is_dir():
        raise ConvertError(f"输出目录不存在：{out_path.parent}")
    if out_path.exists() and out_path.is_file() and out_path.resolve() == in_path.resolve():
        raise ConvertError(f"输出路径与输入相同，拒绝执行：{out_path}")

    return ConvertPlan(
        input_path=in_path,
        output_path=out_path,
        spec=spec,
        info=info,
        black_background=black_background,
        overwrite=overwrite,
        output_exists=out_path.exists(),
    )


def plan_to_dict(plan: ConvertPlan) -> dict:
    return {
        "input": str(plan.input_path),
        "output": str(plan.output_path),
        "format": plan.spec.key,
        "label": plan.spec.label,
        "codec": plan.spec.codec,
        "pix_fmt": plan.spec.pix_fmt,
        "lossy": plan.spec.lossy,
        "alpha": plan.spec.alpha,
        "writer_options": dict(plan.spec.writer_options),
        "muxer_options": dict(plan.spec.muxer_options),
        "canvas": f"{plan.info.width}x{plan.info.height}",
        "frames": plan.info.frame_count,
        "duration_seconds": plan.info.duration_seconds,
        "pts_seconds": list(plan.info.pts_seconds_list),
        "background_strategy": plan.background_strategy,
        "overwrite_allowed": plan.overwrite,
        "output_exists": plan.output_exists,
        "note": plan.spec.note,
    }


def render_plan(plan: ConvertPlan) -> str:
    """计划展示（对应旧 skill「转换前必须展示计划并等待确认」）。"""
    info = plan.info
    lines = [
        "转换计划（未执行）：",
        f"  输入    : {plan.input_path}（{info.width}×{info.height} / "
        f"{info.frame_count} 帧 / {info.duration_seconds if info.duration_seconds is not None else '?'}s）",
        f"  格式    : {plan.spec.label}（codec={plan.spec.codec}，pix_fmt={plan.spec.pix_fmt}，"
        f"{'有损' if plan.spec.lossy else '无损'}，{'保留 alpha' if plan.spec.alpha else '不保留 alpha'}）",
        f"  关键参数: {' '.join(f'{k}={v}' for k, v in plan.spec.writer_options.items()) or '（默认）'}",
        f"  输出    : {plan.output_path}",
        f"  底色策略: {plan.background_strategy}",
    ]
    if plan.spec.note:
        lines.append(f"  说明    : {plan.spec.note}")
    if plan.output_exists:
        lines.append(
            f"  覆盖行为: 输出已存在——默认拒绝覆盖；确认覆盖该文件须显式加 --overwrite"
            if not plan.overwrite
            else "  覆盖行为: 输出已存在，已获 --overwrite 明确确认，将覆盖"
        )
    else:
        lines.append("  覆盖行为: 输出不存在，将创建")
    return "\n".join(lines)


# ------------------------------------------------------------------ 执行 --


def execute(plan: ConvertPlan) -> ConvertResult:
    """执行计划（写文件）。调用方须已完成计划确认；覆盖须已通过 --overwrite。"""
    if plan.output_exists and not plan.overwrite:
        raise ConvertError(
            f"输出已存在，拒绝覆盖（-n 语义）：{plan.output_path}。"
            "确认覆盖该具体文件请使用 --overwrite。"
        )

    spec = plan.spec
    source = GifSource(plan.input_path)
    frames_written = 0
    container = None
    try:
        container = av.open(
            str(plan.output_path), "w", format=spec.muxer,
            options=dict(spec.muxer_options) if spec.muxer_options else None,
        )
        stream = container.add_stream(spec.codec, rate=100)
        cc = stream.codec_context
        cc.width = plan.info.width
        cc.height = plan.info.height
        cc.pix_fmt = spec.pix_fmt
        cc.time_base = plan.info.time_base  # 与 GIF demuxer 一致（-enc_time_base demux）
        cc.options = dict(spec.writer_options)

        # 逐帧时长保留（含最后一帧）：libvpx/x264 会缓冲帧、包集中在 flush
        # 吐出且 duration=0，因此按 pts 建「源帧 → duration」映射，在 mux 前
        # 写回包上——Matroska 的 BlockDuration 与容器 Duration 由此得到
        # 正确值（实测：不设置时 3×100ms 素材的 WebM 总时长会缩成 210ms，
        # 而 ffmpeg CLI 产出 300ms；2026-10-03 定位）。
        duration_by_pts: dict[int, int] = {}

        def _mux(pkt) -> None:
            if pkt is not None and pkt.pts is not None:
                d = duration_by_pts.get(int(pkt.pts))
                if d and d > 0:
                    pkt.duration = d
            container.mux(pkt)

        for gf in source.iter_frames():
            if gf.frame.duration:
                duration_by_pts[int(gf.pts)] = int(gf.frame.duration)
            frame = gf.frame
            if plan.black_background or spec.key == "mp4-black":
                frame = filters.apply_black_background(frame)
            frame = frame.reformat(format=spec.pix_fmt)
            frame.pts = gf.pts  # 原样透传（-fps_mode passthrough）
            frame.time_base = gf.time_base
            if gf.frame.duration:
                frame.duration = int(gf.frame.duration)
            for pkt in stream.encode(frame):
                _mux(pkt)
            frames_written += 1
        for pkt in stream.encode(None):  # flush
            _mux(pkt)
        container.close()
        container = None
    except ConvertError:
        raise
    except Exception as e:  # noqa: BLE001 —— 统一转可读错误；输出视为可能不完整
        raise ConvertError(
            f"编码失败（输出可能不完整，请检查 {plan.output_path}）：{type(e).__name__}: {e}"
        ) from e
    finally:
        if container is not None:
            try:
                container.close()
            except Exception:  # noqa: BLE001 —— 清理失败不掩盖原错误
                pass

    if frames_written != plan.info.frame_count:
        raise ConvertError(
            f"写入帧数 {frames_written} 与源帧数 {plan.info.frame_count} 不一致，"
            f"输出可能不完整：{plan.output_path}"
        )
    return ConvertResult(
        output_path=plan.output_path,
        output_size_bytes=plan.output_path.stat().st_size,
        frames_written=frames_written,
        source_duration_seconds=plan.info.duration_seconds,
        alpha_preserved=spec.alpha,
    )
