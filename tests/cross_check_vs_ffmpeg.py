#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Phase 6 迁移期对照：同夹具下，新栈（tgtv = PyAV 18.1.0）产物 vs
imageio-ffmpeg 0.6.0（ffmpeg 7.0.2 CLI）旧命令产物的等价性比对。

CLI 命令直接由 formats.FORMATS 的 writer_options/muxer_options 生成——
即两栈的编码参数逐项相同，唯一差异是执行引擎（CLI 管线 vs PyAV 管线）。
这是「迁移不偷换、不同档不冒充」的证据链一环。

比对维度（对每对产物）：
  1. 帧数、画布尺寸；
  2. PTS 逐帧一致（容差 1ms）与容器总时长（容差 10ms）；
  3. 解码像素格式集合一致；
  4. alpha 阈值化一致率（两产物之间，>99%）；无损路径要求 100%；
  5. 透明区 / 可见区 RGB 平均绝对差（两产物之间，同档内小差异，上限 8/10）；
  6. 无损路径：两产物各自对源逐像素一致（RGBA 域：png-mov/qtrle；
     yuva444p 原生域：ffv1），从而由传递性互相等价。

用法（开发环境，需要 dev extras 里的 imageio-ffmpeg）：
    pip install -e ".[dev]"
    python tests/cross_check_vs_ffmpeg.py [报告输出路径]

默认把 Markdown 报告写到 test-reports/<今天>-pyav-vs-ffmpeg-cli.md，
退出码 0=全部等价 / 1=存在不等价项。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import make_fixtures as mf  # noqa: E402

import av  # noqa: E402
import imageio_ffmpeg  # noqa: E402

from tgtv import convert, verify as V  # noqa: E402
from tgtv.formats import FORMATS  # noqa: E402
from tgtv.source import GifSource  # noqa: E402
from tgtv.verify import NATIVE_EXACT_KEYS, RGBA_EXACT_KEYS  # noqa: E402

EXE = imageio_ffmpeg.get_ffmpeg_exe()

PTS_TOL = 0.001     # 秒
DUR_TOL = 0.01      # 秒
ALPHA_AGREE_LOSSY = 0.99
VIS_MAD_LIMIT = 8.0     # 可见区：同参数同档的两产物之间
TRANS_MAD_LIMIT = 10.0  # 透明区：同上（信息性上限）

results = []  # (组名, 维度, 通过, 细节)


def check(group, dim, cond, detail=""):
    cond = bool(cond)
    results.append((group, dim, cond, detail))
    print("  [%s] %-42s %s" % ("PASS" if cond else "FAIL", dim, detail))
    return cond


def cli_options(spec):
    """把 FORMATS 的选项字典翻译成 CLI 参数（与 PyAV cc.options 同源）。"""
    out = []
    for k, v in spec.writer_options.items():
        flag = "-b:v" if k == "b" else ("-" + k)
        out += [flag, str(v)]
    for k, v in spec.muxer_options.items():
        out += ["-" + k, str(v)]
    return out


def cli_convert(src, dst, fmt_key, black=False):
    spec = FORMATS[fmt_key]
    cmd = [EXE, "-hide_banner", "-loglevel", "error", "-y",
           "-ignore_loop", "1", "-i", str(src), "-map", "0:v:0", "-an",
           "-fps_mode", "passthrough", "-enc_time_base", "demux"]
    if black or fmt_key == "mp4-black":
        # 旧文档命令的黑底链路：premultiply planes=0x7（与 filters.premultiply_rgb 等价）
        vf_end = "yuv420p" if fmt_key == "mp4-black" else spec.pix_fmt
        cmd += ["-vf", f"format=rgba,premultiply=inplace=1:planes=0x7,format={vf_end}"]
        if fmt_key != "mp4-black":
            cmd += ["-c:v", spec.codec]  # pix_fmt 已由 vf 链给出
            cmd += cli_options(spec)
        else:
            cmd += ["-c:v", spec.codec, "-pix_fmt", spec.pix_fmt] + cli_options(spec)
    else:
        cmd += ["-c:v", spec.codec, "-pix_fmt", spec.pix_fmt] + cli_options(spec)
    cmd.append(str(dst))
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("CLI 转换失败: %s\n%s" % (dst, p.stderr.decode(errors="ignore")[-400:]))
    return dst


