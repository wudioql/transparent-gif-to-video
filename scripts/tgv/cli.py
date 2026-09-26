"""Argument parsing and dispatch."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .common import SkillError, internal_error, print_json
from .analysis import inspect_source
from .background import suggest_background
from .convert import convert
from .encoding import CODECS, DEFAULT_CRF
from .verification import verify

def build_parser() -> argparse.ArgumentParser:
    sequence_parent = argparse.ArgumentParser(add_help=False)
    sequence_parent.add_argument(
        "--sequence-duration-ms",
        type=float,
        default=None,
        help="图片序列/单帧素材的每帧时长（毫秒）；GIF/APNG 自带时长不受此项影响",
    )
    tools_parent = argparse.ArgumentParser(add_help=False)
    tools_parent.add_argument("--ffmpeg", default="ffmpeg")

    parser = argparse.ArgumentParser(
        description="透明 GIF/PNG/APNG → 保留 alpha 或合成实色背景的视频；严格且可疑感知的 auto-edge 检测。"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_parser = sub.add_parser(
        "inspect", parents=[sequence_parent], help="检查帧数、时长、alpha 分布和透明边缘色；不需要 ffmpeg"
    )
    inspect_parser.add_argument("input", help="GIF、PNG、APNG 或图片序列目录")
    inspect_parser.add_argument(
        "--no-edge-scan", action="store_true", help="跳过透明边缘色扫描（大素材更快；auto-edge 需要它）"
    )

    convert_parser = sub.add_parser(
        "convert", parents=[sequence_parent, tools_parent], help="准备帧并调用 ffmpeg 编码"
    )
    convert_parser.add_argument("input", help="GIF、PNG、APNG 或图片序列目录")
    convert_parser.add_argument("output")
    mode = convert_parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--keep-alpha", action="store_true", help="保留 alpha；默认自动选 VP9，或由 --codec 指定")
    mode.add_argument(
        "--background",
        help="合成背景色，如 #000000；auto-edge 仅在透明边缘严格单色且不可疑时允许；"
             "ask 表示先分析再与用户确认（非交互环境会输出候选清单并退出）",
    )
    convert_parser.add_argument(
        "--allow-suspicious-edge-colour",
        action="store_true",
        help="允许接受可疑的 auto-edge 结果（例如全零 RGB 造成的 #000000）",
    )
    convert_parser.add_argument(
        "--bleed-edges",
        type=int,
        default=None,
        metavar="N",
        help="保留 alpha 时把可见颜色向透明区外扩 N 轮（建议 2），减少有损编码在浅色背景上的黑边；"
             "默认 0，因为实测二值 alpha + VP9 的收益很小且在深色背景上为负；不改变合成结果",
    )
    convert_parser.add_argument("--codec", choices=["auto", *CODECS], default="auto")
    convert_parser.add_argument(
        "--crf", type=int, default=None,
        help=f"VP8/VP9/H.264 的 CRF；默认按编码器取值 {DEFAULT_CRF}",
    )
    convert_parser.add_argument("--preset", default=None, help="H.264 preset（默认 slow）")
    convert_parser.add_argument("--cpu-used", type=int, default=None, help="VP8/VP9 编码速度档位（默认 2）")
    convert_parser.add_argument("--lossless", action="store_true", help="VP9 无损；其他编码器不适用")
    convert_parser.add_argument("--dry-run", action="store_true", help="准备帧并打印 ffmpeg 命令，但不编码")

    suggest_parser = sub.add_parser(
        "suggest-background",
        parents=[sequence_parent],
        help="分析透明区域并给出带信任度的背景色候选与可直接提问的话术；不需要 ffmpeg",
    )
    suggest_parser.add_argument("input", help="GIF、PNG、APNG 或图片序列目录")
    suggest_parser.add_argument(
        "--preview", metavar="PNG", default=None, help="额外渲染一张候选背景对比图，便于用户看图决定"
    )

    verify_parser = sub.add_parser(
        "verify", parents=[sequence_parent, tools_parent], help="校验输出尺寸、帧数、时长、像素格式和解码 alpha"
    )
    verify_parser.add_argument("input", help="原始 GIF/PNG/图片序列目录，用来对照尺寸、帧数与透明性")
    verify_parser.add_argument("output", help="待校验视频")
    verify_parser.add_argument("--expect", choices=["alpha", "opaque"], required=True)
    verify_parser.add_argument(
        "--sample-frames", type=int, default=3, help="解码抽样帧数（首/中/末均匀取样），默认 3"
    )
    verify_parser.add_argument("--ffprobe", default="ffprobe")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            print_json(
                inspect_source(
                    Path(args.input).expanduser().resolve(),
                    args.sequence_duration_ms,
                    scan_edges=not args.no_edge_scan,
                )
            )
            return 0
        if args.command == "suggest-background":
            return suggest_background(args)
        if args.command == "convert":
            return convert(args)
        if args.command == "verify":
            return verify(args)
        internal_error(f"未接线的子命令: {args.command}")
    except SkillError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
