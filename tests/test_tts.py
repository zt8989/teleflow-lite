"""TDD tests for the TTS / synthesis layer (ticket 02).

Covers the Markdown cleaner, ffmpeg discovery (config path vs PATH vs missing),
the transcode failure path, and the fake backend. The real edge-tts synthesis
requires network and is intentionally not asserted in CI (native/network-only).
"""

import shutil
import sys
import time
from pathlib import Path

import pytest

from teleflow.tts import (
    FfmpegError,
    FfmpegNotFound,
    CachingTtsBackend,
    ConversionQueue,
    EdgeTtsBackend,
    FakeTtsBackend,
    SyncConversionQueue,
    TtsError,
    clean_markdown,
    locate_ffmpeg,
)


def test_clean_markdown_strips_common_syntax() -> None:
    src = "**粗体** 和 __粗体2__ 与 `代码` 与 ~~删除~~ 与 [链接](http://x) 与 *斜体*"
    out = clean_markdown(src)
    assert "**" not in out
    assert "__" not in out
    assert "`" not in out
    assert "~~" not in out
    assert "http://x" not in out
    assert "链接" in out
    assert "粗体" in out
    assert "斜体" in out


def test_clean_markdown_handles_structure_and_tables() -> None:
    src = (
        "# 标题\n"
        "- 列表项\n"
        "1、 有序项\n"
        "> 引用\n"
        "| 列1 | 列2 |\n"
        "***\n"
        "正文"
    )
    out = clean_markdown(src)
    assert "#" not in out
    assert "- 列表项" not in out
    assert "1、有序项" not in out
    assert ">" not in out
    assert "|" not in out  # table pipes become pauses
    assert "***" not in out
    assert "标题" in out
    assert "正文" in out


def test_edge_tts_backend_finds_ffmpeg_on_path(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: sys.executable)
    backend = EdgeTtsBackend(ffmpeg_path="", cache_dir=Path("/tmp/teleflow-tts-test"))
    assert backend._ffmpeg_bin() == sys.executable


def test_edge_tts_backend_uses_configured_ffmpeg_path(tmp_path: Path) -> None:
    backend = EdgeTtsBackend(ffmpeg_path=str(tmp_path / "ffmpeg"), cache_dir=tmp_path)
    # Path doesn't exist yet -> FfmpegNotFound until the file is present.
    with pytest.raises(FfmpegNotFound):
        backend._ffmpeg_bin()
    (tmp_path / "ffmpeg").write_text("")
    assert backend._ffmpeg_bin() == str(tmp_path / "ffmpeg")


def test_edge_tts_backend_raises_when_ffmpeg_missing(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "")
    backend = EdgeTtsBackend(ffmpeg_path="", cache_dir=Path("/tmp/teleflow-tts-test"))
    with pytest.raises(FfmpegNotFound):
        backend._ffmpeg_bin()


def test_locate_ffmpeg_returns_configured_existing_path(tmp_path: Path) -> None:
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_text("")
    assert locate_ffmpeg(str(ffmpeg)) == str(ffmpeg)


def test_locate_ffmpeg_configured_missing_path_wins_over_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A configured-but-missing path is reported as not found even when a
    # ffmpeg exists on PATH: the user asked for that specific binary.
    monkeypatch.setattr(shutil, "which", lambda _name: sys.executable)
    assert locate_ffmpeg(str(tmp_path / "nope")) is None


def test_locate_ffmpeg_falls_back_to_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: sys.executable)
    assert locate_ffmpeg("") == sys.executable


def test_locate_ffmpeg_treats_whitespace_path_as_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A whitespace-only ffmpeg_path means "not configured": fall back to PATH
    # instead of silently skipping the lookup (matches _log_ffmpeg_readiness's
    # reason message, which claims PATH was searched).
    monkeypatch.setattr(shutil, "which", lambda _name: sys.executable)
    assert locate_ffmpeg("   ") == sys.executable


def test_locate_ffmpeg_returns_none_when_nothing_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "")
    assert locate_ffmpeg("") is None


