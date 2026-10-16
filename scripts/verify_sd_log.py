#!/usr/bin/env python3
"""Wait for one RX SD write, dump that SD file, and verify its CSV shape."""

import argparse
from collections import Counter
import csv
from pathlib import Path
import time

import serial


EXPECTED_HEADER = [
    "seq", "t_sync_us", "t_detect_us", "tof_us", "range_m", "score",
    "rx_peak_pct", "noise_high_pct", "status",
]
EXPECTED_EDGE_HEADER = [
    "seq", "t_sync_us", "rise_index", "rise_us", "rise_from_sync_us",
    "region", "dropped_total",
]


def read_line(port: serial.Serial) -> str:
    return port.readline().decode("utf-8", errors="replace").strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument(
        "--expected",
        help="CSV whose measurement rows must all exist in the SD dump",
    )
    parser.add_argument(
        "--out",
        help="save the complete SD dump as a new CSV (never overwritten)",
    )
    parser.add_argument(
        "--dump-now", action="store_true",
        help="verify the current SD file without waiting for a new write",
    )
    parser.add_argument(
        "--edge", action="store_true",
        help="read and verify the AQEnnn.CSV rising-edge log",
    )
    args = parser.parse_args()

    deadline = time.monotonic() + args.timeout
    write_prefix = "# SD,EDGE_WRITE_OK," if args.edge else "# SD,WRITE_OK,"
    write_error_prefix = (
        "# SD,EDGE_WRITE_ERROR," if args.edge else "# SD,WRITE_ERROR,"
    )
    dump_begin_prefix = (
        "# SD,EDGE_DUMP_BEGIN," if args.edge else "# SD,DUMP_BEGIN,"
    )
    dump_end_prefix = "# SD,EDGE_DUMP_END," if args.edge else "# SD,DUMP_END,"
    dump_command = b"E" if args.edge else b"D"
    expected_header = EXPECTED_EDGE_HEADER if args.edge else EXPECTED_HEADER
    with serial.Serial(args.port, args.baud, timeout=0.25) as port:
        write_line = ""
        if not args.dump_now:
            while time.monotonic() < deadline:
                line = read_line(port)
                if not line:
                    continue
                if line.startswith(("# SD,", "# AquaBeacon", "# ID:")):
                    print(line)
                if line.startswith(write_prefix):
                    write_line = line
                    break
                if line.startswith(write_error_prefix):
                    raise SystemExit(f"SD write failed: {line}")
            if not write_line:
                raise SystemExit("Timed out waiting for an automatic measurement SD write")

        port.write(dump_command)
        port.flush()
        dump_deadline = time.monotonic() + 15.0
        dump_lines = []
        dump_name = ""
        while time.monotonic() < dump_deadline:
            line = read_line(port)
            if not line:
                continue
            if line.startswith(dump_begin_prefix):
                dump_name = line.rsplit(",", 1)[-1]
                print(line)
                continue
            if line.startswith(dump_end_prefix):
                print(line)
                break
            if dump_name and not line.startswith("#"):
                dump_lines.append(line)
        else:
            raise SystemExit("Timed out while reading the SD log")

    rows = list(csv.reader(dump_lines))
    if not rows or rows[0] != expected_header:
        raise SystemExit("SD CSV header is missing or incompatible")
    if len(rows) < 2 or any(len(row) != len(expected_header) for row in rows[1:]):
        raise SystemExit(
            f"SD CSV does not contain a valid {len(expected_header)}-column row"
        )

    if args.expected:
        if args.edge:
            raise SystemExit("--expected is only supported for the summary log")
        expected_path = Path(args.expected)
        with expected_path.open(encoding="utf-8-sig", newline="") as handle:
            expected_rows = [
                row for row in csv.reader(
                    line for line in handle
                    if line.strip() and not line.startswith("#")
                )
            ]
        if not expected_rows or expected_rows[0] != EXPECTED_HEADER:
            raise SystemExit("Expected CSV header is missing or incompatible")
        available = Counter(tuple(row) for row in rows[1:])
        missing = []
        for row in expected_rows[1:]:
            key = tuple(row)
            if available[key] <= 0:
                missing.append(row)
            else:
                available[key] -= 1
        if missing:
            raise SystemExit(
                f"SD dump is missing {len(missing)} expected measurement row(s)"
            )
        print(f"[PASS] SD contains all {len(expected_rows) - 1} expected row(s)")

    if args.out:
        output_path = Path(args.out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("x", encoding="utf-8-sig", newline="") as handle:
            csv.writer(handle).writerows(rows)
        print(f"[PASS] SD dump saved: {output_path}")

    print(f"[PASS] {dump_name}: {len(rows) - 1} measurement row(s)")
    print(",".join(rows[-1]))


if __name__ == "__main__":
    main()
