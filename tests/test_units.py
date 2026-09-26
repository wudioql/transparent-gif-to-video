"""Unit tests: no ffmpeg required.

Admission criterion (see tests/README.md): a test earns its place only if it
guards a defect that could plausibly happen *and* would not be visible by
reading the code. Constants asserting themselves, and a second copy of an
invariant already proven end to end, were removed rather than kept for
symmetry.
"""

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from fractions import Fraction
from pathlib import Path

from PIL import Image

from support import SCRIPTS, background, make_rgba_frame, module, save_apng, save_palette_gif, sources


class SourceReadingTests(unittest.TestCase):
    def test_directory_sequence_is_read_in_natural_order(self):
        """frame_10 must not sort before frame_2 (a broken regex once made it)."""
        with tempfile.TemporaryDirectory() as tmp:
            sequence = Path(tmp) / "frames"
            sequence.mkdir()
            for name, offset in (("frame_10.png", 3), ("frame_2.png", 0)):
                make_rgba_frame(offset=offset).save(sequence / name)
            self.assertEqual(
                [p.name for p in module.sequence_files(sequence)], ["frame_2.png", "frame_10.png"]
            )
            frames = list(module.iter_source_frames(sequence, sequence_duration_ms=50))
            # frame_2 draws at offset 0, so (2,2) is opaque there and clear in frame_10.
            self.assertEqual(frames[0][0].getpixel((2, 2))[3], 255)
            self.assertEqual(frames[1][0].getpixel((2, 2))[3], 0)

    def test_missing_durations_are_never_invented(self):
        with tempfile.TemporaryDirectory() as tmp:
            sequence = Path(tmp) / "frames"
            sequence.mkdir()
            make_rgba_frame().save(sequence / "a.png")
            still = Path(tmp) / "still.png"
            make_rgba_frame().save(still)
            for label, path in (("目录序列", sequence), ("单帧静图", still)):
                with self.subTest(label):
                    with self.assertRaises(module.SkillError):
                        module.inspect_source(path)
            self.assertEqual(module.inspect_source(sequence, 50)["timing"]["constant_fps"], 20.0)
            self.assertEqual(module.inspect_source(still, 250)["duration_seconds"], 0.25)

    def test_variable_apng_timing_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            save_apng(path, ((17, 34, 51), (17, 34, 51)))
            report = module.inspect_source(path)
            self.assertTrue(report["frame_duration_seconds"]["variable"])
            self.assertEqual(report["timing"]["source_mode"], "vfr")
            self.assertAlmostEqual(report["timing"]["average_fps"], 12.5)
            self.assertIsNone(report["timing"]["constant_fps"])


