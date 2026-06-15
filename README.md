# browserg5

A browser-based **simulation of the Garmin G5 Electronic Flight Instrument**,
fed live from X-Plane. It renders the G5's PFD and HSI pages on an HTML canvas
and aims to match the real unit's layout, scales, colors, and knob/menu behavior
as documented in the [G5 Pilot's Guide](pilots_guide.pdf) (distilled into
[`docs/`](docs/)).

No app store and no extra Python deps — `server.py` reuses `ifrbridge`'s X-Plane
UDP client and streams data to the browser over Server-Sent Events.

## What it renders

- **PFD page** — attitude (pitch ladder, roll scale, slip/skid), airspeed tape,
  altimeter + selected-altitude bug + baro, VSI, heading/track tape, ground
  speed. (In progress: airspeed color strip & trend vector, turn-rate indicator,
  CDI, vertical-deviation diamond — see [`G5_BUILD.md`](G5_BUILD.md).)
- **HSI page** — rotating compass card, course needle + CDI, heading/track bugs,
  distance/ground-speed readouts. (In progress: bearing pointers, nav-source &
  CDI-scale annunciations.)

Display data arrives at ~30 Hz and is smoothed at the display end so motion
stays fluid.

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

- Data fields and their datarefs: `DATAREFS` in `server.py`.
- Visuals (layout, tape scales, colors): `g5.html` — all drawing is in the
  `<script>` block. `L()` controls PFD geometry; `pxKt` / `pxFt` set tape scales.
- Conformance status and what's left to build: `G5_BUILD.md`.
