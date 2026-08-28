"""TDD tests for the TTS / synthesis layer (ticket 02).

Covers the Markdown cleaner, ffmpeg discovery (config path vs PATH vs missing),
the transcode failure path, and the fake backend. The real edge-tts synthesis
requires network and is intentionally not asserted in CI (native/network-only).
"""

import shutil
from pathlib import Path

import pytest

from teleflow.tts import (
    FfmpegError,
    FfmpegNotFound,
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
    monkeypatch.setattr(shutil, "which", lambda _name: "/bin/cat")
    backend = EdgeTtsBackend(ffmpeg_path="", cache_dir=Path("/tmp/teleflow-tts-test"))
    assert backend._ffmpeg_bin() == "/bin/cat"


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
    # A present-but-failing binary stands in for ffmpeg exiting non-zero.
    script = tmp_path / "ffmpeg_fail"
    script.write_text("#!/bin/sh\nexit 3\n")
    script.chmod(0o755)
    backend = EdgeTtsBackend(ffmpeg_path=str(script), cache_dir=tmp_path)
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
