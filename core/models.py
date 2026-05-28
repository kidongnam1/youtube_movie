"""
core/models.py — V2.2
[NEW] BatchJob: start_time, end_time, elapsed_sec 필드 추가
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import List, Literal, Optional


class UserVisibleError(Exception):
    pass


class ProcessingError(UserVisibleError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class AppState(Enum):
    IDLE          = auto()
    BUSY          = auto()
    PROJECT_READY = auto()


@dataclass
class ProjectPaths:
    root:           Path
    input_dir:      Path
    audio_dir:      Path
    subtitles_dir:  Path
    thumbnails_dir: Path
    output_dir:     Path
    logs_dir:       Path


@dataclass
class WorkflowResult:
    project_paths:    ProjectPaths
    input_video_path: Path
    captured_frames:  List[Path]
    audio_path:       Optional[Path]
    text_path:        Optional[Path]
    srt_path:         Optional[Path]
    final_video_path: Optional[Path]


@dataclass
class SmartFrameSearchRequest:
    video_path:            Path
    search_mode:           Literal["auto", "range"]
    result_count:          int   = 3
    sampling_interval_sec: float = 0.5
    min_frame_gap_sec:     float = 1.5
    scene_threshold:       float = 30.0
    use_sharpness_filter:  bool  = True
    use_brightness_filter: bool  = True
    target_time_sec:       Optional[float] = None
    search_window_sec:     Optional[float] = None


@dataclass
class FrameSample:
    timestamp_sec: float
    frame_image:   object


@dataclass
class SmartFrameCandidate:
    timestamp_sec:    float
    frame_path:       Optional[Path]
    total_score:      float
    scene_score:      float
    sharpness_score:  float
    brightness_score: float


@dataclass
class SmartFrameSearchResult:
    mode:             str
    candidates:       List[SmartFrameCandidate]
    search_start_sec: float
    search_end_sec:   float


# ══════════════════════════════════════════
# 배치 처리 모델
# ══════════════════════════════════════════

class BatchStatus(Enum):
    PENDING   = auto()
    RUNNING   = auto()
    DONE      = auto()
    FAILED    = auto()
    SKIPPED   = auto()
    CANCELLED = auto()


@dataclass
class BatchJob:
    """배치 큐의 단일 작업 단위."""
    job_id:        int
    video_path:    Path
    trim_start:    float = 0.0
    trim_end:      float = 0.0
    volume_ratio:  float = 1.0
    capture_times: List[float] = field(default_factory=lambda: [3.0, 10.0, 20.0])
    status:        BatchStatus = BatchStatus.PENDING
    error_message: str         = ""
    result:        Optional[WorkflowResult] = None

    # [NEW] 처리 시간 기록
    start_time:    Optional[datetime] = None
    end_time:      Optional[datetime] = None

    @property
    def elapsed_sec(self) -> Optional[float]:
        """처리 소요 시간(초). 완료된 경우만 반환."""
        if self.start_time and self.end_time:
            return round((self.end_time - self.start_time).total_seconds(), 1)
        return None

    @property
    def elapsed_str(self) -> str:
        """사람이 읽기 쉬운 소요 시간 문자열."""
        sec = self.elapsed_sec
        if sec is None:
            return "-"
        if sec < 60:
            return f"{sec:.0f}초"
        m, s = divmod(int(sec), 60)
        return f"{m}분 {s:02d}초"


@dataclass
class BatchSummary:
    """배치 전체 실행 결과 요약."""
    total:     int = 0
    done:      int = 0
    failed:    int = 0
    skipped:   int = 0
    cancelled: int = 0

    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return round(self.done / self.total * 100, 1)
