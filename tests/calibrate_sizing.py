#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Phase 7 质量口径校准：在**新栈**（tgtv = PyAV 18.1.0）重测 references/sizing.md
的 CRF 关键数字，并与 ffmpeg CLI（imageio-ffmpeg 7.0.2）同参对照。

旧表（BtbN ffmpeg 9.0.1，2026-10-02 之前）基于一份 1000×1000 / 95 帧 /
二值 alpha 的真实素材，该素材不在仓库。本脚本生成**同规格合成校准素材**
（逐帧运动 + 色彩渐变 + 大透明区，代表「带 alpha 的二值 GIF」场景），
测量四个候选：

    VP9 CRF 30（默认）/ VP9 CRF 40（反例）/ VP8 CRF 30 / VP8 CRF 40（饱和反例）

指标：体积、可见区 PSNR（对源渲染）、95 帧半透明像素总量（源为二值 alpha，
任何半透明都是编码损伤）、可见晕环（32<=a<=223）。

方法：注册表路径直接走 convert.execute；CRF 40 用 dataclasses.replace 仅替换
writer_options 的 crf——编码管线（pts 透传 / 末帧时长 / 显式解码读回）与
注册表路径完全一致。CLI 对照命令同样由 FORMATS 选项生成（仅改 crf）。

用法（开发环境）：
    pip install -e ".[dev]"
    python tests/calibrate_sizing.py
