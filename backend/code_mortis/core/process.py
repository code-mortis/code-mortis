"""Bounded cancellable child processes with isolated application credentials."""

import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from code_mortis.core.errors import ReviewStopped

MAX_OUTPUT = 20_000_000


def child_environment() -> dict[str, str]:
    # Only the selected provider is configured below; application and GitHub secrets stay out.
    allowed = {
        "PATH",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SHARED_CREDENTIALS_FILE",
        "AWS_CONFIG_FILE",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_ROLE_ARN",
        "AWS_ROLE_SESSION_NAME",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    # Preserve the actual service account home. Never redirect or modify the operator's home.
    for key in ("HOME", "USERPROFILE"):
        if key in os.environ:
            env[key] = os.environ[key]
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "NO_COLOR": "1",
        }
    )
    return env


def command(
    args: list[str],
    directory: Path,
    env: dict,
    timeout: int | None = 60,
    stopped: Callable[[], bool] = lambda: False,
    poll: Callable[[], None] = lambda: None,
    *,
    binary: bool = False,
    max_output: int = MAX_OUTPUT,
) -> tuple[int, str | bytes, str | bytes]:
    if stopped():
        raise ReviewStopped("The review was cancelled or replaced by a newer commit. Open the pull request to check its latest review.")
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            args,
            cwd=directory,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=errors,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout if timeout is not None else None
        try:
            while process.poll() is None:
                if stopped():
                    raise ReviewStopped("The review was cancelled or replaced by a newer commit. Open the pull request to check its latest review.")
                if deadline is not None and time.monotonic() > deadline:
                    raise TimeoutError("The operation exceeded its configured time limit.")
                if os.fstat(output.fileno()).st_size + os.fstat(errors.fileno()).st_size > max_output:
                    raise ValueError("Review output exceeded the 20 MB safety limit.")
                poll()
                try:
                    process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    pass
            output.seek(0)
            errors.seek(0)
            if os.fstat(output.fileno()).st_size + os.fstat(errors.fileno()).st_size > max_output:
                raise ValueError("Review output exceeded the 20 MB safety limit.")
            stdout, stderr = output.read(max_output), errors.read(max_output)
            return (process.returncode, stdout, stderr) if binary else (
                process.returncode, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace"),
            )
        finally:
            if process.poll() is None:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


