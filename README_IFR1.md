# browserg5 + IFR-1 — wiring the panel to the G5

How the browser G5 (`browserg5/`) connects to the **Octavi IFR-1** hardware and
**X-Plane**, reusing the parent **`ifrbridge`** project. Covers the data flow, the
"FMS2 = G5 mode" control mapping, knob write-back, and how to run it.

> **NOT FOR NAVIGATION** — this is a simulation/visualization.

## The big picture

One process — `browserg5/server.py` — owns everything: it is the single HID owner,
the X-Plane UDP client, and the web server for the phone/browser.

```
                         ┌─────────────────────────────────────────────---──┐
                         │            browserg5/server.py                   │
                         │  (single process — owns the HID + UDP + HTTP)    │
                         │                                                  │
   Octavi IFR-1 ─USB/HID─┼─► _ifr1_loop ──► Decoder ──► mode?               │
   (encoders, buttons,   │                              │                   │
    mode selector,       │                   ┌──────────┴──────────┐        │
    AP LEDs) ◄───LEDs────┤                   │                     │        │
                         │              mode == FMS2          other modes   │
                         │              "G5 mode"                  │        │
                         │                   │                     ▼        │
                         │                   ▼              ifrbridge.Bridge│
                         │             _push_g5_input        (.mcc routing  │
                         │             (unit, action)         + AP LEDs)    │
                         │                   │                     │        │
                         │                   │              CMND/DREF       │
                         │                   ▼                     ▼        │
                         │   ┌──────────── snapshot() ──────────────────┐   │
   X-Plane  ◄──UDP RREF──┼───┤  XPlaneClient (shared)                   │   │
   (49000)  ──UDP DREF◄──┼───┤   • RREF  subscribe/read datarefs        │   │
            ──UDP CMND◄──┼───┤   • DREF  write-back (knob → sim)        │   │
                         │   │   • CMND  aircraft commands (.mcc)       │   │
                         │   └──────────────────────────────────────────┘   │
                         │            │ SSE /events ▲ POST /write           │
                         └────────────┼─────────────┼─────────────────────-─┘
                                      │ data + _inputs│ {baro,hdgbug,…}
                                      ▼             │
                         ┌───────────────────────-──┴─────────--─┐
                         │   browser  g5.html  (phone)           │
                         │   • renders PFD + HSI                 │
                         │   • dispatchBridgeInputs(_inputs)     │
                         │       → g5Input(unit, action)         │
                         │   • knob setters → writeBack()        │
                         └────────────────────────────────────-──┘
```

## Relationship to the `ifrbridge` project

`server.py` does **not** reimplement the hardware or X-Plane layers — it imports
them from the parent package:

| Reused from `ifrbridge/` | Used for |
|---|---|
| `xplane.XPlaneClient` | UDP to X-Plane: `subscribe`/`value` (RREF), `set_dataref` (DREF write-back), `send_command` (CMND) |
| `ifr1.IFR1Device` | Open the HID, read input reports, drive the AP LEDs |
| `ifr1.Decoder` | Raw report → `EncoderEvent` / `ButtonEvent` / `ModeEvent`, tracks the current mode |
| `bridge.Bridge` | The existing aircraft bridge — `.mcc` routing + LED computation, used in **non-G5** modes |
| `mcc.parse_mcc` | Parse the MobiFlight `.mcc` (e.g. `XP_C172.mcc`) |

Because the IFR-1 is a **single HID device**, only one process may open it. So you
do **not** run `python -m ifrbridge run` *and* `server.py --ifr1` at the same time —
`server.py --ifr1 --mcc …` subsumes the aircraft bridge (it calls `Bridge.handle_event`
for non-G5 modes and refreshes the LEDs itself).

## "FMS2 = G5 mode" — deconfliction

The IFR-1's buttons/encoders normally drive the **aircraft** (COM/NAV/AP) via the
`.mcc`. To also drive the **G5 display** without stealing those functions, one mode
selector position is reserved as **G5 mode**:

