"""Codec policy and ffmpeg command construction."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence

from .common import fail, internal_error
from .sources import check_path_safe_for_concat
from .staging import materialise_cfr_sequence, write_concat_manifest
from .timing import CONCAT_INFLATE, TimingPlan

ALPHA_CODECS = {"vp9", "vp8", "prores4444", "png", "qtrle", "ffv1"}
OPAQUE_CODECS = {"vp9", "vp8", "h264"}
CODECS = sorted(ALPHA_CODECS | OPAQUE_CODECS)
RATE_CONTROLLED_CODECS = {"vp9", "vp8", "h264"}
EVEN_SIZE_CODECS = {"vp9", "vp8", "h264"}
# Codec defaults differ because the codecs differ. One global default was a
# bug: H.264 delivery at CRF 30 is visibly worse than the documented 18-22.
DEFAULT_CRF = {"vp9": 30, "vp8": 30, "h264": 20}
PRESET_CODECS = {"h264"}
DEFAULT_PRESET = "slow"
DEFAULT_CPU_USED = 2
CPU_USED_CODECS = {"vp8", "vp9"}

def ffmpeg_supports_enc_time_base(ffmpeg: str) -> bool:
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-h", "full"], capture_output=True, text=True, timeout=60
        )
    except Exception:  # pragma: no cover - environment dependent
        return False
    return "-enc_time_base" in (proc.stdout or "")


def infer_codec(keep_alpha: bool, output: Path) -> str:
    if not keep_alpha:
        return "h264"
    suffix = output.suffix.lower()
    if suffix == ".mov":
        return "prores4444"
    if suffix == ".mkv":
        return "ffv1"
    return "vp9"


def codec_options(codec: str, keep_alpha: bool, crf: int, preset: str, cpu_used: int, lossless: bool) -> list[str]:
    if codec == "vp9":
        options = ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p" if keep_alpha else "yuv420p"]
        if keep_alpha:
            options += ["-auto-alt-ref", "0"]
        options += ["-lossless", "1"] if lossless else ["-b:v", "0", "-crf", str(crf)]
        return options + ["-deadline", "good", "-cpu-used", str(cpu_used), "-row-mt", "1"]
    if codec == "vp8":
        options = ["-c:v", "libvpx", "-pix_fmt", "yuva420p" if keep_alpha else "yuv420p"]
        if keep_alpha:
            options += ["-auto-alt-ref", "0"]
        return options + ["-b:v", "0", "-crf", str(crf), "-deadline", "good", "-cpu-used", str(cpu_used)]
    if codec == "h264":
        return ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf), "-preset", preset, "-movflags", "+faststart"]
    if codec == "prores4444":
        return ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le"]
    if codec == "png":
        return ["-c:v", "png", "-pix_fmt", "rgba"]
    if codec == "qtrle":
        return ["-c:v", "qtrle", "-pix_fmt", "argb"]
    if codec == "ffv1":
        return [
            "-c:v", "ffv1", "-level", "3", "-coder", "1", "-context", "1",
            "-g", "1", "-slices", "4", "-slicecrc", "1", "-pix_fmt", "yuva444p",
        ]
    internal_error(f"codec_options 缺少分支: {codec}")


def validate_codec_choice(codec: str, keep_alpha: bool, lossless: bool) -> None:
    if codec not in CODECS:  # defensive: argparse already restricts the CLI
        fail(f"未知编码器 {codec!r}；可选: {', '.join(CODECS)}")
    if keep_alpha and codec not in ALPHA_CODECS:
        fail(f"{codec} 不支持 alpha；请改用 {', '.join(sorted(ALPHA_CODECS))}，或合成实色背景。")
    if not keep_alpha and codec not in OPAQUE_CODECS:
        fail(f"{codec} 是 alpha/母版编码器；不透明模式请使用 {', '.join(sorted(OPAQUE_CODECS))}。")
    if lossless and codec != "vp9":
        extra = "FFV1/PNG/qtrle 本身就是无损路径，去掉该参数即可。" if codec in ALPHA_CODECS else ""
        fail(f"--lossless 只适用于 VP9，当前为 {codec}。{extra}")


def build_encode_command(
    ffmpeg: str,
    plan: TimingPlan,
    frame_paths: Sequence[Path],
    staging_dir: Path,
    output: Path,
    keep_alpha: bool,
    codec: str,
    crf: int,
    preset: str,
    cpu_used: int,
    lossless: bool,
    pin_time_base: bool = True,
) -> list[str]:
    validate_codec_choice(codec, keep_alpha, lossless)
    command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "warning"]

    if plan.mode == "cfr":
        if plan.framerate is None:
            internal_error("CFR 计划缺少 framerate。")
        sequence_dir = materialise_cfr_sequence(frame_paths, plan.repeats, staging_dir)
        pattern = sequence_dir / "frame_%08d.png"
        check_path_safe_for_concat(pattern)
        command += [
            "-framerate", f"{plan.framerate.numerator}/{plan.framerate.denominator}",
            "-f", "image2", "-start_number", "0", "-i", str(pattern),
            "-frames:v", str(plan.output_frames),
        ]
    else:
        manifest = write_concat_manifest(
            staging_dir / "frames.ffconcat",
            frame_paths,
            [d * CONCAT_INFLATE for d in plan.durations_ms],
        )
        command += [
            "-f", "concat", "-safe", "0", "-i", str(manifest),
            "-frames:v", str(len(frame_paths)),
            "-vf", f"settb=1/1000,setpts=PTS/{CONCAT_INFLATE}",
            "-fps_mode", "passthrough",
        ]

    if pin_time_base:
        command += ["-enc_time_base", "1/1000"]
    command += ["-an", *codec_options(codec, keep_alpha, crf, preset, cpu_used, lossless), str(output)]
    return command


# --------------------------------------------------------------------------
