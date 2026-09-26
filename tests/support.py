"""Shared fixtures for the skill's tests."""

import shutil
import unittest
import sys
from pathlib import Path

from PIL import Image

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import tgv  # noqa: E402
from tgv import analysis, background, common, convert, encoding, sources, staging, timing, verification  # noqa: E402,F401


class _Facade:
    """Flat access to the package, so tests read like the CLI does."""

    _modules = (common, sources, analysis, background, staging, timing, encoding, convert, verification, tgv.cli)

    def __getattr__(self, name):
        for mod in self._modules:
            if hasattr(mod, name):
                return getattr(mod, name)
        raise AttributeError(name)


module = _Facade()

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
needs_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "需要 ffmpeg 和 ffprobe")


def make_rgba_frame(size=(8, 6), hidden=(0, 0, 0), offset=0):
    frame = Image.new("RGBA", size, (*hidden, 0))
    for y in range(1, size[1] - 1):
        for x in range(2 + offset, 5 + offset):
            frame.putpixel((x, y), (255, 50, 20, 255))
    return frame


def save_apng(path: Path, hidden_colours, durations=(40, 120)):
    frames = [make_rgba_frame(hidden=c, offset=i) for i, c in enumerate(hidden_colours)]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=list(durations), loop=0)


def save_palette_gif(path: Path, transparent_rgb, frames=2, duration=30):
    images = []
    for i in range(frames):
        im = Image.new("P", (16, 16))
        im.putpalette([*transparent_rgb, 0, 255, 0] + [0] * (254 * 3))
        for y in range(4, 12):
            for x in range(4 + i, 10 + i):
                im.putpixel((x, y), 1)
        images.append(im)
    images[0].save(
        path, save_all=True, append_images=images[1:], duration=duration,
        transparency=0, disposal=2, loop=0,
    )


