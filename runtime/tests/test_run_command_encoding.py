from __future__ import annotations

import sys
from pathlib import Path

from local_agent_runtime.tools._shared import run_shell


def test_powershell_type_reads_utf8_file_without_mojibake(tmp_path: Path) -> None:
    source_text = "# \u6ce8\u91ca: \u4fdd\u7559\u4e2d\u6587\nprint('\u5b8c\u6210')\n"
    source = tmp_path / "calculator.py"
    source.write_bytes(source_text.encode("utf-8"))

    stdout, stderr, exit_code, status, _duration_ms = run_shell(
        "powershell",
        "type calculator.py",
        tmp_path,
        10_000,
    )

    assert status == "completed"
    assert exit_code == 0
    assert stderr == ""
    assert stdout == source_text


def test_powershell_native_command_output_uses_utf8(tmp_path: Path) -> None:
    native_text = "\u7f16\u7801\u8f93\u51fa\u6b63\u5e38"
    command = f'"{sys.executable}" -c "print(\'{native_text}\')"'

    stdout, stderr, exit_code, status, _duration_ms = run_shell("powershell", command, tmp_path, 10_000)

    assert status == "completed"
    assert exit_code == 0
    assert stderr == ""
    assert stdout.strip() == native_text
