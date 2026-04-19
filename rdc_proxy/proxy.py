"""Transparent TCP proxy + internet/cloud monitoring for rdc-proxy.

Three modes (decided per RDC connection):
- PROXY: pass-through to real cloud, capture handshake, tap telemetry into STATE
- LOCAL: replay captured handshake, ACK telemetry into STATE (no cloud)
- WAITING: no internet + no handshake — hold the RDC connection until internet
"""

import asyncio
import socket
import time

from rdc_proxy.config import CFG
from rdc_proxy.state import HANDSHAKE, STATE, have_handshake, save_handshake


# ── Internet & cloud reachability ──────────────────────────────────────────

def check_internet():
    try:
        socket.setdefaulttimeout(3)
        socket.create_connection(("8.8.8.8", 53), timeout=3).close()
        return True
    except OSError:
        return False


def resolve_cloud():
    try:
        results = socket.getaddrinfo(
            CFG["cloud_dns"], CFG["cloud_port"], socket.AF_INET, socket.SOCK_STREAM
        )
        return [r[4][0] for r in results]
    except socket.gaierror:
        return []


def check_cloud_reachable():
    for ip in resolve_cloud()[:3]:
        try:
            s = socket.create_connection((ip, CFG["cloud_port"]), timeout=5)
            s.close()
            return ip
        except OSError:
            continue
    return None


async def internet_monitor():
    interval = CFG.get("internet_check_interval_s", 30)
    loop = asyncio.get_running_loop()
    while True:
        # Run blocking probes in a thread so we don't stall handle_rdc_connection.
        up = await loop.run_in_executor(None, check_internet)
        STATE.internet_up = up
        if up:
            if STATE.internet_stable_since is None:
                STATE.internet_stable_since = time.time()
                print("[internet] connection detected, starting stability timer", flush=True)
            cloud_ip = await loop.run_in_executor(None, check_cloud_reachable)
            STATE.set_cloud_check_result(cloud_ip)
        else:
            if STATE.internet_stable_since is not None:
                print("[internet] connection lost, resetting stability timer", flush=True)
            STATE.internet_stable_since = None
            STATE.set_cloud_check_result(None)
        await asyncio.sleep(interval)


# ── TCP proxy primitives ───────────────────────────────────────────────────

