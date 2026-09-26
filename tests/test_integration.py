"""End-to-end tests: frame order, frame timing and frame count must survive
the encoder. These self-skip when ffmpeg/ffprobe are unavailable.
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import FFMPEG, FFPROBE, make_rgba_frame, module, needs_ffmpeg, save_apng, save_palette_gif


@needs_ffmpeg
class IntegrationTests(unittest.TestCase):
    """End-to-end: frame count and frame timing must survive the encoder."""

    def probe_frames(self, path: Path):
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "frame=pts_time", "-of", "json", str(path)],
            check=True, capture_output=True, text=True,
        )
        return [float(f["pts_time"]) for f in json.loads(out.stdout)["frames"]]

    def test_uniform_30ms_gif_keeps_30ms_timestamps(self):
        """The regression that motivated the timing rewrite.

        A 30 ms GIF used to come out at 40 ms per frame (25 fps), 33% slower,
        because both the concat demuxer and the encoder default to 1/25.
        """
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src.gif"
            save_palette_gif(source, (200, 30, 40), frames=5, duration=30)
            output = Path(tmp) / "out.webm"
            self.assertEqual(
                module.main([
                    "convert", str(source), str(output), "--keep-alpha",
                    "--codec", "vp9", "--crf", "40", "--cpu-used", "8",
                ]),
                0,
            )
            pts = self.probe_frames(output)
            self.assertEqual(len(pts), 5)
            for index, value in enumerate(pts):
                self.assertAlmostEqual(value, index * 0.03, places=4)

    def test_variable_timing_roundtrip_and_alpha_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src.png"
            save_apng(source, ((70, 80, 90), (70, 80, 90)), durations=(30, 100))
            output = Path(tmp) / "out.webm"
            self.assertEqual(
                module.main([
                    "convert", str(source), str(output), "--keep-alpha",
                    "--codec", "vp9", "--crf", "40", "--cpu-used", "8",
                ]),
                0,
            )
            # 30ms + 100ms expands onto a 10ms grid: 13 frames, exact duration.
            pts = self.probe_frames(output)
            self.assertEqual(len(pts), 13)
            self.assertAlmostEqual(pts[1], 0.01, places=4)
            self.assertAlmostEqual(pts[-1], 0.12, places=4)
            self.assertEqual(
                module.main(["verify", str(source), str(output), "--expect", "alpha"]), 0
            )

    def test_h264_opaque_frame_count_matches_source(self):
        """Guards the old '+1 sentinel' assumption about the MP4 muxer."""
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src.gif"
            save_palette_gif(source, (200, 30, 40), frames=5, duration=30)
            output = Path(tmp) / "out.mp4"
            self.assertEqual(
                module.main([
                    "convert", str(source), str(output), "--background", "#123456", "--codec", "h264",
                ]),
                0,
            )
            self.assertEqual(len(self.probe_frames(output)), 5)
            self.assertEqual(
                module.main(["verify", str(source), str(output), "--expect", "opaque"]), 0
            )

    def test_image_sequence_frame_order_survives_encoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            sequence = Path(tmp) / "frames"
            sequence.mkdir()
            for name, offset in (("f_10.png", 3), ("f_2.png", 0)):
                make_rgba_frame(size=(16, 16), offset=offset).save(sequence / name)
            output = Path(tmp) / "seq.mov"
            self.assertEqual(
                module.main([
                    "convert", str(sequence), str(output), "--keep-alpha", "--codec", "qtrle",
                    "--sequence-duration-ms", "50",
                ]),
                0,
            )
            raw = module.decode_frame_rgba(output, FFMPEG, "qtrle", 16, 16, 0)
            # f_2 (offset 0) must be first: pixel (2,2) opaque.
            self.assertEqual(raw[(2 * 16 + 2) * 4 + 3], 255)



if __name__ == "__main__":
    unittest.main()
