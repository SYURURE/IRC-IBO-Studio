# SPDX-License-Identifier: GPL-3.0-only
# PNG script adaptation: Copyright (c) 2015 Gerald Knizia.
# Adapted from IboView example-scripts/save_images_00_frames_of_what_you_see.js.
# Studio changes: absolute directory, escaping, preflight checks and media pipeline.
# See LICENSE, licenses/IboView-COPYRIGHT.txt and THIRD_PARTY_NOTICES.md.
"""IboView frame-export script and safe PNG-to-video conversion.

This module never calculates or synthesizes orbitals. It only exports the actual
view in IboView and combines user-generated PNG frames. Run export_movie on a
worker thread; progress callbacks execute on that same worker thread.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
from collections import deque
from typing import Callable


class MediaError(RuntimeError):
    """A media export failed, with an actionable user-facing explanation."""


class ExportCancelled(MediaError):
    """The user cancelled an export; no completed output was published."""


def make_iboview_script(output_dir: str | Path) -> str:
    """Return an IboView script capturing every frame with the current view.

    The caller must create output_dir before the script is executed. The script
    uses the official save_images_00_frames_of_what_you_see.js API. IboView must
    already have the intended trajectory and orbitals loaded and displayed.
    """
    # JSON strings are valid JavaScript literals, including quote/backslash and
    # Unicode escaping. Forward slashes also work as Windows path separators.
    folder = Path(output_dir).expanduser().resolve().as_posix().rstrip("/") + "/"
    prefix = json.dumps(folder, ensure_ascii=True)
    return "\n".join([
        "// SPDX-License-Identifier: GPL-3.0-only",
        "// Copyright (c) 2015 Gerald Knizia; adapted for IRC IBO Studio.",
        "// Based on IboView example-scripts/save_images_00_frames_of_what_you_see.js",
        "// License: https://www.gnu.org/licenses/gpl-3.0.html",
        "// IRC IBO Studio: export the actual current IboView display.",
        "// Set the orbitals, colours, camera, and isovalue before running.",
        "// Existing ibo-frame-XXXX.png files in this directory are overwritten.",
        "// API: KoehnLab/iboview/example-scripts/",
        "//      save_images_00_frames_of_what_you_see.js",
        "view.save_alpha = false;",
        "view.crop_images = false;",
        f"var exportDirectory = {prefix};",
        "for (var iFrame = 0; iFrame < doc.num_frames(); ++iFrame) {",
        "    app.set_frame(iFrame);",
        "    var filename = exportDirectory +",
        '        format("ibo-frame-{0}.png", fmti("%04i", iFrame));',
        '    print("saving: " + filename);',
        "    view.save_png(filename);",
        "}",
        "",
    ])


def _natural_key(path: Path) -> tuple:
    parts = re.split(r"(\d+)", path.name.casefold())
    # Tagged values avoid mixed int/string comparisons for unusual filenames.
    return tuple((1, int(part)) if part.isdigit() else (0, part) for part in parts)


def list_frames(images_dir: str | Path) -> list[Path]:
    """List top-level PNG files in natural filename order, never recursively."""
    folder = Path(images_dir).expanduser()
    if not folder.is_dir():
        raise MediaError(f"PNGフォルダーが見つかりません: {folder}")
    frames = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".png"]
    frames.sort(key=_natural_key)
    if not frames:
        raise MediaError("PNG画像がありません。IboViewで画像を書き出したフォルダーを選んでください。")
    return frames


def locate_ffmpeg(configured: str = "") -> str:
    """Locate an explicit executable, system ffmpeg, or imageio's bundled one."""
    if configured.strip():
        candidate = Path(configured.strip().strip('"')).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
        resolved = shutil.which(configured.strip())
        if resolved:
            return resolved
        raise MediaError(f"指定したFFmpegが見つかりません: {configured}")
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg
        executable = imageio_ffmpeg.get_ffmpeg_exe()
        if executable and Path(executable).is_file():
            return executable
    except (ImportError, RuntimeError, OSError):
        pass
    raise MediaError(
        "FFmpegが見つかりません。セットアップで imageio-ffmpeg をインストールするか、"
        "FFmpeg実行ファイルの場所を指定してください。GIF出力にはFFmpegは不要です。"
    )


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ExportCancelled("動画出力をキャンセルしました。")


