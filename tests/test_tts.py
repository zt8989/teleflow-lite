"""TDD tests for the TTS / synthesis layer (ticket 02).

Covers the Markdown cleaner, ffmpeg discovery (config path vs PATH vs missing),
the transcode failure path, and the fake backend. The real edge-tts synthesis
requires network and is intentionally not asserted in CI (native/network-only).
"""

import shutil
import sys
from pathlib import Path

import pytest

from teleflow.tts import (
    FfmpegError,
    FfmpegNotFound,
    CachingTtsBackend,
    EdgeTtsBackend,
    FakeTtsBackend,
    clean_markdown,
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
