# browserg5

A browser-based **simulation of the Garmin G5 Electronic Flight Instrument**,
fed live from X-Plane. It renders the G5's PFD and HSI pages on an HTML canvas
and aims to match the real unit's layout, scales, colors, and knob/menu behavior
as documented in the [G5 Pilot's Guide](pilots_guide.pdf) (distilled into
[`docs/`](docs/)).

No app store and no extra Python deps — `server.py` reuses `ifrbridge`'s X-Plane
UDP client and streams data to the browser over Server-Sent Events.

## What it renders

- **PFD page** — attitude (pitch ladder, roll scale w/ white pointer, slip/skid,
  yellow aircraft + chevron), airspeed tape with color "barber pole" strip +
  magenta trend vector + Vne-red readout + Vspeed tags, altimeter with
  selected-altitude box/bug + baro box, VSI, turn-rate indicator, CDI, vertical-
  deviation (glideslope/glidepath) diamond, and a heading/track tape (3-digit
  labels, 1°/5° ticks clamped to the bottom, current-heading lubber box with a
  triangle tab + interlocking heading bug, magenta track triangle). Fixed-size
  cyan/magenta corner boxes (selected altitude, baro, ground speed).
- **HSI page** — rotating compass card, magenta course needle + CDI + TO/FROM,
  current-track triangle + dashed line, heading bug, light-blue bearing pointer,
  center annunciations (nav source, GPS CDI scale, + MSG/OBS/GPSS stubs), vertical-
  deviation diamond, and edge-flush corner boxes (DIST, GS, selected heading).

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
g5Input(unitId, action)   // unitId: "TOP_G5" | "BOTTOM_G5"; action: "cw" | "ccw" | "press"
```

Two sources feed it:

- **Hardware controller via the ifr-1 bridge** (production) — the bridge maps a
  physical knob to a unit and forwards events. *(Transport is an open question;
  see `G5_BUILD.md`.)*
- **Mouse** (testing only) — scroll wheel = turn, button 1 = press, routed to the
  unit the cursor is hovering over (the hovered unit gets a faint outline).

Knob behavior follows the spec: when no menu is open, turning adjusts the
barometric setting (PFD) or the heading/track bug (HSI); pressing opens the menu;
in the menu, turning moves the cursor and pressing selects; selecting Heading /
Altitude opens a centered edit dialog.

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
```

It prints the URL(s) to open, e.g. `http://192.168.1.42:8080/`. Make sure
X-Plane's network UDP output is enabled (Settings → Network), same as for the
panel bridge. Open the URL in a browser and click once to start.

> The `server.py` reads `g5.html` fresh on every request, so a browser refresh
> picks up edits without restarting the server.

## Project layout

| Path | What |
|------|------|
| `g5.html` | The unit — all rendering + input handling (single self-contained file). |
| `server.py` | X-Plane UDP → SSE bridge; `--demo` for synthetic data. |
| `docs/` | G5 spec distilled from the pilot's guide (per-instrument reference). |
| `docs/images/` | Cropped G5 screen references, one folder per figure. |
| `G5_BUILD.md` | Build notes: spec↔implementation conformance, plan, open questions. |
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
