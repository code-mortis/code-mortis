"""Disposable Docker acceptance checks for Phase 1B. No GitHub or model calls."""

import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "disposable-code-graph-test-token"
ENV = os.environ | {"CM_CODE_GRAPH_TOKEN": TOKEN, "POSTGRES_PASSWORD": "test-only",
                    "CM_RUNNER_TOKEN": "test-only", "COMPOSE_PROFILES": "", "CM_PUBLIC_HOST": "example.test", "COOLIFY_RESOURCE_UUID": "test-only"}
COMPOSE = ["docker", "compose", "-f", "compose.code-graph.test.yaml"]


def command(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, env=ENV, text=True, capture_output=True, check=True, **kwargs).stdout


def inside(container, code, *args):
    return command(["docker", "exec", container, "python", "-c", code, *args])


def configurations():
    for base in ("compose.yaml", "compose.dev.yaml"):
        prefix = ["docker", "compose", "--env-file", "/dev/null", "-f", base]
        # Disabled installations must resolve without a graph token or parser service.
        disabled_env = ENV | {"COMPOSE_PROFILES": "", "CM_CODE_GRAPH_TOKEN": ""}
        disabled = subprocess.run(prefix + ["config", "--services"], cwd=ROOT, env=disabled_env,
                                  text=True, capture_output=True, check=True).stdout.splitlines()
        assert "code-graph-parser" not in disabled and "review-worker" in disabled
        with tempfile.TemporaryDirectory() as directory:
            environment = Path(directory) / "profiles.env"
            environment.write_text("COMPOSE_PROFILES=webhooks,code-graph\n")
            # Exercise the persistent env-file setting, not a --profile override.
            enabled_env = {key: value for key, value in ENV.items() if key != "COMPOSE_PROFILES"}
            enabled_prefix = ["docker", "compose", "--env-file", str(environment), "-f", base]
            enabled = subprocess.run(enabled_prefix + ["config", "--services"], cwd=ROOT, env=enabled_env,
                                     text=True, capture_output=True, check=True).stdout.splitlines()
            assert "code-graph-parser" in enabled
            if base == "compose.dev.yaml":
                assert "relay" in enabled
        configured = json.loads(command(prefix + ["--profile", "code-graph", "config", "--format", "json", "--no-env-resolution"]))
        services = configured["services"]
        parser = services["code-graph-parser"]
        assert set(parser["networks"]) == {"code-graph"}
        assert set(parser["environment"]) == {"CM_CODE_GRAPH_TOKEN"}
        assert not parser.get("volumes") and not parser.get("ports") and not parser.get("env_file")
        assert configured["networks"]["code-graph"]["internal"] is True
        assert {name for name, service in services.items() if "code-graph" in service.get("networks", {})} == {"code-graph-parser", "review-worker"}
        assert set(services["review-worker"]["networks"]) == {"default", "runners", "code-graph"}
        stores = [(name, volume) for name, service in services.items() for volume in service.get("volumes", [])
                  if volume.get("source") == "code-graph-data"]
        assert len(stores) == 1 and stores[0][0] == "review-worker"
        assert stores[0][1]["target"] == "/var/lib/code-mortis/code-graph" and not stores[0][1].get("read_only")
        if base == "compose.yaml":
            coolify = json.loads(command(prefix + ["-f", "compose.coolify.yaml", "--profile", "code-graph", "config", "--format", "json", "--no-env-resolution"]))
            assert coolify["services"]["code-graph-parser"] == parser


STOP_CHILD = '''
import os,signal,time
from pathlib import Path
for _ in range(1000):
    for path in Path('/proc').glob('[0-9]*/cmdline'):
        try: args=path.read_bytes().split(b'\\0')
        except (FileNotFoundError, PermissionError, ProcessLookupError): continue
        if len(args)>2 and args[1:3]==[b'-m',b'code_mortis.code_graph.extract']:
            pid=int(path.parent.name); os.kill(pid,signal.SIGSTOP); print(pid,flush=True); raise SystemExit
    time.sleep(.01)
raise RuntimeError('Could not stop the real parser child')
'''


