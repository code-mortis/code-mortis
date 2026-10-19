"""Run inside Dockerfile.runner with empty temporary /profiles, no paid requests."""

import asyncio
import contextlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from code_mortis.delegated import output_schema
from code_mortis.native_runner import (
    CLAUDE_VERSION,
    CODEX_VERSION,
    NativeError,
    ReviewBody,
    auth_console,
    auth_status,
    cancel_login,
    claude_models,
    codex,
    consoles,
    locks,
    run_codex,
)
from code_mortis.security_engine import model_schema
from code_mortis.security_schemas import SecurityAnalysisSelection, SecurityFixSelection


async def codex_review_smoke(client, identity, model, task="review"):
    config = (await client.call("config/read", {"includeLayers": False}))["config"]
    permissions = config["permissions"]["mortis-review"]
    assert config["default_permissions"] == "mortis-review"
    assert permissions["extends"] is None and not permissions["workspace_roots"]
    assert {k: v for k, v in permissions["filesystem"].items() if v is not None} == {":workspace_roots": "read"}
    assert {k: v for k, v in permissions["network"].items() if v is not None} == {"enabled": False}
    call = client.call
    thread = turn = None

    async def checked_call(method, params=None):
        nonlocal thread, turn
        result = await call(method, params)
        if method == "thread/start":
            thread = result
        if method == "turn/start":
            turn = result["turn"]["id"]
            assert thread["activePermissionProfile"] == {"id": "mortis-review", "extends": None}
            assert thread["runtimeWorkspaceRoots"] == [thread["cwd"]]
            assert thread["sandbox"] == {"type": "readOnly", "networkAccess": False}
            assert thread["approvalPolicy"] == "never" and not thread["instructionSources"]
            # Stop at native protocol acceptance; empty profiles cannot use a subscription.
            raise asyncio.CancelledError
        return result

    client.call = checked_call
    try:
        with tempfile.TemporaryDirectory(prefix="mortis-context-") as temporary:
            schema = model_schema(SecurityFixSelection if task == "security_verification" else SecurityAnalysisSelection) if task.startswith("security_") else output_schema()
            if task.startswith("security_"):
                selection = schema["$defs"]["SecurityEvidenceSelection"]["properties"]
                selection["source_id"]["enum"] = ["source_1"]
                selection["start_line"]["maximum"] = selection["end_line"]["maximum"] = 1
            body = ReviewBody(
                task=task, kind="chatgpt", model=model, effort="low", prompt="Validate task startup only.",
                output_schema=schema, timeout=30,
            )
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(run_codex(identity, body, Path(temporary), {"tokens": None}), 35)
            assert turn is not None, "Codex rejected the review turn before startup"
    finally:
        client.call = call
        if turn is not None:
            await call("turn/interrupt", {"threadId": thread["thread"]["id"], "turnId": turn})
        if thread is not None:
            with contextlib.suppress(NativeError):
                await call("thread/archive", {"threadId": thread["thread"]["id"]})


async def claude_login_smoke():
    os.environ.setdefault("CM_RUNNER_TOKEN", "isolated-client-smoke-test-only-token")
    ready = asyncio.Event()

    class Terminal:
        headers = {"authorization": "Bearer " + os.environ["CM_RUNNER_TOKEN"]}
        output = ""

        async def accept(self):
            pass

        async def close(self, **kwargs):
            pass

        async def receive_text(self):
            await asyncio.Event().wait()

        async def send_text(self, text):
            self.output += text
            if any(
                host in self.output
                for host in ("https://claude.ai/", "https://claude.com/", "https://console.anthropic.com/")
            ):
                ready.set()

    identity = "00000000-0000-4000-8000-000000000003"
    task = asyncio.create_task(auth_console(Terminal(), identity))
    try:
        await asyncio.wait_for(ready.wait(), 45)
        await cancel_login(identity)
        assert task.done() and identity not in consoles and not locks[identity].locked()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    print(
        "Official Claude Code login console reached authorization and cancelled cleanly (no account authenticated)."
    )


async def main():
    assert CODEX_VERSION in subprocess.check_output(["codex", "--version"], text=True)
    assert CLAUDE_VERSION in subprocess.check_output(["claude", "--version"], text=True)
    client = await codex("00000000-0000-4000-8000-000000000001")
    try:
        assert (await client.call("account/read"))["account"] is None
        models = await client.call("model/list", {"limit": 100, "includeHidden": False})
        assert models["data"] and all(row["model"] and row["displayName"] for row in models["data"])
        await codex_review_smoke(client, "00000000-0000-4000-8000-000000000001", models["data"][0]["model"])
        for task in ("security_analysis", "security_verification"):
            await codex_review_smoke(client, "00000000-0000-4000-8000-000000000001", models["data"][0]["model"], task)
        claude_catalog = await claude_models("00000000-0000-4000-8000-000000000002")
        assert claude_catalog and all(row.id and row.name for row in claude_catalog)
        if "--auth-start" in sys.argv:
            result = await client.call("account/login/start", {"type": "chatgptDeviceCode"})
            assert (
                result["type"] == "chatgptDeviceCode"
                and result["userCode"]
                and result["verificationUrl"].startswith("https://")
            )
            await client.call("account/login/cancel", {"loginId": result["loginId"]})
            print(
                "Official ChatGPT device authorization started and cancelled successfully (no account authenticated)."
            )
        status = await auth_status("00000000-0000-4000-8000-000000000002", "claude_code")
        assert status["status"] == "disconnected"
        if "--auth-start" in sys.argv:
            await claude_login_smoke()
    finally:
        await client.close()
    print(
        "Pinned Codex review turn and sandbox, plus client model discovery and auth status, passed with empty profiles, without inference."
    )


if __name__ == "__main__":
    asyncio.run(main())
