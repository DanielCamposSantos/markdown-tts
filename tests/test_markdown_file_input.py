import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]


def test_file_helpers_accept_extensions_reject_invalid_and_derive_title():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node não disponível para helpers JavaScript")
    script = """
const h = require('./static/file_input.js');
console.log(JSON.stringify({
  md: h.validate({name:'redes.md', size:100}),
  markdown: h.validate({name:'Aula.MARKDOWN', size:100}),
  invalid: h.validate({name:'redes.txt', size:100}),
  large: h.validate({name:'redes.md', size:h.MAX_BYTES + 1}),
  title: h.titleFromFilename('redes.md')
}));
"""
    result = subprocess.run([node, "-e", script], cwd=ROOT, text=True, capture_output=True, check=True)
    data = json.loads(result.stdout)
    assert data == {"md": None, "markdown": None, "invalid": "Selecione um arquivo .md ou .markdown.",
                    "large": "O arquivo excede o limite de 5 MiB.", "title": "redes"}


def test_file_ui_uses_drop_events_utf8_confirmation_and_debounce():
    javascript = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    for event in ("dragenter", "dragover", "dragleave", "drop"):
        assert f'addEventListener("{event}"' in javascript
    assert 'new TextDecoder("utf-8", {fatal: true})' in javascript
    assert 'confirm("Substituir o Markdown atual' in javascript
    assert "setTimeout(updatePreview, 350)" in javascript
    file_loader = javascript[javascript.index("async function loadMarkdownFile"):javascript.index("function setProgress")]
    assert "startGeneration" not in file_loader
