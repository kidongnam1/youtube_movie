"""
core/smart_frame.py
===================
스마트 프레임 검색 및 정적 장면(Static Snapshot) 감지 전담 모듈.
OpenCV 기반 분석, run.py의 S1~S5 로직을 여기로 이전했습니다.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np

from core.models import (
    ProcessingError,
    FrameSample,
    SmartFrameCandidate,
    SmartFrameSearchRequest,
    SmartFrameSearchResult,
)
from core.video import get_video_duration
from config.settings import (
    SCORE_WEIGHT_SCENE, SCORE_WEIGHT_SHARPNESS, SCORE_WEIGHT_BRIGHTNESS,
    UPPER_BODY_FRACTION, STATIC_MIN_DURATION_SEC,
    STATIC_SAMPLE_INTERVAL, STATIC_DIFF_THRESHOLD,
)
from core.project import now_stamp


# ──────────────────────────────────────────
# Smart Frame Search
# ──────────────────────────────────────────

def run_smart_frame_search(
    request: SmartFrameSearchRequest,
    output_dir: Path,
    logger: logging.Logger,
) -> SmartFrameSearchResult:
    """스마트 프레임 검색 메인 함수."""
    _validate_request(request)

    duration = get_video_duration(request.video_path)
    if duration <= 0:
        raise ProcessingError("smart_search_error", "영상 길이를 확인할 수 없습니다.")

    start_sec, end_sec = _resolve_range(request, duration)
    logger.info("스마트 프레임 검색 범위: %.2f ~ %.2f 초", start_sec, end_sec)

    samples   = _sample_frames(request.video_path, start_sec, end_sec, request.sampling_interval_sec)
    candidates = _score_candidates(samples, request)
    candidates = _filter_by_gap(candidates, request.min_frame_gap_sec)
    candidates = candidates[: request.result_count]

    if not candidates:
        raise ProcessingError(
            "smart_search_error",
            "좋은 프레임을 찾지 못했습니다.\n검색 범위를 넓혀보세요."
        )

    saved = _save_candidates(samples, candidates, output_dir, request.search_mode, logger)
    return SmartFrameSearchResult(
        mode=request.search_mode,
        candidates=saved,
        search_start_sec=start_sec,
        search_end_sec=end_sec,
    )


def _validate_request(req: SmartFrameSearchRequest) -> None:
    if req.search_mode not in {"auto", "range"}:
        raise ProcessingError("smart_search_error", "검색 모드는 auto 또는 range여야 합니다.")
    if req.result_count <= 0:
        raise ProcessingError("smart_search_error", "결과 프레임 수는 1 이상이어야 합니다.")
    if req.sampling_interval_sec <= 0:
        raise ProcessingError("smart_search_error", "샘플링 간격은 0보다 커야 합니다.")
    if req.search_mode == "range":
        if req.target_time_sec is None:
            raise ProcessingError("smart_search_error", "Range 모드에서는 목표 시간이 필요합니다.")
        if not req.search_window_sec or req.search_window_sec <= 0:
            raise ProcessingError("smart_search_error", "검색 창 크기는 0보다 커야 합니다.")


def _resolve_range(req: SmartFrameSearchRequest, duration: float):
    if req.search_mode == "auto":
        return 0.0, duration
    target = float(req.target_time_sec or 0.0)
    if target > duration:
        raise ProcessingError("smart_search_error", "목표 시간이 영상 길이를 초과합니다.")
    half = float(req.search_window_sec or 0.0) / 2.0
    start = max(0.0, target - half)
    end   = min(duration, target + half)
    if start >= end:
        raise ProcessingError("smart_search_error", "검색 범위가 유효하지 않습니다.")
    return start, end


def _sample_frames(
    video_path: Path, start: float, end: float, interval: float
) -> List[FrameSample]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        cap.release()
        raise ProcessingError("smart_search_error", "스마트 검색을 위한 영상을 열 수 없습니다.")

    samples: List[FrameSample] = []
    t = start
    while t <= end:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if ok and frame is not None:
            samples.append(FrameSample(timestamp_sec=t, frame_image=frame))
        t += interval

    cap.release()
    if not samples:
        raise ProcessingError("smart_search_error", "선택한 범위에서 프레임을 추출할 수 없습니다.")
    return samples


def _score_candidates(
    samples: List[FrameSample], req: SmartFrameSearchRequest
) -> List[SmartFrameCandidate]:
    scene_scores = _calc_scene_scores(samples, req.scene_threshold)
    candidates: List[SmartFrameCandidate] = []

    for i, sample in enumerate(samples):
        scene = scene_scores[i]
        sharp = _sharpness(sample.frame_image) if req.use_sharpness_filter else 0.0
        bright = _brightness(sample.frame_image) if req.use_brightness_filter else 0.0
        total = (
            scene  * SCORE_WEIGHT_SCENE +
            sharp  * SCORE_WEIGHT_SHARPNESS +
            bright * SCORE_WEIGHT_BRIGHTNESS
        )
        candidates.append(SmartFrameCandidate(
            timestamp_sec=sample.timestamp_sec,
            frame_path=None,
            total_score=total,
            scene_score=scene,
            sharpness_score=sharp,
            brightness_score=bright,
        ))

    return sorted(candidates, key=lambda c: c.total_score, reverse=True)


def _calc_scene_scores(samples: List[FrameSample], threshold: float) -> List[float]:
    scores: List[float] = []
    prev = None
    for s in samples:
        gray = cv2.cvtColor(s.frame_image, cv2.COLOR_BGR2GRAY)
        if prev is None:
            scores.append(0.0)
        else:
            diff = float(cv2.absdiff(prev, gray).mean())
            scores.append(min(diff / max(threshold, 1.0), 1.0))
        prev = gray
    return scores


def _sharpness(frame) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(min(cv2.Laplacian(gray, cv2.CV_64F).var() / 1000.0, 1.0))


def _brightness(frame) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    val = float(gray.mean())
    return max(0.0, min(1.0 - abs(val - 128.0) / 128.0, 1.0))


def _filter_by_gap(
    candidates: List[SmartFrameCandidate], min_gap: float
) -> List[SmartFrameCandidate]:
    kept: List[SmartFrameCandidate] = []
    for c in candidates:
        if all(abs(c.timestamp_sec - k.timestamp_sec) >= min_gap for k in kept):
            kept.append(c)
    return kept


def _save_candidates(
    samples: List[FrameSample],
    candidates: List[SmartFrameCandidate],
    output_dir: Path,
    mode: str,
    logger: logging.Logger,
) -> List[SmartFrameCandidate]:
    ts_map = {round(s.timestamp_sec, 3): s for s in samples}
    saved: List[SmartFrameCandidate] = []

    for i, c in enumerate(candidates, start=1):
        s = ts_map.get(round(c.timestamp_sec, 3))
        if s is None:
            continue
        path = output_dir / f"smart_{mode}_{i:02d}_{int(c.timestamp_sec)}s.jpg"
        try:
            cv2.imwrite(str(path), s.frame_image)
            saved.append(SmartFrameCandidate(
                timestamp_sec=c.timestamp_sec, frame_path=path,
                total_score=c.total_score, scene_score=c.scene_score,
                sharpness_score=c.sharpness_score, brightness_score=c.brightness_score,
            ))
            logger.info("스마트 프레임 저장: %s (점수: %.3f)", path.name, c.total_score)
        except Exception as exc:
            logger.warning("스마트 프레임 저장 실패: %s", exc)

    if not saved:
        raise ProcessingError(
            "smart_search_error",
            "선택된 범위에서 좋은 프레임을 찾지 못했습니다.\n검색 창을 넓혀보세요."
        )
    return saved


# ──────────────────────────────────────────
# Static Snapshot (정적 장면 감지 S1~S5)
# ──────────────────────────────────────────

def export_static_snapshots(
    video_path: Path,
    project_root: Path,
    logger: logging.Logger,
    sample_interval: float = STATIC_SAMPLE_INTERVAL,
    diff_threshold: float  = STATIC_DIFF_THRESHOLD,
    min_duration: float    = STATIC_MIN_DURATION_SEC,
    upper_frac: float      = UPPER_BODY_FRACTION,
) -> Path:
    """
    영상에서 정적 장면(움직임 없는 구간)을 감지하고
    마지막 안정 프레임을 JPG로 저장합니다.
    단일 패스 방식 — 고속, 저메모리.
    """
    out_dir = project_root / "static_snapshots" / f"run_{now_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        cap.release()
        raise ProcessingError("input_error", "정적 스냅샷을 위한 영상을 열 수 없습니다.")

    duration = get_video_duration(video_path)
    if duration <= 0:
        cap.release()
        logger.warning("정적 스냅샷: 영상 길이가 0 — 빈 결과 저장")
        _write_snapshot_index(out_dir, video_path, project_root, sample_interval, diff_threshold, [])
        return out_dir

    in_static = False
    seg_start = 0.0
    prev_gray: Optional[np.ndarray] = None
    last_full: Optional[np.ndarray] = None
    last_body: Optional[np.ndarray] = None
    t = 0.0
    seg_idx = 0
    segments = []

    try:
        while t < duration + 1e-6:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok or frame is None:
                break

            upper = _crop_upper(frame, upper_frac)
            gray  = cv2.GaussianBlur(cv2.cvtColor(upper, cv2.COLOR_BGR2GRAY), (5, 5), 0)

            if prev_gray is not None:
                diff = float(cv2.absdiff(gray, prev_gray).mean())
                is_static = diff < diff_threshold

                if is_static:
                    if not in_static:
                        seg_start = t
                        in_static = True
                    last_full = frame.copy()
                    last_body = upper.copy()
                else:
                    if in_static and (t - seg_start) >= min_duration and last_full is not None:
                        seg_idx += 1
                        segments.append(_save_segment(out_dir, seg_idx, seg_start, t, last_full, last_body))
                    in_static, last_full, last_body = False, None, None

            prev_gray = gray
            t += sample_interval

        # 마지막 구간 처리
        if in_static and (duration - seg_start) >= min_duration and last_full is not None:
            seg_idx += 1
            segments.append(_save_segment(out_dir, seg_idx, seg_start, duration, last_full, last_body))

    finally:
        cap.release()

    logger.info("정적 스냅샷: %d 구간 저장 완료", len(segments))
    _write_snapshot_index(out_dir, video_path, project_root, sample_interval, diff_threshold, segments)
    return out_dir


def _crop_upper(frame: np.ndarray, frac: float) -> np.ndarray:
    h = frame.shape[0]
    cut = max(1, int(round(h * frac)))
    return frame[:cut, :, :]


def _save_segment(
    out_dir: Path, idx: int,
    start: float, end: float,
    full: np.ndarray, body: Optional[np.ndarray],
) -> dict:
    full_p = out_dir / f"segment_{idx:03d}_full.jpg"
    body_p = out_dir / f"segment_{idx:03d}_body.jpg"
    cv2.imwrite(str(full_p), full)
    if body is not None:
        cv2.imwrite(str(body_p), body)
        scores = _chart_heuristic(body)
    else:
        scores = {}

    return {
        "index": idx, "start_sec": start, "end_sec": end,
        "duration_sec": end - start, "mid_sec": (start + end) / 2,
        "files": {"full_jpg": full_p.name, "body_jpg": body_p.name if body is not None else ""},
        "heuristics": scores,
    }


def _chart_heuristic(body: np.ndarray) -> dict:
    gray = cv2.cvtColor(body, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    edge_density = float(np.clip(edges.mean() / 255.0, 0, 1))
    sx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    sy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    grid = float(np.clip((np.abs(sx).mean() + np.abs(sy).mean()) / 512.0, 0, 1))
    return {
        "edge_density": round(edge_density, 4),
        "line_grid_score": round(grid, 4),
        "text_like_score": round(0.55 * edge_density + 0.45 * grid, 4),
    }


def _write_snapshot_index(
    out_dir: Path, video_path: Path, project_root: Path,
    interval: float, threshold: float, segments: list,
) -> None:
    payload = {
        "video_path": str(video_path),
        "project_root": str(project_root),
        "upper_body_fraction": UPPER_BODY_FRACTION,
        "sample_interval_sec": interval,
        "diff_threshold": threshold,
        "min_static_duration_sec": STATIC_MIN_DURATION_SEC,
        "segment_count": len(segments),
        "segments": segments,
    }
    (out_dir / "static_index.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
