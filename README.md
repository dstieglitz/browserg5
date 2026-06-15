# browserg5

![browserg5 in action](browserg5_in_action.jpg)

A browser-based **simulation of the Garmin G5 Electronic Flight Instrument**,
fed live from X-Plane. It renders the G5's PFD and HSI pages on an HTML canvas
and aims to match the real unit's layout, scales, colors, and knob/menu behavior.

Data streams to the browser over Server-Sent Events — no app store, and no
third-party Python packages for the display path (`hidapi` only for `--ifr1`).

> **Depends on [`ifrbridge`](https://github.com/dstieglitz/ifr-1).** `server.py` imports the sibling `ifrbridge`
> package — `XPlaneClient` always, plus the IFR-1 HID layer
> (`IFR1Device`/`Decoder`/`Bridge`) for `--ifr1`. Satisfy it either way: run
> browserg5 **nested inside the `ifr-1` checkout** (server.py adds the repo root to
> `sys.path`), or **`pip install -e ..`** into this venv (ifrbridge ships a
> `pyproject.toml`). See [`README_IFR1.md`](README_IFR1.md).

## What it renders

- **PFD page** — attitude (pitch ladder, roll scale w/ white pointer, slip/skid,
  yellow aircraft symbol), airspeed tape, altimeter with selected-altitude box/bug
  + **altitude alerting flash** + baro box, VSI, turn-rate indicator, CDI
  (source-colored magenta/green), heading/track tape with current-heading lubber
  box + interlocking heading bug, a **boxed battery indicator**, and a transient
  **HDG** selected-heading pop-up that appears when the heading bug moves.
- **HSI page** — rotating compass card, course needle + CDI + TO/FROM
  (**source-colored** by nav source), current-track triangle, heading bug,
  bearing pointer, and **data-driven center annunciations** (nav source GPS/VLOC,
  GPS CDI scale ENR/TERM/APR…, MSG/OBS/GPSS), plus edge-flush corner boxes
  (DIST, GS, selected heading). The menu is **contextual** — Course/OBS appear
  with the active nav source.

Display data arrives at ~30 Hz and is smoothed at the display end so motion
stays fluid. The `--demo` server animates attitude, speeds, and the CDI/bearing
needles for testing.

> **NOT FOR NAVIGATION** — this is a simulation/visualization, not a certified
> instrument.

## Two independent units

The default `?mode=both` stacks **two independent G5 units** — `TOP_G5` (PFD) and
`BOTTOM_G5` (HSI) — each with its own knob, menu, and page. Each can switch its
own page (PFD↔HSI) via its menu. Single-unit views: `?mode=pfd` or `?mode=hsi`.

## Inputs (knob + button)

The real G5 has two operable inputs (power is ignored here): the knob **turn**
and the knob **press**. All input flows through one source-agnostic API:

```js
g5Input(unitId, action)   // unitId: "TOP_G5" | "BOTTOM_G5"; action: "cw" | "ccw" | "press" | "hold"
```

Knob behavior follows the spec: closed → turning adjusts baro (PFD) / heading bug
(HSI), pressing opens the menu; in the menu, turning moves the cursor and pressing
selects; selecting Heading/Altitude/Course opens a centered slide-up edit dialog.
A **press-and-hold (≥3 s)** on the HSI syncs the heading bug to the current
heading (or, while editing Altitude, syncs selected altitude). Menus and dialogs
**auto-close after 10 s** of inactivity.

Three sources feed `g5Input`:

- **IFR-1 hardware** (production) — the panel's mode selector reserves **FMS2 as
  "G5 mode"**: there, the **inner ring + CRSR drive the focused unit** and **SWAP
  switches focus** (a cyan border marks the selected unit); hold CRSR ≥3 s = hold.
  Other modes drive the aircraft via the `.mcc`. Run with `--ifr1`; see
  **[`README_IFR1.md`](README_IFR1.md)**.
- **Mouse** (desktop testing) — wheel = turn, click = press, hold ≥3 s = hold,
  routed to the unit under the cursor (which gets a faint outline). **Touch taps
  never operate the avionics** — on a phone a tap opens the display-fit menu.
- **Keyboard** (desktop testing) — Arrow ↑/↓ = turn, Enter = press / hold,
  **S = switch focus**.

Knob-set values (baro / heading bug / selected altitude / course) are **written
back to X-Plane** so the sim follows the simulated knob (POST `/write` →
`set_dataref`; coalesced, no-op in `--demo`).

## Phone display-fit menu

On a phone, a **tap** opens a config overlay for sizing the image to clear the
device's bezels/notches: a box with up/down arrows on each edge that grow/shrink
the drawable viewport, OK in the center. Settings persist in `localStorage`. The
splash screen explains it. (Tap is touch-only, so desktop testing is unaffected.)

## Debug overlay

Press **`G`** (desktop) to toggle a live readout of the raw incoming stream —
`live`/age, per-unit menu state, decoded `navsrc`/scale/to-from, active knob
overrides, and every data field — for eyeballing G5-vs-sim agreement.

## Developer mode (visual tuning)

Press **`D`** in the browser (or open with `?dev`) to toggle a live style panel:

- Every tunable size/font lives in the `STYLE` object; the panel auto-generates a
  number input per key. Edits apply **immediately** and persist in `localStorage`.
- Opening the panel **freezes all animation** so the display can be inspected.
- **Hover a component** on the canvas and the panel highlights the row(s) that
  size it — so you can find the right knob without guessing.
- The panel is **draggable** (grab its header) and remembers its position.
- **Copy JSON** copies the current values (also logged to console) to bake back
  into `STYLE_DEFAULTS`; **Reset** clears overrides.

## Run it

On a machine running (or networked to) X-Plane:

```bash
# X-Plane on the same machine:
python server.py

# X-Plane on another machine:
python server.py --xplane-host 192.168.1.50

# No X-Plane — synthetic motion for development:
python server.py --demo

# Pick a port (default 8080):
python server.py --demo --http-port 9090

# Drive it from the IFR-1 panel (FMS2 = G5 mode) + aircraft via the .mcc:
python server.py --ifr1 --mcc ../XP_C172.mcc --xplane-host 192.168.1.50
```

It prints the URL(s) to open, e.g. `http://192.168.1.42:8080/`. Make sure
X-Plane's network UDP output is enabled (Settings → Network), same as for the
panel bridge. Open the URL in a browser and click once to start.

For the IFR-1 wiring (FMS2 = G5 mode, control mapping, knob write-back, single
HID owner), see **[`README_IFR1.md`](README_IFR1.md)**.

> The `server.py` reads `g5.html` fresh on every request, so a browser refresh
> picks up edits without restarting the server.

## Project layout

| Path | What |
|------|------|
| `g5.html` | The unit — all rendering + input handling (single self-contained file). |
| `server.py` | X-Plane UDP → SSE bridge; knob write-back; `--demo`; `--ifr1` HID. |
| `README_IFR1.md` | IFR-1 ↔ G5 ↔ X-Plane wiring: FMS2 G5 mode, mapping, write-back. |
| `docs/` | G5 spec distilled from the pilot's guide (per-instrument reference). |
| `docs/images/` | Cropped G5 screen references, one folder per figure. |
| `G5_BUILD.md` | Build notes: spec↔implementation conformance, confirmed datarefs. |
| `pilots_guide.pdf` | Source: Garmin G5 Pilot's Guide for Certified Aircraft. |

## Tuning / extending

- **Sizes/fonts:** use developer mode (press `D`) — values live in `STYLE`.
  Adding a key to `STYLE_DEFAULTS` auto-adds a panel input; reference it in the
  draw code and (optionally) register a `hit(x,y,w,h,[keys])` region so hovering
  the component highlights its row.
- Data fields and their datarefs: `DATAREFS` in `server.py`.
- Other visuals (layout, tape scales, colors): `g5.html` — all drawing is in the
  `<script>` block. `L()` controls PFD geometry; `pxKt` / `pxFt` set tape scales.
- Conformance status and what's left to build: `G5_BUILD.md`.
