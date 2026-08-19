# HeartRateWidget

A small, floating, always-on-top desktop widget that shows your live heart rate from any Bluetooth LE heart rate device (Fitbit Air, chest straps, etc.).

![Widget Preview](preview.png)   ← optional screenshot

## Features
- Compact floating widget (no title bar)
- Drag to move, resize, minimize, or close
- Color-coded zones (Light / Moderate / Vigorous / Peak)
- Automatic unpair of stale Bluetooth connections on Windows
- Works with any standard BLE Heart Rate device

## Download (Windows)
1. Go to the [Releases](https://github.com/LordPerkyy/HeartRateWidget/releases) page
2. Download the latest `HeartRateWidget.exe`
3. Run it (Windows may show a SmartScreen warning the first time — click "More info" → "Run anyway")

## Run from source
```bash
pip install bleak
python widget_heart_rate_6.0.py
