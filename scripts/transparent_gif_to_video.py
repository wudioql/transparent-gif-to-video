#!/usr/bin/env python3
"""Convert transparent GIF/PNG/APNG assets to alpha or opaque video.

Thin entry point; the implementation lives in the `tgv` package next to this
file so each concern stays readable. The CLI contract is unchanged:

    python scripts/transparent_gif_to_video.py inspect|suggest-background|convert|verify ...

Requires Pillow >= 9.2 (getbbox(alpha_only)) and NumPy. `convert` requires
ffmpeg (>= 6.0 for -enc_time_base); `verify` also requires ffprobe.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tgv.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