def test_edge_tts_backend_transcode_failure_raises(tmp_path: Path) -> None:
    # A present-but-failing binary stands in for ffmpeg exiting non-zero: the
    # Python interpreter rejects ffmpeg's flags and exits non-zero on any OS.
    backend = EdgeTtsBackend(ffmpeg_path=sys.executable, cache_dir=tmp_path)
    mp3 = tmp_path / "in.mp3"
    mp3.write_bytes(b"fake")
    wav = tmp_path / "out.wav"
    with pytest.raises(FfmpegError):
        backend.transcode(mp3, wav)


def test_fake_tts_backend_records_calls(tmp_path: Path) -> None:
    fake_wav = tmp_path / "fake.wav"
    backend = FakeTtsBackend(fake_wav=fake_wav)
    mp3 = backend.synthesize("hello", "zh-CN-XiaoxiaoNeural")
    wav = backend.transcode(mp3, tmp_path / "out.wav")
    assert backend.synthesized == [("hello", "zh-CN-XiaoxiaoNeural")]
    assert backend.transcoded == [(mp3, tmp_path / "out.wav")]
    assert wav == tmp_path / "out.wav"


class _FileFakeTts(FakeTtsBackend):
    """Fake TTS whose transcode materializes the wav file (like ffmpeg), so the
    cache layer's file-existence check behaves as it will in production."""

    def transcode(self, mp3_path: Path, wav_path: Path) -> Path:
        Path(wav_path).write_bytes(b"x")
        return wav_path


def test_caching_backend_reuses_wav_on_identical_text(tmp_path: Path) -> None:
    inner = _FileFakeTts(fake_wav=tmp_path / "inner.wav")
    cache = CachingTtsBackend(inner, cache_dir=tmp_path / "cache")
    first = cache.synthesize_to_wav("你好", "zh-CN-XiaoxiaoNeural")
    rendered = len(inner.synthesized)
    second = cache.synthesize_to_wav("你好", "zh-CN-XiaoxiaoNeural")
    # Cache hit: inner backend not re-rendered, same wav returned.
    assert len(inner.synthesized) == rendered
    assert first == second
    assert first.name.startswith("ivr_")


def test_caching_backend_rerenders_on_text_change(tmp_path: Path) -> None:
    inner = _FileFakeTts(fake_wav=tmp_path / "inner.wav")
    cache = CachingTtsBackend(inner, cache_dir=tmp_path / "cache")
    cache.synthesize_to_wav("你好", "v")
    rendered = len(inner.synthesized)
    cache.synthesize_to_wav("再见", "v")
    assert len(inner.synthesized) == rendered + 1


def test_caching_backend_renders_per_voice(tmp_path: Path) -> None:
    inner = _FileFakeTts(fake_wav=tmp_path / "inner.wav")
    cache = CachingTtsBackend(inner, cache_dir=tmp_path / "cache")
    cache.synthesize_to_wav("你好", "voiceA")
    rendered = len(inner.synthesized)
    cache.synthesize_to_wav("你好", "voiceB")
    assert len(inner.synthesized) == rendered + 1


def test_caching_backend_logs_hit_and_miss(tmp_path: Path) -> None:
    inner = _FileFakeTts(fake_wav=tmp_path / "inner.wav")
    logged: list[str] = []
    cache = CachingTtsBackend(inner, cache_dir=tmp_path / "cache", logger=logged.append)
    cache.synthesize_to_wav("你好", "v")  # miss -> 合成
    cache.synthesize_to_wav("你好", "v")  # hit  -> 复用
    assert any("缓存未命中" in m for m in logged)
    assert any("缓存命中" in m for m in logged)