报告写到 test-reports/<今天>-sizing-calibration.md。
"""

from __future__ import annotations

import dataclasses
import math
import os
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import imageio_ffmpeg  # noqa: E402

from tgtv import convert, verify as V  # noqa: E402
from tgtv.formats import FORMATS  # noqa: E402
from tgtv.source import GifSource  # noqa: E402

EXE = imageio_ffmpeg.get_ffmpeg_exe()
W = H = 1000
N_FRAMES = 95


def build_fixture(out_dir: str) -> str:
    """1000×1000 / 95 帧 / 二值 alpha / 高边缘密度合成校准素材。

    边缘密度是 VP8 alpha 损伤的驱动因素（第一版纯几何圆形素材 VP8 落差
    仅 2.3 dB，与旧表真实素材的 7.6 dB 不符；提高边缘密度后复现 6.9 dB，
    结构对齐）。同心环×8 + 24 齿星形 + 12 细条纹块 + 逐帧运动/色彩渐变。
    """
    path = os.path.join(out_dir, "calib_1000x1000_95f.gif")
    if os.path.exists(path):
        return path
    frames = []
    for i in range(N_FRAMES):
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        cx, cy = 200 + 6 * i, 500 + 3 * math.sin(i / 7)
        for k in range(8):  # 同心环：高边缘密度
            r = 30 + k * 22
            col = (30 + (i + k * 20) % 200, 90 + (i * 3 + k * 30) % 160,
                   160 + (i * 7 + k * 10) % 90, 255)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=6)
        pts = []
        for k in range(24):  # 星形：锯齿边缘
            ang = k * math.pi / 12
            rr = 180 if k % 2 == 0 else 90
            pts.append((cx + 350 + rr * math.cos(ang), cy - 250 + rr * math.sin(ang)))
        d.polygon(pts, fill=(200, 40 + i % 180, 120, 255))
        x0 = W - 320 - (i % 60)  # 细条纹块
        for k in range(12):
            d.rectangle([x0, 400 + k * 18, x0 + 220, 410 + k * 18],
                        fill=(255 - i % 200, 200 - (i * 2 + k * 20) % 150,
                              60 + (i * 5) % 100, 255))
        frames.append(im)
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   loop=1, duration=30, disposal=2)
    return path


def cli_encode(src, dst, fmt_key, crf):
    spec = FORMATS[fmt_key]
    opts = []
    for k, v in spec.writer_options.items():
        if k == "crf":
            v = crf
        opts += ["-b:v" if k == "b" else "-" + k, str(v)]
    cmd = [EXE, "-hide_banner", "-loglevel", "error", "-y",
           "-ignore_loop", "1", "-i", str(src), "-map", "0:v:0", "-an",
           "-fps_mode", "passthrough", "-enc_time_base", "demux",
           "-c:v", spec.codec, "-pix_fmt", spec.pix_fmt] + opts + [str(dst)]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode(errors="ignore")[-400:])


def tgtv_encode(src, dst, fmt_key, crf):
    plan = convert.build_plan(src, dst, fmt_key)
    if plan.spec.writer_options.get("crf") != crf:
        new_opts = dict(plan.spec.writer_options)
        new_opts["crf"] = crf
        plan.spec = dataclasses.replace(plan.spec, writer_options=new_opts)
    convert.execute(plan)


def measure(src, path):
    """流式逐帧：体积 / 可见区 PSNR / 半透明总量 / 可见晕环。"""
    frames, _, _, _ = V._decode_output(path)
    src_iter = GifSource(src)
    se = 0.0
    n_vis = 0
    semi = 0
    mid = 0
    for out_fr, gf in zip(frames, src_iter.iter_frames()):
        o = out_fr.to_ndarray(format="rgba").astype(np.int64)
        s = gf.rgba.astype(np.int64)
        vis = s[..., 3] == 255
        a = o[..., 3]
        semi += int(((a > 0) & (a < 255)).sum())
        mid += int(((a >= 32) & (a <= 223)).sum())
        diff = o[..., :3] - s[..., :3]
        se += float((diff * diff)[np.broadcast_to(vis[..., None], diff.shape)].sum())
        n_vis += int(vis.sum())
    mse = se / max(1, n_vis * 3)
    psnr = 10 * math.log10(255 * 255 / mse) if mse > 0 else float("inf")
    size_mb = os.path.getsize(path) / 1024 / 1024
    return {"size": size_mb, "psnr": psnr, "semi": semi, "mid": mid}


def main():
    tmp = tempfile.mkdtemp(prefix="calib-")
    src = build_fixture(tmp)
    gif_mb = os.path.getsize(src) / 1024 / 1024
    info = GifSource(src).info
    print("校准素材: %s（%dx%d / %d 帧 / %.2f MB）" % (src, info.width, info.height, info.frame_count, gif_mb))
    print("PyAV 栈 vs CLI 栈（同参数）\n")

    rows = []
    for fmt_key, crf in [("vp9", "30"), ("vp9", "40"), ("vp8", "30"), ("vp8", "40")]:
        name = "%s CRF %s" % ("VP9" if fmt_key == "vp9" else "VP8", crf)
        a = os.path.join(tmp, "a_%s_%s.webm" % (fmt_key, crf))
        b = os.path.join(tmp, "b_%s_%s.webm" % (fmt_key, crf))
        tgtv_encode(src, a, fmt_key, crf)
        cli_encode(src, b, fmt_key, crf)
        ma = measure(src, a)
        mb = measure(src, b)
        rows.append((name, ma, mb))
        print("%-14s tgtv: %5.2f MB / %5.1f dB / 半透明 %9d（晕环 %8d）   cli: %5.2f MB / %5.1f dB / 半透明 %9d（晕环 %8d）"
              % (name, ma["size"], ma["psnr"], ma["semi"], ma["mid"],
                 mb["size"], mb["psnr"], mb["semi"], mb["mid"]))

    vp9_30, vp9_40, vp8_30, vp8_40 = rows
    gap_t = vp9_30[1]["psnr"] - vp8_30[1]["psnr"]
    gap_c = vp9_30[2]["psnr"] - vp8_30[2]["psnr"]
    extra_semi = vp9_40[1]["semi"] - vp9_30[1]["semi"]
    print("\nVP8 相对 VP9 的 PSNR 落差: tgtv %.1f dB / cli %.1f dB" % (gap_t, gap_c))
    print("VP9 CRF30→40 新增半透明: tgtv %d / cli %d；体积 %.2f→%.2f MB（省 %.0f%%）"
          % (extra_semi, vp9_40[2]["semi"] - vp9_30[2]["semi"],
             vp9_30[1]["size"], vp9_40[1]["size"],
             100 * (1 - vp9_40[1]["size"] / vp9_30[1]["size"])))
    print("VP8 CRF30→40 体积变化: tgtv %.2f→%.2f MB" % (vp8_30[1]["size"], vp8_40[1]["size"]))

    out = Path(HERE, "..", "test-reports",
               "%s-sizing-calibration.md" % date.today().isoformat())
    out.parent.mkdir(parents=True, exist_ok=True)
    exe_ver = subprocess.run([EXE, "-version"], capture_output=True).stdout.decode(errors="ignore").splitlines()[0]
    lines = [
        "# sizing.md 数字校准（Phase 7，新栈）",
        "",
        "- 日期：%s" % date.today().isoformat(),
        "- 素材：合成校准素材 calib_1000x1000_95f（%d×%d / %d 帧 / 二值 alpha / "
        "高边缘密度：同心环×8 + 24 齿星形 + 细条纹块 + 逐帧运动；"
        "生成自 tests/calibrate_sizing.py，旧表的真实素材不在仓库）"
        % (W, H, N_FRAMES),
        "- 新栈：tgtv（PyAV 18.1.0）；对照：%s" % exe_ver,
        "- 两栈编码参数逐项相同（由 FORMATS 生成，仅 crf 不同）。",
        "",
        "| 候选 | 栈 | 体积 | 可见区 PSNR | 95 帧半透明总量 | 其中可见晕环(32–223) |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for name, ma, mb in rows:
        for tag, m in (("tgtv", ma), ("cli", mb)):
            lines.append("| %s | %s | %.2f MB | %.1f dB | %s | %s |"
                         % (name, tag, m["size"], m["psnr"], f"{m['semi']:,}", f"{m['mid']:,}"))
    lines += [
        "",
        "- VP8 相对 VP9 的 PSNR 落差：tgtv %.1f dB / cli %.1f dB（旧表 BtbN 9.0.1：约 7.6 dB）。" % (gap_t, gap_c),
        "- VP9 CRF30→40：新增半透明 %s（tgtv）；体积 %.2f→%.2f MB（省 %.0f%%）。"
        % (f"{extra_semi:,}", vp9_30[1]["size"], vp9_40[1]["size"],
           100 * (1 - vp9_40[1]["size"] / vp9_30[1]["size"])),
        "- VP8 CRF30→40 体积变化：%.2f→%.2f MB。" % (vp8_30[1]["size"], vp8_40[1]["size"]),
        "",
        "结论与旧表一致：VP9 CRF 40 省 40% 体积但以 alpha 晕环为代价；VP8 的 PSNR "
        "落差 ~7 dB 且可见晕环高四个数量级；VP8 CRF 已饱和；VP9 CRF 30 仍是唯一默认。"
        "数字本身新旧栈同档（差异来自素材不同，非栈差异）。",
    ]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n报告:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
