"""Run the graph extractor with only bounded stdin and stdout on Linux."""

import ctypes
import errno
import importlib
import os
import resource
import signal
import sys

from code_mortis.code_graph.extract import extract
from code_mortis.code_graph.syntax import LANGUAGES
from code_mortis.schemas.code_graph import (
    EXTRACTION_SECONDS,
    MAX_REQUEST_BYTES,
    MAX_RESULT_BYTES,
    CodeGraphRequest,
)


def restrict_syscalls():
    # Install before decoding source. A child can use its inherited descriptors
    # and memory only, with no filesystem opens, sockets, or process access.
    seccomp = ctypes.CDLL("libseccomp.so.2")
    seccomp.seccomp_init.argtypes = (ctypes.c_uint32,)
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = (ctypes.c_char_p,)
    seccomp.seccomp_rule_add.argtypes = (ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint)
    seccomp.seccomp_load.argtypes = (ctypes.c_void_p,)
    seccomp.seccomp_release.argtypes = (ctypes.c_void_p,)
    seccomp.seccomp_release.restype = None
    filter_context = seccomp.seccomp_init(0x00050000 | errno.EPERM)
    if not filter_context:
        raise RuntimeError("Could not create the parser sandbox")
    try:
        for name in (b"read", b"write", b"close", b"brk", b"mmap", b"mprotect", b"munmap", b"mremap",
                     b"madvise", b"futex", b"clock_gettime", b"getrandom", b"rt_sigreturn", b"exit", b"exit_group"):
            syscall = seccomp.seccomp_syscall_resolve_name(name)
            if syscall < 0 or seccomp.seccomp_rule_add(filter_context, 0x7FFF0000, syscall, 0):
                raise RuntimeError("Could not configure the parser sandbox")
        if seccomp.seccomp_load(filter_context):
            raise RuntimeError("Could not enable the parser sandbox")
    finally:
        seccomp.seccomp_release(filter_context)


def main():
    try:
        libc = ctypes.CDLL(None)
        # Kill the disposable parser if the worker process disappears.
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) or os.getppid() != int(sys.argv[1]):
            return 5
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024**2, 256 * 1024**2))
        resource.setrlimit(resource.RLIMIT_CPU, (EXTRACTION_SECONDS, EXTRACTION_SECONDS))
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_RESULT_BYTES, MAX_RESULT_BYTES))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        for package in {entry[0].replace("tsx", "typescript") for entry in LANGUAGES.values()}:
            importlib.import_module(f"tree_sitter_{package}")
        restrict_syscalls()
    except (ImportError, OSError, RuntimeError, ValueError):
        return 5
    try:
        body = bytearray()
        while chunk := os.read(0, min(1024 * 1024, MAX_REQUEST_BYTES + 1 - len(body))):
            body.extend(chunk)
            if len(body) > MAX_REQUEST_BYTES:
                return 2
        request = CodeGraphRequest.model_validate_json(body)
        del body
        if not {"schema_version", "extractor_version"} <= request.model_fields_set:
            return 2
    except ValueError:
        return 2
    except MemoryError:
        return 3
    try:
        result = memoryview(extract(request).model_dump_json().encode())
        while result:
            result = result[os.write(1, result):]
    except OSError as exc:
        return 4 if exc.errno in {errno.ENOSPC, errno.EDQUOT, errno.EFBIG} else 3
    except (ValueError, MemoryError):
        return 3
    return 0


if __name__ == "__main__":
    os._exit(main())
