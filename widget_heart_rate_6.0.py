#!/usr/bin/env python3
"""
Live Heart Rate Widget (Bluetooth LE)
=======================================

A small, floating desktop widget that shows your live heart rate --
no big window, no title bar, just a compact card you can drag anywhere
on screen and leave running while you work.

Reads from any Bluetooth LE device that advertises the standard
"Heart Rate" GATT service (Fitbit Air, chest straps, etc).

SETUP
-----
1. Install Python 3.9+ on your PC.
2. Install the Bluetooth library (that's the only dependency now --
   no matplotlib needed, which is why this version is much lighter):
       pip install bleak
3. Make sure your PC has Bluetooth and the Fitbit Air is being worn.
4. IMPORTANT: some BLE heart rate devices only stream to ONE connected
   client at a time. If Google Health is actively connected on your
   phone, close it first.
5. On startup, the widget automatically runs unpair_device.ps1 (place it
   in the same folder) to forget any stale paired connection Windows may
   be silently holding -- this is what used to require manually going
   into Settings > Bluetooth & devices > Remove device each time. Safe to
   delete that .ps1 file if you'd rather it not touch Bluetooth pairings;
   the script just won't attempt the auto-unpair step if it's missing.

USAGE
-----
    python heart_rate_widget.py                  # opens the floating widget (default)
    python heart_rate_widget.py --max-hr 190       # your max HR, for zone %
    python heart_rate_widget.py --console           # plain terminal output instead
    python heart_rate_widget.py --name Fitbit        # filter scan by name (also used
                                                        # to match the device to unpair)

While the widget is open:
    - Click and drag anywhere on it to move it around your screen
    - Click − to minimize it to the taskbar, or × to close it
    - Shrink it below ~170px to switch to a minimal number-only view
    - It stays on top of other windows by default
"""

import asyncio
import os
import subprocess
import sys
import argparse
from collections import deque
from datetime import datetime
from typing import Optional

from bleak import BleakScanner, BleakClient

HEART_RATE_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

# ====================== USER SETTINGS ======================
DEFAULT_MAX_HR = 174        # <- change this to your max heart rate
# =============================================================


def try_auto_unpair(name_filter: Optional[str]):
    """Best-effort: ask Windows to forget the device before scanning."""
    if sys.platform != "win32":
        return

    base_path = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    ps_script = os.path.join(base_path, "unpair_device.ps1")
    if not os.path.exists(ps_script):
        return

    target = name_filter or "Fitbit"

    # Prevent the black PowerShell window from flashing
    CREATE_NO_WINDOW = 0x08000000

    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden",
                "-File", ps_script,
                "-DeviceName", target,
            ],
            timeout=15,
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        pass


def parse_heart_rate(data: bytearray) -> int:
    """Parse the Heart Rate Measurement characteristic per the Bluetooth GATT spec."""
    flags = data[0]
    hr_format_uint16 = flags & 0x01
    if hr_format_uint16:
        return int.from_bytes(data[1:3], byteorder="little")
    return data[1]


async def find_hr_devices(name_filter: Optional[str], timeout: float = 10.0):
    print(f"Scanning for Bluetooth heart rate devices ({timeout:.0f}s)...")
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)

    candidates = []
    for device, adv in found.values():
        service_uuids = [u.lower() for u in (adv.service_uuids or [])]
        name = device.name or ""
        has_hr_service = HEART_RATE_SERVICE_UUID in service_uuids
        name_ok = (name_filter.lower() in name.lower()) if name_filter else True
        if has_hr_service and name_ok:
            candidates.append(device)

    return candidates


async def choose_device(name_filter: Optional[str]):
    candidates = await find_hr_devices(name_filter)
    if not candidates:
        print("No matching devices found.")
        print("Make sure:")
        print("  - Bluetooth is on and your PC supports BLE")
        print("  - The Fitbit Air is nearby and worn (so it's actively broadcasting)")
        print("  - It isn't already connected/streaming to your phone's Google Health app")
        sys.exit(1)

    if len(candidates) == 1:
        chosen = candidates[0]
        print(f"Found: {chosen.name or 'Unknown device'} ({chosen.address})")
        return chosen

    print("Multiple devices found:")
    for i, d in enumerate(candidates):
        print(f"  [{i}] {d.name or 'Unknown device'} ({d.address})")
    idx = int(input("Select device number: "))
    return candidates[idx]


