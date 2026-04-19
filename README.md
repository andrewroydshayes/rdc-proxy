# rdc-proxy

Transparent TCP proxy for the **Kohler RDC2 (Blue Board)** generator
controller. Decodes the plaintext TLV wire protocol in real time, survives
Kohler/Rehlko cloud outages by replaying a captured handshake, and serves a
local-only dashboard.

## What it does

- Sits as a Linux L2 bridge between the RDC and your LAN — zero config on the
  RDC side, no cloud account required after initial pairing.
- In **PROXY mode** it relays RDC ↔ Kohler cloud transparently while tapping
  the stream to decode telemetry.
- In **LOCAL mode** (no internet) it replays a captured cloud handshake so the
  RDC keeps streaming to the Pi instead of faulting.
- Serves a dashboard at `http://<pi>/` with engine state, voltages, frequency,
  runtime hours, utility loss events, and interface counters.

## Quick start (fresh Raspberry Pi)

See **[docs/PI-SETUP.md](docs/PI-SETUP.md)** for the full flash-to-running
walkthrough. The short version once you're SSH'd into the Pi:

```bash
curl -fsSL https://raw.githubusercontent.com/andrewroydshayes/rdc-proxy/main/install/install.sh \
  | sudo RDC_IP=192.168.4.50 bash
```

## Architecture

See **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for module layout, the
plugin contract, and the TPROXY-on-bridge gotchas.

## Plugins

External integrations live in separate packages that register under the
`rdc_proxy.plugins` entry-point group. Install a plugin with pip after
rdc-proxy is up — it's picked up automatically on next service restart.

## Development

```bash
# Run tests
pip install -e ".[dev]"
pytest

# Run locally (without systemd)
python -m rdc_proxy
```

## Status

Runs in production on a Raspberry Pi 4 monitoring a Kohler 20 kW generator
(Model20KW, RDC2, firmware 3.4.5). See project memory for the handshake
reverse-engineering notes and mode-transition validation tests.

## License

MIT.
