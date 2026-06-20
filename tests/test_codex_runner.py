import shlex
import sys

from src.codex.runner import run_codex_exec, terminate_active_codex_processes


def test_run_codex_exec_success_writes_outputs(tmp_path) -> None:
    script = "import sys; sys.stdin.read(); print('ok'); print('err', file=sys.stderr)"

    result = run_codex_exec(
        prompt="hello",
        work_dir=tmp_path,
        codex_command=f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
        timeout_sec=5,
    )

    assert result.returncode == 0
    assert result.timed_out is False
    assert "ok" in result.stdout
    assert "err" in result.stderr
    assert (tmp_path / "prompt.txt").read_text(encoding="utf-8") == "hello"
    assert "ok" in (tmp_path / "stdout.txt").read_text(encoding="utf-8")
    assert "err" in (tmp_path / "stderr.txt").read_text(encoding="utf-8")
    assert "ok" in (tmp_path / "combined_output.txt").read_text(encoding="utf-8")
    assert (tmp_path / "last_message.txt").exists()


def test_run_codex_exec_timeout_cleans_up_and_writes_outputs(tmp_path) -> None:
    script = "import time; print('started', flush=True); time.sleep(10)"

    result = run_codex_exec(
        prompt="",
        work_dir=tmp_path,
        codex_command=f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
        timeout_sec=0.1,
    )

    assert result.timed_out is True
    assert "Timed out after 0.1 seconds." in result.stderr
    assert "Timed out after 0.1 seconds." in result.combined_output
    assert (tmp_path / "stdout.txt").exists()
    assert (tmp_path / "stderr.txt").exists()
    assert (tmp_path / "combined_output.txt").exists()
    assert (tmp_path / "last_message.txt").exists()


def test_terminate_active_codex_processes_without_active_processes() -> None:
    assert terminate_active_codex_processes() == 0
