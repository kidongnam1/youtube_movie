"""
ui/main_window.py — V2.2
[NEW #2] 실패 항목 재시도 버튼
[NEW #3] 환경 점검 + 실행 가이드 (Preflight 강화)
[NEW #1] 소요 시간 결과 테이블에 표시
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QFileDialog,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSlider,
    QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget,
)

from config.settings import APP_NAME, DEFAULT_CAPTURE_TIMES, SUPPORTED_EXTENSIONS, ERROR_MESSAGES
from core.models import (
    WorkflowResult, SmartFrameSearchRequest, AppState,
    BatchJob, BatchStatus, BatchSummary,
)
from core.project import (
    extended_preflight_check, preflight_summary,
    find_reusable_project, build_export_summary,
)
from core.video import select_thumbnail
from core.excel_export import export_batch_to_excel
from ui.workers import (
    WorkflowWorker, SubtitleSaveWorker,
    SmartFrameWorker, SnapshotWorker, BatchWorker,
    YouTubeWorkflowWorker,
)

log = logging.getLogger(__name__)

# ──────────────────────────────────────────
# 툴팁
# ──────────────────────────────────────────
TT = {
    "state_label":     "현재 앱 상태. 대기 중 / 처리 중 / 완료.",
    "video_label":     "현재 선택된 영상 파일 이름.",
    "lbl_processed":   "이 세션에서 성공 처리된 영상 횟수.",
    "lbl_errors":      "이 세션에서 오류가 발생한 횟수.",
    "btn_preflight":   "FFmpeg·OpenCV·Whisper·디스크 확장 점검 + 실행 가이드 제공.",
    "btn_generate":    "단일 영상 전체 자동 처리 시작.",
    "txt_video_path":  "처리할 영상 경로. MP4·MOV·AVI·MKV 지원.",
    "btn_select_video":"로컬 영상 선택. 이전 프로젝트 이력 자동 감지.",
    "txt_youtube_url": "YouTube 단일 영상 URL. 플레이리스트 불가.",
    "btn_yt_preview":  "다운로드 없이 제목·길이·업로더 미리 확인.",
    "txt_proj_root":   "결과 파일 저장 최상위 폴더.",
    "txt_capture":     "프레임 추출 시간(초) 쉼표 구분. 예: 3,10,20",
    "spin_trim_start": "앞부분 자르기(초). 0=자르지 않음.",
    "spin_trim_end":   "뒷부분 자르기(초). 0=자르지 않음.",
    "slider_volume":   "볼륨 배율. 1.00x=원본.",
    "txt_subtitle":    "Whisper 생성 자막. 직접 수정 후 저장.",
    "btn_save_sub":    "자막을 백그라운드로 저장. UI 멈춤 없음.",
    "list_thumbs":     "추출된 프레임 목록. 클릭 시 미리보기.",
    "btn_set_thumb":   "선택 프레임을 썸네일로 저장.",
    "combo_mode":      "자동: 전체 탐색. 범위: 지정 구간 탐색.",
    "spin_sample_int": "샘플링 간격(초). 0.5초 권장.",
    "spin_min_gap":    "결과 프레임 최소 간격(초).",
    "spin_scene_thr":  "장면 변화 감지 민감도. 기본 30.0.",
    "btn_smart_auto":  "전체 영상에서 최적 프레임 자동 선택.",
    "btn_smart_range": "지정 범위에서 최적 프레임 탐색.",
    "txt_summary":     "처리 완료 후 전체 결과 요약.",
    "btn_open_folder": "결과 폴더를 탐색기에서 열기.",
    "btn_snapshot":    "정적 구간 자동 감지 후 JPG 저장.",
    "batch_list":      "처리할 영상 목록. 파일/폴더 추가 가능.",
    "btn_batch_add":   "배치 큐에 파일 추가 (다중 선택 가능).",
    "btn_batch_folder":"폴더 안 모든 지원 영상을 한 번에 추가.",
    "btn_batch_remove":"선택 항목을 큐에서 제거.",
    "btn_batch_clear": "큐를 전부 비웁니다.",
    "btn_batch_start": "큐의 영상을 순서대로 자동 처리.",
    "btn_batch_cancel":"현재 영상 완료 후 안전 중단.",
    # [NEW #2]
    "btn_batch_retry": "❌ 실패한 영상만 골라서 재시도합니다. 성공/재사용 항목은 건드리지 않습니다.",
    "batch_total_bar": "전체 배치 진행률.",
    "batch_result_table":"영상명·상태·처리시간·경로·오류 결과 테이블.",
}

BATCH_STATUS_KO = {
    BatchStatus.PENDING:   "⏳ 대기",
    BatchStatus.RUNNING:   "🔄 처리중",
    BatchStatus.DONE:      "✅ 완료",
    BatchStatus.FAILED:    "❌ 실패",
    BatchStatus.SKIPPED:   "⏭ 재사용",
    BatchStatus.CANCELLED: "⏹ 취소",
}
BATCH_STATUS_COLOR = {
    BatchStatus.PENDING:   "#888888",
    BatchStatus.RUNNING:   "#2d7a2d",
    BatchStatus.DONE:      "#1a6b1a",
    BatchStatus.FAILED:    "#cc3300",
    BatchStatus.SKIPPED:   "#aa7700",
    BatchStatus.CANCELLED: "#555555",
}


class MainWindow(QMainWindow):
    """Video Automation System V2.2"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} V2.2")
        self.resize(1480, 960)

        self._state:           AppState                 = AppState.IDLE
        self._original_video:  Optional[Path]           = None
        self._project_video:   Optional[Path]           = None
        self._workflow_result: Optional[WorkflowResult] = None
        self._thumbnail_paths: List[Path]               = []
        self._project_base:    Path                     = Path.cwd() / "video_projects"
        self._project_base.mkdir(parents=True, exist_ok=True)

        self._total_processed: int = 0
        self._error_count:     int = 0

        self._workflow_worker:  Optional[WorkflowWorker]    = None
        self._subtitle_worker:  Optional[SubtitleSaveWorker]= None
        self._smart_worker:     Optional[SmartFrameWorker]  = None
        self._snapshot_worker:  Optional[SnapshotWorker]    = None
        self._batch_worker:     Optional[BatchWorker]       = None

        self._batch_jobs:       List[BatchJob]              = []
        self._batch_job_id_seq: int                         = 0

        self._build_ui()

    # ══════════════════════════════════════
    # UI 구성
    # ══════════════════════════════════════

    def _build_ui(self) -> None:
        root = QWidget(); self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.addWidget(self._build_dashboard())
        tabs = QTabWidget()
        tabs.addTab(self._build_tab_input(),  "📁 입력")
        tabs.addTab(self._build_tab_edit(),   "✂️ 편집")
        tabs.addTab(self._build_tab_frames(), "🖼 프레임")
        tabs.addTab(self._build_tab_export(), "🚀 내보내기")
        tabs.addTab(self._build_tab_batch(),  "📦 배치 처리")
        lay.addWidget(tabs)
        lay.addWidget(self._build_bottom())

    def _build_dashboard(self) -> QGroupBox:
        box = QGroupBox("대시보드"); lay = QGridLayout(box)
        self.lbl_state = QLabel("상태: 대기 중"); self.lbl_state.setToolTip(TT["state_label"])
        self.lbl_video = QLabel("영상: 없음"); self.lbl_video.setToolTip(TT["video_label"])
        self.lbl_processed = QLabel("처리 완료: 0건")
        self.lbl_processed.setStyleSheet("color:#2d7a2d;font-weight:bold;")
        self.lbl_processed.setToolTip(TT["lbl_processed"])
        self.lbl_errors = QLabel("오류: 0건")
        self.lbl_errors.setStyleSheet("color:#999;")
        self.lbl_errors.setToolTip(TT["lbl_errors"])
        self.btn_preflight = QPushButton("⚙ 환경 점검 + 실행 가이드")
        self.btn_preflight.setToolTip(TT["btn_preflight"])
        self.btn_preflight.clicked.connect(self._on_preflight)
        self.btn_generate = QPushButton("▶ 단일 처리 시작")
        self.btn_generate.setToolTip(TT["btn_generate"])
        self.btn_generate.setStyleSheet(
            "QPushButton{background:#2d7a2d;color:white;font-weight:bold;padding:6px 18px;}"
            "QPushButton:disabled{background:#666;}")
        self.btn_generate.clicked.connect(self._on_generate)
        lay.addWidget(self.lbl_state, 0,0); lay.addWidget(self.lbl_video, 0,1)
        lay.addWidget(self.lbl_processed,0,2); lay.addWidget(self.lbl_errors,0,3)
        lay.addWidget(self.btn_preflight,0,4); lay.addWidget(self.btn_generate,0,5)
        return box

    def _build_tab_input(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        fb = QGroupBox("영상 파일 / YouTube URL"); fl = QGridLayout(fb)
        self.txt_video_path = QLineEdit(); self.txt_video_path.setReadOnly(True)
        self.txt_video_path.setPlaceholderText("영상 파일 선택 또는 YouTube URL 입력")
        self.txt_video_path.setToolTip(TT["txt_video_path"])
        self.btn_select_video = QPushButton("📂 파일 선택")
        self.btn_select_video.setToolTip(TT["btn_select_video"])
        self.btn_select_video.clicked.connect(self._on_select_video)
        self.txt_youtube_url = QLineEdit()
        self.txt_youtube_url.setPlaceholderText("https://www.youtube.com/watch?v=...")
        self.txt_youtube_url.setToolTip(TT["txt_youtube_url"])
        self.btn_yt_preview = QPushButton("🔍 YouTube 미리보기")
        self.btn_yt_preview.setToolTip(TT["btn_yt_preview"])
        self.btn_yt_preview.clicked.connect(self._on_youtube_preview)
        fl.addWidget(QLabel("로컬 파일"),   0,0); fl.addWidget(self.txt_video_path,  0,1); fl.addWidget(self.btn_select_video,0,2)
        fl.addWidget(QLabel("YouTube URL"),1,0); fl.addWidget(self.txt_youtube_url, 1,1); fl.addWidget(self.btn_yt_preview,  1,2)
        lay.addWidget(fb)
        pb = QGroupBox("프로젝트 저장 위치"); pl = QHBoxLayout(pb)
        self.txt_project_root = QLineEdit(str(self._project_base)); self.txt_project_root.setReadOnly(True)
        self.txt_project_root.setToolTip(TT["txt_proj_root"])
        bp = QPushButton("📂 변경"); bp.clicked.connect(self._on_select_project_root)
        pl.addWidget(self.txt_project_root); pl.addWidget(bp); lay.addWidget(pb)
        cb = QGroupBox("프레임 추출 시간 (초, 쉼표 구분)"); cl = QHBoxLayout(cb)
        self.txt_capture_times = QLineEdit(",".join(str(t) for t in DEFAULT_CAPTURE_TIMES))
        self.txt_capture_times.setToolTip(TT["txt_capture"]); cl.addWidget(self.txt_capture_times)
        lay.addWidget(cb); lay.addStretch(); return w

    def _build_tab_edit(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        tb = QGroupBox("트림 (자르기)"); tl = QGridLayout(tb)
        self.spin_trim_start = QDoubleSpinBox(); self.spin_trim_start.setRange(0,36000); self.spin_trim_start.setSuffix(" 초"); self.spin_trim_start.setToolTip(TT["spin_trim_start"])
        self.spin_trim_end   = QDoubleSpinBox(); self.spin_trim_end.setRange(0,36000);   self.spin_trim_end.setSuffix(" 초");   self.spin_trim_end.setToolTip(TT["spin_trim_end"])
        tl.addWidget(QLabel("앞"),0,0); tl.addWidget(self.spin_trim_start,0,1)
        tl.addWidget(QLabel("뒤"),1,0); tl.addWidget(self.spin_trim_end,  1,1)
        lay.addWidget(tb)
        vb = QGroupBox("볼륨"); vl = QHBoxLayout(vb)
        self.slider_volume = QSlider(Qt.Orientation.Horizontal); self.slider_volume.setRange(1,300); self.slider_volume.setValue(100); self.slider_volume.setToolTip(TT["slider_volume"])
        self.lbl_volume = QLabel("1.00x")
        self.slider_volume.valueChanged.connect(lambda v: self.lbl_volume.setText(f"{v/100:.2f}x"))
        vl.addWidget(QLabel("볼륨:")); vl.addWidget(self.slider_volume,1); vl.addWidget(self.lbl_volume)
        lay.addWidget(vb)
        sb = QGroupBox("자막 편집"); sl = QVBoxLayout(sb)
        self.txt_subtitle = QPlainTextEdit(); self.txt_subtitle.setPlaceholderText("처리 완료 후 자막이 표시됩니다."); self.txt_subtitle.setToolTip(TT["txt_subtitle"])
        self.btn_save_subtitle = QPushButton("💾 자막 저장 (백그라운드)"); self.btn_save_subtitle.setToolTip(TT["btn_save_sub"]); self.btn_save_subtitle.clicked.connect(self._on_save_subtitle)
        sl.addWidget(self.txt_subtitle); sl.addWidget(self.btn_save_subtitle)
        lay.addWidget(sb); return w

    def _build_tab_frames(self) -> QWidget:
        w = QWidget(); lay = QHBoxLayout(w)
        tb = QGroupBox("추출된 프레임"); tbl = QVBoxLayout(tb)
        self.list_thumbnails = QListWidget(); self.list_thumbnails.setToolTip(TT["list_thumbs"])
        self.list_thumbnails.currentRowChanged.connect(self._on_thumbnail_selected)
        self.btn_set_thumbnail = QPushButton("✅ 썸네일로 저장"); self.btn_set_thumbnail.setToolTip(TT["btn_set_thumb"]); self.btn_set_thumbnail.clicked.connect(self._on_set_thumbnail)
        tbl.addWidget(self.list_thumbnails); tbl.addWidget(self.btn_set_thumbnail); lay.addWidget(tb,1)
        rw = QWidget(); rl = QVBoxLayout(rw)
        pv = QGroupBox("미리보기"); pl = QVBoxLayout(pv)
        self.lbl_preview = QLabel("프레임을 선택하면 미리보기가 표시됩니다.")
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter); self.lbl_preview.setMinimumSize(420,260)
        pl.addWidget(self.lbl_preview); rl.addWidget(pv)
        sm = QGroupBox("스마트 프레임 검색"); sml = QGridLayout(sm)
        self.combo_search_mode    = QComboBox(); self.combo_search_mode.addItems(["자동 최적 프레임","범위 지정 검색"]); self.combo_search_mode.setToolTip(TT["combo_mode"])
        self.spin_result_count    = QDoubleSpinBox(); self.spin_result_count.setRange(1,10); self.spin_result_count.setValue(3); self.spin_result_count.setDecimals(0)
        self.spin_sample_interval = QDoubleSpinBox(); self.spin_sample_interval.setRange(0.1,10); self.spin_sample_interval.setValue(0.5); self.spin_sample_interval.setDecimals(2); self.spin_sample_interval.setSuffix(" 초"); self.spin_sample_interval.setToolTip(TT["spin_sample_int"])
        self.spin_min_gap         = QDoubleSpinBox(); self.spin_min_gap.setRange(0,30); self.spin_min_gap.setValue(1.5); self.spin_min_gap.setDecimals(2); self.spin_min_gap.setSuffix(" 초"); self.spin_min_gap.setToolTip(TT["spin_min_gap"])
        self.spin_scene_threshold = QDoubleSpinBox(); self.spin_scene_threshold.setRange(1,100); self.spin_scene_threshold.setValue(30); self.spin_scene_threshold.setDecimals(1); self.spin_scene_threshold.setToolTip(TT["spin_scene_thr"])
        self.spin_target_time     = QDoubleSpinBox(); self.spin_target_time.setRange(0,36000); self.spin_target_time.setSuffix(" 초")
        self.spin_search_window   = QDoubleSpinBox(); self.spin_search_window.setRange(1,60); self.spin_search_window.setValue(6); self.spin_search_window.setSuffix(" 초")
        self.btn_find_best    = QPushButton("🔍 자동 최적 프레임 찾기"); self.btn_find_best.setToolTip(TT["btn_smart_auto"]); self.btn_find_best.clicked.connect(self._on_find_best_frames)
        self.btn_refine_range = QPushButton("🎯 범위 지정 재검색");     self.btn_refine_range.setToolTip(TT["btn_smart_range"]); self.btn_refine_range.clicked.connect(self._on_refine_by_range)
        r = 0
        for lbl, wgt in [("검색 모드",self.combo_search_mode),("결과 수",self.spin_result_count),("샘플링 간격",self.spin_sample_interval),("최소 간격",self.spin_min_gap),("장면 감도",self.spin_scene_threshold),("목표 시간",self.spin_target_time),("검색 창",self.spin_search_window)]:
            sml.addWidget(QLabel(lbl),r,0); sml.addWidget(wgt,r,1); r+=1
        sml.addWidget(self.btn_find_best,r,0); sml.addWidget(self.btn_refine_range,r,1)
        rl.addWidget(sm); lay.addWidget(rw,1); return w

    def _build_tab_export(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        rb = QGroupBox("처리 결과 요약"); rl = QVBoxLayout(rb)
        self.txt_export_summary = QTextEdit(); self.txt_export_summary.setReadOnly(True); self.txt_export_summary.setToolTip(TT["txt_summary"])
        self.btn_open_folder = QPushButton("📂 결과 폴더 열기"); self.btn_open_folder.setToolTip(TT["btn_open_folder"]); self.btn_open_folder.clicked.connect(self._on_open_folder)
        rl.addWidget(self.txt_export_summary); rl.addWidget(self.btn_open_folder); lay.addWidget(rb)
        sb = QGroupBox("정적 장면 스냅샷"); sl = QVBoxLayout(sb)
        self.btn_snapshot = QPushButton("📸 정적 장면 감지 실행"); self.btn_snapshot.setToolTip(TT["btn_snapshot"]); self.btn_snapshot.clicked.connect(self._on_snapshot)
        self.lbl_snapshot_result = QLabel("결과: 없음")
        sl.addWidget(self.btn_snapshot); sl.addWidget(self.lbl_snapshot_result)
        lay.addWidget(sb); lay.addStretch(); return w

    def _build_tab_batch(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)

        # 컨트롤 버튼
        ctrl = QGroupBox("배치 큐 관리"); cl = QHBoxLayout(ctrl)
        self.btn_batch_add    = QPushButton("➕ 파일 추가");   self.btn_batch_add.setToolTip(TT["btn_batch_add"]);    self.btn_batch_add.clicked.connect(self._on_batch_add_files)
        self.btn_batch_folder = QPushButton("📂 폴더 추가");   self.btn_batch_folder.setToolTip(TT["btn_batch_folder"]); self.btn_batch_folder.clicked.connect(self._on_batch_add_folder)
        self.btn_batch_remove = QPushButton("➖ 선택 제거");   self.btn_batch_remove.setToolTip(TT["btn_batch_remove"]); self.btn_batch_remove.clicked.connect(self._on_batch_remove)
        self.btn_batch_clear  = QPushButton("🗑 전체 비우기"); self.btn_batch_clear.setToolTip(TT["btn_batch_clear"]);  self.btn_batch_clear.clicked.connect(self._on_batch_clear)
        self.btn_batch_start  = QPushButton("▶▶ 배치 시작");
        self.btn_batch_start.setStyleSheet("QPushButton{background:#1a5c99;color:white;font-weight:bold;padding:6px 16px;}QPushButton:disabled{background:#666;}")
        self.btn_batch_start.setToolTip(TT["btn_batch_start"]); self.btn_batch_start.clicked.connect(self._on_batch_start)

        # [NEW #2] 실패 재시도 버튼
        self.btn_batch_retry  = QPushButton("🔁 실패 재시도")
        self.btn_batch_retry.setStyleSheet("QPushButton{background:#8b4500;color:white;font-weight:bold;padding:6px 16px;}QPushButton:disabled{background:#666;}")
        self.btn_batch_retry.setToolTip(TT["btn_batch_retry"]); self.btn_batch_retry.clicked.connect(self._on_batch_retry)
        self.btn_batch_retry.setEnabled(False)

        self.btn_batch_cancel = QPushButton("⏹ 중단"); self.btn_batch_cancel.setToolTip(TT["btn_batch_cancel"]); self.btn_batch_cancel.clicked.connect(self._on_batch_cancel); self.btn_batch_cancel.setEnabled(False)
        for b in [self.btn_batch_add, self.btn_batch_folder, self.btn_batch_remove,
                  self.btn_batch_clear, self.btn_batch_start, self.btn_batch_retry,
                  self.btn_batch_cancel]:
            cl.addWidget(b)
        lay.addWidget(ctrl)

        # 큐 목록 + 진행 상황
        mid = QHBoxLayout()
        ql = QGroupBox("처리 큐"); qll = QVBoxLayout(ql)
        self.batch_list = QListWidget(); self.batch_list.setToolTip(TT["batch_list"])
        self.batch_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.lbl_batch_count = QLabel("0개 대기 중")
        qll.addWidget(self.batch_list); qll.addWidget(self.lbl_batch_count); mid.addWidget(ql,1)

        pr = QGroupBox("진행 상황"); prl = QVBoxLayout(pr)
        self.lbl_batch_current = QLabel("대기 중")
        self.batch_current_bar = QProgressBar(); self.batch_current_bar.setValue(0)
        self.lbl_batch_overall = QLabel("전체: 0 / 0")
        self.batch_total_bar   = QProgressBar(); self.batch_total_bar.setValue(0); self.batch_total_bar.setToolTip(TT["batch_total_bar"])
        self.lbl_batch_elapsed = QLabel("경과 시간: -")
        self.lbl_batch_elapsed.setStyleSheet("color:#1a5c99;font-weight:bold;")
        prl.addWidget(QLabel("현재 영상:")); prl.addWidget(self.lbl_batch_current)
        prl.addWidget(self.batch_current_bar)
        prl.addWidget(QLabel("전체 진행:")); prl.addWidget(self.lbl_batch_overall)
        prl.addWidget(self.batch_total_bar)
        prl.addWidget(self.lbl_batch_elapsed)
        prl.addStretch(); mid.addWidget(pr,1); lay.addLayout(mid)

        # 결과 테이블 — [NEW #1] 처리 시간 열 추가
        rt = QGroupBox("처리 결과 테이블"); rtl = QVBoxLayout(rt)
        self.batch_result_table = QTableWidget(0, 5)
        self.batch_result_table.setHorizontalHeaderLabels(
            ["영상명", "상태", "처리 시간", "최종 영상 경로", "오류 메시지"]
        )
        self.batch_result_table.setToolTip(TT["batch_result_table"])
        self.batch_result_table.horizontalHeader().setStretchLastSection(True)
        self.batch_result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.batch_result_table.setColumnWidth(0, 200)
        self.batch_result_table.setColumnWidth(1, 90)
        self.batch_result_table.setColumnWidth(2, 90)
        self.batch_result_table.setColumnWidth(3, 350)
        rtl.addWidget(self.batch_result_table); lay.addWidget(rt)
        return w

    def _build_bottom(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        self.progress_bar = QProgressBar(); self.progress_bar.setValue(0)
        self.lbl_status   = QLabel("대기 중")
        lay.addWidget(self.progress_bar); lay.addWidget(self.lbl_status)
        return w

    # ══════════════════════════════════════
    # 이벤트 — 단일 처리
    # ══════════════════════════════════════

    # [NEW #3] 환경 점검 + 실행 가이드
    def _on_preflight(self) -> None:
        result = extended_preflight_check(self._project_base)
        ok, summary = preflight_summary(result)

        guide_lines = []
        if not result.get("ffmpeg_ok"):
            guide_lines.append("❗ FFmpeg 미설치 → https://ffmpeg.org/download.html\n   또는: winget install ffmpeg")
        if not result.get("opencv_ok"):
            guide_lines.append("❗ OpenCV 미설치 → pip install opencv-python")
        if not result.get("whisper_ok"):
            guide_lines.append("❗ Whisper 미설치 → pip install openai-whisper")
        if not result.get("project_root_writable"):
            guide_lines.append(f"❗ 저장 폴더 쓰기 불가 → 다른 폴더를 선택하세요")
        if not result.get("disk_ok"):
            guide_lines.append(f"❗ 디스크 여유 공간 부족 (현재 {result.get('disk_free_gb',0)}GB) → 200MB 이상 필요")

        msg = f"{'✅ 모든 환경 정상 — 처리 시작 가능!' if ok else '⚠️ 아래 항목을 먼저 해결해주세요'}\n\n{summary}"
        if guide_lines:
            msg += "\n\n📋 실행 가이드:\n" + "\n".join(guide_lines)
        if ok:
            msg += "\n\n▶ 실행 방법:\n1. 📁 입력 탭에서 영상 파일 선택\n2. ▶ 단일 처리 시작 클릭\n또는 📦 배치 처리 탭에서 다중 처리"

        QMessageBox.information(self, "환경 점검 + 실행 가이드", msg)

    def _on_select_video(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(SUPPORTED_EXTENSIONS))
        path, _ = QFileDialog.getOpenFileName(self, "영상 파일 선택", "", f"영상 파일 ({exts})")
        if not path: return
        self._original_video = Path(path)
        self.txt_video_path.setText(path)
        self.lbl_video.setText(f"영상: {Path(path).name}")
        reuse = find_reusable_project(self._project_base, self._original_video)
        if reuse:
            paths, vpath = reuse
            if QMessageBox.question(self, "기존 프로젝트 발견",
                f"이전에 처리한 프로젝트:\n{paths.root}\n\n재사용하시겠습니까?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) == QMessageBox.StandardButton.Yes:
                self._project_video = vpath
                self.lbl_status.setText(f"기존 프로젝트 재사용: {paths.root.name}")

    def _on_select_project_root(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "저장 폴더 선택")
        if d:
            self._project_base = Path(d)
            self.txt_project_root.setText(d)

    def _on_youtube_preview(self) -> None:
        from core.youtube import fetch_youtube_metadata
        from core.models import ProcessingError as PE
        url = self.txt_youtube_url.text().strip()
        if not url: QMessageBox.warning(self,"알림","YouTube URL을 입력해주세요."); return
        try:
            meta = fetch_youtube_metadata(url, log)
            QMessageBox.information(self,"YouTube 미리보기",
                f"제목:    {meta['title']}\n길이:    {int(meta['duration']//60)}분 {int(meta['duration']%60)}초\n업로더: {meta['uploader']}")
        except PE as exc:
            QMessageBox.critical(self,"오류",str(exc))

    def _on_generate(self) -> None:
        # ── 입력 소스 판단 ──────────────────────────
        url         = self.txt_youtube_url.text().strip()
        has_local   = self._original_video is not None
        has_youtube = bool(url)

        if not has_local and not has_youtube:
            QMessageBox.warning(self, "알림",
                "영상 파일을 선택하거나\nYouTube URL을 입력해주세요.")
            return

        if self._state == AppState.BUSY:
            QMessageBox.warning(self, "알림", "현재 처리 중입니다."); return

        try:
            ct = [float(t.strip()) for t in self.txt_capture_times.text().split(",") if t.strip()]
        except ValueError:
            QMessageBox.warning(self, "오류", "프레임 추출 시간 형식 오류. 예: 3,10,20"); return

        self._set_busy(True)

        # ── 경로 A: 로컬 파일 ───────────────────────
        if has_local:
            self._workflow_worker = WorkflowWorker(
                str(self._original_video), str(self._project_base),
                self.spin_trim_start.value(), self.spin_trim_end.value(),
                self.slider_volume.value()/100.0, ct, parent=self)
            self._workflow_worker.progress.connect(self.progress_bar.setValue)
            self._workflow_worker.status.connect(self.lbl_status.setText)
            self._workflow_worker.finished_ok.connect(self._on_workflow_done)
            self._workflow_worker.failed.connect(self._on_worker_failed)
            self._workflow_worker.start()

        # ── 경로 B: YouTube URL ─────────────────────
        else:
            self._workflow_worker = YouTubeWorkflowWorker(
                youtube_url=url,
                project_base_dir=str(self._project_base),
                trim_start=self.spin_trim_start.value(),
                trim_end=self.spin_trim_end.value(),
                volume_ratio=self.slider_volume.value()/100.0,
                capture_times=ct,
                parent=self)
            self._workflow_worker.progress.connect(self.progress_bar.setValue)
            self._workflow_worker.status.connect(self.lbl_status.setText)
            self._workflow_worker.finished_ok.connect(self._on_workflow_done)
            self._workflow_worker.failed.connect(self._on_worker_failed)
            self._workflow_worker.start()

    def _on_workflow_done(self, result: WorkflowResult) -> None:
        self._workflow_result = result; self._project_video = result.input_video_path
        self._thumbnail_paths = list(result.captured_frames); self._set_busy(False)
        self._total_processed += 1; self.lbl_processed.setText(f"처리 완료: {self._total_processed}건")
        if result.text_path and result.text_path.exists():
            self.txt_subtitle.setPlainText(result.text_path.read_text(encoding="utf-8"))
        self.list_thumbnails.clear()
        for p in result.captured_frames: self.list_thumbnails.addItem(p.name)
        self.txt_export_summary.setText(build_export_summary(result, self._original_video))
        QMessageBox.information(self,"완료","처리 완료! 📁 내보내기 탭에서 결과를 확인하세요.")

    def _on_worker_failed(self, msg: str) -> None:
        self._set_busy(False)
        self._error_count += 1
        self.lbl_errors.setText(f"오류: {self._error_count}건")
        self.lbl_errors.setStyleSheet("color:#cc3300;font-weight:bold;")
        cat  = msg.split("]")[0].lstrip("[") if msg.startswith("[") else "unexpected_error"
        user = ERROR_MESSAGES.get(cat, ERROR_MESSAGES["unexpected_error"])
        QMessageBox.critical(self,"처리 오류",f"{user}\n\n상세:\n{msg}")

    def _on_thumbnail_selected(self, row: int) -> None:
        if 0 <= row < len(self._thumbnail_paths):
            pix = QPixmap(str(self._thumbnail_paths[row]))
            if not pix.isNull():
                self.lbl_preview.setPixmap(pix.scaled(
                    self.lbl_preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))

    def _on_set_thumbnail(self) -> None:
        row = self.list_thumbnails.currentRow()
        if row < 0: QMessageBox.warning(self,"알림","프레임을 먼저 선택하세요."); return
        if not self._workflow_result: QMessageBox.warning(self,"알림","먼저 처리를 완료해주세요."); return
        path = select_thumbnail(self._thumbnail_paths, row, self._workflow_result.project_paths.output_dir, log)
        QMessageBox.information(self,"저장",f"썸네일 저장: {path}")

    def _on_save_subtitle(self) -> None:
        if not self._workflow_result: QMessageBox.warning(self,"알림","먼저 처리를 완료해주세요."); return
        self.btn_save_subtitle.setEnabled(False)
        self._subtitle_worker = SubtitleSaveWorker(
            self.txt_subtitle.toPlainText(),
            self._workflow_result.project_paths.subtitles_dir, parent=self)
        self._subtitle_worker.status.connect(self.lbl_status.setText)
        self._subtitle_worker.finished_ok.connect(lambda m: (
            QMessageBox.information(self,"저장 완료",m),
            self.btn_save_subtitle.setEnabled(True)))
        self._subtitle_worker.failed.connect(lambda e: (
            QMessageBox.critical(self,"오류",e),
            self.btn_save_subtitle.setEnabled(True)))
        self._subtitle_worker.start()

    def _on_find_best_frames(self) -> None: self._run_smart_search("auto")
    def _on_refine_by_range(self)  -> None: self._run_smart_search("range")

    def _run_smart_search(self, mode: str) -> None:
        if not self._project_video or not self._workflow_result:
            QMessageBox.warning(self,"알림","먼저 처리를 완료해주세요."); return
        req = SmartFrameSearchRequest(
            video_path=self._project_video, search_mode=mode,
            result_count=int(self.spin_result_count.value()),
            sampling_interval_sec=self.spin_sample_interval.value(),
            min_frame_gap_sec=self.spin_min_gap.value(),
            scene_threshold=self.spin_scene_threshold.value(),
            target_time_sec=self.spin_target_time.value() if mode=="range" else None,
            search_window_sec=self.spin_search_window.value() if mode=="range" else None)
        self._smart_worker = SmartFrameWorker(req, self._workflow_result.project_paths.thumbnails_dir, parent=self)
        self._smart_worker.status.connect(self.lbl_status.setText)
        self._smart_worker.finished_ok.connect(self._on_smart_done)
        self._smart_worker.failed.connect(self._on_worker_failed)
        self._smart_worker.start()

    def _on_smart_done(self, result) -> None:
        new = [c.frame_path for c in result.candidates if c.frame_path]
        self._thumbnail_paths.extend(new)
        for p in new: self.list_thumbnails.addItem(f"[스마트] {p.name}")
        QMessageBox.information(self,"완료",f"스마트 검색: {len(new)}개 프레임")

    def _on_snapshot(self) -> None:
        if not self._project_video or not self._workflow_result:
            QMessageBox.warning(self,"알림","먼저 처리를 완료해주세요."); return
        self._snapshot_worker = SnapshotWorker(self._project_video, self._workflow_result.project_paths.root, parent=self)
        self._snapshot_worker.status.connect(self.lbl_status.setText)
        self._snapshot_worker.finished_ok.connect(lambda p: self.lbl_snapshot_result.setText(f"저장: {p}"))
        self._snapshot_worker.failed.connect(self._on_worker_failed)
        self._snapshot_worker.start()

    def _on_open_folder(self) -> None:
        folder = str(self._workflow_result.project_paths.root if self._workflow_result else self._project_base)
        os.startfile(folder) if os.name=="nt" else __import__("subprocess").Popen(["xdg-open",folder])

    # ══════════════════════════════════════
    # 이벤트 — 배치 처리
    # ══════════════════════════════════════

    def _on_batch_add_files(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(SUPPORTED_EXTENSIONS))
        paths, _ = QFileDialog.getOpenFileNames(self,"영상 파일 선택 (다중)","",f"영상 파일 ({exts})")
        for p in paths: self._add_batch_job(Path(p))
        self._refresh_batch_count()

    def _on_batch_add_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self,"폴더 선택")
        if not d: return
        added = 0
        for ext in SUPPORTED_EXTENSIONS:
            for f in Path(d).glob(f"*{ext}"):
                self._add_batch_job(f); added += 1
        if added == 0:
            QMessageBox.information(self,"알림","폴더에서 지원 형식 영상을 찾지 못했습니다.")
        self._refresh_batch_count()

    def _add_batch_job(self, path: Path) -> None:
        self._batch_job_id_seq += 1
        job = BatchJob(
            job_id=self._batch_job_id_seq, video_path=path,
            trim_start=self.spin_trim_start.value(),
            trim_end=self.spin_trim_end.value(),
            volume_ratio=self.slider_volume.value()/100.0,
            capture_times=[float(t.strip()) for t in self.txt_capture_times.text().split(",") if t.strip()])
        self._batch_jobs.append(job)
        item = QListWidgetItem(f"⏳ {path.name}")
        item.setData(Qt.ItemDataRole.UserRole, job.job_id)
        self.batch_list.addItem(item)

    def _on_batch_remove(self) -> None:
        for item in self.batch_list.selectedItems():
            jid = item.data(Qt.ItemDataRole.UserRole)
            self._batch_jobs = [j for j in self._batch_jobs if j.job_id != jid]
            self.batch_list.takeItem(self.batch_list.row(item))
        self._refresh_batch_count()

    def _on_batch_clear(self) -> None:
        self._batch_jobs.clear(); self.batch_list.clear()
        self.batch_result_table.setRowCount(0); self._refresh_batch_count()
        self.btn_batch_retry.setEnabled(False)

    def _on_batch_start(self) -> None:
        pending = [j for j in self._batch_jobs if j.status == BatchStatus.PENDING]
        if not pending:
            QMessageBox.information(self,"알림","처리할 영상이 없습니다."); return
        self._start_batch_worker(pending)

    # [NEW #2] 실패 항목만 재시도
    def _on_batch_retry(self) -> None:
        failed = [j for j in self._batch_jobs if j.status == BatchStatus.FAILED]
        if not failed:
            QMessageBox.information(self,"알림","재시도할 실패 항목이 없습니다."); return

        # 실패 항목 status를 PENDING으로 리셋
        for job in failed:
            job.status        = BatchStatus.PENDING
            job.error_message = ""
            job.result        = None
            job.start_time    = None
            job.end_time      = None
            self._update_batch_list_item(job.job_id, "⏳ 대기 (재시도)", "#888888")

        QMessageBox.information(self,"재시도",
            f"❌ 실패 {len(failed)}건을 재시도합니다.")
        self._start_batch_worker(failed)

    def _start_batch_worker(self, jobs: list) -> None:
        """공통 배치 시작 로직."""
        self.btn_batch_start.setEnabled(False)
        self.btn_batch_retry.setEnabled(False)
        self.btn_batch_cancel.setEnabled(True)
        self.batch_total_bar.setMaximum(len(jobs))
        self.batch_total_bar.setValue(0)
        self.lbl_batch_overall.setText(f"전체: 0 / {len(jobs)}")

        self._batch_worker = BatchWorker(jobs, self._project_base, parent=self)
        self._batch_worker.job_started.connect(self._on_batch_job_started)
        self._batch_worker.job_progress.connect(self._on_batch_job_progress)
        self._batch_worker.job_done.connect(self._on_batch_job_done)
        self._batch_worker.job_failed.connect(self._on_batch_job_failed)
        self._batch_worker.job_skipped.connect(self._on_batch_job_skipped)
        self._batch_worker.batch_progress.connect(self._on_batch_overall_progress)
        self._batch_worker.batch_done.connect(self._on_batch_done)
        self._batch_worker.status.connect(self.lbl_status.setText)
        self._batch_worker.start()

    def _on_batch_cancel(self) -> None:
        if self._batch_worker: self._batch_worker.cancel()
        self.btn_batch_cancel.setEnabled(False)

    def _on_batch_job_started(self, job_id: int, name: str) -> None:
        self._update_batch_list_item(job_id,"🔄 처리 중...","#2d7a2d")
        self.lbl_batch_current.setText(name); self.batch_current_bar.setValue(0)

    def _on_batch_job_progress(self, job_id: int, pct: int) -> None:
        self.batch_current_bar.setValue(pct)
        job = self._find_job(job_id)
        if job and job.start_time:
            from datetime import datetime
            elapsed = (datetime.now() - job.start_time).total_seconds()
            self.lbl_batch_elapsed.setText(
                f"경과: {elapsed:.0f}초" if elapsed < 60
                else f"경과: {int(elapsed//60)}분 {int(elapsed%60):02d}초")

    def _on_batch_job_done(self, job_id: int, result: object) -> None:
        job = self._find_job(job_id)
        elapsed = job.elapsed_str if job else "-"
        self._update_batch_list_item(job_id, f"✅ 완료 ({elapsed})", "#1a6b1a")
        self._total_processed += 1
        self.lbl_processed.setText(f"처리 완료: {self._total_processed}건")
        if job and job.result:
            self._add_result_row(job_id, job.video_path.name, "✅ 완료",
                                 elapsed, str(job.result.final_video_path or ""), "")

    def _on_batch_job_failed(self, job_id: int, msg: str) -> None:
        self._update_batch_list_item(job_id,"❌ 실패","#cc3300")
        self._error_count += 1
        self.lbl_errors.setText(f"오류: {self._error_count}건")
        self.lbl_errors.setStyleSheet("color:#cc3300;font-weight:bold;")
        job  = self._find_job(job_id)
        name = job.video_path.name if job else f"job_{job_id}"
        elapsed = job.elapsed_str if job else "-"
        self._add_result_row(job_id, name, "❌ 실패", elapsed, "", msg[:120])

    def _on_batch_job_skipped(self, job_id: int, proj: str) -> None:
        self._update_batch_list_item(job_id,"⏭ 재사용","#aa7700")
        job  = self._find_job(job_id)
        name = job.video_path.name if job else f"job_{job_id}"
        self._add_result_row(job_id, name, "⏭ 재사용", "-", proj, "기존 프로젝트 재사용")

    def _on_batch_overall_progress(self, done: int, total: int) -> None:
        self.batch_total_bar.setValue(done)
        self.lbl_batch_overall.setText(f"전체: {done} / {total}")

    def _on_batch_done(self, summary: BatchSummary) -> None:
        self.btn_batch_start.setEnabled(True)
        self.btn_batch_cancel.setEnabled(False)
        self.batch_current_bar.setValue(100)
        self.lbl_batch_current.setText("배치 완료")

        # 실패 항목이 있으면 재시도 버튼 활성화
        has_failed = any(j.status == BatchStatus.FAILED for j in self._batch_jobs)
        self.btn_batch_retry.setEnabled(has_failed)

        # Excel 자동 저장
        xlsx_info = "저장 실패"
        try:
            xlsx_path = export_batch_to_excel(
                self._batch_jobs, summary,
                self._project_base / "batch_reports", log)
            xlsx_info = str(xlsx_path)
            self.lbl_status.setText(f"📊 Excel 저장: {xlsx_path.name}")
        except Exception as exc:
            log.warning("Excel 저장 실패: %s", exc)

        msg = (
            f"배치 처리 완료!\n\n"
            f"✅ 성공:    {summary.done}건\n"
            f"❌ 실패:    {summary.failed}건\n"
            f"⏭ 재사용:  {summary.skipped}건\n"
            f"⏹ 취소:    {summary.cancelled}건\n"
            f"─────────────────\n"
            f"성공률: {summary.success_rate}%\n\n"
            f"📊 Excel 저장:\n{xlsx_info}"
        )
        if has_failed:
            msg += f"\n\n🔁 실패 {summary.failed}건은 '실패 재시도' 버튼으로 다시 처리할 수 있습니다."
        QMessageBox.information(self, "배치 완료", msg)

    # ── 배치 헬퍼 ───────────────────────────

    def _find_job(self, job_id: int) -> Optional[BatchJob]:
        return next((j for j in self._batch_jobs if j.job_id == job_id), None)

    def _update_batch_list_item(self, job_id: int, label: str, color: str) -> None:
        for i in range(self.batch_list.count()):
            item = self.batch_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == job_id:
                job  = self._find_job(job_id)
                name = job.video_path.name if job else f"job_{job_id}"
                item.setText(f"{label}  {name}")
                item.setForeground(QColor(color))
                break

    def _add_result_row(self, job_id: int, name: str, status: str,
                        elapsed: str, path: str, msg: str) -> None:
        row = self.batch_result_table.rowCount()
        self.batch_result_table.insertRow(row)
        for col, val in enumerate([name, status, elapsed, path, msg]):
            self.batch_result_table.setItem(row, col, QTableWidgetItem(val))

    def _refresh_batch_count(self) -> None:
        pending = sum(1 for j in self._batch_jobs if j.status == BatchStatus.PENDING)
        self.lbl_batch_count.setText(f"{pending}개 대기 중 (전체 {len(self._batch_jobs)}개)")

    # ══════════════════════════════════════
    # 헬퍼
    # ══════════════════════════════════════

    def _set_busy(self, busy: bool) -> None:
        self._state = AppState.BUSY if busy else AppState.PROJECT_READY
        self.lbl_state.setText("상태: 처리 중..." if busy else "상태: 완료 ✅")
        self.btn_generate.setEnabled(not busy)
        self.btn_find_best.setEnabled(not busy)
        self.btn_refine_range.setEnabled(not busy)
        if not busy: self.progress_bar.setValue(100)