class EdgeColourTests(unittest.TestCase):
    def test_single_edge_colour_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "single.png"
            save_apng(path, ((17, 34, 51), (17, 34, 51)))
            report = module.inspect_source(path)
            self.assertEqual(report["transparent_edge"]["colour"], "#112233")
            self.assertIsNone(report["transparent_edge"]["suspicious_reason"])
            self.assertEqual(module.resolve_background("auto-edge", report), (17, 34, 51))

    def test_multi_colour_edge_is_rejected_with_deterministic_reporting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "multi.png"
            save_apng(path, ((17, 34, 51), (99, 88, 77)))
            report = module.inspect_source(path)
            self.assertEqual(report["transparent_edge"]["distinct_colours"], 2)
            self.assertEqual(
                report["transparent_edge"]["top_colours"],
                module.inspect_source(path)["transparent_edge"]["top_colours"],
            )
            with self.assertRaises(module.SkillError):
                module.resolve_background("auto-edge", report)

    def test_all_zero_edge_colour_is_suspicious_but_still_offered(self):
        """A unanimous #000000 edge is usually a decoder artefact, not intent."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "zeroed.png"
            save_apng(path, ((0, 0, 0), (0, 0, 0)))
            report = module.inspect_source(path)
            self.assertIsNotNone(report["transparent_edge"]["suspicious_reason"])
            with self.assertRaises(module.SkillError):
                module.resolve_background("auto-edge", report)
            self.assertEqual(
                module.resolve_background("auto-edge", report, allow_suspicious=True), (0, 0, 0)
            )
            self.assertEqual(module.resolve_background("#000000", report), (0, 0, 0))
            # Still a candidate a human may legitimately choose.
            proposal = module.build_background_proposal(report)
            self.assertFalse(proposal["auto_edge_usable"])
            self.assertIn("#000000", [o["colour"] for o in proposal["options"]])

    def test_edge_scan_can_be_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            save_apng(path, ((17, 34, 51), (17, 34, 51)))
            report = module.inspect_source(path, scan_edges=False)
            self.assertEqual(report["transparent_edge"], {"scanned": False})
            with self.assertRaises(AssertionError):
                module.resolve_background("auto-edge", report)


class TimingTests(unittest.TestCase):
    def test_plan_chooses_between_exact_grid_and_vfr_fallback(self):
        uniform = module.plan_timing([0.03] * 5)
        self.assertEqual((uniform.mode, uniform.framerate, uniform.output_frames), ("cfr", Fraction(100, 3), 5))

        mixed = module.plan_timing([0.03, 0.04, 0.1])
        self.assertEqual((mixed.mode, mixed.framerate), ("cfr", Fraction(100, 1)))  # gcd 10ms
        self.assertEqual((mixed.repeats, mixed.output_frames, mixed.total_ms), ([3, 4, 10], 17, 170))
        self.assertTrue(mixed.exact_duration)

        pathological = module.plan_timing([0.031, 0.1] * 30)
        self.assertEqual(pathological.mode, "vfr")
        self.assertFalse(pathological.exact_duration)

    def _staged(self, directory: Path, count: int = 3) -> list[Path]:
        frames = []
        for i in range(count):
            frame = directory / f"frame_{i:08d}.png"
            make_rgba_frame().save(frame)
            frames.append(frame)
        return frames

    def test_cfr_command_uses_image2_without_a_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_dir = Path(tmp)
            command = module.build_encode_command(
                "ffmpeg", module.plan_timing([0.03] * 3), self._staged(staging_dir), staging_dir,
                staging_dir / "o.webm", keep_alpha=True, codec="vp9", crf=30, preset="slow",
                cpu_used=2, lossless=False,
            )
            self.assertIn("100/3", command)
            self.assertIn("-enc_time_base", command)  # or 30ms silently becomes 40ms
            self.assertNotIn("-vf", command)
            self.assertEqual(len(list((staging_dir / "cfr").iterdir())), 3)

    def test_vfr_fallback_uses_a_constant_size_setpts(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_dir = Path(tmp)
            command = module.build_encode_command(
                "ffmpeg", module.TimingPlan("vfr", [30, 40, 100]), self._staged(staging_dir),
                staging_dir, staging_dir / "o.webm", keep_alpha=True, codec="vp9", crf=30,
                preset="slow", cpu_used=2, lossless=False,
            )
            self.assertIn("settb=1/1000,setpts=PTS/40", command)
            manifest = (staging_dir / "frames.ffconcat").read_text()
            # Durations inflated by 40 so the demuxer's 1/25 grid is lossless,
            # plus the repeated sentinel that makes the last duration count.
            self.assertIn("duration 1.200000", manifest)
            self.assertEqual(manifest.count("frame_00000002.png"), 2)


class PolicyTests(unittest.TestCase):
    def test_alpha_and_opaque_codec_sets_are_enforced(self):
        for codec, keep_alpha, lossless in (
            ("h264", True, False),        # H.264 has no alpha
            ("prores4444", False, False), # mastering codec in opaque mode
            ("vp8", True, True),          # lossless is VP9-only
        ):
            with self.subTest(codec=codec):
                with self.assertRaises(module.SkillError):
                    module.validate_codec_choice(codec, keep_alpha=keep_alpha, lossless=lossless)
        module.validate_codec_choice("vp9", keep_alpha=True, lossless=True)

    def test_codec_specific_options_are_reported_as_ignored(self):
        """Defaults are None so 'user asked' differs from 'not given'."""
        args = module.build_parser().parse_args(
            ["convert", "a.gif", "b.mov", "--keep-alpha", "--codec", "qtrle", "--crf", "20"]
        )
        self.assertIsNone(args.preset)
        module.reset_warnings()
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            module.warn_ignored_options(args, "qtrle")
        self.assertIn("--crf", buffer.getvalue())
        self.assertNotIn("--preset", buffer.getvalue())
        module.reset_warnings()

    def test_parse_rgb_separates_user_error_from_internal_error(self):
        with self.assertRaises(AssertionError):
            module.parse_rgb("auto-edge")
        self.assertEqual(module.parse_rgb("#0A0B0C"), (10, 11, 12))
        self.assertEqual(module.parse_rgb("white"), (255, 255, 255))
        with self.assertRaises(module.SkillError):
            module.parse_rgb("not-a-colour")


class StagingTests(unittest.TestCase):
    def test_opaque_staging_fills_transparent_pixels_in_one_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.png"
            save_apng(source, ((17, 34, 51), (17, 34, 51)))
            staging_dir = Path(tmp) / "staged"
            staging_dir.mkdir()
            stats = module.SourceStats(path=source, scan_edges=False)
            frames, durations = module.stage_frames(
                source, staging_dir, keep_alpha=False, background=(1, 2, 3), stats=stats
            )
            self.assertEqual(durations, [0.04, 0.12])
            self.assertEqual(stats.report()["frames"], 2)
            prepared = Image.open(frames[0]).convert("RGB")
            self.assertEqual(prepared.getpixel((0, 0)), (1, 2, 3))
            self.assertEqual(prepared.getpixel((3, 2)), (255, 50, 20))

    def test_concat_manifest_rejects_newlines_in_paths(self):
        with self.assertRaises(module.SkillError):
            module.check_path_safe_for_concat(Path("/tmp/bad\nname.png"))

    def test_bleed_touches_only_transparent_rgb_and_never_wraps(self):
        frame = make_rgba_frame(size=(16, 16), hidden=(0, 0, 0), offset=2)
        bled = module.bleed_transparent_rgb(frame, 2)
        before, after = frame.load(), bled.load()
        changed = 0
        for y in range(16):
            for x in range(16):
                self.assertEqual(before[x, y][3], after[x, y][3], "alpha 不得被改动")
                if before[x, y][3] == 255:
                    self.assertEqual(before[x, y], after[x, y], "可见像素不得被改动")
                elif before[x, y][:3] != after[x, y][:3]:
                    changed += 1
        self.assertGreater(changed, 0, "透明边缘应当被填上可见颜色")
        for colour in ((255, 255, 255), (0, 0, 0), (12, 34, 56)):
            self.assertEqual(
                list(module.composite_on_background(frame, colour).getdata()),
                list(module.composite_on_background(bled, colour).getdata()),
                "外扩后在任意背景上的合成结果必须逐像素不变",
            )
        corner = Image.new("RGBA", (6, 6), (0, 0, 0, 0))
        corner.putpixel((0, 0), (200, 10, 10, 255))
        self.assertEqual(module.bleed_transparent_rgb(corner, 1).getpixel((5, 5))[:3], (0, 0, 0))


class BackgroundConsultationTests(unittest.TestCase):
    def test_evidence_is_collected_and_ranked_without_ever_auto_picking(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.gif"
            save_palette_gif(path, (255, 0, 0))
            report = module.inspect_source(path)
            edge = report["transparent_edge"]
            trust = {c["source"]: c["trust"] for c in edge["background_candidates"]}
            self.assertIn("transparent_pixel_rgb", trust)
            self.assertIn("gif_palette_transparent_index", trust)
            self.assertEqual(trust["visible_rim_dominant"], "none")  # rim is not a background

            proposal = module.build_background_proposal(report)
            colours = [o["colour"] for o in proposal["options"]]
            self.assertEqual(colours[0], "#ff0000")  # asset evidence outranks convention
            self.assertIn("#ffffff", colours)
            origins = {o["colour"]: o["origin"] for o in proposal["options"]}
            self.assertEqual(origins["#ffffff"], "delivery-convention")
            self.assertNotIn("chosen", proposal)
            self.assertIn("ask", proposal["question"])

    def test_padding_only_sources_get_a_cropping_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pad.png"
            save_apng(path, ((10, 20, 30), (10, 20, 30)))
            report = module.inspect_source(path)
            self.assertFalse(report["content_bbox"]["covers_full_canvas"])
            self.assertIn("裁切", module.build_background_proposal(report)["question"]["context"])

    def test_ask_mode_without_a_tty_emits_a_proposal_and_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "a.png"
            save_apng(source, ((0, 0, 0), (0, 0, 0)))
            output = Path(tmp) / "o.mp4"
            self.assertEqual(
                module.main(["convert", str(source), str(output), "--background", "ask"]), 2
            )
            self.assertFalse(output.exists())


class HygieneTests(unittest.TestCase):
    def test_warnings_are_deduplicated_within_a_run(self):
        module.reset_warnings()
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            module.warn("同一条消息")
            module.warn("同一条消息")
            module.warn("另一条消息")
        self.assertEqual(buffer.getvalue().count("同一条消息"), 1)
        self.assertIn("另一条消息", buffer.getvalue())
        module.reset_warnings()

    def test_preview_does_not_materialise_every_frame(self):
        """Previewing one frame once held the whole animation (~380 MB) in memory."""
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "many.png"
            frames = [make_rgba_frame(size=(8, 6), offset=i % 3) for i in range(12)]
            frames[0].save(source, save_all=True, append_images=frames[1:], duration=40, loop=0)
            seen = []
            original = sources.iter_source_frames

            def counting(*args, **kwargs):
                for index, item in enumerate(original(*args, **kwargs)):
                    seen.append(index)
                    yield item

            background.iter_source_frames = counting
            try:
                frame = module.middle_frame(source)
            finally:
                background.iter_source_frames = original
            self.assertEqual(frame.size, (8, 6))
            self.assertLess(len(seen), 12 * 2)  # count pass + stop at the middle

    def test_missing_numpy_fails_loudly_with_an_install_hint(self):
        """NumPy 缺失曾让功能静默退化（证据消失、外扩变空操作），现在必须直接报错。"""
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "numpy.py").write_text("raise ImportError('simulated')", encoding="utf-8")
            env = dict(os.environ, PYTHONPATH=tmp)
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "transparent_gif_to_video.py"), "--help"],
                env=env, capture_output=True, text=True,
            )
        self.assertNotEqual(result.returncode, 0, "缺依赖不得静默继续")
        self.assertIn("pip install numpy", result.stdout + result.stderr, "报错要给出安装命令")

    def test_sample_indices_cover_first_middle_last(self):
        self.assertEqual(module.sample_indices(95, 3), [0, 47, 94])
        self.assertEqual(module.sample_indices(1, 3), [0])
        self.assertEqual(module.sample_indices(None, 3), [0])
        self.assertEqual(module.sample_indices(2, 5), [0, 1])


if __name__ == "__main__":
    unittest.main()
