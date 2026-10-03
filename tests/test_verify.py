"""Phase 5 验收测试：转换后验证模块。

断言口径 = 旧 SKILL.md §4 / tests/README.md「通用断言」：
    - 完整解码；VP9/VP8 显式 libvpx 解码保留 alpha（§4.2 陷阱专项）；
    - 全帧扫描 alpha_min<255（首帧不透明素材也能发现透明）；
    - 半透明区分轻重（32–223 晕环占比 <0.5%）；
    - PTS 逐帧一致、尺寸不变、总时长一致；
    - 底色策略：默认保持源色（意外预乘 = FAIL）；--black/--black 路径归黑 = PASS；
    - 黑底 MP4：无 alpha（预期）+ 透明区黑；
    - png/qtrle RGBA 域逐像素一致；ffv1 yuva444p 原生域逐像素一致。

运行方式（开发环境，无需系统 ffmpeg）：
    pip install -e ".[dev]"
    pytest tests/test_verify.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import make_fixtures as mf  # noqa: E402

from tgtv import convert, verify as verify_mod  # noqa: E402
from tgtv.cli import main  # noqa: E402
from tgtv.source import GifSource  # noqa: E402
from tgtv.verify import VerifyError  # noqa: E402


@pytest.fixture(scope="module")
def fx(tmp_path_factory) -> dict[str, Path]:
    out = tmp_path_factory.mktemp("fixtures")
    return {
        "small5": Path(mf.fx_small5(str(out))),
        "vardur": Path(mf.fx_vardur(str(out))),
        "binary": Path(mf.fx_binary_known_rgb(str(out))),
        "first_opaque": Path(mf.fx_first_opaque(str(out))),
    }


def _converted(fx: dict, key: str, tmp_path: Path, fmt: str = "vp9", **kw) -> Path:
    ext = {"vp9": ".webm", "vp9-lossless": ".webm", "vp8": ".webm", "prores4444": ".mov",
           "png-mov": ".mov", "qtrle": ".mov", "ffv1": ".mkv", "mp4-black": ".mp4"}[fmt]
    out = tmp_path / (key + ext)
    plan = convert.build_plan(fx[key], out, fmt, **kw)
    convert.execute(plan)
    return out


# --------------------------------------------------------------- 主路径 --


def test_verify_vp9_full_pass(fx, tmp_path):
    out = _converted(fx, "small5", tmp_path, "vp9")
    rep = verify_mod.verify(out, fx["small5"])
    assert rep.passed, verify_mod.render_report(rep)
    assert rep.decoder_used == "libvpx-vp9"  # §4.2：显式 decoder
    assert rep.decoded_pix_fmts == {"yuva420p"}  # 陷阱未发生：alpha 平面在
    names = {c.name for c in rep.checks}
    assert {"完整解码", "显式 libvpx 解码且保留 alpha 平面",
            "存在真实透明像素（全帧扫描 alpha_min<255）", "PTS 逐帧一致（未被平均成 CFR）",
            "透明区 RGB 保持源 GIF 底色（无意外预乘）"} <= names


def test_verify_first_opaque_full_scan(fx, tmp_path):
    """首帧不透明素材：全帧扫描仍判有透明像素（旧坑 3 的正面证据）。"""
    out = _converted(fx, "first_opaque", tmp_path, "vp9")
    rep = verify_mod.verify(out, fx["first_opaque"])
    assert rep.passed, verify_mod.render_report(rep)
    assert rep.stats["alpha_min_first_frame"] == 255  # 只看首帧会误判
    assert rep.stats["alpha_min_all_frames"] == 0  # 全帧扫描给出真相


def test_verify_vardur_pts(fx, tmp_path):
    out = _converted(fx, "vardur", tmp_path, "vp9")
    rep = verify_mod.verify(out, fx["vardur"])
    assert rep.passed, verify_mod.render_report(rep)
    assert rep.pts_seconds == [0.0, 0.01, 0.04, 0.09, 0.19]


@pytest.mark.parametrize("fmt", ["vp9-lossless", "vp8", "prores4444", "png-mov", "qtrle", "ffv1"])
def test_verify_all_alpha_formats(fx, tmp_path, fmt):
    out = _converted(fx, "small5", tmp_path, fmt)
    rep = verify_mod.verify(out, fx["small5"])
    assert rep.passed, f"{fmt}: {verify_mod.render_report(rep)}"


def test_verify_lossless_exact_checks_present(fx, tmp_path):
    out = _converted(fx, "small5", tmp_path, "png-mov")
    rep = verify_mod.verify(out, fx["small5"])
    assert any("RGBA 域逐像素一致" in c.name and c.passed for c in rep.checks)
    out = _converted(fx, "small5", tmp_path, "ffv1")
    rep = verify_mod.verify(out, fx["small5"])
    assert any("yuva444p 原生域逐像素一致" in c.name and c.passed for c in rep.checks)


# --------------------------------------------------------------- 黑底 --


def test_verify_black_expected_passes(fx, tmp_path):
    out = _converted(fx, "binary", tmp_path, "vp9", black_background=True)
    rep = verify_mod.verify(out, fx["binary"], expected_black=True)
    assert rep.passed, verify_mod.render_report(rep)
    assert any("透明区 RGB 已归黑" in c.name and c.passed for c in rep.checks)


def test_verify_detects_unintended_premultiply(fx, tmp_path):
    """黑底产物在默认期望下必须 FAIL——这正是旧 out.webm 意外预乘 bug 的检测器。"""
    out = _converted(fx, "binary", tmp_path, "vp9", black_background=True)
    rep = verify_mod.verify(out, fx["binary"], expected_black=False)
    assert not rep.passed
    failed = [c for c in rep.checks if not c.passed]
    assert any("意外预乘" in c.name or "保持源 GIF 底色" in c.name for c in failed)
    assert any("--black" in c.detail for c in failed)  # 提示如何修正期望


def test_verify_mp4_black(fx, tmp_path):
    out = _converted(fx, "binary", tmp_path, "mp4-black")
    rep = verify_mod.verify(out, fx["binary"], expected_black=True)
    assert rep.passed, verify_mod.render_report(rep)
    assert any("无 alpha（预期）" in c.name for c in rep.checks)


# --------------------------------------------------------------- 无源模式 --


def test_verify_without_source(fx, tmp_path):
    out = _converted(fx, "small5", tmp_path, "vp9")
    rep = verify_mod.verify(out)
    assert rep.passed
    assert rep.source_path is None
    # 无源时不含比对类检查
    assert not any("PTS 逐帧一致" in c.name for c in rep.checks)


# --------------------------------------------------------------- 错误路径 --


def test_verify_missing_file(tmp_path):
    with pytest.raises(VerifyError, match="无法打开输出"):
        verify_mod.verify(tmp_path / "nope.webm")


def test_verify_garbage_file(tmp_path):
    p = tmp_path / "bad.webm"
    p.write_bytes(b"not a webm" * 50)
    with pytest.raises(VerifyError):
        verify_mod.verify(p)


# --------------------------------------------------------------- CLI --


def test_cli_verify_pass_exit_zero(fx, tmp_path, capsys):
    out = _converted(fx, "small5", tmp_path, "vp9")
    assert main(["verify", str(out), "--source", str(fx["small5"])]) == 0
    outtext = capsys.readouterr().out
    assert "验证通过" in outtext


def test_cli_verify_fail_exit_one(fx, tmp_path, capsys):
    out = _converted(fx, "binary", tmp_path, "vp9", black_background=True)
    assert main(["verify", str(out), "--source", str(fx["binary"])]) == 1
    assert "验证失败" in capsys.readouterr().out


def test_cli_verify_json(fx, tmp_path, capsys):
    out = _converted(fx, "small5", tmp_path, "vp9")
    assert main(["verify", str(out), "--source", str(fx["small5"]), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["passed"] is True
    assert report["decoder"] == "libvpx-vp9"
    assert report["stats"]["alpha_min_all_frames"] == 0
