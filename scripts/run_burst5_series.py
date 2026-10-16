#!/usr/bin/env python3
"""Capture a fixed number of automatic AquaBeacon 5-wave measurements."""

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


def is_csv_line(line: str, field_count: int) -> bool:
    if not line or line.startswith("#"):
        return False
    try:
        return len(next(csv.reader([line]))) == field_count
    except csv.Error:
        return False


def reader_loop(role, ser, events, events_lock, important, stop):
    while not stop.is_set():
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        elapsed = time.monotonic()
        with events_lock:
            events.write(f"{elapsed:.6f},{role},{line}\n")

        keep = (
            is_csv_line(line, 5 if role == "TX" else 9)
            or line.startswith("# ID:")
            or line.startswith("# DIAG,TX_PIN,")
            or line.startswith("# SD,")
            or line.startswith("# DETECT")
            or line.startswith("# CAPTURE")
            or line.startswith("# RAW_GPIO")
            or line.startswith("# EDGE_DUMP_END")
            or line.startswith("# POLLEDGE_END")
        )
        if keep:
            important.put((role, line))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tx-port", default="COM10")
    parser.add_argument("--rx-port", default="COM5")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--shots", type=int, default=5)
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--timeout", type=float, default=210.0)
    parser.add_argument("--out")
    args = parser.parse_args()

    if args.shots <= 0:
        raise SystemExit("--shots must be positive")
    if args.tx_port.upper() == args.rx_port.upper():
        raise SystemExit("TX and RX ports must be different")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_path = Path(args.out or f"logs/rx_burst5_series_{timestamp}.csv")
    event_path = result_path.with_name(f"{result_path.stem}_events.log")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    if result_path.exists() or event_path.exists():
        raise SystemExit("Refusing to overwrite an existing log file")

    important = queue.Queue()
    stop = threading.Event()
    events_lock = threading.Lock()
    tx_rows = []
    rx_rows = []
    sd_writes = 0
    safe_off_sequences = set()
    started = time.monotonic()

    with event_path.open("x", encoding="utf-8", newline="") as events:
        events.write("# monotonic_s,source,line\n")
        events.write(f"# captured_at,{datetime.now().isoformat()}\n")
        events.write(f"# tx_port,{args.tx_port}\n")
        events.write(f"# rx_port,{args.rx_port}\n")
        events.write(f"# requested_shots,{args.shots}\n")
        events.write(f"# configured_interval_s,{args.interval}\n")

        with serial.Serial(args.rx_port, args.baud, timeout=0.05) as rx, \
                serial.Serial(args.tx_port, args.baud, timeout=0.05) as tx:
            time.sleep(0.25)
            rx.reset_input_buffer()
            tx.reset_input_buffer()

            rx_thread = threading.Thread(
                target=reader_loop,
                args=("RX", rx, events, events_lock, important, stop),
                daemon=True,
            )
            tx_thread = threading.Thread(
                target=reader_loop,
                args=("TX", tx, events, events_lock, important, stop),
                daemon=True,
            )
            rx_thread.start()
            tx_thread.start()
            rx.write(b"?")
            tx.write(b"?")
            rx.flush()
            tx.flush()

            deadline = started + args.timeout
            while time.monotonic() < deadline:
                try:
                    role, line = important.get(timeout=0.25)
                except queue.Empty:
                    continue

                if role == "TX" and is_csv_line(line, 5):
                    row = next(csv.reader([line]))
                    if row[-1] == "TX_DONE":
                        tx_rows.append(row)
                        print(
                            f"[progress] TX {len(tx_rows)}/{args.shots}: "
                            f"seq={row[0]} t={row[1]}us"
                        )
                elif role == "TX" and line.startswith("# DIAG,TX_PIN,"):
                    fields = line.split(",")
                    if (
                        len(fields) >= 9
                        and fields[4] == "OFF"
                        and "pwm=0" in fields
                        and "override=LOW" in fields
                        and "pin_readback=0" in fields
                        and "failsafe=0" in fields
                    ):
                        safe_off_sequences.add(fields[2])
                elif role == "RX" and is_csv_line(line, 9):
                    row = next(csv.reader([line]))
                    rx_rows.append(row)
                    print(
                        f"[progress] RX {len(rx_rows)}/{args.shots}: "
                        f"seq={row[0]} ToF={row[3]}us "
                        f"range={row[4]}m status={row[8]}"
                    )
                elif role == "RX" and line.startswith("# SD,WRITE_OK,"):
                    sd_writes += 1
                    print(f"[progress] SD {sd_writes}/{args.shots}: {line}")

                if (
                    len(tx_rows) >= args.shots
                    and len(rx_rows) >= args.shots
                    and sd_writes >= args.shots
                ):
                    break
            else:
                stop.set()
                rx_thread.join(timeout=1.0)
                tx_thread.join(timeout=1.0)
                raise SystemExit(
                    "Timed out: "
                    f"TX={len(tx_rows)}, RX={len(rx_rows)}, SD={sd_writes}; "
                    f"events saved to {event_path}"
                )

            stop.set()
            rx_thread.join(timeout=1.0)
            tx_thread.join(timeout=1.0)

    tx_rows = tx_rows[:args.shots]
    rx_rows = rx_rows[:args.shots]
    if len(safe_off_sequences) < args.shots:
        raise SystemExit(
            f"TX OFF verification incomplete: {len(safe_off_sequences)}/"
            f"{args.shots}; events saved to {event_path}"
        )

    with result_path.open("x", encoding="utf-8", newline="") as handle:
        handle.write("# AquaBeacon RX automatic 5-wave series\n")
        handle.write(f"# Captured: {datetime.now().isoformat()}\n")
        handle.write(f"# TX port: {args.tx_port}\n")
        handle.write(f"# RX port: {args.rx_port}\n")
        handle.write(f"# Requested interval: {args.interval:.3f} s\n")
        writer = csv.writer(handle)
        writer.writerow(RX_HEADER)
        writer.writerows(rx_rows)

    print(f"[result] TX_DONE: {len(tx_rows)}/{args.shots}")
    print(f"[result] TX safe OFF: {len(safe_off_sequences)}/{args.shots}")
    print(f"[result] RX rows: {len(rx_rows)}/{args.shots}")
    print(f"[result] SD writes: {sd_writes}/{args.shots}")
    print(f"[result] Saved: {result_path}")
    print(f"[result] Events: {event_path}")


if __name__ == "__main__":
    main()
