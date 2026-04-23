"""Entry point: assembles all the pieces and runs the asyncio loop."""

import asyncio
import os
import threading

from rdc_proxy.config import CFG, load_config
from rdc_proxy.dashboard import collect_traffic
from rdc_proxy.plugins import load_plugins
from rdc_proxy.proxy import internet_monitor, start_server
from rdc_proxy.state import STATE, have_handshake, load_handshake
from rdc_proxy.web import run_web


async def main():
    config_dir = os.environ.get("RDC_PROXY_CONFIG_DIR", "/etc/rdc-proxy")
    config_path = os.path.join(config_dir, "config.json")
    load_config(config_path)
    # Env-provided config_dir WINS over the file's default. Handshake + other
    # sidecar files must live in the same dir as the config.
    CFG["config_dir"] = config_dir
    load_handshake()

    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()

    threading.Thread(target=collect_traffic, daemon=True).start()
    print("[dashboard] traffic collector started", flush=True)

    load_plugins(STATE)

    asyncio.create_task(internet_monitor())
    await asyncio.sleep(2)

    if not STATE.internet_up and not have_handshake():
        STATE.set_proxy_mode("waiting")
        print("[startup] NO internet + NO handshake — WAITING for internet to capture handshake", flush=True)
        print("[startup] Connect the Pi to internet and restart, or provide a handshake.json", flush=True)

    server = await start_server()
    async with server:
        await server.serve_forever()


def cli():
    asyncio.run(main())


if __name__ == "__main__":
    cli()
