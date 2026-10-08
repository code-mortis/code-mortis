"""Run inside Dockerfile.runner with empty temporary /profiles, no paid requests."""

import asyncio
import os
import subprocess
import sys

from code_mortis.native_runner import (
    CLAUDE_VERSION,
    CODEX_VERSION,
    auth_console,
    auth_status,
    cancel_login,
    claude_models,
    codex,
    consoles,
    locks,
)


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
        "Pinned Codex and Claude model discovery and auth status passed with isolated empty profiles, without inference."
    )


if __name__ == "__main__":
    asyncio.run(main())
