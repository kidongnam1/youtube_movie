"""
core/video.py — V2.1
[P0-1] cap.release() finally 보장 — 모든 VideoCapture에 try/finally 적용
[P0-5] get_video_duration() @lru_cache 적용
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import cv2

from core.models import ProcessingError
from config.settings import FFMPEG_VIDEO_CODEC, FFMPEG_AUDIO_CODEC, FFMPEG_PRESET, FFMPEG_CRF


def ensure_ffmpeg() -> None:
    if not shutil.which("ffmpeg"):
        raise ProcessingError(
            "ffmpeg_error",
            "FFmpeg가 설치되어 있지 않습니다.\nhttps://ffmpeg.org/download.html 에서 설치해주세요."
        )


def run_ffmpeg(args: List[str], logger: logging.Logger) -> None:
    ensure_ffmpeg()
    cmd = ["ffmpeg", "-y", *args]
    logger.info("FFmpeg 실행: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, check=True
        )
        if proc.stderr:
            logger.debug(proc.stderr.strip())
    except FileNotFoundError as exc:
        raise ProcessingError("ffmpeg_error", "FFmpeg 실행 파일을 찾을 수 없습니다.") from exc
    except subprocess.CalledProcessError as exc:
        err_msg = exc.stderr.strip() if exc.stderr else str(exc)
        logger.error("FFmpeg 오류: %s", err_msg)
        raise ProcessingError("ffmpeg_error", f"FFmpeg 처리 실패.\n{err_msg[:300]}") from exc


def build_subtitle_filter(subtitle_file: Path) -> str:
    """[Fix-1] Windows 한글/공백 경로 완전 대응."""
    path = subtitle_file.resolve()
    if os.name == "nt":
        s = str(path).replace("\\", "/")
        if len(s) >= 2 and s[1] == ":":
            s = s[0] + "\\:" + s[2:]
    else:
        s = str(path)
    return f"subtitles='{s.replace(chr(39), chr(92)+chr(39))}'"


# ──────────────────────────────────────────
# [P0-5] lru_cache — duration 중복 호출 방지
# ──────────────────────────────────────────

@lru_cache(maxsize=64)
def get_video_duration(video_path: Path) -> float:
    """
    영상 길이(초) 반환.
    [P0-5] lru_cache 적용 — 동일 경로 중복 호출 시 캐시 반환.
    [Fix-3] duration=0 → ffprobe 폴백.
    """
    try:
        cap = cv2.VideoCapture(str(video_path))
        try:
            if cap.isOpened():
                fps = cap.get(cv2.CAP_PROP_FPS)
                fc  = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                if fps and fps > 0 and fc and fc > 0:
                    return fc / fps
        finally:
            cap.release()
    except Exception:
        pass

    if shutil.which("ffprobe"):
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(video_path)],
                capture_output=True, text=True, timeout=15,
            )
            dur = float(r.stdout.strip())
            if dur > 0:
                return dur
        except Exception:
            pass

    raise ProcessingError(
        "input_error",
        "영상 길이를 읽을 수 없습니다.\n파일이 손상됐거나 지원하지 않는 코덱일 수 있습니다."
    )


def invalidate_duration_cache(video_path: Path) -> None:
    """영상 파일이 변경됐을 때 캐시를 무효화합니다."""
    try:
        get_video_duration.cache_clear()
    except Exception:
        pass


# ──────────────────────────────────────────
# [P0-1] cap.release() — 모든 VideoCapture finally 보장
# ──────────────────────────────────────────

def capture_frames(
    video_path: Path,
    timestamps: List[float],
    output_dir: Path,
    logger: logging.Logger,
) -> List[Path]:
    """[P0-1] try/finally로 cap.release() 보장."""
    cap = cv2.VideoCapture(str(video_path))
    saved: List[Path] = []
    try:
        if not cap.isOpened():
            raise ProcessingError("input_error", "프레임 추출을 위한 영상을 열 수 없습니다.")
        duration = get_video_duration(video_path)
        for i, sec in enumerate(timestamps, start=1):
            if sec < 0:
                logger.warning("음수 타임스탬프 건너뜀: %s", sec)
                continue
            if duration and sec > duration:
                logger.warning("영상 길이 초과 건너뜀: %.2f > %.2f", sec, duration)
                continue
            cap.set(cv2.CAP_PROP_POS_MSEC, sec * 1000)
            ok, frame = cap.read()
            if not ok or frame is None:
                logger.warning("%.2f초 프레임 추출 실패", sec)
                continue
            out = output_dir / f"thumb_{i:02d}_{int(sec)}s.jpg"
            try:
                cv2.imwrite(str(out), frame)
                saved.append(out)
            except OSError as exc:
                logger.warning("프레임 저장 실패: %s", exc)
    finally:
        cap.release()   # ← [P0-1] 예외 여부 관계없이 반드시 해제

    if not saved:
        raise ProcessingError("input_error", "영상에서 프레임을 추출할 수 없습니다.")
    return saved


def extract_audio(video_path: Path, audio_dir: Path, logger: logging.Logger) -> Path:
    out = audio_dir / "audio.mp3"
    run_ffmpeg(["-i", str(video_path), "-vn", "-acodec", "mp3", str(out)], logger)
    if not out.exists():
        raise ProcessingError("ffmpeg_error", "오디오 추출에 실패했습니다.")
    return out


def trim_video(
    video_path: Path, start_time: float, end_time: float,
    output_dir: Path, logger: logging.Logger,
) -> Path:
    if start_time < 0 or end_time < 0:
        raise ProcessingError("input_error", "트림 값은 0 이상이어야 합니다.")
    duration = get_video_duration(video_path)
    if duration <= 0:
        raise ProcessingError("input_error", "영상 길이를 확인할 수 없어 트림이 불가능합니다.")
    if start_time + end_time >= duration:
        raise ProcessingError("input_error", "트림 값이 영상 전체 길이보다 크거나 같습니다.")
    out = output_dir / "trimmed_video.mp4"
    cmd: List[str] = []
    if start_time > 0:
        cmd += ["-ss", str(start_time)]
    cmd += ["-i", str(video_path)]
    if end_time > 0:
        cmd += ["-t", str(max(duration - start_time - end_time, 0.1))]
    cmd += ["-c:v", FFMPEG_VIDEO_CODEC, "-c:a", FFMPEG_AUDIO_CODEC, str(out)]
    run_ffmpeg(cmd, logger)
    if not out.exists():
        raise ProcessingError("ffmpeg_error", "트림 처리에 실패했습니다.")
    return out


def adjust_volume(
    video_path: Path, volume_ratio: float,
    output_dir: Path, logger: logging.Logger,
) -> Path:
    if volume_ratio <= 0:
        raise ProcessingError("input_error", "볼륨 배율은 0보다 커야 합니다.")
    out = output_dir / "volume_adjusted_video.mp4"
    run_ffmpeg(
        ["-i", str(video_path), "-filter:a", f"volume={volume_ratio}", "-c:v", "copy", str(out)],
        logger,
    )
    if not out.exists():
        raise ProcessingError("ffmpeg_error", "볼륨 조절에 실패했습니다.")
    return out


def export_video(
    source_path: Path, output_dir: Path,
    logger: logging.Logger,
    subtitle_path: Optional[Path] = None,
) -> Path:
    if not source_path.exists():
        raise ProcessingError("input_error", "내보낼 원본 영상이 존재하지 않습니다.")
    final = output_dir / "final_video.mp4"
    if subtitle_path and subtitle_path.exists():
        logger.info("자막 burn-in 내보내기: %s", subtitle_path)
        vf = build_subtitle_filter(subtitle_path)
        run_ffmpeg(
            ["-i", str(source_path), "-vf", vf,
             "-c:v", FFMPEG_VIDEO_CODEC, "-preset", FFMPEG_PRESET,
             "-crf", FFMPEG_CRF, "-c:a", "copy", str(final)],
            logger,
        )
    else:
        if subtitle_path:
            logger.warning("자막 파일 없음, 자막 없이 내보냄")
        try:
            shutil.copy2(source_path, final)
        except PermissionError as exc:
            raise ProcessingError("file_write_error", "최종 영상 저장 권한이 없습니다.") from exc
    if not final.exists():
        raise ProcessingError("ffmpeg_error", "최종 내보내기에 실패했습니다.")
    logger.info("최종 영상 저장 완료: %s", final)
    return final


def select_thumbnail(
    image_list: List[Path], selected_index: int,
    output_dir: Path, logger: logging.Logger,
) -> Path:
    if selected_index < 0 or selected_index >= len(image_list):
        raise ProcessingError("input_error", "잘못된 썸네일 선택입니다.")
    dst = output_dir / "selected_thumbnail.jpg"
    try:
        shutil.copy2(image_list[selected_index], dst)
    except PermissionError as exc:
        raise ProcessingError("file_write_error", "썸네일 저장 권한이 없습니다.") from exc
    logger.info("썸네일 선택: %s", dst)
    return dst
