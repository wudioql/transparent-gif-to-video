"""Phase 2 验收测试：GIF 读取层与旧 ffmpeg CLI 回归数值的一致性。

夹具全部由 tests/make_fixtures.py 现场生成（Pillow + 手写 GIF89a 编码器），
基准数值来自 2026-10-02 的 ffmpeg CLI 回归（test-reports/2026-10-02-matrix.md §15）
与 tests/README.md 的夹具表——**数值一致 = 新读取层与旧栈语义等价**。

运行方式（开发环境，无需系统 ffmpeg）：
    pip install -e ".[dev]"
    pytest tests/test_source.py
"""

from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))

import make_fixtures as mf  # noqa: E402

from tgtv.source import GifFrame, GifInfo, GifSource, GifSourceError  # noqa: E402


@pytest.fixture(scope="module")
def fx(tmp_path_factory) -> dict[str, Path]:
    """一次性生成 Phase 2 需要的全部夹具（不落仓库目录）。"""
    out = tmp_path_factory.mktemp("fixtures")
    built: dict[str, Path] = {}
    built["small5"] = Path(mf.fx_small5(str(out)))
    built["vardur"] = Path(mf.fx_vardur(str(out)))
    built["loop_infinite"] = Path(mf.fx_loop_infinite(str(out)))
    built["partial_real"] = Path(mf.fx_partial_handcrafted(str(out))[0])
    built["partial_disp2"] = Path(mf.fx_partial_disposal(str(out), 2))
    built["partial_disp3"] = Path(mf.fx_partial_disposal(str(out), 3))
    built["first_opaque"] = Path(mf.fx_first_opaque(str(out)))
    built["binary"] = Path(mf.fx_binary_known_rgb(str(out)))
    built["odd_999"] = Path(mf.fx_odd(str(out), 999, 999, "odd"))
    built["weird_path"] = Path(mf.fx_weird_path(str(out)))
    return built


def _opaque_counts(src: GifSource) -> list[int]:
    return [int((gf.rgba[..., 3] > 0).sum()) for gf in src.iter_frames()]


def _alpha_min_first_frame(src: GifSource) -> int:
    for gf in src.iter_frames():
        return int(gf.rgba[..., 3].min())
    raise AssertionError("0 帧")


# ------------------------------------------------------------ 基础读取 --


def test_small5(fx):
    src = GifSource(fx["small5"])
    info = src.info
    assert info.frame_count == 5
    assert (info.width, info.height) == (64, 48)
    assert info.time_base == Fraction(1, 100)
    assert info.pts_seconds_list == (0.0, 0.03, 0.06, 0.09, 0.12)  # 30ms/帧
    assert info.alpha_min == 0 and info.has_transparent_pixels
    assert info.decoded_pix_fmt == "bgra"


def test_vardur_pts_not_averaged(fx):
    """变帧时长 10/30/50/100/200ms：PTS 逐帧保留，未被平均成 CFR（回归分组 1）。"""
    src = GifSource(fx["vardur"])
    info = src.info
    assert info.pts_seconds_list == (0.0, 0.01, 0.04, 0.09, 0.19)
    assert info.frame_count == 5
    assert info.duration_seconds == pytest.approx(0.39, abs=1e-6)


def test_loop_infinite_single_period(fx):
    """无限循环 GIF 只出一个周期（回归分组 2：4 帧 / 0.4s）。"""
    src = GifSource(fx["loop_infinite"])
    info = src.info
    assert info.frame_count == 4
    assert info.duration_seconds == pytest.approx(0.4, abs=1e-6)


def test_info_and_iter_frames_agree(fx):
    """两次独立打开（info 扫描 + 编码迭代）的 PTS/帧数必须一致。"""
    src = GifSource(fx["vardur"])
    frames = list(src.iter_frames())
    assert len(frames) == src.info.frame_count
    assert [gf.pts for gf in frames] == list(src.info.pts_list)
    assert all(isinstance(gf, GifFrame) for gf in frames)
    assert all(gf.time_base == Fraction(1, 100) for gf in frames)


def test_iter_frames_is_repeatable(fx):
    """iter_frames 每次调用独立打开输入，可重复消费。"""
    src = GifSource(fx["small5"])
    first = [gf.pts for gf in src.iter_frames()]
    second = [gf.pts for gf in src.iter_frames()]
    assert first == second == [0, 3, 6, 9, 12]


