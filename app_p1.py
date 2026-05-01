from __future__ import annotations

"""
GPT_V1_VideoAutomationSystem_v1_1_p1.py

Single-file desktop application for automated video processing.

Core features:
- Select one video file
- Create project folder structure
- Extract frames at fixed timestamps
- Extract audio with FFmpeg
- Generate subtitles with local Whisper
- Minimal editing:
    - Trim start/end
    - Edit subtitle text
    - Select thumbnail
    - Adjust volume ratio
- Smart Frame Search V1.1:
    - Auto Best Frames
    - Guided Range Search
- Export final video
- Logging

P0 stabilization included:
- Subtitle TXT/SRT synchronization after text edits
- Export source safety check
- Smart-frame empty-result handling
- Original/project path separation
- Structured error categories
- Permission/write failure handling

Dependencies:
    pip install PySide6 opencv-python openai-whisper

External requirement:
    FFmpeg must be installed and available in PATH.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional

import cv2
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


APP_NAME = "Video Automation System V1.1 P1"
SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}
DEFAULT_CAPTURE_TIMES = [3.0, 10.0, 20.0]
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"


class UserVisibleError(Exception):
    """Readable exception for end users."""


class ProcessingError(UserVisibleError):
    """Categorized user-visible processing error."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass
class ProjectPaths:
    root: Path
    input_dir: Path
    audio_dir: Path
    subtitles_dir: Path
    thumbnails_dir: Path
    output_dir: Path
    logs_dir: Path


@dataclass
class WorkflowResult:
    project_paths: ProjectPaths
    input_video_path: Path
    captured_frames: List[Path]
    audio_path: Optional[Path]
    text_path: Optional[Path]
    srt_path: Optional[Path]
    selected_thumbnail: Optional[Path]
    final_video_path: Optional[Path]


@dataclass
class SmartFrameSearchRequest:
    video_path: Path
    search_mode: Literal["auto", "range"]
    result_count: int = 3
    sampling_interval_sec: float = 0.5
    min_frame_gap_sec: float = 1.5
    scene_threshold: float = 30.0
    use_sharpness_filter: bool = True
    use_brightness_filter: bool = True
    target_time_sec: Optional[float] = None
    search_window_sec: Optional[float] = None


@dataclass
class FrameSample:
    timestamp_sec: float
    frame_image: object


@dataclass
class SmartFrameCandidate:
    timestamp_sec: float
    frame_path: Optional[Path]
    total_score: float
    scene_score: float
    sharpness_score: float
    brightness_score: float


@dataclass
class SmartFrameSearchResult:
    mode: str
    candidates: List[SmartFrameCandidate]
    search_start_sec: float
    search_end_sec: float


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def sanitize_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value.strip())
    return safe or f"project_{now_stamp()}"


def parse_capture_times(raw_text: str) -> List[float]:
    values: List[float] = []
    for token in raw_text.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            values.append(float(token))
        except ValueError as exc:
            raise ProcessingError("input_error", f"Invalid capture time value: {token}") from exc
    if not values:
        raise ProcessingError("input_error", "Capture times are empty.")
    return values


def run_preflight_check(project_root: Path) -> dict:
    result = {
        "python_ok": True,
        "ffmpeg_ok": shutil.which("ffmpeg") is not None,
        "opencv_ok": True,
        "whisper_ok": True,
        "project_root_writable": True,
    }

    try:
        import cv2 as _cv2  # noqa: F401
    except Exception:
        result["opencv_ok"] = False

    try:
        import whisper as _whisper  # noqa: F401
    except Exception:
        result["whisper_ok"] = False

    try:
        project_root.mkdir(parents=True, exist_ok=True)
        test_file = project_root / "__write_test__.tmp"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
    except Exception:
        result["project_root_writable"] = False

    return result