def _progress(callback: Callable[[str], None] | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _validate_images(frames: list[Path], cancel_event: threading.Event | None) -> tuple[int, int]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise MediaError("Pillowが必要です。アプリのセットアップを実行してください。") from exc
    size = None
    for path in frames:
        _check_cancel(cancel_event)
        try:
            with Image.open(path) as im:
                if im.format != "PNG":
                    raise MediaError(f"PNG形式ではないファイルです: {path.name}")
                current = im.size
                im.verify()
        except MediaError:
            raise
        except Exception as exc:
            raise MediaError(f"画像を読み込めません: {path.name}\n{exc}") from exc
        if size is None:
            size = current
        elif current != size:
            raise MediaError(
                f"画像のサイズが一致しません: {path.name} は {current[0]}×{current[1]}、"
                f"最初の画像は {size[0]}×{size[1]} です。"
                "IboViewのウィンドウサイズを固定し、crop_images=falseで再出力してください。"
            )
    assert size is not None
    return size


def _publish_without_overwrite(staged: Path, output: Path) -> None:
    """Atomically publish a completed file without replacing an existing one."""
    try:
        os.link(staged, output)
    except FileExistsError as exc:
        raise MediaError(f"同名ファイルが既に存在します。別名で保存してください: {output}") from exc
    except OSError as exc:
        if os.name == "nt":
            # Unlike POSIX rename(), Windows rename() never replaces a file.
            try:
                os.rename(staged, output)
                return
            except FileExistsError as exists:
                raise MediaError(f"同名ファイルが既に存在します: {output}") from exists
            except OSError as problem:
                raise MediaError(f"完成した動画を保存できません。保存先を確認してください: {problem}") from problem
        raise MediaError(
            "この保存場所では完成ファイルを安全に確定できません。"
            f"ローカルディスク上の別の保存先を試してください: {exc}"
        ) from exc


def _save_gif(sequence: list[Path], destination: Path, fps: float,
              progress: Callable[[str], None] | None,
              cancel_event: threading.Event | None) -> None:
    from PIL import Image

    palette_frames = []
    # GIF durations have a 10 ms time base, so report the real rounded interval.
    duration = max(10, round(1000 / fps / 10) * 10)
    try:
        for i, path in enumerate(sequence):
            _check_cancel(cancel_event)
            with Image.open(path) as im:
                rgba = im.convert("RGBA")
                rgb = Image.new("RGB", rgba.size, "white")
                rgb.paste(rgba, mask=rgba.getchannel("A"))
                palette_frames.append(rgb.quantize(colors=256))
                rgb.close()
                rgba.close()
            _progress(progress, f"GIF画像を準備中: {i + 1}/{len(sequence)}")
        _check_cancel(cancel_event)
        _progress(progress, f"GIFを書き出しています（1フレーム {duration} ms）…")
        # Pillow encoding is not interruptible mid-call. Cancellation is checked
        # immediately afterwards, before any final output is made visible.
        palette_frames[0].save(
            destination, format="GIF", save_all=True,
            append_images=palette_frames[1:], duration=duration,
            loop=0, optimize=False, disposal=2,
        )
        _check_cancel(cancel_event)
    finally:
        for frame in palette_frames:
            frame.close()


def _save_mp4(sequence: list[Path], destination: Path, fps: float,
              executable: str, progress: Callable[[str], None] | None,
              cancel_event: threading.Event | None) -> None:
    # Numbered links avoid concat-file quoting, Unicode, and embedded-newline
    # filename hazards. Copying is a portable fallback (e.g. across disks).
    staging = destination.parent / "frames"
    staging.mkdir()
    for index, source in enumerate(sequence):
        _check_cancel(cancel_event)
        target = staging / f"frame-{index:06d}.png"
        try:
            os.link(source, target)
        except OSError:
            shutil.copyfile(source, target)
    command = [
        executable, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
        "-framerate", str(fps), "-i", str(staging / "frame-%06d.png"),
        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2:color=white",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
        "-movflags", "+faststart", "-an", str(destination),
    ]
    _progress(progress, f"MP4を書き出しています（{len(sequence)}フレーム）…")
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, **options,
        )
    except OSError as exc:
        raise MediaError(f"FFmpegを起動できません。実行ファイルの指定を確認してください: {exc}") from exc
    # Drain the pipe concurrently but retain only its last 16 KiB.
    tail: deque[bytes] = deque(maxlen=16)

    def read_errors() -> None:
        assert process.stderr is not None
        while True:
            block = process.stderr.read(1024)
            if not block:
                break
            tail.append(block)

    reader = threading.Thread(target=read_errors, daemon=True)
    reader.start()
    try:
        while True:
            _check_cancel(cancel_event)
            try:
                result = process.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        reader.join(timeout=3)
        if process.stderr is not None:
            process.stderr.close()
    _check_cancel(cancel_event)
    if result != 0:
        details = b"".join(tail).decode("utf-8", errors="replace")[-8000:]
        raise MediaError(
            "FFmpegの動画変換に失敗しました。libx264対応のFFmpegと、保存先の空き容量を確認してください。"
            f"\n終了コード: {result}\n{details}"
        )


