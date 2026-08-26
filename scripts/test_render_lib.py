#!/usr/bin/env python3
"""Unit tests for render_lib (no ffmpeg / no bundle required)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from render_lib import (
    Slice,
    atempo_chain,
    audio_slice_filters,
    clean_path,
    find_system_wallpaper,
    fit_zoom,
    hide_cursor_enable,
    screen_frac_from_padding,
    output_duration,
    parse_ffmpeg_progress,
    parse_slices,
    resolve_audio_sources,
    slices_are_identity,
    source_to_output_time,
    video_timeline_filter,
)


class PathTests(unittest.TestCase):
    def test_strips_backslash_escapes(self):
        got = clean_path("/Users/me/Downloads/h30\\.screenstudio")
        self.assertTrue(got.endswith("h30.screenstudio"))
        self.assertNotIn("\\", got)

    def test_empty(self):
        self.assertEqual(clean_path(""), "")


class SliceTests(unittest.TestCase):
    def test_output_duration_with_ramps(self):
        sl = parse_slices([
            {"sourceStartMs": 0, "sourceEndMs": 40000, "timeScale": 1},
            {"sourceStartMs": 40000, "sourceEndMs": 43000, "timeScale": 0.125},
            {"sourceStartMs": 43000, "sourceEndMs": 50000, "timeScale": 1},
        ], 50)
        # timeScale 0.125 = 8× speed → 3s source becomes 0.375s
        self.assertAlmostEqual(output_duration(sl), 47.375, places=5)

    def test_source_to_output_time(self):
        sl = [
            Slice(0, 40, 1.0),
            Slice(40, 43, 0.125),
            Slice(43, 50, 1.0),
        ]
        self.assertAlmostEqual(source_to_output_time(20, sl), 20.0)
        self.assertAlmostEqual(source_to_output_time(41.5, sl), 40 + 1.5 * 0.125)
        self.assertAlmostEqual(source_to_output_time(46, sl), 40 + 0.375 + 3)

    def test_identity(self):
        sl = [Slice(0, 10, 1.0)]
        self.assertTrue(slices_are_identity(sl, 10))
        self.assertFalse(slices_are_identity([Slice(0, 10, 0.5)], 10))

    def test_timeline_filter_uses_setpts_not_split(self):
        sl = [Slice(0, 40, 1.0), Slice(40, 43, 0.125)]
        f = video_timeline_filter(sl, 60, 43, "c0", "vout")
        self.assertIn("setpts=", f)
        self.assertNotIn("split=", f)
        self.assertTrue(f.startswith("[c0]") and f.endswith("[vout]"))

    def test_timeline_identity_is_null(self):
        sl = [Slice(0, 10, 1.0)]
        self.assertEqual(video_timeline_filter(sl, 60, 10, "c0", "vout"), "[c0]null[vout]")


class AudioTests(unittest.TestCase):
    def test_atempo_chain_slow(self):
        self.assertEqual(atempo_chain(0.125), "atempo=0.5,atempo=0.5,atempo=0.5")

    def test_atempo_chain_fast(self):
        self.assertEqual(atempo_chain(2), "atempo=2")

    def test_atempo_chain_one(self):
        self.assertEqual(atempo_chain(1), "")

    def test_audio_filters_include_tempo(self):
        sl = [Slice(0, 2, 0.125, volume=0.5)]
        f = audio_slice_filters(sl, "")
        self.assertIn("atempo=8", f)
        self.assertIn("volume=0.5", f)
        self.assertIn("[out]", f)

    def test_resolve_silence_when_no_tracks(self):
        mode, voice, sysa, warns = resolve_audio_sources("/tmp", {}, "auto")
        self.assertEqual(mode, "silence")
        self.assertIsNone(voice)
        self.assertTrue(warns)

    def test_resolve_mic_plus_system_without_mic(self):
        rec = tempfile.mkdtemp()
        try:
            sysf = os.path.join(rec, "channel-1-system-audio-0.m4a")
            open(sysf, "wb").close()
            chans = {"channel-1-system-audio": {"type": "systemAudio"}}
            mode, voice, _sys, warns = resolve_audio_sources(rec, chans, "mic+system")
            self.assertEqual(mode, "system")
            self.assertEqual(voice, sysf)
            self.assertTrue(warns)
        finally:
            os.remove(sysf)
            os.rmdir(rec)


class ProgressTests(unittest.TestCase):
    def test_out_time_ms(self):
        self.assertAlmostEqual(parse_ffmpeg_progress("out_time_ms=1500000"), 1.5)

    def test_time_hms(self):
        self.assertAlmostEqual(parse_ffmpeg_progress("frame=10 time=00:01:05.50 bitrate=1"), 65.5)

    def test_hide_cursor_enable(self):
        sl = [Slice(0, 5, 1.0, hide_cursor=True), Slice(5, 10, 1.0)]
        expr = hide_cursor_enable(sl)
        self.assertIn("not(", expr)
        self.assertIn("between(t,0.000000,5.000000)", expr)


class FramingTests(unittest.TestCase):
    def test_padding_percent(self):
        self.assertAlmostEqual(screen_frac_from_padding(10), 0.80)

    def test_padding_fraction(self):
        self.assertAlmostEqual(screen_frac_from_padding(0.1), 0.80)

    def test_padding_none(self):
        self.assertAlmostEqual(screen_frac_from_padding(None), 0.80)

    def test_fit_zoom_keeps_project_when_clicks_are_close(self):
        self.assertAlmostEqual(fit_zoom(2, 20, 10, 2854, 1628, 0.25), 2.0)

    def test_fit_zoom_backs_off_when_group_is_wide(self):
        z = fit_zoom(2, 1943, 1135, 2854, 1628, 0.25)
        self.assertAlmostEqual(z, 1.0)

    def test_fit_zoom_never_exceeds_project(self):
        self.assertLessEqual(fit_zoom(1.5, 10, 10, 2854, 1628, 0.25), 1.5)


class WallpaperTests(unittest.TestCase):
    def test_extracts_tahoe_light_from_screen_studio(self):
        asar = "/Applications/Screen Studio.app/Contents/Resources/app.asar"
        if not os.path.isfile(asar):
            self.skipTest("Screen Studio.app not installed")
        path = find_system_wallpaper("macOS/tahoe-light.jpg")
        self.assertTrue(path and os.path.isfile(path), path)
        self.assertGreater(os.path.getsize(path), 50_000)


if __name__ == "__main__":
    unittest.main()
