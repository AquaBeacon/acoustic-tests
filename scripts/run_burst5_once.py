#!/usr/bin/env python3
"""AquaBeacon 5波バーストを厳密に1回だけ実行し、RXログを保存する。"""

import argparse
import csv
from datetime import datetime
from pathlib import Path
import time

import serial


TRIGGER = b"T"
IDENTIFY = b"?"
REARM = b"R"
RX_HEADER = [
    "seq", "t_sync_us", "t_detect_us", "tof_us", "range_m",
    "score", "rx_peak", "noise", "status",
]


def read_until(ser: serial.Serial, predicate, timeout_s: float):
    deadline = time.monotonic() + timeout_s
    seen = []
    while time.monotonic() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        # Full edge data is preserved in the event log. Avoid flooding the
        # console at 115200 bps while thousands of buffered edges are dumped.
        if not line.startswith(("# EDGE,", "# POLLEDGE,")):
            print(f"[{ser.port}] {line}")
        seen.append(line)
        if predicate(line):
            return line, seen
    raise TimeoutError(f"{ser.port}: expected response not received")


def ensure_ready(ser: serial.Serial, device: str, ready_state: str):
    """Identify first; rearm only when the firmware explicitly reports DONE."""
    ser.write(IDENTIFY)
    ser.flush()
    identity_line, seen = read_until(
        ser, lambda line: f"ID:{device};STATE=" in line, 3.0)
    if f"STATE={ready_state}" in identity_line:
        return seen
    if "STATE=DONE" not in identity_line:
        raise RuntimeError(f"{ser.port}: unexpected state: {identity_line}")

    ser.write(REARM)
    ser.flush()
    _, rearm_seen = read_until(
        ser,
        lambda line: f"ID:{device};STATE={ready_state}" in line,
        3.0,
    )
    return seen + rearm_seen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tx-port", default="COM10")
    parser.add_argument("--rx-port", default="COM5")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--out")
    parser.add_argument("--rearm", action="store_true")
    parser.add_argument(
        "--confirm-single-shot",
        action="store_true",
        help="required safety acknowledgement; without it no trigger is sent",
    )
    args = parser.parse_args()

    if not args.confirm_single_shot:
        raise SystemExit("Refusing to transmit: add --confirm-single-shot")
    if args.tx_port.upper() == args.rx_port.upper():
        raise SystemExit("TX and RX ports must be different")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.out or f"logs/rx_burst5_once_{timestamp}.csv")
    diag_out = out.with_name(f"{out.stem}_events.log")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() or diag_out.exists():
        raise SystemExit("Refusing to overwrite an existing log file")

    captured_at = datetime.now().isoformat()
    with diag_out.open("x", encoding="utf-8", newline="") as handle:
        handle.write("# AquaBeacon TX/RX event log\n")
        handle.write(f"# Captured: {captured_at}\n")
        handle.write(f"# TX port: {args.tx_port}\n")
        handle.write(f"# RX port: {args.rx_port}\n")

    # RXを先に開いて確実に待受状態へ入れ、その後TXを開く。
    with serial.Serial(args.rx_port, args.baud, timeout=0.2) as rx:
        time.sleep(0.3)
        rx.reset_input_buffer()
        rx_identity = ensure_ready(rx, "RX", "ARMED")

        with serial.Serial(args.tx_port, args.baud, timeout=0.2) as tx:
            time.sleep(0.3)
            tx.reset_input_buffer()
            tx_identity = ensure_ready(tx, "TX", "AUTO_WAIT")
            tx.reset_input_buffer()
            print("[safety] Sending exactly one trigger character")
            tx.write(TRIGGER)
            tx.flush()

            tx_line, tx_seen = read_until(
                tx,
                lambda line: not line.startswith("#")
                and line.count(",") == 4
                and ",TX_" in line,
                3.0,
            )
            with diag_out.open("a", encoding="utf-8", newline="") as handle:
                for line in tx_identity + tx_seen:
                    handle.write(f"TX {line}\n")

            if not tx_line.endswith(",TX_DONE"):
                raise SystemExit(
                    f"Transmission did not complete normally; events saved to {diag_out}"
                )

            rx_line, rx_seen = read_until(
                rx,
                lambda line: not line.startswith("#") and line.count(",") == 8,
                args.timeout,
            )
            with diag_out.open("a", encoding="utf-8", newline="") as handle:
                for line in rx_identity + rx_seen:
                    handle.write(f"RX {line}\n")

    row = next(csv.reader([rx_line]))
    if len(row) != len(RX_HEADER):
        raise SystemExit(f"Unexpected RX field count: {len(row)}")

    with out.open("x", encoding="utf-8", newline="") as handle:
        handle.write("# AquaBeacon RX single-shot 5-wave burst log\n")
        handle.write(f"# Captured: {captured_at}\n")
        handle.write(f"# TX port: {args.tx_port}\n")
        handle.write(f"# RX port: {args.rx_port}\n")
        for line in tx_seen + rx_seen:
            if line.startswith("# DIAG,"):
                handle.write(line + "\n")
        writer = csv.writer(handle)
        writer.writerow(RX_HEADER)
        writer.writerow(row)

    print(f"[result] TX: {tx_line}")
    print(f"[result] RX: {rx_line}")
    print(f"[result] Saved: {out}")
    print(f"[result] Events: {diag_out}")


if __name__ == "__main__":
    main()
