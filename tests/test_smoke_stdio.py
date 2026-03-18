from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.smoke


def test_stdio_smoke_script() -> None:
    script = Path(__file__).with_name("smoke_lsp_stdio.py")
    completed = subprocess.run([sys.executable, str(script)], check=False)

    assert completed.returncode == 0