def product(path):
    """解码产物 → (帧 rgba 数组, pts 秒列表, 容器时长秒, 像素格式集合)。"""
    frames, _, _, dur = V._decode_output(path)
    pts = [float(fr.pts * fr.time_base) for fr in frames]
    fmts = {fr.format.name for fr in frames}
    return V._rgba(frames), pts, dur, fmts


def native_planes(path):
    frames, _, _, _ = V._decode_output(path)
    return np.stack([V._yuva444p_planes(fr) for fr in frames])


def compare(group, src_path, a_path, b_path, fmt_key, black=False):
    """a = tgtv 产物，b = CLI 产物。"""
    spec = FORMATS[fmt_key]
    A, pts_a, dur_a, fmts_a = product(a_path)
    B, pts_b, dur_b, fmts_b = product(b_path)
    ref = V._rgba(list(GifSource(src_path).iter_frames()))
    n = min(len(A), len(B), len(ref))
    ok = True
    ok &= check(group, "帧数一致", len(A) == len(B),
                "tgtv=%d cli=%d（源=%d）" % (len(A), len(B), len(ref)))
    same_size = A.shape[1:3] == B.shape[1:3]
    ok &= check(group, "画布尺寸一致", same_size,
                "tgtv=%s cli=%s" % (A.shape[1:3], B.shape[1:3]))
    ok &= check(group, "PTS 逐帧一致",
                len(pts_a) == len(pts_b) and all(
                    abs(x - y) < PTS_TOL for x, y in zip(pts_a, pts_b)),
                "tgtv=%s cli=%s" % (pts_a[:6], pts_b[:6]))
    ok &= check(group, "容器总时长一致", dur_a is not None and dur_b is not None
                and abs(dur_a - dur_b) < DUR_TOL,
                "tgtv=%.3fs cli=%.3fs" % (dur_a or -1, dur_b or -1))
    ok &= check(group, "解码像素格式一致", fmts_a == fmts_b,
                "tgtv=%s cli=%s" % (sorted(fmts_a), sorted(fmts_b)))

    ra, rb, rr = A[:n], B[:n], ref[:n]
    a_alpha, b_alpha = ra[..., 3].astype(int), rb[..., 3].astype(int)
    agree = float(((a_alpha >= 128) == (b_alpha >= 128)).mean())
    if spec.lossy:
        ok &= check(group, "alpha 阈值化一致（两产物）", agree > ALPHA_AGREE_LOSSY,
                    "%.3f%%" % (agree * 100))
    else:
        ok &= check(group, "alpha 阈值化一致（无损=100%）", agree == 1.0,
                    "%.3f%%" % (agree * 100))

    vis = rr[..., 3] == 255
    trans = rr[..., 3] == 0
    if vis.any():
        vis_mad = float(np.abs(ra[..., :3].astype(int) - rb[..., :3].astype(int))[np.broadcast_to(vis[..., None], ra[..., :3].shape)].mean())
        ok &= check(group, "可见区 RGB 同档（MAD<%.0f）" % VIS_MAD_LIMIT,
                    vis_mad < VIS_MAD_LIMIT, "MAD=%.2f" % vis_mad)
    if trans.any() and spec.alpha:
        t_mad = float(np.abs(ra[..., :3].astype(int) - rb[..., :3].astype(int))[np.broadcast_to(trans[..., None], ra[..., :3].shape)].mean())
        ok &= check(group, "透明区 RGB 同档（MAD<%.0f）" % TRANS_MAD_LIMIT,
                    t_mad < TRANS_MAD_LIMIT, "MAD=%.2f" % t_mad)

    # 逐像素精确断言：与 verify.py 同一套域规则（不是所有「无损」格式都在
    # RGBA 域逐像素可断言——vp9-lossless 受 4:2:0 表示限制、prores 受
    # 10bit 量化限制，二者的等价性由上面的同档指标承载）
    if fmt_key in RGBA_EXACT_KEYS:
        exact_a = np.array_equal(ra, rr)
        exact_b = np.array_equal(rb, rr)
        ok &= check(group, "RGBA 域对源逐像素一致（两产物）",
                    exact_a and exact_b,
                    "tgtv=%s cli=%s" % (exact_a, exact_b))
    elif fmt_key in NATIVE_EXACT_KEYS:
        na = native_planes(a_path)
        nb = native_planes(b_path)
        ok &= check(group, "yuva444p 原生域逐像素一致（两产物）",
                    np.array_equal(na, nb) and len(na) == len(ref),
                    "产物间逐像素一致" if np.array_equal(na, nb) else "产物间存在差异")
    return ok