def termination(container, mode):
    process = subprocess.Popen(COMPOSE + ["run", "--rm", "probe", mode], cwd=ROOT, env=ENV,
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        pid = int(inside(container, STOP_CHILD).strip())
        busy = inside(container, '''
import os,urllib.request,urllib.error
request=urllib.request.Request('http://localhost:8091/extract',b'{}',headers={'Authorization':'Bearer '+os.environ['CM_CODE_GRAPH_TOKEN'],'Content-Type':'application/json'})
try: urllib.request.urlopen(request,timeout=5); raise AssertionError('Expected busy')
except urllib.error.HTTPError as e: assert e.code==409
print('busy rejected')
''')
        assert "busy rejected" in busy
        output, error = process.communicate(timeout=135)
        assert process.returncode == 0, error
        # Cancellation is observed asynchronously by the parser's socket monitor.
        inside(container, '''
import time,sys
from pathlib import Path
for _ in range(100):
    if not Path('/proc/'+sys.argv[1]).exists(): break
    time.sleep(.05)
else: raise AssertionError('Parser child survived cancellation/deadline')
assert not list(Path('/tmp').glob('code-graph-*'))
''', str(pid))
        return json.loads(output.strip())
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def main():
    configurations()
    report = {"checked_at": datetime.now(UTC).isoformat(),
              "baseline_commit": command(["git", "rev-parse", "HEAD"]).strip(),
              "local_changes": True, "compose_prod_dev_optional": True}
    command(COMPOSE + ["--profile", "checks", "build"], timeout=300)
    try:
        command(COMPOSE + ["up", "-d", "--wait", "code-graph-parser", "unrelated-peer"], timeout=60)
        container = command(COMPOSE + ["ps", "-q", "code-graph-parser"]).strip()
        info = json.loads(command(["docker", "inspect", container]))[0]
        report["parser_image_id"] = info["Image"]
        host = info["HostConfig"]
        assert host["Memory"] == host["MemorySwap"] == 256 * 1024**2
        assert host["NanoCpus"] == 10**9 and host["PidsLimit"] == 32
        assert host["ReadonlyRootfs"] and host["CapDrop"] == ["ALL"] and host["Init"]
        assert "no-new-privileges:true" in host["SecurityOpt"]
        assert not host["PortBindings"] and not host["Binds"]
        assert info["Config"]["User"] == "65534:65534"
        networks = info["NetworkSettings"]["Networks"]
        assert len(networks) == 1
        network = json.loads(command(["docker", "network", "inspect", next(iter(networks))]))[0]
        assert network["Internal"] is True
        missing_token = subprocess.run(COMPOSE + ["run", "--rm", "--no-deps", "-e", "CM_CODE_GRAPH_TOKEN=", "code-graph-parser"],
                                       cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=15, check=False)
        assert missing_token.returncode != 0 and "Run upgrade-env" in missing_token.stderr
        report["missing_token_rejected"] = True
        report["fixtures_and_max_source"] = json.loads(command(COMPOSE + ["run", "--rm", "probe"], timeout=60).strip())
        peer = command(COMPOSE + ["ps", "-q", "unrelated-peer"]).strip()
        peer_info = json.loads(command(["docker", "inspect", peer]))[0]
        peer_ip = next(iter(peer_info["NetworkSettings"]["Networks"].values()))["IPAddress"]
        inside(peer, "import socket; [socket.create_connection(('127.0.0.1',port),timeout=1).close() for port in (5432,6379)]")
        report["runtime"] = json.loads(inside(container, '''
import json,os,socket,sys
from pathlib import Path
assert not Path('/tmp/CODE_GRAPH_REPO_EXECUTED').exists()
assert not Path('/var/run/docker.sock').exists()
assert not any(k.startswith(('CM_DATABASE','CM_REDIS','GITHUB','OPENAI','ANTHROPIC','AWS_')) for k in os.environ)
assert os.getuid()==65534
try: Path('/etc/code-graph-write-trap').write_text('bad')
except OSError: pass
else: raise AssertionError('Writable root')
blocked=[]
for host,port in [('1.1.1.1',443),('8.8.8.8',53),('postgres',5432),('redis',6379),('host.docker.internal',55439),(sys.argv[1],5432),(sys.argv[1],6379)]:
    try:
        connection=socket.create_connection((host,port),timeout=1); connection.close()
    except OSError: blocked.append([host,port])
    else: raise AssertionError('Unexpected network access: '+host)
assert Path('/sys/fs/cgroup/memory.max').read_text().strip()==str(256*1024**2)
assert Path('/sys/fs/cgroup/memory.swap.max').read_text().strip()=='0'
assert Path('/sys/fs/cgroup/cpu.max').read_text().split()==['100000','100000']
assert os.statvfs('/tmp').f_blocks*os.statvfs('/tmp').f_frsize==128*1024**2
print(json.dumps({'network_denials':blocked,'memory_max_bytes':256*1024**2,'swap_bytes':0,'cpu_quota':1,'tmpfs_bytes':128*1024**2,'fixture_peak_bytes':int(Path('/sys/fs/cgroup/memory.peak').read_text()),'repository_execution_marker_absent':True}))
''', peer_ip))
        report["cancellation"] = termination(container, "cancel")
        print("Cancellation passed; checking the full 120-second deadline.", file=sys.stderr, flush=True)
        report["deadline"] = termination(container, "deadline")
        report["tmpfs_limit"] = inside(container, '''
import errno
from pathlib import Path
path=Path('/tmp/limit-probe')
try:
    with path.open('wb') as stream:
        for _ in range(129): stream.write(b'x'*1024**2)
except OSError as e: assert e.errno==errno.ENOSPC
else: raise AssertionError('tmpfs limit not enforced')
finally: path.unlink(missing_ok=True)
print('ENOSPC at 128 MiB')
''').strip()
        # The memory test runs in this disposable cgroup, never in an application worker.
        before = int(inside(container, "from pathlib import Path; print(dict(line.split() for line in Path('/sys/fs/cgroup/memory.events').read_text().splitlines())['oom_kill'])").strip())
        oom = subprocess.run(["docker", "exec", container, "python", "-c", "payload=bytearray(300*1024**2)"], capture_output=True, check=False)
        assert oom.returncode == 137, oom.returncode
        after = int(inside(container, "from pathlib import Path; print(dict(line.split() for line in Path('/sys/fs/cgroup/memory.events').read_text().splitlines())['oom_kill'])").strip())
        assert after > before
        report["memory_limit"] = "300 MiB allocation killed inside 256 MiB cgroup"
        report["recovered"] = json.loads(command(COMPOSE + ["run", "--rm", "probe"], timeout=60).strip())["passed"]
        output = json.dumps(report, indent=2) + "\n"
        if len(sys.argv) > 1:
            Path(sys.argv[1]).write_text(output)
        print(output)
    finally:
        command(COMPOSE + ["--profile", "checks", "down", "--remove-orphans"], timeout=60)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(exc.stderr, file=sys.stderr)
        raise
