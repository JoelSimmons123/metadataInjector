from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_NVIDIA_DLL_HANDLES = []


def configure_nvidia_dlls() -> list[str]:
    """Expose NVIDIA CUDA/cuBLAS/cuDNN DLLs installed in the current venv."""
    if os.name != "nt":
        return []
    nvidia_root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    if not nvidia_root.exists():
        return []

    preferred = [
        nvidia_root / "cublas" / "bin",
        nvidia_root / "cudnn" / "bin",
        nvidia_root / "cuda_runtime" / "bin",
    ]
    folders: list[str] = []
    seen: set[str] = set()
    for candidate in preferred + sorted(nvidia_root.glob("*/bin")):
        try:
            candidate = candidate.resolve()
        except Exception:
            pass
        key = str(candidate).lower()
        if key in seen or not candidate.is_dir():
            continue
        seen.add(key)
        folders.append(str(candidate))

    if folders:
        os.environ["PATH"] = os.pathsep.join(folders + [os.environ.get("PATH", "")])
        if hasattr(os, "add_dll_directory"):
            for folder in folders:
                try:
                    _NVIDIA_DLL_HANDLES.append(os.add_dll_directory(folder))
                except OSError:
                    pass
    return folders


NVIDIA_DLL_DIRS = configure_nvidia_dlls()


def _creationflags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _run(cmd: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_creationflags(),
        env=env,
    )


@dataclass
class Word:
    start: float
    end: float
    text: str


def clean_word(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def phrase_groups(words: list[Word], max_words: int, max_chars: int) -> list[list[Word]]:
    groups: list[list[Word]] = []
    current: list[Word] = []
    for raw in words:
        text = clean_word(raw.text)
        if not text:
            continue
        word = Word(raw.start, max(raw.end, raw.start + 0.04), text)
        prospective = current + [word]
        chars = len(" ".join(item.text for item in prospective))
        if current and (len(prospective) > max_words or chars > max_chars):
            groups.append(current)
            current = [word]
        else:
            current = prospective
        if current and re.search(r"[.!?]$", current[-1].text) and len(current) >= 2:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:
        s += 1
        cs = 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(text: str) -> str:
    return (
        text.replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\n", r"\N")
    )


def make_ass(
    words: list[Word],
    width: int,
    height: int,
    out_path: Path,
    *,
    font: str = "Arial",
    words_per_caption: int = 4,
    max_chars: int = 30,
    font_scale: float = 0.050,
    bottom_margin_scale: float = 0.225,
    outline_scale: float = 0.0032,
    highlight: bool = True,
    uppercase: bool = False,
) -> None:
    groups = phrase_groups(words, max(1, words_per_caption), max(8, max_chars))
    font_size = max(24, round(height * font_scale))
    margin_v = max(30, round(height * bottom_margin_scale))
    outline = max(2, round(height * outline_scale))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Caption,{font},{font_size},&H00FFFFFF,&H0000FFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{outline},0,2,60,60,{margin_v},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    lines = [header]
    white = r"{\c&HFFFFFF&}"
    yellow = r"{\c&H00FFFF&}"

    for group in groups:
        texts = [item.text.upper() if uppercase else item.text for item in group]
        if not highlight:
            rendered = ass_escape(" ".join(texts))
            lines.append(
                f"Dialogue: 0,{ass_time(group[0].start)},{ass_time(group[-1].end)},Caption,,0,0,0,,{rendered}\n"
            )
            continue

        for index, word in enumerate(group):
            start = word.start
            end = group[index + 1].start if index + 1 < len(group) else group[-1].end
            if end <= start:
                end = max(word.end, start + 0.05)
            rendered_words = []
            for j, text in enumerate(texts):
                rendered_words.append((yellow if j == index else white) + ass_escape(text))
            lines.append(
                f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{' '.join(rendered_words)}\n"
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(lines), encoding="utf-8-sig")


def ffmpeg_filter_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    return value.replace(":", r"\:").replace("'", r"\'")


def filter_available(ffmpeg: str, filter_name: str, env: dict | None = None) -> bool:
    try:
        proc = _run([ffmpeg, "-hide_banner", "-filters"], env=env)
        if proc.returncode != 0:
            return False
        pattern = re.compile(rf"(^|\s){re.escape(filter_name)}(\s|$)", re.MULTILINE)
        return bool(pattern.search(proc.stdout or ""))
    except Exception:
        return False


def encoder_available(ffmpeg: str, encoder: str) -> bool:
    try:
        proc = _run([ffmpeg, "-hide_banner", "-encoders"])
        return proc.returncode == 0 and encoder in (proc.stdout or "")
    except Exception:
        return False


class Transcriber:
    def __init__(
        self,
        model_name: str = "distil-large-v3",
        device_pref: str = "Auto",
        language: str = "en",
        log: Callable[[str], None] | None = None,
    ):
        self.model_name = model_name
        self.device_pref = device_pref
        self.language = language.strip() or None
        self.log = log or (lambda _message: None)
        self.model = None
        self.device_used: str | None = None
        self._reported_device = False

    def _load(self) -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "Captioning dependencies are not installed. Close the app and run run.bat again."
            ) from exc

        if self.device_pref == "CPU":
            attempts = [("cpu", "int8")]
        elif self.device_pref == "CUDA":
            attempts = [("cuda", "float16")]
        else:
            attempts = [("cuda", "float16"), ("cpu", "int8")]

        last_error: Exception | None = None
        for device, compute_type in attempts:
            try:
                self.log(f"Loading {self.model_name} on {device.upper()} ({compute_type})...")
                self.model = WhisperModel(
                    self.model_name,
                    device=device,
                    compute_type=compute_type,
                )
                self.device_used = device
                return
            except Exception as exc:
                last_error = exc
                self.log(f"  {device.upper()} load failed: {exc}")
        raise RuntimeError(f"Could not load Whisper model: {last_error}")

    def _transcribe_once(self, path: Path) -> list[Word]:
        if self.model is None:
            self._load()
        segments, _info = self.model.transcribe(
            str(path),
            language=self.language,
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
            condition_on_previous_text=False,
        )
        words: list[Word] = []
        for segment in segments:
            if not segment.words:
                continue
            for word in segment.words:
                if word.start is None or word.end is None:
                    continue
                text = clean_word(word.word)
                if text:
                    words.append(Word(float(word.start), float(word.end), text))

        if not self._reported_device:
            self.log(f"  Transcription device active: {str(self.device_used).upper()}")
            self._reported_device = True
        return words

    def transcribe(self, path: Path) -> list[Word]:
        try:
            return self._transcribe_once(path)
        except Exception as exc:
            if self.device_pref == "Auto" and self.device_used == "cuda":
                self.log(f"  CUDA inference failed: {exc}")
                self.log("  Falling back to CPU automatically...")
                self.model = None
                self.device_pref = "CPU"
                self._reported_device = False
                return self._transcribe_once(path)
            raise