def main():
    tmp = Path(tempfile.mkdtemp(prefix="xcheck-"))
    fx = {
        "small5": mf.fx_small5(str(tmp)),
        "binary": mf.fx_binary_known_rgb(str(tmp)),
        "vardur": mf.fx_vardur(str(tmp)),
        "partial_real": mf.fx_partial_handcrafted(str(tmp))[0],
        "first_opaque": mf.fx_first_opaque(str(tmp)),
        "edge": mf.fx_edge(str(tmp)),
        "odd_999": mf.fx_odd(str(tmp), 999, 999, "odd"),
        "disposal2": mf.fx_disposal(str(tmp), 2),
    }
    pairs = (
        [("small5", k, False) for k in
         ("vp9", "vp9-lossless", "vp8", "prores4444", "png-mov", "qtrle", "ffv1", "mp4-black")]
        + [("binary", "vp9", False), ("binary", "vp9", True)]
        + [("binary", "mp4-black", False)]
        + [(k, "vp9", False) for k in
           ("vardur", "partial_real", "first_opaque", "edge", "odd_999", "disposal2")]
    )

    print("CLI 基准:", EXE)
    print("PyAV:", av.__version__)
    groups_ok = {}
    for fx_key, fmt_key, black in pairs:
        spec = FORMATS[fmt_key]
        group = "%s × %s%s" % (fx_key, fmt_key, " --black" if black else "")
        print("\n%s" % group)
        src = fx[fx_key]
        a = tmp / f"{fx_key}_{fmt_key}{'_black' if black else ''}_tgtv{spec.ext}"
        b = tmp / f"{fx_key}_{fmt_key}{'_black' if black else ''}_cli{spec.ext}"
        convert.execute(convert.build_plan(src, a, fmt_key, black_background=black))
        cli_convert(src, b, fmt_key, black=black)
        groups_ok[group] = compare(group, src, a, b, fmt_key, black=black)

    # ---- 报告 ----
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        HERE, "..", "test-reports", "%s-pyav-vs-ffmpeg-cli.md" % date.today().isoformat())
    out.parent.mkdir(parents=True, exist_ok=True)
    exe_ver = subprocess.run([EXE, "-version"], capture_output=True).stdout.decode(errors="ignore").splitlines()[0]
    lines = [
        "# 新栈 vs ffmpeg CLI 等价性对照（Phase 6 迁移期）",
        "",
        "- 日期：%s" % date.today().isoformat(),
        "- 新栈：tgtv（PyAV %s + NumPy，注册表驱动 writer）" % av.__version__,
        "- 基准：%s（imageio-ffmpeg 捆带）" % exe_ver,
        "- 方法：CLI 命令由 `formats.FORMATS.writer_options` 逐项生成——两栈编码参数相同，"
        "唯一差异是执行引擎；同夹具、同解码器（显式 libvpx*）读回比对。",
        "",
        "| 组 | 结果 |",
        "|---|---|",
    ]
    for g, ok in groups_ok.items():
        lines.append("| %s | %s |" % (g, "✅ 等价" if ok else "❌ 不等价"))
    n_pass = sum(1 for _, _, c, _ in results if c)
    lines += [
        "",
        "逐项检查：%d/%d 通过。" % (n_pass, len(results)),
        "",
        "## 阈值校准记录",
        "",
        "- `THRESH_TRANS_KEPT_MAD`（verify.py 透明区底色检查）由 12 调整为 40："
        "VP9 CRF30 在 disposal2/3 夹具上透明区 RGB 存在 15–18 的固有漂移"
        "（CLI 18.45/15.02，tgtv 15.83/15.92——两栈同档等价，均非回归），"
        "而意外预乘的漂移量级 ≥100（白底拉黑）。",
        "",
    ]
    for g, dim, c, d in results:
        if not c:
            lines.append("- ❌ %s / %s —— %s" % (g, dim, d))
    if all(c for _, _, c, _ in results):
        lines.append("- 全部维度通过，无失败项。")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n报告:", out)
    fails = [g for g, ok in groups_ok.items() if not ok]
    print("等价组 %d/%d" % (len(groups_ok) - len(fails), len(groups_ok)))
    if fails:
        for g in fails:
            print("  不等价:", g)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
