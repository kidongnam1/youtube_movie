"""
tests/test_subtitle_extended.py
================================
core/subtitle.py 확장 테스트
- Whisper 캐싱 동작
- SRT 타임코드 형식
- regenerate_srt 폴백
- _distribute_text 경계 케이스
"""
import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.subtitle import (
    _seconds_to_srt_time, write_srt,
    edit_subtitle_text, regenerate_srt,
    _distribute_text, clear_whisper_cache,
)
from core.models import ProcessingError


# ──────────────────────────────────────────
# _seconds_to_srt_time
# ──────────────────────────────────────────

def test_srt_time_zero():
    assert _seconds_to_srt_time(0.0) == "00:00:00,000"

def test_srt_time_one_hour():
    assert _seconds_to_srt_time(3600.0) == "01:00:00,000"

def test_srt_time_millis():
    result = _seconds_to_srt_time(1.5)
    assert result == "00:00:01,500"

def test_srt_time_complex():
    result = _seconds_to_srt_time(3723.456)
    assert result == "01:02:03,456"

def test_srt_time_rounding():
    """밀리초 반올림이 정상 동작해야 함."""
    result = _seconds_to_srt_time(1.9999)
    assert "001" in result or "000" in result  # 반올림 허용


# ──────────────────────────────────────────
# write_srt
# ──────────────────────────────────────────

def test_write_srt_numbering():
    segs = [
        {"start": 0.0, "end": 1.0, "text": "첫 번째"},
        {"start": 1.0, "end": 2.0, "text": "두 번째"},
        {"start": 2.0, "end": 3.0, "text": "세 번째"},
    ]
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "test.srt"
        write_srt(segs, out)
        content = out.read_text(encoding="utf-8")
        assert "1\n" in content
        assert "2\n" in content
        assert "3\n" in content

def test_write_srt_empty_segments():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "empty.srt"
        write_srt([], out)
        assert out.exists()
        assert out.read_text(encoding="utf-8").strip() == ""

def test_write_srt_arrow_format():
    segs = [{"start": 0.0, "end": 2.5, "text": "test"}]
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "t.srt"
        write_srt(segs, out)
        content = out.read_text(encoding="utf-8")
        assert "-->" in content


# ──────────────────────────────────────────
# Whisper 캐싱 (_MODEL_CACHE)
# ──────────────────────────────────────────

def test_whisper_cache_hit():
    """캐시 히트 시 load_model이 2회 호출되지 않아야 함."""
    import core.subtitle as sub_mod
    clear_whisper_cache()

    fake_model = MagicMock()
    fake_model.transcribe.return_value = {"text": "hello", "segments": []}

    with patch("core.subtitle._MODEL_CACHE", {"base": fake_model}):
        logger = MagicMock()
        model = sub_mod._load_whisper_model("base", logger)
        assert model is fake_model
        logger.info.assert_called()

def test_whisper_cache_cleared():
    """clear_whisper_cache 후 캐시가 비워져야 함."""
    import core.subtitle as sub_mod
    sub_mod._MODEL_CACHE["base"] = MagicMock()
    clear_whisper_cache()
    assert len(sub_mod._MODEL_CACHE) == 0


# ──────────────────────────────────────────
# edit_subtitle_text
# ──────────────────────────────────────────

def test_edit_subtitle_text_saves():
    with tempfile.TemporaryDirectory() as d:
        sub_dir = Path(d)
        text_path = sub_dir / "subtitle.txt"
        text_path.write_text("원본", encoding="utf-8")
        edit_subtitle_text("수정된 텍스트", sub_dir, MagicMock())
        assert text_path.read_text(encoding="utf-8") == "수정된 텍스트"

def test_edit_subtitle_text_creates_if_missing():
    with tempfile.TemporaryDirectory() as d:
        sub_dir = Path(d)
        edit_subtitle_text("새 텍스트", sub_dir, MagicMock())
        assert (sub_dir / "subtitle.txt").read_text(encoding="utf-8") == "새 텍스트"


# ──────────────────────────────────────────
# regenerate_srt
# ──────────────────────────────────────────

def test_regenerate_srt_with_metadata():
    """메타데이터가 있으면 타이밍을 유지해야 함."""
    with tempfile.TemporaryDirectory() as d:
        sub_dir = Path(d)
        meta = {
            "segments": [
                {"start": 0.0, "end": 3.0, "text": "hello world"},
                {"start": 3.0, "end": 6.0, "text": "goodbye"},
            ]
        }
        (sub_dir / "subtitle_metadata.json").write_text(
            json.dumps(meta), encoding="utf-8"
        )
        srt_path = regenerate_srt("새로운 자막입니다", sub_dir, MagicMock())
        content = srt_path.read_text(encoding="utf-8")
        assert "-->" in content
        assert "00:00:00" in content

def test_regenerate_srt_fallback_no_metadata():
    """메타데이터 없으면 단일 큐 폴백."""
    with tempfile.TemporaryDirectory() as d:
        sub_dir = Path(d)
        srt_path = regenerate_srt("폴백 자막", sub_dir, MagicMock())
        content = srt_path.read_text(encoding="utf-8")
        assert "폴백 자막" in content
        assert "00:00:00,000 --> 00:10:00,000" in content


# ──────────────────────────────────────────
# _distribute_text (경계 케이스)
# ──────────────────────────────────────────

def test_distribute_text_single_segment():
    segs = [{"start": 0.0, "end": 5.0, "text": "hello"}]
    result = _distribute_text("전체 텍스트", segs)
    assert len(result) == 1
    assert result[0]["text"] == "전체 텍스트"

def test_distribute_text_preserves_timing():
    segs = [
        {"start": 1.0, "end": 2.0, "text": "a"},
        {"start": 5.0, "end": 7.0, "text": "bb"},
    ]
    result = _distribute_text("새텍스트", segs)
    assert result[0]["start"] == 1.0
    assert result[1]["end"] == 7.0

def test_distribute_text_total_chars_preserved():
    """분배 후 전체 텍스트 길이가 원본과 같아야 함."""
    segs = [
        {"start": 0.0, "end": 1.0, "text": "aaa"},
        {"start": 1.0, "end": 2.0, "text": "bbb"},
        {"start": 2.0, "end": 3.0, "text": "cc"},
    ]
    original = "새로운자막텍스트"
    result = _distribute_text(original, segs)
    combined = "".join(r["text"] for r in result)
    assert len(combined) == len(original)
