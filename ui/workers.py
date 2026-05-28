"""
ui/workers.py — V2.2
[NEW] BatchWorker._process_one(): start_time/end_time 기록
[NEW] SubtitleSaveWorker, SmartFrameWorker, SnapshotWorker 기존 유지
"""
from __future__ import annotations

import traceback
from datetime import datetime
from pathlib import Path
from typing import List

from PySide6.QtCore import QThread, Signal

from core.models import (
    UserVisibleError, WorkflowResult,
    SmartFrameSearchRequest,
    BatchJob, BatchStatus, BatchSummary,
)
from core.project import (
    create_project_structure, setup_logger,
    copy_input_video, validate_video_file,
    find_reusable_project,
)
from core.video import capture_frames, extract_audio, trim_video, adjust_volume, export_video
from core.subtitle import generate_subtitles, edit_subtitle_text, regenerate_srt
from core.smart_frame import run_smart_frame_search, export_static_snapshots
from core.youtube import validate_youtube_url, download_youtube_video
from config.settings import SUPPORTED_EXTENSIONS


# ══════════════════════════════════════════
# 단일 영상 워크플로우
# ══════════════════════════════════════════

class WorkflowWorker(QThread):
    progress    = Signal(int)
    status      = Signal(str)
    finished_ok = Signal(object)
    failed      = Signal(str)

    def __init__(self, video_path: str, project_base_dir: str,
                 trim_start: float, trim_end: float,
                 volume_ratio: float, capture_times: List[float],
                 parent=None) -> None:
        super().__init__(parent)
        self.video_path       = Path(video_path)
        self.project_base_dir = Path(project_base_dir)
        self.trim_start       = trim_start
        self.trim_end         = trim_end
        self.volume_ratio     = volume_ratio
        self.capture_times    = capture_times

    def run(self) -> None:
        try:
            self._execute()
        except UserVisibleError as exc:
            self.failed.emit(f"[{getattr(exc,'category','unexpected_error')}] {exc}")
        except Exception:
            self.failed.emit("[unexpected_error] 예상치 못한 오류\n\n" + traceback.format_exc())

    def _execute(self) -> None:
        self.progress.emit(5);  self.status.emit("입력 영상 확인 중...")
        validate_video_file(self.video_path, SUPPORTED_EXTENSIONS)

        self.status.emit("프로젝트 폴더 생성 중...")
        paths  = create_project_structure(self.project_base_dir, self.video_path.stem)
        logger = setup_logger(paths.logs_dir / "run.log")

        self.progress.emit(10); self.status.emit("영상 복사 중...")
        video  = copy_input_video(self.video_path, paths.input_dir)

        self.progress.emit(20); self.status.emit("프레임 추출 중...")
        frames = capture_frames(video, self.capture_times, paths.thumbnails_dir, logger)

        self.progress.emit(35); self.status.emit("오디오 추출 중...")
        audio  = extract_audio(video, paths.audio_dir, logger)

        self.progress.emit(50); self.status.emit("Whisper 자막 생성 중...")
        text_path, srt_path = generate_subtitles(audio, paths.subtitles_dir, logger)

        current = video
        if self.trim_start > 0 or self.trim_end > 0:
            self.progress.emit(70); self.status.emit("트림 적용 중...")
            current = trim_video(current, self.trim_start, self.trim_end, paths.output_dir, logger)

        if abs(self.volume_ratio - 1.0) > 1e-9:
            self.progress.emit(82); self.status.emit("볼륨 조절 중...")
            current = adjust_volume(current, self.volume_ratio, paths.output_dir, logger)

        self.progress.emit(92); self.status.emit("최종 내보내기 중...")
        final  = export_video(current, paths.output_dir, logger, srt_path)

        self.progress.emit(100); self.status.emit("완료!")
        self.finished_ok.emit(WorkflowResult(
            project_paths=paths, input_video_path=video,
            captured_frames=frames, audio_path=audio,
            text_path=text_path, srt_path=srt_path,
            final_video_path=final,
        ))


# ══════════════════════════════════════════
# 자막 저장 Worker (P0-2)
# ══════════════════════════════════════════

