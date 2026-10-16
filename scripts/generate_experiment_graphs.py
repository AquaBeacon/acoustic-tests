#!/usr/bin/env python3
"""Generate compact AquaBeacon experiment graphs from a burst5 CSV log."""

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def load_log(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    header_index = next(
        index for index, line in enumerate(lines)
        if line.startswith("seq,t_sync_us,")
    )
    measurements = list(csv.DictReader(lines[header_index:]))
    echoes = []
    for line in lines:
        if not line.startswith("# DIAG,RX_ECHO,"):
            continue
        values = {}
        for item in line.split(",")[3:]:
            key, value = item.split("=", 1)
            values[key] = int(value)
        echoes.append(values)
    return measurements, echoes


def plot_tof(measurement, output: Path):
    tof_us = int(measurement["tof_us"])
    range_m = float(measurement["range_m"])

    fig, axis = plt.subplots(figsize=(9, 3.4))
    axis.axvspan(200, 1000, color="#2a9d8f", alpha=0.18,
                 label="Valid ToF region starts at 200 us")
    axis.axvline(200, color="#2a9d8f", linewidth=1.5)
    axis.scatter([tof_us], [0], color="#e76f51", s=90, zorder=3)
    axis.annotate(
        f"Measured {tof_us} us ({range_m:.3f} m)",
        (tof_us, 0),
        xytext=(tof_us + 55, 0.12),
        arrowprops={"arrowstyle": "->", "color": "#e76f51"},
    )
    axis.set_xlim(0, 1000)
    axis.set_ylim(-0.35, 0.45)
    axis.set_yticks([])
    axis.set_xlabel("Time from SYNC [us]")
    axis.set_title("Measured ToF versus configured minimum range")
    axis.grid(axis="x", alpha=0.25)
    axis.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(output, dpi=180)
    plt.close(fig)


def plot_echoes(echoes, output: Path):
    starts = [echo["start_from_sync_us"] for echo in echoes]
    durations = [
        echo["end_from_sync_us"] - echo["start_from_sync_us"]
        for echo in echoes
    ]
    high_samples = [echo["high_samples"] for echo in echoes]

    fig, (duration_axis, sample_axis) = plt.subplots(
        2, 1, figsize=(10, 7), sharex=True
    )
    duration_axis.scatter(starts, durations, s=14, color="#457b9d", alpha=0.8)
    duration_axis.set_ylabel("Echo duration [us]")
    duration_axis.set_title("GP20 echo groups recorded after SYNC")
    duration_axis.grid(alpha=0.25)

    sample_axis.scatter(starts, high_samples, s=14, color="#e76f51", alpha=0.8)
    sample_axis.set_xlabel("Echo start from SYNC [us]")
    sample_axis.set_ylabel("High samples [count]")
    sample_axis.grid(alpha=0.25)

    fig.tight_layout()
    fig.savefig(output, dpi=180)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("output_dir")
    args = parser.parse_args()

    source = Path(args.source)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    measurements, echoes = load_log(source)
    if len(measurements) != 1:
        raise SystemExit(
            f"Expected one measurement row, found {len(measurements)}"
        )
    if not echoes:
        raise SystemExit("No RX echo diagnostics found")

    plot_tof(measurements[0], output_dir / "tof判定.png")
    plot_echoes(echoes, output_dir / "gp20エコー群.png")
    print(f"measurement_rows={len(measurements)}")
    print(f"echo_groups={len(echoes)}")


if __name__ == "__main__":
    main()
