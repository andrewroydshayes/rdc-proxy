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

## ⚠️ Use at your own risk

This is an **unofficial, community project** — not made, endorsed, or supported
by Kohler or Rehlko. It runs on your hardware, on your network, near your
expensive generator. **You are responsible for your install.** The proxy is
designed to be passive and read-only on the wire, and has done no harm in
testing on the developer's generator, but we can't guarantee nothing odd will
happen on yours. If you're not comfortable tinkering near a generator, don't.

**Nothing leaves your Pi.** No telemetry, no analytics, no phone-home. The
only outbound network traffic is (a) the generator's existing connection to
Kohler's cloud — which is unchanged — and (b) package downloads from apt and
PyPI the first time you install. All decoding, storage, and the dashboard stay
on the Pi.

**The source is open; read it before running it.** MIT-licensed, all three
packages on GitHub ([rdc-proxy](https://github.com/andrewroydshayes/rdc-proxy),
[rdc-proxy-unifi](https://github.com/andrewroydshayes/rdc-proxy-unifi),
[rdc-correlate](https://github.com/andrewroydshayes/rdc-correlate)). The
installer is a ~200-line bash script. If you'd rather not `curl | sudo bash`,
clone the repo, read it, run it yourself.

**No warranty.** Per the MIT license, the software is provided **"AS IS,"
without warranty of any kind**, express or implied — including merchantability,
fitness for a particular purpose, and non-infringement. The authors are not
liable for any claim, damages, or other liability arising from use of this
software. If Kohler changes their wire protocol, this project could break, and
there is no guarantee it will be updated.

**Observing traffic on your own network is your right.** rdc-proxy is a
passive bridge on your LAN between two pieces of hardware you own. Kohler
has no legal standing over packets on your wire. US copyright law
([DMCA §1201(f)](https://www.law.cornell.edu/uscode/text/17/1201)) explicitly
permits reverse engineering for interoperability with equipment you own.
rdc-proxy does **not** use Kohler's cloud API, does **not** access the
service in an automated way, and does **not** share account access — it just
watches packets fly by on your network. If you plan to go further and
automate calls to Kohler's cloud API on top of this, that's a separate
question — see [rdc-correlate](https://github.com/andrewroydshayes/rdc-correlate)
for that discussion.

The full "please read before starting" block is also at the top of
[docs/PI-SETUP.md](docs/PI-SETUP.md#important--please-read-before-starting).

## Quick start (fresh Raspberry Pi)

See **[docs/PI-SETUP.md](docs/PI-SETUP.md)** for the full flash-to-running
walkthrough. The short version once you're SSH'd into the Pi:

```bash
curl -fsSL https://raw.githubusercontent.com/andrewroydshayes/rdc-proxy/main/install/install.sh \
  | sudo RDC_IP=10.0.0.50 bash
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

Runs in production on a Raspberry Pi 4 monitoring a Kohler 20 kW RDC2
generator. See the
[rdc-protocol-research](https://github.com/andrewroydshayes/rdc-protocol-research)
repo for handshake reverse-engineering notes and the parameter-mapping
registry.

## License

[MIT](LICENSE). Provided "AS IS," without warranty. See the full disclaimer
in the [Use at your own risk](#️-use-at-your-own-risk) section above.
