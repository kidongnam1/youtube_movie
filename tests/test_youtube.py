"""tests/test_youtube.py — core/youtube.py URL 검증 테스트"""
import pytest
from core.youtube import validate_youtube_url
from core.models import ProcessingError


def test_valid_watch_url():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert validate_youtube_url(url) == url


def test_valid_youtu_be():
    url = "https://youtu.be/dQw4w9WgXcQ"
    assert validate_youtube_url(url) == url


def test_valid_shorts():
    url = "https://www.youtube.com/shorts/abcdefg"
    assert validate_youtube_url(url) == url


def test_reject_playlist():
    with pytest.raises(ProcessingError) as exc:
        validate_youtube_url("https://youtube.com/playlist?list=PLxxx")
    assert "플레이리스트" in str(exc.value)


def test_reject_empty():
    with pytest.raises(ProcessingError):
        validate_youtube_url("")


def test_reject_invalid_domain():
    with pytest.raises(ProcessingError):
        validate_youtube_url("https://vimeo.com/12345")