def test_cache_key_is_text_plus_voice_hash(tmp_path: Path) -> None:
    # Key = sha256(clean_markdown(text) + "\0" + voice)[:16]; the same text under
    # a different voice must NOT collide, and 16 hex chars is the expected length.
    a = CachingTtsBackend._cache_key("你好", "voiceA")
    b = CachingTtsBackend._cache_key("你好", "voiceB")
    same = CachingTtsBackend._cache_key("你好", "voiceA")
    md = CachingTtsBackend._cache_key("**你好**", "voiceA")  # markdown stripped
    assert len(a) == 16
    assert all(c in "0123456789abcdef" for c in a)
    assert a != b
    assert a == same
    assert md == a  # clean_markdown normalizes the bold markers away


def test_caching_backend_ttl_reuses_fresh_entry(tmp_path: Path) -> None:
    inner = _FileFakeTts(fake_wav=tmp_path / "inner.wav")
    cache = CachingTtsBackend(inner, cache_dir=tmp_path / "cache", cache_ttl_seconds=604800)
    first = cache.synthesize_to_wav("你好", "v")
    rendered = len(inner.synthesized)
    second = cache.synthesize_to_wav("你好", "v")
    # Fresh wav (mtime just written) is within TTL -> reused, no second render.
    assert first == second
    assert len(inner.synthesized) == rendered


def test_caching_backend_ttl_expires_stale_entry(tmp_path: Path) -> None:
    import os
    import time

    inner = _FileFakeTts(fake_wav=tmp_path / "inner.wav")
    cache = CachingTtsBackend(inner, cache_dir=tmp_path / "cache", cache_ttl_seconds=60)
    first = cache.synthesize_to_wav("你好", "v")
    # Age the cached wav beyond the TTL (mtime in the past) to simulate staleness.
    stale_mtime = time.time() - 3600
    os.utime(first, (stale_mtime, stale_mtime))
    rendered = len(inner.synthesized)
    second = cache.synthesize_to_wav("你好", "v")
    # Expired entry is re-rendered rather than reused.
    assert len(inner.synthesized) == rendered + 1
    assert first == second


class _RaiseTts(FakeTtsBackend):
    """Fake TTS whose unified entry always raises, to exercise the queue error path."""

    def synthesize_to_wav(self, text: str, voice: str, prefix: str = "ivr") -> Path:
        raise RuntimeError("boom")


def test_sync_conversion_queue_delivers_inline(tmp_path: Path) -> None:
    # SyncConversionQueue runs the callback inline (deterministic, test-friendly).
    q: SyncConversionQueue = SyncConversionQueue(_FileFakeTts(fake_wav=tmp_path / "i.wav"))
    delivered: list[tuple[object, object, object]] = []

    def on_done(wav, error=None, order=None) -> None:
        delivered.append((wav, error, order))

    q.submit("a", "v", prefix="ivr", order=7, on_done=on_done)
    q.submit("b", "v", prefix="ivr", order=3, on_done=on_done)
    # Inline: both callbacks have already run by the time submit returns.
    assert len(delivered) == 2
    orders = sorted(o for _w, _e, o in delivered)
    assert orders == [3, 7]
    assert all(e is None for _w, e, _o in delivered)


def test_conversion_queue_delivers_asynchronously(tmp_path: Path) -> None:
    import threading

    # Real ConversionQueue dispatches work to a worker pool; on_done fires (with
    # the original order) once each conversion completes, off the calling thread.
    q = ConversionQueue(_FileFakeTts(fake_wav=tmp_path / "i.wav"), max_workers=2)
    delivered: list[tuple[object, object, object]] = []
    ready = threading.Event()

    def on_done(wav, error=None, order=None) -> None:
        delivered.append((wav, error, order))
        if len(delivered) == 2:
            ready.set()

    q.submit("a", "v", order=0, on_done=on_done)
    q.submit("b", "v", order=1, on_done=on_done)
    assert ready.wait(timeout=5)
    q.shutdown()
    orders = sorted(o for _w, _e, o in delivered)
    assert orders == [0, 1]
    assert all(e is None for _w, e, _o in delivered)


