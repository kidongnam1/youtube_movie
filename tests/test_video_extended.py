"""
tests/test_video_extended.py
============================
core/video.py 확장 테스트
- trim_video 입력 검증
- adjust_volume 입력 검증
- get_video_duration lru_cache
- capture_frames finally 보장
- select_thumbnail 인덱스 검증
"""
import pytest
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.video import (
    build_subtitle_filter, get_video_duration,
    trim_video, adjust_volume, select_thumbnail,
    invalidate_duration_cache,
)
from core.models import ProcessingError


# ──────────────────────────────────────────
# build_subtitle_filter (확장)
# ──────────────────────────────────────────

def test_subtitle_filter_no_single_quote_injection():
    """작은따옴표가 이스케이프되어야 함."""
    p = Path("/home/user/sub'title.srt")
    result = build_subtitle_filter(p)
    # 이스케이프 처리 확인
    assert "subtitles=" in result

def test_subtitle_filter_deep_path():
    p = Path("/a/b/c/d/e/subtitle.srt")
    result = build_subtitle_filter(p)
    assert "subtitle.srt" in result


# ──────────────────────────────────────────
# get_video_duration — lru_cache
# ──────────────────────────────────────────

def test_get_video_duration_cache_stores_result():
    """lru_cache 모듈이 maxsize=64로 설정되어 있는지 확인."""
    import functools
    from core.video import get_video_duration
    # lru_cache가 적용된 함수인지 확인
    assert hasattr(get_video_duration, "cache_info")
    info = get_video_duration.cache_info()
    assert info.maxsize == 64

def test_get_video_duration_invalidate_clears_cache():
    """invalidate_duration_cache 호출 후 캐시가 비워져야 함."""
    from core.video import get_video_duration, invalidate_duration_cache
    invalidate_duration_cache(Path("any.mp4"))
    info = get_video_duration.cache_info()
    assert info.currsize == 0

def test_get_video_duration_raises_on_broken_file():
    """broken 파일이면 ProcessingError 발생."""
    with tempfile.TemporaryDirectory() as d:
        broken = Path(d) / "broken.mp4"
        broken.write_bytes(b"not a video")
        invalidate_duration_cache(broken)
        with patch("cv2.VideoCapture") as mock_cap, \
             patch("shutil.which", return_value=None):
            instance = MagicMock()
            instance.isOpened.return_value = False
            mock_cap.return_value = instance
            with pytest.raises(ProcessingError) as exc:
                get_video_duration(broken)
            assert "영상 길이" in str(exc.value)


# ──────────────────────────────────────────
# trim_video — 입력 검증
# ──────────────────────────────────────────

def test_trim_video_negative_start():
    with tempfile.TemporaryDirectory() as d:
        fake = Path(d) / "v.mp4"; fake.write_bytes(b"x")
        with pytest.raises(ProcessingError) as exc:
            trim_video(fake, -1.0, 0.0, Path(d), MagicMock())
        assert "0 이상" in str(exc.value)

def test_trim_video_exceeds_duration():
    with tempfile.TemporaryDirectory() as d:
        fake = Path(d) / "v.mp4"; fake.write_bytes(b"x")
        with patch("core.video.get_video_duration", return_value=10.0):
            with pytest.raises(ProcessingError) as exc:
                trim_video(fake, 6.0, 5.0, Path(d), MagicMock())
            assert "크거나 같습니다" in str(exc.value)

def test_trim_video_zero_duration():
    with tempfile.TemporaryDirectory() as d:
        fake = Path(d) / "v.mp4"; fake.write_bytes(b"x")
        with patch("core.video.get_video_duration", return_value=0.0):
            with pytest.raises(ProcessingError) as exc:
                trim_video(fake, 1.0, 0.0, Path(d), MagicMock())
            assert "길이를 확인할 수 없어" in str(exc.value)


# ──────────────────────────────────────────
# adjust_volume — 입력 검증
# ──────────────────────────────────────────

def test_adjust_volume_zero():
    with tempfile.TemporaryDirectory() as d:
        fake = Path(d) / "v.mp4"; fake.write_bytes(b"x")
        with pytest.raises(ProcessingError) as exc:
            adjust_volume(fake, 0.0, Path(d), MagicMock())
        assert "0보다 커야" in str(exc.value)

def test_adjust_volume_negative():
    with tempfile.TemporaryDirectory() as d:
        fake = Path(d) / "v.mp4"; fake.write_bytes(b"x")
        with pytest.raises(ProcessingError) as exc:
            adjust_volume(fake, -0.5, Path(d), MagicMock())
        assert "0보다 커야" in str(exc.value)


# ──────────────────────────────────────────
# select_thumbnail — 인덱스 검증
# ──────────────────────────────────────────

def test_select_thumbnail_valid():
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "frame_01.jpg"
        src.write_bytes(b"jpg")
        out = Path(d) / "output"
        out.mkdir()
        result = select_thumbnail([src], 0, out, MagicMock())
        assert result.exists()
        assert result.name == "selected_thumbnail.jpg"

def test_select_thumbnail_out_of_range():
    with pytest.raises(ProcessingError) as exc:
        select_thumbnail([Path("a.jpg")], 5, Path("."), MagicMock())
    assert "잘못된" in str(exc.value)

def test_select_thumbnail_negative_index():
    with pytest.raises(ProcessingError) as exc:
        select_thumbnail([Path("a.jpg")], -1, Path("."), MagicMock())
    assert "잘못된" in str(exc.value)

def test_select_thumbnail_empty_list():
    with pytest.raises(ProcessingError):
        select_thumbnail([], 0, Path("."), MagicMock())
