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
import concurrent.futures
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from teleflow.config import DEFAULT_CONFIG_PATH

DEFAULT_CACHE_DIR = DEFAULT_CONFIG_PATH.parent / "reports"

# Transcode params mirror the user's battle-tested FreeSWITCH pipeline:
# 8 kHz, mono, 16-bit PCM — exactly what pjsua2's wav player expects.
TRANSCODE_ARGS = ["-ar", "8000", "-ac", "1", "-c:a", "pcm_s16le"]

# Per-attempt deadline for a single edge-tts synthesis call. An edge-tts request
# that exceeds this is treated as a timeout and retried (see EdgeTtsBackend).
EDGE_TTS_TIMEOUT_SECONDS = 30


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

    def synthesize_to_wav(self, text: str, voice: str, prefix: str = "ivr") -> Path:
        """Render ``text`` with ``voice`` straight to a playable wav (8k mono).

        ``prefix`` is a filename namespace (e.g. ``"ivr"`` / ``"report"``) used
        by caching backends; it never affects the cache *key*. The default
        implementation sequences ``synthesize`` + ``transcode``; a caching
        wrapper overrides it to reuse a previously rendered wav.
        """
        ...


def locate_ffmpeg(ffmpeg_path: str = "") -> str | None:
    """Resolve the ffmpeg binary: configured path first, else PATH.

    Returns ``None`` when neither resolves to an existing file — callers
    decide whether that is fatal (``EdgeTtsBackend``) or just log-worthy
    (the SIP service's startup readiness check).
    """
    candidate = ffmpeg_path or (shutil.which("ffmpeg") or "")
    if candidate and os.path.isfile(candidate):
        return candidate
    return None


