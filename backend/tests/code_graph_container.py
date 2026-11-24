"""Run graph extraction and sandbox checks inside the Code Mortis image."""

import base64
import errno
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

from code_mortis.code_graph import client
from code_mortis.code_graph.syntax import LANGUAGES
from code_mortis.core import file_policy
from code_mortis.schemas.code_graph import CodeGraphRequest, blob_sha


def payload(files, **limits):
    return CodeGraphRequest(
        repository_id=1, commit_sha="a" * 40, policy_digest="b" * 64, limits=limits,
        files=[
            dict(path=path, blob_sha=blob_sha(source), size_bytes=len(source),
                 content_base64=(base64.b64encode(source).decode()
                                 if Path(path).suffix in LANGUAGES and not file_policy.excluded(path) else None),
                 exclusion="excluded" if file_policy.excluded(path) else None)
            for path, source in files.items()
        ],
    )


def check_isolation():
    from code_mortis.code_graph.sandbox import restrict_syscalls

    pid = os.getpid()
    restrict_syscalls()
    for operation in (lambda: os.open("/proc/self/environ", os.O_RDONLY), socket.socket, lambda: os.kill(pid, 0)):
        try:
            operation()
        except OSError as exc:
            assert exc.errno == errno.EPERM, exc
        else:
            raise AssertionError("A disallowed parser operation succeeded")


def main():
    if sys.argv[1:] == ["isolation"]:
        check_isolation()
        return

    subprocess.run([sys.executable, str(Path(__file__).resolve()), "isolation"], env={}, check=True)
    fixture = Path(__file__).with_name("fixtures") / "code_graph_parser.json"
    files = {path: text.encode() for path, text in json.loads(fixture.read_text()).items()}
    result = client.extract(payload(files))
    assert len(result.files) == len(files)
    assert len(result.symbols) == 37
    assert len(result.imports) == 20

    # Exercise the saved upper source limit inside the production sandbox.
    largest = b"#" + b"a" * (4 * 1024 * 1024 - 2) + b"\n"
    result = client.extract(payload({f"{i}.py": largest for i in range(8)},
                                    max_file_bytes=len(largest), max_source_bytes=8 * len(largest)))
    assert len(result.files) == 8 and all(item.status == "parsed" for item in result.files)
    assert not result.symbols
    print("Graph fixtures, 32 MiB extraction, and syscall isolation passed.")


if __name__ == "__main__":
    main()
