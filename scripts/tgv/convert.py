"""The convert command: wire analysis, background, staging, timing and
encoding together, and never guess on the user's behalf.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from .common import fail, format_rgb, is_auto_edge, parse_rgb, print_json, require_tool, warn
from .analysis import SourceStats, inspect_source
from .background import build_background_proposal, prompt_for_background, resolve_background
from .encoding import (
    CPU_USED_CODECS,
    DEFAULT_CPU_USED,
    DEFAULT_PRESET,
    DEFAULT_CRF,
    EVEN_SIZE_CODECS,
    PRESET_CODECS,
    RATE_CONTROLLED_CODECS,
    build_encode_command,
    ffmpeg_supports_enc_time_base,
    infer_codec,
    validate_codec_choice,
)
from .staging import default_bleed_iterations, stage_frames
from .timing import plan_timing

def convert(args: argparse.Namespace) -> int:
    source = Path(args.input).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if source == output:
        fail("输入和输出路径不能相同。")
    if not source.is_file() and not source.is_dir():
        fail(f"输入文件或图片序列目录不存在: {source}")
    output.parent.mkdir(parents=True, exist_ok=True)

    keep_alpha = bool(args.keep_alpha)
    asks_user = not keep_alpha and (args.background or "").strip().lower() == "ask"
    needs_edge_scan = not keep_alpha and (is_auto_edge(args.background or "") or asks_user)

    # auto-edge is the only mode that needs a global answer before staging, so
    # it is the only mode that pays for a second decoding pass.
    background: tuple[int, int, int] | None = None
    prescan_report: dict | None = None
    if needs_edge_scan:
        prescan_report = inspect_source(source, args.sequence_duration_ms, scan_edges=True)
        if asks_user:
            if sys.stdin.isatty():
                background = prompt_for_background(prescan_report)
            else:
                # Non-interactive caller (agent, CI): hand back a structured
                # proposal instead of guessing, so it can ask the actual human.
                print_json(build_background_proposal(prescan_report))
                fail(
                    "--background ask 需要有人回答。上面是可直接提问的候选清单；"
                    "拿到答复后改用 --background '#RRGGBB'。"
                )
        else:
            background = resolve_background(args.background, prescan_report, args.allow_suspicious_edge_colour)
    elif not keep_alpha:
        background = parse_rgb(args.background)

    with tempfile.TemporaryDirectory(prefix="transparent-gif-to-video-") as temp:
        staging_dir = Path(temp)
        stats = SourceStats(path=source, scan_edges=False) if prescan_report is None else None
        codec_preview = args.codec if args.codec != "auto" else infer_codec(keep_alpha, output)
        bleed = (
            default_bleed_iterations(keep_alpha, codec_preview, args.lossless)
            if args.bleed_edges is None
            else args.bleed_edges
        )
        frame_paths, durations = stage_frames(
            source, staging_dir, keep_alpha, background, args.sequence_duration_ms, stats, bleed
        )
        report = prescan_report if prescan_report is not None else stats.report()  # type: ignore[union-attr]

        codec = args.codec if args.codec != "auto" else infer_codec(keep_alpha, output)
        validate_codec_choice(codec, keep_alpha, args.lossless)
        if codec in EVEN_SIZE_CODECS and (report["width"] % 2 or report["height"] % 2):
            fail(
                f"{codec} 的 4:2:0 输出要求偶数尺寸，源为 {report['width']}x{report['height']}。"
                "请先明确缩放/裁切策略后再转换；脚本不自动改变源画面。"
            )
        crf = args.crf if args.crf is not None else DEFAULT_CRF.get(codec, 30)
        preset = args.preset if args.preset is not None else DEFAULT_PRESET
        cpu_used = args.cpu_used if args.cpu_used is not None else DEFAULT_CPU_USED
        warn_ignored_options(args, codec)

        plan = plan_timing(durations)
        print("源素材:")
        print_json(report)
        if background is not None:
            print(f"不透明化背景: {format_rgb(background)}")
        elif keep_alpha:
            if bleed:
                print(f"透明边缘外扩: {bleed} 轮（只改透明像素的 RGB，不改 alpha，合成结果不变）")
        print(
            f"输出编码: {codec}; 时间轴: {plan.mode}"
            + (f" @ {plan.framerate} fps ({plan.output_frames} 帧)" if plan.framerate else "")
            + f"; 输出文件: {output}"
        )

        ffmpeg = args.ffmpeg if args.dry_run else require_tool(args.ffmpeg)
        pin_time_base = True
        if not args.dry_run and not ffmpeg_supports_enc_time_base(ffmpeg):
            pin_time_base = False
            warn(
                "当前 ffmpeg 不支持 -enc_time_base，编码器时间基可能退回 1/25，"
                "非 40ms 整数倍的帧时长会被重新量化（30ms → 40ms）。建议升级 ffmpeg 到 6.0+。"
            )
        command = build_encode_command(
            ffmpeg, plan, frame_paths, staging_dir, output, keep_alpha,
            codec, crf, preset, cpu_used, args.lossless, pin_time_base,
        )
        if args.dry_run:
            print("将执行:")
            print(shlex.join(command))
            return 0
        print("开始编码…")
        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as exc:
            fail(f"ffmpeg 编码失败，退出码 {exc.returncode}。输出文件可能不完整。")

    print(f"已生成: {output}")
    return 0


# Options that only apply to some codecs. Each entry is
# (attribute, applicable codecs, human name); the CLI defaults them to None so
# "user actually asked for this" is distinguishable from "default value",
# instead of comparing against a literal copy of the default.
CODEC_SPECIFIC_OPTIONS = (
    ("crf", RATE_CONTROLLED_CODECS, "--crf"),
    ("preset", PRESET_CODECS, "--preset"),
    ("cpu_used", CPU_USED_CODECS, "--cpu-used"),
)


def warn_ignored_options(args: argparse.Namespace, codec: str) -> None:
    for attribute, applicable, name in CODEC_SPECIFIC_OPTIONS:
        value = getattr(args, attribute, None)
        if value is not None and codec not in applicable:
            warn(f"{name} 对 {codec} 无效（仅适用于 {', '.join(sorted(applicable))}），已忽略 {value}。")


# --------------------------------------------------------------------------