class EdgeTtsBackend:
    """Real backend: edge-tts for speech, external ffmpeg for transcode.

    A single edge-tts synthesis is bounded by ``EDGE_TTS_TIMEOUT_SECONDS`` and
    retried up to ``retry_attempts`` times on transient failures (timeouts,
    network blips) before giving up — a flaky network should not abort the whole
    report/IVR prompt.
    """

    def __init__(
        self,
        ffmpeg_path: str = "",
        cache_dir: Path | None = None,
        retry_attempts: int = 3,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self._ffmpeg_path = ffmpeg_path
        self._cache_dir = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._retry_attempts = retry_attempts
        self.logger = logger

    def _ffmpeg_bin(self) -> str:
        """Resolve the ffmpeg binary via :func:`locate_ffmpeg`.

        Raises ``FfmpegNotFound`` when it can't be found — never silently proceed.
        """
        bin_ = locate_ffmpeg(self._ffmpeg_path)
        if bin_ is None:
            raise FfmpegNotFound(
                "ffmpeg not found (set ffmpeg_path in settings or install ffmpeg on PATH)"
            )
        return bin_

    def synthesize(self, text: str, voice: str) -> Path:
        """Render ``text`` with ``voice`` to an mp3, retrying transient edge-tts
        / network failures (incl. timeouts) up to ``retry_attempts`` times."""
        last_err: Exception | None = None
        for attempt in range(1, self._retry_attempts + 1):
            try:
                return self._synthesize_once(text, voice)
            except Exception as exc:  # noqa: BLE001 - transient network errors are retried
                last_err = exc
                if self.logger is not None:
                    self.logger(
                        f"[TTS] 合成失败(第{attempt}/{self._retry_attempts}次, 将重试): {exc}"
                    )
        raise TtsError(
            f"edge-tts 合成失败(已重试{self._retry_attempts}次): {last_err}"
        ) from last_err

    def _synthesize_once(self, text: str, voice: str) -> Path:  # pragma: no cover - needs network
        """One best-effort edge-tts synthesis, bounded by EDGE_TTS_TIMEOUT_SECONDS."""
        import edge_tts

        ts = time.strftime("%Y%m%d_%H%M%S")
        # Unique per render: the IVR flow synthesizes all prompts concurrently, so
        # two jobs in the same second must not share one report_*.mp3 — they would
        # clobber each other's file and both transcode the same audio into their
        # own keyed wav (two cache keys ending up with byte-identical audio).
        mp3 = self._cache_dir / f"report_{ts}_{uuid.uuid4().hex[:8]}.mp3"
        communicate = edge_tts.Communicate(text, voice)
        asyncio.run(
            asyncio.wait_for(communicate.save(str(mp3)), timeout=EDGE_TTS_TIMEOUT_SECONDS)
        )
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

    def synthesize_to_wav(self, text: str, voice: str, prefix: str = "ivr") -> Path:  # pragma: no cover - needs network
        """Sequence ``synthesize`` + ``transcode`` into one wav (intermediate mp3)."""
        mp3 = self.synthesize(text, voice)
        wav_path = mp3.with_suffix(".wav")
        return self.transcode(mp3, wav_path)


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

    def synthesize_to_wav(self, text: str, voice: str, prefix: str = "ivr") -> Path:
        # Record the synthesize call so caching tests can observe cache hits
        # (a hit never reaches this method on the caching wrapper's inner).
        self.synthesized.append((text, voice))
        return self._fake_wav


class CachingTtsBackend:
    """Wraps a ``TtsBackend`` and caches rendered wavs by (cleaned text + voice).

    IVR replays the same welcome/menu text on every inbound call, so re-synthesizing
    each time wastes edge-tts/ffmpeg cycles. The cache key is a short hash of
    ``clean_markdown(text)`` + ``voice``; a present wav is returned without touching
    the inner backend, and only a text/voice change (different hash) re-renders.
    """

    def __init__(
        self,
        inner: TtsBackend,
        cache_dir: Path | None = None,
        logger: Callable[[str], None] | None = None,
        cache_ttl_seconds: int = 604800,
    ) -> None:
        self._inner = inner
        self._cache_dir = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_ttl = cache_ttl_seconds
        self.logger = logger
        # Recorded for tests: (text, voice) tuples that actually reached the inner
        # backend (i.e. cache misses / changes).
        self.rendered: list[tuple[str, str]] = []

    @staticmethod
    def _cache_key(text: str, voice: str) -> str:
        import hashlib

        h = hashlib.sha256()
        h.update(clean_markdown(text).encode("utf-8"))
        h.update(b"\x00")
        h.update(voice.encode("utf-8"))
        return h.hexdigest()[:16]

    def synthesize_to_wav(self, text: str, voice: str, prefix: str = "ivr") -> Path:
        key = self._cache_key(text, voice)
        wav_path = self._cache_dir / f"{prefix}_{key}.wav"
        fresh = wav_path.exists() and (
            time.time() - wav_path.stat().st_mtime
        ) <= self._cache_ttl
        if fresh:
            # Cache hit (within TTL): no edge-tts / ffmpeg, return the wav.
            if self.logger is not None:
                self.logger(f"[TTS] 缓存命中: {prefix}_{key}.wav")
            return wav_path
        if self.logger is not None:
            self.logger(f"[TTS] 缓存未命中, 开始合成: {prefix}_{key}.wav")
        mp3 = self._inner.synthesize(clean_markdown(text), voice)
        wav = self._inner.transcode(mp3, wav_path)
        self.rendered.append((text, voice))
        if self.logger is not None:
            self.logger(f"[TTS] 合成完成: {wav_path}")
            self.logger(f"[FFMPEG] 转码完成: {wav_path}")
        return wav

    # Delegate the lower-level protocol methods so this wrapper satisfies
    # ``TtsBackend`` and the report flow (which calls synthesize/transcode
    # directly) keeps working unchanged.
    def synthesize(self, text: str, voice: str) -> Path:
        return self._inner.synthesize(text, voice)

    def transcode(self, mp3_path: Path, wav_path: Path) -> Path:
        return self._inner.transcode(mp3_path, wav_path)


# ---------------------------------------------------------------------------
# Conversion queue: render text -> wav off the call / event thread.
# ---------------------------------------------------------------------------
class ConversionQueue:
    """Background converter: submits ``(text, voice)`` jobs to a bounded worker
    pool and delivers each result via
    ``on_done(wav_path, error=..., order=...)``.

    Used in production. Results are marshalled onto the GUI thread via
    ``marshal`` (the app wires ``service._defer`` -> ``MainWindow.gui``) so the
    callback can safely touch Qt / pjsua2. ``order`` is an opaque tag the caller
    uses to keep playback ordering independent of completion order (see the IVR
    flow, which submits all prompts in parallel but plays them by ``order``).
    """

    def __init__(
        self,
        backend: TtsBackend,
        max_workers: int = 4,
        marshal: Callable[[Callable[[], None]], None] | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self._backend = backend
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="teleflow-tts"
        )
        self._marshal = marshal
        self.logger = logger

    def submit(
        self,
        text: str,
        voice: str,
        *,
        prefix: str = "ivr",
        order: object = None,
        on_done: Callable[..., None],
    ) -> None:
        def _run() -> None:
            try:
                wav = self._backend.synthesize_to_wav(text, voice, prefix=prefix)
            except Exception as exc:  # noqa: BLE001 - report, don't crash the pool
                result: Path | None = None
                err: Exception | None = exc
            else:
                result, err = wav, None

            def _deliver() -> None:
                on_done(result, error=err, order=order)

            if self._marshal is not None:
                self._marshal(_deliver)
            else:
                _deliver()

        self._executor.submit(_run)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)


class SyncConversionQueue:
    """Inline variant of :class:`ConversionQueue` that runs the conversion and
    delivers ``on_done`` synchronously (no threads). Used by headless tests so
    assertions about playback / placement stay deterministic, mirroring
    ``FakeSipBackend`` / ``FakeTtsBackend``.
    """

    def __init__(
        self,
        backend: TtsBackend,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self._backend = backend
        self.logger = logger

    def submit(
        self,
        text: str,
        voice: str,
        *,
        prefix: str = "ivr",
        order: object = None,
        on_done: Callable[..., None],
    ) -> None:
        try:
            wav = self._backend.synthesize_to_wav(text, voice, prefix=prefix)
        except Exception as exc:  # noqa: BLE001
            result: Path | None = None
            err: Exception | None = exc
        else:
            result, err = wav, None
        on_done(result, error=err, order=order)

    def shutdown(self) -> None:
        pass
