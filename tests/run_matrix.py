#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
针对 tests/README.md 中列出的边界夹具跑完整回归 —— 新栈版（Phase 6）。

**仅用于开发/回归测试**，不属于运行时。执行层 = tgtv 包本身
（PyAV 18.1.0 + NumPy），**不依赖系统 ffmpeg CLI**。

与旧版（ffmpeg CLI 执行层，见 git 历史 phase5 之前的 tests/run_matrix.py）
的断言分组与口径一一对应；两处形态调整：

  - 「对照：不带 -ignore_loop 也是一个周期」→ av.open 默认 options 的行为；
  - 「危险对照：-ignore_loop 0 会无限重复」→ options={"ignore_loop": "0"}
    配帧数上限读到 >1 周期即证（不再依赖 8 秒超时挂起）。
  - HAP 尺寸组随 Phase 0 决策（HAP 移除，docs/python-only-refactor-analysis.md
    附录 A）整体下线；
  - 「MP4 省略 format=yuv420p 静默产出 yuv444p」的危险对照不再存在——
    新栈的预检在写入前拒绝奇数尺寸，该路径被结构性消灭。

用法（开发环境）：
    pip install -e ".[dev]"
    python tests/run_matrix.py

断言覆盖（沿用旧矩阵）：
  - 变帧时长逐帧保留（不重采样成 CFR）；
  - 无限循环 GIF 只输出一个周期；
  - disposal 2/3 与局部帧的合成结果与读取层自身渲染一致；
  - 奇数 / 非 4 倍数尺寸在 WebM 与 MP4 上的真实行为；
  - 含空格、中文、括号、方括号的路径；
  - 首帧不透明时「必须扫全帧」（刻意记录只看首帧会得到什么）；
  - 透明区底层 RGB 是否被意外改动。
