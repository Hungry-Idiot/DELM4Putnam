from __future__ import annotations

import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CodexRunResult:
    returncode: int
    stdout: str
    stderr: str
    elapsed_sec: float
    timed_out: bool
    command: list[str] | None = None
    last_message: str | None = None
    combined_output: str | None = None


def run_codex_exec(
    *,
    prompt: str,
    work_dir: Path,
    codex_command: str = "codex",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    provider: str = "relay",
    timeout_sec: int = 600,
    approval: str = "never",
    sandbox: str = "read-only",
    skip_git_repo_check: bool = True,
    include_approval_flag: bool = False,
    include_sandbox_flag: bool = True,
) -> CodexRunResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = work_dir / "prompt.txt"
    stdout_path = work_dir / "stdout.txt"
    stderr_path = work_dir / "stderr.txt"
    last_message_path = work_dir / "last_message.txt"
    combined_output_path = work_dir / "combined_output.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    command = _build_codex_command(
        codex_command=codex_command,
        model=model,
        base_url=base_url,
        provider=provider,
        approval=approval,
        sandbox=sandbox,
        skip_git_repo_check=skip_git_repo_check,
        include_approval_flag=include_approval_flag,
        include_sandbox_flag=include_sandbox_flag,
        output_last_message_path=last_message_path.resolve(),
    )
    env = os.environ.copy()
    if api_key:
        env["CODEX_API_KEY"] = api_key

    start = time.perf_counter()
    stdout = ""
    stderr = ""
    returncode = -1
    timed_out = False

    try:
        completed = subprocess.run(
            command,
            input=prompt,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
            env=env,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        returncode = completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = _to_text(exc.stdout)
        stderr = _to_text(exc.stderr)
        stderr = f"{stderr}\nTimed out after {timeout_sec} seconds.".strip()
        timed_out = True
    except FileNotFoundError as exc:
        stderr = str(exc)

    elapsed_sec = time.perf_counter() - start
    last_message = _read_optional_text(last_message_path)
    combined_output = "\n".join(
        part for part in [stdout, stderr, last_message or ""] if part
    )
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    combined_output_path.write_text(combined_output, encoding="utf-8")

    return CodexRunResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        elapsed_sec=elapsed_sec,
        timed_out=timed_out,
        command=command,
        last_message=last_message,
        combined_output=combined_output,
    )


def _build_codex_command(
    *,
    codex_command: str,
    model: str | None,
    base_url: str | None,
    provider: str,
    approval: str,
    sandbox: str,
    skip_git_repo_check: bool,
    include_approval_flag: bool,
    include_sandbox_flag: bool,
    output_last_message_path: Path | None = None,
) -> list[str]:
    command = shlex.split(codex_command)
    if not command:
        raise ValueError("codex_command must not be empty.")
    if command[-1] != "exec":
        command.append("exec")
    if skip_git_repo_check:
        command.append("--skip-git-repo-check")
    if include_approval_flag:
        command.extend(["--ask-for-approval", approval])
    if include_sandbox_flag:
        command.extend(["--sandbox", sandbox])
    if model:
        command.extend(["--model", model])
    if base_url:
        command.extend(["-c", f"model_provider={provider}"])
        command.extend(["-c", f"model_providers.{provider}.name={provider}"])
        command.extend(["-c", f"model_providers.{provider}.base_url={base_url}"])
        command.extend(["-c", f"model_providers.{provider}.env_key=CODEX_API_KEY"])
    if output_last_message_path is not None:
        command.extend(["--output-last-message", str(output_last_message_path)])
    command.append("-")
    return command


def _to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _read_optional_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None
