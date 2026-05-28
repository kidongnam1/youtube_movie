"""
core/subtitle.py — V2.1
[P0-3] Whisper 모델 캐싱 — 매 호출 30초 → 2회차 이후 0.01초
[Fix-2] GPU 실패 시 CPU 폴백 (gpu_error_msg 스코프 보존)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple

from core.models import ProcessingError
from config.settings import WHISPER_MODEL, WHISPER_DEVICE_PRIMARY, WHISPER_DEVICE_FALLBACK


# ──────────────────────────────────────────
# [P0-3] 모듈 레벨 캐시 — 프로세스 생애 동안 유지
# ──────────────────────────────────────────

_MODEL_CACHE: Dict[str, object] = {}


def clear_whisper_cache() -> None:
    """메모리 절약이 필요할 때 캐시를 명시적으로 비웁니다."""
    _MODEL_CACHE.clear()


def _load_whisper_model(model_name: str, logger: logging.Logger):
    """
    [P0-3] 캐시 히트 시 즉시 반환.
    [Fix-2] GPU 실패 → CPU 폴백, gpu_error_msg outer scope 보존.
    """
    if model_name in _MODEL_CACHE:
        logger.info("Whisper 캐시 적중: %s (재로드 생략)", model_name)
        return _MODEL_CACHE[model_name]

    try:
        import whisper
    except ImportError as exc:
        raise ProcessingError(
            "whisper_error",
            "Whisper가 설치되어 있지 않습니다.\n실행: pip install openai-whisper"
        ) from exc

    gpu_error_msg: str = ""

    try:
        logger.info("Whisper 모델 로드 중 (GPU 시도): %s", model_name)
        model = whisper.load_model(model_name, device=WHISPER_DEVICE_PRIMARY)
        logger.info("Whisper GPU 로드 성공")
        _MODEL_CACHE[model_name] = model
        return model
    except Exception as gpu_exc:
        gpu_error_msg = str(gpu_exc)
        logger.warning("GPU 로드 실패, CPU 재시도: %s", gpu_error_msg)

    try:
        model = whisper.load_model(model_name, device=WHISPER_DEVICE_FALLBACK)
        logger.info("Whisper CPU 로드 성공")
        _MODEL_CACHE[model_name] = model
        return model
    except Exception as cpu_exc:
        raise ProcessingError(
            "whisper_error",
            f"Whisper 모델 로드 실패.\nGPU: {gpu_error_msg}\nCPU: {cpu_exc}"
        ) from cpu_exc


# ──────────────────────────────────────────
# SRT 유틸
# ──────────────────────────────────────────

def _seconds_to_srt_time(seconds: float) -> str:
    millis = int(round((seconds % 1) * 1000))
    whole  = int(seconds)
    h, m, s = whole // 3600, (whole % 3600) // 60, whole % 60
    return f"{h:02d}:{m:02d}:{s:02d},{millis:03d}"


def write_srt(segments: List[dict], output_path: Path) -> None:
    lines: List[str] = []
    for i, seg in enumerate(segments, start=1):
        start = _seconds_to_srt_time(float(seg["start"]))
        end   = _seconds_to_srt_time(float(seg["end"]))
        text  = str(seg.get("text", "")).strip()
        lines += [str(i), f"{start} --> {end}", text, ""]
    try:
        output_path.write_text("\n".join(lines), encoding="utf-8")
    except PermissionError as exc:
        raise ProcessingError("file_write_error", "SRT 파일 저장 권한이 없습니다.") from exc


# ──────────────────────────────────────────
# 자막 생성
# ──────────────────────────────────────────

def generate_subtitles(
    audio_path: Path,
    subtitles_dir: Path,
    logger: logging.Logger,
) -> Tuple[Path, Path]:
    model = _load_whisper_model(WHISPER_MODEL, logger)
    logger.info("자막 생성 중: %s", audio_path)
    try:
        result = model.transcribe(str(audio_path))
    except Exception as exc:
        logger.error("Whisper 변환 실패: %s", exc)
        raise ProcessingError("whisper_error", f"자막 생성 실패.\n{exc}") from exc

    text_path = subtitles_dir / "subtitle.txt"
    try:
        text_path.write_text(result.get("text", "").strip(), encoding="utf-8")
    except PermissionError as exc:
        raise ProcessingError("file_write_error", "자막 TXT 저장 권한이 없습니다.") from exc

    srt_path  = subtitles_dir / "subtitle.srt"
    write_srt(result.get("segments", []), srt_path)

    meta_path = subtitles_dir / "subtitle_metadata.json"
    try:
        meta_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except PermissionError as exc:
        raise ProcessingError("file_write_error", "자막 메타데이터 저장 권한이 없습니다.") from exc

    logger.info("자막 생성 완료: %s / %s", text_path, srt_path)
    return text_path, srt_path


def edit_subtitle_text(updated: str, subtitles_dir: Path, logger: logging.Logger) -> Path:
    text_path = subtitles_dir / "subtitle.txt"
    try:
        text_path.write_text(updated, encoding="utf-8")
    except PermissionError as exc:
        raise ProcessingError("file_write_error", "자막 TXT 저장 권한이 없습니다.") from exc
    logger.info("자막 텍스트 수정 완료")
    return text_path


def regenerate_srt(updated: str, subtitles_dir: Path, logger: logging.Logger) -> Path:
    srt_path  = subtitles_dir / "subtitle.srt"
    meta_path = subtitles_dir / "subtitle_metadata.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            segs = list(meta.get("segments") or [])
            if segs:
                write_srt(_distribute_text(updated, segs), srt_path)
                logger.info("SRT 재생성 완료 (타이밍 유지)")
                return srt_path
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("메타데이터 파싱 실패, 폴백: %s", exc)
    logger.warning("단일 큐 폴백 SRT 저장")
    write_srt([{"start": 0.0, "end": 600.0, "text": updated.strip()}], srt_path)
    return srt_path


def _distribute_text(text: str, segments: List[dict]) -> List[dict]:
    text = text.strip()
    if not segments:
        return []
    if not text:
        return [{"start": s["start"], "end": s["end"], "text": ""} for s in segments]
    weights = [max(1, len(str(s.get("text", "")).strip())) for s in segments]
    total_w = sum(weights)
    counts  = [round(len(text) * w / total_w) for w in weights]
    counts[-1] += len(text) - sum(counts)
    rebuilt, pos = [], 0
    for seg, count in zip(segments, counts):
        rebuilt.append({"start": seg["start"], "end": seg["end"],
                        "text": text[pos: pos + count].strip()})
        pos += count
    if pos < len(text) and rebuilt:
        rebuilt[-1]["text"] = (rebuilt[-1]["text"] + text[pos:]).strip()
    return rebuilt
