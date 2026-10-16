#!/usr/bin/env python3
"""Create pool-test summaries and PNG graphs from integrated SD analysis."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import re
import statistics

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties


def number(value: str) -> float:
    return float(value) if value not in ("", None) else 0.0


def file_number(name: str) -> int:
    match = re.fullmatch(r"S(\d{3})\.CSV", name, re.IGNORECASE)
    if not match:
        raise ValueError(f"unsupported SD file name: {name}")
    return int(match.group(1))


def mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def stdev(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) >= 2 else 0.0


def japanese_font() -> FontProperties:
    candidates = [
        Path("C:/Windows/Fonts/meiryo.ttc"),
        Path("C:/Windows/Fonts/YuGothM.ttc"),
    ]
    for path in candidates:
        if path.is_file():
            return FontProperties(fname=str(path))
    return FontProperties()


def summarize(
    rows: list[dict[str, str]], label: str, expected_distance_m: float
) -> dict[str, object]:
    valid = [row for row in rows if row["status"] == "VALID"]
    no_detect = [row for row in rows if row["status"] == "NO_DETECT"]
    ranges = [number(row["range_m"]) for row in valid]
    valid_adc = [number(row["adc_peak_mv"]) for row in valid]
    no_detect_adc = [number(row["adc_peak_mv"]) for row in no_detect]
    return {
        "distance_label": label,
        "expected_distance_m": expected_distance_m,
        "first_file": rows[0]["source_file"],
        "last_file": rows[-1]["source_file"],
        "measurements": len(rows),
        "valid": len(valid),
        "no_detect": len(no_detect),
        "valid_rate_pct": 100.0 * len(valid) / len(rows),
        "range_mean_m": mean(ranges),
        "range_std_m": stdev(ranges),
        "range_min_m": min(ranges, default=0.0),
        "range_max_m": max(ranges, default=0.0),
        "mean_error_m": mean(ranges) - expected_distance_m,
        "valid_adc_mean_mv": mean(valid_adc),
        "valid_adc_min_mv": min(valid_adc, default=0.0),
        "valid_adc_max_mv": max(valid_adc, default=0.0),
        "no_detect_adc_mean_mv": mean(no_detect_adc),
        "no_detect_adc_min_mv": min(no_detect_adc, default=0.0),
        "no_detect_adc_max_mv": max(no_detect_adc, default=0.0),
        "threshold_min_mv": min(number(row["threshold_mv"]) for row in rows),
        "threshold_max_mv": max(number(row["threshold_mv"]) for row in rows),
        "all_valid_score_8": all(row["score"] == "8" for row in valid),
    }


def write_summary(path: Path, summaries: list[dict[str, object]]) -> None:
    columns = list(summaries[0])
    with path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(summaries)


def write_sd_readback(path: Path, rows: list[dict[str, str]]) -> None:
    columns = [
        "source_file", "seq", "t_sync_us", "t_detect_us", "tof_us",
        "range_m", "score", "rx_peak_pct", "noise_high_pct", "status",
    ]
    with path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_measurement_results(
    path: Path, rows: list[dict[str, str]]
) -> None:
    columns = [
        "seq", "t_sync_us", "t_detect_us", "tof_us", "range_m", "score",
        "rx_peak_pct", "noise_high_pct", "status",
    ]
    with path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def distance_plot(
    path: Path,
    groups: list[tuple[str, float, list[dict[str, str]]]],
    font: FontProperties,
) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), constrained_layout=True)
    for panel_index, (axis, (label, expected, rows)) in enumerate(
        zip(axes, groups)
    ):
        valid = [row for row in rows if row["status"] == "VALID"]
        invalid = [row for row in rows if row["status"] != "VALID"]
        x_valid = [file_number(row["source_file"]) for row in valid]
        ranges = [number(row["range_m"]) for row in valid]
        x_invalid = [file_number(row["source_file"]) for row in invalid]
        avg = mean(ranges)
        std = stdev(ranges)
        axis.scatter(
            x_valid, ranges, s=42, color="#0072B2", label="VALID", zorder=3
        )
        if x_invalid:
            axis.scatter(
                x_invalid, [expected] * len(x_invalid), marker="x", s=38,
                color="#888888", label="NO_DETECT（目標距離位置に表示）",
            )
        axis.axhline(
            expected, color="#009E73", linewidth=2, linestyle="--",
            label=f"目標 {expected:.0f} m",
        )
        axis.axhline(
            avg, color="#D55E00", linewidth=1.8,
            label=f"VALID平均 {avg:.3f} m",
        )
        axis.fill_between(
            [file_number(rows[0]["source_file"]), file_number(rows[-1]["source_file"])],
            [avg - std, avg - std], [avg + std, avg + std],
            color="#D55E00", alpha=0.12, label=f"平均±1σ ({std:.3f} m)",
        )
        margin = max(0.18, abs(avg - expected) + 3 * std)
        axis.set_ylim(expected - margin, expected + margin)
        axis.set_title(f"{label}測定", fontproperties=font, fontsize=14)
        if panel_index == len(groups) - 1:
            axis.set_xlabel("SDファイル番号", fontproperties=font)
        axis.set_ylabel("測定距離 [m]", fontproperties=font)
        axis.grid(alpha=0.25)
        axis.legend(prop=font, loc="best")
    fig.suptitle("学校プール 距離測定結果", fontproperties=font, fontsize=18)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def adc_threshold_plot(
    path: Path,
    groups: list[tuple[str, float, list[dict[str, str]]]],
    font: FontProperties,
) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), constrained_layout=True)
    for panel_index, (axis, (label, _expected, rows)) in enumerate(
        zip(axes, groups)
    ):
        x = [file_number(row["source_file"]) for row in rows]
        threshold = [number(row["threshold_mv"]) for row in rows]
        valid = [row for row in rows if row["status"] == "VALID"]
        invalid = [row for row in rows if row["status"] != "VALID"]
        axis.plot(
            x, threshold, color="#CC79A7", linewidth=2, marker=".",
            label="TH_PWM使用値",
        )
        axis.scatter(
            [file_number(row["source_file"]) for row in valid],
            [number(row["adc_peak_mv"]) for row in valid],
            color="#0072B2", s=38, label="ENV_ADCピーク（VALID）", zorder=3,
        )
        axis.scatter(
            [file_number(row["source_file"]) for row in invalid],
            [number(row["adc_peak_mv"]) for row in invalid],
            color="#999999", marker="x", s=38,
            label="ENV_ADCピーク（NO_DETECT）", zorder=3,
        )
        axis.set_ylim(0, 340)
        axis.set_title(f"{label}測定", fontproperties=font, fontsize=14)
        if panel_index == len(groups) - 1:
            axis.set_xlabel("SDファイル番号", fontproperties=font)
        axis.set_ylabel("電圧 [mV]", fontproperties=font)
        axis.grid(alpha=0.25)
        axis.legend(prop=font, loc="best")
    fig.suptitle(
        "ENV_ADCピークと自動閾値の推移", fontproperties=font, fontsize=18
    )
    fig.savefig(path, dpi=180)
    plt.close(fig)


def comparison_plot(
    path: Path, summaries: list[dict[str, object]], font: FontProperties
) -> None:
    labels = [str(item["distance_label"]) for item in summaries]
    rates = [float(item["valid_rate_pct"]) for item in summaries]
    errors_cm = [100.0 * float(item["mean_error_m"]) for item in summaries]
    std_cm = [100.0 * float(item["range_std_m"]) for item in summaries]
    fig, (rate_axis, error_axis) = plt.subplots(
        1, 2, figsize=(11, 4.8), constrained_layout=True
    )
    rate_axis.bar(labels, rates, color=["#56B4E9", "#0072B2"])
    rate_axis.axhline(90, color="#D55E00", linestyle="--", label="合格基準 90%")
    rate_axis.set_ylim(0, 105)
    rate_axis.set_ylabel("VALID率 [%]", fontproperties=font)
    rate_axis.set_title("検出率", fontproperties=font)
    rate_axis.legend(prop=font)
    rate_axis.grid(axis="y", alpha=0.25)
    error_axis.bar(
        labels, errors_cm, yerr=std_cm, capsize=8,
        color=["#009E73", "#F0E442"], edgecolor="#555555",
    )
    error_axis.axhline(0, color="#333333", linewidth=1)
    error_axis.set_ylabel("平均距離誤差 [cm]（誤差棒: 1σ）", fontproperties=font)
    error_axis.set_title("距離誤差と再現性", fontproperties=font)
    error_axis.grid(axis="y", alpha=0.25)
    for axis in (rate_axis, error_axis):
        for tick in axis.get_xticklabels() + axis.get_yticklabels():
            tick.set_fontproperties(font)
    fig.suptitle("1m・5m実験比較", fontproperties=font, fontsize=18)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--one-session", type=int, required=True)
    parser.add_argument("--five-session", type=int, required=True)
    args = parser.parse_args()

    report = Path(args.report_dir)
    with Path(args.analysis).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    one_rows = [row for row in rows if int(row["session"]) == args.one_session]
    five_rows = [row for row in rows if int(row["session"]) == args.five_session]
    if not one_rows or not five_rows:
        raise SystemExit("指定されたセッションに測定行がありません")

    summaries = [
        summarize(one_rows, "1m", 1.0),
        summarize(five_rows, "5m", 5.0),
    ]
    if not (report / "距離別集計.csv").exists():
        write_summary(report / "距離別集計.csv", summaries)
    if not (report / "SD読出し.csv").exists():
        write_sd_readback(report / "SD読出し.csv", rows)
    if not (report / "1m実験結果.csv").exists():
        write_measurement_results(report / "1m実験結果.csv", one_rows)
    if not (report / "5m実験結果.csv").exists():
        write_measurement_results(report / "5m実験結果.csv", five_rows)
    groups = [("1m", 1.0, one_rows), ("5m", 5.0, five_rows)]
    font = japanese_font()
    if not (report / "距離測定結果.png").exists():
        distance_plot(report / "距離測定結果.png", groups, font)
    if not (report / "ADC_閾値推移.png").exists():
        adc_threshold_plot(report / "ADC_閾値推移.png", groups, font)
    if not (report / "1m_5m比較.png").exists():
        comparison_plot(report / "1m_5m比較.png", summaries, font)
    for item in summaries:
        print(
            f"{item['distance_label']}: valid={item['valid']}/"
            f"{item['measurements']} ({item['valid_rate_pct']:.1f}%), "
            f"mean={item['range_mean_m']:.4f}m, "
            f"std={item['range_std_m']:.4f}m"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
