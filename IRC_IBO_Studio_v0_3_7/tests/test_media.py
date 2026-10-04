"""Media tests use simple coloured squares, never fabricated orbital data."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image

from irc_ibo_studio.media import (
    ExportCancelled, MediaError, export_movie, list_frames,
    locate_ffmpeg, make_iboview_script,
)


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="irc media 日本語 ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.frames = self.root / "入力 PNG"
        self.frames.mkdir()

    def fixture(self, name, color, size=(25, 17)):
        path = self.frames / name
        with Image.new("RGB", size, color) as image:
            image.save(path)
        return path

    def make_three_frames(self):
        # Intentionally create out of playback order.
        self.fixture("ibo-frame-10.png", (0, 0, 255))
        self.fixture("ibo-frame-2.png", (0, 255, 0))
        self.fixture("ibo-frame-1.png", (255, 0, 0))

    def test_script_uses_official_callable_and_escaped_absolute_path(self):
        folder = self.root / '日本語 "quote" space'
        script = make_iboview_script(folder)
        self.assertIn("doc.num_frames()", script)
        self.assertIn("app.set_frame(iFrame)", script)
        self.assertIn("view.save_png(filename)", script)
        self.assertIn("view.save_alpha = false", script)
        prefix = script.split("var exportDirectory = ", 1)[1].split(";\n", 1)[0]
        self.assertEqual(json.loads(prefix), folder.resolve().as_posix() + "/")

    def test_frames_sorted_naturally_png_only_not_recursive(self):
        self.make_three_frames()
        (self.frames / "notes.txt").write_text("ignored")
        (self.frames / "subdir").mkdir()
        self.assertEqual([p.name for p in list_frames(self.frames)], [
            "ibo-frame-1.png", "ibo-frame-2.png", "ibo-frame-10.png",
        ])

    def test_empty_and_missing_directories_raise_helpful_error(self):
        with self.assertRaisesRegex(MediaError, "PNG画像がありません"):
            list_frames(self.frames)
        with self.assertRaisesRegex(MediaError, "見つかりません"):
            list_frames(self.root / "missing")

    def test_gif_pingpong_frame_order_size_and_timing(self):
        self.make_three_frames()
        output = self.root / "結合 変化.gif"
        messages = []
        self.assertEqual(export_movie(self.frames, output, kind="gif", fps=10,
                                      progress=messages.append), output)
        with Image.open(output) as gif:
            self.assertEqual(gif.n_frames, 4)
            self.assertEqual(gif.size, (25, 17))
            self.assertEqual(gif.info["loop"], 0)
            self.assertEqual(gif.info["duration"], 100)
            colors = []
            for index in range(gif.n_frames):
                gif.seek(index)
                colors.append(gif.convert("RGB").getpixel((0, 0)))
        self.assertEqual(colors, [(255, 0, 0), (0, 255, 0), (0, 0, 255), (0, 255, 0)])
        self.assertTrue(messages[-1].startswith("保存しました"))
        self.assertFalse(list(self.root.glob(".irc-ibo-export-*")))

    def test_one_frame_gif_supported(self):
        self.fixture("ibo-frame-0000.png", "red")
        output = export_movie(self.frames, self.root / "single.gif", kind="gif")
        with Image.open(output) as gif:
            self.assertEqual(gif.n_frames, 1)

    def test_mismatched_sizes_are_rejected_and_no_partial_output(self):
        self.fixture("1.png", "red", (20, 20))
        self.fixture("2.png", "blue", (25, 20))
        output = self.root / "mismatch.gif"
        with self.assertRaisesRegex(MediaError, "サイズが一致しません"):
            export_movie(self.frames, output, kind="gif")
        self.assertFalse(output.exists())

    def test_corrupted_png_is_rejected(self):
        (self.frames / "broken.png").write_text("not a PNG")
        with self.assertRaisesRegex(MediaError, "読み込めません"):
            export_movie(self.frames, self.root / "bad.gif", kind="gif")

    def test_output_is_never_overwritten(self):
        self.make_three_frames()
        output = self.root / "existing.gif"
        output.write_bytes(b"keep me")
        with self.assertRaisesRegex(MediaError, "既に存在"):
            export_movie(self.frames, output, kind="gif")
        self.assertEqual(output.read_bytes(), b"keep me")

    def test_output_race_does_not_overwrite_other_file(self):
        self.make_three_frames()
        output = self.root / "race.gif"

        def create_competing_file(message):
            if message.startswith("GIFを書き出しています"):
                output.write_bytes(b"other application's result")

        with self.assertRaisesRegex(MediaError, "既に存在"):
            export_movie(self.frames, output, kind="gif", progress=create_competing_file)
        self.assertEqual(output.read_bytes(), b"other application's result")
        self.assertFalse(list(self.root.glob(".irc-ibo-export-*")))

    def test_cancel_before_start(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(ExportCancelled):
            export_movie(self.frames, self.root / "cancel.gif", kind="gif", cancel_event=event)

    def test_cancel_during_gif_preparation_leaves_no_output(self):
        self.make_three_frames()
        event = threading.Event()
        output = self.root / "cancel.gif"

        def cancel_after_one_frame(message):
            if "GIF画像を準備中: 1/" in message:
                event.set()

        with self.assertRaises(ExportCancelled):
            export_movie(self.frames, output, kind="gif", progress=cancel_after_one_frame,
                         cancel_event=event)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob(".irc-ibo-export-*")))

    def test_invalid_fps_and_extension_fail_before_export(self):
        self.make_three_frames()
        for fps in (0, -3, float("nan"), float("inf"), 121, "bad"):
            with self.subTest(fps=fps), self.assertRaises(MediaError):
                export_movie(self.frames, self.root / "bad.gif", fps=fps, kind="gif")
        with self.assertRaisesRegex(MediaError, "拡張子"):
            export_movie(self.frames, self.root / "bad.png", kind="gif")

    def test_invalid_configured_ffmpeg_does_not_silently_fall_back(self):
        with self.assertRaisesRegex(MediaError, "指定したFFmpeg"):
            locate_ffmpeg(str(self.root / "not ffmpeg.exe"))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "System ffmpeg + ffprobe are required for real MP4 test")
    def test_real_mp4_unicode_spaces_odd_size_padding_frame_count(self):
        self.make_three_frames()
        output = export_movie(self.frames, self.root / "実際の video.mp4", fps=10)
        info = json.loads(subprocess.check_output([
            shutil.which("ffprobe"), "-v", "error", "-select_streams", "v:0",
            "-count_frames", "-show_entries", "stream=codec_name,pix_fmt,width,height,nb_read_frames,r_frame_rate",
            "-of", "json", str(output),
        ]))
        stream = info["streams"][0]
        self.assertEqual(stream["codec_name"], "h264")
        self.assertEqual(stream["pix_fmt"], "yuv420p")
        self.assertEqual((stream["width"], stream["height"]), (26, 18))
        self.assertEqual(int(stream["nb_read_frames"]), 4)
        self.assertEqual(stream["r_frame_rate"], "10/1")
        self.assertFalse(list(self.root.glob(".irc-ibo-export-*")))

    @unittest.skipUnless(shutil.which("ffmpeg"), "System ffmpeg required")
    def test_mp4_cancel_on_encoder_start_terminates_and_cleans(self):
        self.make_three_frames()
        output = self.root / "cancel.mp4"
        event = threading.Event()

        def stop_when_encoder_starts(message):
            if message.startswith("MP4を書き出しています"):
                event.set()

        with self.assertRaises(ExportCancelled):
            export_movie(self.frames, output, progress=stop_when_encoder_starts,
                         cancel_event=event)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob(".irc-ibo-export-*")))


if __name__ == "__main__":
    unittest.main()
