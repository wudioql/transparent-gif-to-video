"""Proving the output is what was asked for, by decoding it rather than by
trusting its metadata.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Sequence

from .common import fail, print_json, require_tool
from .analysis import inspect_source

def run_json_command(command: Sequence[str]) -> dict:
    try:
        proc = subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        fail((exc.stderr or "").strip() or f"命令失败: {' '.join(command)}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        fail(f"无法解析 ffprobe 输出: {proc.stdout[:500]}")


def probe_video(path: Path, ffprobe: str) -> dict:
    return run_json_command(
        [
            ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0",
            "-show_entries",
            "stream=codec_name,width,height,pix_fmt,nb_read_frames,duration,avg_frame_rate,r_frame_rate",
            "-show_entries", "format=duration",
            "-of", "json", str(path),
        ]
    )


def decoder_args(codec_name: str) -> list[str]:
    # ffprobe's native WebM path ignores the BlockAdditional alpha plane, so
    # alpha must be checked through libvpx.
    if codec_name == "vp9":
        return ["-c:v", "libvpx-vp9"]
    if codec_name == "vp8":
        return ["-c:v", "libvpx"]
    return []


def decode_frame_rgba(path: Path, ffmpeg: str, codec_name: str, width: int, height: int, index: int) -> bytes:
    if width <= 0 or height <= 0:
        fail(f"输出视频尺寸无效: {width}x{height}")
    selector = ["-vf", f"select=eq(n\\,{index})", "-fps_mode", "passthrough"] if index else []
    command = [
        ffmpeg, "-v", "error", *decoder_args(codec_name), "-i", str(path),
        *selector, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-",
    ]
    try:
        proc = subprocess.run(command, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        fail(f"无法解码输出第 {index} 帧: {(exc.stderr or b'').decode(errors='replace')}")
    expected = width * height * 4
    if len(proc.stdout) < expected:
        fail(f"输出第 {index} 帧数据不足：得到 {len(proc.stdout)} bytes，期望至少 {expected}。")
    return proc.stdout[:expected]


def sample_indices(frame_count: int | None, requested: int) -> list[int]:
    if not frame_count or frame_count < 1:
        return [0]
    requested = max(1, min(requested, frame_count))
    if requested == 1:
        return [0]
    step = (frame_count - 1) / (requested - 1)
    return sorted({int(round(i * step)) for i in range(requested)})


def verify(args: argparse.Namespace) -> int:
    source = Path(args.input).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if not source.is_file() and not source.is_dir():
        fail(f"verify 的输入必须是存在的文件或图片序列目录: {source}")
    if not output.is_file():
        fail(f"verify 的输出视频不存在: {output}")
    ffmpeg = require_tool(args.ffmpeg)
    ffprobe = require_tool(args.ffprobe)

    source_report = inspect_source(source, args.sequence_duration_ms, scan_edges=False)
    probe = probe_video(output, ffprobe)
    streams = probe.get("streams") or []
    if not streams:
        fail("输出没有视频流。")
    stream = streams[0]
    width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
    codec_name = stream.get("codec_name") or ""
    pix_fmt = (stream.get("pix_fmt") or "").lower()
    raw_frames = stream.get("nb_read_frames")
    try:
        output_frame_count = int(raw_frames) if raw_frames is not None else None
    except (TypeError, ValueError):
        output_frame_count = None

    indices = sample_indices(output_frame_count, args.sample_frames)
    samples = []
    for index in indices:
        raw = decode_frame_rgba(output, ffmpeg, codec_name, width, height, index)
        alpha = raw[3::4]
        samples.append({"index": index, "alpha_min": min(alpha), "alpha_max": max(alpha)})
    alpha_min = min(sample["alpha_min"] for sample in samples)
    alpha_max = max(sample["alpha_max"] for sample in samples)

    output_duration = stream.get("duration") or (probe.get("format") or {}).get("duration")
    try:
        output_duration_float = float(output_duration) if output_duration is not None else None
    except (TypeError, ValueError):
        output_duration_float = None

    checks: dict[str, bool] = {}
    result = {
        "source": source_report,
        "output": {
            "path": str(output),
            "codec": codec_name,
            "width": width,
            "height": height,
            "pix_fmt": pix_fmt,
            "frames": output_frame_count,
            "duration_seconds": output_duration_float,
            "r_frame_rate": stream.get("r_frame_rate"),
            "average_frame_rate": stream.get("avg_frame_rate"),
            "sampled_frames": samples,
        },
        "notes": [],
        "checks": checks,
    }
    notes: list[str] = result["notes"]  # type: ignore[assignment]

    plan = source_report["timing"]["plan"]
    expected_frames = plan["output_frames"]
    if expected_frames != source_report["frames"]:
        notes.append(
            f"源 {source_report['frames']} 帧按 {plan['framerate']} fps 的统一网格展开为 "
            f"{expected_frames} 帧（重复帧不改变画面时序，只让时间戳和总时长精确）。"
        )
    result["output"]["expected_frames"] = expected_frames
    checks["dimensions_match"] = (width, height) == (source_report["width"], source_report["height"])
    checks["frame_count_match"] = output_frame_count == expected_frames
    tolerance = max(0.05, source_report["duration_seconds"] * 0.02)
    expected_duration = source_report["duration_seconds"]
    if not plan["duration_is_exact"]:
        notes.append("VFR 路径无法把最后一帧时长写入容器，时长校验按缺少最后一帧处理。")
        expected_duration -= source_report["frame_duration_seconds"]["max"]
        tolerance = max(tolerance, source_report["frame_duration_seconds"]["max"])
    checks["duration_close"] = (
        output_duration_float is not None
        and abs(output_duration_float - expected_duration) <= tolerance
    )
    has_alpha_pix_fmt = any(token in pix_fmt for token in ("yuva", "rgba", "argb", "bgra", "abgr", "ya8", "ya16"))

    if args.expect == "alpha":
        if not source_report["alpha"]["has_transparency"]:
            notes.append("源素材本身没有透明像素，alpha 校验只能证明容器/像素格式支持 alpha，不能证明透明被保留。")
        checks["alpha_capable_output"] = has_alpha_pix_fmt or alpha_min < 255
        checks["transparent_pixels_decoded"] = (
            not source_report["alpha"]["has_transparency"] or alpha_min < 255
        )
    else:
        checks["alpha_absent_in_pix_fmt"] = not has_alpha_pix_fmt
        checks["decoded_frames_opaque"] = alpha_min == 255 and alpha_max == 255

    checks["all_pass"] = all(checks.values())
    print_json(result)
    if not checks["all_pass"]:
        fail("校验未通过；请检查上面的 checks 字段。")
    return 0


# --------------------------------------------------------------------------
