"""
tests/test_project.py
=====================
core/project.py 핵심 함수 테스트
"""
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.project import (
    sanitize_name, now_stamp, create_project_structure,
    validate_video_file, copy_input_video, build_export_summary,
    preflight_summary, find_reusable_project, setup_logger,
)
from core.models import ProcessingError, ProjectPaths


# ──────────────────────────────────────────
# sanitize_name
# ──────────────────────────────────────────

def test_sanitize_name_korean():
    """한글 이름이 _ 로 치환되어야 함."""
    result = sanitize_name("남기동_테스트")
    assert "_" in result or result.isalnum()

def test_sanitize_name_spaces():
    result = sanitize_name("my video file")
    assert " " not in result

def test_sanitize_name_empty():
    result = sanitize_name("")
    assert result.startswith("project_")

def test_sanitize_name_special_chars():
    result = sanitize_name("file:name<>|?*")
    assert all(c not in result for c in ':/<>|?*')


# ──────────────────────────────────────────
# now_stamp
# ──────────────────────────────────────────

def test_now_stamp_format():
    stamp = now_stamp()
    assert len(stamp) == 15          # YYYYmmdd_HHMMSS
    assert "_" in stamp
    assert stamp[:8].isdigit()


# ──────────────────────────────────────────
# create_project_structure
# ──────────────────────────────────────────

def test_create_project_structure_creates_folders():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        paths = create_project_structure(base, "test_video")
        assert paths.root.exists()
        assert paths.input_dir.exists()
        assert paths.audio_dir.exists()
        assert paths.subtitles_dir.exists()
        assert paths.thumbnails_dir.exists()
        assert paths.output_dir.exists()
        assert paths.logs_dir.exists()

def test_create_project_structure_duplicate_name():
    """같은 이름 두 번 생성 시 타임스탬프 추가."""
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        p1 = create_project_structure(base, "dup")
        p2 = create_project_structure(base, "dup")
        assert p1.root != p2.root   # 서로 다른 경로

def test_create_project_structure_returns_project_paths():
    with tempfile.TemporaryDirectory() as d:
        paths = create_project_structure(Path(d), "my_project")
        assert isinstance(paths, ProjectPaths)


# ──────────────────────────────────────────
# validate_video_file
# ──────────────────────────────────────────

def test_validate_video_file_not_exists():
    with pytest.raises(ProcessingError) as exc:
        validate_video_file(Path("/nonexistent/video.mp4"), {".mp4"})
    assert "찾을 수 없습니다" in str(exc.value)

def test_validate_video_file_wrong_extension():
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        p = Path(f.name)
    try:
        with pytest.raises(ProcessingError) as exc:
            validate_video_file(p, {".mp4", ".mov"})
        assert "지원하지 않는" in str(exc.value)
    finally:
        p.unlink(missing_ok=True)

def test_validate_video_file_valid():
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        p = Path(f.name)
    try:
        validate_video_file(p, {".mp4"})   # 예외 없어야 함
    finally:
        p.unlink(missing_ok=True)

def test_validate_video_file_directory():
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(ProcessingError) as exc:
            validate_video_file(Path(d), {".mp4"})
        assert "파일이 아닙니다" in str(exc.value)


# ──────────────────────────────────────────
# copy_input_video
# ──────────────────────────────────────────

def test_copy_input_video():
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "source.mp4"
        src.write_bytes(b"fake video data")
        dst_dir = Path(d) / "project" / "input"
        dst_dir.mkdir(parents=True)
        result = copy_input_video(src, dst_dir)
        assert result.exists()
        assert result.read_bytes() == b"fake video data"

def test_copy_input_video_preserves_name():
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "my_video.mp4"
        src.write_bytes(b"x")
        dst_dir = Path(d) / "input"
        dst_dir.mkdir()
        result = copy_input_video(src, dst_dir)
        assert result.name == "my_video.mp4"


# ──────────────────────────────────────────
# setup_logger
# ──────────────────────────────────────────

def test_setup_logger_creates_file():
    with tempfile.TemporaryDirectory() as d:
        log_file = Path(d) / "logs" / "test.log"
        logger = setup_logger(log_file)
        logger.info("테스트 로그")
        assert log_file.exists()

def test_setup_logger_parent_created():
    with tempfile.TemporaryDirectory() as d:
        log_file = Path(d) / "deep" / "nested" / "run.log"
        setup_logger(log_file)           # 부모 폴더 자동 생성
        assert log_file.parent.exists()


# ──────────────────────────────────────────
# preflight_summary
# ──────────────────────────────────────────

def test_preflight_summary_all_ok():
    result = {
        "ffmpeg_ok": True, "opencv_ok": True,
        "whisper_ok": True, "project_root_writable": True,
        "disk_ok": True, "disk_free_gb": 50.0,
    }
    ok, summary = preflight_summary(result)
    assert ok is True
    assert "✅" in summary

def test_preflight_summary_ffmpeg_missing():
    result = {
        "ffmpeg_ok": False, "opencv_ok": True,
        "whisper_ok": True, "project_root_writable": True,
        "disk_ok": True, "disk_free_gb": 10.0,
    }
    ok, summary = preflight_summary(result)
    assert ok is False
    assert "❌" in summary


# ──────────────────────────────────────────
# find_reusable_project
# ──────────────────────────────────────────

def test_find_reusable_project_found():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        # 가짜 프로젝트 구조 생성
        proj = base / "my_video"
        (proj / "input").mkdir(parents=True)
        src = base / "my_video.mp4"
        src.write_bytes(b"video_content")
        (proj / "input" / "my_video.mp4").write_bytes(b"video_content")  # 같은 크기

        result = find_reusable_project(base, src)
        assert result is not None
        paths, vpath = result
        assert vpath.name == "my_video.mp4"

def test_find_reusable_project_not_found():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        src  = base / "new_video.mp4"
        src.write_bytes(b"new")
        result = find_reusable_project(base, src)
        assert result is None

def test_find_reusable_project_size_mismatch():
    """크기가 다르면 재사용하지 않아야 함."""
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        proj = base / "my_video"
        (proj / "input").mkdir(parents=True)
        src = base / "my_video.mp4"
        src.write_bytes(b"original_content")
        (proj / "input" / "my_video.mp4").write_bytes(b"different")  # 크기 다름
        result = find_reusable_project(base, src)
        assert result is None