def validate_video_file(video_path: Path) -> None:
    if not video_path.exists():
        raise ProcessingError("input_error", "File not found.")
    if not video_path.is_file():
        raise ProcessingError("input_error", "Selected path is not a file.")
    if video_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ProcessingError(
            "input_error",
            f"Unsupported file format. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )


def ensure_ffmpeg_available() -> None:
    if not shutil.which("ffmpeg"):
        raise ProcessingError("ffmpeg_error", "FFmpeg is not installed or not available in PATH.")


def run_ffmpeg(args: List[str], logger: logging.Logger) -> None:
    ensure_ffmpeg_available()
    full_cmd = ["ffmpeg", "-y", *args]
    logger.info("Running FFmpeg: %s", " ".join(full_cmd))
    try:
        process = subprocess.run(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        if process.stderr:
            logger.info(process.stderr.strip())
    except FileNotFoundError as exc:
        raise ProcessingError("ffmpeg_error", "FFmpeg executable was not found.") from exc
    except subprocess.CalledProcessError as exc:
        logger.error("FFmpeg failed: %s", exc.stderr.strip() if exc.stderr else str(exc))
        raise ProcessingError("ffmpeg_error", "Video processing failed while running FFmpeg.") from exc


def create_project_structure(base_directory: Path, project_name: str) -> ProjectPaths:
    root = base_directory / sanitize_name(project_name)
    if root.exists():
        root = base_directory / f"{sanitize_name(project_name)}_{now_stamp()}"

    input_dir = root / "input"
    audio_dir = root / "audio"
    subtitles_dir = root / "subtitles"
    thumbnails_dir = root / "thumbnails"
    output_dir = root / "output"
    logs_dir = root / "logs"

    try:
        for folder in [root, input_dir, audio_dir, subtitles_dir, thumbnails_dir, output_dir, logs_dir]:
            folder.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise ProcessingError("file_write_error", "Permission denied while creating project folders.") from exc

    return ProjectPaths(
        root=root,
        input_dir=input_dir,
        audio_dir=audio_dir,
        subtitles_dir=subtitles_dir,
        thumbnails_dir=thumbnails_dir,
        output_dir=output_dir,
        logs_dir=logs_dir,
    )


def setup_logger(log_file: Path) -> logging.Logger:
    logger = logging.getLogger(f"video_automation_{log_file.stem}_{now_stamp()}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(LOG_FORMAT)

    try:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except PermissionError:
        pass

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


def copy_input_video(video_path: Path, input_dir: Path) -> Path:
    destination = input_dir / video_path.name
    try:
        shutil.copy2(video_path, destination)
    except PermissionError as exc:
        raise ProcessingError("file_write_error", "Permission denied while copying the input video.") from exc
    return destination


def get_video_duration(video_path: Path) -> float:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        cap.release()
        raise ProcessingError("input_error", "Could not open the input video.")

    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
    cap.release()

    if fps <= 0 or frame_count <= 0:
        return 0.0
    return frame_count / fps


def capture_frames(video_path: Path, timestamps: List[float], output_dir: Path, logger: logging.Logger) -> List[Path]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        cap.release()
        raise ProcessingError("input_error", "Could not open video for frame extraction.")

    duration = get_video_duration(video_path)
    saved_paths: List[Path] = []

    for index, second in enumerate(timestamps, start=1):
        if second < 0:
            logger.warning("Skipping negative timestamp: %s", second)
            continue
        if duration and second > duration:
            logger.warning("Skipping timestamp beyond duration: %s > %s", second, duration)
            continue

        cap.set(cv2.CAP_PROP_POS_MSEC, second * 1000)
        success, frame = cap.read()
        if not success or frame is None:
            logger.warning("Failed to capture frame at %.2f seconds", second)
            continue

        file_path = output_dir / f"thumb_{index:02d}_{int(second)}s.jpg"
        try:
            cv2.imwrite(str(file_path), frame)
        except Exception as exc:
            logger.warning("Failed to save frame: %s", exc)
            continue
        saved_paths.append(file_path)
        logger.info("Saved frame: %s", file_path)

    cap.release()

    if not saved_paths:
        raise ProcessingError("input_error", "No frames could be extracted from the video.")

    return saved_paths


def extract_audio(video_path: Path, audio_dir: Path, logger: logging.Logger) -> Path:
    audio_path = audio_dir / "audio.mp3"
    run_ffmpeg(["-i", str(video_path), "-vn", "-acodec", "mp3", str(audio_path)], logger)
    if not audio_path.exists():
        raise ProcessingError("ffmpeg_error", "Audio extraction failed.")
    return audio_path


def format_seconds_to_srt(seconds: float) -> str:
    millis = int(round((seconds % 1) * 1000))
    whole = int(seconds)
    hours = whole // 3600
    minutes = (whole % 3600) // 60
    secs = whole % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt_from_segments(segments: List[dict], output_path: Path) -> None:
    lines: List[str] = []
    for idx, segment in enumerate(segments, start=1):
        start = format_seconds_to_srt(float(segment["start"]))
        end = format_seconds_to_srt(float(segment["end"]))
        text = str(segment["text"]).strip()
        lines.extend([str(idx), f"{start} --> {end}", text, ""])
    try:
        output_path.write_text("\n".join(lines), encoding="utf-8")
    except PermissionError as exc:
        raise ProcessingError("file_write_error", "Permission denied while saving subtitle SRT.") from exc


def generate_subtitles(audio_path: Path, subtitles_dir: Path, logger: logging.Logger) -> tuple[Path, Path]:
    try:
        import whisper
    except ImportError as exc:
        raise ProcessingError("whisper_error", "Whisper is not installed. Run: pip install openai-whisper") from exc

    logger.info("Loading Whisper model...")
    try:
        model = whisper.load_model("base")
    except Exception as exc:
        logger.error("Whisper model load failed: %s", str(exc))
        raise ProcessingError("whisper_error", "Could not load the Whisper model.") from exc

    logger.info("Transcribing audio...")
    try:
        result = model.transcribe(str(audio_path))
    except Exception as exc:
        logger.error("Whisper transcription failed: %s", str(exc))
        raise ProcessingError("whisper_error", "Subtitle generation failed.") from exc

    text_path = subtitles_dir / "subtitle.txt"
    srt_path = subtitles_dir / "subtitle.srt"

    try:
        text_path.write_text(result.get("text", "").strip(), encoding="utf-8")
    except PermissionError as exc:
        raise ProcessingError("file_write_error", "Permission denied while saving subtitle text.") from exc

    segments = result.get("segments", [])
    write_srt_from_segments(segments, srt_path)

    metadata_path = subtitles_dir / "subtitle_metadata.json"
    try:
        metadata_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except PermissionError as exc:
        raise ProcessingError("file_write_error", "Permission denied while saving subtitle metadata.") from exc

    logger.info("Subtitle files created.")
    return text_path, srt_path


def edit_subtitle_text(updated_text: str, subtitles_dir: Path, logger: logging.Logger) -> Path:
    text_path = subtitles_dir / "subtitle.txt"
    try:
        text_path.write_text(updated_text, encoding="utf-8")
    except PermissionError as exc:
        raise ProcessingError("file_write_error", "Permission denied while saving subtitle text.") from exc
    logger.info("Subtitle text updated.")
    return text_path


def regenerate_srt_from_text(updated_text: str, subtitles_dir: Path, logger: logging.Logger) -> Path:
    srt_path = subtitles_dir / "subtitle.srt"
    srt_content = [
        "1",
        "00:00:00,000 --> 00:10:00,000",
        updated_text.strip(),
        "",
    ]
    try:
        srt_path.write_text("\n".join(srt_content), encoding="utf-8")
    except PermissionError as exc:
        raise ProcessingError("file_write_error", "Permission denied while saving subtitle SRT.") from exc
    logger.info("Subtitle SRT regenerated from edited text.")
    return srt_path


def select_thumbnail(image_list: List[Path], selected_index: int, output_dir: Path, logger: logging.Logger) -> Path:
    if selected_index < 0 or selected_index >= len(image_list):
        raise ProcessingError("input_error", "Invalid thumbnail selection.")
    source = image_list[selected_index]
    destination = output_dir / "selected_thumbnail.jpg"
    try:
        shutil.copy2(source, destination)
    except PermissionError as exc:
        raise ProcessingError("file_write_error", "Permission denied while saving the selected thumbnail.") from exc
    logger.info("Selected thumbnail: %s", destination)
    return destination


def trim_video(video_path: Path, start_time: float, end_time: float, output_dir: Path, logger: logging.Logger) -> Path:
    if start_time < 0 or end_time < 0:
        raise ProcessingError("input_error", "Trim values must be zero or greater.")

    duration = get_video_duration(video_path)
    if duration and start_time + end_time >= duration:
        raise ProcessingError("input_error", "Trim values remove the entire video.")

    output_path = output_dir / "trimmed_video.mp4"
    cmd = ["-i", str(video_path)]

    if start_time > 0:
        cmd = ["-ss", str(start_time), *cmd]

    if end_time > 0 and duration > 0:
        trimmed_duration = max(duration - start_time - end_time, 0.1)
        cmd.extend(["-t", str(trimmed_duration)])

    cmd.extend(["-c:v", "libx264", "-c:a", "aac", str(output_path)])
    run_ffmpeg(cmd, logger)

    if not output_path.exists():
        raise ProcessingError("ffmpeg_error", "Trim operation failed.")
    return output_path


def adjust_volume(video_path: Path, volume_ratio: float, output_dir: Path, logger: logging.Logger) -> Path:
    if volume_ratio <= 0:
        raise ProcessingError("input_error", "Volume ratio must be greater than zero.")
    output_path = output_dir / "volume_adjusted_video.mp4"
    run_ffmpeg(
        [
            "-i",
            str(video_path),
            "-filter:a",
            f"volume={volume_ratio}",
            "-c:v",
            "copy",
            str(output_path),
        ],
        logger,
    )
    if not output_path.exists():
        raise ProcessingError("ffmpeg_error", "Volume adjustment failed.")
    return output_path


def export_video(source_video_path: Path, output_dir: Path, logger: logging.Logger, subtitle_path: Optional[Path] = None) -> Path:
    final_path = output_dir / "final_video.mp4"

    if subtitle_path and subtitle_path.exists():
        logger.info("Subtitle file prepared separately: %s", subtitle_path)

    if not source_video_path.exists():
        raise ProcessingError("input_error", "Final export source video does not exist.")

    try:
        shutil.copy2(source_video_path, final_path)
    except PermissionError as exc:
        raise ProcessingError("file_write_error", "Permission denied while exporting the final video.") from exc

    logger.info("Final video exported: %s", final_path)
    return final_path


def validate_smart_frame_request(request: SmartFrameSearchRequest) -> None:
    validate_video_file(request.video_path)

    if request.search_mode not in {"auto", "range"}:
        raise ProcessingError("smart_search_error", "Invalid search mode.")
    if request.result_count <= 0:
        raise ProcessingError("smart_search_error", "Result count must be greater than zero.")
    if request.sampling_interval_sec <= 0:
        raise ProcessingError("smart_search_error", "Sampling interval must be greater than zero.")
    if request.min_frame_gap_sec < 0:
        raise ProcessingError("smart_search_error", "Minimum frame gap must be zero or greater.")

    if request.search_mode == "range":
        if request.target_time_sec is None:
            raise ProcessingError("smart_search_error", "Target time is required for range mode.")
        if request.search_window_sec is None or request.search_window_sec <= 0:
            raise ProcessingError("smart_search_error", "Search window must be greater than zero.")


def resolve_search_range(request: SmartFrameSearchRequest, duration_sec: float) -> tuple[float, float]:
    if request.search_mode == "auto":
        return 0.0, duration_sec

    target = float(request.target_time_sec or 0.0)
    if target > duration_sec:
        raise ProcessingError("smart_search_error", "Target time exceeds video duration.")

    half_window = float(request.search_window_sec or 0.0) / 2.0
    start_sec = max(0.0, target - half_window)
    end_sec = min(duration_sec, target + half_window)

    if start_sec >= end_sec:
        raise ProcessingError("smart_search_error", "Invalid search range.")

    return start_sec, end_sec


def sample_frames_in_range(video_path: Path, start_sec: float, end_sec: float, sampling_interval_sec: float) -> List[FrameSample]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        cap.release()
        raise ProcessingError("smart_search_error", "Could not open video for smart frame search.")

    samples: List[FrameSample] = []
    current_sec = start_sec

    while current_sec <= end_sec:
        cap.set(cv2.CAP_PROP_POS_MSEC, current_sec * 1000)
        success, frame = cap.read()
        if success and frame is not None:
            samples.append(FrameSample(timestamp_sec=current_sec, frame_image=frame))
        current_sec += sampling_interval_sec

    cap.release()

    if not samples:
        raise ProcessingError("smart_search_error", "No frames were sampled in the selected range.")

    return samples


def calculate_scene_change_scores(samples: List[FrameSample], scene_threshold: float) -> List[float]:
    scores: List[float] = []
    prev_gray = None

    for sample in samples:
        gray = cv2.cvtColor(sample.frame_image, cv2.COLOR_BGR2GRAY)
        if prev_gray is None:
            scores.append(0.0)
        else:
            diff = cv2.absdiff(prev_gray, gray)
            raw_score = float(diff.mean())
            norm_score = min(raw_score / max(scene_threshold, 1.0), 1.0)
            scores.append(norm_score)
        prev_gray = gray

    return scores


def calculate_sharpness_score(frame_image) -> float:
    gray = cv2.cvtColor(frame_image, cv2.COLOR_BGR2GRAY)
    value = cv2.Laplacian(gray, cv2.CV_64F).var()
    return float(min(value / 1000.0, 1.0))


def calculate_brightness_score(frame_image) -> float:
    gray = cv2.cvtColor(frame_image, cv2.COLOR_BGR2GRAY)
    mean_val = float(gray.mean())
    score = 1.0 - abs(mean_val - 128.0) / 128.0
    return max(0.0, min(score, 1.0))


def build_frame_scores(samples: List[FrameSample], scene_scores: List[float], use_sharpness_filter: bool, use_brightness_filter: bool) -> List[SmartFrameCandidate]:
    candidates: List[SmartFrameCandidate] = []

    for idx, sample in enumerate(samples):
        scene_score = scene_scores[idx]
        sharpness_score = calculate_sharpness_score(sample.frame_image) if use_sharpness_filter else 0.0
        brightness_score = calculate_brightness_score(sample.frame_image) if use_brightness_filter else 0.0
        total_score = scene_score * 0.4 + sharpness_score * 0.4 + brightness_score * 0.2

        candidates.append(
            SmartFrameCandidate(
                timestamp_sec=sample.timestamp_sec,
                frame_path=None,
                total_score=total_score,
                scene_score=scene_score,
                sharpness_score=sharpness_score,
                brightness_score=brightness_score,
            )
        )

    return candidates


def filter_by_min_gap(candidates: List[SmartFrameCandidate], min_frame_gap_sec: float) -> List[SmartFrameCandidate]:
    ordered = sorted(candidates, key=lambda x: x.total_score, reverse=True)
    filtered: List[SmartFrameCandidate] = []

    for candidate in ordered:
        if all(abs(candidate.timestamp_sec - kept.timestamp_sec) >= min_frame_gap_sec for kept in filtered):
            filtered.append(candidate)

    return filtered


def select_top_candidates(candidates: List[SmartFrameCandidate], result_count: int) -> List[SmartFrameCandidate]:
    ordered = sorted(candidates, key=lambda x: x.total_score, reverse=True)
    return ordered[:result_count]


def save_candidate_frames(samples: List[FrameSample], candidates: List[SmartFrameCandidate], output_dir: Path, mode: str) -> List[SmartFrameCandidate]:
    timestamp_to_sample = {round(s.timestamp_sec, 3): s for s in samples}
    saved_candidates: List[SmartFrameCandidate] = []

    for idx, candidate in enumerate(candidates, start=1):
        sample = timestamp_to_sample.get(round(candidate.timestamp_sec, 3))
        if sample is None:
            continue

        file_path = output_dir / f"smart_{mode}_{idx:02d}_{int(candidate.timestamp_sec)}s.jpg"
        try:
            cv2.imwrite(str(file_path), sample.frame_image)
        except Exception:
            continue

        saved_candidates.append(
            SmartFrameCandidate(
                timestamp_sec=candidate.timestamp_sec,
                frame_path=file_path,
                total_score=candidate.total_score,
                scene_score=candidate.scene_score,
                sharpness_score=candidate.sharpness_score,
                brightness_score=candidate.brightness_score,
            )
        )

    if not saved_candidates:
        raise ProcessingError("smart_search_error", "No good frame was found in the selected range. Try a wider search window.")

    return saved_candidates


def run_smart_frame_search(request: SmartFrameSearchRequest, output_dir: Path, logger: logging.Logger) -> SmartFrameSearchResult:
    validate_smart_frame_request(request)

    duration_sec = get_video_duration(request.video_path)
    if duration_sec <= 0:
        raise ProcessingError("smart_search_error", "Could not determine video duration.")

    start_sec, end_sec = resolve_search_range(request, duration_sec)
    logger.info("Smart frame search range: %.2f ~ %.2f", start_sec, end_sec)

    samples = sample_frames_in_range(request.video_path, start_sec, end_sec, request.sampling_interval_sec)
    scene_scores = calculate_scene_change_scores(samples, request.scene_threshold)

    candidates = build_frame_scores(
        samples=samples,
        scene_scores=scene_scores,
        use_sharpness_filter=request.use_sharpness_filter,
        use_brightness_filter=request.use_brightness_filter,
    )

    candidates = filter_by_min_gap(candidates, request.min_frame_gap_sec)
    candidates = select_top_candidates(candidates, request.result_count)

    if not candidates:
        raise ProcessingError("smart_search_error", "No good frame was found in the selected range. Try a wider search window.")

    saved_candidates = save_candidate_frames(samples, candidates, output_dir, request.search_mode)

    return SmartFrameSearchResult(
        mode=request.search_mode,
        candidates=saved_candidates,
        search_start_sec=start_sec,
        search_end_sec=end_sec,
    )


class WorkflowRunner(QThread):
    progress = Signal(int)
    status = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        video_path: str,
        project_base_dir: str,
        trim_start: float,
        trim_end: float,
        volume_ratio: float,
        capture_times_text: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.video_path = Path(video_path)
        self.project_base_dir = Path(project_base_dir)
        self.trim_start = trim_start
        self.trim_end = trim_end
        self.volume_ratio = volume_ratio
        self.capture_times_text = capture_times_text

    def run(self) -> None:
        try:
            self.progress.emit(5)
            validate_video_file(self.video_path)

            self.status.emit("Creating project structure...")
            project_name = self.video_path.stem
            project_paths = create_project_structure(self.project_base_dir, project_name)

            logger = setup_logger(project_paths.logs_dir / "run.log")
            logger.info("Workflow started.")
            logger.info("Input video: %s", self.video_path)

            self.status.emit("Copying input video...")
            self.progress.emit(10)
            input_video_path = copy_input_video(self.video_path, project_paths.input_dir)

            self.status.emit("Extracting frames...")
            self.progress.emit(25)
            capture_times = parse_capture_times(self.capture_times_text)
            frames = capture_frames(input_video_path, capture_times, project_paths.thumbnails_dir, logger)

            self.status.emit("Extracting audio...")
            self.progress.emit(40)
            audio_path = extract_audio(input_video_path, project_paths.audio_dir, logger)

            self.status.emit("Generating subtitles with Whisper...")
            self.progress.emit(60)
            text_path, srt_path = generate_subtitles(audio_path, project_paths.subtitles_dir, logger)

            current_video = input_video_path

            if self.trim_start > 0 or self.trim_end > 0:
                self.status.emit("Applying trim...")
                self.progress.emit(75)
                current_video = trim_video(current_video, self.trim_start, self.trim_end, project_paths.output_dir, logger)

            if abs(self.volume_ratio - 1.0) > 1e-9:
                self.status.emit("Adjusting volume...")
                self.progress.emit(85)
                current_video = adjust_volume(current_video, self.volume_ratio, project_paths.output_dir, logger)

            self.status.emit("Exporting final video...")
            self.progress.emit(92)
            logger.info("Final export source: %s", current_video)
            final_video_path = export_video(current_video, project_paths.output_dir, logger, srt_path)

            logger.info("Workflow completed successfully.")
            self.progress.emit(100)
            self.status.emit("Completed.")

            result = WorkflowResult(
                project_paths=project_paths,
                input_video_path=input_video_path,
                captured_frames=frames,
                audio_path=audio_path,
                text_path=text_path,
                srt_path=srt_path,
                selected_thumbnail=None,
                final_video_path=final_video_path,
            )
            self.finished_ok.emit(result)

        except UserVisibleError as exc:
            if isinstance(exc, ProcessingError):
                self.failed.emit(f"[{exc.category}] {exc}")
            else:
                self.failed.emit(str(exc))
        except Exception:
            self.failed.emit("[unexpected_error] An unexpected error occurred.\n\n" + traceback.format_exc())


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1380, 860)

        self.original_video_path: Optional[Path] = None
        self.project_video_path: Optional[Path] = None
        self.output_base_dir: Path = Path.cwd() / "video_projects"
        self.output_base_dir.mkdir(parents=True, exist_ok=True)

        self.workflow_result: Optional[WorkflowResult] = None
        self.current_selected_thumbnail: Optional[Path] = None
        self.worker: Optional[WorkflowRunner] = None

        self._build_ui()
        self._update_search_mode_ui()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)

        status_box = QGroupBox("Dashboard")
        status_layout = QGridLayout(status_box)

        self.lbl_total_processed = QLabel("Total Processed: 0")
        self.lbl_last_status = QLabel("Last Status: Idle")
        self.lbl_error_count = QLabel("Error Count: 0")
        self.btn_preflight = QPushButton("Run Preflight Check")
        self.btn_preflight.clicked.connect(self.on_run_preflight_check)
        self.btn_generate = QPushButton("Generate")
        self.btn_generate.clicked.connect(self.on_generate_clicked)

        status_layout.addWidget(self.lbl_total_processed, 0, 0)
        status_layout.addWidget(self.lbl_last_status, 0, 1)
        status_layout.addWidget(self.lbl_error_count, 0, 2)
        status_layout.addWidget(self.btn_preflight, 0, 3)
        status_layout.addWidget(self.btn_generate, 0, 4)
        main_layout.addWidget(status_box)

        middle_layout = QHBoxLayout()

        left_box = QGroupBox("Input")
        left_layout = QVBoxLayout(left_box)

        self.btn_select_video = QPushButton("Select Video")
        self.btn_select_video.clicked.connect(self.on_select_video)

        self.btn_select_project_dir = QPushButton("Select Project Root")
        self.btn_select_project_dir.clicked.connect(self.on_select_project_root)

        self.txt_video_path = QLineEdit()
        self.txt_video_path.setReadOnly(True)
        self.txt_video_path.setPlaceholderText("No video selected")

        self.txt_project_root = QLineEdit(str(self.output_base_dir))
        self.txt_project_root.setReadOnly(True)

        left_layout.addWidget(self.btn_select_video)
        left_layout.addWidget(self.txt_video_path)
        left_layout.addWidget(self.btn_select_project_dir)
        left_layout.addWidget(self.txt_project_root)

        options_box = QGroupBox("Minimal Editing")
        options_layout = QGridLayout(options_box)

        self.spin_trim_start = QDoubleSpinBox()
        self.spin_trim_start.setRange(0, 36000)
        self.spin_trim_start.setDecimals(2)
        self.spin_trim_start.setSuffix(" sec")

        self.spin_trim_end = QDoubleSpinBox()
        self.spin_trim_end.setRange(0, 36000)
        self.spin_trim_end.setDecimals(2)
        self.spin_trim_end.setSuffix(" sec")

        self.slider_volume = QSlider(Qt.Orientation.Horizontal)
        self.slider_volume.setRange(1, 300)
        self.slider_volume.setValue(100)
        self.lbl_volume_value = QLabel("1.00x")
        self.slider_volume.valueChanged.connect(self.on_volume_changed)

        self.txt_capture_times = QLineEdit("3,10,20")

        options_layout.addWidget(QLabel("Trim Start"), 0, 0)
        options_layout.addWidget(self.spin_trim_start, 0, 1)
        options_layout.addWidget(QLabel("Trim End"), 1, 0)
        options_layout.addWidget(self.spin_trim_end, 1, 1)
        options_layout.addWidget(QLabel("Volume"), 2, 0)
        options_layout.addWidget(self.slider_volume, 2, 1)
        options_layout.addWidget(self.lbl_volume_value, 2, 2)
        options_layout.addWidget(QLabel("Capture Times"), 3, 0)
        options_layout.addWidget(self.txt_capture_times, 3, 1, 1, 2)

        left_layout.addWidget(options_box)

        smart_box = QGroupBox("Smart Frame Search")
        smart_layout = QGridLayout(smart_box)

        self.combo_search_mode = QComboBox()
        self.combo_search_mode.addItems(["Auto Best Frames", "Guided Range Search"])
        self.combo_search_mode.currentIndexChanged.connect(self._update_search_mode_ui)

        self.spin_result_count = QDoubleSpinBox()
        self.spin_result_count.setRange(1, 10)
        self.spin_result_count.setDecimals(0)
        self.spin_result_count.setValue(3)

        self.spin_sampling_interval = QDoubleSpinBox()
        self.spin_sampling_interval.setRange(0.1, 10.0)
        self.spin_sampling_interval.setDecimals(2)
        self.spin_sampling_interval.setValue(0.5)
        self.spin_sampling_interval.setSuffix(" sec")

        self.spin_min_frame_gap = QDoubleSpinBox()
        self.spin_min_frame_gap.setRange(0.0, 30.0)
        self.spin_min_frame_gap.setDecimals(2)
        self.spin_min_frame_gap.setValue(1.5)
        self.spin_min_frame_gap.setSuffix(" sec")

        self.spin_target_time = QDoubleSpinBox()
        self.spin_target_time.setRange(0.0, 36000.0)
        self.spin_target_time.setDecimals(2)
        self.spin_target_time.setValue(0.0)
        self.spin_target_time.setSuffix(" sec")

        self.spin_search_window = QDoubleSpinBox()
        self.spin_search_window.setRange(1.0, 60.0)
        self.spin_search_window.setDecimals(2)
        self.spin_search_window.setValue(6.0)
        self.spin_search_window.setSuffix(" sec")

        self.btn_find_best_frames = QPushButton("Find Best Frames")
        self.btn_find_best_frames.clicked.connect(self.on_find_best_frames)

        self.btn_refine_by_range = QPushButton("Refine by Range")
        self.btn_refine_by_range.clicked.connect(self.on_refine_by_range)

        smart_layout.addWidget(QLabel("Search Mode"), 0, 0)
        smart_layout.addWidget(self.combo_search_mode, 0, 1)
        smart_layout.addWidget(QLabel("Result Count"), 1, 0)
        smart_layout.addWidget(self.spin_result_count, 1, 1)
        smart_layout.addWidget(QLabel("Sampling Interval"), 2, 0)
        smart_layout.addWidget(self.spin_sampling_interval, 2, 1)
        smart_layout.addWidget(QLabel("Min Frame Gap"), 3, 0)
        smart_layout.addWidget(self.spin_min_frame_gap, 3, 1)
        smart_layout.addWidget(QLabel("Target Time"), 4, 0)
        smart_layout.addWidget(self.spin_target_time, 4, 1)
        smart_layout.addWidget(QLabel("Search Window"), 5, 0)
        smart_layout.addWidget(self.spin_search_window, 5, 1)
        smart_layout.addWidget(self.btn_find_best_frames, 6, 0)
        smart_layout.addWidget(self.btn_refine_by_range, 6, 1)

        left_layout.addWidget(smart_box)
        middle_layout.addWidget(left_box, 1)

        center_box = QGroupBox("Processing")
        center_layout = QVBoxLayout(center_box)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)

        self.lbl_processing_status = QLabel("Idle")
        self.lbl_processing_status.setWordWrap(True)

        self.thumbnail_list = QListWidget()
        self.thumbnail_list.currentRowChanged.connect(self.on_thumbnail_selected)

        self.thumbnail_preview = QLabel("No thumbnail selected")
        self.thumbnail_preview.setMinimumHeight(260)
        self.thumbnail_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_preview.setStyleSheet("border: 1px solid #999;")

        center_layout.addWidget(QLabel("Processing Status"))
        center_layout.addWidget(self.lbl_processing_status)
        center_layout.addWidget(self.progress_bar)
        center_layout.addWidget(QLabel("Candidate Frames"))
        center_layout.addWidget(self.thumbnail_list)
        center_layout.addWidget(self.thumbnail_preview)

        middle_layout.addWidget(center_box, 1)

        right_box = QGroupBox("Subtitle and Logs")
        right_layout = QVBoxLayout(right_box)

        self.subtitle_editor = QPlainTextEdit()
        self.subtitle_editor.setPlaceholderText("Subtitle text will appear here after generation")

        self.btn_apply_subtitle = QPushButton("Apply Subtitle Changes")
        self.btn_apply_subtitle.clicked.connect(self.on_apply_subtitle_changes)
        self.btn_apply_subtitle.setEnabled(False)

        self.btn_select_thumbnail_save = QPushButton("Save Selected Thumbnail")
        self.btn_select_thumbnail_save.clicked.connect(self.on_save_selected_thumbnail)
        self.btn_select_thumbnail_save.setEnabled(False)

        self.combo_preset = QComboBox()
        self.combo_preset.addItems(["Standard", "Smart Frames Only", "Subtitle First", "Quick Preview"])
        self.combo_preset.currentIndexChanged.connect(self.on_preset_changed)

        self.btn_open_output = QPushButton("Open Output Folder")
        self.btn_open_output.clicked.connect(self.on_open_output_folder)
        self.btn_open_output.setEnabled(False)

        self.btn_export = QPushButton("Export")
        self.btn_export.clicked.connect(self.on_export_clicked)
        self.btn_export.setEnabled(False)

        self.logs_editor = QTextEdit()
        self.logs_editor.setReadOnly(True)

        right_layout.addWidget(QLabel("Subtitle Preview"))
        right_layout.addWidget(self.subtitle_editor)
        right_layout.addWidget(QLabel("Preset"))
        right_layout.addWidget(self.combo_preset)
        right_layout.addWidget(self.btn_apply_subtitle)
        right_layout.addWidget(self.btn_select_thumbnail_save)
        right_layout.addWidget(self.btn_open_output)
        right_layout.addWidget(self.btn_export)
        right_layout.addWidget(QLabel("Logs"))
        right_layout.addWidget(self.logs_editor)

        middle_layout.addWidget(right_box, 1)
        main_layout.addLayout(middle_layout)

    def append_log(self, text: str) -> None:
        self.logs_editor.append(text)

    def _get_counter_from_label(self, label: QLabel) -> int:
        try:
            return int(label.text().split(":")[-1].strip())
        except ValueError:
            return 0

    def _update_search_mode_ui(self) -> None:
        is_range = self.combo_search_mode.currentText() == "Guided Range Search"
        self.spin_target_time.setEnabled(is_range)
        self.spin_search_window.setEnabled(is_range)
        self.btn_find_best_frames.setEnabled(not is_range)
        self.btn_refine_by_range.setEnabled(is_range)

    def on_run_preflight_check(self) -> None:
        result = run_preflight_check(self.output_base_dir)
        lines = [f"{key}: {'OK' if value else 'FAIL'}" for key, value in result.items()]
        QMessageBox.information(self, APP_NAME, "\n".join(lines))
        self.append_log("Preflight check executed.")

    def on_preset_changed(self) -> None:
        preset = self.combo_preset.currentText()
        if preset == "Standard":
            self.spin_trim_start.setValue(0.0)
            self.spin_trim_end.setValue(0.0)
            self.slider_volume.setValue(100)
            self.txt_capture_times.setText("3,10,20")
        elif preset == "Smart Frames Only":
            self.spin_trim_start.setValue(0.0)
            self.spin_trim_end.setValue(0.0)
            self.slider_volume.setValue(100)
            self.txt_capture_times.setText("3,10,20")
        elif preset == "Subtitle First":
            self.spin_trim_start.setValue(0.0)
            self.spin_trim_end.setValue(0.0)
            self.slider_volume.setValue(100)
            self.txt_capture_times.setText("5,15,30")
        elif preset == "Quick Preview":
            self.spin_trim_start.setValue(0.0)
            self.spin_trim_end.setValue(0.0)
            self.slider_volume.setValue(100)
            self.txt_capture_times.setText("2,5,8")

    def on_open_output_folder(self) -> None:
        if not self.workflow_result:
            QMessageBox.warning(self, APP_NAME, "No active project found.")
            return
        output_dir = self.workflow_result.project_paths.output_dir
        if not output_dir.exists():
            QMessageBox.warning(self, APP_NAME, "Output folder does not exist.")
            return
        try:
            os.startfile(str(output_dir))
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Could not open output folder.\n\n{exc}")

    def on_volume_changed(self, value: int) -> None:
        self.lbl_volume_value.setText(f"{value / 100:.2f}x")

    def on_select_video(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Video", str(Path.home()), "Video Files (*.mp4 *.mov *.avi *.mkv)")
        if not file_path:
            return
        self.original_video_path = Path(file_path)
        self.txt_video_path.setText(str(self.original_video_path))
        self.append_log(f"Selected video: {self.original_video_path}")

    def on_select_project_root(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select Project Root", str(self.output_base_dir))
        if not directory:
            return
        self.output_base_dir = Path(directory)
        self.txt_project_root.setText(str(self.output_base_dir))
        self.append_log(f"Project root set to: {self.output_base_dir}")

    def _ensure_project_for_smart_search(self) -> tuple[ProjectPaths, logging.Logger, Path]:
        if self.workflow_result:
            project_paths = self.workflow_result.project_paths
            working_video = self.workflow_result.input_video_path
            self.project_video_path = working_video
        else:
            if not self.original_video_path:
                raise ProcessingError("input_error", "Please select a video first.")
            project_paths = create_project_structure(self.output_base_dir, self.original_video_path.stem)
            working_video = copy_input_video(self.original_video_path, project_paths.input_dir)
            self.project_video_path = working_video

        logger = setup_logger(project_paths.logs_dir / "run.log")
        return project_paths, logger, working_video

    def _render_smart_candidates(self, result: SmartFrameSearchResult) -> None:
        self.thumbnail_list.clear()
        self.thumbnail_preview.setText("No thumbnail selected")
        self.thumbnail_preview.setPixmap(QPixmap())
        self.current_selected_thumbnail = None

        for candidate in result.candidates:
            if not candidate.frame_path:
                continue
            label = f"{candidate.frame_path.name} | {candidate.timestamp_sec:.2f}s | score={candidate.total_score:.2f}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(candidate.frame_path))
            self.thumbnail_list.addItem(item)

        if result.candidates:
            self.thumbnail_list.setCurrentRow(0)

        self.append_log(
            f"Smart frame search completed: mode={result.mode}, "
            f"range={result.search_start_sec:.2f}~{result.search_end_sec:.2f}, "
            f"candidates={len(result.candidates)}"
        )

    def on_find_best_frames(self) -> None:
        try:
            project_paths, logger, working_video = self._ensure_project_for_smart_search()
            request = SmartFrameSearchRequest(
                video_path=working_video,
                search_mode="auto",
                result_count=int(self.spin_result_count.value()),
                sampling_interval_sec=float(self.spin_sampling_interval.value()),
                min_frame_gap_sec=float(self.spin_min_frame_gap.value()),
            )
            result = run_smart_frame_search(request, project_paths.thumbnails_dir, logger)
            self._render_smart_candidates(result)
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            self.append_log(f"Smart frame search failed: {exc}")

    def on_refine_by_range(self) -> None:
        try:
            project_paths, logger, working_video = self._ensure_project_for_smart_search()
            request = SmartFrameSearchRequest(
                video_path=working_video,
                search_mode="range",
                result_count=int(self.spin_result_count.value()),
                sampling_interval_sec=float(self.spin_sampling_interval.value()),
                min_frame_gap_sec=float(self.spin_min_frame_gap.value()),
                target_time_sec=float(self.spin_target_time.value()),
                search_window_sec=float(self.spin_search_window.value()),
            )
            result = run_smart_frame_search(request, project_paths.thumbnails_dir, logger)
            self._render_smart_candidates(result)
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            self.append_log(f"Smart frame refine failed: {exc}")

    def on_generate_clicked(self) -> None:
        if not self.original_video_path:
            QMessageBox.warning(self, APP_NAME, "Please select a video first.")
            return

        self.btn_generate.setEnabled(False)
        self.progress_bar.setValue(0)
        self.lbl_processing_status.setText("Starting...")
        self.thumbnail_list.clear()
        self.thumbnail_preview.setText("No thumbnail selected")
        self.thumbnail_preview.setPixmap(QPixmap())
        self.subtitle_editor.clear()
        self.btn_apply_subtitle.setEnabled(False)
        self.btn_select_thumbnail_save.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.workflow_result = None
        self.current_selected_thumbnail = None

        self.worker = WorkflowRunner(
            video_path=str(self.original_video_path),
            project_base_dir=str(self.output_base_dir),
            trim_start=float(self.spin_trim_start.value()),
            trim_end=float(self.spin_trim_end.value()),
            volume_ratio=float(self.slider_volume.value() / 100.0),
            capture_times_text=self.txt_capture_times.text(),
        )
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.status.connect(self.lbl_processing_status.setText)
        self.worker.finished_ok.connect(self.on_workflow_success)
        self.worker.failed.connect(self.on_workflow_failure)
        self.worker.start()

    def on_workflow_success(self, result: WorkflowResult) -> None:
        self.workflow_result = result
        self.project_video_path = result.input_video_path
        self.btn_generate.setEnabled(True)
        self.btn_apply_subtitle.setEnabled(True)
        self.btn_select_thumbnail_save.setEnabled(True)
        self.btn_open_output.setEnabled(True)
        self.btn_export.setEnabled(True)

        self.lbl_last_status.setText("Last Status: Success")
        self.append_log("Workflow completed successfully.")
        self.append_log(f"Project root: {result.project_paths.root}")
        self.append_log(f"Final video: {result.final_video_path}")
        self.append_log(f"Subtitle file: {result.srt_path}")

        total_processed = self._get_counter_from_label(self.lbl_total_processed) + 1
        self.lbl_total_processed.setText(f"Total Processed: {total_processed}")

        self.thumbnail_list.clear()
        for frame_path in result.captured_frames:
            item = QListWidgetItem(frame_path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(frame_path))
            self.thumbnail_list.addItem(item)

        if result.captured_frames:
            self.thumbnail_list.setCurrentRow(0)

        if result.text_path and result.text_path.exists():
            self.subtitle_editor.setPlainText(result.text_path.read_text(encoding="utf-8"))

    def on_workflow_failure(self, error_text: str) -> None:
        self.btn_generate.setEnabled(True)
        self.lbl_last_status.setText("Last Status: Error")
        error_count = self._get_counter_from_label(self.lbl_error_count) + 1
        self.lbl_error_count.setText(f"Error Count: {error_count}")
        self.append_log(error_text)
        QMessageBox.critical(self, APP_NAME, error_text)

    def on_thumbnail_selected(self, current_row: int) -> None:
        if current_row < 0:
            return
        item = self.thumbnail_list.item(current_row)
        if not item:
            return
        path_str = item.data(Qt.ItemDataRole.UserRole)
        if not path_str:
            return
        path = Path(path_str)
        self.current_selected_thumbnail = path
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.thumbnail_preview.setText("Preview unavailable")
            self.thumbnail_preview.setPixmap(QPixmap())
            return
        scaled = pixmap.scaled(self.thumbnail_preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.thumbnail_preview.setText("")
        self.thumbnail_preview.setPixmap(scaled)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.current_selected_thumbnail and self.current_selected_thumbnail.exists():
            self.on_thumbnail_selected(self.thumbnail_list.currentRow())

    def on_apply_subtitle_changes(self) -> None:
        if not self.workflow_result:
            QMessageBox.warning(self, APP_NAME, "No active project found.")
            return
        try:
            updated_text = self.subtitle_editor.toPlainText()
            log_file = self.workflow_result.project_paths.logs_dir / "run.log"
            logger = setup_logger(log_file)
            text_path = edit_subtitle_text(updated_text, self.workflow_result.project_paths.subtitles_dir, logger)
            srt_path = regenerate_srt_from_text(updated_text, self.workflow_result.project_paths.subtitles_dir, logger)
            self.workflow_result.text_path = text_path
            self.workflow_result.srt_path = srt_path
            self.append_log(f"Subtitle text updated: {text_path}")
            self.append_log(f"Subtitle SRT updated: {srt_path}")
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))

    def on_save_selected_thumbnail(self) -> None:
        if not self.workflow_result:
            QMessageBox.warning(self, APP_NAME, "No active project found.")
            return
        current_row = self.thumbnail_list.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, APP_NAME, "Please select a thumbnail first.")
            return

        try:
            log_file = self.workflow_result.project_paths.logs_dir / "run.log"
            logger = setup_logger(log_file)

            ui_paths: List[Path] = []
            for idx in range(self.thumbnail_list.count()):
                item = self.thumbnail_list.item(idx)
                if item:
                    path_str = item.data(Qt.ItemDataRole.UserRole)
                    if path_str:
                        ui_paths.append(Path(path_str))

            selected_path = select_thumbnail(ui_paths, current_row, self.workflow_result.project_paths.output_dir, logger)
            self.workflow_result.selected_thumbnail = selected_path
            self.append_log(f"Selected thumbnail saved: {selected_path}")
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))

    def on_export_clicked(self) -> None:
        if not self.workflow_result:
            QMessageBox.warning(self, APP_NAME, "No active project found.")
            return

        project = self.workflow_result.project_paths
        parts = [
            f"Project: {project.root}",
            f"Original Video: {self.original_video_path}",
            f"Project Video: {self.project_video_path}",
            f"Output Folder: {project.output_dir}",
            f"Final Video: {self.workflow_result.final_video_path} | Exists: {self.workflow_result.final_video_path.exists() if self.workflow_result.final_video_path else False}",
            f"Subtitle TXT: {self.workflow_result.text_path} | Exists: {self.workflow_result.text_path.exists() if self.workflow_result.text_path else False}",
            f"Subtitle SRT: {self.workflow_result.srt_path} | Exists: {self.workflow_result.srt_path.exists() if self.workflow_result.srt_path else False}",
            f"Selected Thumbnail: {self.workflow_result.selected_thumbnail or 'Not saved yet'}",
            f"Logs: {project.logs_dir / 'run.log'}",
        ]
        QMessageBox.information(self, APP_NAME, "\n".join(parts))
        self.append_log("Export summary displayed.")

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self,
                APP_NAME,
                "A task is still running. Do you want to exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return
        event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