等价性对照（新栈 vs imageio-ffmpeg 旧命令）另见 tests/cross_check_vs_ffmpeg.py。
"""

from __future__ import annotations

import os
import sys

import av
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import make_fixtures as mf  # noqa: E402

from tgtv import convert, verify as V  # noqa: E402
from tgtv.cli import main as tgtv_main  # noqa: E402
from tgtv.convert import ConvertError  # noqa: E402
from tgtv.source import GifSource  # noqa: E402

results = []


def check(name, cond, detail=""):
    cond = bool(cond)
    results.append((name, cond, detail))
    print("  [%s] %-56s %s" % ("PASS" if cond else "FAIL", name, detail))
    return cond


def info(name, detail=""):
    results.append((name, "INFO", detail))
    print("  [INFO] %-56s %s" % (name, detail))


def convert_to(src, dst, fmt="vp9", **kw):
    plan = convert.build_plan(src, dst, fmt, **kw)
    convert.execute(plan)
    return dst


def fold_verify(rep, label):
    """把 tgtv verify 的逐项检查倒进矩阵结果（带组名前缀）。"""
    ok = True
    for c in rep.checks:
        ok &= check("%s / %s" % (label, c.name), c.passed, c.detail)
    return ok


def count_frames(path, options=None, cap=64):
    """解码计数（带帧数上限：ignore_loop=0 的对照不允许挂起）。"""
    n = 0
    with av.open(str(path), options=options or {}) as c:
        for _ in c.decode(c.streams.video[0]):
            n += 1
            if n >= cap:
                break
    return n


def out_rgba(path):
    frames, _, _, _ = V._decode_output(path)
    return V._rgba(frames)


# =============================================================== 测试用例 ==


def t_vardur(fx, tmp):
    """变帧时长必须逐帧保留，不能被平均成 CFR。"""
    src, dst = fx["vardur"], os.path.join(tmp, "vardur.webm")
    try:
        convert_to(src, dst)
    except ConvertError as e:
        return check("变帧时长 / VP9 转换", False, str(e)[:160])
    check("变帧时长 / 转换成功", True, "exit=0")
    rep = V.verify(dst, src)
    check("变帧时长 / 帧数一致", rep.frames == 5, "src=5 dst=%d" % rep.frames)
    check("变帧时长 / PTS 逐帧一致（未被平均）",
          rep.pts_seconds == [0.0, 0.01, 0.04, 0.09, 0.19],
          "dst=%s" % rep.pts_seconds)
    check("变帧时长 / 总时长正确", abs(rep.duration_seconds - 0.39) < 0.01,
          "期望 0.39s 实际 %.3fs" % rep.duration_seconds)
    check("变帧时长 / alpha 全帧扫描通过", rep.stats["alpha_min_all_frames"] < 255,
          "全帧 alpha_min=%s" % rep.stats["alpha_min_all_frames"])


def t_loop_infinite(fx, tmp):
    """无限循环 GIF：只输出一个周期。"""
    src, dst = fx["loop_infinite"], os.path.join(tmp, "loopinf.webm")
    convert_to(src, dst)
    rep = V.verify(dst, src)
    check("无限循环 / 转换成功", True, "exit=0")
    check("无限循环 / 只出一个周期（4 帧）", rep.frames == 4, "实际 %d 帧" % rep.frames)
    check("无限循环 / 时长为一个周期 0.4s", abs(rep.duration_seconds - 0.4) < 0.01,
          "实际 %.3fs" % rep.duration_seconds)
    # 对照组：不显式传 ignore_loop（avformat gif demuxer 默认 true）也只读一个周期。
    raw = count_frames(src)
    check("无限循环 / 对照：不传 ignore_loop 也是一个周期（默认 true）", raw == 4,
          "默认 options 读出 %d 帧" % raw)
    # 危险对照：显式 ignore_loop=0 会无限重复——读到超过一个周期的帧数即证。
    rep_len = count_frames(src)
    looped = count_frames(src, options={"ignore_loop": "0"}, cap=rep_len + 8)
    check("无限循环 / 危险对照：ignore_loop=0 会无限重复", looped > rep_len,
          "上限 %d 帧读满 %d（>1 周期即在重复；故 GifSource 显式 ignore_loop=1 不可省）"
          % (rep_len + 8, looped))


def t_disposal_partial(fx, tmp):
    """disposal 2/3 与局部帧：合成结果必须与读取层自身渲染一致。"""
    for key, label in [("disposal2", "disposal 2"), ("disposal3", "disposal 3"),
                       ("partial_real", "局部帧")]:
        dst = os.path.join(tmp, key + ".webm")
        convert_to(fx[key], dst)
        check("%s / VP9 转换" % label, True, "exit=0")
        rep = V.verify(dst, fx[key])
        fold_verify(rep, label)

    # 局部帧 + disposal 2/3：结果必须与 disposal=1 不同，才算真的被按模式处置
    d1 = V._rgba(list(GifSource(fx["partial_real"]).iter_frames()))
    for key, mode in [("partial_disp2", 2), ("partial_disp3", 3)]:
        dst = os.path.join(tmp, key + ".webm")
        convert_to(fx[key], dst)
        check("局部帧+disposal%d / VP9 转换" % mode, True, "exit=0")
        rep = V.verify(dst, fx[key])
        fold_verify(rep, "局部帧+disposal%d" % mode)
        dm = V._rgba(list(GifSource(fx[key]).iter_frames()))
        k = min(len(d1), len(dm))
        diff = float(np.abs(d1[:k][..., 3].astype(int) - dm[:k][..., 3].astype(int)).mean())
        check("局部帧+disposal%d / 读取层确实按模式处置（与 disposal=1 不同）" % mode,
              diff > 0.1, "与 disposal=1 的 alpha 平面均差 %.2f" % diff)


def t_first_opaque_frame(fx, tmp):
    """首帧完全不透明：只看首帧会得出"没有透明像素"的错误结论。"""
    src, dst = fx["first_opaque"], os.path.join(tmp, "first_opaque.webm")
    convert_to(src, dst)
    rep = V.verify(dst, src)
    check("首帧不透明 / 转换成功", True, "exit=0")
    check("首帧不透明 / 全帧扫描能发现透明像素",
          rep.stats["alpha_min_all_frames"] < 255,
          "全帧 alpha_min=%s" % rep.stats["alpha_min_all_frames"])
    check("首帧不透明 / 反例：只看首帧会误判",
          rep.stats["alpha_min_first_frame"] >= 255,
          "首帧 alpha_min=%s（≥255 即判定为无透明，正是误判来源）"
          % rep.stats["alpha_min_first_frame"])


def t_edge_and_rgb(fx, tmp):
    """透明区底层 RGB 保持源 GIF 原色，且贴边透明像素不被染色。"""
    src, dst = fx["binary"], os.path.join(tmp, "binary.webm")
    convert_to(src, dst)
    rep = V.verify(dst, src)
    check("二值底RGB / 转换成功", rep.passed, "verify %d/%d 项通过"
          % (sum(c.passed for c in rep.checks), len(rep.checks)))
    check("二值底RGB / 透明区保持源 GIF 白色（未被涂黑）",
          rep.stats.get("transparent_rgb_mean", 255) > 200,
          "透明区均值 %.1f" % rep.stats.get("transparent_rgb_mean", -1))
    # 已知值断言：可见区 RGB ≈ (66, 74, 71)
    out = out_rgba(dst)
    ref = V._rgba(list(GifSource(src).iter_frames()))
    vis = ref[0][..., 3] == 255
    vis_mean = out[0][..., :3][vis].mean(0)
    check("二值底RGB / 可见区 RGB 接近已知值 (66,74,71)",
          abs(vis_mean - np.array([66, 74, 71])).max() < 12,
          "输出=(%.1f, %.1f, %.1f)" % tuple(vis_mean))
    # 贴边透明
    src2, dst2 = fx["edge"], os.path.join(tmp, "edge.webm")
    convert_to(src2, dst2)
    out2 = out_rgba(dst2)
    ref2 = V._rgba(list(GifSource(src2).iter_frames()))
    h, w = ref2.shape[1:3]
    border = np.zeros((h, w), bool)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    trans_edge = border & (ref2[0][..., 3] == 0)
    agree = ((out2[0][..., 3] == 0) & trans_edge).sum() / max(1, trans_edge.sum())
    check("边缘透明 / 贴边透明像素仍为透明", agree > 0.98,
          "%.2f%% 保持透明（共 %d 个贴边透明像素）" % (agree * 100, trans_edge.sum()))


def t_odd_sizes(fx, tmp):
    """奇数/非4倍数尺寸在 WebM 与 MP4 上的真实行为。"""
    for key, label in [("odd_999", "999x999"), ("odd_1000x999", "1000x999"),
                       ("odd_999x1000", "999x1000"), ("odd_998", "998x998")]:
        w, h = map(int, label.split("x"))
        dst = os.path.join(tmp, "odd_%s.webm" % key)
        try:
            convert_to(fx[key], dst)
        except ConvertError as e:
            check("奇数尺寸 %s / VP9" % label, False, str(e)[:120])
            continue
        rep = V.verify(dst)
        check("奇数尺寸 %s / VP9 转换" % label, True, "输出 %dx%d" % (rep.canvas[0], rep.canvas[1]))
        check("奇数尺寸 %s / 尺寸未被缩放" % label, tuple(rep.canvas) == (w, h),
              "输出 %dx%d" % (rep.canvas[0], rep.canvas[1]))
        check("奇数尺寸 %s / alpha 保留" % label,
              rep.stats["alpha_min_all_frames"] < 255,
              "全帧 alpha_min=%s" % rep.stats["alpha_min_all_frames"])

    # MP4：奇数尺寸必须在预检阶段拒绝且零产出（不再有静默 yuv444p 的危险路径）
    dst = os.path.join(tmp, "odd999.mp4")
    if os.path.exists(dst):
        os.remove(dst)
    try:
        convert_to(fx["odd_999"], dst, "mp4-black")
        rejected, msg = False, "（未拒绝！）"
    except ConvertError as e:
        rejected, msg = True, str(e)[:100]
    check("奇数尺寸 / MP4 预检拒绝奇数尺寸", rejected, msg)
    check("奇数尺寸 / MP4 拒绝后零产出", not os.path.exists(dst),
          "输出文件 %s" % ("不存在" if not os.path.exists(dst) else "被创建了！"))
    info("奇数尺寸 / 危险对照已结构性消灭",
         "旧栈省略 format=yuv420p 会静默产出 yuv444p（High 4:4:4）；"
         "新栈 build_plan 预检先行拒绝，不存在绕过路径")


def t_weird_path(fx, tmp):
    src = fx["weird_path"]
    dst = os.path.join(os.path.dirname(src), "输出 文件 [1].webm")
    if os.path.exists(dst):
        os.remove(dst)
    rc = tgtv_main(["convert", src, "-o", dst, "--yes"])
    check("特殊路径 / tgtv convert（CLI 端到端）", rc == 0, "exit=%d" % rc)
    rep = V.verify(dst, src)
    check("特殊路径 / alpha 保留", rep.stats["alpha_min_all_frames"] < 255,
          "全帧 alpha_min=%s" % rep.stats["alpha_min_all_frames"])
    check("特殊路径 / 帧数正确", rep.frames == 2, "%d 帧" % rep.frames)
    os.remove(dst)


def main():
    tmp = os.path.join(HERE, "..", ".tmp-matrix")
    fixtures = os.path.join(tmp, "fixtures")
    os.makedirs(fixtures, exist_ok=True)
    # 清掉上一轮的输出产物（覆盖保护是 convert 的默认语义，矩阵不绕过它）
    for pat in ("*.webm", "*.mp4", "*.mov", "*.mkv"):
        for p in __import__("glob").glob(os.path.join(tmp, pat)):
            os.remove(p)
    print("夹具目录:", fixtures)
    fx = {
        "vardur": mf.fx_vardur(fixtures),
        "loop_infinite": mf.fx_loop_infinite(fixtures),
        "disposal2": mf.fx_disposal(fixtures, 2),
        "disposal3": mf.fx_disposal(fixtures, 3),
        "partial_real": mf.fx_partial_handcrafted(fixtures)[0],
        "partial_disp2": mf.fx_partial_disposal(fixtures, 2),
        "partial_disp3": mf.fx_partial_disposal(fixtures, 3),
        "first_opaque": mf.fx_first_opaque(fixtures),
        "edge": mf.fx_edge(fixtures),
        "binary": mf.fx_binary_known_rgb(fixtures),
        "odd_999": mf.fx_odd(fixtures, 999, 999, "odd"),
        "odd_1000x999": mf.fx_odd(fixtures, 1000, 999, "oddh"),
        "odd_999x1000": mf.fx_odd(fixtures, 999, 1000, "oddw"),
        "odd_998": mf.fx_odd(fixtures, 998, 998, "even-not4"),
        "weird_path": mf.fx_weird_path(fixtures),
    }
    missing = [k for k, v in fx.items() if not os.path.exists(v)]
    if missing:
        print("夹具构建失败：", missing)
        return 1

    print("=" * 108)
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
    info("分组 7：HAP 尺寸约束", "随 Phase 0 决策（HAP 移除）下线，见 docs/python-only-refactor-analysis.md 附录 A")
    print("\n分组 8：含空格 / 中文 / 括号 / 方括号的路径")
    t_weird_path(fx, tmp)

    print("\n" + "=" * 108)
    fails = [r for r in results if r[1] is False]
    infos = [r for r in results if r[1] == "INFO"]
    passes = [r for r in results if r[1] is True]
    print("通过 %d / 失败 %d / 说明 %d" % (len(passes), len(fails), len(infos)))
    for n, _, d in fails:
        print("  FAIL: %s  %s" % (n, d))
    for n, _, d in infos:
        print("  INFO: %s  %s" % (n, d))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