# ------------------------------------------------- disposal / 局部帧合成 --


def test_partial_frames_disposal1(fx):
    """局部帧 + disposal=1：累积合成。数值 = ffmpeg CLI 回归实测值。"""
    counts = _opaque_counts(GifSource(fx["partial_real"]))
    assert counts == [6060, 6860, 7410]


@pytest.mark.parametrize("key", ["partial_disp2", "partial_disp3"])
def test_partial_frames_disposal_2_3(fx, key):
    """局部帧 + disposal 2/3：首帧全幅，后续帧只剩局部块。数值 = CLI 回归实测值。"""
    counts = _opaque_counts(GifSource(fx[key]))
    assert counts == [6060, 1600, 1600]


def test_disposal_modes_really_differ(fx):
    """disposal=1 与 2/3 的合成结果必须不同，证明 disposal 真正生效。"""
    d1 = np.stack([gf.rgba[..., 3] for gf in GifSource(fx["partial_real"]).iter_frames()])
    d2 = np.stack([gf.rgba[..., 3] for gf in GifSource(fx["partial_disp2"]).iter_frames()])
    assert abs(d1.astype(np.int16) - d2.astype(np.int16)).mean() > 0.1


# -------------------------------------------------------- 首帧不透明坑 --


def test_first_opaque_full_scan_finds_transparency(fx):
    """首帧全不透明：info 全帧扫描仍能发现透明像素（结构性消灭首帧误判）。"""
    src = GifSource(fx["first_opaque"])
    assert _alpha_min_first_frame(src) == 255  # 只看首帧会误判为「无透明」
    assert src.info.alpha_min == 0  # 全帧扫描给出真相
    assert src.info.has_transparent_pixels
    assert src.info.frame_count == 4


# ------------------------------------------------------ 奇数尺寸/特殊路径 --


def test_odd_size_canvas_preserved(fx):
    """999×999：画布尺寸原样读取（WebM 侧无偶数约束）。"""
    src = GifSource(fx["odd_999"])
    info = src.info
    assert (info.width, info.height) == (999, 999)
    assert info.frame_count == 2


def test_weird_path(fx):
    """含空格、中文、圆括号、方括号的路径（回归分组 8）。"""
    src = GifSource(fx["weird_path"])
    assert src.info.frame_count == 2
    assert src.info.alpha_min == 0


# ------------------------------------------------------------- 输入校验 --


def test_missing_file_rejected():
    with pytest.raises(GifSourceError, match="输入不存在"):
        GifSource("/nonexistent/输入 文件 [x].gif")


def test_non_gif_rejected(tmp_path):
    # 真实 PNG 字节冠以 .gif 扩展名：按内容探测拒绝（不看扩展名）
    from PIL import Image

    png_path = tmp_path / "fake.gif"
    Image.new("RGB", (8, 8), (255, 0, 0)).save(tmp_path / "real.png", format="PNG")
    png_path.write_bytes((tmp_path / "real.png").read_bytes())
    with pytest.raises(GifSourceError, match="不是 GIF"):
        GifSource(png_path).info


def test_garbage_rejected(tmp_path):
    p = tmp_path / "garbage.gif"
    p.write_bytes(b"this is definitely not a gif " * 8)
    with pytest.raises(GifSourceError, match="无法打开输入"):
        GifSource(p).info


# ----------------------------------------------------------- 底色语义 --


def test_transparent_region_keeps_source_rgb(fx):
    """透明区底层 RGB 保持源 GIF 原色（白），未被预乘/涂黑（回归分组 5 语义）。

    binary 夹具：透明区隐藏 RGB=(255,255,255)，可见区=(66,74,71)。
    """
    src = GifSource(fx["binary"])
    gf = next(src.iter_frames())
    rgba = gf.rgba
    trans = rgba[..., 3] == 0
    assert trans.any()
    assert np.allclose(rgba[..., :3][trans], 255.0)  # 白底保持
    vis = rgba[..., 3] == 255
    assert np.allclose(rgba[..., :3][vis], (66, 74, 71), atol=1)  # 可见区取值已知
