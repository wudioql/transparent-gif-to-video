#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生成 tests/README.md 中列出的全部回归夹具。

**仅用于开发/回归测试**，不属于 skill 运行时。
skill 运行时唯一依赖仍是系统 ffmpeg；本脚本与 Pillow 不参与运行期。

用法：
    python tests/make_fixtures.py [输出目录]

输出的 .gif 被 .gitignore 忽略，不入库；夹具可随时由本脚本重建。
夹具刻意满足「透明区隐藏 RGB 为白色」，便于回归时断言底色策略是否被意外改动。
"""

import os
import sys

from PIL import Image, ImageDraw

# --- GIF 结构工具（用于改写 delay / disposal，并读取结构做自检） -------------


def find_gces(data):
    """暴力扫描所有 Graphic Control Extension：返回 packed 字节的偏移列表。
    结构化解析容易在 local color table / sub-block 边界上漏帧，这里用
    0x21 0xF9 0x04 <4 字节> 0x00 的固定形态精确定位。"""
    offs, i = [], 0
    pat = b"\x21\xf9\x04"
    while True:
        k = data.find(pat, i)
        if k < 0:
            break
        if data[k + 7] == 0x00:  # block terminator 紧跟 4 字节数据
            offs.append(k + 3)  # packed 字节
            i = k + 3
        else:
            i = k + 1
    return offs


def find_image_descs(data):
    """返回每帧的 (left, top, w, h)。从各 GCE 之后寻找 0x2C 描述符。"""
    descs = []
    for g in find_gces(data):
        k = data.find(b"\x2c", g + 5)
        if k < 0 or k > len(data) - 10:
            continue
        descs.append(tuple(int.from_bytes(data[k + 1 + 2 * j:k + 3 + 2 * j], "little")
                           for j in range(4)))
    return descs


def _read_metadata(data):
    return find_gces(data), find_image_descs(data)


def patch_gce(data, delays_cs=None, disposals=None, index=None):
    """改写每帧的 delay（厘秒）/ disposal（0-7）。index 为 None 表示对所有帧生效。"""
    buf = bytearray(data)
    gces, _ = _read_metadata(bytes(data))
    for n, off in enumerate(gces):
        if index is not None and n != index:
            continue
        if delays_cs is not None:
            cs = delays_cs[n] if isinstance(delays_cs, (list, tuple)) else delays_cs
            buf[off + 1] = cs & 0xFF
            buf[off + 2] = (cs >> 8) & 0xFF
        if disposals is not None:
            disp = disposals[n] if isinstance(disposals, (list, tuple)) else disposals
            buf[off] = (buf[off] & ~0x1C) | ((disp & 0x07) << 2)
    return bytes(buf)


def has_netscape_loop(data):
    return b"NETSCAPE2.0" in data


def loop_count(data):
    """NETSCAPE2.0 块：0x21 0xFF 0x0B "NETSCAPE2.0" 0x03 0x01 <loop_lo> <loop_hi> 0x00"""
    i = data.find(b"NETSCAPE2.0")
    if i < 0:
        return None
    j = i + len(b"NETSCAPE2.0") + 2  # 跳过块长度 0x03 与子块 ID 0x01
    return int.from_bytes(data[j:j + 2], "little")


# --- 画布绘制：透明像素一律写成不透明白 (255,255,255,0) --------------------

TRANSPARENT = (255, 255, 255, 0)  # 隐藏 RGB 为白，便于断言底色未被改动


def _canvas(w, h):
    im = Image.new("RGBA", (w, h), TRANSPARENT)
    return im, ImageDraw.Draw(im)


def _save_gif(path, frames, loop=1, duration=100):
    frames[0].save(
        path, save_all=True, append_images=frames[1:], duration=duration,
        loop=loop, disposal=2, optimize=False,
    )


# --- 各夹具 ---------------------------------------------------------------


def fx_small5(out):
    """1. 5 帧透明 GIF，30ms/帧。"""
    frames = []
    for i in range(5):
        im, d = _canvas(64, 48)
        d.ellipse([4 + i * 8, 8, 28 + i * 8, 40], fill=(220, 40, 40, 255))
        frames.append(im)
    p = os.path.join(out, "fx_01_small5_30ms.gif")
    _save_gif(p, frames, loop=1, duration=30)
    return p


def fx_vardur(out):
    """2. 五种不同帧时长：10/30/50/100/200 ms。"""
    frames = []
    for i in range(5):
        im, d = _canvas(80, 60)
        d.rectangle([i * 12, i * 6, i * 12 + 30, i * 6 + 30], fill=(30, 90, 200, 255))
        frames.append(im)
    p = os.path.join(out, "fx_02_vardur.gif")
    _save_gif(p, frames, loop=1, duration=100)
    data = patch_gce(open(p, "rb").read(), delays_cs=[1, 3, 5, 10, 20])
    open(p, "wb").write(data)
    return p


def fx_loop_infinite(out):
    """4. 无限循环（NETSCAPE loop count = 0）。"""
    frames = []
    for i in range(4):
        im, d = _canvas(60, 60)
        d.polygon([(30, 6), (54, 54), (6, 54)], fill=(40 + i * 30, 200 - i * 20, 90, 255))
        frames.append(im)
    p = os.path.join(out, "fx_03_loop_infinite.gif")
    _save_gif(p, frames, loop=0, duration=100)
    return p


def fx_disposal(out, mode):
    """3a. disposal 2 / 3 —— 后续帧逐渐变透明，靠 disposal 合成。"""
    frames = []
    for i in range(6):
        im, d = _canvas(100, 80)
        # 每帧都画完整内容：disposal 主要影响解码器如何处置上一帧
        d.rectangle([10, 10, 90, 70], fill=(0, 0, 0, 0))
        d.rectangle([10 + i * 4, 10, 90 - i * 4, 70], fill=(255, 160, 0, 255))
        frames.append(im)
    p = os.path.join(out, "fx_04_disposal%d.gif" % mode)
    _save_gif(p, frames, loop=1, duration=120)
    data = patch_gce(open(p, "rb").read(), disposals=mode)
    open(p, "wb").write(data)
    return p


# --- 手写最小 GIF89a 编码器（局部帧夹具用） --------------------------------
#
# Pillow / ffmpeg 都只写全帧 GIF，无法产出「image descriptor 带偏移且小于画布」
# 的局部更新帧。这一段提供可控的 GIF 写出能力：任意 disposal、延迟、偏移与尺寸。
# 生成器自带校验：写出的文件必须与预期索引完全一致，否则视为夹具不可信。


def _lzw_encode(indices, min_code_size):
    """标准 GIF LZW：返回压缩后的字节流（不含 block size 与分块）。"""
    clear = 1 << min_code_size
    end = clear + 1
    code_size = min_code_size + 1
    table = {}
    next_code = end + 1

    bits = []

    def emit(code, size):
        for i in range(size):
            bits.append((code >> i) & 1)

    def reset():
        nonlocal table, next_code, code_size
        table = {(i,): i for i in range(clear)}
        next_code = end + 1
        code_size = min_code_size + 1

    reset()
    emit(clear, code_size)
    prev = None
    for px in indices:
        cand = (px,) if prev is None else prev + (px,)
        if cand in table:
            prev = cand
            continue
        emit(table[prev], code_size)
        table[cand] = next_code
        next_code += 1
        # 位宽必须在 next_code == 2^code_size + 1 时增。写成 2^code_size 会产出 ffmpeg
        # 无法正确还原的流（已用 ffmpeg 作为裁判实测确认，见 tests/run_matrix.py 分组 3）。
        if next_code == (1 << code_size) + 1 and code_size < 12:
            code_size += 1
        if next_code >= 4096:  # 表满：清表重来，保证任何尺寸都能编码
            emit(clear, code_size)
            reset()
        prev = (px,)
    if prev is not None:
        emit(table[prev], code_size)
    emit(end, code_size)

    # 位流打包成字节（LSB first）
    out = bytearray()
    for i in range(0, len(bits), 8):
        b = 0
        for j, bit in enumerate(bits[i:i + 8]):
            b |= bit << j
        out.append(b)
    return bytes(out)


def _to_subblocks(data):
    out = bytearray()
    for i in range(0, len(data), 255):
        chunk = data[i:i + 255]
        out.append(len(chunk))
        out += chunk
    out.append(0)
    return bytes(out)


def write_gif(path, w, h, palette, frames, loop=1, transparent_index=0):
    """frames: [(left, top, rows, delay_cs, disposal)]，rows 为索引二维数组。"""
    min_code_size = max(2, (len(palette) - 1).bit_length())
    gct_entries = 1 << max(1, min_code_size)          # 必须是 2 的幂
    pal = list(palette) + [(0, 0, 0)] * (gct_entries - len(palette))

    out = bytearray(b"GIF89a")
    out += w.to_bytes(2, "little") + h.to_bytes(2, "little")
    out += bytes([0x80 | ((min_code_size - 1) & 0x07) if min_code_size > 1 else 0xF0, 0, 0])
    for r, g, b in pal:
        out += bytes([r, g, b])

    # NETSCAPE 扩展（0 = 无限循环）
    if loop is not None:
        out += b"\x21\xff\x0bNETSCAPE2.0\x03\x01" + loop.to_bytes(2, "little") + b"\x00"

    for left, top, rows, delay_cs, disposal in frames:
        packed = ((disposal & 0x07) << 2) | 0x01  # transparent flag = 1
        out += b"\x21\xf9\x04" + bytes([packed,
                                       delay_cs & 0xFF, (delay_cs >> 8) & 0xFF,
                                       transparent_index, 0x00])
        fh, fw = len(rows), len(rows[0])
        out += b"\x2c" + left.to_bytes(2, "little") + top.to_bytes(2, "little")
        out += fw.to_bytes(2, "little") + fh.to_bytes(2, "little") + b"\x00"
        flat = [px for row in rows for px in row]
        out += bytes([min_code_size]) + _to_subblocks(_lzw_encode(flat, min_code_size))
    out += b"\x3b"
    open(path, "wb").write(bytes(out))


def _partial_frames(mode):
    """构造局部帧：第 1 帧全幅，后续帧只更新中央小区域。mode 为 disposal。"""
    W, H = 120, 100
    frames = []
    rows = [[0] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            rows[y][x] = 1 if (x // 10 + y // 10) % 2 == 0 else 0
    for x in range(W):
        rows[0][x] = 3
    frames.append((0, 0, [r[:] for r in rows], 10, mode))  # 首帧也要带 disposal：
    # GCE 里的 disposal 决定「显示完这一帧后如何处置」，真正影响下一帧合成的是当前帧的标志，
    # 只把它放在最后一帧是测不到任何东西的。
    for ox, oy in [(40, 30), (60, 45)]:
        frames.append((ox, oy, [[2] * 40 for _ in range(40)], 10, mode))
    return W, H, frames


def fx_partial_handcrafted(out):
    """3b. 真正的局部帧：第 2 帧起只更新中央小区域，disposal=1（保留前一帧合成）。
    若 ffmpeg 合成错误（未继承前一帧内容 / 未正确定位偏移），这里会被逐像素比对抓出。"""
    W, H = 120, 100
    pal = [(255, 255, 255), (30, 90, 200), (240, 160, 40), (20, 20, 20)]  # idx0 = 透明
    W, H, frames = _partial_frames(1)
    p = os.path.join(out, "fx_05b_partial_real.gif")
    write_gif(p, W, H, pal, frames, loop=1)
    validate_handcrafted(p, W, H, min_opaque=1000)
    return p, None, pal


def fx_partial_disposal(out, mode):
    """3c. 局部帧 + disposal 2/3。与 disposal=1 的结果理应不同，
    足以证明「disposal 真的被解码器按模式处置了」而不仅仅是能转成功。"""
    pal = [(255, 255, 255), (30, 90, 200), (240, 160, 40), (20, 20, 20)]
    W, H, frames = _partial_frames(mode)
    p = os.path.join(out, "fx_05c_partial_disp%d.gif" % mode)
    write_gif(p, W, H, pal, frames, loop=1)
    validate_handcrafted(p, W, H, min_opaque=1000)
    return p


def validate_handcrafted(path, w, h, min_opaque=1):
    """自检：手写编码器极易产出看似合法、实际全透明的坏流。
    任何由 write_gif 生成的夹具都必须先过这一关才能被回归使用。"""
    import subprocess
    import numpy as np
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-ignore_loop", "1", "-i", path,
         "-frames:v", "1", "-pix_fmt", "rgba", "-f", "rawvideo", "-"],
        capture_output=True)
    if len(p.stdout) != w * h * 4:
        raise AssertionError("%s 解码字节数异常：%d（期望 %d）" % (path, len(p.stdout), w * h * 4))
    a = np.frombuffer(p.stdout, dtype=np.uint8).reshape(h, w, 4)
    opaque = int((a[:, :, 3] > 0).sum())
    if opaque < min_opaque:
        raise AssertionError("%s 首帧不透明像素仅 %d 个 —— 编码器产出坏流，夹具不可信"
                             % (path, opaque))
    return opaque


TRAILER = b"\x3b"  # 供外部校验使用


def fx_partial(out):
    """3b. 局部帧 —— 只有中央小区域变化，用于验证 ffmpeg 局部更新合成。"""
    frames = []
    for i in range(6):
        im, d = _canvas(120, 100)
        d.rectangle([0, 0, 119, 99], outline=(0, 0, 0, 0))
        d.rectangle([40, 30 + i * 4, 70, 60 + i * 4], fill=(120, 40, 180, 255))
        frames.append(im)
    p = os.path.join(out, "fx_05_partial.gif")
    _save_gif(p, frames, loop=1, duration=100)
    return p


def fx_first_opaque(out):
    """5. 首帧完全不透明，后续帧含透明区。"""
    frames = []
    im, d = _canvas(90, 90)
    d.rectangle([0, 0, 89, 89], fill=(10, 120, 60, 255))
    frames.append(im)
    for i in range(3):
        im, d = _canvas(90, 90)
        d.rectangle([i * 20, i * 20, 89 - i * 20, 89 - i * 20], fill=(200, 30, 90, 255))
        frames.append(im)
    p = os.path.join(out, "fx_06_first_opaque.gif")
    _save_gif(p, frames, loop=1, duration=80)
    return p


def fx_edge(out):
    """6. 透明主体接触画布四条边。
    注意：Pillow 会丢弃完全相同的相邻帧，因此每帧内容必须有差异。"""
    frames = []
    for i in range(2):
        im, d = _canvas(80, 80)
        d.rectangle([0, 0, 40, 79], fill=(30, 30, 30, 255))
        d.rectangle([0, 0, 79, 10], fill=(240, 240, 240, 255))
        # 触右上角的可见像素，且每帧位置不同
        d.rectangle([70 + i, 70 - i * 2, 79, 79], fill=(90, 20, 160, 255))
        frames.append(im)
    p = os.path.join(out, "fx_07_edge_touch.gif")
    _save_gif(p, frames, loop=1, duration=100)
    return p


def fx_binary_known_rgb(out):
    """10. 二值 alpha：透明=(255,255,255,0)，可见=(66,74,71,255)，取值已知便于逐像素断言。"""
    frames = []
    for i in range(3):
        im, d = _canvas(70, 70)
        d.rectangle([10 + i, 10, 60, 60], fill=(66, 74, 71, 255))
        frames.append(im)
    p = os.path.join(out, "fx_08_binary_known_rgb.gif")
    _save_gif(p, frames, loop=1, duration=100)
    return p


def fx_odd(out, w, h, name):
    """8/9. 奇数尺寸 / 非 4 倍数尺寸。"""
    frames = []
    for i in range(2):
        im, d = _canvas(w, h)
        d.ellipse([10 + 2 * i, 10, max(11, w - 10), max(11, h - 10)], fill=(15, 105, 190, 255))
        frames.append(im)
    p = os.path.join(out, "fx_09_%s_%dx%d.gif" % (name, w, h))
    _save_gif(p, frames, loop=1, duration=100)
    return p


FX_DIR = "测试目录 (带有 空格) [括号]"


def fx_weird_path(out):
    """7. 含空格、中文、圆括号、方括号的路径。"""
    d = os.path.join(out, FX_DIR)
    os.makedirs(d, exist_ok=True)
    frames = []
    for i in range(2):
        im, dr = _canvas(64, 64)
        dr.rectangle([8 + i * 3, 8, 56, 56], fill=(255, 90, 0, 255))
        frames.append(im)
    p = os.path.join(d, "输入 [1].gif")
    _save_gif(p, frames, loop=1, duration=100)
    return p


def _ffmpeg_frames(path):
    """用 ffmpeg 作权威帧计数，交叉验证本脚本的解析是否正确。"""
    import subprocess
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-ignore_loop", "1", "-i", path,
         "-map", "0:v:0", "-c", "copy", "-f", "null", "NUL"],
        capture_output=True)
    txt = p.stderr.decode(errors="ignore")
    vals = [int(x) for x in __import__("re").findall(r"frame=\s*(\d+)", txt)]
    return vals[-1] if vals else None


def report(path):
    data = open(path, "rb").read()
    gces, images = _read_metadata(data)
    canvas = int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    partial = any(img[2:] != canvas or img[0] or img[1] for img in images)
    delays = [(data[o + 1] | (data[o + 2] << 8)) * 10 for o in gces]
    disposals = [(data[o] >> 2) & 0x07 for o in gces]
    n_ff = _ffmpeg_frames(path)
    ok = "OK" if n_ff == len(gces) else "!! 解析器与 ffmpeg 不一致"
    print("  %-32s %6d B  解析%2d帧/ffmpeg%s  %4dx%-4d 延迟(ms)=%s disposal=%s loop=%s 局部帧=%s  %s"
          % (os.path.basename(path), len(data), len(gces), n_ff,
             canvas[0], canvas[1], delays, disposals, loop_count(data),
             "是" if partial else "否", ok))
    return dict(frames=len(gces), ffmpeg_frames=n_ff, delays=delays,
                disposals=disposals, loop=loop_count(data), partial=partial,
                images=images, canvas=canvas)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures")
    os.makedirs(out, exist_ok=True)
    print("输出目录:", out)
    print()

    built = {}
    built["small5"] = fx_small5(out)
    built["vardur"] = fx_vardur(out)
    built["loop_infinite"] = fx_loop_infinite(out)
    built["disposal2"] = fx_disposal(out, 2)
    built["disposal3"] = fx_disposal(out, 3)
    built["partial"] = fx_partial(out)
    built["partial_real"] = fx_partial_handcrafted(out)[0]
    built["partial_disp2"] = fx_partial_disposal(out, 2)
    built["partial_disp3"] = fx_partial_disposal(out, 3)
    built["first_opaque"] = fx_first_opaque(out)
    built["edge"] = fx_edge(out)
    built["binary"] = fx_binary_known_rgb(out)
    built["odd_999"] = fx_odd(out, 999, 999, "odd")
    built["odd_1000x999"] = fx_odd(out, 1000, 999, "oddh")
    built["odd_999x1000"] = fx_odd(out, 999, 1000, "oddw")
    built["odd_998"] = fx_odd(out, 998, 998, "even-not4")
    built["odd_1002"] = fx_odd(out, 1002, 1002, "not4")
    built["hap_ok"] = fx_odd(out, 1000, 1000, "hapok")
    built["w_only"] = fx_odd(out, 1002, 1000, "w-only")   # 仅宽非 4 倍数
    built["h_only"] = fx_odd(out, 1000, 1002, "h-only")   # 仅高非 4 倍数
    built["weird_path"] = fx_weird_path(out)

    print("\n=== 夹具自检 ===")
    for k, p in built.items():
        report(p)
    return built


if __name__ == "__main__":
    main()