```
 IFR-1 mode selector
 ┌───────┬───────┬───────┬───────┬───────┬───────┬───────┬───────┐
 │ COM1  │ COM2  │ NAV1  │ NAV2  │ FMS1  │ FMS2  │  AP   │ XPDR  │
 └───────┴───────┴───────┴───────┴───────┴───▲───┴───────┴───────┘
   └──────────── aircraft (.mcc) ────────────┘ │ └─── aircraft (.mcc) ───┘
                                          "G5 MODE"
                                       events → the G5
```

- **Mode = FMS2** → encoders/buttons are routed to the G5 units (no aircraft effect).
- **Any other mode** → unchanged; the `.mcc` flies the aircraft as before.

Change the reserved mode with `G5_MODE` in `server.py`.

## Control mapping in G5 mode (FMS2)

Two stacked G5 units: `TOP_G5` = PFD, `BOTTOM_G5` = HSI. The **inner ring + CRSR
drive whichever unit is *focused***; **SWAP** switches the focus. A thin cyan
border marks the focused unit on screen.

| IFR-1 control | G5 action | In `server.py` |
|---|---|---|
| **Inner ring** turn | turn the **focused** unit (cw/ccw) | `_route_g5` → `"FOCUS"` |
| **CRSR** (inner push, tap) | press the **focused** unit | `_g5_held["CRSR"]` |
| **CRSR** held ≥3 s | focused unit **hold** (sync) | `HOLD_SEC`, `_g5_tick()` |
| **SWAP** button | **switch** the focused unit | `G5_SWITCH_BTN` |
| Outer ring / MENU | (unused in G5 mode) | — |

The browser resolves the `"FOCUS"` sentinel to the selected unit (`focusIdx`), and
`"switch"` toggles it. Each event becomes a canonical `g5Input(unit, action)` call:
"press" opens/advances the focused unit's menu; "turn" adjusts its baro (PFD) /
heading bug (HSI), moves the menu cursor, or edits a value; **"hold"** syncs the
heading bug to current heading (or selected altitude while editing it). Buttons
defer: a quick press fires on release, a long hold fires `hold` and suppresses the
press — exactly as the mouse/keyboard test inputs do.

> Single-unit views (`?mode=pfd` / `?mode=hsi`) have nothing to switch — the inner
> ring + CRSR just drive the one unit, and no focus border is shown.

### Operating the G5 from the panel

It's all the **inner knob + SWAP**; the cyan border shows which unit you're on:

- **Select a unit:** tap **SWAP** to move the focus border between PFD and HSI.
- **Adjust the bug/setting** (menu closed): turn the **inner ring** — PFD baro or
  HSI heading bug, whichever is focused.
