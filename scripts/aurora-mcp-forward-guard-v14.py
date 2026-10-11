#!/usr/bin/env python3
"""Maintain Banco Aurora's loopback-only Kubernetes MCP port-forward.

No credentials are read, no proxy/role/Secret/Deployment is mutated, and this
process never rotates Cloudflare URLs. A real MCP initialize response, not a
TCP listener or unauthenticated bridge 401, establishes forwarding health.

A health check that cannot recover fails closed. This service supervises only
the owned 18101 -> aurora-mcp:8101 port-forward and never takes over another
service's listener.
"""
import argparse
import fcntl
import http.client
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / 'agent-manager-support' / '.mcp-public-tunnel'
LOCAL_PORT = 18101
BRIDGE_PORT = 18102
REMOTE_PORT = 8101
CONTEXT = 'k3d-amp-local'
NAMESPACE = 'aurora-demo'
SERVICE = 'svc/aurora-mcp'


def ensure_dir():
    DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    # The directory may have been created by the existing tunnel publisher.
    # Never change permissions of parent directories or owned secret files.


def listen(port, timeout=1.0):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=timeout):
            return True
    except OSError:
        return False


def mcp_initialize(timeout=4):
    """Verify real MCP initialize (read-only), never print HTTP body."""
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
        'protocolVersion': '2025-06-18', 'capabilities': {},
        'clientInfo': {'name': 'aurora-forward-health-v14', 'version': '1.0'}}}
    connection = http.client.HTTPConnection('127.0.0.1', LOCAL_PORT, timeout=timeout)
    try:
        connection.request('POST', '/mcp', json.dumps(request), headers={
            'Host': 'aurora-mcp.aurora-demo.svc.cluster.local:8101',
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/event-stream',
            'MCP-Protocol-Version': '2025-06-18'})
        response = connection.getresponse()
        # The response body's content isn't printed. Avoid unlimited reads.
        if response.status != 200:
            return False
        ctype = response.getheader('Content-Type', '')
        if 'json' in ctype:
            body = response.read(8192)
            doc = json.loads(body)
            return isinstance(doc, dict) and doc.get('jsonrpc') == '2.0' and (
                isinstance(doc.get('result'), dict)) and bool(doc['result'].get('serverInfo'))
        if 'text/event-stream' in ctype:
            # SSE streams can remain open; do not block waiting for EOF.
            parts = []
            for _ in range(16):
                line = response.readline(1024)
                if not line:
                    break
                parts.append(line)
                if b'"serverInfo"' in line or b'"serverInfo"' in b''.join(parts):
                    break
            body = b''.join(parts).replace(b' ', b'')
            return b'"jsonrpc":"2.0"' in body and b'"serverInfo"' in body
        return False
    except (OSError, ValueError, json.JSONDecodeError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def _process_args(pid):
    try:
        result = subprocess.run(['ps', '-ww', '-p', str(pid), '-o', 'args='],
                                capture_output=True, text=True, timeout=4)
        return result.stdout.strip() if result.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def is_our_kubectl(pid):
    cmd = _process_args(pid)
    return (bool(re.search(r'(^|[ /])kubectl( |$)', cmd))
            and 'port-forward' in cmd
            and SERVICE in cmd
            and f'{LOCAL_PORT}:{REMOTE_PORT}' in cmd
            and CONTEXT in cmd and NAMESPACE in cmd)


def listener_pids():
    try:
        proc = subprocess.run(['lsof', '-nP', '-t', f'-iTCP:{LOCAL_PORT}',
                               '-sTCP:LISTEN'], capture_output=True,
                              text=True, timeout=5)
        return sorted({int(line) for line in proc.stdout.splitlines()
                       if line.isdecimal()})
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError('Cannot identify listener owner (lsof required)')


def known_forward_owner(pid):
    """Never terminate an identically shaped manual kubectl process.

    Recognize only a PID tracked by this guard or by the existing tunnel
    lifecycle state, with an exact process-identity check performed separately.
    """
    if not DIR.exists():
        return False
    for record in DIR.rglob('*.pid'):
        if record.is_symlink() or not record.is_file():
            continue
        name = record.name.lower()
        if name not in {'v14-forward.pid', 'kpf.pid', 'port-forward.pid',
                        'portforward.pid', 'kubectl-forward.pid'}:
            continue
        try:
            if record.stat().st_size < 40 and int(record.read_text().strip()) == pid:
                return True
        except (OSError, ValueError):
            continue
    return False


def stop_known_failed_forward():
    """Terminate only an exact Aurora kubectl forward; never SSH/other ports."""
    pids = listener_pids()
    if not pids:
        return
    if (len(pids) != 1 or not is_our_kubectl(pids[0])
            or not known_forward_owner(pids[0])):
        raise RuntimeError('Port 18101 is not owned by the Aurora recovery manager')
    pid = pids[0]
    os.kill(pid, signal.SIGTERM)
    for _ in range(40):
        if not listener_pids():
            return
        time.sleep(0.1)
    raise RuntimeError('Owned stale Kubernetes port-forward did not stop safely')


def start_forward():
    ensure_dir()
    log_path = DIR / 'v14-forward.log'
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        proc = subprocess.Popen(
            ['kubectl', '--context', CONTEXT, '-n', NAMESPACE,
             'port-forward', SERVICE, f'{LOCAL_PORT}:{REMOTE_PORT}',
             '--address', '127.0.0.1'],
            stdin=subprocess.DEVNULL, stdout=fd, stderr=subprocess.STDOUT,
            start_new_session=True, close_fds=True, cwd=ROOT)
    finally:
        os.close(fd)
    (DIR / 'v14-forward.pid').write_text(str(proc.pid) + '\n')
    for _ in range(40):
        if proc.poll() is not None:
            raise RuntimeError('Kubernetes forward exited during recovery')
        if mcp_initialize(timeout=1):
            return
        time.sleep(0.25)
    # Fail closed; no gateway health claim just because kubectl is still running.
    if proc.poll() is None and is_our_kubectl(proc.pid):
        proc.terminate()
    raise RuntimeError('Real MCP initialization failed after reconnecting forward')


def repair():
    if not listen(BRIDGE_PORT):
        raise RuntimeError('Authenticated bridge on 18102 unavailable; restore owned tunnel first')
    if mcp_initialize():
        return False
    # A short recheck avoids killing a forward during momentary pod rescheduling.
    time.sleep(0.5)
    if mcp_initialize():
        return False
    stop_known_failed_forward()
    start_forward()
    return True


def monitor():
    ensure_dir()
    fd = os.open(DIR / 'v14-monitor.lock', os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0  # Another monitor holds this lock.
        (DIR / 'v14-monitor.pid').write_text(str(os.getpid()) + '\n')
        failures = 0
        while listen(BRIDGE_PORT):
            if mcp_initialize():
                failures = 0
            else:
                failures += 1
                if failures >= 2:
                    try:
                        repair()
                        failures = 0
                        print('Aurora MCP forwarding recovered', flush=True)
                    except RuntimeError as exc:
                        # Messages are static and contain no secrets/URLs.
                        print(f'Aurora forward recovery deferred: {exc}', flush=True)
            time.sleep(3)
        print('Aurora MCP bridge unavailable; monitor exiting', flush=True)
        return 0
    finally:
        fd and os.close(fd)


def stop_monitor_and_wait():
    """Quiesce only our named monitor before tunnel publisher cleanup.

    Unlike a best-effort SIGTERM, returning ensures it cannot recreate an
    owned forward while the publisher is stopping/starting its processes.
    """
    if not monitor_running():
        return
    pid = int((DIR / 'v14-monitor.pid').read_text().strip())
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if not monitor_running():
            return
        time.sleep(0.1)
    raise RuntimeError('Owned MCP forward monitor did not stop safely')


def handoff_to_publisher():
    """Relinquish only watchdog-owned port-forward to the tunnel publisher.

    An independently owned publisher kpf or manually started port-forward is
    NEVER killed here. The publisher continues its own PID/port checks.
    """
    stop_monitor_and_wait()
    path = DIR / 'v14-forward.pid'
    if not path.exists():
        return
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('Invalid owned MCP forward PID record')
    try:
        recorded = int(path.read_text().strip())
    except ValueError:
        raise RuntimeError('Invalid owned MCP forward PID record')
    if recorded <= 1:
        raise RuntimeError('Invalid owned MCP forward PID record')
    owners = listener_pids()
    if not owners:
        # The tracked forward has already exited. No process is terminated.
        path.unlink(missing_ok=True)
        return
    if recorded not in owners:
        # A publisher/manual process owns the port, so only its own cleanup
        # rules may manage it. Leave this stale record untouched for audit.
        return
    if len(owners) != 1 or not is_our_kubectl(recorded):
        raise RuntimeError('Tracked MCP forward identity changed; refusing termination')
    os.kill(recorded, signal.SIGTERM)
    for _ in range(50):
        if not listener_pids():
            path.unlink(missing_ok=True)
            print('AURORA_MCP_HANDOFF=owned_forward_released', flush=True)
            return
        time.sleep(0.1)
    raise RuntimeError('Owned MCP forward port did not release safely')


def monitor_running():
    p = DIR / 'v14-monitor.pid'
    if not p.exists():
        return False
    try:
        pid = int(p.read_text().strip())
    except ValueError:
        return False
    return pid > 1 and 'aurora-mcp-forward-guard-v14.py monitor' in _process_args(pid)


def ensure_monitor():
    if monitor_running():
        return
    ensure_dir()
    fd = os.open(DIR / 'v14-monitor.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'monitor'],
                                stdin=subprocess.DEVNULL, stdout=fd,
                                stderr=subprocess.STDOUT, start_new_session=True,
                                close_fds=True, cwd=ROOT)
    finally:
        os.close(fd)
    for _ in range(20):
        if monitor_running():
            return
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    raise RuntimeError('Cannot start owned MCP forwarding monitor')


def gateway_proof():
    # A real positive read and real denial MUST both pass. No model or writes.
    cmd = [sys.executable, str(ROOT / 'scripts' / 'verify-aurora-agentid-denial.py'), '--run']
    for attempt in range(3):
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, cwd=ROOT, timeout=180)
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError('Native MCP gateway proof failed to execute')
        if proc.returncode == 0 and 'AGENTID_GATEWAY_DENIAL=verified:' in proc.stdout \
           and 'HTTP=403' in proc.stdout:
            print('AURORA_GATEWAY_MCP=verified (permitted read, denied HTTP 403)', flush=True)
            return
        # Only a known transient 502 merits port-forward repair and another
        # read-only attempt; NEVER retry permission mismatches as success.
        if attempt == 2 or 'HTTP=502' not in proc.stdout:
            break
        repair()
        time.sleep(2)
    raise RuntimeError('Native MCP gateway positive-read/negative-403 proof failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['ensure', 'monitor', 'stop-monitor', 'handoff', 'status'])
    parser.add_argument('--gateway-check', action='store_true')
    args = parser.parse_args()
    if args.action == 'monitor':
        return monitor()
    if args.action == 'handoff':
        handoff_to_publisher()
        print('AURORA_MCP_HANDOFF=complete', flush=True)
        return 0
    if args.action == 'stop-monitor':
        if monitor_running():
            pid = int((DIR / 'v14-monitor.pid').read_text().strip())
            os.kill(pid, signal.SIGTERM)
            print('Owned monitoring process stop requested; tunnel and forwarding retained')
        return 0
    if args.action == 'status':
        print('AURORA_MCP_FORWARD=' + ('healthy' if mcp_initialize() else 'unhealthy'))
        print('AURORA_MCP_MONITOR=' + ('running' if monitor_running() else 'stopped'))
        return 0 if mcp_initialize() else 1
    repaired = repair()
    ensure_monitor()
    print('AURORA_MCP_FORWARD=' + ('recovered' if repaired else 'healthy'))
    print('AURORA_MCP_MONITOR=running')
    if args.gateway_check:
        gateway_proof()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError) as exc:
        print('AURORA_MCP_FORWARD=failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
