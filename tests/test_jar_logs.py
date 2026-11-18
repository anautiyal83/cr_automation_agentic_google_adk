"""Every JAR call's full console output is kept in the run folder: jar_logs/step<N>_<NN>_<label>.log (OB-105)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from shared import jar_logs
from skills.network_config_test import jar_executor


def test_each_call_gets_its_own_numbered_log_with_the_whole_output(tmp_path):
    jar_logs.set_context(str(tmp_path), 9)
    try:
        long_out = "x" * 5000 + "\nEND"
        code = f"import sys; print({long_out!r}); print('boom', file=sys.stderr); sys.exit(3)"
        res = asyncio.run(jar_executor._run_jar([sys.executable, "-c", code], None, 60,
                                                "execution test (TC-P01 CR1/NORTH1)", cwd=str(tmp_path)))
        asyncio.run(jar_executor._run_jar([sys.executable, "-c", "print('ok')"], None, 60, "MOP generate"))
    finally:
        jar_logs.set_context()
    assert res["exit_code"] == 3
    logs = sorted((tmp_path / "jar_logs").iterdir())
    assert [p.name for p in logs] == ["step9_01_execution-test-TC-P01-CR1-NORTH1.log", "step9_02_MOP-generate.log"]
    text = logs[0].read_text(encoding="utf-8")
    assert "Exit code: 3" in text and f"Working folder: {tmp_path}" in text
    assert long_out in text and "boom" in text          # nothing cut off at 500 characters


def test_parallel_calls_never_share_a_log_and_no_run_writes_nothing(tmp_path):
    jar_logs.set_context(str(tmp_path), 5)
    try:
        paths = [jar_logs.write("ciq-processor (negative TC_1)", ["java"], None, 0, 0.1, "o", "")
                 for _ in range(3)]
    finally:
        jar_logs.set_context()
    assert len(set(paths)) == 3 and all(Path(p).is_file() for p in paths)
    assert jar_logs.write("x", ["java"], None, 0, 0.1, "", "") is None
