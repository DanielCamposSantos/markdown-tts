from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_player_exposes_accessible_transport_and_library_controls():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    for control in (
        "previousButton", "rewindButton", "playButton", "forwardButton",
        "nextButton", "speedSelect", "generationSelect", "openGenerationButton",
    ):
        assert f'id="{control}"' in html
    assert 'aria-label="Unidade anterior"' in html
    assert 'aria-label="Próxima unidade"' in html


def test_frontend_throttles_saves_and_ignores_editor_keyboard_focus():
    javascript = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "now - lastPlaybackSave < 3000" in javascript
    assert 'matches("textarea,input,select")' in javascript
    assert 'event.code === "Space"' in javascript
    assert 'event.key === "ArrowLeft"' in javascript
    assert 'event.key === "ArrowRight"' in javascript
    assert 'window.addEventListener("beforeunload"' in javascript
    assert 'class="regenerate-button"' in javascript
    assert "/regenerate" in javascript