async def read_exactly(reader, n, timeout=30):
    data = b""
    while len(data) < n:
        try:
            chunk = await asyncio.wait_for(reader.read(n - len(data)), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        if not chunk:
            return None
        data += chunk
    return data


async def forward_and_tap(src_reader, dst_writer, tap_fn, label=""):
    try:
        while True:
            data = await src_reader.read(8192)
            if not data:
                break
            if tap_fn:
                tap_fn(data)
            dst_writer.write(data)
            await dst_writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    except Exception as e:
        print(f"[forward:{label}] error: {e}", flush=True)


# ── Connection lifecycle / mode dispatch ───────────────────────────────────

def _local_ips():
    """Best-effort set of this host's own IPs (for detecting non-TPROXY connects)."""
    ips = {"0.0.0.0", "127.0.0.1", "::1"}
    try:
        import socket as _s
        for info in _s.getaddrinfo(_s.gethostname(), None):
            ips.add(info[4][0])
    except Exception:
        pass
    return ips


_LOCAL_IPS = _local_ips()


async def handle_rdc_connection(rdc_reader, rdc_writer):
    peer = rdc_writer.get_extra_info("peername")
    sock = rdc_writer.get_extra_info("socket")

    orig_dst = None
    if sock:
        try:
            orig_dst = sock.getsockname()
            if orig_dst[0] in _LOCAL_IPS:
                orig_dst = None  # non-TPROXY connect to us directly
        except Exception:
            pass

    print(
        f"[proxy] RDC connected from {peer}"
        + (f" (orig dst {orig_dst[0]}:{orig_dst[1]})" if orig_dst else ""),
        flush=True,
    )
    STATE.rdc_connected = True

    try:
        stable_threshold = CFG.get("internet_stable_before_proxy_s", 300)
        internet_stable = (
            STATE.internet_up
            and STATE.internet_stable_since is not None
            and (time.time() - STATE.internet_stable_since) >= stable_threshold
        )

        if internet_stable:
            cloud_ip = orig_dst[0] if orig_dst else check_cloud_reachable()
            cloud_port = orig_dst[1] if orig_dst else None
            if cloud_ip:
                await proxy_mode(rdc_reader, rdc_writer, cloud_ip, cloud_port)
                return

        if have_handshake():
            await local_mode(rdc_reader, rdc_writer)
        else:
            STATE.proxy_mode = "waiting"
            print("[proxy] no handshake + no stable internet — WAITING mode", flush=True)
            while not STATE.internet_up:
                await asyncio.sleep(5)
            cloud_ip = check_cloud_reachable()
            if cloud_ip:
                await proxy_mode(rdc_reader, rdc_writer, cloud_ip)
            else:
                print("[proxy] cloud unreachable despite internet — closing", flush=True)
                rdc_writer.close()
    finally:
        STATE.rdc_connected = False
        STATE.cloud_connected = False
        print("[proxy] RDC connection ended", flush=True)


async def proxy_mode(rdc_reader, rdc_writer, cloud_ip, cloud_port=None):
    """Bidirectional pass-through. Captures handshake on first run."""
    STATE.proxy_mode = "proxy"
    STATE.cloud_ip = cloud_ip
    cp = cloud_port or CFG["cloud_port"]
    print(f"[proxy] PROXY mode — connecting to cloud {cloud_ip}:{cp}", flush=True)

    try:
        cloud_reader, cloud_writer = await asyncio.open_connection(cloud_ip, cp)
    except OSError as e:
        print(f"[proxy] cloud connect failed: {e}", flush=True)
        if have_handshake():
            await local_mode(rdc_reader, rdc_writer)
        return

    STATE.cloud_connected = True

    try:
        cloud_greeting = await read_exactly(cloud_reader, 576, timeout=30)
        if not cloud_greeting or len(cloud_greeting) != 576:
            print("[proxy] failed to read cloud greeting", flush=True)
            return
        rdc_writer.write(cloud_greeting)
        await rdc_writer.drain()

        rdc_response = await read_exactly(rdc_reader, 576, timeout=30)
        if not rdc_response or len(rdc_response) != 576:
            print("[proxy] failed to read RDC response", flush=True)
            return
        cloud_writer.write(rdc_response)
        await cloud_writer.drain()

        config_msg = await read_exactly(cloud_reader, 36, timeout=30)
        if not config_msg or len(config_msg) != 36:
            print("[proxy] failed to read cloud config", flush=True)
            return
        rdc_writer.write(config_msg)
        await rdc_writer.drain()

        if not have_handshake():
            HANDSHAKE["cloud_greeting"] = cloud_greeting
            HANDSHAKE["rdc_response"] = rdc_response
            HANDSHAKE["config_msg"] = config_msg
            save_handshake()
            print("[proxy] handshake captured and persisted!", flush=True)

        print("[proxy] handshake complete — forwarding data", flush=True)

        rdc_to_cloud = asyncio.create_task(
            forward_and_tap(rdc_reader, cloud_writer, STATE.ingest_buffer, "rdc->cloud")
        )
        cloud_to_rdc = asyncio.create_task(
            forward_and_tap(cloud_reader, rdc_writer, None, "cloud->rdc")
        )

        done, pending = await asyncio.wait(
            [rdc_to_cloud, cloud_to_rdc], return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    except Exception as e:
        print(f"[proxy] proxy_mode error: {e}", flush=True)
    finally:
        STATE.cloud_connected = False
        try:
            cloud_writer.close()
            await cloud_writer.wait_closed()
        except Exception:
            pass
        if not STATE.internet_up and have_handshake():
            print("[proxy] internet lost during proxy — will serve locally on reconnect", flush=True)


async def local_mode(rdc_reader, rdc_writer):
    """Replay the captured cloud handshake; absorb telemetry."""
    STATE.proxy_mode = "local"
    STATE.cloud_connected = False
    print("[proxy] LOCAL mode — serving as cloud", flush=True)

    try:
        rdc_writer.write(HANDSHAKE["cloud_greeting"])
        await rdc_writer.drain()

        rdc_response = await read_exactly(rdc_reader, 576, timeout=30)
        if not rdc_response:
            print("[proxy] RDC didn't respond to greeting", flush=True)
            return

        rdc_writer.write(HANDSHAKE["config_msg"])
        await rdc_writer.drain()
        print("[proxy] local handshake complete — ingesting telemetry", flush=True)

        stable_threshold = CFG.get("internet_stable_before_proxy_s", 300)
        while True:
            data = await rdc_reader.read(8192)
            if not data:
                break
            STATE.ingest_buffer(data)
            if (
                STATE.internet_up
                and STATE.internet_stable_since
                and (time.time() - STATE.internet_stable_since) >= stable_threshold
            ):
                cloud_ip = check_cloud_reachable()
                if cloud_ip:
                    print("[proxy] internet stable + cloud reachable — terminating local session for proxy switchover", flush=True)
                    break

    except (ConnectionError, asyncio.CancelledError):
        pass
    except Exception as e:
        print(f"[proxy] local_mode error: {e}", flush=True)


# ── Server bootstrap ───────────────────────────────────────────────────────

async def start_server():
    proxy_port = CFG.get("proxy_port", 5253)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        IP_TRANSPARENT = 19
        sock.setsockopt(socket.SOL_IP, IP_TRANSPARENT, 1)
    except OSError:
        print("[proxy] WARNING: IP_TRANSPARENT not available — TPROXY won't work", flush=True)
    sock.bind(("0.0.0.0", proxy_port))
    sock.listen(32)
    sock.setblocking(False)

    server = await asyncio.start_server(handle_rdc_connection, sock=sock)
    print(f"[proxy] listening on 0.0.0.0:{proxy_port} (TPROXY)", flush=True)
    return server
