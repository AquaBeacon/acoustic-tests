#!/usr/bin/env python3
"""Dump the latest per-measurement RX_improved_mini SD file."""

import argparse
from pathlib import Path
import time

import serial


def line(port):
    return port.readline().decode("utf-8", errors="replace").strip()


def wait_armed(port, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        port.write(b"?")
        port.flush()
        query_end = time.monotonic() + 0.3
        while time.monotonic() < query_end:
            value = line(port)
            if value == "# ID:RX_MINI;STATE=ARMED":
                return
        time.sleep(0.05)
    raise RuntimeError("RX did not enter ARMED state")


def dump_latest(port, output, timeout):
    wait_armed(port)
    port.reset_input_buffer()
    port.write(b"D")
    port.flush()
    deadline = time.monotonic() + timeout
    started = False
    rows = 0
    with output.open("x", encoding="utf-8-sig", newline="") as file:
        while time.monotonic() < deadline:
            value = line(port)
            if not value:
                continue
            if value.startswith("# SD,SHOT_DUMP_BEGIN,"):
                started = True
                print(value, flush=True)
                continue
            if started and value.startswith("# SD,SHOT_DUMP_END,"):
                print(value, flush=True)
                print(f"{output.name}: {rows} lines", flush=True)
                return
            if started:
                file.write(value + "\n")
                rows += 1
    raise RuntimeError(f"Timed out dumping {output.name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--timeout", type=float, default=240.0)
    args = parser.parse_args()
    report_dir = Path(args.report_dir)
    with serial.Serial(args.port, 115200, timeout=0.1) as port:
        time.sleep(0.3)
        dump_latest(port, report_dir / "SD最新測定.csv", args.timeout)


if __name__ == "__main__":
    main()
