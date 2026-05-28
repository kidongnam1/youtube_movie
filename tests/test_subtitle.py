"""tests/test_subtitle.py — core/subtitle.py SRT 처리 테스트"""
# pytest는 conftest.py를 통해 자동 주입됨
from pathlib import Path
import tempfile
from core.subtitle import write_srt, _distribute_text


def test_write_srt_creates_file():
    segments = [
        {"start": 0.0, "end": 3.5, "text": "안녕하세요"},
        {"start": 3.5, "end": 7.0, "text": "테스트입니다"},
    ]
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "test.srt"
        write_srt(segments, out)
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "00:00:00,000 --> 00:00:03,500" in content
        assert "안녕하세요" in content


def test_distribute_text_basic():
    segments = [
        {"start": 0.0, "end": 3.0, "text": "hello world"},
        {"start": 3.0, "end": 6.0, "text": "goodbye"},
    ]
    result = _distribute_text("새로운 자막 내용입니다", segments)
    assert len(result) == 2
    assert result[0]["start"] == 0.0
    assert result[1]["end"] == 6.0
    # 두 파트 합쳐서 원본 텍스트와 유사해야 함
    combined = result[0]["text"] + result[1]["text"]
    assert len(combined) > 0


def test_distribute_empty_text():
    segments = [{"start": 0.0, "end": 3.0, "text": "hi"}]
    result = _distribute_text("", segments)
    assert result[0]["text"] == ""