def print_console(bpm: int):
    ts = datetime.now().strftime("%H:%M:%S")
    bar = "#" * min(bpm // 2, 100)
    sys.stdout.write(f"\r[{ts}]  {bpm:3d} bpm  {bar}" + " " * 20)
    sys.stdout.flush()


async def run_console(name_filter: Optional[str]):
    try_auto_unpair(name_filter)
    device = await choose_device(name_filter)

    def handle_notify(_, data):
        bpm = parse_heart_rate(data)
        print_console(bpm)

    async with BleakClient(device) as client:
        await client.start_notify(HEART_RATE_MEASUREMENT_UUID, handle_notify)
        print("Connected. Streaming live heart rate. Press Ctrl+C to stop.\n")
        try:
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await client.stop_notify(HEART_RATE_MEASUREMENT_UUID)
            print("\nDisconnected.")


def get_zone(bpm: int, max_hr: Optional[int]):
    """
    Zones based on % of max HR:
      Light     (blue)   :  0 - 58%
      Moderate  (green)  : 58 - 73%
      Vigorous  (yellow) : 73 - 92%
      Peak      (red)    : 92%+
    """
    if not max_hr or max_hr <= 0:
        max_hr = DEFAULT_MAX_HR
    pct = (bpm / max_hr) * 100
    if pct < 58:
        return "Light", "#3b82f6"
    elif pct < 73:
        return "Moderate", "#22c55e"
    elif pct < 92:
        return "Vigorous", "#eab308"
    else:
        return "Peak", "#ef4444"


def _pick_font(root, candidates, size, weight="normal"):
    import tkinter.font as tkfont
    available = set(tkfont.families(root))
    for name in candidates:
        if name in available:
            return (name, size, weight)
    return (candidates[-1], size, weight)


def _rounded_rect(canvas, x1, y1, x2, y2, r=18, **kwargs):
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def run_widget(name_filter: Optional[str], max_hr: Optional[int]):
    import tkinter as tk
    import threading
    import os

    if max_hr is None:
        max_hr = DEFAULT_MAX_HR

    BG = "#0f0f14"
    CARD = "#17171f"
    CARD_BORDER = "#2a2a38"
    MUTED = "#6b7280"
    DEFAULT_COLOR = "#3b82f6"

    W, H = 260, 300

    root = tk.Tk()
    root.title("Heart Rate")
    root.overrideredirect(True)          # no title bar -- true widget look
    root.attributes("-topmost", True)    # stays above other windows

    # Window/taskbar icon, if an icon.ico is sitting next to the script (or
    # bundled into a PyInstaller .exe via --add-data). Safe to skip if absent.
    try:
        base_path = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(base_path, "icon.ico")
        if os.path.exists(icon_path):
            root.iconbitmap(icon_path)
    except Exception:
        pass

    # A "transparent color key" -- Windows treats this exact color as
    # invisible (and click-through), so only the rounded card itself is
    # visible instead of a square window with a rounded shape drawn inside it.
    TRANSPARENT_KEY = "#0a0a0a" if BG != "#0a0a0a" else "#0b0b0b"
    supports_transparency = True
    try:
        root.attributes("-transparentcolor", TRANSPARENT_KEY)
    except tk.TclError:
        supports_transparency = False  # not on Windows -- fall back to solid bg

    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{W}x{H}+{sw - W - 40}+60")
    MIN_W, MIN_H = 100, 100
    COMPACT_BELOW = 170  # shrink below this height (or width) -> number-only mode
    win_bg = TRANSPARENT_KEY if supports_transparency else BG
    root.configure(bg=win_bg)

    # Resolve the best-available font family once; sizes are recomputed on resize.
    FAMILY = ["Segoe UI Semibold", "Segoe UI", "SF Pro Display", "Helvetica Neue", "Helvetica", "Arial"]
    family_name = _pick_font(root, FAMILY, 10, "normal")[0]

    canvas = tk.Canvas(root, width=W, height=H, bg=win_bg, highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    history = deque(maxlen=60)
    all_readings = []
    state = {"bpm": None, "zone": "Starting...", "color": DEFAULT_COLOR, "pct": None,
             "dot": "#52525b"}

    def layout(w, h):
        """Redraw everything for the current window size -- called on load and
        whenever the widget is resized, so text/spacing scale with it.
        Below COMPACT_BELOW, switch to a minimal number-only view."""
        canvas.delete("all")
        size_state["w"], size_state["h"] = w, h
        compact = h < COMPACT_BELOW or w < COMPACT_BELOW

        _rounded_rect(canvas, 2, 2, w - 2, h - 2, r=min(22, w * 0.08, h * 0.08),
                      fill=CARD, outline=CARD_BORDER, width=1)

                # ---- Status dot (perfectly round) ----
        dot_r = 5 if compact else 6
        # Center the circle at (12, 12) so it stays perfectly circular
        dot_id = canvas.create_oval(
            12 - dot_r, 12 - dot_r,
            12 + dot_r, 12 + dot_r,
            fill=state["dot"], outline=""
        )

        # ---- close (×) and minimize (−) buttons ----
        f_close = (family_name, max(10, round(min(w, h) * 0.09)), "normal")
        close_x = w - (14 if compact else 18)
        close_y = 14 if compact else 16

        # Both buttons use the same text centering → perfect vertical alignment
        canvas.create_text(close_x, close_y, text="\u00d7", font=f_close,
                           fill=MUTED, anchor="center")

        min_x = close_x - (16 if compact else 22)
        canvas.create_text(min_x, close_y, text="\u2212", font=f_close,  # proper minus sign
                           fill=MUTED, anchor="center")

        hit_pad = 10
        hitboxes["close"] = (close_x - hit_pad, close_y - hit_pad,
                             close_x + hit_pad, close_y + hit_pad)
        hitboxes["min"]   = (min_x - hit_pad, close_y - hit_pad,
                             min_x + hit_pad, close_y + hit_pad)

        ids = {"dot": dot_id}

        if compact:
            # ---- minimal view: just the number ----
            f_bpm = (family_name, max(18, round(min(w, h) * 0.4)), "bold")
            bpm_id = canvas.create_text(w / 2, h / 2, text=str(state["bpm"] or "--"),
                                         font=f_bpm, fill=state["color"])
            ids["bpm"] = bpm_id
        else:
            scale = max(h / H, 0.6)
            f_bpm = (family_name, max(26, round(52 * scale)), "bold")
            f_unit = (family_name, max(8, round(10 * scale)), "normal")
            f_zone = (family_name, max(10, round(13 * scale)), "bold")
            f_pct = (family_name, max(8, round(10 * scale)), "normal")
            f_tiny = (family_name, max(7, round(9 * scale)), "normal")

            cx = w / 2
            bpm_id = canvas.create_text(cx, h * 0.36, text=str(state["bpm"] or "--"),
                                         font=f_bpm, fill=state["color"])
            canvas.create_text(cx, h * 0.49, text="BPM", font=f_unit, fill=MUTED)
            zone_id = canvas.create_text(cx, h * 0.585, text=state["zone"], font=f_zone, fill=state["color"])
            pct_id = canvas.create_text(cx, h * 0.65, text=(state["pct"] or ""), font=f_pct, fill=MUTED)

            spark_x0, spark_x1 = 20, w - 20
            spark_y0, spark_y1 = h * 0.72, h * 0.855
            canvas.create_line(spark_x0, spark_y1, spark_x1, spark_y1, fill=CARD_BORDER, width=1)
            spark_id = canvas.create_line(0, 0, 0, 0, fill=state["color"], width=2, smooth=True)

            stats_id = canvas.create_text(cx, h - max(12, h * 0.05),
                                           text=stats_string(), font=f_tiny, fill=MUTED)

            ids.update({"bpm": bpm_id, "zone": zone_id, "pct": pct_id, "spark": spark_id,
                        "stats": stats_id,
                        "spark_x0": spark_x0, "spark_x1": spark_x1,
                        "spark_y0": spark_y0, "spark_y1": spark_y1})

        # ---- resize grip, bottom-right corner (visual only -- hit-testing
        # for dragging it is done at the canvas level, see on_press/on_motion
        # below, so it keeps working even though this exact item gets
        # destroyed and recreated on every redraw) ----
        gx, gy = w - 10, h - 10
        canvas.create_line(gx - 6, gy, gx, gy - 6, gx, gy, gx - 6, gy,
                            fill=CARD_BORDER, width=2)

        current_ids.clear()
        current_ids.update(ids)
        update_sparkline()

    current_ids = {}
    size_state = {"w": W, "h": H}
    hitboxes = {}

    def stats_string():
        if not all_readings:
            return "min -- \u00b7 avg -- \u00b7 max --"
        return (f"min {min(all_readings)} \u00b7 "
                f"avg {round(sum(all_readings)/len(all_readings))} \u00b7 "
                f"max {max(all_readings)}")

    def update_sparkline():
        if "spark" not in current_ids:
            return
        x0, x1 = current_ids["spark_x0"], current_ids["spark_x1"]
        y0, y1 = current_ids["spark_y0"], current_ids["spark_y1"]
        if len(history) < 2:
            canvas.coords(current_ids["spark"], 0, 0, 0, 0)
            return
        vmin, vmax = min(history), max(history)
        span = max(vmax - vmin, 1)
        n = len(history)
        pts = []
        for i, v in enumerate(history):
            x = x0 + (i / (n - 1)) * (x1 - x0)
            ratio = (v - vmin) / span
            y = y1 - ratio * (y1 - y0)
            pts.extend([x, y])
        canvas.coords(current_ids["spark"], *pts)

    # ---- unified mouse handling, bound to the canvas itself (not to
    # individual items) so it survives layout() destroying/recreating items
    # mid-drag -- that's what was breaking the resize grip before. ----
    GRIP_HIT = 24   # px from bottom-right corner counted as "grabbing the grip"

    mode = {"type": None}
    drag = {"x": None, "y": None}
    resize_start = {"x": 0, "y": 0, "w": W, "h": H}

    def point_in(box, x, y):
        return box and box[0] <= x <= box[2] and box[1] <= y <= box[3]

    def minimize():
        # overrideredirect windows have no OS-managed frame, so Windows can't
        # normally minimize/restore them. Trick: briefly give it back a real
        # frame right before iconifying, then strip the frame again once
        # it's restored (caught via the <Map> event below).
        root.overrideredirect(False)
        root.iconify()

    def on_map(event):
        if root.state() == "normal":
            root.after(10, lambda: root.overrideredirect(True))

    root.bind("<Map>", on_map)

    def on_press(event):
        w, h = size_state["w"], size_state["h"]
        if point_in(hitboxes.get("close"), event.x, event.y):
            root.destroy()
        elif point_in(hitboxes.get("min"), event.x, event.y):
            minimize()
        elif event.x > w - GRIP_HIT and event.y > h - GRIP_HIT:
            mode["type"] = "resize"
            resize_start["x"], resize_start["y"] = event.x_root, event.y_root
            resize_start["w"], resize_start["h"] = root.winfo_width(), root.winfo_height()
        else:
            mode["type"] = "move"
            drag["x"], drag["y"] = event.x, event.y

    def on_motion(event):
        if mode["type"] == "resize":
            dw = event.x_root - resize_start["x"]
            dh = event.y_root - resize_start["y"]
            new_w = max(MIN_W, resize_start["w"] + dw)
            new_h = max(MIN_H, resize_start["h"] + dh)
            root.geometry(f"{new_w}x{new_h}")
        elif mode["type"] == "move":
            dx, dy = event.x - drag["x"], event.y - drag["y"]
            root.geometry(f"+{root.winfo_x() + dx}+{root.winfo_y() + dy}")

    def on_release(event):
        mode["type"] = None

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_motion)
    canvas.bind("<ButtonRelease-1>", on_release)

    def on_configure(event):
        if event.width > 10 and event.height > 10:
            layout(event.width, event.height)

    canvas.bind("<Configure>", on_configure)

    def on_bpm(bpm: int):
        history.append(bpm)
        all_readings.append(bpm)
        zone, color = get_zone(bpm, max_hr)
        pct = round((bpm / max_hr) * 100)
        state.update(bpm=bpm, zone=zone, color=color, pct=f"{pct}% of max HR", dot="#22c55e")

        ids = current_ids
        if "bpm" in ids:
            canvas.itemconfig(ids["bpm"], text=str(bpm), fill=color)
        if "zone" in ids:
            canvas.itemconfig(ids["zone"], text=zone, fill=color)
        if "pct" in ids:
            canvas.itemconfig(ids["pct"], text=state["pct"])
        if "spark" in ids:
            canvas.itemconfig(ids["spark"], fill=color)
        if "dot" in ids:
            canvas.itemconfig(ids["dot"], fill="#22c55e")
        if "stats" in ids:
            canvas.itemconfig(ids["stats"], text=stats_string())
        update_sparkline()

    def set_status(text, color=None):
        state["zone"] = text
        if color:
            state["color"] = color
            state["dot"] = color
        if "zone" in current_ids:
            canvas.itemconfig(current_ids["zone"], text=text, fill=state["color"])
        if "dot" in current_ids:
            canvas.itemconfig(current_ids["dot"], fill=state["dot"])

    layout(W, H)  # initial draw

    async def scan_until_found(name_filter):
        """Keep scanning in a loop instead of giving up after one miss -- a
        widget meant to sit running in the background should retry, not die."""
        attempt = 0
        while True:
            attempt += 1
            dots = "." * ((attempt % 3) + 1)
            root.after(0, lambda d=dots: set_status(f"Scanning{d}", "#52525b"))
            candidates = await find_hr_devices(name_filter, timeout=8.0)
            if candidates:
                # Widget mode auto-picks the first match -- no terminal prompt,
                # since there may not be a visible console to type into.
                return candidates[0]
            root.after(0, lambda: set_status("No device found, retrying...", "#f59e0b"))
            await asyncio.sleep(3)

    async def ble_task():
        while True:
            device = await scan_until_found(name_filter)
            root.after(0, lambda d=device: set_status(f"Connecting to {d.name or 'device'}...", "#f59e0b"))

            def handle_notify(_, data):
                bpm = parse_heart_rate(data)
                root.after(0, on_bpm, bpm)

            try:
                async with BleakClient(device) as client:
                    await client.start_notify(HEART_RATE_MEASUREMENT_UUID, handle_notify)
                    root.after(0, lambda: set_status("Connected", "#22c55e"))
                    while client.is_connected:
                        await asyncio.sleep(1)
            except Exception:
                pass

            # Connection lost or never established -- pause, then rescan.
            root.after(0, lambda: set_status("Disconnected, rescanning...", "#f59e0b"))
            await asyncio.sleep(2)

    def start_loop():
        try_auto_unpair(name_filter)  # runs in this background thread, not the UI thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(ble_task())

    threading.Thread(target=start_loop, daemon=True).start()
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description="Live heart rate widget over Bluetooth LE")
    parser.add_argument("--console", action="store_true",
                         help="Run in the terminal instead of opening the widget")
    parser.add_argument("--name", type=str, default=None,
                         help="Filter scan results by device name substring")
    parser.add_argument("--max-hr", type=int, default=None,
                         help=f"Your max heart rate, e.g. 190 (default: {DEFAULT_MAX_HR})")
    args = parser.parse_args()

    if args.console:
        asyncio.run(run_console(args.name))
    else:
        run_widget(args.name, args.max_hr)


if __name__ == "__main__":
    main()