class SubtitleSaveWorker(QThread):
    status      = Signal(str)
    finished_ok = Signal(str)
    failed      = Signal(str)

    def __init__(self, text: str, subtitles_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.text          = text
        self.subtitles_dir = subtitles_dir

    def run(self) -> None:
        try:
            import logging
            logger = logging.getLogger("subtitle_save")
            self.status.emit("자막 저장 중...")
            edit_subtitle_text(self.text, self.subtitles_dir, logger)
            regenerate_srt(self.text, self.subtitles_dir, logger)
            self.finished_ok.emit("자막 저장 완료")
        except UserVisibleError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit("자막 저장 오류\n\n" + traceback.format_exc())


# ══════════════════════════════════════════
# 배치 처리 Worker — [NEW] 시간 기록 추가
# ══════════════════════════════════════════

class BatchWorker(QThread):
    """
    [V2.2] 각 BatchJob의 start_time / end_time 자동 기록.
    elapsed_sec, elapsed_str 로 소요 시간 조회 가능.
    """
    job_started    = Signal(int, str)
    job_progress   = Signal(int, int)
    job_done       = Signal(int, object)
    job_failed     = Signal(int, str)
    job_skipped    = Signal(int, str)
    batch_progress = Signal(int, int)
    batch_done     = Signal(object)
    status         = Signal(str)

    def __init__(self, jobs: List[BatchJob], project_base_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self._jobs             = jobs
        self._project_base_dir = project_base_dir
        self._cancelled        = False

    def cancel(self) -> None:
        self._cancelled = True
        self.status.emit("취소 요청됨 — 현재 영상 완료 후 중단합니다...")

    def run(self) -> None:
        summary = BatchSummary(total=len(self._jobs))

        for job in self._jobs:
            if self._cancelled:
                job.status = BatchStatus.CANCELLED
                summary.cancelled += 1
                continue

            # 기존 프로젝트 재사용 확인
            reuse = find_reusable_project(self._project_base_dir, job.video_path)
            if reuse:
                paths, vpath = reuse
                job.status = BatchStatus.SKIPPED
                summary.skipped += 1
                self.job_skipped.emit(job.job_id, str(paths.root))
                self.batch_progress.emit(
                    summary.done + summary.skipped + summary.failed, summary.total)
                continue

            self.job_started.emit(job.job_id, job.video_path.name)
            job.status     = BatchStatus.RUNNING
            job.start_time = datetime.now()   # ← [NEW] 시작 시각 기록

            try:
                result     = self._process_one(job)
                job.end_time = datetime.now()  # ← [NEW] 종료 시각 기록
                job.status = BatchStatus.DONE
                job.result = result
                summary.done += 1
                self.job_done.emit(job.job_id, result)
            except Exception as exc:
                job.end_time  = datetime.now()  # ← [NEW] 실패 시각도 기록
                job.status        = BatchStatus.FAILED
                job.error_message = str(exc)
                summary.failed += 1
                self.job_failed.emit(job.job_id, str(exc))

            self.batch_progress.emit(
                summary.done + summary.skipped + summary.failed, summary.total)

        self.batch_done.emit(summary)

    def _process_one(self, job: BatchJob) -> WorkflowResult:
        def _prog(pct: int, msg: str) -> None:
            self.job_progress.emit(job.job_id, pct)
            self.status.emit(f"[{job.video_path.name}] {msg}")

        _prog(5,  "입력 확인 중...")
        validate_video_file(job.video_path, SUPPORTED_EXTENSIONS)

        _prog(8,  "프로젝트 폴더 생성 중...")
        paths  = create_project_structure(self._project_base_dir, job.video_path.stem)
        logger = setup_logger(paths.logs_dir / "run.log")

        _prog(12, "영상 복사 중...")
        video  = copy_input_video(job.video_path, paths.input_dir)

        _prog(22, "프레임 추출 중...")
        frames = capture_frames(video, job.capture_times, paths.thumbnails_dir, logger)

        _prog(36, "오디오 추출 중...")
        audio  = extract_audio(video, paths.audio_dir, logger)

        _prog(52, "자막 생성 중...")
        text_path, srt_path = generate_subtitles(audio, paths.subtitles_dir, logger)

        current = video
        if job.trim_start > 0 or job.trim_end > 0:
            _prog(72, "트림 중...")
            current = trim_video(current, job.trim_start, job.trim_end, paths.output_dir, logger)

        if abs(job.volume_ratio - 1.0) > 1e-9:
            _prog(84, "볼륨 조절 중...")
            current = adjust_volume(current, job.volume_ratio, paths.output_dir, logger)

        _prog(93, "내보내기 중...")
        final = export_video(current, paths.output_dir, logger, srt_path)

        _prog(100, "완료!")
        return WorkflowResult(
            project_paths=paths, input_video_path=video,
            captured_frames=frames, audio_path=audio,
            text_path=text_path, srt_path=srt_path,
            final_video_path=final,
        )


# ══════════════════════════════════════════
# 스마트 프레임 / 스냅샷
# ══════════════════════════════════════════

class SmartFrameWorker(QThread):
    status      = Signal(str)
    finished_ok = Signal(object)
    failed      = Signal(str)

    def __init__(self, request: SmartFrameSearchRequest, output_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.request    = request
        self.output_dir = output_dir

    def run(self) -> None:
        try:
            self.status.emit("스마트 프레임 검색 중...")
            log_dir = self.output_dir.parent / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            logger = setup_logger(log_dir / "smart_search.log")
            result = run_smart_frame_search(self.request, self.output_dir, logger)
            self.status.emit(f"검색 완료: {len(result.candidates)}개")
            self.finished_ok.emit(result)
        except UserVisibleError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit("스마트 검색 오류\n\n" + traceback.format_exc())


class SnapshotWorker(QThread):
    status      = Signal(str)
    finished_ok = Signal(str)
    failed      = Signal(str)

    def __init__(self, video_path: Path, project_root: Path, parent=None) -> None:
        super().__init__(parent)
        self.video_path   = video_path
        self.project_root = project_root

    def run(self) -> None:
        try:
            self.status.emit("정적 장면 감지 중...")
            log_dir = self.project_root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            logger  = setup_logger(log_dir / "snapshot.log")
            out_dir = export_static_snapshots(self.video_path, self.project_root, logger)
            self.status.emit(f"스냅샷 완료: {out_dir}")
            self.finished_ok.emit(str(out_dir))
        except UserVisibleError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit("스냅샷 오류\n\n" + traceback.format_exc())


# ══════════════════════════════════════════
# [NEW] YouTube URL → 다운로드 → 전체 처리
# ══════════════════════════════════════════

class YouTubeWorkflowWorker(QThread):
    """
    YouTube URL 입력 시 사용하는 Worker.
    1단계: yt-dlp로 영상 다운로드
    2단계: 다운로드된 파일로 WorkflowWorker._execute() 동일 처리
    """
    progress    = Signal(int)
    status      = Signal(str)
    finished_ok = Signal(object)   # WorkflowResult
    failed      = Signal(str)

    def __init__(
        self,
        youtube_url: str,
        project_base_dir: str,
        trim_start: float,
        trim_end: float,
        volume_ratio: float,
        capture_times: List[float],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.youtube_url      = youtube_url
        self.project_base_dir = Path(project_base_dir)
        self.trim_start       = trim_start
        self.trim_end         = trim_end
        self.volume_ratio     = volume_ratio
        self.capture_times    = capture_times

    def run(self) -> None:
        try:
            self._execute()
        except UserVisibleError as exc:
            self.failed.emit(f"[{getattr(exc,'category','unexpected_error')}] {exc}")
        except Exception:
            self.failed.emit("[unexpected_error] 예상치 못한 오류\n\n" + traceback.format_exc())

    def _execute(self) -> None:
        # ── 1단계: URL 검증 ──────────────────
        self.progress.emit(3)
        self.status.emit("YouTube URL 검증 중...")
        validate_youtube_url(self.youtube_url)

        # ── 2단계: 임시 다운로드 폴더 준비 ──
        self.progress.emit(8)
        self.status.emit("다운로드 준비 중...")
        import tempfile
        tmp_dir = Path(tempfile.mkdtemp(prefix="yt_download_"))
        tmp_log = tmp_dir / "yt_download.log"
        logger  = setup_logger(tmp_log)

        # ── 3단계: 다운로드 ──────────────────
        self.progress.emit(12)
        self.status.emit("YouTube 영상 다운로드 중... (영상 크기에 따라 시간이 걸립니다)")
        downloaded = download_youtube_video(self.youtube_url, tmp_dir, logger)
        logger.info("다운로드 완료: %s", downloaded)

        # ── 4단계: 프로젝트 구조 생성 ────────
        self.progress.emit(25)
        self.status.emit("프로젝트 폴더 생성 중...")
        paths = create_project_structure(self.project_base_dir, downloaded.stem)
        logger = setup_logger(paths.logs_dir / "run.log")

        # ── 5단계: 이하 WorkflowWorker 동일 ──
        self.progress.emit(30)
        self.status.emit("영상 복사 중...")
        video = copy_input_video(downloaded, paths.input_dir)

        self.progress.emit(38)
        self.status.emit("프레임 추출 중...")
        frames = capture_frames(video, self.capture_times, paths.thumbnails_dir, logger)

        self.progress.emit(48)
        self.status.emit("오디오 추출 중...")
        audio = extract_audio(video, paths.audio_dir, logger)

        self.progress.emit(58)
        self.status.emit("Whisper 자막 생성 중... (처음엔 시간이 걸립니다)")
        text_path, srt_path = generate_subtitles(audio, paths.subtitles_dir, logger)

        current = video
        if self.trim_start > 0 or self.trim_end > 0:
            self.progress.emit(72)
            self.status.emit("트림 적용 중...")
            current = trim_video(current, self.trim_start, self.trim_end, paths.output_dir, logger)

        if abs(self.volume_ratio - 1.0) > 1e-9:
            self.progress.emit(83)
            self.status.emit("볼륨 조절 중...")
            current = adjust_volume(current, self.volume_ratio, paths.output_dir, logger)

        self.progress.emit(92)
        self.status.emit("최종 내보내기 중...")
        final = export_video(current, paths.output_dir, logger, srt_path)

        # ── 임시 다운로드 폴더 정리 ──────────
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

        self.progress.emit(100)
        self.status.emit("완료!")

        self.finished_ok.emit(WorkflowResult(
            project_paths=paths,
            input_video_path=video,
            captured_frames=frames,
            audio_path=audio,
            text_path=text_path,
            srt_path=srt_path,
            final_video_path=final,
        ))
