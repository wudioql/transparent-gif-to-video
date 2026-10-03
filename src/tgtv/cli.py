"""tgtv 命令行入口（Phase 1–3）。

子命令：
    tgtv probe [--json] [--require KEY[,KEY...]] [--size WxH]
        能力探测（只读）。--require 让退出码表达「预检是否通过」，
        供 agent 在展示转换计划前做脚本化预检（对应旧 SKILL.md §1）。

    tgtv convert <input.gif> [-f KEY] [-o PATH] [--black] [--overwrite]
                            [--yes] [--dry-run] [--json]
        转换（Phase 3）。流程：只读构建计划 → 展示 → 确认闸 → 执行。
        确认闸：交互终端提示输入 yes；非交互环境（agent）须先另行向用户
        展示计划并获得确认，再用 --yes。--dry-run 只展示计划。

不变量（落在 CLI 层，见分析文档 §6.3）：
    - 输出已存在时默认拒绝覆盖（旧 -n 语义），仅 --overwrite 明确确认后覆盖；
    - 缺 encoder / 尺寸约束不满足 → 停止并报告，不偷换格式、不缩放裁切；
    - --black（黑色归一化）仅 VP8/VP9 链路可用，且必须显式要求。

后续阶段将按 docs/python-only-refactor-analysis.md §7 增补：
    tgtv verify <output> ...       （Phase 5：全帧 alpha/时长/像素断言）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata

from . import __version__, convert, formats, probe
from .convert import ConvertError
from .source import GifSourceError

_SIZE_RE = re.compile(r"^(\d+)x(\d+)$")


def _pad(text: str, width: int) -> str:
    """按显示宽度补齐（中文等全角字符占 2 列）。"""
    display = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
    return text + " " * max(0, width - display)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tgtv",
        description="透明 GIF → 带 alpha 的视频（纯 Python 栈：PyAV + NumPy，不依赖系统 ffmpeg）",
    )
    parser.add_argument("--version", action="version", version=f"tgtv {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p_probe = sub.add_parser(
        "probe",
        help="能力探测（只读）：encoder 可用性 / 验证解码器 / 尺寸约束 / 环境信息",
    )
    p_probe.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON（机读），默认为中文表格（人读）",
    )
    p_probe.add_argument(
        "--require",
        metavar="KEY[,KEY...]",
        help="要求指定格式可用（如 vp9 或 vp9,mp4-black）；任一缺失/不满足则退出码 1",
    )
    p_probe.add_argument(
        "--size",
        metavar="WxH",
        help="目标画布尺寸（如 999x999），结合 --require 做尺寸约束预检；须与 --require 同用",
    )
    p_probe.set_defaults(func=_cmd_probe)

    p_conv = sub.add_parser(
        "convert",
        help="转换单个透明 GIF（先展示计划，确认后执行）",
    )
    p_conv.add_argument("input", help="输入 .gif 路径（一次一个，按内容探测必须是 GIF）")
    p_conv.add_argument(
        "-f", "--format", default="vp9", metavar="KEY",
        help="输出格式（默认 vp9；可用：%(default)s 之外的见 tgtv probe）",
    )
    p_conv.add_argument("-o", "--output", metavar="PATH", help="输出路径（默认输入同目录换扩展名）")
    p_conv.add_argument(
        "--black", action="store_true",
        help="透明 RGB 黑色归一化（仅 VP8/VP9 链路；不修复播放器 alpha 兼容性）",
    )
    p_conv.add_argument(
        "--overwrite", action="store_true",
        help="确认覆盖既有输出文件（默认拒绝覆盖，-n 语义）",
    )
    p_conv.add_argument(
        "--yes", action="store_true",
        help="跳过交互确认（计划已经向用户展示并获确认后使用）",
    )
    p_conv.add_argument(
        "--dry-run", action="store_true", help="只展示计划，不执行、不写任何文件"
    )
    p_conv.add_argument("--json", action="store_true", help="计划与结果用 JSON 输出")
    p_conv.set_defaults(func=_cmd_convert)
    return parser


def _parse_size(text: str) -> tuple[int, int]:
    m = _SIZE_RE.match(text)
    if not m:
        raise ValueError(f"无法解析尺寸 '{text}'（应为 WxH，例如 999x999）")
    w, h = int(m.group(1)), int(m.group(2))
    if w <= 0 or h <= 0:
        raise ValueError(f"尺寸必须为正整数：{w}x{h}")
    return w, h


def _render_probe_human(report: dict) -> str:
    env = report["environment"]
    lines = []
    lines.append("tgtv 能力探测（只读）")
    lines.append(
        f"Python {env['python']} ({env['implementation']}) | av {env['av_version']}"
        f"（内置 FFmpeg：{', '.join(f'{k} {v}' for k, v in env['ffmpeg_libraries'].items())}）"
    )
    sysff = env["system_ffmpeg"]
    lines.append(
        f"avfilter：{'可用' if env['avfilter_available'] else '不可用'} | "
        f"系统 ffmpeg：{sysff if sysff else '不存在（本工具不依赖它）'}"
    )
    lines.append("")
    lines.append("输出格式：")
    for f in report["formats"]:
        if f["offered"]:
            mark = "✅ 可用" if f["available"] else f"❌ {f['reason']}"
        else:
            mark = "⛔ 已移除：" + (f["removed_note"] or f["reason"])
        lines.append(
            f"  {_pad(f['key'], 13)} {_pad(f['label'], 30)} {_pad(f['codec'], 12)} "
            f"{_pad(f['pix_fmt'], 13)} {mark}"
        )
    lines.append("")
    lines.append(
        "验证解码器：" + "  ".join(
            f"{name} {'✅' if ok else '❌'}" for name, ok in report["decoders"].items()
        )
    )
    return "\n".join(lines)


def _cmd_probe(args: argparse.Namespace) -> int:
    report = probe.probe_all()

    require_keys: list[str] = []
    if args.require:
        require_keys = [k.strip() for k in args.require.split(",") if k.strip()]
        known = set(formats.FORMATS) | set(formats.REMOVED_FORMATS)
        unknown = [k for k in require_keys if k not in known]
        if unknown:
            print(
                f"未知格式：{', '.join(unknown)}。可用键：{', '.join(known)}",
                file=sys.stderr,
            )
            return 2

    size: tuple[int, int] | None = None
    if args.size:
        if not require_keys:
            print("--size 必须与 --require 同用（尺寸约束按所选格式检查）", file=sys.stderr)
            return 2
        try:
            size = _parse_size(args.size)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2

    if args.json:
        if require_keys:
            # 机读模式下把预检结论附进 JSON，退出码仍表达成败
            report["require"] = _evaluate_require(report, require_keys, size)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_render_probe_human(report))
        if require_keys:
            report["require"] = _evaluate_require(report, require_keys, size)
            for item in report["require"]:
                if item["ok"]:
                    print(f"  ✅ 预检通过：{item['key']}")
                else:
                    print(f"  ❌ 预检失败：{item['key']} —— {item['reason']}")

    if require_keys:
        return 0 if all(item["ok"] for item in report["require"]) else 1
    return 0


def _evaluate_require(report: dict, keys: list[str], size: tuple[int, int] | None) -> list[dict]:
    """对 --require 的每个 key 给出（不）满足的原因；size 参与约束判定。"""
    by_key = {f["key"]: f for f in report["formats"]}
    results = []
    for key in keys:
        f = by_key[key]
        if not f["offered"]:
            results.append({"key": key, "ok": False, "reason": f["removed_note"] or f["reason"]})
            continue
        if not f["available"]:
            results.append({"key": key, "ok": False, "reason": f["reason"]})
            continue
        if size is not None:
            err = probe.size_error(formats.FORMATS[key], size[0], size[1])
            if err:
                results.append({"key": key, "ok": False, "reason": err})
                continue
        results.append({"key": key, "ok": True, "reason": ""})
    return results


def _cmd_convert(args: argparse.Namespace) -> int:
    plan = convert.build_plan(
        args.input,
        args.output,
        args.format,
        black_background=args.black,
        overwrite=args.overwrite,
    )

    if args.dry_run:
        print(json.dumps(convert.plan_to_dict(plan), ensure_ascii=False, indent=2)
              if args.json else convert.render_plan(plan))
        return 0

    if not args.json:
        print(convert.render_plan(plan))

    # 覆盖保护前移：输出已存在且未获 --overwrite 时，在确认闸之前就拒绝
    # （旧 -n 语义：确认执行计划 ≠ 确认覆盖该文件，两件事分开确认）
    if plan.output_exists and not plan.overwrite:
        print(
            f"输出已存在，拒绝覆盖（-n 语义）：{plan.output_path}。"
            "确认覆盖该具体文件请使用 --overwrite。",
            file=sys.stderr,
        )
        return 1

    # 确认闸：--yes 之外必须交互确认；非交互环境拒绝盲执行
    if not args.yes:
        if not sys.stdin.isatty():
            print(
                "非交互环境且未提供 --yes：请先向用户展示以上计划并获得明确确认，"
                "再以 --yes 执行。",
                file=sys.stderr,
            )
            return 1
        reply = input("\n确认执行以上计划？输入 yes 继续：")
        if reply.strip().lower() not in ("yes", "y"):
            print("已取消，未写任何文件。")
            return 1

    result = convert.execute(plan)
    if args.json:
        print(json.dumps({
            "output": str(result.output_path),
            "size_bytes": result.output_size_bytes,
            "frames": result.frames_written,
            "duration_seconds": result.source_duration_seconds,
            "alpha_preserved": result.alpha_preserved,
        }, ensure_ascii=False, indent=2))
    else:
        print(
            f"\n完成：{result.output_path}（{result.frames_written} 帧 / "
            f"{result.output_size_bytes / 1024:.1f} KiB / "
            f"{'alpha 保留' if result.alpha_preserved else '无 alpha（黑底 MP4，符合预期）'}）"
        )
        print("提示：可用 `tgtv verify` 做转换后验证（Phase 5 提供）。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except (ConvertError, GifSourceError) as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
