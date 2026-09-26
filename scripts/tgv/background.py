"""Deciding — or rather, refusing to decide alone — what colour goes behind
a transparent asset.

The authoritative answer lives on the delivery surface, not in the file, so
this module's job is to gather evidence, rank options, and produce a question
for a human. Nothing here silently picks a colour.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from PIL import Image

from .common import fail, internal_error, is_auto_edge, parse_rgb, print_json, warn
from .analysis import inspect_source
from .sources import iter_source_frames
from .staging import composite_on_background

# Colours that are never *evidence*, but are almost always the real answer,
# because the answer lives on the delivery surface rather than in the asset.
COMMON_BACKGROUNDS = [
    ("#000000", "黑色：深色页面、播放器默认底色"),
    ("#ffffff", "白色：浅色页面、文档、PPT"),
]


def rank_background_options(report: dict) -> list[dict]:
    """Turn raw evidence into a ranked, human-answerable option list.

    Ranking rule, in order: evidence the asset actually carries (and that we
    trust), then evidence we distrust, then the two colours that delivery
    surfaces usually are. No option is auto-selected; the point of this list
    is to be shown to a person.
    """
    edge = report.get("transparent_edge") or {}
    options: list[dict] = []
    seen: set[str] = set()
    order = {"high": 0, "medium": 1, "low": 2, "none": 3}
    for candidate in sorted(
        edge.get("background_candidates") or [], key=lambda c: order.get(c.get("trust", "none"), 9)
    ):
        colour = candidate.get("colour")
        if not colour or candidate["source"] == "visible_rim_dominant" or colour in seen:
            continue
        seen.add(colour)
        options.append({
            "colour": colour,
            "origin": "asset",
            "source": candidate["source"],
            "trust": candidate["trust"],
            "note": candidate["note"],
        })
    for colour, note in COMMON_BACKGROUNDS:
        if colour in seen:
            # Same colour reached from two directions: keep the evidence entry
            # but tell the user it is also a conventional delivery colour.
            for option in options:
                if option["colour"] == colour:
                    option["note"] = f"{option['note']} 同时也是常见投放背景（{note}）。"
            continue
        seen.add(colour)
        options.append({
            "colour": colour,
            "origin": "delivery-convention",
            "source": "common_background",
            "trust": "none",
            "note": note,
        })
    return options


def cropping_hint(report: dict) -> str | None:
    """If the transparent area is just padding, say so before asking for a colour."""
    bbox = report.get("content_bbox")
    if not bbox or bbox["covers_full_canvas"]:
        return None
    return (
        f"注意：所有帧的可见内容并集只有 {bbox['width']}x{bbox['height']}"
        f"（画布 {report['width']}x{report['height']}），透明区其实是留白。"
        "如果那圈留白不是画面的一部分，裁切到内容区域通常比给它填一个颜色更正确。"
    )


def background_question(report: dict, options: list[dict]) -> dict:
    """A ready-to-ask question, so the caller does not improvise one."""
    alpha = report.get("alpha") or {}
    context = (
        f"透明像素占 {alpha.get('transparent_pct')}%，"
        + ("alpha 为二值，边缘不会与背景混色。" if alpha.get("binary", True) else "存在半透明像素，边缘会与背景真实混色，选错颜色会留下脏边。")
    )
    hint = cropping_hint(report)
    if hint:
        context = f"{context} {hint}"
    return {
        "headline": "这段素材有透明区域，转成不透明视频前需要你确认背景色。",
        "context": context,
        "ask": "这段视频最终会放在什么背景上？",
        "options": [f"{o['colour']} — {o['note']}" for o in options],
        "fallback": "如果最终背景本身是透明/可变的，就不要转 MP4，改用 --keep-alpha 输出 WebM/MOV。",
    }


def build_background_proposal(report: dict) -> dict:
    options = rank_background_options(report)
    return {
        "input": report["path"],
        "frames": report["frames"],
        "alpha": report["alpha"],
        "evidence": (report.get("transparent_edge") or {}).get("background_candidates", []),
        "options": options,
        "auto_edge_usable": bool(
            (report.get("transparent_edge") or {}).get("single_colour")
            and not (report.get("transparent_edge") or {}).get("suspicious_reason")
        ),
        "question": background_question(report, options),
        "next_step": "把 question 原样问用户，得到答案后执行 convert --background '#RRGGBB'。",
    }


def middle_frame(source: Path, sequence_duration_ms: float | None = None) -> Image.Image:
    """Return the middle frame without holding the whole animation in memory."""
    count = 0
    for _ in iter_source_frames(source, sequence_duration_ms):
        count += 1
    if not count:
        fail("没有可预览的帧。")
    target = count // 2
    for index, (rgba, _duration) in enumerate(iter_source_frames(source, sequence_duration_ms)):
        if index == target:
            return rgba
    internal_error("中间帧索引超出范围。")  # pragma: no cover


def render_background_preview(
    source: Path,
    options: Sequence[dict],
    destination: Path,
    sequence_duration_ms: float | None = None,
    max_width: int = 360,
) -> Path:
    """Render the middle frame composited over each candidate, side by side.

    A person answers "which background" far faster by looking than by reading
    hex codes.
    """
    # Stream to the middle frame: materialising every frame of a 95x1000x1000
    # asset just to preview one of them would cost ~380 MB.
    frame = middle_frame(source, sequence_duration_ms)
    scale = min(1.0, max_width / max(1, frame.width))
    if scale < 1.0:
        frame = frame.resize((max(1, int(frame.width * scale)), max(1, int(frame.height * scale))), Image.LANCZOS)
    tiles = [composite_on_background(frame, parse_rgb(option["colour"])) for option in options]
    if not tiles:
        fail("没有候选背景可预览。")
    gap = 8
    sheet = Image.new("RGB", (len(tiles) * frame.width + (len(tiles) + 1) * gap, frame.height + 2 * gap), (128, 128, 128))
    for index, tile in enumerate(tiles):
        sheet.paste(tile, (gap + index * (frame.width + gap), gap))
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination)
    return destination


def suggest_background(args: argparse.Namespace) -> int:
    source = Path(args.input).expanduser().resolve()
    report = inspect_source(source, args.sequence_duration_ms, scan_edges=True)
    if not report["alpha"]["has_transparency"]:
        fail("素材没有透明像素，不需要选择背景色。")
    proposal = build_background_proposal(report)
    if args.preview:
        preview = render_background_preview(
            source, proposal["options"], Path(args.preview).expanduser().resolve(), args.sequence_duration_ms
        )
        proposal["preview_image"] = str(preview)
        proposal["preview_order"] = [option["colour"] for option in proposal["options"]]
    print_json(proposal)
    return 0


def prompt_for_background(report: dict) -> tuple[int, int, int]:
    """Interactive fallback for humans running the script directly."""
    options = rank_background_options(report)
    question = background_question(report, options)
    print(question["headline"], file=sys.stderr)
    print(question["context"], file=sys.stderr)
    print(question["ask"], file=sys.stderr)
    for index, option in enumerate(options, 1):
        print(f"  {index}. {option['colour']} — {option['note']}", file=sys.stderr)
    print(f"  0. 其他（手动输入 #RRGGBB）；{question['fallback']}", file=sys.stderr)
    try:
        answer = input("请选择序号或直接输入颜色: ").strip()
    except EOFError:
        fail("没有可用的交互输入。")
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return parse_rgb(options[int(answer) - 1]["colour"])
    return parse_rgb(answer)


def describe_candidates(report_or_edge: dict) -> str:
    """One-line summary of the evidence, shared by every failure message."""
    edge = report_or_edge.get("transparent_edge", report_or_edge)
    items = edge.get("background_candidates") or []
    usable = [f"{i['source']}={i['colour']}(信任度 {i['trust']})" for i in items if i.get("colour")]
    return "; ".join(usable) if usable else "无"


def resolve_background(value: str, source_report: dict, allow_suspicious: bool = False) -> tuple[int, int, int]:
    if not is_auto_edge(value):
        return parse_rgb(value)
    edge = source_report["transparent_edge"]
    if not edge.get("scanned"):
        internal_error("auto-edge 需要边缘扫描结果，但报告中没有。")
    if not edge["single_colour"] or not edge["colour"]:
        samples = ", ".join(edge["top_colours"]) or "无"
        fail(
            "无法使用 --background auto-edge：透明区域边缘没有唯一单色。"
            f"检测到 {edge['distinct_colours']} 种颜色（出现最多的: {samples}）。\n"
            f"其他证据: {describe_candidates(edge)}\n"
            "请显式指定 #RRGGBB，或先运行 suggest-background 与用户确认；脚本不会自动平均多种颜色。"
        )
    reason = edge.get("suspicious_reason")
    if reason and not allow_suspicious:
        fail(
            f"auto-edge 检测到单色 {edge['colour']}，但该结果不可信：{reason}\n"
            f"其他证据: {describe_candidates(edge)}\n"
            "请先运行 suggest-background 与用户确认，再用 --background '#RRGGBB'；"
            "确认黑色就是目标背景时可加 --allow-suspicious-edge-colour。"
        )
    if reason:
        warn(f"按要求接受可疑的 auto-edge 结果 {edge['colour']}：{reason}")
    return parse_rgb(edge["colour"])


# --------------------------------------------------------------------------