def test_conversion_queue_delivers_error(tmp_path: Path) -> None:
    import threading

    q = ConversionQueue(_RaiseTts(), max_workers=1)
    captured: list[object] = []
    ready = threading.Event()

    def on_done(wav, error=None, order=None) -> None:
        captured.append(error)
        ready.set()

    q.submit("a", "v", order=0, on_done=on_done)
    assert ready.wait(timeout=5)
    q.shutdown()
    assert len(captured) == 1
    assert isinstance(captured[0], RuntimeError)



class _RetryProbe(EdgeTtsBackend):
    """EdgeTtsBackend whose single-shot synthesis fails the first ``fail_times``
    calls, so the retry loop is exercised without any network/ffmpeg."""

    def __init__(self, *args, fail_times: int = 0, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_times = fail_times
        self.calls = 0

    def _synthesize_once(self, text: str, voice: str) -> Path:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TimeoutError("edge-tts timed out (simulated)")
        out = self._cache_dir / "ok.mp3"
        out.write_bytes(b"x")
        return out

    def transcode(self, mp3_path: Path, wav_path: Path) -> Path:
        Path(wav_path).write_bytes(b"x")
        return wav_path


def test_edge_tts_retries_then_succeeds(tmp_path: Path) -> None:
    probe = _RetryProbe(cache_dir=tmp_path, retry_attempts=3, fail_times=1)
    wav = probe.synthesize_to_wav("你好", "v")
    # First attempt failed, the second succeeded -> exactly 2 calls, no error.
    assert probe.calls == 2
    assert wav.exists()


def test_edge_tts_retries_until_exhausted_then_raises(tmp_path: Path) -> None:
    logged: list[str] = []
    probe = _RetryProbe(
        cache_dir=tmp_path, retry_attempts=3, fail_times=99, logger=logged.append
    )
    with pytest.raises(TtsError):
        probe.synthesize("你好", "v")
    # All 3 attempts were made before giving up.
    assert probe.calls == 3
    # A log line was emitted for each failed attempt.
    assert sum("将重试" in m for m in logged) == 3


def test_edge_tts_retry_attempts_default_is_three(tmp_path: Path) -> None:
    probe = _RetryProbe(cache_dir=tmp_path)
    assert probe._retry_attempts == 3


class _FakeEdgeTtsModule:
    """Stand-in for the lazily imported ``edge_tts`` package (no network)."""

    class _Communicate:
        def __init__(self, text: str, voice: str) -> None:
            self.text, self.voice = text, voice

        async def save(self, path: str) -> None:
            Path(path).write_bytes(b"fake-mp3")

    def Communicate(self, text: str, voice: str) -> "_FakeEdgeTtsModule._Communicate":
        return self._Communicate(text, voice)


def test_synthesize_once_mp3_names_unique_in_same_second(
    monkeypatch, tmp_path: Path
) -> None:
    # Regression: parallel IVR syntheses landing in the same wall-clock second
    # must not share one report_<ts>.mp3 — a shared intermediate made two cache
    # keys ("1234567890" and "1234567890 请按2") come out byte-identical, so the
    # caller heard the digit-2 prompt while the welcome was announcing.
    monkeypatch.setitem(sys.modules, "edge_tts", _FakeEdgeTtsModule())
    monkeypatch.setattr(time, "strftime", lambda _fmt: "20260901_000000")
    backend = EdgeTtsBackend(cache_dir=tmp_path)
    first = backend._synthesize_once("1234567890", "v")
    second = backend._synthesize_once("1234567890 请按2", "v")
    assert first != second
    assert first.exists() and second.exists()


def test_caching_backend_retries_inner_on_miss(tmp_path: Path) -> None:
    # The production path wraps EdgeTtsBackend in CachingTtsBackend; a cache miss
    # must still reach the inner retry loop (2 failures then a success).
    probe = _RetryProbe(cache_dir=tmp_path, retry_attempts=3, fail_times=2)
    cache = CachingTtsBackend(probe, cache_dir=tmp_path / "cache")
    cache.synthesize_to_wav("你好", "v")
    assert probe.calls == 3
