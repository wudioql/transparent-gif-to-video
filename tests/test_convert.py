"""Phase 3+4 验收测试：8 条路径的转换闭环 + 黑底归一化 + 覆盖/确认不变量。

断言口径继承旧回归矩阵（tests/README.md「通用断言」）：
    - 编码成功且帧数与源一致；PTS 逐帧保留（未被平均）；
    - 尺寸不变（不缩放、不裁切）；
    - alpha 输出：显式 libvpx-vp9 / libvpx 解码得到带 alpha 的像素格式，
      且至少一帧 alpha 最小值 < 255（全帧扫描）；
    - 默认路径透明区保持源 GIF 底色；--black 路径透明 RGB 归零、alpha 掩码不变；
    - 黑底 MP4：无 alpha（预期）、透明区呈现黑、奇数尺寸在预检阶段拒绝；
    - 输出已存在默认拒绝（-n 语义），--overwrite 才覆盖。

运行方式（开发环境，无需系统 ffmpeg）：
    pip install -e ".[dev]"
    pytest tests/test_convert.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import av
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))

import make_fixtures as mf  # noqa: E402

from tgtv import convert  # noqa: E402
from tgtv.cli import main  # noqa: E402
from tgtv.convert import ConvertError  # noqa: E402
from tgtv.source import GifFrame, GifSource  # noqa: E402


@pytest.fixture(scope="module")
def fx(tmp_path_factory) -> dict[str, Path]:
    out = tmp_path_factory.mktemp("fixtures")
    return {
        "small5": Path(mf.fx_small5(str(out))),
        "vardur": Path(mf.fx_vardur(str(out))),
        "binary": Path(mf.fx_binary_known_rgb(str(out))),
        "odd_999": Path(mf.fx_odd(str(out), 999, 999, "odd")),
        "weird_path": Path(mf.fx_weird_path(str(out))),
    }


# ------------------------------------------------------------------ 工具 --


def _decode(path: Path, decoder: str | None = None) -> list[av.VideoFrame]:
    container = av.open(str(path))
    try:
        if decoder:
            dec = av.codec.CodecContext.create(decoder, "r")
            frames = [fr for pkt in container.demux(video=0) for fr in dec.decode(pkt)]
        else:
            frames = list(container.decode(video=0))
    finally:
        container.close()
    return frames


def _rgba(frames) -> np.ndarray:
    """解码帧（av.VideoFrame）或读取帧（GifFrame）统一转 HxNxWx4 uint8。"""
    return np.stack([
        gf.rgba if isinstance(gf, GifFrame) else gf.to_ndarray(format="rgba")
        for gf in frames
    ])


def _convert(src_path: Path, out_path: Path, fmt: str = "vp9", **kw) -> convert.ConvertResult:
    plan = convert.build_plan(src_path, out_path, fmt, **kw)
    return convert.execute(plan)


FORMATS_EXT = {
    "vp9": ".webm", "vp9-lossless": ".webm", "vp8": ".webm", "prores4444": ".mov",
    "png-mov": ".mov", "qtrle": ".mov", "ffv1": ".mkv", "mp4-black": ".mp4",
}


# ------------------------------------------------------------- WebM 路径 --


def test_vp9_default_path(fx, tmp_path):
    result = _convert(fx["small5"], tmp_path / "small5.webm", "vp9")
    assert result.frames_written == 5 and result.alpha_preserved
    frames = _decode(tmp_path / "small5.webm", decoder="libvpx-vp9")
    assert len(frames) == 5
    assert {fr.format.name for fr in frames} == {"yuva420p"}  # alpha 平面在
    arr = _rgba(frames)
    assert arr[..., 3].min() == 0  # 真实透明像素
    assert (frames[0].width, frames[0].height) == (64, 48)  # 尺寸不变


def test_vp9_pts_passthrough_vardur(fx, tmp_path):
    _convert(fx["vardur"], tmp_path / "vardur.webm", "vp9")
    frames = _decode(tmp_path / "vardur.webm", decoder="libvpx-vp9")
    pts = [float(fr.pts * fr.time_base) for fr in frames]
    assert pts == [0.0, 0.01, 0.04, 0.09, 0.19]  # 逐帧保留，未平均成 CFR


def test_vp9_default_keeps_source_background_rgb(fx, tmp_path):
    """默认路径：透明区底层 RGB 保持源 GIF 白色（未被预乘/涂黑）。"""
    _convert(fx["binary"], tmp_path / "binary.webm", "vp9")
    out = _rgba(_decode(tmp_path / "binary.webm", decoder="libvpx-vp9"))
    src = _rgba(list(GifSource(fx["binary"]).iter_frames()))
    trans = src[0][..., 3] == 0
    assert trans.any()
    assert out[0][..., :3][trans].mean() > 245  # 白底保持（有损 CRF30 允许轻微波动）


def test_vp9_black_background(fx, tmp_path):
    """--black：透明 RGB 归零、alpha 掩码阈值化一致、可见区不被破坏。"""
    _convert(fx["binary"], tmp_path / "binary.webm", "vp9", black_background=True)
    out = _rgba(_decode(tmp_path / "binary.webm", decoder="libvpx-vp9"))
    src = _rgba(list(GifSource(fx["binary"]).iter_frames()))
    trans = src[0][..., 3] == 0
    vis = src[0][..., 3] == 255
    assert out[0][..., :3][trans].mean() < 10  # 透明区 RGB ≈ 黑
    assert np.abs(out[0][..., :3][vis].astype(int)
                  - src[0][..., :3][vis].astype(int)).mean() < 8  # 可见区无剧烈失真
    agree = ((out[0][..., 3] >= 128) == ~trans).mean()  # alpha 阈值化一致
    assert agree > 0.99


@pytest.mark.parametrize("fmt,decoder", [("vp9-lossless", "libvpx-vp9"), ("vp8", "libvpx")])
def test_other_webm_paths(fx, tmp_path, fmt, decoder):
    _convert(fx["small5"], tmp_path / ("small5" + FORMATS_EXT[fmt]), fmt)
    frames = _decode(tmp_path / ("small5" + FORMATS_EXT[fmt]), decoder=decoder)
    assert len(frames) == 5
    assert {fr.format.name for fr in frames} == {"yuva420p"}
    assert _rgba(frames)[..., 3].min() == 0


# --------------------------------------------------------- MOV / MKV 路径 --


def test_prores4444(fx, tmp_path):
    _convert(fx["small5"], tmp_path / "small5.mov", "prores4444")
    frames = _decode(tmp_path / "small5.mov")
    assert len(frames) == 5
    assert {fr.format.name for fr in frames} == {"yuva444p12le"}  # 与旧栈实测一致
    assert _rgba(frames)[..., 3].min() == 0


def test_png_mov_lossless_exact(fx, tmp_path):
    _convert(fx["small5"], tmp_path / "small5.mov", "png-mov")
    frames = _decode(tmp_path / "small5.mov")
    assert {fr.format.name for fr in frames} == {"rgba"}
    arr = _rgba(frames)
    src = _rgba(list(GifSource(fx["small5"]).iter_frames()))
    assert np.array_equal(arr, src)  # PNG-in-MOV：RGBA 域逐像素无损


def test_qtrle(fx, tmp_path):
    _convert(fx["small5"], tmp_path / "small5.mov", "qtrle")
    frames = _decode(tmp_path / "small5.mov")
    assert {fr.format.name for fr in frames} == {"argb"}
    assert _rgba(frames)[..., 3].min() == 0


def test_ffv1(fx, tmp_path):
    _convert(fx["small5"], tmp_path / "small5.mkv", "ffv1")
    frames = _decode(tmp_path / "small5.mkv")
    assert {fr.format.name for fr in frames} == {"yuva444p"}
    assert _rgba(frames)[..., 3].min() == 0


# ------------------------------------------------------------- 黑底 MP4 --


def test_mp4_black(fx, tmp_path):
    _convert(fx["binary"], tmp_path / "binary.mp4", "mp4-black")
    frames = _decode(tmp_path / "binary.mp4")
    assert {fr.format.name for fr in frames} == {"yuv420p"}  # 无 alpha（预期）
    arr = _rgba(frames)
    src = _rgba(list(GifSource(fx["binary"]).iter_frames()))
    trans = src[0][..., 3] == 0
    vis = src[0][..., 3] == 255
    assert arr[0][..., :3][trans].mean() < 20  # 透明区呈现黑（合成到黑）
    assert (arr[0][..., :3][vis].astype(int)
            - src[0][..., :3][vis].astype(int)).mean() < 12  # 可见区不劣化


def test_mp4_black_odd_size_rejected_before_writing(fx, tmp_path):
    out = tmp_path / "odd_999.mp4"
    with pytest.raises(ConvertError, match="不是偶数"):
        convert.build_plan(fx["odd_999"], out, "mp4-black")
    assert not out.exists()  # 预检阶段拒绝，零文件产出


def test_vp9_odd_size_ok(fx, tmp_path):
    """WebM 侧奇数尺寸正常且画布不变（旧栈实测行为）。"""
    _convert(fx["odd_999"], tmp_path / "odd_999.webm", "vp9")
    frames = _decode(tmp_path / "odd_999.webm", decoder="libvpx-vp9")
    assert (frames[0].width, frames[0].height) == (999, 999)
    assert _rgba(frames)[..., 3].min() == 0


# ------------------------------------------------------------- 预检拒绝 --


def test_hap_not_silently_substituted(tmp_path):
    with pytest.raises(ConvertError, match="移除"):
        convert.build_plan(tmp_path / "x.gif", None, "hap")


def test_unknown_format_rejected(tmp_path):
    with pytest.raises(ConvertError, match="未知格式"):
        convert.build_plan(tmp_path / "x.gif", None, "webm-ultra")


def test_black_rejected_outside_vp8_vp9(fx, tmp_path):
    with pytest.raises(ConvertError, match="仅支持"):
        convert.build_plan(fx["small5"], tmp_path / "o.mov", "prores4444", black_background=True)


# --------------------------------------------------------- 覆盖与路径保护 --


def test_overwrite_protection_negative_n_semantics(fx, tmp_path):
    out = tmp_path / "small5.webm"
    _convert(fx["small5"], tmp_path / "small5.webm", "vp9")
    before = out.read_bytes()
    plan = convert.build_plan(fx["small5"], out, "vp9")  # 默认 overwrite=False
    with pytest.raises(ConvertError, match="拒绝覆盖"):
        convert.execute(plan)
    assert out.read_bytes() == before  # 原文件未被触碰


def test_overwrite_explicit_confirmation(fx, tmp_path):
    out = tmp_path / "small5.webm"
    _convert(fx["small5"], tmp_path / "small5.webm", "vp9")
    plan = convert.build_plan(fx["small5"], out, "vp9", overwrite=True)
    result = convert.execute(plan)
    assert result.output_size_bytes > 0


def test_output_equals_input_rejected(fx):
    with pytest.raises(ConvertError, match="输出路径与输入相同"):
        convert.build_plan(fx["small5"], fx["small5"], "vp9")


def test_weird_path_end_to_end(fx, tmp_path):
    out = tmp_path / "输出 文件 [1].webm"
    plan = convert.build_plan(fx["weird_path"], out, "vp9")
    convert.execute(plan)
    frames = _decode(out, decoder="libvpx-vp9")
    assert len(frames) == 2 and _rgba(frames)[..., 3].min() == 0


# ------------------------------------------------------------------ 计划 --


def test_plan_render_contains_required_fields(fx, tmp_path):
    plan = convert.build_plan(fx["small5"], tmp_path / "o.webm", "vp9")
    text = convert.render_plan(plan)
    for field in ("输入", "格式", "关键参数", "输出", "底色策略", "覆盖行为"):
        assert field in text
    assert "保留源 GIF 透明区底层 RGB" in text
    plan_black = convert.build_plan(fx["small5"], tmp_path / "o2.webm", "vp9", black_background=True)
    assert "黑色归一化" in convert.render_plan(plan_black)


def test_plan_json_roundtrip(fx, tmp_path):
    plan = convert.build_plan(fx["vardur"], tmp_path / "o.webm", "vp9")
    d = convert.plan_to_dict(plan)
    assert d["frames"] == 5 and d["pts_seconds"] == [0.0, 0.01, 0.04, 0.09, 0.19]


# ------------------------------------------------------------------ CLI --


def test_cli_convert_dry_run_writes_nothing(fx, tmp_path, capsys):
    out = tmp_path / "cli.webm"
    rc = main(["convert", str(fx["small5"]), "-o", str(out), "--dry-run"])
    assert rc == 0
    assert not out.exists()
    assert "转换计划（未执行）" in capsys.readouterr().out


def test_cli_convert_requires_confirmation_in_non_tty(fx, tmp_path, capsys):
    out = tmp_path / "cli.webm"
    rc = main(["convert", str(fx["small5"]), "-o", str(out)])
    assert rc == 1
    assert not out.exists()
    assert "--yes" in capsys.readouterr().err


def test_cli_convert_yes_executes(fx, tmp_path, capsys):
    out = tmp_path / "cli.webm"
    rc = main(["convert", str(fx["small5"]), "-o", str(out), "--yes"])
    assert rc == 0
    assert out.exists() and out.stat().st_size > 0
    assert "完成" in capsys.readouterr().out


def test_cli_convert_interactive_confirm_and_decline(fx, tmp_path, monkeypatch, capsys):
    out = tmp_path / "cli.webm"
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    assert main(["convert", str(fx["small5"]), "-o", str(out)]) == 0
    assert out.exists()
    monkeypatch.setattr("builtins.input", lambda _: "no")
    out2 = tmp_path / "cli2.webm"
    assert main(["convert", str(fx["small5"]), "-o", str(out2)]) == 1
    assert not out2.exists()


def test_cli_convert_overwrite_gate(fx, tmp_path, capsys):
    out = tmp_path / "cli.webm"
    assert main(["convert", str(fx["small5"]), "-o", str(out), "--yes"]) == 0
    # 已存在且无 --overwrite：即使 --yes 也拒绝（确认执行 ≠ 确认覆盖）
    rc = main(["convert", str(fx["small5"]), "-o", str(out), "--yes"])
    assert rc == 1
    assert "拒绝覆盖" in capsys.readouterr().err
    rc = main(["convert", str(fx["small5"]), "-o", str(out), "--yes", "--overwrite"])
    assert rc == 0


def test_cli_convert_error_path(fx, tmp_path, capsys):
    rc = main(["convert", str(fx["odd_999"]), "-f", "mp4-black", "-o", str(tmp_path / "x.mp4"), "--yes"])
    assert rc == 1
    assert "不是偶数" in capsys.readouterr().err
    assert not (tmp_path / "x.mp4").exists()
