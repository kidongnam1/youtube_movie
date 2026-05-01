# Release Notes

## v1.0.0 — 2026-05-01

첫 GitHub 공개 릴리스. **Video Automation System V1.1 P2** 실행 패키지입니다.

### 포함 내용

| 파일 | 설명 |
|------|------|
| `run.py` | 최신 실행본 (P2) |
| `app_p2.py` | 최신 버전 백업 |
| `app_p1.py` | 이전 버전 백업 |
| `README_RUN_PACKAGE.txt` | 실행 방법 안내 |
| `video_projects/` | 프로젝트 출력용 폴더(비어 있을 수 있음) |

### 주요 기능

- 단일 비디오 선택 및 프로젝트 폴더 구조 생성
- 고정 시점 프레임 추출, FFmpeg로 오디오 추출
- 로컬 **Whisper** 자막 생성
- 구간 트림, 자막 텍스트 편집, 썸네일 선택, 볼륨 비율 조정
- **Smart Frame Search V1.1**: Auto Best Frames, Guided Range Search
- 최종 영상 내보내기 및 로깅

### 의존성

```text
pip install PySide6 opencv-python openai-whisper
```

- 시스템에 **FFmpeg**가 설치되어 PATH에 있어야 합니다.

### 실행

```powershell
python .\run.py
```

### 알려진 제한

- `.venv` 등 로컬 가상환경은 저장소에 포함하지 않습니다. 클론 후 위 의존성을 설치하세요.

---

### English summary

Initial publication of the **Video Automation System V1.1 P2** desktop runner package: PySide6 GUI, FFmpeg/Whisper pipeline, smart frame search, export. Requires FFmpeg on PATH and Python deps as listed above.
