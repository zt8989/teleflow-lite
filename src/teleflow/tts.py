"""TTS / audio synthesis layer for the phone-report feature (ticket 02).

Turns report text into a pjsua2-playable 8 kHz mono WAV: strip Markdown so the
synthesizer reads only prose, render speech with edge-tts (mp3), then transcode
to wav with an *external* ffmpeg binary. ffmpeg is located via the configured
path or ``PATH`` and must exist; missing ffmpeg is a clear error, not a silent
failure. The layer is behind a ``TtsBackend`` protocol so the controller and RPC
can run headless against a ``FakeTtsBackend``.

This is a deliberate, scoped exception to TeleFlow's "no WAV I/O" red line: the
file produced here is a *synthesis of the report text*, never a recording of any
call. See the feature spec's red-line note.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Protocol, runtime_checkable

from teleflow.config import DEFAULT_CONFIG_PATH

DEFAULT_CACHE_DIR = DEFAULT_CONFIG_PATH.parent / "reports"

# Transcode params mirror the user's battle-tested FreeSWITCH pipeline:
# 8 kHz, mono, 16-bit PCM — exactly what pjsua2's wav player expects.
TRANSCODE_ARGS = ["-ar", "8000", "-ac", "1", "-c:a", "pcm_s16le"]


class TtsError(Exception):
    """Base class for synthesis/transcode failures."""


class FfmpegNotFound(TtsError):
    """ffmpeg could not be located (not configured and not on PATH)."""


class FfmpegError(TtsError):
    """ffmpeg ran but exited non-zero."""


# ---------------------------------------------------------------------------
# Markdown cleaning — ported from the user's notify_phone.py rules so TTS reads
# only the prose, never the syntax characters.
# ---------------------------------------------------------------------------
def clean_markdown(text: str) -> str:
    """Strip Markdown syntax so the synthesizer reads body text, not markers."""
    text = re.sub(r"`([^`]+)`", r"\1", text)  # inline code
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.S)  # bold **
    text = re.sub(r"__(.+?)__", r"\1", text, flags=re.S)  # bold __
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text, flags=re.S)  # italic *
    text = re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"\1", text, flags=re.S)  # italic _
    text = re.sub(r"~~(.+?)~~", r"\1", text, flags=re.S)  # strikethrough
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # links -> text
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)  # headings
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)  # unordered lists
    text = re.sub(r"^\s*\d+[.、)]\s+", "", text, flags=re.M)  # ordered lists
    text = re.sub(r"^>\s*", "", text, flags=re.M)  # blockquote
    text = re.sub(r"^\s*([-*_]\s*){3,}\s*$", "", text, flags=re.M)  # hr -> delete line
    text = re.sub(r"\|", "，", text)  # table pipes -> pause
    text = re.sub(r"\n{3,}", "\n\n", text)  # collapse blank lines
    return text.strip()


@runtime_checkable
class TtsBackend(Protocol):
    """Synthesis seam: text -> mp3, mp3 -> wav. Implemented for real by
    ``EdgeTtsBackend`` and for tests by ``FakeTtsBackend``."""

    def synthesize(self, text: str, voice: str) -> Path:
        """Render ``text`` with ``voice`` to an mp3 file; return its path."""
        ...

    def transcode(self, mp3_path: Path, wav_path: Path) -> Path:
        """Transcode ``mp3_path`` to ``wav_path`` (8k mono pcm_s16le); return wav."""
        ...


class EdgeTtsBackend:
    """Real backend: edge-tts for speech, external ffmpeg for transcode."""

    def __init__(self, ffmpeg_path: str = "", cache_dir: Path | None = None) -> None:
        self._ffmpeg_path = ffmpeg_path
        self._cache_dir = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _ffmpeg_bin(self) -> str:
        """Resolve the ffmpeg binary: configured path first, else PATH.

        Raises ``FfmpegNotFound`` when neither exists — never silently proceed.
        """
        candidate = self._ffmpeg_path or (shutil.which("ffmpeg") or "")
        if not candidate or not os.path.isfile(candidate):
            raise FfmpegNotFound(
                "ffmpeg not found (set ffmpeg_path in settings or install ffmpeg on PATH)"
            )
        return candidate

    def synthesize(self, text: str, voice: str) -> Path:  # pragma: no cover - needs network
        import edge_tts

        ts = time.strftime("%Y%m%d_%H%M%S")
        mp3 = self._cache_dir / f"report_{ts}.mp3"
        communicate = edge_tts.Communicate(text, voice)
        asyncio.run(communicate.save(str(mp3)))
        return mp3

    def transcode(self, mp3_path: Path, wav_path: Path) -> Path:
        bin_ = self._ffmpeg_bin()
        result = subprocess.run(
            [bin_, "-y", "-i", str(mp3_path), *TRANSCODE_ARGS, str(wav_path)],
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0 or not wav_path.exists():
            err = result.stderr.decode(errors="replace")[:200]
            raise FfmpegError(f"ffmpeg transcode failed: {err}")
        return wav_path


class FakeTtsBackend:
    """Headless stand-in: records calls, returns a canned wav path.

    Used by the controller/RPC integration tests so no network or ffmpeg is
    required. ``synthesize`` returns ``fake_mp3`` (a placeholder the controller
    passes to ``transcode``); ``transcode`` just returns ``wav_path``.
    """

    def __init__(self, fake_wav: Path | None = None) -> None:
        self._fake_wav = fake_wav or Path("/tmp/fake_report.wav")
        self.synthesized: list[tuple[str, str]] = []
        self.transcoded: list[tuple[Path, Path]] = []

    def synthesize(self, text: str, voice: str) -> Path:
        self.synthesized.append((text, voice))
        return self._fake_wav

    def transcode(self, mp3_path: Path, wav_path: Path) -> Path:
        self.transcoded.append((mp3_path, wav_path))
        return wav_path
