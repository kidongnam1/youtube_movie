"""
tests/test_models.py
====================
core/models.py 데이터 모델 + 배치 처리 모델 테스트
"""
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.models import (
    ProcessingError, BatchJob, BatchStatus, BatchSummary,
    ProjectPaths, WorkflowResult, SmartFrameSearchRequest,
)


# ──────────────────────────────────────────
# ProcessingError
# ──────────────────────────────────────────

def test_processing_error_has_category():
    exc = ProcessingError("ffmpeg_error", "FFmpeg 실패")
    assert exc.category == "ffmpeg_error"
    assert str(exc) == "FFmpeg 실패"

def test_processing_error_is_exception():
    with pytest.raises(ProcessingError):
        raise ProcessingError("input_error", "잘못된 입력")

def test_processing_error_categories():
    """지원되는 모든 카테고리 생성 가능해야 함."""
    cats = ["input_error","ffmpeg_error","whisper_error","file_write_error",
            "smart_search_error","youtube_error","unexpected_error"]
    for cat in cats:
        exc = ProcessingError(cat, "test")
        assert exc.category == cat


# ──────────────────────────────────────────
# BatchJob
# ──────────────────────────────────────────

def test_batch_job_default_status():
    job = BatchJob(job_id=1, video_path=Path("test.mp4"))
    assert job.status == BatchStatus.PENDING

def test_batch_job_default_capture_times():
    job = BatchJob(job_id=1, video_path=Path("test.mp4"))
    assert job.capture_times == [3.0, 10.0, 20.0]

def test_batch_job_status_transition():
    job = BatchJob(job_id=1, video_path=Path("test.mp4"))
    job.status = BatchStatus.RUNNING
    assert job.status == BatchStatus.RUNNING
    job.status = BatchStatus.DONE
    assert job.status == BatchStatus.DONE

def test_batch_job_error_message():
    job = BatchJob(job_id=2, video_path=Path("fail.mp4"))
    job.status = BatchStatus.FAILED
    job.error_message = "FFmpeg 오류"
    assert job.error_message == "FFmpeg 오류"


# ──────────────────────────────────────────
# BatchSummary
# ──────────────────────────────────────────

def test_batch_summary_success_rate_zero():
    s = BatchSummary(total=0)
    assert s.success_rate == 0.0

def test_batch_summary_success_rate_100():
    s = BatchSummary(total=5, done=5)
    assert s.success_rate == 100.0

def test_batch_summary_success_rate_partial():
    s = BatchSummary(total=10, done=7, failed=3)
    assert s.success_rate == 70.0

def test_batch_summary_mixed():
    s = BatchSummary(total=10, done=5, failed=2, skipped=2, cancelled=1)
    assert s.done + s.failed + s.skipped + s.cancelled == 10


# ──────────────────────────────────────────
# SmartFrameSearchRequest
# ──────────────────────────────────────────

def test_smart_frame_request_defaults():
    req = SmartFrameSearchRequest(
        video_path=Path("v.mp4"),
        search_mode="auto",
    )
    assert req.result_count == 3
    assert req.sampling_interval_sec == 0.5
    assert req.min_frame_gap_sec == 1.5
    assert req.use_sharpness_filter is True
    assert req.target_time_sec is None

def test_smart_frame_request_range_mode():
    req = SmartFrameSearchRequest(
        video_path=Path("v.mp4"),
        search_mode="range",
        target_time_sec=30.0,
        search_window_sec=10.0,
    )
    assert req.search_mode == "range"
    assert req.target_time_sec == 30.0
    assert req.search_window_sec == 10.0
