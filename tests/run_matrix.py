#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
针对 tests/README.md 中列出的边界夹具跑完整回归。

**仅用于开发/回归测试**，不属于 skill 运行时。运行时唯一依赖仍是系统 ffmpeg。
依赖：numpy、Pillow（夹具有）。

用法：
    python tests/run_matrix.py

断言覆盖：
  - 变帧时长是否被保留（不重采样成 CFR）
  - 无限循环 GIF 是否只输出一个周期
  - disposal 2/3 与局部帧的合成结果是否与 ffmpeg 自身对 GIF 的渲染一致
  - 奇数尺寸 / 非 4 倍数尺寸在各链路上的真实行为
  - 含空格、中文、括号、方括号的路径
  - 首帧不透明时「必须扫全帧」是否必要（刻意记录只看首帧会得到什么）
  - 透明区底层 RGB 是否被意外改动
"""

import os
import re
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from make_fixtures import main as build_fixtures  # noqa: E402

FFMPEG = "ffmpeg"
DEFAULT_VP9 = ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0",
               "-b:v", "0", "-crf", "30", "-deadline", "good", "-cpu-used", "2", "-row-mt", "1"]
PUBLIC = ["-hide_banner", "-n", "-ignore_loop", "1", "-i", "<INPUT>", "-map", "0:v:0", "-an",
          "-fps_mode", "passthrough", "-enc_time_base", "demux"]

results = []


def run(cmd):
    p = subprocess.run(cmd, capture_output=True)
    return p.returncode, p.stdout, p.stderr.decode(errors="ignore")


def convert(src, dst, enc=DEFAULT_VP9, extra_vf=None):
    cmd = ["ffmpeg"]
    cmd += [src if a == "<INPUT>" else a for a in PUBLIC]
    if extra_vf:
        i = cmd.index("-an") + 1
        cmd = cmd[:i] + ["-vf", extra_vf] + cmd[i:]
    cmd += enc + [dst]
    rc, _, err = run(cmd)
    return rc, err


def pts_list(path, dec=None):
    """返回逐帧 pts_time 列表。不能加 -v error，否则 showinfo 输出会消失。"""
    cmd = ["ffmpeg", "-hide_banner", "-i" if dec is None else "-i"]
    cmd = ["ffmpeg", "-hide_banner"]
    if dec:
        cmd += dec
    cmd += ["-i", path, "-vf", "showinfo", "-f", "null", "NUL"]
    _, _, err = run(cmd)
    return [float(x) for x in re.findall(r"pts_time:\s*([0-9.]+)", err)]


def frame_size_count(path, dec=None):
    cmd = ["ffmpeg", "-hide_banner"]
    if dec:
        cmd += dec
    cmd += ["-i", path, "-vf", "showinfo", "-f", "null", "NUL"]
    _, _, err = run(cmd)
    m = re.search(r"Video:.*?,\s*(\d+)x(\d+)", err)
    size = (int(m.group(1)), int(m.group(2))) if m else None
    pts = [float(x) for x in re.findall(r"pts_time:\s*([0-9.]+)", err)]
    return size, len(pts)


def duration_of(path):
    _, _, err = run(["ffmpeg", "-hide_banner", "-i", path])
    m = re.search(r"Duration:\s*([0-9:.]+)", err)
    if not m:
        return None
    h, mm, ss = m.group(1).split(":")
    return int(h) * 3600 + int(mm) * 60 + float(ss)


def alpha_stats(path, dec=None):
    """§4.2 修正后的命令。返回 (采样帧数, 全帧最小 YMIN, 全帧最大 YMAX)。"""
    cmd = ["ffmpeg", "-hide_banner"]
    if dec:
        cmd += dec
    cmd += ["-i", path, "-vf", "alphaextract,format=gray,signalstats,metadata=print",
            "-f", "null", "NUL"]
    _, _, err = run(cmd)
    ymin = [int(x) for x in re.findall(r"lavfi\.signalstats\.YMIN=(\d+)", err)]
    ymax = [int(x) for x in re.findall(r"lavfi\.signalstats\.YMAX=(\d+)", err)]
    return len(ymin), (min(ymin) if ymin else None), (max(ymax) if ymax else None)


def alpha_first_frame_only(path, dec=None):
    """反例对照：只看第一帧会得到什么。"""
    cmd = ["ffmpeg", "-hide_banner"]
    if dec:
        cmd += dec
    cmd += ["-i", path, "-vf", "alphaextract,format=gray,signalstats,metadata=print",
            "-frames:v", "1", "-f", "null", "NUL"]
    _, _, err = run(cmd)
    v = re.findall(r"lavfi\.signalstats\.YMIN=(\d+)", err)
    return int(v[0]) if v else None


def decode_all(path, dec=None, pix="rgba"):
    cmd = ["ffmpeg", "-hide_banner", "-v", "error"]
    if dec:
        cmd += dec
    cmd += ["-i", path, "-map", "0:v:0", "-pix_fmt", pix, "-f", "rawvideo", "-"]
    rc, out, err = run(cmd)
    if rc != 0 or not out:
        raise RuntimeError("解码失败: %s\n%s" % (path, err[-300:]))
    return out


def to_frames(buf, w, h, pix="rgba"):
    ch = 4 if pix == "rgba" else 4
    a = np.frombuffer(buf, dtype=np.uint8).reshape(-1, h, w, ch)
    return a


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print("  [%s] %-46s %s" % ("PASS" if cond else "FAIL", name, detail))
    return cond


# =============================================================== 测试用例 ==


def t_vardur(fx, tmp):
    """变帧时长必须逐帧保留，不能被平均成 CFR。"""
    src = fx["vardur"]
    dst = os.path.join(tmp, "vardur.webm")
    rc, err = convert(src, dst)
    if rc != 0:
        return check("变帧时长 / VP9 转换", False, err[-160:])
    src_pts = pts_list(src)
    dst_pts = pts_list(dst, ["-c:v", "libvpx-vp9"])
    same = len(src_pts) == len(dst_pts) and all(
        abs(a - b) < 0.001 for a, b in zip(src_pts, dst_pts))
    dur = duration_of(dst)
    check("变帧时长 / 转换成功", True, "exit=0")
    check("变帧时长 / 帧数一致", len(src_pts) == len(dst_pts),
          "src=%d dst=%d" % (len(src_pts), len(dst_pts)))
    check("变帧时长 / PTS 逐帧一致（未被平均）", same,
          "src=%s dst=%s" % (src_pts, dst_pts))
    check("变帧时长 / 总时长正确", abs((dur or 0) - 0.39) < 0.01,
          "期望 0.39s 实际 %s" % dur)
    n, ymin, ymax = alpha_stats(dst, ["-c:v", "libvpx-vp9"])
    check("变帧时长 / alpha 全帧扫描通过", n > 0 and ymin is not None and ymin < 255,
          "采样%d帧 YMIN=%s" % (n, ymin))


def t_loop_infinite(fx, tmp):
    """无限循环 GIF：只输出一个周期。"""
    src = fx["loop_infinite"]
    dst = os.path.join(tmp, "loopinf.webm")
    rc, err = convert(src, dst)
    if rc != 0:
        return check("无限循环 / VP9 转换", False, err[-160:])
    nframes = len(pts_list(dst, ["-c:v", "libvpx-vp9"]))
    dur = duration_of(dst)
    check("无限循环 / 转换成功", True, "exit=0")
    check("无限循环 / 只出一个周期（4 帧）", nframes == 4, "实际 %d 帧" % nframes)
    check("无限循环 / 时长为一个周期 0.4s", dur is not None and abs(dur - 0.4) < 0.01,
          "实际 %s" % dur)
    # 对照组：gif demuxer 的 -ignore_loop 默认为 true，因此不带开关也只会读出一个周期；
    # 真正危险的是显式打开 -ignore_loop 0 —— 会得到无限次循环。
    ctrl = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", src, "-map", "0:v:0", "-c", "copy", "-f", "null", "NUL"],
        capture_output=True, timeout=60)
    m = re.findall(r"frame=\s*(\d+)", ctrl.stderr.decode(errors="ignore"))
    raw = int(m[-1]) if m else -1
    check("无限循环 / 对照：不带 -ignore_loop 也是一个周期（默认 true）", raw == 4,
          "不带开关读出 %d 帧" % raw)
    infinite = False
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-ignore_loop", "0", "-i", src,
             "-map", "0:v:0", "-c", "copy", "-f", "null", "NUL"],
            capture_output=True, timeout=8)
    except subprocess.TimeoutExpired:
        infinite = True
    check("无限循环 / 危险对照：-ignore_loop 0 会无限重复", infinite,
          "8s 内未结束 => 确实在无限循环（故本 skill 绝不可省略/取反该开关）")


def _fidelity(src, dst, dec, label, expect_semi=None):
    """把输出与 ffmpeg 自身对 GIF 的渲染逐像素比对，返回是否通过。"""
    size, n = frame_size_count(dst, dec)
    ref = decode_all(src)
    w, h = None, None
    m = re.search(r"Video:.*?,\s*(\d+)x(\d+)", run(["ffmpeg", "-hide_banner", "-i", src])[2])
    w, h = int(m.group(1)), int(m.group(2))
    ref_a = to_frames(ref, w, h)
    out_a = to_frames(decode_all(dst, dec), size[0], size[1])
    k = min(len(ref_a), len(out_a))
    ref_a, out_a = ref_a[:k].astype(np.int16), out_a[:k].astype(np.int16)
    # 只比可见像素差（有损编码）；alpha 单独看
    vis = ref_a[:, :, :, 3] > 0
    diff = np.abs(ref_a[:, :, :, :3] - out_a[:, :, :, :3]).mean()
    amask = (ref_a[:, :, :, 3] == 0)
    alpha_agree = ((out_a[:, :, :, 3] == 0) == amask).mean()
    # 阈值化比对：a>=128 视为不透明。这样 alpha=1 这类量化取整不算失配，
    # 只统计真正会被看成边缘羽化的中间值。
    trans_agree = ((out_a[:, :, :, 3] >= 128) == ~amask).mean()
    semi, mid = semi_breakdown(out_a[:, :, :, 3])
    total_px = out_a.shape[0] * out_a.shape[1] * out_a.shape[2]
    print("       帧数 src=%d dst=%d 可见RGB均差=%.2f alpha严格一致=%.2f%% 阈值化一致=%.2f%% "
          "半透明总量=%d 其中可见晕环=%d(%.3f%%)"
          % (len(ref_a), len(out_a), diff, alpha_agree * 100, trans_agree * 100,
             semi, mid, mid / total_px * 100))
    ok = True
    check("%s / 帧数与源一致" % label, len(ref_a) == len(out_a),
          "%d vs %d" % (len(ref_a), len(out_a)))
    ok &= check("%s / 尺寸未被缩放" % label, size == (w, h),
                "%s vs %sx%s" % (size, w, h))
    ok &= check("%s / 可见区 RGB 无剧烈失真" % label, diff < 8.0, "均差 %.2f" % diff)
    ok &= check("%s / alpha 阈值化一致（取整不算失配）" % label, trans_agree > 0.99,
                "%.4f%%" % (trans_agree * 100))
    ok &= check("%s / 无可见晕环（32<=alpha<=223 占比 <0.5%%）" % label,
                mid / total_px < 0.005, "%.3f%% (%d px)" % (mid / total_px * 100, mid))
    if expect_semi == "zero":
        ok &= check("%s / 无新增半透明" % label, semi == 0, "半透明 %d px" % semi)
    return ok


def t_disposal_partial(fx, tmp):
    for key, label in [("disposal2", "disposal 2"), ("disposal3", "disposal 3")]:
        dst = os.path.join(tmp, key + ".webm")
        rc, err = convert(fx[key], dst)
        if rc != 0:
            check("%s / VP9 转换" % label, False, err[-160:])
            continue
        check("%s / VP9 转换" % label, True, "exit=0")
        _fidelity(fx[key], dst, ["-c:v", "libvpx-vp9"], label)
        n, ymin, _ = alpha_stats(dst, ["-c:v", "libvpx-vp9"])
        check("%s / alpha 全帧扫描通过" % label, n > 0 and ymin is not None and ymin < 255,
              "采样%d帧 YMIN=%s" % (n, ymin))

    # 真正的局部帧（image descriptor 带偏移）
    src = fx["partial_real"]
    dst = os.path.join(tmp, "partial_real.webm")
    rc, err = convert(src, dst)
    if rc != 0:
        return check("局部帧 / VP9 转换", False, err[-160:])
    check("局部帧 / VP9 转换", True, "exit=0")
    _fidelity(src, dst, ["-c:v", "libvpx-vp9"], "局部帧")

    # 局部帧 + disposal 2/3：结果必须与 disposal=1 不同，才算真的被按模式处置
    base_rank = None
    for key, mode in [("partial_disp2", 2), ("partial_disp3", 3)]:
        p_src = fx[key]
        d_dst = os.path.join(tmp, key + ".webm")
        rc, err = convert(p_src, d_dst)
        if rc != 0:
            check("局部帧+disposal%d / VP9 转换" % mode, False, err[-160:])
            continue
        check("局部帧+disposal%d / VP9 转换" % mode, True, "exit=0")
        _fidelity(p_src, d_dst, ["-c:v", "libvpx-vp9"], "局部帧+disposal%d" % mode)
        # 与源（disposal=1）比较 rendering 是否不同
        ref_d1 = to_frames(decode_all(src), 120, 100).astype(np.int16)
        ref_dm = to_frames(decode_all(p_src), 120, 100).astype(np.int16)
        if len(ref_d1) == len(ref_dm):
            diff = np.abs(ref_d1[:, :, :, 3] - ref_dm[:, :, :, 3]).mean()
            check("局部帧+disposal%d / ffmpeg 确实按模式处置（与 disposal=1 不同）" % mode,
                  diff > 0.1, "与 disposal=1 的 alpha 平面均差 %.2f" % diff)
        n, ymin, _ = alpha_stats(d_dst, ["-c:v", "libvpx-vp9"])
        check("局部帧+disposal%d / alpha 全帧扫描通过" % mode, n > 0 and ymin < 255,
              "采样%d帧 YMIN=%s" % (n, ymin))


def t_first_opaque_frame(fx, tmp):
    """首帧完全不透明：只看首帧会得出"没有透明像素"的错误结论。"""
    src = fx["first_opaque"]
    dst = os.path.join(tmp, "first_opaque.webm")
    rc, err = convert(src, dst)
    if rc != 0:
        return check("首帧不透明 / VP9 转换", False, err[-160:])
    dec = ["-c:v", "libvpx-vp9"]
    first = alpha_first_frame_only(dst, dec)
    n, ymin, _ = alpha_stats(dst, dec)
    check("首帧不透明 / 转换成功", True, "exit=0")
    check("首帧不透明 / 全帧扫描能发现透明像素",
          n > 0 and ymin is not None and ymin < 255, "采样%d帧 YMIN=%s" % (n, ymin))
    check("首帧不透明 / 反例：只看首帧会误判",
          first is not None and first >= 255,
          "首帧 YMIN=%s（≥255 即判定为无透明，正是误判来源）" % first)


def t_edge_and_rgb(fx, tmp):
    """透明区底层 RGB 保持源 GIF 原色，且边缘透明像素不被染色。"""
    src = fx["binary"]
    dst = os.path.join(tmp, "binary.webm")
    rc, err = convert(src, dst)
    if rc != 0:
        return check("二值底RGB / VP9 转换", False, err[-160:])
    size, n = frame_size_count(dst, ["-c:v", "libvpx-vp9"])
    ref = to_frames(decode_all(src), 70, 70)
    out = to_frames(decode_all(dst, ["-c:v", "libvpx-vp9"]), size[0], size[1])
    m0 = ref[0][:, :, 3] == 0
    src_rgb = ref[0][:, :, :3][m0].mean(0)
    out_rgb = out[0][:, :, :3][m0].mean(0)
    vis = ref[0][:, :, 3] == 255
    check("二值底RGB / 转换成功", True, "exit=0")
    check("二值底RGB / 透明区保持源 GIF 白色（未被涂黑）", out_rgb.mean() > 200,
          "源=%s 输出=%s" % (tuple(src_rgb.round(1)), tuple(out_rgb.round(1))))
    vis_mean = out[0][:, :, :3][vis].mean(0)
    check("二值底RGB / 可见区 RGB 接近已知值 (66,74,71)",
          abs(vis_mean - np.array([66, 74, 71])).max() < 12,
          "输出=(%.1f, %.1f, %.1f)" % tuple(vis_mean))
    # CRF 30 的关键帧同样是有损的，因此并不能期待 alpha 严格保持二值；
    # 真正要卡的是「中间值晕环」，而不是 alpha=1 这种量化取整。
    semi, mid = semi_breakdown(out[0][:, :, 3])
    total_px = out[0].shape[0] * out[0].shape[1]
    check("二值底RGB / alpha 未见可见晕环（32<=alpha<=223 占比 <0.5%）",
          mid / total_px < 0.005,
          "中间值 %d px (%.3f%%)，轻微取整 %d px" % (mid, mid / total_px * 100, semi - mid))

    # 透明接触画布边缘
    src2 = fx["edge"]
    dst2 = os.path.join(tmp, "edge.webm")
    rc, err = convert(src2, dst2)
    if rc != 0:
        return check("边缘透明 / VP9 转换", False, err[-160:])
    size2, _ = frame_size_count(dst2, ["-c:v", "libvpx-vp9"])
    ref2 = to_frames(decode_all(src2), 80, 80)
    out2 = to_frames(decode_all(dst2, ["-c:v", "libvpx-vp9"]), size2[0], size2[1])
    border = np.zeros((80, 80), bool)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    trans_edge = border & (ref2[0][:, :, 3] == 0)
    agree = ((out2[0][:, :, 3] == 0) & trans_edge).sum() / max(1, trans_edge.sum())
    check("边缘透明 / 转换成功", True, "exit=0")
    check("边缘透明 / 贴边透明像素仍为透明", agree > 0.98,
          "%.2f%% 保持透明（共 %d 个贴边透明像素）" % (agree * 100, trans_edge.sum()))


def t_odd_sizes(fx, tmp):
    """奇数/非4倍数尺寸在 WebM 与 MP4 上的真实行为。"""
    for key, label in [("odd_999", "999x999"), ("odd_1000x999", "1000x999"),
                       ("odd_999x1000", "999x1000"), ("odd_998", "998x998")]:
        dst = os.path.join(tmp, "odd_%s.webm" % key)
        rc, err = convert(fx[key], dst)
        if rc != 0:
            msg = [l for l in err.splitlines() if "error" in l.lower() or "not divisible" in l]
            check("奇数尺寸 %s / VP9" % label, rc == 0, "失败: %s" % (msg[:1] or err[-120:]))
            continue
        size, n = frame_size_count(dst, ["-c:v", "libvpx-vp9"])
        alpha_n, ymin, _ = alpha_stats(dst, ["-c:v", "libvpx-vp9"])
        check("奇数尺寸 %s / VP9 转换" % label, True, "输出 %sx%s" % size)
        check("奇数尺寸 %s / 尺寸未被缩放" % label, size == tuple(map(int, label.split("x"))),
              "输出 %sx%s" % size)
        check("奇数尺寸 %s / alpha 保留" % label, alpha_n > 0 and ymin < 255, "YMIN=%s" % ymin)

    # MP4：按 §3.7 文档命令（含 format=yuv420p）时，奇数尺寸必须失败
    src = fx["odd_999"]
    dst = os.path.join(tmp, "odd999.mp4")
    if os.path.exists(dst):
        os.remove(dst)
    rc, err = convert(src, dst,
                      enc=["-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                           "-movflags", "+faststart"],
                      extra_vf="format=rgba,premultiply=inplace=1:planes=0x7,format=yuv420p")
    hit = re.search(r"(width|height) not divisible by 2", err)
    check("奇数尺寸 / MP4（文档命令）拒绝奇数尺寸", rc != 0, "exit=%d" % rc)
    check("奇数尺寸 / MP4 报错文案可识别", hit is not None,
          hit.group(0) if hit else err.strip().splitlines()[-1][:80])

    # 反例：省掉文档里的 format=yuv420p，ffmpeg 会自动协商成 yuv444p 并绕过偶数约束，
    # 产出 High 4:4:4 —— 多数播放器不支持。这条必须与「不得为了通过尺寸检查而省略 -vf」一起记住。
    dst2 = os.path.join(tmp, "odd999_nofilter.mp4")
    if os.path.exists(dst2):
        os.remove(dst2)
    rc2, err2 = convert(src, dst2, enc=["-c:v", "libx264", "-crf", "20", "-preset", "veryfast"])
    fmt = pix_fmt_of(dst2) if rc2 == 0 else "?"
    check("奇数尺寸 / 危险对照：省略 format=yuv420p 会静默产出 yuv444p",
          rc2 == 0 and fmt.startswith("yuv444"),
          "exit=%d pix_fmt=%s（High 4:4:4，播放兼容性差且不触发偶数校验）" % (rc2, fmt))


def pix_fmt_of(path):
    _, _, err = run(["ffmpeg", "-hide_banner", "-i", path])
    m = re.search(r",\s*(yuv[a-z0-9]+)", err)
    return m.group(1) if m else "?"


def semi_breakdown(alpha):
    """区分「轻微取整」与「可见晕环」。
    alpha=1~31 / 224~254 多半只是量化取整，肉眼不可见；
    真正会呈现为边缘羽化的是中间值，所以断言只卡后者。"""
    a = alpha.astype(np.int16) if hasattr(alpha, "astype") else np.asarray(alpha, np.int16)
    semi = int(((a > 0) & (a < 255)).sum())
    mid = int(((a >= 32) & (a <= 223)).sum())
    return semi, mid


def t_hap_sizes(fx, tmp):
    """HAP 尺寸约束：宽高是否必须各自为 4 的倍数？报错原文是什么？"""
    cases = [("hap_ok", "1000x1000", "应成功"), ("w_only", "1002x1000", "仅宽非4倍数"),
             ("h_only", "1000x1002", "仅高非4倍数"), ("odd_998", "998x998", "宽高均非4倍数"),
             ("odd_1002", "1002x1002", "宽高均非4倍数"), ("odd_999", "999x999", "奇数")]
    for key, label, note in cases:
        dst = os.path.join(tmp, "hap_%s.mov" % key)
        if os.path.exists(dst):
            os.remove(dst)
        rc, err = convert(fx[key], dst,
                          enc=["-c:v", "hap", "-format", "hap_alpha",
                               "-compressor", "snappy", "-pix_fmt", "rgba"])
        hit = re.search(r"Video size \d+x\d+ is not multiple of 4", err)
        if rc == 0:
            check("HAP %s（%s）" % (label, note), True,
                  "编码成功 pix_fmt=%s" % pix_fmt_of(dst))
        else:
            check("HAP %s（%s）" % (label, note), True,
                  "编码失败 -> %s" % (hit.group(0) if hit else err.strip().splitlines()[-1][:80]))
            results[-1] = (results[-1][0], "INFO", results[-1][2])
            print("         -> INFO：%s" % note)


def t_weird_path(fx, tmp):
    src = fx["weird_path"]
    dst = os.path.join(os.path.dirname(src), "输出 文件 [1].webm")
    if os.path.exists(dst):
        os.remove(dst)
    rc, err = convert(src, dst)
    if rc != 0:
        check("特殊路径 / VP9 转换", False, err[-200:])
        return
    check("特殊路径 / VP9 转换", True, "exit=0")
    n, ymin, _ = alpha_stats(dst, ["-c:v", "libvpx-vp9"])
    check("特殊路径 / alpha 保留", n > 0 and ymin < 255, "YMIN=%s" % ymin)
    _, cnt = frame_size_count(dst, ["-c:v", "libvpx-vp9"])
    check("特殊路径 / 帧数正确", cnt == 2, "%d 帧" % cnt)
    os.remove(dst)


def main():
    import tempfile
    tmp = os.path.join(HERE, "..", ".tmp-matrix")
    os.makedirs(tmp, exist_ok=True)
    fixture_dir = os.path.join(HERE, "..", "fixtures")
    if not os.path.isdir(fixture_dir) or not os.listdir(fixture_dir):
        build_fixtures()
    fx = {
        "small5": os.path.join(fixture_dir, "fx_01_small5_30ms.gif"),
        "vardur": os.path.join(fixture_dir, "fx_02_vardur.gif"),
        "loop_infinite": os.path.join(fixture_dir, "fx_03_loop_infinite.gif"),
        "disposal2": os.path.join(fixture_dir, "fx_04_disposal2.gif"),
        "disposal3": os.path.join(fixture_dir, "fx_04_disposal3.gif"),
        "partial_real": os.path.join(fixture_dir, "fx_05b_partial_real.gif"),
        "partial_disp2": os.path.join(fixture_dir, "fx_05c_partial_disp2.gif"),
        "partial_disp3": os.path.join(fixture_dir, "fx_05c_partial_disp3.gif"),
        "first_opaque": os.path.join(fixture_dir, "fx_06_first_opaque.gif"),
        "edge": os.path.join(fixture_dir, "fx_07_edge_touch.gif"),
        "binary": os.path.join(fixture_dir, "fx_08_binary_known_rgb.gif"),
        "odd_999": os.path.join(fixture_dir, "fx_09_odd_999x999.gif"),
        "odd_1000x999": os.path.join(fixture_dir, "fx_09_oddh_1000x999.gif"),
        "odd_999x1000": os.path.join(fixture_dir, "fx_09_oddw_999x1000.gif"),
        "odd_998": os.path.join(fixture_dir, "fx_09_even-not4_998x998.gif"),
        "odd_1002": os.path.join(fixture_dir, "fx_09_not4_1002x1002.gif"),
        "hap_ok": os.path.join(fixture_dir, "fx_09_hapok_1000x1000.gif"),
        "w_only": os.path.join(fixture_dir, "fx_09_w-only_1002x1000.gif"),
        "h_only": os.path.join(fixture_dir, "fx_09_h-only_1000x1002.gif"),
        "weird_path": os.path.join(fixture_dir, "测试目录 (带有 空格) [括号]", "输入 [1].gif"),
    }
    missing = [k for k, v in fx.items() if not os.path.exists(v)]
    if missing:
        print("缺少夹具，请先运行 tests/make_fixtures.py：", missing)
        return 1

    print("=" * 100)
    print("分组 1：变帧时长")
    t_vardur(fx, tmp)
    print("\n分组 2：无限循环")
    t_loop_infinite(fx, tmp)
    print("\n分组 3：disposal 2/3 与局部帧")
    t_disposal_partial(fx, tmp)
    print("\n分组 4：首帧不透明（验证「必须扫全帧」）")
    t_first_opaque_frame(fx, tmp)
    print("\n分组 5：透明区底层 RGB 与贴边透明")
    t_edge_and_rgb(fx, tmp)
    print("\n分组 6：奇数 / 非 4 倍数尺寸")
    t_odd_sizes(fx, tmp)
    print("\n分组 7：HAP 尺寸约束（用于校准文档断言）")
    t_hap_sizes(fx, tmp)
    print("\n分组 8：含空格 / 中文 / 括号 / 方括号的路径")
    t_weird_path(fx, tmp)

    print("\n" + "=" * 100)
    fails = [r for r in results if r[1] is False]
    infos = [r for r in results if r[1] == "INFO"]
    passes = [r for r in results if r[1] is True]
    print("通过 %d / 失败 %d / 待校准 %d" % (len(passes), len(fails), len(infos)))
    for n, _, d in fails:
        print("  FAIL: %s  %s" % (n, d))
    for n, _, d in infos:
        print("  INFO: %s  %s" % (n, d))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
