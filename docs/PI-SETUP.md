# Setting Up Your Raspberry Pi — Start-to-Finish Guide

This guide takes you from **zero** to a **working rdc-proxy** monitoring your
Kohler generator. You don't need to know anything about Linux, networking, or
the command line — just follow the steps exactly as written.

**Estimated time:** 45–60 minutes for first-time setup.

---

## Important — please read before starting

> **Use at your own risk.** rdc-proxy is an unofficial, community project —
> it is not made by, endorsed by, or supported by Kohler or Rehlko. It runs
> on your hardware, on your network, near your generator. **You are
> responsible for your install.** If something goes wrong, you're the one
> fixing it. Generators are expensive, critical equipment; if you're not
> comfortable tinkering near one, don't.
>
> **It should do no harm — but "should" isn't "guaranteed."** The proxy is
> a passive, read-only observer on the wire: it forwards traffic between
> your generator and Kohler's cloud without modifying it, and in local mode
> it replays a handshake your generator has already seen from the real
> cloud. None of this should upset the generator. But we can't prove a
> negative — you accept that risk when you install it.
>
> **Everything runs locally.** This software does **not** phone home. No
> telemetry, no analytics, no data leaves your Pi for any server owned by
> the developer or a third party. The only outbound traffic is (a) your
> generator's existing connection to Kohler's cloud, which is unchanged,
> and (b) package downloads from apt and PyPI the first time you run the
> installer. Everything else — decoding, storage, dashboard — stays on the
> Pi.
>
> **The source is open; read it before running it.** All three packages
> ([rdc-proxy](https://github.com/andrewroydshayes/rdc-proxy),
> [rdc-proxy-unifi](https://github.com/andrewroydshayes/rdc-proxy-unifi),
> [rdc-correlate](https://github.com/andrewroydshayes/rdc-correlate)) are
> MIT-licensed and public on GitHub. The installer is a ~200-line bash
> script. If you'd rather not `curl | sudo bash`, clone the repo, read the
> installer, and run it locally. Good security hygiene regardless of source.
>
> **No warranty.** Per the MIT license, the software is provided **"AS IS,"
> WITHOUT WARRANTY OF ANY KIND**, express or implied — including but not
> limited to the warranties of merchantability, fitness for a particular
> purpose, and non-infringement. The authors are not liable for any claim,
> damages, or other liability arising from the use of this software. If
> Kohler changes their wire protocol tomorrow, this project could break,
> and there's no guarantee it will be updated.
>
> **Your relationship with Kohler/Rehlko is yours.** Observing the protocol
> between your own generator and Kohler's cloud sits in a gray area of
> Kohler's terms of service. The developer's position is that passive,
> read-only observation of traffic on your own network, on equipment you
> own, for personal use, is reasonable. If that matters to you, read
> Kohler's TOS yourself and make your own call.

---

## Table of contents

1. [What you'll need (parts list)](#1-what-youll-need-parts-list)
2. [What you're building (the big picture)](#2-what-youre-building-the-big-picture)
3. [Install Raspberry Pi Imager on your computer](#3-install-raspberry-pi-imager-on-your-computer)
4. [Flash the SD card](#4-flash-the-sd-card)
5. [First boot of the Pi](#5-first-boot-of-the-pi)
6. [Find the Pi's IP address](#6-find-the-pis-ip-address)
7. [Connect to the Pi from your computer](#7-connect-to-the-pi-from-your-computer)
8. [Run the rdc-proxy installer](#8-run-the-rdc-proxy-installer)
9. [Wire the Pi in-line to the generator](#9-wire-the-pi-in-line-to-the-generator)
10. [Verify it's working](#10-verify-its-working)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. What you'll need (parts list)

### Hardware

| Item | Notes |
|---|---|
| **Raspberry Pi 4 Model B** (4 GB RAM or more) | The "brain." Comes in a cardboard box, green circuit board, about the size of a deck of cards. |
| **microSD card**, 32 GB, Class 10 / A2 rated | The Pi's storage. Labels say things like "SanDisk Extreme 32GB A2." Don't buy the cheapest no-name one — they fail. |
| **Power — pick ONE of the two options below** | See the "How will you power the Pi?" section just under this table. |
| ↳ Option A: **Raspberry Pi 4 official power supply** (USB-C, 5.1 V / 3 A) | Use the official one. Random USB-C phone chargers may cause random reboots. Picks up power from a wall outlet. |
| ↳ Option B: **Official Raspberry Pi PoE+ HAT** (for Pi 4) | Powers the Pi over the same Ethernet cable that carries data — no wall brick needed near the Pi. Only works if your switch supports **PoE** or **PoE+** (look for "802.3af" or "802.3at" on the spec sheet). This is the cleaner install if you have it available. |
| **USB-to-Ethernet adapter** (gigabit, with a chipset like RTL8153 or AX88179) | Adds a **second** Ethernet port. The Pi only has one built in; you need two. Any generic "USB 3.0 Gigabit Ethernet Adapter" on Amazon works. |
| **3 Ethernet cables** (Cat5e or Cat6) | At least 3 feet long each. You need three: (1) computer-to-LAN for setup, (2) Pi-to-LAN once installed, (3) Pi-to-generator. If you're using the PoE HAT, the LAN cable (cable #2) MUST be plugged into a PoE-capable switch port. |
| **A case for the Pi** — **get one, don't skip this** | A bare Pi sitting out exposes a live circuit board to dust, cat hair, static, spilled drinks, and accidental bumps. You're installing this near a generator — it needs physical protection. If you chose the **PoE HAT above**, make sure the case is **PoE-HAT-compatible** (the HAT stacks on top of the Pi, so a standard-height case won't close; look for cases labeled "with PoE HAT clearance" or "tall GPIO case"). If you chose the **official power supply**, any standard Pi 4 case works — the official Pi 4 case ($10) or an Argon Neo / Flirc aluminum case are all fine. |

### How will you power the Pi?

Two ways, pick whichever fits your install:

- **Option A — USB-C wall brick (simplest).** Works anywhere there's an outlet. You'll have a small white brick plugged into the wall near the Pi and a USB-C cable running to the Pi.
- **Option B — PoE HAT (cleanest).** The Pi pulls power from the Ethernet cable itself. No wall brick, no extra cable, nothing plugged in at the Pi other than two Ethernet cables. Requires a **PoE or PoE+ switch port** on the switch end. Many UniFi / Netgear / TP-Link managed switches have PoE ports labeled. If you already have PoE available at the switch, this is the recommended option — it's what the developer of rdc-proxy uses.

**Which option you pick affects nothing about the software install** — the Pi boots, networks, and runs rdc-proxy identically either way. The difference is purely cabling at the Pi end.

### On your computer

You'll also need:

- **A computer** (Mac or Windows) with an internet connection.
- **A microSD card reader.** Some laptops have a slot built in; if not, buy a cheap USB microSD reader (~$10).
- **About 2 GB of free disk space** to download the Raspberry Pi OS image.

### Information to have handy

- Your home **Wi-Fi network name (SSID) and password** — as a backup so you can always reach the Pi wirelessly if the wired connection breaks.
- A **strong password** you'll invent for the Pi's user account. Write it down somewhere safe.

---

## 2. What you're building (the big picture)

Today, your Kohler generator's "RDC" (the box with a network port on it) plugs
straight into your home network — probably into a switch or directly into your
router:

```
  [Generator RDC] ─── Ethernet cable ─── [Switch or Router] ─── Internet
```

You're going to insert the Raspberry Pi **in between** the RDC and the switch:

```
  [Generator RDC] ─── Pi (eth1) ─── (eth0) Pi ─── [Switch or Router] ─── Internet
```

The Pi acts like an **invisible listening device**. The generator and the
internet still talk normally — the Pi just quietly records the conversation,
decodes it, and shows you a live dashboard.

It's a transparent **bridge**. Nothing about your network topology changes,
and nothing else on your network notices. If the Pi ever dies or is unplugged,
you can just plug the cable straight back through and everything returns to
normal.

---

## 3. Install Raspberry Pi Imager on your computer

Raspberry Pi Imager is the official tool that writes the Pi's operating system
to an SD card. Free.

### On Mac

1. Open **Safari** (or any browser).
2. Go to: **https://www.raspberrypi.com/software/**
3. Click the **"Download for macOS"** button. A file named something like `imager_X.Y.Z.dmg` downloads.
4. Open your **Downloads** folder and double-click the `.dmg` file. A window opens showing a **Raspberry Pi Imager** icon and an **Applications** folder.
5. **Drag** the Raspberry Pi Imager icon **onto** the Applications folder icon. Wait a few seconds for the copy to finish.
6. Open **Launchpad** (F4 on the keyboard, or click the grid-of-apps icon in the Dock) and click **Raspberry Pi Imager**.
7. If macOS asks "Are you sure you want to open it?" because it was downloaded from the internet, click **Open**.

Leave the Imager window open — you'll use it in the next step.

### On Windows

1. Open **Microsoft Edge** (or any browser).
2. Go to: **https://www.raspberrypi.com/software/**
3. Click the **"Download for Windows"** button. A file named something like `imager_X.Y.Z.exe` downloads.
4. Open your **Downloads** folder and double-click the `.exe` file.
5. If Windows shows a blue "Windows protected your PC" screen, click **More info** → **Run anyway**. (This happens because the file is new; it's safe — it's signed by the Raspberry Pi Foundation.)
6. Click through the installer: **Install** → **Finish**.
7. Click **Start** (the Windows logo), type **Raspberry Pi Imager**, press Enter.

Leave the Imager window open — you'll use it in the next step.

---

## 4. Flash the SD card

The Imager window looks like this (both Mac and Windows):

```
┌─────────────────────────────────────────┐
│  Raspberry Pi Imager                     │
│                                          │
│   [ CHOOSE DEVICE ]                      │
│   [ CHOOSE OS     ]                      │
│   [ CHOOSE STORAGE ]                     │
│                          [ NEXT ]        │
└─────────────────────────────────────────┘
```

### 4a. Insert the SD card

Plug your microSD card into your computer's card reader. The computer may pop
up a notification — ignore it, don't "format" it if offered.

### 4b. Click the buttons in order

1. **CHOOSE DEVICE** → pick **Raspberry Pi 4**.
2. **CHOOSE OS** → pick **Raspberry Pi OS (other)** → then **Raspberry Pi OS Lite (64-bit)**. Make sure it says "Lite" and "64-bit." You do **not** want the desktop version — we don't need a monitor hooked up.
3. **CHOOSE STORAGE** → pick your SD card. It'll be listed by size and brand (e.g. "Generic SD Card Reader — 32 GB"). **Double-check** you're not picking your main hard drive by accident.
4. Click **NEXT**.

### 4c. OS customization — this is the important part

A dialog appears: *"Would you like to apply OS customisation settings?"*

Click **EDIT SETTINGS**.

You now see a form with tabs: **General**, **Services**, **Options**.

**On the "General" tab**, fill in:

| Field | What to type |
|---|---|
| **Set hostname** | Check the box. Type `rdc-pi` (or any name you want — this is how the Pi will identify itself on your network). |
| **Set username and password** | Check the box. Username: `pi` (recommended). Password: make up a strong password and **write it down somewhere** — you'll need it in a few steps. |
| **Configure wireless LAN** | Check the box. Fill in your **Wi-Fi network name (SSID)** and **Wi-Fi password**. This is a backup path — if the wired connection ever breaks, you can still reach the Pi over Wi-Fi. |
| **Wireless LAN country** | Pick your country (e.g. **US** for the United States). |
| **Set locale settings** | Check the box. Pick your **Time zone** (e.g. America/Los_Angeles) and **Keyboard layout** (e.g. us). |

**Switch to the "Services" tab**, and check:

- ☑ **Enable SSH** → pick **Use password authentication**.

**Switch to the "Options" tab**, and optionally:

- ☑ **Eject media when finished** — this doesn't physically pop the card out; it just unmounts it so it's safe to pull out without corrupting the card. Leave it checked.

Click **SAVE**.

Back on the "Apply OS customisation?" dialog, click **YES**.

One more dialog: *"All existing data on the SD card will be erased. Are you sure?"* → Click **YES**.

### 4d. Wait

The Imager writes the OS to the card, then verifies it. Takes **5–15 minutes**
depending on your card speed. Progress bar shows percent done. Don't interrupt
it; don't unplug the card.

When it finishes, a dialog says *"Write Successful"*. Click **CONTINUE**.

Now physically remove the SD card: pull it out of the reader (or press-and-release
if your reader is the spring-loaded kind). If your computer shows a warning
like "Disk not ejected properly," ignore it — the Imager already safely
unmounted the card for you.

---

## 5. First boot of the Pi

1. Unbox the Raspberry Pi.
2. If you bought a **PoE HAT**, install it now: line up the 40-pin connector on the bottom of the HAT with the 40-pin GPIO header on top of the Pi, press straight down until seated, then secure with the included standoffs/screws. See the HAT's included instruction sheet for the exact fasteners.
3. Put the Pi into its **case** now — easier than wrestling with it after cables are attached. Follow the case's instructions.
4. Flip the Pi over (inside its case). On the bottom/side is a slot for the microSD card. Slide your SD card in until it clicks (Pi 4) or just pushes all the way in (some models).
5. Plug one end of an Ethernet cable into the Pi's **built-in** Ethernet port (the one on the edge of the board, **not** a USB adapter yet). Plug the other end into any open port on your home router or switch.
   - **PoE HAT users:** the switch port you plug into **must be PoE-enabled**. This cable is doing double duty — data AND power.
6. Apply power:
   - **Power Option A (USB-C brick):** plug the power supply into the Pi's USB-C port, then into the wall.
   - **Power Option B (PoE HAT):** no separate power step — the Pi starts booting as soon as you plug in the PoE-enabled Ethernet cable from step 5.

   A **red LED** on the Pi should light up immediately.
7. Wait. The first boot takes **60–90 seconds**. During this time a **green LED** will flicker as the Pi initializes. When it goes quiet and the green LED stops flickering for 10+ seconds, the Pi is ready.

Leave the Pi running. Do NOT plug in the USB-to-Ethernet adapter yet — that
comes later.

---

## 6. Find the Pi's IP address

You need the Pi's IP address to connect to it. **Try these in order** until one
works:

### Method A: Use the hostname (often works instantly)

On most home networks, the hostname you chose (`rdc-pi`) resolves automatically.
Test it:

- **Mac:** Open **Terminal** (press Cmd+Space, type "Terminal," press Enter). Type:
  ```
  ping -c 1 rdc-pi.local
  ```
  If it replies with `64 bytes from ...: icmp_seq=0`, the Pi is reachable. **Note the IP address shown** (e.g. `192.168.1.42`). You can skip the other methods.

- **Windows:** Open **PowerShell** (press the Windows key, type "PowerShell," press Enter). Type:
  ```
  ping rdc-pi
  ```
  If it replies with `Reply from 192.168.X.X:`, note that IP. Skip the other methods.

If you see `cannot resolve` or `Request timed out`, try the next method.

### Method B: Check your router's device list

1. Open a browser and go to your router's admin page. Common addresses: **http://192.168.1.1**, **http://192.168.0.1**, or **http://router.asus.com**, depending on your router.
2. Log in (the admin password is often on a sticker on the router, or was set up by whoever installed it).
3. Find the "Connected devices" or "DHCP clients" page.
4. Look for a device named **rdc-pi** or a MAC address starting with **b8:27:eb**, **dc:a6:32**, or **d8:3a:dd** (all Raspberry Pi MAC prefixes).
5. Note its IP address.

### Method C: Scan your network

- **Mac:** In Terminal, run:
  ```
  arp -a | grep -iE 'b8:27:eb|dc:a6:32|d8:3a:dd'
  ```
  The IP appears on the left like `(192.168.1.42)`.

- **Windows:** In PowerShell, run:
  ```
  arp -a | Select-String -Pattern "b8-27-eb|dc-a6-32|d8-3a-dd"
  ```
  The IP appears on the left.

**Write down the Pi's IP** — you'll use it several times. Example: `192.168.1.42`.

---

## 7. Connect to the Pi from your computer

You'll use **SSH** ("Secure Shell"), a tool built into both Mac and Windows
that lets you type commands on the Pi from your own computer.

### On Mac

1. Open **Terminal** if it isn't already.
2. Type this, replacing `<PI-IP>` with your Pi's IP address:
   ```
   ssh pi@<PI-IP>
   ```
   Example: `ssh pi@192.168.1.42`

3. The **first** time only, you'll see:
   ```
   The authenticity of host '192.168.1.42' can't be established.
   ED25519 key fingerprint is SHA256:...
   Are you sure you want to continue connecting (yes/no)?
   ```
   Type **yes** and press Enter.

4. You'll be prompted for the password:
   ```
   pi@192.168.1.42's password:
   ```
   Type the password you set in the Imager. **You will not see dots or asterisks as you type — that's normal.** Press Enter.

5. If successful, the prompt changes to:
   ```
   pi@rdc-pi:~ $
   ```
   You're now "on" the Pi. Everything you type from here goes to the Pi, not your Mac.

### On Windows

1. Open **PowerShell** if it isn't already.
2. Type this, replacing `<PI-IP>` with your Pi's IP:
   ```
   ssh pi@<PI-IP>
   ```
   Example: `ssh pi@192.168.1.42`

3. Same first-time fingerprint prompt — type **yes**, press Enter.

4. Enter your password (no visible feedback while typing; normal). Press Enter.

5. Prompt changes to:
   ```
   pi@rdc-pi:~ $
   ```
   You're on the Pi.

### If you ever get disconnected

Just run the same `ssh pi@<PI-IP>` command again. It won't re-ask for the
fingerprint.

To exit the Pi and return to your computer's shell, type `exit` and press
Enter.

---

## 8. Run the rdc-proxy installer

You're SSH'd in and see the `pi@rdc-pi:~ $` prompt. Now install rdc-proxy.

**Copy this entire command** — yes, both lines, select the whole thing — and
paste it into the terminal:

```
curl -fsSL https://raw.githubusercontent.com/andrewroydshayes/rdc-proxy/main/install/install.sh \
  | sudo RDC_IP=10.0.0.50 bash
```

**But first, change `10.0.0.50`** to your RDC's actual IP address. If you
don't know it, check your router's device list (same place you found the Pi's
IP) for a device named "Kohler," "RDC," or similar. A common pattern is that
the RDC is on the same subnet as your Pi (e.g. if your Pi is `192.168.1.42`
the RDC is likely `192.168.1.50` or similar).

Paste the command and press Enter. You'll be asked for the Pi's password one
more time (for `sudo`). Type it, press Enter.

The installer now runs. **Expected output** — you'll see colored lines like:

```
── 1/8  prerequisites ──
✓ running as root
✓ OS: Linux
✓ arch: aarch64
✓ kernel 6.12.X (>= 5.15)
✓ apt-get present
✓ disk free: 28 GB
✓ internet reachable (github.com)

── 2/8  apt packages ──
✓ installed: git python3 pip venv flask iptables ebtables tcpdump
✓ ebtables-legacy present (required for broute redirect)

── 3/8  clone repo ──
✓ cloned ... → /opt/rdc-proxy
✓ checked out latest tag: v0.2.1

── 4/8  python venv ──
✓ venv + package installed at /opt/rdc-proxy/venv

── 5/8  config ──
✓ wrote /etc/rdc-proxy/rdc-proxy.env

── 6/8  bridge (br0 over eth0+eth1) ──
✓ br0 configured via setup-bridge.sh

── 7/8  systemd unit ──
✓ rdc-proxy.service enabled + started

── 8/8  self-check ──
✓ rdc-proxy is active
✓ proxy listening on :5253
✓ TPROXY rule present
✓ ebtables broute rule present
✓ dashboard /api/status OK (port 80)

rdc-proxy installed successfully.
Dashboard: http://192.168.X.X/
Logs:      journalctl -u rdc-proxy -f
```

**Takes 3–5 minutes total.** If you see any red ✗ lines, look at the
[Troubleshooting](#11-troubleshooting) section at the bottom.

When the installer finishes, **your SSH session may disconnect** — that's
expected. The installer configured a network bridge, which briefly changes how
the Pi talks to the network. **Reconnect** with `ssh pi@<PI-IP>` and run:

```
systemctl is-active rdc-proxy
```

You should see `active`. Done with the installer.

---

## 9. Wire the Pi in-line to the generator

The Pi is now running but isn't intercepting anything yet — it's still just
plugged into your LAN. Time to physically wire it between the RDC and the
switch.

### 9a. Find the cable

Find the Ethernet cable that's currently connected to your generator's RDC
module. The other end plugs into your **switch** or **router**. **Don't
unplug anything yet** — just trace the cable.

### 9b. Plug in the USB-to-Ethernet adapter

With the Pi still running, plug the USB-to-Ethernet adapter into any of the
Pi's blue USB-3 ports (the ones with blue plastic inside). The adapter becomes
the Pi's **eth1**. Wait 5 seconds.

### 9c. Identify the Pi's ports

- **eth0** = the **built-in** RJ-45 port, edge of the board, next to the USB ports.
- **eth1** = the **USB-to-Ethernet adapter** you just plugged in.

You can test which is which by unplugging one cable at a time later. For now:
**eth0 goes to the switch/router. eth1 goes to the RDC.**

### 9d. Rewire

In this order:

1. **Unplug** the Ethernet cable from the **RDC**. The other end is still in your switch; leave it there. This cable becomes your new **"Pi eth1 ↔ RDC"** cable.
2. **Unplug** the Ethernet cable that connects the Pi to the switch (the one you put in during Section 5). You'll reuse it.
3. **Plug** the cable from step 1 into the Pi's **eth1 (USB adapter)** on one end, and into the **RDC** on the other.
4. **Plug** the cable from step 2 into the Pi's **eth0 (built-in port)** on one end, and into the **switch** where the RDC used to be on the other.

Final wiring:

```
[RDC] ──cable A── [Pi eth1 USB adapter]   [Pi eth0 built-in] ──cable B── [Switch]
                                   └─── same Pi ───┘
```

The Pi is now **in-line**. Give it 30 seconds for the network to re-learn
where everything is.

---

## 10. Verify it's working

### 10a. Check the dashboard

On any device on your home network (your phone, your computer), open a web
browser and go to:

- **http://rdc-pi/** (if your network supports `.local` / mDNS — most do)
- Or: **http://<PI-IP>/** (using the IP you noted earlier)

You should see the **Kohler Generator** dashboard, black background with green
accents. The top banner will likely say **"Standby"** (green) if the generator
isn't running. Below that is a status strip:

```
Proxy: local  ·  Cloud: ...  ·  RDC: connected  ·  Internet: up
● LOCAL — cloud X.X.X.X online; switching to PROXY in 290s
```

- **"RDC: connected"** in green means the generator is talking to the Pi — ✅.
- The **countdown** (`switching to PROXY in 290s`) is normal after any restart. After ~5 minutes it flips to:
  ```
  ● PROXY — relaying RDC ↔ cloud X.X.X.X
  ```

### 10b. Check telemetry is flowing

Scroll down on the dashboard. You should see live gauges for:

- **Battery Voltage** (~12–14 V when healthy)
- **Utility Voltage** (~240 V)
- **Controller Temp** (around ambient)

Numbers update every 1–2 seconds. If they're populating, **it works**.

### 10c. Check the real Kohler cloud still sees the generator

Open the Kohler/Rehlko app on your phone. Your generator should still show
"online" and report the same values as the Pi's dashboard. This confirms the
Pi is truly transparent — Kohler doesn't know it's there.

---

## 11. Troubleshooting

### The Pi isn't on my network

- **Red LED on Pi lit but no green flicker?** Power issue — try a different USB-C cable or the official Pi 4 power supply.
- **Green LED flickered then stopped but Pi is unreachable?** The SD card may have been written incorrectly. Re-do [Section 4](#4-flash-the-sd-card).
- **Green LED never turned on at all?** SD card not seated properly, or card is bad. Reseat, retry. If still broken, try a different SD card.
- **PoE HAT users — no LEDs at all?** The switch port may not actually be supplying PoE. Check your switch's management interface to confirm PoE is enabled on that port, and that the port is delivering at least 15 W (802.3af minimum; 802.3at preferred). Try a different PoE port. Also verify the HAT is firmly seated on the GPIO pins — a loose HAT looks like no power.

### I can ping the Pi but SSH says "Connection refused"

The Pi is still booting. Wait another 30 seconds and try again. If it
persists for more than 2 minutes, the SSH wasn't enabled during flashing —
re-do [Section 4](#4-flash-the-sd-card) with the "Enable SSH" checkbox ticked.

### SSH says "Permission denied"

Wrong password. Careful: `Caps Lock` matters. If you're truly stuck:

- Power off the Pi (unplug), pull out the SD card, plug it into your
  computer, re-run the Imager, and flash a fresh copy with a password you
  remember this time.

### Installer fails at `git clone`

The installer needs internet. Check that the Pi has internet: `ping 8.8.8.8`.
If that fails, the Pi's wired connection is broken — check cables.

### Installer fails at "kernel too old"

Your Pi is running a very old version of Raspberry Pi OS. Before retrying the
installer, run:

```
sudo apt-get update && sudo apt-get full-upgrade -y && sudo reboot
```

Wait 2 minutes after the reboot finishes, SSH back in, and retry the installer.

### Dashboard shows "RDC: disconnected" after wiring in

- **Confirm the RDC has power.** Its own small LCD should be lit.
- **Confirm the cables are in the right ports on the Pi.** eth0 = built-in, eth1 = USB adapter. If you mixed them up, just swap the two Ethernet cables on the Pi side.
- **Wait 60 seconds.** The RDC sometimes takes a minute after a network change to re-establish its connection.
- **Check the Pi's bridge** by SSH'ing in and running:
  ```
  ip addr show br0
  ```
  You should see `state UP` and an IP like `inet 192.168.X.X/24`. If `state DOWN`, reboot the Pi: `sudo reboot`.

### The dashboard is stuck on "● WAITING"

WAITING means the Pi has never successfully connected to Kohler's cloud and
has no captured handshake to serve locally. The normal fix is to wait — the
Pi promotes to PROXY automatically within a few minutes once:

1. The RDC reconnects (usually 30–60 seconds after the Pi is wired in).
2. Kohler's cloud is reachable (the Pi probes `devices.kohler.com` every 30 seconds).

If it's been waiting for more than 10 minutes, check:

- The Pi has internet: SSH in and `ping devices.kohler.com`. Should succeed.
- The RDC is wired to eth1 (not eth0).

### I want to undo all this and go back to how things were

1. SSH into the Pi: `ssh pi@<PI-IP>`.
2. Stop the service: `sudo systemctl disable --now rdc-proxy`.
3. Power off the Pi: `sudo poweroff`.
4. Wait 10 seconds, unplug power from the Pi.
5. Unplug both Ethernet cables from the Pi.
6. Reconnect the cable you originally had: RDC → switch (directly, no Pi in between).

Your network is back to original. You can throw the Pi in a drawer and come
back to it later; nothing is permanently changed on the RDC or your network.

### Something else broke

See the logs — SSH in and run:

```
sudo journalctl -u rdc-proxy -n 100 --no-pager
```

This prints the last 100 log lines from the service. If you file a bug on
GitHub, paste those lines so we can see what happened.

---

## You're done

If you got through Section 10 with live telemetry showing in your browser,
**congratulations** — you now have a transparent proxy between your Kohler
generator and the cloud, serving a local dashboard that works even if the
internet goes down.

**What you might want to do next:**

- **Bookmark the dashboard** on your phone's home screen: `http://rdc-pi/` (iOS Safari: Share → "Add to Home Screen").
- **Install the optional UniFi switch plugin** if you have a UniFi or EdgeSwitch and want port-counter stats on the dashboard. See the [rdc-proxy-unifi](https://github.com/andrewroydshayes/rdc-proxy-unifi) repo for instructions.
- **Set up the correlation tool** (`rdc-correlate`) if you want to help reverse-engineer new parameter mappings. See the [rdc-correlate](https://github.com/andrewroydshayes/rdc-correlate) repo.

Keep the Pi plugged in 24/7. It uses about as much power as a night-light
(3 watts) and logs every packet for you in the background.
