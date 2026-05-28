"""
core/project.py
===============
프로젝트 폴더 생성, 경로 관리, preflight 체크.
[복원] find_reusable_project_for_video, extended_preflight_check 추가
"""
from __future__ import annotations

import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from core.models import ProcessingError, ProjectPaths
from config.settings import LOG_FORMAT, LOG_DATE_FORMAT


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def sanitize_name(value: str) -> str:
    safe = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_"
        for ch in value.strip()
    )
    return safe or f"project_{now_stamp()}"


def setup_logger(log_file: Path) -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"vas_{log_file.stem}_{now_stamp()}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    try:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    except PermissionError:
        pass
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)
    return logger


# ──────────────────────────────────────────
# Preflight 체크 (기본)
# ──────────────────────────────────────────

def run_preflight_check(project_root: Path) -> dict:
    result: dict = {
        "python_ok":             True,
        "ffmpeg_ok":             shutil.which("ffmpeg") is not None,
        "opencv_ok":             True,
        "whisper_ok":            True,
        "project_root_writable": True,
        "disk_ok":               True,
        "disk_free_gb":          0.0,
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
        test = project_root / "__write_test__.tmp"
        test.write_text("ok", encoding="utf-8")
        test.unlink(missing_ok=True)
    except Exception:
        result["project_root_writable"] = False
    try:
        usage = shutil.disk_usage(project_root)
        free_gb = round(usage.free / (1024 ** 3), 2)
        result["disk_free_gb"] = free_gb
        result["disk_ok"] = free_gb > 0.2
    except OSError:
        result["disk_ok"] = False
    return result


# ──────────────────────────────────────────
# [복원] Extended Preflight — 디스크·PySide6 위젯 탐침 포함
# ──────────────────────────────────────────

def extended_preflight_check(project_root: Path) -> dict:
    """기본 점검 + 디스크 용량 + PySide6 위젯 탐침까지 포함한 확장 환경 점검."""
    base = run_preflight_check(project_root)
    base["python_runtime_ok"] = True

    # PySide6 버전 확인
    try:
        from PySide6 import __version__ as pyside_ver  # type: ignore
        base["pyside_ok"] = True
        base["pyside_version"] = str(pyside_ver)
    except Exception as exc:
        base["pyside_ok"] = False
        base["pyside_version"] = str(exc)

    # PySide6 위젯 탐침 (QApplication 인스턴스 있을 때만)
    base["pyside_widget_probe_ok"] = False
    try:
        from PySide6.QtWidgets import QApplication, QLabel
        if QApplication.instance() is not None:
            probe = QLabel()
            probe.deleteLater()
        base["pyside_widget_probe_ok"] = True
    except Exception:
        pass

    return base


def preflight_summary(result: dict) -> Tuple[bool, str]:
    lines = []
    all_ok = True
    checks = {
        "FFmpeg":          result.get("ffmpeg_ok", False),
        "OpenCV":          result.get("opencv_ok", False),
        "Whisper":         result.get("whisper_ok", False),
        "디스크 쓰기":     result.get("project_root_writable", False),
        "디스크 여유공간":  result.get("disk_ok", False),
        "PySide6":         result.get("pyside_ok", True),
    }
    for name, ok in checks.items():
        lines.append(f"{'✅' if ok else '❌'} {name}")
        if not ok:
            all_ok = False
    lines.append(f"   └ 디스크 여유: {result.get('disk_free_gb', 0.0)} GB")
    pv = result.get("pyside_version", "")
    if pv:
        lines.append(f"   └ PySide6 버전: {pv}")
    return all_ok, "\n".join(lines)


# ──────────────────────────────────────────
# 프로젝트 폴더 생성
# ──────────────────────────────────────────

def create_project_structure(base_directory: Path, project_name: str) -> ProjectPaths:
    root = base_directory / sanitize_name(project_name)
    if root.exists():
        root = base_directory / f"{sanitize_name(project_name)}_{now_stamp()}"
    folders = [root, root/"input", root/"audio", root/"subtitles",
               root/"thumbnails", root/"output", root/"logs"]
    try:
        for folder in folders:
            folder.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise ProcessingError("file_write_error", "프로젝트 폴더 생성 권한이 없습니다.") from exc
    return _paths_from_root(root)


def _paths_from_root(root: Path) -> ProjectPaths:
    return ProjectPaths(
        root=root, input_dir=root/"input", audio_dir=root/"audio",
        subtitles_dir=root/"subtitles", thumbnails_dir=root/"thumbnails",
        output_dir=root/"output", logs_dir=root/"logs",
    )


# ──────────────────────────────────────────
# [복원] 기존 프로젝트 재사용 탐색
# ──────────────────────────────────────────

def find_reusable_project(base_dir: Path, source_video: Path) -> Optional[Tuple[ProjectPaths, Path]]:
    """
    같은 영상(이름+크기)으로 처리한 프로젝트가 이미 있으면 재사용합니다.
    Smart Search / Snapshot 재실행 시 영상을 다시 복사하지 않아도 됩니다.
    """
    if not base_dir.exists() or not source_video.is_file():
        return None
    stem = sanitize_name(source_video.stem)
    candidates: List[Tuple[float, Path, Path]] = []
    for p in base_dir.iterdir():
        if not p.is_dir():
            continue
        if p.name != stem and not p.name.startswith(f"{stem}_"):
            continue
        dest = p / "input" / source_video.name
        if not dest.is_file():
            continue
        try:
            if dest.stat().st_size == source_video.stat().st_size:
                candidates.append((p.stat().st_mtime, p, dest))
        except OSError:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, root, vpath = candidates[0]
    return _paths_from_root(root), vpath


# ──────────────────────────────────────────
# [복원] 내보내기 요약 라인 생성
# ──────────────────────────────────────────

def build_export_summary(result, original: Optional[Path] = None) -> str:
    """WorkflowResult를 받아 내보내기 요약 문자열을 반환합니다."""
    p = result.project_paths
    lines = [
        f"📁 프로젝트 폴더:  {p.root}",
        f"🎬 원본 영상:      {original or result.input_video_path}",
        f"📂 프로젝트 영상:  {result.input_video_path}",
        f"🔊 오디오:         {result.audio_path}",
        f"📝 자막 TXT:       {result.text_path}",
        f"📝 자막 SRT:       {result.srt_path}",
        f"🖼  선택 썸네일:   {result.selected_thumbnail or '미저장'}",
        f"✅ 최종 영상:      {result.final_video_path}",
        f"📋 로그:           {p.logs_dir / 'run.log'}",
    ]
    return "\n".join(lines)


def copy_input_video(video_path: Path, input_dir: Path) -> Path:
    destination = input_dir / video_path.name
    try:
        shutil.copy2(video_path, destination)
    except PermissionError as exc:
        raise ProcessingError("file_write_error", "입력 영상 복사 권한이 없습니다.") from exc
    return destination


def validate_video_file(video_path: Path, supported: set) -> None:
    if not video_path.exists():
        raise ProcessingError("input_error", "파일을 찾을 수 없습니다.")
    if not video_path.is_file():
        raise ProcessingError("input_error", "선택한 경로가 파일이 아닙니다.")
    if video_path.suffix.lower() not in supported:
        raise ProcessingError(
            "input_error",
            f"지원하지 않는 파일 형식입니다. 지원 형식: {', '.join(sorted(supported))}"
        )
