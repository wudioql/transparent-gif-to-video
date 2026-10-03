#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Python 栈可行性探测脚本（分析阶段的证据收集，不是运行时代码）。

目的：在【没有系统 ffmpeg】的环境中，验证 pip 依赖能否覆盖
transparent-gif-to-video skill 的全部运行时职责。

运行方式（在一个装好依赖的 venv 里）：
    pip install av pillow numpy imageio-ffmpeg
    python docs/probes/2026-10-03-pystack-probe.py

配套结论见 docs/python-only-refactor-analysis.md。
"""

import os
import shutil
import subprocess
import sys
from fractions import Fraction

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TESTS = os.path.join(REPO, "tests")
sys.path.insert(0, TESTS)

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results = []


def check(name, cond, detail=""):
    tag = PASS if cond else FAIL
    results.append((tag, name, detail))
    print("  [%s] %-56s %s" % (tag, name, detail))
    return cond


def section(title):
    print("\n" + "=" * 96)
    print(title)
    print("=" * 96)


# ---------------------------------------------------------------------------
section("A. 环境报告")
# ---------------------------------------------------------------------------

print("python          :", sys.version.split()[0], sys.platform)
print("system ffmpeg   :", shutil.which("ffmpeg") or "（不存在 —— 本探测刻意在无系统 ffmpeg 的环境运行）")
import av
import PIL
import imageio_ffmpeg

print("av (PyAV)       :", av.__version__)
print("内置 ffmpeg 库   :", {k: ".".join(map(str, v)) for k, v in av.library_versions.items()})
print("pillow          :", PIL.__version__)
print("numpy           :", np.__version__)
print("imageio-ffmpeg  :", imageio_ffmpeg.__version__)
check("A1 沙箱无系统 ffmpeg（探测前提）", shutil.which("ffmpeg") is None,
      "若本机有系统 ffmpeg，探测结论仍成立但不再能证明『无系统 ffmpeg』")

# ---------------------------------------------------------------------------
section("B. PyAV 编解码器可用性矩阵（skill 需要的 8 个 encoder + 3 个 decoder）")
# ---------------------------------------------------------------------------

for name in ["libvpx-vp9", "libvpx", "prores_ks", "hap", "png", "qtrle", "ffv1", "libx264"]:
    try:
        av.codec.CodecContext.create(name, "w")
        check("B  encoder %s" % name, True)
    except Exception as e:
        check("B  encoder %s" % name, False, type(e).__name__)
for name in ["libvpx-vp9", "libvpx", "gif", "hap"]:
    try:
        av.codec.CodecContext.create(name, "r")
        check("B  decoder %s" % name, True)
    except Exception:
        check("B  decoder %s" % name, False)

# ---------------------------------------------------------------------------
section("C. GIF 解码保真（PyAV vs ffmpeg CLI 回归数值）")
# ---------------------------------------------------------------------------

from PIL import Image, ImageDraw  # noqa: E402
from make_fixtures import write_gif, _partial_frames  # noqa: E402

# C1: 变帧时长 + 无限循环 GIF
frames = []
for i in range(5):
    im = Image.new("RGBA", (64, 48), (255, 255, 255, 0))
    ImageDraw.Draw(im).ellipse([4 + i * 8, 8, 28 + i * 8, 40], fill=(220, 40, 40, 255))
    frames.append(im)
frames[0].save("/tmp/_probe_t.gif", save_all=True, append_images=frames[1:],
               duration=[30, 10, 50, 100, 200], loop=0, disposal=2)

v = av.open("/tmp/_probe_t.gif", options={"ignore_loop": "1"})
pts, alpha_mins = [], []
for fr in v.decode(video=0):
    pts.append(round(float(fr.pts * fr.time_base), 3))
    alpha_mins.append(int(fr.to_ndarray(format="rgba")[..., 3].min()))
v.close()
check("C1 变帧时长 PTS 逐帧保留", pts == [0.0, 0.03, 0.04, 0.09, 0.19], str(pts))
check("C2 逐帧存在透明像素", all(a == 0 for a in alpha_mins), str(alpha_mins))

# C2: loop=0（无限循环）默认与显式 ignore_loop 都是单周期
v = av.open("/tmp/_probe_t.gif")
n = sum(1 for _ in v.decode(video=0))
v.close()
check("C3 无限循环 GIF 不带选项也是单周期（demuxer 默认 true，与 CLI 一致）", n == 5, "%d 帧" % n)

# C3: disposal 合成与 2026-10-02 回归报告 §15 的 ffmpeg CLI 数值比对
pal = [(255, 255, 255), (30, 90, 200), (240, 160, 40), (20, 20, 20)]
expect = {1: [6060, 6860, 7410], 2: [6060, 1600, 1600], 3: [6060, 1600, 1600]}
for mode in (1, 2, 3):
    W, H, fr = _partial_frames(mode)
    write_gif("/tmp/_probe_d%d.gif" % mode, W, H, pal, fr, loop=1)
    v = av.open("/tmp/_probe_d%d.gif" % mode, options={"ignore_loop": "1"})
    counts = [int((f.to_ndarray(format="rgba")[..., 3] > 0).sum()) for f in v.decode(video=0)]
    v.close()
    check("C4 disposal=%d 合成与 ffmpeg CLI 回归数值一致" % mode, counts == expect[mode],
          "%s (期望 %s)" % (counts, expect[mode]))

# ---------------------------------------------------------------------------
section("D. 默认路径闭环：GIF → VP9 yuva420p WebM(alpha) → 验证")
# ---------------------------------------------------------------------------

src = av.open("/tmp/_probe_t.gif", options={"ignore_loop": "1"})
vst = src.streams.video[0]
out = av.open("/tmp/_probe_t.webm", "w")
ost = out.add_stream("libvpx-vp9", rate=100)
cc = ost.codec_context
cc.width, cc.height = 64, 48
cc.pix_fmt = "yuva420p"
cc.time_base = Fraction(1, 100)  # 与 gif demuxer 相同 → 等价 -enc_time_base demux
cc.options = {"crf": "30", "b": "0", "deadline": "good", "cpu-used": "2",
              "auto-alt-ref": "0", "row-mt": "1"}
for fr in src.decode(video=0):
    if fr.format.name != "yuva420p":
        fr = fr.reformat(format="yuva420p")
    fr.pts = fr.pts  # 源 PTS 原样透传（等价 -fps_mode passthrough）
    fr.time_base = Fraction(1, 100)
    for pkt in ost.encode(fr):
        out.mux(pkt)
for pkt in ost.encode(None):
    out.mux(pkt)
out.close()
src.close()
check("D1 VP9 yuva420p 编码成功", os.path.getsize("/tmp/_probe_t.webm") > 0)

# 显式 libvpx-vp9 解码（skill §4.2 的规则）
v = av.open("/tmp/_probe_t.webm")
dec = av.codec.CodecContext.create("libvpx-vp9", "r")
dpts, dfmts, dmin = [], set(), 999
for pkt in v.demux(video=0):
    for fr in dec.decode(pkt):
        dpts.append(round(float(fr.pts * fr.time_base), 3))
        dfmts.add(fr.format.name)
        dmin = min(dmin, int(fr.to_ndarray(format="rgba")[..., 3].min()))
v.close()
check("D2 显式 libvpx-vp9 解码得到 yuva420p（alpha 平面在）", dfmts == {"yuva420p"}, str(dfmts))
check("D3 输出 PTS 逐帧等于源（变时长未被平均）", dpts == [0.0, 0.03, 0.04, 0.09, 0.19], str(dpts))
check("D4 输出含真实透明像素（全帧扫描 alpha_min=0）", dmin == 0, "alpha_min=%d" % dmin)

# 原生 vp9 解码陷阱复现（文档 §4.2 的坑在 PyAV 下原样存在）
v = av.open("/tmp/_probe_t.webm")
nfmts = set()
for fr in v.decode(video=0):
    nfmts.add(fr.format.name)
v.close()
check("D5 陷阱复现：不指定 decoder 时读出 yuv420p（无 alpha）", nfmts == {"yuv420p"}, str(nfmts))

data = open("/tmp/_probe_t.webm", "rb").read()
check("D6 容器含 AlphaMode(0x53C0)", b"\x53\xc0" in data)
check("D7 容器含 BlockAdditional(0x75A1)（WebM alpha 实际载体）", b"\x75\xa1" in data)

# ---------------------------------------------------------------------------
section("E. 其余编码路径矩阵")
# ---------------------------------------------------------------------------

W, H = 64, 48
matrix = [
    ("vp8-crf30",  "libvpx",     "yuva420p",     "webm",     {"crf": "30", "b": "0", "auto-alt-ref": "0"}, "libvpx",     True),
    ("vp9-loss",   "libvpx-vp9", "yuva420p",     "webm",     {"lossless": "1", "auto-alt-ref": "0"},       "libvpx-vp9", True),
    ("prores4444", "prores_ks",  "yuva444p10le", "mov",      {"profile": "4444", "alpha_bits": "8"},        None,         True),
    ("png-mov",    "png",        "rgba",         "mov",      {},                                           None,         True),
    ("qtrle",      "qtrle",      "argb",         "mov",      {},                                           None,         True),
    ("ffv1",       "ffv1",       "yuva444p",     "matroska", {"level": "3", "coder": "1", "g": "1"},       None,         True),
    ("x264-mp4",   "libx264",    "yuv420p",      "mp4",      {"crf": "20", "preset": "veryfast"},          None,         False),
]
for name, codec, pixfmt, fmt, opts, dec, alpha_expected in matrix:
    try:
        o = av.open("/tmp/_probe_m_%s.%s" % (name, fmt), "w", format=fmt)
        s = o.add_stream(codec, rate=100)
        c = s.codec_context
        c.width, c.height, c.pix_fmt, c.time_base = W, H, pixfmt, Fraction(1, 100)
        c.options = opts
        for i in range(5):
            arr = np.zeros((H, W, 4), np.uint8)
            arr[..., 0], arr[..., 1], arr[..., 2] = 200, 40, 40
            arr[i * 6:i * 6 + 20, 10:30, 3] = 255
            fr = av.VideoFrame.from_ndarray(arr, format="rgba")
            if pixfmt != "rgba":
                fr = fr.reformat(format=pixfmt)
            fr.pts, fr.time_base = i * 3, Fraction(1, 100)
            for pkt in s.encode(fr):
                o.mux(pkt)
        for pkt in s.encode(None):
            o.mux(pkt)
        o.close()
        # 验证
        v = av.open("/tmp/_probe_m_%s.%s" % (name, fmt))
        d = av.codec.CodecContext.create(dec, "r") if dec else None
        n, amin = 0, 999
        gen = (d.decode(pkt) for pkt in v.demux(video=0)) if d else (x for x in [v.decode(video=0)][0])
        frames_iter = v.decode(video=0) if d is None else None
        if d:
            for pkt in v.demux(video=0):
                for fr in d.decode(pkt):
                    n += 1
                    amin = min(amin, int(fr.to_ndarray(format="rgba")[..., 3].min()))
        else:
            for fr in frames_iter:
                n += 1
                amin = min(amin, int(fr.to_ndarray(format="rgba")[..., 3].min()))
        v.close()
        alpha_ok = (amin < 255) if alpha_expected else True
        check("E  %-11s 编码+解码 %d 帧, alpha=%s" % (name, n, "在" if amin < 255 else "无/全255"),
              n == 5 and alpha_ok)
    except Exception as e:
        check("E  %-11s" % name, False, "%s: %s" % (type(e).__name__, str(e)[:90]))

# ---------------------------------------------------------------------------
section("F. 尺寸约束行为")
# ---------------------------------------------------------------------------

try:
    o = av.open("/tmp/_probe_odd999.webm", "w")
    s = o.add_stream("libvpx-vp9", rate=100)
    c = s.codec_context
    c.width, c.height, c.pix_fmt, c.time_base = 999, 999, "yuva420p", Fraction(1, 100)
    c.options = {"crf": "30", "b": "0", "auto-alt-ref": "0"}
    arr = np.zeros((999, 999, 4), np.uint8)
    arr[10:100, 10:100, 3] = 255
    fr = av.VideoFrame.from_ndarray(arr, format="rgba").reformat(format="yuva420p")
    fr.pts, fr.time_base = 0, Fraction(1, 100)
    for pkt in s.encode(fr):
        o.mux(pkt)
    for pkt in s.encode(None):
        o.mux(pkt)
    o.close()
    check("F1 VP9 999×999（奇数）编码成功（WebM 侧不设偶数约束，与文档一致）", True)
except Exception as e:
    check("F1 VP9 999×999 编码", False, str(e)[:90])

try:
    o = av.open("/tmp/_probe_odd999.mp4", "w")
    s = o.add_stream("libx264", rate=100)
    c = s.codec_context
    c.width, c.height, c.pix_fmt, c.time_base = 999, 999, "yuv420p", Fraction(1, 100)
    c.options = {"crf": "20"}
    arr = np.zeros((999, 999, 3), np.uint8)
    fr = av.VideoFrame.from_ndarray(arr, format="rgb24").reformat(format="yuv420p")
    fr.pts, fr.time_base = 0, Fraction(1, 100)
    for pkt in s.encode(fr):
        o.mux(pkt)
    o.close()
    check("F2 libx264 999×999 yuv420p 拒绝（硬失败保留）", False, "竟然成功了")
except Exception:
    check("F2 libx264 999×999 yuv420p 拒绝（硬失败保留）", True,
          "（报错为 ExternalError，文案不含 width/height not divisible by 2 → 需自行预检生成可读报错）")

# ---------------------------------------------------------------------------
section("G. 黑底归一化 filter 链（§3.0 原样迁移）")
# ---------------------------------------------------------------------------

try:
    from av.filter import Graph
    g = Graph()
    b = g.add("buffer", "video_size=64x48:pix_fmt=rgba:time_base=1/100")
    fm = g.add("format", "rgba")
    pm = g.add("premultiply", "inplace=1:planes=0x7")
    sp = g.add("setparams", "alpha_mode=straight")
    sink = g.add("buffersink")
    b.link_to(fm); fm.link_to(pm); pm.link_to(sp); sp.link_to(sink)
    g.configure()
    arr = np.zeros((48, 64, 4), np.uint8)
    arr[..., :3] = 255
    arr[10:30, 10:30, 3] = 255
    fr = av.VideoFrame.from_ndarray(arr, format="rgba")
    fr.pts, fr.time_base = 0, Fraction(1, 100)
    g.push(fr)
    g.push(None)
    outs = []
    while True:
        try:
            outs.append(sink.pull())
        except (av.error.EOFError, StopIteration):
            break
    a = outs[0].to_ndarray(format="rgba")
    trans = a[..., 3] == 0
    vis = a[..., 3] == 255
    black = bool((a[..., :3][trans] == 0).all())
    keep = abs(float(a[..., :3][vis].mean()) - 255.0) < 2
    check("G1 premultiply+setparams(alpha_mode) 链可用（FFmpeg 8.x 代系支持）",
          black and keep, "透明区RGB=%s 可见区=%.1f" % (a[..., :3][trans].mean(axis=0).round(1), a[..., :3][vis].mean()))
except Exception as e:
    check("G1 黑底 filter 链", False, "%s: %s" % (type(e).__name__, str(e)[:90]))

# ---------------------------------------------------------------------------
section("H. imageio-ffmpeg 二进制（路线 A1：命令原样、仅换二进制来源）")
# ---------------------------------------------------------------------------

exe = imageio_ffmpeg.get_ffmpeg_exe()
ver = subprocess.run([exe, "-version"], capture_output=True, text=True).stdout.splitlines()[0]
print("  binary:", exe)
print("  version:", ver)
enc = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
for name in ["libvpx-vp9", "libvpx", "prores_ks", "hap", "png", "qtrle", "ffv1", "libx264"]:
    check("H  encoder %s" % name, name in enc)

# README 默认命令原样执行（仅替换可执行路径）
cmd = [exe, "-hide_banner", "-n", "-ignore_loop", "1", "-i", "/tmp/_probe_t.gif", "-map", "0:v:0", "-an",
       "-fps_mode", "passthrough", "-enc_time_base", "demux",
       "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", "-b:v", "0", "-crf", "30",
       "-deadline", "good", "-cpu-used", "2", "-row-mt", "1", "/tmp/_probe_readme.webm"]
p = subprocess.run(cmd, capture_output=True, text=True)
check("H9 README 默认命令原样执行成功", p.returncode == 0)

v = subprocess.run([exe, "-hide_banner", "-c:v", "libvpx-vp9", "-i", "/tmp/_probe_readme.webm",
                    "-vf", "alphaextract,format=gray,signalstats,metadata=print", "-f", "null", "-"],
                   capture_output=True, text=True)
import re
ymins = [int(x) for x in re.findall(r"lavfi\.signalstats\.YMIN=(\d+)", v.stderr)]
check("H10 §4.2 验证命令原样执行且 YMIN<255", bool(ymins) and min(ymins) < 255,
      "采样 %d 帧 min=%s" % (len(ymins), min(ymins) if ymins else None))

# setparams=alpha_mode 版本缺口
v2 = subprocess.run([exe, "-hide_banner", "-y", "-ignore_loop", "1", "-i", "/tmp/_probe_t.gif",
                     "-map", "0:v:0", "-an",
                     "-vf", "format=rgba,premultiply=inplace=1:planes=0x7,setparams=alpha_mode=straight",
                     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-f", "null", "-"],
                    capture_output=True, text=True)
gap = "Option not found" in v2.stderr or v2.returncode != 0
check("H11 已知缺口：ffmpeg 7.0.2 不支持 setparams=alpha_mode（版本兼容问题，非能力缺失）",
      gap, "exit=%d" % v2.returncode)

# ---------------------------------------------------------------------------
section("I. Windows wheel 可用性（文档目标环境为 Windows）")
# ---------------------------------------------------------------------------

try:
    import json
    import urllib.request
    for pkg in ["av", "imageio-ffmpeg", "pillow", "numpy"]:
        with urllib.request.urlopen("https://pypi.org/pypi/%s/json" % pkg, timeout=15) as r:
            j = json.load(r)
        ver_latest = j["info"]["version"]
        names = [u["filename"] for u in j["releases"].get(ver_latest, []) if "win_amd64" in u["filename"]]
        check("I  %-14s %s 提供 win_amd64 wheel" % (pkg, ver_latest), bool(names), names[0] if names else "")
except Exception as e:
    check("I  PyPI 查询", False, "%s: %s" % (type(e).__name__, str(e)[:90]))

# ---------------------------------------------------------------------------
print("\n" + "=" * 96)
p_ = [r for r in results if r[0] == PASS]
f_ = [r for r in results if r[0] == FAIL]
print("总计：%d PASS / %d FAIL" % (len(p_), len(f_)))
for tag, name, detail in f_:
    print("  FAIL: %s  %s" % (name, detail))
