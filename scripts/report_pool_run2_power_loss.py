#!/usr/bin/env python3
"""Create power-loss diagnostics and figures for the second pool experiment."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path
import re
import statistics

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties


JST = timezone(timedelta(hours=9))
FILE_RE = re.compile(r"S(\d{3})\.CSV", re.IGNORECASE)


def number(value: str) -> float:
    return float(value) if value not in ("", None) else 0.0


def file_number(name: str) -> int:
    match = FILE_RE.fullmatch(name)
    if not match:
        raise ValueError(name)
    return int(match.group(1))


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def japanese_font() -> FontProperties:
    for path in (
        Path("C:/Windows/Fonts/meiryo.ttc"),
        Path("C:/Windows/Fonts/YuGothM.ttc"),
    ):
        if path.is_file():
            return FontProperties(fname=str(path))
    return FontProperties()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def find_integrated_csv(report_dir: Path) -> Path:
    required = {"source_file", "session", "seq", "utc_iso", "status", "adc_peak_mv"}
    for path in report_dir.glob("*.csv"):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            header = set(next(csv.reader(handle), []))
        if required.issubset(header):
            return path
    raise FileNotFoundError("integrated analysis CSV not found")


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def session_summary(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    sessions = sorted({int(row["session"]) for row in rows})
    for session in sessions:
        group = [row for row in rows if int(row["session"]) == session]
        valid = [row for row in group if row["status"] == "VALID"]
        no_detect = [row for row in group if row["status"] == "NO_DETECT"]
        times = [parse_utc(row["utc_iso"]) for row in group]
        intervals = [
            (later - earlier).total_seconds()
            for earlier, later in zip(times, times[1:])
        ]
        peaks = [number(row["adc_peak_mv"]) for row in group]
        first_floor = next(
            (row for row in group if number(row["threshold_mv"]) == 60.0), None
        )
        result.append(
            {
                "session": session,
                "first_file": group[0]["source_file"],
                "last_file": group[-1]["source_file"],
                "files": len(group),
                "seq_first": group[0]["seq"],
                "seq_last": group[-1]["seq"],
                "utc_first": group[0]["utc_iso"],
                "utc_last": group[-1]["utc_iso"],
                "jst_first": times[0].astimezone(JST).isoformat(),
                "jst_last": times[-1].astimezone(JST).isoformat(),
                "valid": len(valid),
                "no_detect": len(no_detect),
                "valid_rate_pct": 100.0 * len(valid) / len(group),
                "adc_peak_mean_mv": statistics.fmean(peaks),
                "adc_peak_max_mv": max(peaks),
                "cmp_rises_total": sum(int(row["cmp_rises"] or 0) for row in group),
                "threshold_first_mv": group[0]["threshold_mv"],
                "threshold_last_mv": group[-1]["threshold_mv"],
                "first_threshold_60_file": first_floor["source_file"]
                if first_floor
                else "",
                "first_threshold_60_seq": first_floor["seq"] if first_floor else "",
                "sync_interval_median_s": statistics.median(intervals)
                if intervals
                else "",
                "sync_interval_max_s": max(intervals) if intervals else "",
            }
        )
    return result


def event_rows(
    rows: list[dict[str, str]], sessions: list[dict[str, object]]
) -> list[dict[str, object]]:
    first = rows[0]
    last_valid = next(row for row in reversed(rows) if row["status"] == "VALID")
    first_no_detect = next(row for row in rows if row["status"] == "NO_DETECT")
    events: list[dict[str, object]] = [
        {
            "event": "first_logged_shot",
            "source_file": first["source_file"],
            "session": first["session"],
            "seq": first["seq"],
            "utc_iso": first["utc_iso"],
            "jst_iso": parse_utc(first["utc_iso"]).astimezone(JST).isoformat(),
            "status": first["status"],
            "evidence": "実験ログ開始",
        },
        {
            "event": "last_valid_before_failure",
            "source_file": last_valid["source_file"],
            "session": last_valid["session"],
            "seq": last_valid["seq"],
            "utc_iso": last_valid["utc_iso"],
            "jst_iso": parse_utc(last_valid["utc_iso"]).astimezone(JST).isoformat(),
            "status": last_valid["status"],
            "evidence": "この時点までは音響受信あり",
        },
        {
            "event": "first_no_detect_after_gap",
            "source_file": first_no_detect["source_file"],
            "session": first_no_detect["session"],
            "seq": first_no_detect["seq"],
            "utc_iso": first_no_detect["utc_iso"],
            "jst_iso": parse_utc(first_no_detect["utc_iso"])
            .astimezone(JST)
            .isoformat(),
            "status": first_no_detect["status"],
            "evidence": "以後359回連続NO_DETECT、CMP立上り0",
        },
    ]
    for summary in sessions[1:]:
        events.append(
            {
                "event": "rx_restart",
                "source_file": summary["first_file"],
                "session": summary["session"],
                "seq": summary["seq_first"],
                "utc_iso": summary["utc_first"],
                "jst_iso": summary["jst_first"],
                "status": "NO_DETECT",
                "evidence": "seq=1、t_sync_us低下、TH_PWM=300mVへ再初期化",
            }
        )
    return events


def plot_timeline(
    path: Path, rows: list[dict[str, str]], session_rows: list[dict[str, object]]
) -> None:
    font = japanese_font()
    x = [file_number(row["source_file"]) for row in rows]
    peaks = [number(row["adc_peak_mv"]) for row in rows]
    thresholds = [number(row["threshold_mv"]) for row in rows]
    valid_x = [xv for xv, row in zip(x, rows) if row["status"] == "VALID"]
    valid_y = [
        peak for peak, row in zip(peaks, rows) if row["status"] == "VALID"
    ]
    no_x = [xv for xv, row in zip(x, rows) if row["status"] == "NO_DETECT"]
    no_y = [
        peak for peak, row in zip(peaks, rows) if row["status"] == "NO_DETECT"
    ]
    fig, axis = plt.subplots(figsize=(13.5, 5.5))
    axis.plot(x, thresholds, color="#CC79A7", linewidth=1.5, label="TH_PWM")
    axis.scatter(valid_x, valid_y, s=30, color="#0072B2", label="VALID ADC peak")
    axis.scatter(no_x, no_y, s=10, color="#999999", alpha=0.55, label="NO_DETECT ADC peak")
    for summary in session_rows[1:]:
        boundary = file_number(str(summary["first_file"]))
        axis.axvline(boundary, color="#D55E00", linewidth=0.8, alpha=0.55)
    axis.axvspan(0, 9.5, color="#009E73", alpha=0.08, label="Acoustic signal present")
    axis.set_xlabel("SD file number")
    axis.set_ylabel("Voltage (mV)")
    axis.set_title("Pool experiment 2: power-loss timeline")
    axis.set_xlim(min(x) - 2, max(x) + 2)
    axis.set_ylim(0, 330)
    axis.grid(alpha=0.22)
    axis.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def plot_valid_distance(path: Path, rows: list[dict[str, str]]) -> None:
    valid = [row for row in rows if row["status"] == "VALID"]
    x = [file_number(row["source_file"]) for row in valid]
    ranges = [number(row["range_m"]) for row in valid]
    font = japanese_font()
    fig, axis = plt.subplots(figsize=(9.0, 4.8))
    colors = ["#D55E00" if value >= 1.3 else "#0072B2" for value in ranges]
    axis.scatter(x, ranges, color=colors, s=55)
    axis.axhline(1.0, color="#009E73", linestyle="--", label="1 m reference")
    axis.axhspan(0.9, 1.1, color="#009E73", alpha=0.08, label="1 m ± 0.1 m")
    axis.set_xticks(x)
    axis.set_xlabel("SD file number")
    axis.set_ylabel("Measured range (m)")
    axis.set_title("VALID measurements before acoustic power loss")
    axis.grid(alpha=0.22)
    axis.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report_dir", type=Path)
    args = parser.parse_args()
    report_dir = args.report_dir.resolve()
    rows = read_rows(find_integrated_csv(report_dir))
    sessions = session_summary(rows)
    events = event_rows(rows, sessions)

    assert len(rows) == 369
    assert len(sessions) == 6
    assert sum(row["valid"] for row in sessions) == 10
    assert all(row["status"] == "VALID" for row in rows[:10])
    assert all(row["status"] == "NO_DETECT" for row in rows[10:])
    assert sum(int(row["cmp_rises"] or 0) for row in rows[10:]) == 0

    write_csv(report_dir / "セッション別集計.csv", sessions)
    write_csv(report_dir / "電源断イベント.csv", events)
    plot_timeline(report_dir / "電源断タイムライン.png", rows, sessions)
    plot_valid_distance(report_dir / "電源断前距離結果.png", rows)
    print(f"rows={len(rows)} sessions={len(sessions)}")
    print("valid_before_loss=10 no_detect_after_loss=359")
    print(
        "failure_window_jst="
        f"{events[1]['jst_iso']}..{events[2]['jst_iso']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
