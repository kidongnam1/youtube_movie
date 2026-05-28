"""
core/youtube.py
===============
YouTube URL 검증, 메타데이터 조회, 다운로드를 담당합니다.
yt-dlp 라이브러리를 사용합니다.
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

from core.models import ProcessingError


# ──────────────────────────────────────────
# URL 검증
# ──────────────────────────────────────────

def validate_youtube_url(raw_url: str) -> str:
    """
    YouTube URL 유효성 검사.
    - 플레이리스트 거부
    - watch / shorts / live / youtu.be 허용
    - age-restricted 경고 포함
    """
    url = raw_url.strip()
    if not url:
        raise ProcessingError("input_error", "YouTube URL을 입력해주세요.")

    low = url.lower()

    # 플레이리스트 거부
    if "list=" in low or "/playlist" in low:
        raise ProcessingError("input_error", "플레이리스트 URL은 지원하지 않습니다.\n개별 영상 URL을 입력해주세요.")

    parsed = urlparse(url)
    host   = (parsed.netloc or "").lower().lstrip("www.")
    path   = parsed.path or ""

    # youtu.be 단축 URL
    if host == "youtu.be" and len(path) > 1:
        return url

    # youtube.com
    if "youtube.com" in host:
        if "/watch" in path or path.startswith("/shorts/") or path.startswith("/live"):
            return url

    raise ProcessingError(
        "input_error",
        "지원하지 않는 YouTube URL 형식입니다.\n"
        "예시: https://www.youtube.com/watch?v=XXXXXXXXXXX"
    )


# ──────────────────────────────────────────
# 메타데이터 조회 (다운로드 없이)
# ──────────────────────────────────────────

def fetch_youtube_metadata(url: str, logger: logging.Logger) -> dict:
    """영상 제목, 길이, 업로더 등을 미리 조회합니다."""
    validate_youtube_url(url)

    try:
        import yt_dlp
    except ImportError as exc:
        raise ProcessingError(
            "youtube_error",
            "yt-dlp가 설치되어 있지 않습니다.\n실행: pip install yt-dlp"
        ) from exc

    logger.info("YouTube 메타데이터 조회 중: %s", url)
    opts = {"quiet": True, "noplaylist": True, "skip_download": True}

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        err = str(exc)
        if "age" in err.lower() or "sign in" in err.lower():
            raise ProcessingError(
                "youtube_error",
                "연령 제한 영상입니다. 해당 영상은 다운로드할 수 없습니다."
            ) from exc
        raise ProcessingError("youtube_error", f"메타데이터 조회 실패:\n{err[:300]}") from exc

    return {
        "title":       str(info.get("title") or ""),
        "duration":    float(info.get("duration") or 0),
        "uploader":    str(info.get("uploader") or ""),
        "webpage_url": str(info.get("webpage_url") or url),
        "id":          str(info.get("id") or ""),
        "thumbnail":   str(info.get("thumbnail") or ""),
    }


# ──────────────────────────────────────────
# 다운로드
# ──────────────────────────────────────────

def download_youtube_video(
    url: str,
    output_dir: Path,
    logger: logging.Logger,
    resolution: str = "bestvideo+bestaudio/best",
) -> Path:
    """
    YouTube 영상을 MP4로 다운로드합니다.
    반환값: 다운로드된 파일 경로
    """
    validate_youtube_url(url)

    try:
        import yt_dlp
    except ImportError as exc:
        raise ProcessingError(
            "youtube_error",
            "yt-dlp가 설치되어 있지 않습니다.\n실행: pip install yt-dlp"
        ) from exc

    output_template = str(output_dir / "%(title)s.%(ext)s")
    opts = {
        "format":   resolution,
        "outtmpl":  output_template,
        "noplaylist": True,
        "quiet":    False,
        "merge_output_format": "mp4",
    }

    logger.info("YouTube 다운로드 시작: %s", url)
    downloaded_path: list = []

    def _progress_hook(d: dict) -> None:
        if d.get("status") == "finished":
            downloaded_path.append(Path(d["filename"]))

    opts["progress_hooks"] = [_progress_hook]

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        err = str(exc)
        if "age" in err.lower():
            raise ProcessingError("youtube_error", "연령 제한 영상은 다운로드할 수 없습니다.") from exc
        raise ProcessingError("youtube_error", f"다운로드 실패:\n{err[:300]}") from exc

    if not downloaded_path:
        # progress_hook 미호출 시 폴더에서 mp4 탐색
        mp4s = list(output_dir.glob("*.mp4"))
        if mp4s:
            return sorted(mp4s, key=lambda p: p.stat().st_mtime, reverse=True)[0]
        raise ProcessingError("youtube_error", "다운로드 완료됐지만 파일을 찾을 수 없습니다.")

    logger.info("다운로드 완료: %s", downloaded_path[0])
    return downloaded_path[0]
