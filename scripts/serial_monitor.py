#!/usr/bin/env python3
"""
AquaBeacon RX シリアルモニター
指定秒数だけ受信してCSVに保存する

使い方:
  python scripts/serial_monitor.py --port COM4 --duration 30 --out logs/rx_001.csv
"""

import serial
import argparse
import time
from pathlib import Path
from datetime import datetime

def monitor(port: str, baud: int, duration: float, outfile: str):
    Path(outfile).parent.mkdir(parents=True, exist_ok=True)

    print(f"[monitor] ポート: {port}, {baud}bps, {duration}秒間受信")
    print(f"[monitor] 保存先: {outfile}")

    try:
        ser = serial.Serial(port, baud, timeout=1)
    except serial.SerialException as e:
        print(f"[ERROR] シリアルポートを開けません: {e}")
        print(f"  → COMポートを確認してください (現在: {port})")
        raise

    time.sleep(0.5)  # Pico起動待ち

    start = time.time()
    line_count = 0

    with open(outfile, "w", encoding="utf-8") as f:
        f.write("# AquaBeacon RX Log\n")
        f.write(f"# Captured: {datetime.now().isoformat()}\n")
        f.write(f"# Port: {port}\n")
        f.write("seq,t_sync_us,t_detect_us,tof_us,range_m,score,rx_peak,noise,status\n")

        while time.time() - start < duration:
            try:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
            except Exception:
                continue

            # コメント行はそのまま表示
            if line.startswith("#"):
                print(f"  {line}")
                f.write(line + "\n")
                f.flush()
                continue

            # CSVデータ行
            if "," in line:
                print(f"  {line}")
                f.write(line + "\n")
                f.flush()
                line_count += 1

        remaining = duration - (time.time() - start)
        elapsed = time.time() - start
        print(f"\n[monitor] 完了: {elapsed:.1f}秒, {line_count}行受信")

    ser.close()
    return line_count

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port",     default="COM4",  help="RX COMポート (例: COM4)")
    parser.add_argument("--baud",     default=115200,  type=int)
    parser.add_argument("--duration", default=30,      type=float, help="受信秒数")
    parser.add_argument("--out",      default=None,    help="出力ファイルパス")
    args = parser.parse_args()

    if args.out is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.out = f"logs/rx_{ts}.csv"

    monitor(args.port, args.baud, args.duration, args.out)
