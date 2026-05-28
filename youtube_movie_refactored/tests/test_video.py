"""tests/test_video.py — core/video.py 핵심 테스트"""
# pytest는 conftest.py를 통해 자동 주입됨
from pathlib import Path
from core.video import build_subtitle_filter
# ProcessingError: 미래 예외 테스트 대비 보존


def test_subtitle_filter_windows_korean_path():
    """한글 경로가 포함된 Windows 경로를 올바르게 처리하는지 확인."""
    p = Path("C:/사용자/남기동/프로젝트/subtitle.srt")
    result = build_subtitle_filter(p)
    assert "subtitles=" in result
    assert "'" in result  # 따옴표로 감싸짐


def test_subtitle_filter_space_in_path():
    """공백이 있는 경로 처리 확인."""
    p = Path("/home/user/my project/subtitle.srt")
    result = build_subtitle_filter(p)
    assert "subtitles=" in result


def test_subtitle_filter_simple_path():
    """일반 영문 경로 처리 확인."""
    p = Path("/home/user/project/subtitle.srt")
    result = build_subtitle_filter(p)
    assert result.startswith("subtitles=")
