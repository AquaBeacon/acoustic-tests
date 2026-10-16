#!/usr/bin/env python3
"""Capture an automatic RX_improved_mini TX/RX measurement series."""

import argparse
import csv
from datetime import datetime
from pathlib import Path
import queue
import threading
import time

import serial


RX_HEADER = [
    "seq", "t_sync_us", "t_detect_us", "tof_us", "range_m",
    "score", "rx_peak_pct", "noise_high_pct", "status",
]
TX_HEADER = ["seq", "t_tx_us", "wave_count", "burst_us", "status"]


def csv_row(line: str, columns: int):
    if not line or line.startswith("#"):
        return None
    try:
        row = next(csv.reader([line]))
    except csv.Error:
        return None
    return row if len(row) == columns else None


def reader(role, port, output, lock, events, stop):
    while not stop.is_set():
        raw = port.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        with lock:
            output.writerow([
                datetime.now().isoformat(timespec="milliseconds"), role, line
            ])
        events.put((role, line))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tx-port", default="COM10")
    parser.add_argument("--rx-port", default="COM5")
    parser.add_argument("--shots", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=75.0)
    parser.add_argument("--reports-dir", default="reports")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = Path(args.reports_dir) / f"{stamp}_rx_mini_water_few_cm"
    report_dir.mkdir(parents=True, exist_ok=False)
    event_path = report_dir / "元ログ.csv"
    result_path = report_dir / "実験結果.csv"
    tx_path = report_dir / "TX実行結果.csv"

    events = queue.Queue()
    stop = threading.Event()
    lock = threading.Lock()
    rx_rows = []
    tx_rows = []

    with event_path.open("x", encoding="utf-8-sig", newline="") as event_file:
        event_writer = csv.writer(event_file)
        event_writer.writerow(["timestamp", "source", "line"])
        with serial.Serial(args.rx_port, 115200, timeout=0.05) as rx, \
                serial.Serial(args.tx_port, 115200, timeout=0.05) as tx:
            time.sleep(0.3)
            rx.reset_input_buffer()
            tx.reset_input_buffer()
            rx_thread = threading.Thread(
                target=reader,
                args=("RX", rx, event_writer, lock, events, stop),
                daemon=True,
            )
            tx_thread = threading.Thread(
                target=reader,
                args=("TX", tx, event_writer, lock, events, stop),
                daemon=True,
            )
            rx_thread.start()
            tx_thread.start()
            rx.write(b"?")
            tx.write(b"?")
            rx.flush()
            tx.flush()

            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                try:
                    role, line = events.get(timeout=0.25)
                except queue.Empty:
                    continue
                row = csv_row(line, 9 if role == "RX" else 5)
                if row is None:
                    continue
                if role == "RX":
                    rx_rows.append(row)
                    print(
                        f"RX {len(rx_rows)}/{args.shots}: "
                        f"seq={row[0]} tof={row[3]}us "
                        f"range={row[4]}m status={row[8]}",
                        flush=True,
                    )
                elif row[-1] == "TX_DONE":
                    tx_rows.append(row)
                    print(
                        f"TX {len(tx_rows)}/{args.shots}: seq={row[0]}",
                        flush=True,
                    )
                if len(rx_rows) >= args.shots and len(tx_rows) >= args.shots:
                    break
            stop.set()
            rx_thread.join(timeout=1.0)
            tx_thread.join(timeout=1.0)

    with result_path.open("x", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(RX_HEADER)
        writer.writerows(rx_rows[:args.shots])
    with tx_path.open("x", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(TX_HEADER)
        writer.writerows(tx_rows[:args.shots])

    print(f"REPORT_DIR={report_dir.resolve()}")
    print(f"RX_ROWS={len(rx_rows)} TX_ROWS={len(tx_rows)}")
    if len(rx_rows) < args.shots or len(tx_rows) < args.shots:
        raise SystemExit("Timed out before the requested series completed")


if __name__ == "__main__":
    main()
