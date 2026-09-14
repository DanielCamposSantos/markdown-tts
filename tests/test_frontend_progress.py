import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_progress_card_has_detailed_fields_and_phase_labels():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    for element_id in ("progressPhase", "progressElapsed", "progressEta"):
        assert f'id="{element_id}"' in html
    for phase in ("model_loading", "generation", "decode", "assemble", "export", "publish"):
        assert f'{phase}:' in javascript
    assert "job.queue_position" in javascript
    assert "job.eta_seconds == null" in javascript
    assert 'id="cancelJobButton"' in html
    assert 'fetch(`/api/jobs/${activeJobId}/cancel`' in javascript


def test_progress_time_formatter_examples_without_browser():
    javascript = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    start = javascript.index("function formatProgressTime")
    end = javascript.index("function renderJobProgress")
    helper = javascript[start:end]
    script = helper + "\nconsole.log(JSON.stringify([formatProgressTime(8), formatProgressTime(65), formatProgressTime(3661)]));"
    result = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True,
    )
    assert result.stdout.strip() == '["00:08","01:05","1:01:01"]'