def burn_subtitles_high_quality(
    ffmpeg: str,
    input_path: Path,
    ass_path: Path,
    output_path: Path,
    log: Callable[[str], None] | None = None,
) -> None:
    """Fallback caption pass when Topaz FFmpeg lacks libass.

    The fallback deliberately over-allocates video quality and stream-copies the
    audio so captioning cannot introduce another AAC generation.
    """
    log = log or (lambda _message: None)
    vf = f"ass='{ffmpeg_filter_path(ass_path)}'"
    common = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(input_path),
        "-map", "0:v:0", "-map", "0:a?",
        "-vf", vf,
    ]

    if encoder_available(ffmpeg, "h264_nvenc"):
        cmd = common + [
            "-c:v", "h264_nvenc",
            "-profile:v", "high",
            "-preset", "p7",
            "-tune", "hq",
            "-rc", "vbr",
            "-cq", "10",
            "-b:v", "0",
            "-pix_fmt", "yuv420p",
            "-tag:v", "avc1",
            "-c:a", "copy",
            "-map_metadata", "0",
            "-metadata:s:v:0", "encoder=",
            "-movflags", "+faststart",
            str(output_path),
        ]
        proc = _run(cmd)
        if proc.returncode == 0:
            return
        log("    NVENC caption pass failed; retrying with libx264.")
        if proc.stderr.strip():
            log("    " + proc.stderr.strip().splitlines()[-1])

    cmd = common + [
        "-c:v", "libx264",
        "-profile:v", "high",
        "-preset", "slow",
        "-crf", "10",
        "-pix_fmt", "yuv420p",
        "-tag:v", "avc1",
        "-c:a", "copy",
        "-map_metadata", "0",
        "-metadata:s:v:0", "encoder=",
        "-movflags", "+faststart",
        str(output_path),
    ]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(
            "FFmpeg caption pass failed:\n" + (proc.stderr.strip() or "Unknown FFmpeg error")
        )
