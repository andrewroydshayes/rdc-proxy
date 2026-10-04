"""Transparent TCP proxy + internet/cloud monitoring for rdc-proxy.

Three modes (decided per RDC connection):
- PROXY: pass-through to real cloud, capture handshake, tap telemetry into STATE
- LOCAL: replay captured handshake, ACK telemetry into STATE (no cloud)
- WAITING: no internet + no handshake — hold the RDC connection until internet
"""

import asyncio
import ipaddress
import socket
import struct
import time

from rdc_proxy.config import CFG
from rdc_proxy.state import HANDSHAKE, STATE, have_handshake, save_handshake


# ── Internet & cloud reachability ──────────────────────────────────────────

def resolve_cloud():
    try:
        results = socket.getaddrinfo(
            CFG["cloud_dns"], CFG["cloud_port"], socket.AF_INET, socket.SOCK_STREAM
        )
        return [r[4][0] for r in results]
    except socket.gaierror:
        return []


def check_cloud_reachable():
    for ip in resolve_cloud():
        try:
            s = socket.create_connection((ip, CFG["cloud_port"]), timeout=5)
            s.close()
            return ip
        except OSError:
            continue
    return None


def check_internet():
    return check_cloud_reachable() is not None


async def internet_monitor():
    interval = CFG.get("internet_check_interval_s", 30)
    loop = asyncio.get_running_loop()
    while True:
        cloud_ip = await loop.run_in_executor(None, check_cloud_reachable)
        up = cloud_ip is not None
        STATE.internet_up = up
        if up:
            if STATE.internet_stable_since is None:
                STATE.internet_stable_since = time.time()
                print("[internet] connection detected, starting stability timer", flush=True)
            STATE.set_cloud_check_result(cloud_ip)
        else:
            if STATE.internet_stable_since is not None:
                print("[internet] connection lost, resetting stability timer", flush=True)
            STATE.internet_stable_since = None
            STATE.set_cloud_check_result(None)
        await asyncio.sleep(interval)


# ── TCP proxy primitives ───────────────────────────────────────────────────

def is_private_or_local(ip):
    if not ip:
        return True
    if ip in ("0.0.0.0", "127.0.0.1", "::1"):
        return True
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_private or addr.is_loopback
    except ValueError:
        return False


def force_close_socket(writer):
    """Forcefully reset TCP socket by sending a TCP RST."""
    if not writer:
        return
    try:
        sock = writer.get_extra_info("socket")
        if sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
    except Exception:
        pass
    try:
        writer.close()
    except Exception:
        pass


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

async def handle_rdc_connection(rdc_reader, rdc_writer):
    peer = rdc_writer.get_extra_info("peername")
    sock = rdc_writer.get_extra_info("socket")

    orig_dst = None
    if sock:
        try:
            raw_dst = sock.getsockname()
            # If the destination address is a private/local RFC1918 address (due to router NAT port forward),
            # it is NOT a public cloud IP. We discard it so we always connect to the real public cloud IP.
            if not is_private_or_local(raw_dst[0]):
                orig_dst = raw_dst
        except Exception:
            pass

    print(
        f"[proxy] RDC connected from {peer}"
        + (f" (orig dst {orig_dst[0]}:{orig_dst[1]})" if orig_dst else ""),
        flush=True,
    )
    STATE.rdc_connected = True
    loop = asyncio.get_running_loop()

    try:
        # Determine if internet has met the stability threshold
        stable_threshold = CFG.get("internet_stable_before_proxy_s", 180)
        is_internet_stable = (
            STATE.internet_up
            and STATE.internet_stable_since is not None
            and (time.time() - STATE.internet_stable_since) >= stable_threshold
        )

        cloud_ip = orig_dst[0] if orig_dst else await loop.run_in_executor(None, check_cloud_reachable)
        cloud_port = orig_dst[1] if orig_dst else CFG.get("cloud_port", 5253)

        # If we have a handshake and internet is not yet stable, serve initial connection locally!
        if have_handshake() and not (is_internet_stable and cloud_ip):
            await local_mode(rdc_reader, rdc_writer)
            return

        # Once internet is stable and cloud is reachable, connect in PROXY mode:
        if cloud_ip and is_internet_stable:
            await proxy_mode(rdc_reader, rdc_writer, cloud_ip, cloud_port)
            return

        if have_handshake():
            await local_mode(rdc_reader, rdc_writer)
        else:
            STATE.set_proxy_mode("waiting")
            print("[proxy] no handshake + no cloud — WAITING mode", flush=True)
            while not STATE.internet_up:
                await asyncio.sleep(5)
            cloud_ip = await loop.run_in_executor(None, check_cloud_reachable)
            if cloud_ip:
                await proxy_mode(rdc_reader, rdc_writer, cloud_ip, CFG.get("cloud_port", 5253))
            else:
                print("[proxy] cloud unreachable despite internet — closing", flush=True)
                force_close_socket(rdc_writer)
    finally:
        STATE.rdc_connected = False
        STATE.cloud_connected = False
        force_close_socket(rdc_writer)
        print("[proxy] RDC connection ended", flush=True)


async def proxy_mode(rdc_reader, rdc_writer, cloud_ip, cloud_port=None):
    """Bidirectional pass-through. Captures handshake on first run."""
    STATE.set_proxy_mode("proxy")
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
        force_close_socket(rdc_writer)
        if not STATE.internet_up and have_handshake():
            print("[proxy] internet lost during proxy — will serve locally on reconnect", flush=True)


async def local_mode(rdc_reader, rdc_writer):
    """Replay the captured cloud handshake; absorb telemetry."""
    STATE.set_proxy_mode("local")
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
        loop = asyncio.get_running_loop()
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
                cloud_ip = await loop.run_in_executor(None, check_cloud_reachable)
                if cloud_ip:
                    print("[proxy] internet stable + cloud reachable — resetting local session for proxy switchover", flush=True)
                    force_close_socket(rdc_writer)
                    break

    except (ConnectionError, asyncio.CancelledError):
        pass
    except Exception as e:
        print(f"[proxy] local_mode error: {e}", flush=True)
    finally:
        force_close_socket(rdc_writer)


# ── Server bootstrap ───────────────────────────────────────────────────────

async def start_server():
    proxy_addr = CFG.get("proxy_listen_addr", "0.0.0.0")
    proxy_port = CFG.get("proxy_port", 5253)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        IP_TRANSPARENT = 19
        sock.setsockopt(socket.SOL_IP, IP_TRANSPARENT, 1)
    except OSError:
        print("[proxy] WARNING: IP_TRANSPARENT not available — TPROXY won't work", flush=True)
    sock.bind((proxy_addr, proxy_port))
    sock.listen(32)
    sock.setblocking(False)

    server = await asyncio.start_server(handle_rdc_connection, sock=sock)
    print(f"[proxy] listening on {proxy_addr}:{proxy_port} (TPROXY)", flush=True)
    return server
