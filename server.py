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
    "trk":     "sim/cockpit2/gauges/indicators/ground_track_mag_pilot",
    "crs":     "sim/cockpit/radios/gps_course_degtm",      # selected course
    "cdi":     "sim/cockpit2/radios/indicators/hsi_hdef_dots_pilot",
    "tofrom":  "sim/cockpit2/radios/indicators/hsi_flag_from_to_pilot",  # 0/1 to/2 from
    "dist":    "sim/cockpit2/radios/indicators/hsi_dme_distance_nm_pilot",
    "brg":     "sim/cockpit2/radios/indicators/gps_bearing_deg_mag_pilot",
    "vdef":    "sim/cockpit2/radios/indicators/hsi_vdef_dots_pilot",  # vertical (GS/GP) deviation, dots
    "vshow":   "sim/cockpit2/radios/indicators/hsi_flag_glideslope_pilot",  # 1 = vertical guidance valid
    # --- flight director command bars ---
    "fdpitch": "sim/cockpit2/autopilot/flight_director_pitch_deg",   # commanded pitch
    "fdroll":  "sim/cockpit2/autopilot/flight_director_roll_deg",    # commanded roll
    "apmode":  "sim/cockpit2/autopilot/autopilot_mode",              # 0 off / 1 FD / 2 AP engaged
    # --- HSI nav-state annunciations ---
    "navsrc":  "sim/cockpit2/radios/indicators/hsi_source_select_pilot",  # 0/1 = VLOC, 2 = GPS
    "cdiscale": "sim/cockpit/gps/cdi_scale_index",   # 0 ENR / 1 TERM / 2 APR  (UNVERIFIED path)
    "msg":     "sim/cockpit2/annunciators/gps_message",       # GPS message flag (UNVERIFIED path)
    "obs":     "sim/cockpit/gps/gps_obs_mode",                # OBS mode active  (UNVERIFIED path)
    "gpss":    "sim/cockpit2/autopilot/gpss_status",          # GPSS roll steering (UNVERIFIED path)
}

_last_rx = 0.0
_demo: dict | None = None


def _mark_rx(_path: str, _value: float) -> None:
    global _last_rx
    _last_rx = time.monotonic()


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
            "alt": 3000.0 + 350.0 * math.sin(t * 0.08),  # swings across ±200 band → exercises alt alerting
            "vsi": 700.0 * math.cos(t * 0.08),
            "hdg": (t * 4.0) % 360.0,
            "slip": 1.5 * math.sin(t * 0.5),
            "baro": 29.92,
            "gs": 110.0 + 12.0 * math.sin(t * 0.12),
            "turn": 2.0 * math.sin(t * 0.18),
            "hdgbug": 130.0,       # fixed selected heading for the demo
            "altsel": 3000.0,      # fixed selected altitude for the demo
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
            "cdiscale": float(int(t / 5) % 3),                 # ENR -> TERM -> APR
            "msg": 1.0 if (int(t / 7) % 2 == 0) else 0.0,      # MSG flag blink
            "obs": 1.0 if (int(t / 9) % 2 == 0) else 0.0,      # OBS on/off
            "gpss": 1.0 if (int(t / 11) % 2 == 0) else 0.0,    # GPSS on/off
        }
        _last_rx = time.monotonic()
        time.sleep(0.03)


def snapshot(xp: XPlaneClient | None) -> dict:
    if _demo is not None:
        data = {k: round(v, 3) for k, v in _demo.items()}
    else:
        data = {key: round(xp.value(path), 3) for key, path in DATAREFS.items()}
    data["live"] = (time.monotonic() - _last_rx) < 1.0
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
    args = ap.parse_args()

    xp = None
    if args.demo:
        threading.Thread(target=_demo_loop, daemon=True).start()
        print("DEMO mode: synthetic flight data (no X-Plane).")
    else:
        xp = XPlaneClient(host=args.xplane_host, port=args.xplane_port)
        xp.start_receiver(on_change=_mark_rx)
        for path in DATAREFS.values():
            xp.subscribe(path, freq=int(args.rate))

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