- **Open the menu:** tap **CRSR**. **Navigate:** turn the inner ring. **Select:**
  tap CRSR again. (Tap, don't hold; a quick press registers on release.)
- **Switch a unit's page (PFD ↔ HSI):** no dedicated button — open the menu, turn
  to the **`PFD`** (or **`HSI`**) item, and select it.
- **Sync heading bug → current heading:** focus the HSI, then hold **CRSR ≥3 s**.
  While editing Altitude, the hold syncs selected altitude instead.

All of this only applies in **FMS2** (G5 mode); other modes fly the aircraft.
(Desktop testing: **S** = switch focus, mouse/keyboard otherwise act on the
hovered unit.)

### Event path (inbound)

```
IFR-1 report ─► Decoder.feed() ─► EncoderEvent/ButtonEvent
   (mode==FMS2) ─► _route_g5() ─► _push_g5_input(unit, action)
   ─► snapshot() folds the queue into  data["_inputs"]
   ─► SSE /events ─► browser dispatchBridgeInputs(d) ─► g5Input(unit, action)
```

## Knob write-back (G5 → X-Plane)

When a G5 knob changes a value, the browser POSTs it so the **sim follows the knob**:

```
g5 knob (setBaro/​setHdgBug/​setAltSel/​setCrs)
   ─► writeBack(key,value)  (coalesced ~80 ms)
   ─► POST /write  {"baro":30.05, ...}
   ─► server xp.set_dataref(DATAREFS[key], value)   (UDP DREF)
   ─► X-Plane
```

Only these keys are writable (server-side allow-list `WRITABLE`), each mapped to its
dataref in `DATAREFS`:

| Key | Dataref |
|---|---|
| `baro` | `…/actuators/barometer_setting_in_hg_pilot` |
| `hdgbug` | `sim/cockpit/autopilot/heading_mag` |
| `altsel` | `sim/cockpit/autopilot/altitude` |
| `crs` | `sim/cockpit/radios/gps_course_degtm` |

In `--demo` mode there is no X-Plane client, so `/write` returns 200 and no-ops
(handy for testing the round trip without the sim).

## Running it

From the repo root (`ifr-1/`), with the project's virtualenv active:

```bash
# 1) Display only, live X-Plane (no hardware):
python browserg5/server.py --xplane-host 192.168.1.50

# 2) Full rig — IFR-1 drives the G5 (FMS2) AND the aircraft (other modes):
python browserg5/server.py --ifr1 --mcc XP_C172.mcc --xplane-host 192.168.1.50

# 3) IFR-1 for the G5 only (no aircraft bridge, no LEDs):
python browserg5/server.py --ifr1 --xplane-host 192.168.1.50

# 4) Bench test — hardware drives the G5 against synthetic data (no X-Plane):
python browserg5/server.py --demo --ifr1
```

Flags: `--ifr1` (open the IFR-1), `--mcc PATH` (aircraft bridge for non-G5 modes),
`--xplane-host/--xplane-port`, `--http-port` (default 8080), `--demo`, `--verbose`.

The server prints the URL(s); open one on the phone and tap to start. Make sure
X-Plane's **network UDP output** is enabled (Settings → Network), same as for the
standalone `ifrbridge`.

### Requirements

- X-Plane reachable over UDP (port 49000 by default).
- For `--ifr1`: the `hidapi` Python package (`hid`) and OS permission to open the
  device (VID `0x04D8` / PID `0xE6D6`). Without it, `--ifr1` prints a warning and the
  server still runs as a display.
- Do **not** run `python -m ifrbridge run` simultaneously — one HID owner only.

#### Linux HID permissions (`OSError: open failed`)

On Linux `/dev/hidraw*` is root-only by default, so opening the IFR-1 fails for a
normal user. Install the udev rule shipped with the **ifrbridge project** (the
device layer) — see the ifrbridge README "HID permissions (Linux)" section:

```bash
sudo cp ../linux/99-octavi-ifr1.rules /etc/udev/rules.d/   # from the ifr-1 project root
sudo udevadm control --reload-rules && sudo udevadm trigger
# then unplug and replug the IFR-1
```

Sanity checks: `lsusb | grep -i 04d8` (device enumerated?), `ls -l /dev/hidraw*`
(node + perms). `sudo python server.py --ifr1 …` working but the non-root run
failing confirms it's purely permissions. Prefer the **hidraw** hidapi backend
(`pip install hid` / distro `python3-hidapi`); the libusb backend can be blocked
by the kernel HID driver already claiming the device.

## Where to change things

| Want to change | Edit |
|---|---|
| Which mode = G5 mode | `G5_MODE` in `server.py` |
| Ring/button → unit/action mapping | `G5_ENC_UNIT` / `G5_BTN_UNIT` in `server.py` |
| Writable knobs / their datarefs | `WRITABLE` + `DATAREFS` in `server.py` |
| Coalesce interval for write-back | `writeBack()` timeout in `g5.html` |
| Aircraft (non-G5) behavior + LEDs | the `.mcc` file (via `ifrbridge`) |

## Known limitations

- `_inputs` is drained per `snapshot()`, so it assumes a **single** browser client
  (the normal one-phone deployment). Multiple simultaneous tabs would split events.
- Live datarefs for some HSI annunciations (`cdiscale`/`msg`/`obs`/`gpss`) and the
  G5 backup battery are best-effort/absent in X-Plane — see `G5_BUILD.md`.
