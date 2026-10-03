"""转换后验证（Phase 5）：旧 SKILL.md §4 全部规则的结构化实现。

signalstats 时代的三个历史坑在此**结构性消灭**（分析文档 §6.4）：

| 旧坑                                | 本模块                                        |
|-------------------------------------|-----------------------------------------------|
| `-v error` 把 YMIN 采样清零、断言静默跳过 | 不存在日志级别问题；逐帧显式循环统计，零帧即异常 |
| >8bit alpha 需 `format=gray` 归一     | `to_ndarray(format="rgba")` 统一归一到 8 位域   |
| 只看首帧误判（首帧关键帧天然干净）      | 全帧扫描由循环结构保证                          |

规则映射：

1. **完整解码**：全部帧无错解出，帧数 > 0。
2. **显式 decoder**（§4.2 硬规则）：WebM 的 VP9/VP8 按容器声明的 codec
   选择 `libvpx-vp9` / `libvpx` 显式解码（不按扩展名猜、不用原生 vp9 解码器，
   否则 alpha 会被悄悄丢掉、`yuva420p` 变 `yuv420p`——本模块对该陷阱
   有专项检查）。
3. **alpha 存在 ≠ 有透明像素**：全帧扫描 alpha 最小值，断言至少一帧 < 255。
4. **半透明区分轻重**：轻微取整（1–31 / 224–254）与可见晕环（32–223）
   分开统计；有损路径卡晕环占比 < 0.5%。
5. **PTS 逐帧一致**（提供源时）：未被平均成 CFR。
6. **尺寸一致**（提供源时）：不缩放、不裁切。
7. **底色策略**（提供源时）：默认断言透明区 RGB 与源一致（检出**意外的
   预乘**——2026-10-02 报告 §9 的旧 `out.webm` 黑底 bug 正是这一类）；
   `expected_black=True` 时断言透明区为黑。两者都不修复播放器兼容性。
8. **可见区保真**（提供源时）：RGB 平均绝对差与 PSNR；无损交换路径
   （png/qtrle）要求 RGBA 域逐像素一致；ffv1 在其原生 yuva444p 域逐像素一致
   （RGBA 域差异是色彩空间往返假象，2026-10-02 报告 §5B）。
9. **黑底 MP4**：断言解码无 alpha（预期）且透明区呈现黑；「无 alpha 平面」
   本身就是该路径的正确证据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import av
import numpy as np

from .formats import FORMATS
from .source import GifSource

#: 容器声明 codec → 验证用显式 decoder（§4.2：不可按扩展名猜）
EXPLICIT_DECODERS = {"vp9": "libvpx-vp9", "vp8": "libvpx"}

#: 无损交换路径：允许做逐像素一致断言（rgba 域）
RGBA_EXACT_KEYS = frozenset({"png-mov", "qtrle"})
#: ffv1：原生域（yuva444p）逐像素一致；rgba 域只做容差比较
NATIVE_EXACT_KEYS = frozenset({"ffv1"})

#: 阈值（与旧回归矩阵一致，tests/README.md「通用断言」）
THRESH_MID_BAND_RATIO = 0.005  # 可见晕环（32<=a<=223）占比上限
THRESH_VISIBLE_MAD = 8.0       # 可见区 RGB 平均绝对差上限（有损）
THRESH_ALPHA_AGREE = 0.99      # alpha 阈值化一致率下限
THRESH_PTS_TOL = 0.001         # PTS 逐帧容差（秒）
THRESH_TRANS_KEPT_MAD = 12.0   # 透明区 RGB 与源的平均绝对差上限（保留底色）
THRESH_BLACK_MEAN = 10.0       # 黑底路径透明区 RGB 均值上限
THRESH_MP4_BLACK_MEAN = 20.0   # mp4 透明区黑判定（yuv420p 往返放宽）
THRESH_DURATION_TOL = 0.01     # 总时长容差（秒）


class VerifyError(Exception):
    """验证无法进行（文件打不开 / 无视频流 / 解码失败）。"""


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class VerifyReport:
    output_path: Path
    source_path: Path | None
    codec_name: str
    decoder_used: str
    frames: int
    canvas: tuple[int, int]
    duration_seconds: float | None
    pts_seconds: list[float]
    decoded_pix_fmts: set[str]
    stats: dict = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


# ------------------------------------------------------------------ 解码 --


def _decode_output(path: Path):
    """完整解码输出。返回 (frames, codec_name, decoder_used, duration, container_fmt)。"""
    try:
        container = av.open(str(path))
    except Exception as e:  # noqa: BLE001
        raise VerifyError(f"无法打开输出：{path} —— {type(e).__name__}: {e}") from e
    try:
        streams = container.streams.video
        if not streams:
            raise VerifyError(f"输出没有视频流：{path}")
        codec_name = streams[0].codec_context.name
        decoder = EXPLICIT_DECODERS.get(codec_name)
        if decoder:
            # §4.2：VP9/VP8 必须显式 libvpx 解码（原生解码器会丢 alpha）
            dec = av.codec.CodecContext.create(decoder, "r")
            frames = [fr for pkt in container.demux(video=0) for fr in dec.decode(pkt)]
        else:
            decoder = codec_name or "(容器默认)"
            frames = list(container.decode(video=0))
        duration = (
            container.duration / 1_000_000.0
            if container.duration and container.duration > 0 else None
        )
    finally:
        container.close()
    if not frames:
        raise VerifyError(f"解码出 0 帧：{path}")
    return frames, codec_name, decoder, duration


def _rgba(frames) -> np.ndarray:
    """解码帧（av.VideoFrame）或读取帧（GifFrame）统一转 NxHxWx4 uint8。"""
    from .source import GifFrame

    return np.stack([
        gf.rgba if isinstance(gf, GifFrame) else gf.to_ndarray(format="rgba")
        for gf in frames
    ])


def _alpha_had_alpha_plane(frames) -> bool:
    """像素格式是否携带 alpha 平面（yuva*/rgba/argb/bgra/gbrap*）。"""
    names = {fr.format.name for fr in frames}
    return all(
        n.startswith("yuva") or n in ("rgba", "argb", "bgra") or n.startswith("gbrap")
        for n in names
    )


def _yuva444p_planes(frame) -> np.ndarray:
    """按平面提取 yuva444p 帧（PyAV 的 to_ndarray 不支持该格式）→ 4xHxW uint8。"""
    h, w = frame.height, frame.width
    planes = []
    for i in range(4):
        plane = frame.planes[i]
        stride = plane.line_size
        buf = np.frombuffer(plane, dtype=np.uint8)[: stride * h].reshape(h, stride)[:, :w]
        planes.append(np.ascontiguousarray(buf))
    return np.stack(planes)


# ------------------------------------------------------------------ 统计 --


def _alpha_stats(out_rgba: np.ndarray) -> dict:
    alpha = out_rgba[..., 3].astype(np.int16)
    semi = int(((alpha > 0) & (alpha < 255)).sum())
    mid = int(((alpha >= 32) & (alpha <= 223)).sum())
    total = int(alpha.size)
    return {
        "alpha_min_all_frames": int(alpha.min()),
        "alpha_min_first_frame": int(alpha[0].min()),
        "semi_transparent_total": semi,
        "visible_halo_total": mid,
        "visible_halo_ratio": mid / total if total else 1.0,
    }


def _psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(((a.astype(np.float64) - b.astype(np.float64)) ** 2).mean())
    if mse <= 0:
        return float("inf")
    return 10.0 * np.log10(255.0 * 255.0 / mse)


# ------------------------------------------------------------------ 主入口 --


def verify(
    output_path: str | Path,
    source_path: str | Path | None = None,
    *,
    expected_black: bool = False,
) -> VerifyReport:
    """验证一个转换产物。提供源 GIF 时做逐帧/逐像素比对。

    Args:
        output_path: 转换产物（webm/mov/mkv/mp4）。
        source_path: 源 GIF（可选；提供后启用 PTS/尺寸/底色/保真比对）。
        expected_black: 产物是 --black / mp4-black 转换时为 True——
            透明区 RGB 归黑是预期行为而不是意外预乘。
    """
    out_path = Path(output_path)
    frames, codec_name, decoder_used, duration = _decode_output(out_path)
    out_rgba = _rgba(frames)
    canvas = (frames[0].width, frames[0].height)
    pts_seconds = [float(fr.pts * fr.time_base) for fr in frames]

    # 输出格式身份（用于路径级规则）：按容器声明的 codec 反查注册表。
    # 注册表存的是编码器名（如 libx264），容器声明的是解码器名（如 h264），需映射。
    # （不用扩展名；libvpx-vp9 同时对应 vp9/vp9-lossless，二者验证规则相同，取先者即可。）
    encode_to_decode = {"libx264": "h264", "libvpx-vp9": "vp9", "libvpx": "vp8", "prores_ks": "prores"}
    spec = None
    for s in FORMATS.values():
        if codec_name in {s.codec, encode_to_decode.get(s.codec, s.codec)}:
            spec = s
            break

    report = VerifyReport(
        output_path=out_path,
        source_path=Path(source_path) if source_path else None,
        codec_name=codec_name,
        decoder_used=decoder_used,
        frames=len(frames),
        canvas=canvas,
        duration_seconds=duration,
        pts_seconds=pts_seconds,
        decoded_pix_fmts={f.format.name for f in frames},
    )

    is_mp4_black = spec is not None and spec.key == "mp4-black"
    has_alpha_fmt = _alpha_had_alpha_plane(frames)

    # ---- 检查 1：完整解码（能到这里就已通过；记录事实） ----
    report.checks.append(Check("完整解码", True, f"{len(frames)} 帧全部解出"))

    # ---- 检查 2：VP9/VP8 的解码器陷阱（§4.2） ----
    if codec_name in EXPLICIT_DECODERS:
        ok = decoder_used == EXPLICIT_DECODERS[codec_name] and has_alpha_fmt
        report.checks.append(Check(
            "显式 libvpx 解码且保留 alpha 平面", ok,
            f"decoder={decoder_used}，像素格式={sorted(report.decoded_pix_fmts)}"
            + ("" if ok else "（原生解码器会读成 yuv420p 丢 alpha——验证器已显式规避）"),
        ))

    # ---- 检查 3：alpha 与真实透明像素（全帧扫描） ----
    stats: dict = {}
    if has_alpha_fmt:
        stats.update(_alpha_stats(out_rgba))
        ok = stats["alpha_min_all_frames"] < 255
        report.checks.append(Check(
            "存在真实透明像素（全帧扫描 alpha_min<255）", ok,
            f"全帧 alpha_min={stats['alpha_min_all_frames']}"
            f"（首帧 alpha_min={stats['alpha_min_first_frame']}——首帧不透明素材"
            "只有全帧扫描能发现透明）",
        ))
        halo_ratio = stats["visible_halo_ratio"]
        report.checks.append(Check(
            "无可见晕环（32<=alpha<=223 占比 <0.5%）",
            halo_ratio < THRESH_MID_BAND_RATIO,
            f"{stats['visible_halo_total']} px（{halo_ratio * 100:.3f}%）；"
            f"轻微取整（不计）{stats['semi_transparent_total'] - stats['visible_halo_total']} px",
        ))
    elif is_mp4_black:
        report.checks.append(Check(
            "黑底 MP4 无 alpha（预期）", True,
            f"像素格式={sorted(report.decoded_pix_fmts)}——无 alpha 平面是该路径的正确证据",
        ))
    else:
        report.checks.append(Check("alpha 平面存在", False,
                                   f"像素格式 {sorted(report.decoded_pix_fmts)} 不含 alpha"))

    # ---- 提供源时的比对 ----
    src_rgba = src_info = None
    if source_path is not None:
        src = GifSource(source_path)
        src_info = src.info
        src_rgba = _rgba(list(src.iter_frames()))

        report.checks.append(Check(
            "帧数与源一致", len(frames) == src_info.frame_count,
            f"输出 {len(frames)} 帧 / 源 {src_info.frame_count} 帧",
        ))
        report.checks.append(Check(
            "尺寸未缩放", canvas == (src_info.width, src_info.height),
            f"输出 {canvas[0]}×{canvas[1]} / 源 {src_info.width}×{src_info.height}",
        ))
        k = min(len(pts_seconds), len(src_info.pts_seconds_list))
        pts_ok = k == len(pts_seconds) == len(src_info.pts_seconds_list) and all(
            abs(a - b) < THRESH_PTS_TOL
            for a, b in zip(pts_seconds, src_info.pts_seconds_list)
        )
        report.checks.append(Check(
            "PTS 逐帧一致（未被平均成 CFR）", pts_ok,
            f"输出 {pts_seconds} / 源 {list(src_info.pts_seconds_list)}",
        ))
        if src_info.duration_seconds is not None and duration is not None:
            report.checks.append(Check(
                "总时长与源一致",
                abs(duration - src_info.duration_seconds) < THRESH_DURATION_TOL,
                f"输出 {duration:.3f}s / 源 {src_info.duration_seconds:.3f}s",
            ))

        n = min(len(out_rgba), len(src_rgba))
        src_a = src_rgba[:n][..., 3].astype(np.int16)
        out_a = out_rgba[:n][..., 3].astype(np.int16)
        trans = src_a == 0
        vis = src_a == 255
        stats["alpha_threshold_agreement"] = float(
            ((out_a >= 128) == (src_a >= 128)).mean()
        )

        # 底色策略：意外预乘检测（2026-10-02 报告 §9 的旧 bug 正是这类）
        if trans.any():
            trans_rgb_out = out_rgba[:n][..., :3][trans]
            trans_rgb_src = src_rgba[:n][..., :3][trans]
            if is_mp4_black or expected_black:
                mean = float(trans_rgb_out.mean())
                lim = THRESH_MP4_BLACK_MEAN if is_mp4_black else THRESH_BLACK_MEAN
                report.checks.append(Check(
                    "透明区 RGB 已归黑（黑底路径）", mean < lim,
                    f"透明区 RGB 均值 {mean:.1f}（阈值 {lim}）",
                ))
            else:
                mad = float(np.abs(trans_rgb_out.astype(int) - trans_rgb_src.astype(int)).mean())
                stats["transparent_rgb_mad_vs_source"] = mad
                black_mean = float(trans_rgb_out.mean())
                report.checks.append(Check(
                    "透明区 RGB 保持源 GIF 底色（无意外预乘）", mad < THRESH_TRANS_KEPT_MAD,
                    f"与源平均差 {mad:.2f}；透明区均值 {black_mean:.1f}"
                    + (f"（接近黑——若这是 --black 转换，验证时请传 expected_black/--black）"
                       if black_mean < THRESH_BLACK_MEAN else ""),
                ))

        # 可见区保真
        if vis.any():
            o = out_rgba[:n][..., :3][vis]
            s = src_rgba[:n][..., :3][vis]
            mad = float(np.abs(o.astype(int) - s.astype(int)).mean())
            psnr = _psnr(o, s)
            stats["visible_rgb_mad"] = mad
            stats["visible_rgb_psnr_db"] = psnr
            if spec and spec.key in RGBA_EXACT_KEYS:
                report.checks.append(Check(
                    f"无损交换：RGBA 域逐像素一致（{spec.key}）",
                    mad == 0.0 and int(np.abs(o.astype(int) - s.astype(int)).max()) == 0,
                    f"最大差 {int(np.abs(o.astype(int) - s.astype(int)).max())}"
                    f"（逐像素一致已含保真断言；PSNR {psnr:.1f} dB）",
                ))
            else:
                lim = THRESH_VISIBLE_MAD
                report.checks.append(Check(
                    "可见区 RGB 无剧烈失真", mad < lim,
                    f"平均差 {mad:.2f} / PSNR {psnr:.1f} dB（阈值 {lim}）",
                ))
            if has_alpha_fmt:  # 黑底 MP4 无 alpha 平面，该比对不适用
                report.checks.append(Check(
                    "alpha 阈值化一致（取整不算失配）",
                    stats["alpha_threshold_agreement"] > THRESH_ALPHA_AGREE,
                    f"{stats['alpha_threshold_agreement'] * 100:.3f}%",
                ))

    # ---- ffv1 原生域无损（RGBA 域差异是色彩空间往返假象） ----
    if spec and spec.key in NATIVE_EXACT_KEYS and source_path is not None:
        src = GifSource(source_path)
        src_native = np.stack([
            _yuva444p_planes(gf.frame.reformat(format="yuva444p")) for gf in src.iter_frames()
        ])
        out_native = np.stack([_yuva444p_planes(f) for f in frames[: len(src_native)]])
        exact = src_native.shape == out_native.shape and np.array_equal(src_native, out_native)
        report.checks.append(Check(
            "无损归档：yuva444p 原生域逐像素一致（ffv1）", exact,
            "同域比较排除 RGB↔YUV 往返假象" if exact else "原生域存在差异",
        ))

    report.stats = stats
    return report


# ------------------------------------------------------------------ 渲染 --


def render_report(report: VerifyReport) -> str:
    lines = [
        f"验证报告：{report.output_path}",
        f"  codec={report.codec_name}，decoder={report.decoder_used}，"
        f"{report.frames} 帧，{report.canvas[0]}×{report.canvas[1]}，"
        f"像素格式={sorted(report.decoded_pix_fmts)}",
        f"  时长={report.duration_seconds if report.duration_seconds is not None else '?'}s，"
        f"PTS={report.pts_seconds}",
    ]
    if report.source_path:
        lines.append(f"  比对源：{report.source_path}")
    for c in report.checks:
        lines.append(f"  [{'PASS' if c.passed else 'FAIL'}] {c.name} —— {c.detail}")
    verdict = "验证通过" if report.passed else "验证失败"
    lines.append(f"  结论：{verdict}（{sum(c.passed for c in report.checks)}/{len(report.checks)} 项）")
    return "\n".join(lines)
