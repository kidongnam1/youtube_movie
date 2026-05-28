"""
config/settings.py
==================
모든 상수와 설정을 한 곳에서 관리합니다.
코드를 수정하지 않고 이 파일만 수정하면 앱 전체 동작이 바뀝니다.
"""

# ──────────────────────────────────────────
# 앱 기본 정보
# ──────────────────────────────────────────
APP_NAME    = "Video Automation System V2.0"
APP_VERSION = "2.0.0"

# ──────────────────────────────────────────
# 지원 파일 형식
# ──────────────────────────────────────────
SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}

# ──────────────────────────────────────────
# 영상 처리 기본값
# ──────────────────────────────────────────
DEFAULT_CAPTURE_TIMES        = [3.0, 10.0, 20.0]   # 프레임 추출 타임스탬프 (초)
DEFAULT_VOLUME_RATIO         = 1.0                  # 기본 볼륨 비율
DEFAULT_TRIM_START           = 0.0                  # 기본 앞 자르기 (초)
DEFAULT_TRIM_END             = 0.0                  # 기본 뒤 자르기 (초)

# ──────────────────────────────────────────
# Whisper 설정
# ──────────────────────────────────────────
WHISPER_MODEL                = "base"               # tiny / base / small / medium / large
WHISPER_DEVICE_PRIMARY       = "cuda"               # GPU 우선 시도
WHISPER_DEVICE_FALLBACK      = "cpu"                # GPU 실패 시 CPU 폴백

# ──────────────────────────────────────────
# FFmpeg 품질 설정
# ──────────────────────────────────────────
FFMPEG_VIDEO_CODEC           = "libx264"
FFMPEG_AUDIO_CODEC           = "aac"
FFMPEG_PRESET                = "fast"               # ultrafast/fast/medium/slow
FFMPEG_CRF                   = "23"                 # 품질 (낮을수록 고화질, 18~28 권장)

# ──────────────────────────────────────────
# Smart Frame Search 기본값
# ──────────────────────────────────────────
SMART_FRAME_RESULT_COUNT     = 3
SMART_FRAME_SAMPLE_INTERVAL  = 0.5   # 초
SMART_FRAME_MIN_GAP          = 1.5   # 초
SMART_FRAME_SCENE_THRESHOLD  = 30.0
SMART_FRAME_USE_SHARPNESS    = True
SMART_FRAME_USE_BRIGHTNESS   = True

# 스마트 프레임 점수 가중치
SCORE_WEIGHT_SCENE           = 0.4
SCORE_WEIGHT_SHARPNESS       = 0.4
SCORE_WEIGHT_BRIGHTNESS      = 0.2

# ──────────────────────────────────────────
# Static Snapshot (정적 장면 감지) 설정
# ──────────────────────────────────────────
UPPER_BODY_FRACTION          = 0.82   # 상단 82% 영역만 분석
STATIC_MIN_DURATION_SEC      = 2.0   # 최소 정적 유지 시간 (초)
STATIC_SAMPLE_INTERVAL       = 0.5   # 샘플링 간격 (초)
STATIC_DIFF_THRESHOLD        = 12.0  # 프레임 차이 임계값

# ──────────────────────────────────────────
# 로깅 설정
# ──────────────────────────────────────────
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ──────────────────────────────────────────
# 에러 메시지 (한국어)
# ──────────────────────────────────────────
ERROR_MESSAGES = {
    "input_error":      "입력값을 확인하고 다시 시도해주세요.",
    "ffmpeg_error":     "FFmpeg 처리 중 오류가 발생했습니다. FFmpeg 설치 여부를 확인해주세요.",
    "whisper_error":    "자막 생성 중 오류가 발생했습니다. 오디오와 Whisper 설치를 확인해주세요.",
    "file_write_error": "파일 저장 실패. 디스크 여유 공간과 쓰기 권한을 확인해주세요.",
    "smart_search_error": "스마트 프레임 검색에 실패했습니다.",
    "youtube_error":    "YouTube 처리 중 오류가 발생했습니다.",
    "unexpected_error": "예상치 못한 오류가 발생했습니다. 로그를 확인해주세요.",
}
