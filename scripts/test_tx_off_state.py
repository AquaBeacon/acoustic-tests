#!/usr/bin/env python3
"""Send one unloaded TX trigger and log GP15 state after PWM shutdown."""

import argparse
from datetime import datetime
from pathlib import Path
import time

import serial


def collect_until(ser, lines, predicate, timeout_s):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        print(f"[{ser.port}] {line}")
        lines.append(line)
        if predicate(line):
            return line
    raise TimeoutError(f"{ser.port}: expected response was not received")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM10")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--wait-after", type=float, default=1.0)
    parser.add_argument("--confirm-unloaded-single-shot", action="store_true")
    args = parser.parse_args()

    if not args.confirm_unloaded_single_shot:
        raise SystemExit("Refusing to trigger without unloaded single-shot confirmation")

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise SystemExit(f"Refusing to overwrite existing log: {output}")

    lines = []
    with serial.Serial(args.port, args.baud, timeout=0.2) as ser:
        time.sleep(0.8)
        ser.reset_input_buffer()
        ser.write(b"?")
        ser.flush()
        collect_until(
            ser, lines, lambda line: "ID:TX;STATE=SAFE_WAIT" in line, 3.0)

        ser.reset_input_buffer()
        print("[safety] Sending exactly one unloaded trigger")
        ser.write(b"T")
        ser.flush()
        collect_until(
            ser,
            lines,
            lambda line: not line.startswith("#")
            and line.count(",") == 4
            and ",TX_" in line,
            3.0,
        )

        time.sleep(args.wait_after)
        ser.write(b"?")
        ser.flush()
        collect_until(
            ser, lines, lambda line: line.startswith("# DIAG,TX_LIVE,"), 3.0)

    with output.open("x", encoding="utf-8", newline="") as handle:
        handle.write("# AquaBeacon unloaded TX OFF-state diagnostic\n")
        handle.write(f"# Captured: {datetime.now().isoformat()}\n")
        handle.write(f"# Label: {args.label}\n")
        handle.write(f"# Port: {args.port}\n")
        handle.write(f"# Post-trigger wait: {args.wait_after} s\n")
        for line in lines:
            handle.write(line + "\n")

    print(f"[result] Saved: {output}")


if __name__ == "__main__":
    main()