def export_movie(images_dir: str | Path, output_path: str | Path,
                 fps: float = 12, pingpong: bool = True,
                 ffmpeg_path: str = "", kind: str = "mp4",
                 progress: Callable[[str], None] | None = None,
                 cancel_event: threading.Event | None = None) -> Path:
    """Export naturally sorted PNG frames to GIF or H.264 MP4.

    All images must have equal dimensions. MP4 pads odd dimensions by at most
    one pixel on the right/bottom. PNG transparency becomes white in GIF.
    Ping-pong playback omits duplicate endpoints. GIF repeats forever; MP4 is
    one traversal (the player controls repetition). Existing outputs are never
    overwritten. Failure/cancellation leaves no final output or staging folder.
    """
    _check_cancel(cancel_event)
    kind = kind.lower().lstrip(".")
    if kind not in {"mp4", "gif"}:
        raise MediaError("出力形式は mp4 または gif を選んでください。")
    try:
        fps = float(fps)
    except (TypeError, ValueError) as exc:
        raise MediaError("フレームレートは0より大きい数値で指定してください。") from exc
    if not math.isfinite(fps) or not 0 < fps <= 120:
        raise MediaError("フレームレートは0より大きく120以下で指定してください。")
    output = Path(output_path).expanduser().absolute()
    if output.suffix.lower() != f".{kind}":
        raise MediaError(f"保存ファイル名の拡張子を .{kind} にしてください。")
    if output.exists() or output.is_symlink():
        raise MediaError(f"同名ファイルが既に存在します。別名で保存してください: {output}")
    if not output.parent.is_dir():
        raise MediaError(f"保存先フォルダーが見つかりません: {output.parent}")
    frames = list_frames(images_dir)
    _progress(progress, f"PNG画像を確認しています（{len(frames)}枚）…")
    _validate_images(frames, cancel_event)
    sequence = frames + frames[-2:0:-1] if pingpong and len(frames) > 2 else frames
    executable = locate_ffmpeg(ffmpeg_path) if kind == "mp4" else ""
    try:
        with tempfile.TemporaryDirectory(prefix=".irc-ibo-export-", dir=output.parent) as folder:
            staged = Path(folder) / f"movie.{kind}"
            if kind == "gif":
                _save_gif(sequence, staged, fps, progress, cancel_event)
            else:
                _save_mp4(sequence, staged, fps, executable, progress, cancel_event)
            _check_cancel(cancel_event)
            if not staged.is_file() or staged.stat().st_size == 0:
                raise MediaError("出力ファイルが生成されませんでした。入力画像と保存先を確認してください。")
            _publish_without_overwrite(staged, output)
    except (MediaError, ExportCancelled):
        raise
    except OSError as exc:
        raise MediaError(f"動画出力中にファイル操作が失敗しました。空き容量と権限を確認してください: {exc}") from exc
    _progress(progress, f"保存しました: {output}")
    return output
