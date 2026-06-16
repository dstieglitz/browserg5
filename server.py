#!/usr/bin/env python3
"""browserg5 — serve a G5-style PFD to a phone browser, fed live from X-Plane.

Reuses ifrbridge's tested X-Plane UDP client to subscribe to the handful of
datarefs a G5 PFD needs, then streams them to the browser over Server-Sent
Events (no extra Python dependencies). Open http://<this-host>:8080/ on the
phone, or "Add to Home Screen" for true fullscreen.

    python browserg5/server.py                       # X-Plane on this machine
    python browserg5/server.py --xplane-host 192.168.1.50
    python browserg5/server.py --http-port 8080
"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Reuse the bridge's X-Plane UDP driver (RREF subscriptions).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ifrbridge.xplane import XPlaneClient  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "g5.html")

# Friendly key -> X-Plane dataref. The browser only ever sees the friendly keys.
DATAREFS: dict[str, str] = {
    "pitch":   "sim/cockpit2/gauges/indicators/pitch_electric_deg_pilot",
    "roll":    "sim/cockpit2/gauges/indicators/roll_electric_deg_pilot",
    "ias":     "sim/cockpit2/gauges/indicators/airspeed_kts_pilot",
    "alt":     "sim/cockpit2/gauges/indicators/altitude_ft_pilot",
    "vsi":     "sim/cockpit2/gauges/indicators/vvi_fpm_pilot",
    "hdg":     "sim/cockpit2/gauges/indicators/heading_electric_deg_mag_pilot",
    "slip":    "sim/cockpit2/gauges/indicators/slip_deg",
    "baro":    "sim/cockpit2/gauges/actuators/barometer_setting_in_hg_pilot",
    "gs":      "sim/cockpit2/gauges/indicators/ground_speed_kt",
    "turn":    "sim/cockpit2/gauges/indicators/turn_rate_heading_deg_pilot",
    "hdgbug":  "sim/cockpit/autopilot/heading_mag",        # selected heading
    "altsel":  "sim/cockpit/autopilot/altitude",           # selected altitude
    # --- HSI mode ---
    # crs/cdi/tofrom/dist are DERIVED source-aware in snapshot() from the
    # per-source `_*` datarefs below — the hsi_* composites are nav1-only.
    "trk":     "sim/cockpit2/gauges/indicators/ground_track_mag_pilot",
    "brg":     "sim/cockpit2/radios/indicators/gps_bearing_deg_mag_pilot",
    "vdef":    "sim/cockpit2/radios/indicators/hsi_vdef_dots_pilot",  # vertical (GS/GP) deviation, dots
    "vshow":   "sim/cockpit2/radios/indicators/hsi_flag_glideslope_pilot",  # 1 = vertical guidance valid
    # per-source variants — consumed and dropped in snapshot() (underscore keys)
    # pilot-side selected course (OBS for VORs / localizer heading for ILS)
    "_crs_nav1": "sim/cockpit2/radios/actuators/nav1_course_deg_mag_pilot",
    "_crs_nav2": "sim/cockpit2/radios/actuators/nav2_course_deg_mag_pilot",
    "_crs_gps":  "sim/cockpit/radios/gps_obs_degm",   # GPS DTK/desired track (what the HSI shows; gps_course_degtm is the VOR radial)
    "_cdi_nav1": "sim/cockpit2/radios/indicators/nav1_hdef_dots_pilot",
    "_cdi_nav2": "sim/cockpit2/radios/indicators/nav2_hdef_dots_pilot",
    "_cdi_gps":  "sim/cockpit2/radios/indicators/gps_hdef_dots_pilot",
    "_tf_nav1":  "sim/cockpit2/radios/indicators/nav1_flag_from_to_pilot",  # 0 flag/1 to/2 from
    "_tf_nav2":  "sim/cockpit2/radios/indicators/nav2_flag_from_to_pilot",
    "_dme_nav":  "sim/cockpit2/radios/indicators/hsi_dme_distance_nm_pilot",  # nav1 DME
    "_dme_gps":  "sim/cockpit/radios/gps_dme_dist_m",                         # GPS dist to wpt (nm)
    "_gps_dest": "sim/cockpit/gps/destination_type",                         # >0 = GPS has an active leg
    # --- flight director command bars ---
    "fdpitch": "sim/cockpit2/autopilot/flight_director_pitch_deg",   # commanded pitch
    "fdroll":  "sim/cockpit2/autopilot/flight_director_roll_deg",    # commanded roll
    "apmode":  "sim/cockpit2/autopilot/autopilot_mode",              # 0 off / 1 FD / 2 AP engaged
    # --- HSI nav-state annunciations ---
    # actuator (switch position) — the indicator variant is stuck on some aircraft
    "navsrc":  "sim/cockpit2/radios/actuators/HSI_source_select_pilot",   # 0/1 = VLOC, 2 = GPS
    "cdiscale": "sim/cockpit/radios/gps_cdi_sensitivity",  # enum 0=OCN 1=ENR 2=TERM 5=APR … (CONFIRMED)
    "msg":     "sim/cockpit2/annunciators/gps_message",       # GPS message flag (UNVERIFIED path)
    "obs":     "sim/cockpit/gps/gps_obs_mode",                # OBS mode active  (UNVERIFIED path)
    "gpss":    "sim/cockpit2/autopilot/gpss_status",          # GPSS roll steering (UNVERIFIED path)
}

_last_rx = 0.0
_demo: dict | None = None

# Keys the G5 knob may write back to X-Plane (their datarefs are in DATAREFS).
WRITABLE = {"baro", "hdgbug", "altsel", "crs"}

# --- IFR-1 -> G5 input channel ----------------------------------------------
# When the panel's mode selector is on FMS2 ("G5 mode"), its encoders/buttons
# drive the G5 units instead of the aircraft. Decoded events are queued here and
# folded into the SSE stream as `_inputs` for the browser's dispatchBridgeInputs.
G5_MODE = "FMS2"
# In G5 mode the inner ring + CRSR drive the FOCUSED unit; SWAP toggles focus.
# The browser resolves the "FOCUS" sentinel to whichever unit is selected.
G5_FOCUS = "FOCUS"
G5_SWITCH_BTN = "SWAP"
_g5_lock = threading.Lock()
_g5_inputs: list[dict] = []


def _push_g5_input(unit: str, action: str) -> None:
    with _g5_lock:
        _g5_inputs.append({"unit": unit, "action": action})


def _drain_g5_inputs() -> list[dict]:
    with _g5_lock:
        if not _g5_inputs:
            return []
        out = _g5_inputs[:]
        _g5_inputs.clear()
        return out


def _write_dataref(key: str, xp) -> str | None:
    """Resolve a writable G5 knob key to its X-Plane dataref. `crs` is special:
    its READ dataref (nav*_course_deg_mag, the displayed course) is read-only —
    writing the course goes to the OBS setpoint of the SELECTED source instead."""
    if key == "crs":
        src = round(xp.value(DATAREFS["navsrc"]))
        return ("sim/cockpit/radios/gps_obs_degm" if src >= 2   # GPS DTK/OBS course
                else "sim/cockpit/radios/nav2_obs_degm" if src == 1
                else "sim/cockpit/radios/nav1_obs_degm")
    return DATAREFS.get(key)


HOLD_SEC = 3.0
_g5_held: dict = {}   # button -> [unit, press_monotonic, hold_fired]


def _route_g5(ev) -> None:
    """Translate an IFR-1 decoder event into a G5 knob action for the FOCUSED unit.
    Inner ring = turn, CRSR = press/hold (deferred: quick press fires on RELEASE,
    a ≥3 s hold fires `hold` via _g5_tick), SWAP = switch focus."""
    if hasattr(ev, "ring"):                        # EncoderEvent — inner ring only
        if ev.ring == "inner":
            _push_g5_input(G5_FOCUS, "cw" if ev.direction > 0 else "ccw")
    elif hasattr(ev, "button"):                    # ButtonEvent
        if ev.button == G5_SWITCH_BTN:             # SWAP = select the other unit
            if ev.edge == "press":
                _push_g5_input(G5_FOCUS, "switch")
        elif ev.button == "CRSR":                  # inner push = press/hold focused unit
            if ev.edge == "press":
                _g5_held["CRSR"] = [G5_FOCUS, time.monotonic(), False]
            elif ev.edge == "release":
                st = _g5_held.pop("CRSR", None)
                if st and not st[2]:               # released before the hold threshold
                    _push_g5_input(G5_FOCUS, "press")


def _g5_tick() -> None:
    """Fire `hold` for any G5 button held past HOLD_SEC (call each poll)."""
    now = time.monotonic()
    for st in _g5_held.values():
        if not st[2] and (now - st[1]) >= HOLD_SEC:
            st[2] = True
            _push_g5_input(st[0], "hold")


def _mark_rx(_path: str, _value: float) -> None:
    global _last_rx
    _last_rx = time.monotonic()


def _resend_subscriptions(xp, subs, freq: int) -> None:
    """Re-send the RREF subscriptions. X-Plane forgets them when it (re)starts,
    and the client only subscribes once at boot — so without this the server
    stays dark if it launched before X-Plane (e.g. as a boot service)."""
    for path, index in subs:
        pb = path.encode("latin-1")
        msg = b"RREF\x00" + struct.pack("<ii", freq, index) + pb + b"\x00" * (400 - len(pb))
        try:
            xp.sock.sendto(msg, xp.addr)
        except OSError:
            pass


def _resubscribe_loop(xp, subs, freq: int) -> None:
    """Watchdog: while no fresh data is arriving, keep (re)subscribing every 2 s
    so the server self-heals across start-order and X-Plane restarts."""
    while True:
        time.sleep(2.0)
        if time.monotonic() - _last_rx > 2.0:
            _resend_subscriptions(xp, subs, freq)


def _demo_loop():
    """Synthetic flight motion so the PFD can be tried without X-Plane."""
    global _demo, _last_rx
    t0 = time.monotonic()
    while True:
        t = time.monotonic() - t0
        _demo = {
            "pitch": 7.0 * math.sin(t * 0.25),
            "roll": 22.0 * math.sin(t * 0.18),
            "ias": 105.0 + 12.0 * math.sin(t * 0.12),
            "alt": 3300.0 + 350.0 * math.sin(t * 0.08),  # ~3,300 ft; swings ±350 for alerting
            "vsi": 700.0 * math.cos(t * 0.08),
            "hdg": (t * 4.0) % 360.0,
            "slip": 1.5 * math.sin(t * 0.5),
            "baro": 29.92,
            "gs": 110.0 + 12.0 * math.sin(t * 0.12),
            "turn": 2.0 * math.sin(t * 0.18),
            "hdgbug": 130.0,       # fixed selected heading for the demo
            "altsel": 3300.0,      # fixed selected altitude for the demo
            "trk": 50.0,
            "crs": 150.0,
            "cdi": 2.0 * math.sin(t * 0.13),                     # sweep the CDI needle
            "tofrom": 1.0,
            "dist": 1000.0,
            "brg": (120.0 + 45.0 * math.sin(t * 0.1)) % 360.0,   # sweep the bearing needle
            "vdef": 0.8 * math.sin(t * 0.15),   # synthetic glideslope deviation
            "vshow": 1.0,
            "fdpitch": 5.0 * math.sin(t * 0.22),
            "fdroll": 14.0 * math.sin(t * 0.16),
            "apmode": 2.0 if (int(t / 8) % 2 == 0) else 1.0,   # toggle AP (solid) / FD-only (hollow)
            # cycle the HSI annunciations so each state is exercised
            "navsrc": 2.0 if (int(t / 10) % 2 == 0) else 0.0,  # GPS (magenta) <-> VLOC (green)
            "cdiscale": float([1, 2, 5][int(t / 5) % 3]),      # ENR -> TERM -> APR
            "msg": 1.0 if (int(t / 7) % 2 == 0) else 0.0,      # MSG flag blink
            "obs": 1.0 if (int(t / 9) % 2 == 0) else 0.0,      # OBS on/off
            "gpss": 1.0 if (int(t / 11) % 2 == 0) else 0.0,    # GPSS on/off
            # battery indicator (G5 backup battery not in X-Plane -> demo only)
            "batt": 50.0 + 48.0 * math.sin(t * 0.05),          # sweep 2%..98% (green/yellow/red)
            "battchg": 1.0 if (int(t / 13) % 2 == 0) else 0.0, # charging bolt toggle
            "battshow": 1.0,
        }
        _last_rx = time.monotonic()
        time.sleep(0.03)


def _ifr1_loop(xp: "XPlaneClient | None", mcc_path: str | None, verbose: bool):
    """Own the IFR-1 HID. In G5_MODE, route events to the G5; otherwise hand them
    to the aircraft Bridge (if an .mcc was given). Also refreshes the AP LEDs."""
    try:
        from ifrbridge.ifr1 import IFR1Device, Decoder
    except Exception as e:  # noqa: BLE001 — hidapi missing / import error
        print(f"IFR-1: cannot load HID layer ({e}); --ifr1 disabled.")
        return
    try:
        device = IFR1Device()
    except Exception as e:  # noqa: BLE001 — no device / not permitted
        print(f"IFR-1: cannot open device ({e}); --ifr1 disabled.")
        return

    bridge = None
    if mcc_path and xp is not None:
        try:
            from ifrbridge.bridge import Bridge
            from ifrbridge.mcc import parse_mcc
            bridge = Bridge(parse_mcc(mcc_path), xp, device=device, verbose=verbose)
        except Exception as e:  # noqa: BLE001
            print(f"IFR-1: aircraft bridge disabled ({e}); G5 routing only.")
    decoder = bridge.decoder if bridge is not None else Decoder()
    print(f"IFR-1: connected. Mode '{G5_MODE}' drives the G5; "
          f"aircraft bridge={'on' if bridge else 'off'}.")

    led_byte = -1
    while True:
        report = device.read()
        if report:
            for ev in decoder.feed(report):
                if decoder.mode == G5_MODE:
                    _route_g5(ev)
                elif bridge is not None:
                    bridge.handle_event(ev)
        _g5_tick()   # emit `hold` for any G5 button held ≥ HOLD_SEC
        if bridge is not None:
            nb = bridge.compute_led_byte()
            if nb != led_byte:
                led_byte = nb
                device.set_leds(nb)
        time.sleep(1.0 / 200.0)


def snapshot(xp: XPlaneClient | None) -> dict:
    if _demo is not None:
        data = {k: round(v, 3) for k, v in _demo.items()}
    else:
        data = {key: round(xp.value(path), 3) for key, path in DATAREFS.items()}
        # Build the HSI course/CDI/to-from/distance for the SELECTED source —
        # the hsi_* composites are nav1-only, so pick per source (0/1=NAV, 2=GPS).
        src = round(data.get("navsrc", 0))
        if src >= 2:        # GPS
            data["crs"], data["cdi"] = data["_crs_gps"], data["_cdi_gps"]
            data["tofrom"] = 1.0 if data["_gps_dest"] > 0 else 0.0   # GPS leg = TO
            data["dist"] = data["_dme_gps"]
        elif src == 1:      # NAV2
            data["crs"], data["cdi"] = data["_crs_nav2"], data["_cdi_nav2"]
            data["tofrom"], data["dist"] = data["_tf_nav2"], data["_dme_nav"]
        else:               # NAV1
            data["crs"], data["cdi"] = data["_crs_nav1"], data["_cdi_nav1"]
            data["tofrom"], data["dist"] = data["_tf_nav1"], data["_dme_nav"]
        for k in [k for k in data if k.startswith("_")]:
            data.pop(k)
    data["live"] = (time.monotonic() - _last_rx) < 1.0
    inputs = _drain_g5_inputs()
    if inputs:
        data["_inputs"] = inputs
    return data


def make_handler(xp: XPlaneClient, rate_hz: float):
    period = 1.0 / rate_hz

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # quiet
            pass

        def _send_headers(self, ctype: str, length: int | None = None,
                          extra: dict | None = None):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            if length is not None:
                self.send_header("Content-Length", str(length))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html", "/g5.html"):
                self._serve_page()
            elif path == "/events":
                self._serve_events()
            else:
                self.send_error(404)

        def do_POST(self):
            # Knob write-back: the G5 pushes the value it set (baro/heading bug/
            # selected altitude/course) so X-Plane follows the simulated knob.
            if self.path.split("?")[0] != "/write":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                data = {}
            if xp is not None:
                for k, v in data.items():
                    if k not in WRITABLE:
                        continue
                    path = _write_dataref(k, xp)
                    if path:
                        try:
                            xp.set_dataref(path, float(v))
                        except (ValueError, TypeError):
                            pass
            self._send_headers("text/plain; charset=utf-8", 0)

        def _serve_page(self):
            try:
                with open(PAGE, "rb") as f:
                    body = f.read()
            except OSError:
                self.send_error(500, "g5.html missing")
                return
            self._send_headers("text/html; charset=utf-8", len(body))
            self.wfile.write(body)

        def _serve_events(self):
            self._send_headers("text/event-stream", extra={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            })
            try:
                while True:
                    payload = json.dumps(snapshot(xp))
                    self.wfile.write(f"data: {payload}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(period)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return  # phone navigated away / slept

    return Handler


def local_ips() -> list[str]:
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ips)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xplane-host", default="127.0.0.1")
    ap.add_argument("--xplane-port", type=int, default=49000)
    ap.add_argument("--http-port", type=int, default=8080)
    ap.add_argument("--rate", type=float, default=30.0, help="stream Hz")
    ap.add_argument("--demo", action="store_true",
                    help="synthetic motion instead of X-Plane (for testing)")
    ap.add_argument("--ifr1", action="store_true",
                    help="read the Octavi IFR-1; mode FMS2 drives the G5 knobs")
    ap.add_argument("--mcc", default=None,
                    help="MobiFlight .mcc to bridge the aircraft in non-G5 modes")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    xp = None
    if args.demo:
        threading.Thread(target=_demo_loop, daemon=True).start()
        print("DEMO mode: synthetic flight data (no X-Plane).")
    else:
        xp = XPlaneClient(host=args.xplane_host, port=args.xplane_port)
        xp.start_receiver(on_change=_mark_rx)
        freq = int(args.rate)
        subs = [(path, xp.subscribe(path, freq=freq)) for path in DATAREFS.values()]
        # self-healing watchdog: re-subscribe while no data (handles boot-before-
        # X-Plane and X-Plane restarts without needing a server restart).
        threading.Thread(target=_resubscribe_loop, args=(xp, subs, freq), daemon=True).start()

    if args.ifr1:
        threading.Thread(target=_ifr1_loop, args=(xp, args.mcc, args.verbose),
                         daemon=True).start()

    handler = make_handler(xp, args.rate)
    httpd = ThreadingHTTPServer(("0.0.0.0", args.http_port), handler)
    httpd.daemon_threads = True

    print(f"browserg5 serving on port {args.http_port}, X-Plane at "
          f"{args.xplane_host}:{args.xplane_port}")
    for ip in local_ips():
        print(f"  open on your phone:  http://{ip}:{args.http_port}/")
    print("Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        httpd.shutdown()
        if xp is not None:
            xp.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
