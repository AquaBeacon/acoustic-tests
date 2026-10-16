#!/usr/bin/env python3
"""Analyze all AquaBeacon per-shot SD CSV files without external packages.

The script never modifies source files.  It creates a new timestamped report
directory containing raw tables, per-shot analyses, integrity checks, Markdown
summaries, and SVG plots.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
import math
from pathlib import Path
import re
import statistics
import sys
from typing import Iterable


SUMMARY_COLUMNS = [
    "seq", "t_sync_us", "t_detect_us", "tof_us", "range_m", "score",
    "rx_peak_pct", "noise_high_pct", "status",
]
RECORD_HEADER = [
    "record_type", "index", "t_from_sync_us", "env_adc_raw", "env_adc_mv",
    "cmp_out", "threshold_mv", "tof_us", "range_m", "score", "rx_peak_pct",
    "noise_high_pct", "status",
]
def as_int(value: object, default: int | None = None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def as_float(value: object, default: float | None = None) -> float | None:
    try:
        number = float(str(value).strip())
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return statistics.fmean(items) if items else None


def median(values: Iterable[float]) -> float | None:
    items = list(values)
    return statistics.median(items) if items else None


def sample_std(values: Iterable[float]) -> float | None:
    items = list(values)
    return statistics.stdev(items) if len(items) >= 2 else (0.0 if items else None)


def fmt(value: object, digits: int = 3) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def parse_utc(value: str) -> datetime | None:
    if not value:
        return None


def load_config(path: Path) -> dict[str, float]:
    """Read numeric #define values needed by the analyzer."""
    wanted = {
        "SOUND_SPEED_MPS", "MIN_RANGE_M", "MAX_TOF_US", "SCORE_VALID",
        "RX_MINI_SD_FILE_WARN_BYTES",
    }
    values: dict[str, float] = {}
    pattern = re.compile(r"^\s*#define\s+([A-Z0-9_]+)\s+([0-9.]+)(?:[uUlLfF]*)\b")
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.match(line)
        if match and match.group(1) in wanted:
            values[match.group(1)] = float(match.group(2))
    missing = wanted - values.keys()
    if missing:
        raise ValueError(f"config values are missing: {', '.join(sorted(missing))}")
    return values
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class Shot:
    path: Path
    metadata: dict[str, str]
    summary: dict[str, str]
    adc: list[dict[str, str]]
    edges: list[dict[str, str]]
    raw_lines: list[str]
    session: int = 1
    integrity_errors: list[str] = field(default_factory=list)
    integrity_warnings: list[str] = field(default_factory=list)
    metrics: dict[str, object] = field(default_factory=dict)


def parse_shot(path: Path) -> Shot:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    raw_lines = text.splitlines()
    metadata: dict[str, str] = {}
    csv_lines: list[str] = []
    for line in raw_lines:
        if line.startswith("#"):
            body = line[1:].strip()
            if "," in body:
                key, value = body.split(",", 1)
                metadata[key.strip()] = value.strip()
        elif line.strip():
            csv_lines.append(line)

    errors: list[str] = []
    warnings: list[str] = []
    if not csv_lines:
        return Shot(path, metadata, {}, [], [], raw_lines,
                    integrity_errors=["CSV records are missing"])

    reader = csv.DictReader(csv_lines)
    if reader.fieldnames != RECORD_HEADER:
        errors.append("record header is incompatible")
    records = list(reader)
    summaries = [row for row in records if row.get("record_type") == "SUMMARY"]
    adc = [row for row in records if row.get("record_type") == "ADC"]
    edges = [row for row in records if row.get("record_type") == "EDGE"]
    if len(summaries) != 1:
        errors.append(f"SUMMARY count={len(summaries)} (expected 1)")
    if not adc:
        warnings.append("ADC samples are missing")
    return Shot(
        path, metadata, summaries[0] if summaries else {}, adc, edges,
        raw_lines, integrity_errors=errors, integrity_warnings=warnings,
    )


def calculate_metrics(shot: Shot, config: dict[str, float]) -> None:
    meta = shot.metadata
    summary = shot.summary
    adc_t = [as_int(row.get("t_from_sync_us")) for row in shot.adc]
    adc_mv = [as_float(row.get("env_adc_mv")) for row in shot.adc]
    adc_pairs = [
        (t, value) for t, value in zip(adc_t, adc_mv)
        if t is not None and value is not None
    ]
    edge_pairs = [
        (as_int(row.get("t_from_sync_us")), as_int(row.get("cmp_out")))
        for row in shot.edges
    ]
    edge_pairs = [
        (t, level) for t, level in edge_pairs if t is not None and level is not None
    ]
    rises = [t for t, level in edge_pairs if level == 1]
    min_tof_us = round(
        config["MIN_RANGE_M"] / config["SOUND_SPEED_MPS"] * 1_000_000
    )
    eligible_rises = [t for t in rises if t >= min_tof_us]
    deltas = [
        adc_pairs[i][0] - adc_pairs[i - 1][0]
        for i in range(1, len(adc_pairs))
        if adc_pairs[i][0] >= adc_pairs[i - 1][0]
    ]
    peak_pair = max(adc_pairs, key=lambda pair: pair[1]) if adc_pairs else (None, None)
    early_values = [value for t, value in adc_pairs if t < 200]

    seq = as_int(meta.get("seq"), as_int(summary.get("seq"), -1))
    threshold = as_int(meta.get("threshold_mv"), as_int(summary.get("threshold_mv")))
    threshold_next = as_int(meta.get("threshold_next_mv"))
    status = summary.get("status", "")
    tof = as_int(summary.get("tof_us"))
    range_m = as_float(summary.get("range_m"))
    expected_samples = as_int(meta.get("capture_samples"))
    expected_edges = as_int(meta.get("cmp_edges_written"))

    if expected_samples is not None and expected_samples != len(shot.adc):
        shot.integrity_errors.append(
            f"ADC rows={len(shot.adc)} metadata={expected_samples}"
        )
    if expected_edges is not None and expected_edges != len(shot.edges):
        shot.integrity_errors.append(
            f"EDGE rows={len(shot.edges)} metadata={expected_edges}"
        )
    if (as_int(meta.get("capture_dropped"), 0) or 0) > 0:
        shot.integrity_errors.append("ADC capture_dropped is nonzero")
    if (as_int(meta.get("cmp_edges_omitted"), 0) or 0) > 0:
        shot.integrity_errors.append("CMP edges were omitted")
    if shot.path.stat().st_size > config["RX_MINI_SD_FILE_WARN_BYTES"]:
        shot.integrity_warnings.append(
            f"file exceeds configured warning size "
            f"{int(config['RX_MINI_SD_FILE_WARN_BYTES'])} bytes"
        )
    if tof is not None and status == "VALID" and eligible_rises:
        if abs(tof - eligible_rises[0]) > 5:
            shot.integrity_warnings.append(
                f"ToF differs from first eligible CMP rise by "
                f"{abs(tof - eligible_rises[0])} us"
            )

    shot.metrics = {
        "source_file": shot.path.name,
        "source_path": str(shot.path.resolve()),
        "session": shot.session,
        "seq": seq,
        "utc_iso": meta.get("utc_iso", ""),
        "utc_valid": as_int(meta.get("utc_valid"), 0),
        "time_source": meta.get("time_source", ""),
        "gps_rx_pin": meta.get("gps_rx_pin", ""),
        "t_sync_us": as_int(meta.get("t_sync_us"), as_int(summary.get("t_sync_us"))),
        "t_detect_us": as_int(meta.get("t_detect_us"), as_int(summary.get("t_detect_us"))),
        "tof_us": tof,
        "range_m": range_m,
        "score": as_int(summary.get("score")),
        "rx_peak_pct": as_int(summary.get("rx_peak_pct")),
        "noise_high_pct": as_int(summary.get("noise_high_pct")),
        "status": status,
        "threshold_mv": threshold,
        "threshold_auto": as_int(meta.get("threshold_auto")),
        "threshold_next_mv": threshold_next,
        "threshold_next_source": "metadata" if threshold_next is not None else "unknown",
        "threshold_delta_mv": (
            threshold_next - threshold
            if threshold is not None and threshold_next is not None else None
        ),
        "adc_rows": len(shot.adc),
        "adc_metadata_rows": expected_samples,
        "adc_dropped": as_int(meta.get("capture_dropped"), 0),
        "adc_min_mv": min((value for _, value in adc_pairs), default=None),
        "adc_mean_mv": mean(value for _, value in adc_pairs),
        "adc_median_mv": median(value for _, value in adc_pairs),
        "adc_peak_mv": peak_pair[1],
        "adc_peak_time_us": peak_pair[0],
        "adc_early_mean_mv": mean(early_values),
        "adc_sample_interval_median_us": median(deltas),
        "adc_sample_interval_max_us": max(deltas, default=None),
        "cmp_edge_rows": len(shot.edges),
        "cmp_metadata_rows": expected_edges,
        "cmp_edges_omitted": as_int(meta.get("cmp_edges_omitted"), 0),
        "cmp_rises": len(rises),
        "cmp_early_rises": sum(t < 200 for t in rises),
        "cmp_first_rise_us": min(rises, default=None),
        "cmp_first_eligible_rise_us": min(eligible_rises, default=None),
        "capture_duration_us": as_int(meta.get("capture_duration_us")),
        "max_range_m": as_float(meta.get("max_range_m")),
        "file_size_bytes": shot.path.stat().st_size,
        "integrity": "PASS" if not shot.integrity_errors else "FAIL",
        "integrity_errors": "; ".join(shot.integrity_errors),
        "integrity_warnings": "; ".join(shot.integrity_warnings),
    }


def shot_sort_key(path: Path) -> tuple[int, int | str]:
    match = re.fullmatch(r"S(\d{3})\.CSV", path.name, re.IGNORECASE)
    return (0, int(match.group(1))) if match else (1, path.name.lower())


def discover_files(inputs: list[str], sd_root: str, pattern: str) -> list[Path]:
    found: list[Path] = []
    if inputs:
        for item in inputs:
            path = Path(item)
            if any(char in item for char in "*?[]"):
                found.extend(Path().glob(item))
            elif path.is_dir():
                found.extend(path.glob(pattern))
            elif path.is_file():
                found.append(path)
            else:
                raise FileNotFoundError(item)
    else:
        root = Path(sd_root)
        if not root.is_dir():
            raise FileNotFoundError(sd_root)
        found.extend(root.glob(pattern))
    unique = {str(path.resolve()).lower(): path.resolve() for path in found}
    return sorted(unique.values(), key=shot_sort_key)


def assign_sessions(shots: list[Shot]) -> None:
    session = 1
    previous_seq: int | None = None
    for shot in shots:
        seq = as_int(shot.metadata.get("seq"), as_int(shot.summary.get("seq")))
        if previous_seq is not None and seq is not None and seq <= previous_seq:
            session += 1
        shot.session = session
        previous_seq = seq


def write_csv(path: Path, columns: list[str], rows: Iterable[dict[str, object]]) -> None:
    with path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: fmt(row.get(key)) for key in columns})


def svg_polyline(
    points: list[tuple[float, float]], x0: float, y0: float,
    width: float, height: float, y_min: float, y_max: float,
) -> str:
    if not points or y_max <= y_min:
        return ""
    x_min, x_max = min(x for x, _ in points), max(x for x, _ in points)
    x_span = max(1.0, x_max - x_min)
    coords = []
    for x, y in points:
        px = x0 + (x - x_min) / x_span * width
        py = y0 + height - (y - y_min) / (y_max - y_min) * height
        coords.append(f"{px:.1f},{py:.1f}")
    return " ".join(coords)


def create_trend_svg(path: Path, shots: list[Shot]) -> None:
    rows = [shot.metrics for shot in shots]
    width, height = 1400, 900
    left, plot_width = 100, 1220
    panels = [
        ("Measured range", "m", "range_m", "#0072b2"),
        ("ENV ADC peak", "mV", "adc_peak_mv", "#d55e00"),
        ("TH_PWM used / next", "mV", "threshold_mv", "#009e73"),
    ]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#222}.grid{stroke:#ddd;stroke-width:1}'
        '.axis{stroke:#333;stroke-width:1.5}.line{fill:none;stroke-width:2.5}</style>',
        '<text x="700" y="32" text-anchor="middle" font-size="22">'
        'AquaBeacon onsite analysis</text>',
    ]
    for panel_index, (title, unit, key, color) in enumerate(panels):
        top = 70 + panel_index * 270
        panel_height = 200
        indexed = [
            (float(i), float(row[key])) for i, row in enumerate(rows, 1)
            if isinstance(row.get(key), (int, float))
            and (key != "range_m" or row.get("status") == "VALID")
        ]
        extra = []
        if key == "threshold_mv":
            extra = [
                (float(i), float(row["threshold_next_mv"]))
                for i, row in enumerate(rows, 1)
                if isinstance(row.get("threshold_next_mv"), (int, float))
            ]
        all_values = [value for _, value in indexed + extra]
        y_min = min(all_values, default=0.0)
        y_max = max(all_values, default=1.0)
        pad = max((y_max - y_min) * 0.1, 1.0)
        y_min = max(0.0, y_min - pad)
        y_max += pad
        parts.append(f'<text x="{left}" y="{top - 12}" font-size="18">{escape(title)} [{unit}]</text>')
        for grid in range(5):
            y = top + grid * panel_height / 4
            label = y_max - grid * (y_max - y_min) / 4
            parts.append(f'<line class="grid" x1="{left}" y1="{y}" x2="{left + plot_width}" y2="{y}"/>')
            parts.append(f'<text x="{left - 10}" y="{y + 5}" text-anchor="end" font-size="12">{label:.2f}</text>')
        parts.append(f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{top + panel_height}"/>')
        parts.append(f'<line class="axis" x1="{left}" y1="{top + panel_height}" x2="{left + plot_width}" y2="{top + panel_height}"/>')
        coords = svg_polyline(indexed, left, top, plot_width, panel_height, y_min, y_max)
        if coords:
            parts.append(f'<polyline class="line" stroke="{color}" points="{coords}"/>')
        if extra:
            coords = svg_polyline(extra, left, top, plot_width, panel_height, y_min, y_max)
            parts.append(f'<polyline class="line" stroke="#cc79a7" stroke-dasharray="8 5" points="{coords}"/>')
            parts.append(f'<text x="{left + plot_width - 170}" y="{top + 18}" font-size="12">solid: used / dashed: next</text>')
    parts.append(f'<text x="700" y="880" text-anchor="middle" font-size="14">Measurement order (all sessions)</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def create_adc_svg(path: Path, shots: list[Shot]) -> None:
    candidates: list[Shot] = []
    for shot in [shots[0], shots[-1], max(shots, key=lambda s: as_float(s.metrics.get("adc_peak_mv"), -1) or -1)]:
        if shot not in candidates:
            candidates.append(shot)
    no_detect = next((shot for shot in shots if shot.metrics.get("status") == "NO_DETECT"), None)
    if no_detect and no_detect not in candidates:
        candidates.append(no_detect)
    width, height = 1400, 650
    left, top, plot_width, plot_height = 100, 70, 1220, 500
    colors = ["#0072b2", "#d55e00", "#009e73", "#cc79a7"]
    all_pairs: list[list[tuple[float, float]]] = []
    for shot in candidates:
        pairs = [
            (float(t), float(value))
            for row in shot.adc
            if (t := as_int(row.get("t_from_sync_us"))) is not None
            and (value := as_float(row.get("env_adc_mv"))) is not None
        ]
        all_pairs.append(pairs)
    all_values = [value for pairs in all_pairs for _, value in pairs]
    y_max = max(400.0, max(all_values, default=1.0) * 1.05)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#222}.grid{stroke:#ddd;stroke-width:1}'
        '.axis{stroke:#333;stroke-width:1.5}.line{fill:none;stroke-width:1.5}</style>',
        '<text x="700" y="32" text-anchor="middle" font-size="22">Representative ENV_ADC waveforms</text>',
    ]
    for grid in range(6):
        y = top + grid * plot_height / 5
        label = y_max * (1 - grid / 5)
        parts.append(f'<line class="grid" x1="{left}" y1="{y}" x2="{left + plot_width}" y2="{y}"/>')
        parts.append(f'<text x="{left - 10}" y="{y + 5}" text-anchor="end" font-size="12">{label:.0f}</text>')
    parts.append(f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}"/>')
    parts.append(f'<line class="axis" x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}"/>')
    for index, (shot, pairs) in enumerate(zip(candidates, all_pairs)):
        coords = svg_polyline(pairs, left, top, plot_width, plot_height, 0.0, y_max)
        color = colors[index]
        if coords:
            parts.append(f'<polyline class="line" stroke="{color}" points="{coords}"/>')
        label = f'{shot.path.name} seq={shot.metrics.get("seq")} {shot.metrics.get("status")}'
        parts.append(f'<line x1="{left + 20}" y1="{top + 20 + index * 22}" x2="{left + 55}" y2="{top + 20 + index * 22}" stroke="{color}" stroke-width="3"/>')
        parts.append(f'<text x="{left + 65}" y="{top + 25 + index * 22}" font-size="13">{escape(label)}</text>')
    parts.append('<text x="32" y="330" transform="rotate(-90 32 330)" text-anchor="middle" font-size="14">ENV_ADC [mV]</text>')
    parts.append('<text x="700" y="625" text-anchor="middle" font-size="14">Time from SYNC [us]</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def report_text(
    shots: list[Shot], source_description: str, config: dict[str, float]
) -> tuple[str, dict[str, object]]:
    rows = [shot.metrics for shot in shots]
    valid = [row for row in rows if row.get("status") == "VALID"]
    valid_ranges = [float(row["range_m"]) for row in valid if row.get("range_m") is not None]
    thresholds = [int(row["threshold_mv"]) for row in rows if row.get("threshold_mv") is not None]
    adc_peaks = [float(row["adc_peak_mv"]) for row in rows if row.get("adc_peak_mv") is not None]
    integrity_pass = sum(row.get("integrity") == "PASS" for row in rows)
    utc_valid = sum(row.get("utc_valid") == 1 for row in rows)
    valid_rate = 100.0 * len(valid) / len(rows)
    min_tof_us = round(
        config["MIN_RANGE_M"] / config["SOUND_SPEED_MPS"] * 1_000_000
    )
    max_tof_us = round(config["MAX_TOF_US"])
    valid_score = round(config["SCORE_VALID"])
    pass_criteria = {
        "10測定以上": len(rows) >= 10,
        "VALID率90%以上": valid_rate >= 90.0,
        f"VALIDのscoreが全て{valid_score}以上": (
            bool(valid) and all((row.get("score") or 0) >= valid_score for row in valid)
        ),
        f"VALIDのToFが{min_tof_us}〜{max_tof_us}us": (
            bool(valid)
            and all(min_tof_us <= (row.get("tof_us") or -1) <= max_tof_us for row in valid)
        ),
        "距離標準偏差10cm以下": bool(valid_ranges) and (sample_std(valid_ranges) or 0.0) <= 0.1,
        "全ファイル整合性PASS": integrity_pass == len(rows),
    }
    overall = all(pass_criteria.values())
    stats = {
        "files": len(rows),
        "sessions": max((shot.session for shot in shots), default=0),
        "valid": len(valid),
        "valid_rate": valid_rate,
        "range_mean": mean(valid_ranges),
        "range_std": sample_std(valid_ranges),
        "range_min": min(valid_ranges, default=None),
        "range_max": max(valid_ranges, default=None),
        "threshold_min": min(thresholds, default=None),
        "threshold_max": max(thresholds, default=None),
        "adc_peak_mean": mean(adc_peaks),
        "adc_peak_min": min(adc_peaks, default=None),
        "adc_peak_max": max(adc_peaks, default=None),
        "integrity_pass": integrity_pass,
        "utc_valid": utc_valid,
        "overall": overall,
    }
    criteria_lines = "\n".join(
        f"- {'PASS' if passed else 'FAIL'}: {name}" for name, passed in pass_criteria.items()
    )
    warning_lines = [
        f"- `{shot.path.name}`: {message}"
        for shot in shots
        for message in shot.integrity_errors + shot.integrity_warnings
    ]
    if not warning_lines:
        warning_lines = ["- なし"]
    text = f"""# SD全データ 現場解析レポート

## 入力

- 解析日時: {datetime.now().astimezone().isoformat(timespec="seconds")}
- 入力: {source_description}
- 測定ファイル数: {len(rows)}
- セッション数: {stats["sessions"]}

## 距離測定結果

- VALID: {len(valid)}/{len(rows)} ({valid_rate:.1f}%)
- 距離平均: {fmt(stats["range_mean"], 4)} m
- 距離標準偏差: {fmt(stats["range_std"], 4)} m
- 距離範囲: {fmt(stats["range_min"], 4)}〜{fmt(stats["range_max"], 4)} m

## ADC読み取り結果

- ENV_ADCピーク平均: {fmt(stats["adc_peak_mean"], 1)} mV
- ENV_ADCピーク範囲: {fmt(stats["adc_peak_min"], 1)}〜{fmt(stats["adc_peak_max"], 1)} mV
- 詳細: `ADC解析.csv`、全サンプル: `ADC全データ.csv`

## 閾値設定結果

- TH_PWM使用値範囲: {fmt(stats["threshold_min"], 0)}〜{fmt(stats["threshold_max"], 0)} mV
- 使用値・次回値・変化量: `閾値解析.csv`

## CMP_OUT・保存健全性

- 整合性PASS: {integrity_pass}/{len(rows)}
- UTC有効: {utc_valid}/{len(rows)}
- CMP_OUT全エッジ: `CMP_OUT全エッジ.csv`

## 合格基準

{criteria_lines}

- 総合判定: {"PASS" if overall else "FAIL"}

## 異常・注意

{chr(10).join(warning_lines)}

## 出力の使い分け

- `元ログ.csv`: 原文をファイル名・行番号付きで保存
- `入力ファイル一覧.csv`: 短いファイル名と元の絶対パスの対応
- `実験結果.csv`: 既存9列形式を維持した距離結果
- `測定統合解析.csv`: 1測定1行の統合指標
- `ADC全データ.csv` / `ADC解析.csv`: 生ADCと測定単位の統計
- `閾値解析.csv`: 自動閾値の使用値・次回値・変化
- `CMP_OUT全エッジ.csv` / `CMP_OUT解析.csv`: 生エッジと測定単位の統計
- `SD読出し結果.csv`: 行数、ドロップ、欠落、ファイル容量
"""
    return text, stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AquaBeacon SDカードの全Sxxx.CSVを現場で一括解析します"
    )
    parser.add_argument("--sd-root", default=r"D:\\", help="SDカードのルート")
    parser.add_argument("--pattern", default="S[0-9][0-9][0-9].CSV")
    parser.add_argument(
        "--input", action="append", default=[],
        help="単一CSVまたはフォルダ（複数回指定可、指定時は--sd-rootより優先）",
    )
    parser.add_argument("--output-root", default="reports")
    parser.add_argument("--report-dir", help="新規作成する出力フォルダの明示指定")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parents[1] / "common" / "aquabeacon_config.h"),
        help="判定値を読み取るaquabeacon_config.h",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="合格基準を満たさない場合に終了コード2を返す",
    )
    args = parser.parse_args()

    try:
        config = load_config(Path(args.config))
        files = discover_files(args.input, args.sd_root, args.pattern)
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"[ERROR] 入力を開けません: {exc}", file=sys.stderr)
        return 1
    if not files:
        print("[ERROR] 解析対象のCSVがありません", file=sys.stderr)
        return 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = (
        Path(args.report_dir)
        if args.report_dir
        else Path(args.output_root) / f"{timestamp}_sd_all_data_analysis"
    )
    try:
        report_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print(f"[ERROR] 出力先は既に存在します: {report_dir}", file=sys.stderr)
        return 1

    shots: list[Shot] = []
    parse_failures: list[str] = []
    for path in files:
        try:
            shots.append(parse_shot(path))
        except (OSError, csv.Error) as exc:
            parse_failures.append(f"{path}: {exc}")
    if not shots:
        print("[ERROR] 読み取れる測定ファイルがありません", file=sys.stderr)
        return 1

    assign_sessions(shots)
    for shot in shots:
        calculate_metrics(shot, config)
    # Infer the effective next threshold from the next shot when old firmware did
    # not write threshold_next_mv.
    for current, following in zip(shots, shots[1:]):
        current_seq = current.metrics.get("seq")
        following_seq = following.metrics.get("seq")
        if (
            current.session == following.session
            and isinstance(current_seq, int)
            and following_seq == current_seq + 1
            and current.metrics.get("threshold_next_mv") is None
            and following.metrics.get("threshold_mv") is not None
        ):
            current.metrics["threshold_next_mv"] = following.metrics["threshold_mv"]
            current.metrics["threshold_next_source"] = "next_file"
            threshold = current.metrics.get("threshold_mv")
            current.metrics["threshold_delta_mv"] = (
                following.metrics["threshold_mv"] - threshold
                if threshold is not None else None
            )
    for shot in shots:
        delta = shot.metrics.get("threshold_delta_mv")
        shot.metrics["threshold_action"] = (
            "RAISE" if isinstance(delta, (int, float)) and delta > 0
            else "LOWER" if isinstance(delta, (int, float)) and delta < 0
            else "HOLD" if delta == 0
            else "UNKNOWN"
        )

    raw_rows = (
        {
            "source_file": shot.path.name,
            "session": shot.session,
            "seq": shot.metrics.get("seq"),
            "line_number": line_number,
            "raw_line": line,
        }
        for shot in shots
        for line_number, line in enumerate(shot.raw_lines, 1)
    )
    write_csv(
        report_dir / "元ログ.csv",
        ["source_file", "session", "seq", "line_number", "raw_line"],
        raw_rows,
    )
    write_csv(
        report_dir / "入力ファイル一覧.csv",
        ["source_file", "source_path", "session", "seq", "file_size_bytes"],
        (
            {
                "source_file": shot.path.name,
                "source_path": str(shot.path.resolve()),
                "session": shot.session,
                "seq": shot.metrics.get("seq"),
                "file_size_bytes": shot.path.stat().st_size,
            }
            for shot in shots
        ),
    )
    write_csv(
        report_dir / "実験結果.csv",
        SUMMARY_COLUMNS,
        (
            {
                "seq": shot.metrics.get("seq"),
                "t_sync_us": shot.metrics.get("t_sync_us"),
                "t_detect_us": shot.metrics.get("t_detect_us"),
                "tof_us": shot.metrics.get("tof_us"),
                "range_m": shot.metrics.get("range_m"),
                "score": shot.metrics.get("score"),
                "rx_peak_pct": shot.metrics.get("rx_peak_pct"),
                "noise_high_pct": shot.metrics.get("noise_high_pct"),
                "status": shot.metrics.get("status"),
            }
            for shot in shots
        ),
    )
    integrated_columns = list(shots[0].metrics)
    write_csv(report_dir / "測定統合解析.csv", integrated_columns, (s.metrics for s in shots))
    adc_columns = [
        "source_file", "session", "seq", "index", "t_from_sync_us",
        "env_adc_raw", "env_adc_mv", "cmp_out", "threshold_mv",
    ]
    write_csv(
        report_dir / "ADC全データ.csv",
        adc_columns,
        (
            {
                "source_file": shot.path.name,
                "session": shot.session,
                "seq": shot.metrics.get("seq"),
                **row,
            }
            for shot in shots for row in shot.adc
        ),
    )
    adc_summary_columns = [
        "source_file", "session", "seq", "status", "adc_rows", "adc_metadata_rows",
        "adc_dropped", "adc_min_mv", "adc_mean_mv", "adc_median_mv",
        "adc_peak_mv", "adc_peak_time_us", "adc_early_mean_mv",
        "adc_sample_interval_median_us", "adc_sample_interval_max_us",
    ]
    write_csv(report_dir / "ADC解析.csv", adc_summary_columns, (s.metrics for s in shots))
    threshold_columns = [
        "source_file", "session", "seq", "status", "threshold_auto",
        "threshold_mv", "threshold_next_mv", "threshold_next_source",
        "threshold_delta_mv", "threshold_action",
        "adc_early_mean_mv", "adc_peak_mv", "noise_high_pct",
    ]
    write_csv(report_dir / "閾値解析.csv", threshold_columns, (s.metrics for s in shots))
    edge_columns = [
        "source_file", "session", "seq", "index", "t_from_sync_us", "cmp_out",
        "threshold_mv",
    ]
    write_csv(
        report_dir / "CMP_OUT全エッジ.csv",
        edge_columns,
        (
            {
                "source_file": shot.path.name,
                "session": shot.session,
                "seq": shot.metrics.get("seq"),
                **row,
            }
            for shot in shots for row in shot.edges
        ),
    )
    edge_summary_columns = [
        "source_file", "session", "seq", "status", "cmp_edge_rows",
        "cmp_metadata_rows", "cmp_edges_omitted", "cmp_rises", "cmp_early_rises",
        "cmp_first_rise_us", "cmp_first_eligible_rise_us", "tof_us",
    ]
    write_csv(report_dir / "CMP_OUT解析.csv", edge_summary_columns, (s.metrics for s in shots))
    integrity_columns = [
        "source_file", "session", "seq", "file_size_bytes", "adc_rows",
        "adc_metadata_rows", "adc_dropped", "cmp_edge_rows", "cmp_metadata_rows",
        "cmp_edges_omitted", "integrity", "integrity_errors", "integrity_warnings",
    ]
    write_csv(report_dir / "SD読出し結果.csv", integrity_columns, (s.metrics for s in shots))

    source_description = (
        ", ".join(str(Path(item).resolve()) for item in args.input)
        if args.input else str(Path(args.sd_root).resolve())
    )
    text, stats = report_text(shots, source_description, config)
    if parse_failures:
        text += "\n## 読み取り失敗\n\n" + "\n".join(f"- {item}" for item in parse_failures) + "\n"
    (report_dir / "簡易レポート.md").write_text(text, encoding="utf-8")
    conditions = f"""# 前提条件

- 解析日時: {datetime.now().astimezone().isoformat(timespec="seconds")}
- 入力元: {source_description}
- 入力ファイル数: {len(files)}
- 解析成功ファイル数: {len(shots)}
- 解析スクリプト: `{Path(__file__).resolve()}`
- 原ファイルは読み取り専用で扱い、変更していない。
- 判定値の参照元: `{Path(args.config).resolve()}`
- 距離の合格基準は仕様書とconfigに従い、VALID率90%以上、score {round(config["SCORE_VALID"])}以上、ToF {round(config["MIN_RANGE_M"] / config["SOUND_SPEED_MPS"] * 1_000_000)}〜{round(config["MAX_TOF_US"])}us、標準偏差0.1m以下。
- ファイル容量の注意基準は1測定{round(config["RX_MINI_SD_FILE_WARN_BYTES"])} bytes。超過は削除せず警告する。
"""
    (report_dir / "前提条件.md").write_text(conditions, encoding="utf-8")
    create_trend_svg(report_dir / "距離_ADC_閾値推移.svg", shots)
    create_adc_svg(report_dir / "ADC代表波形.svg", shots)

    print(f"[OK] 解析ファイル: {stats['files']} (セッション {stats['sessions']})")
    print(f"[OK] 距離: VALID {stats['valid']}/{stats['files']} "
          f"({stats['valid_rate']:.1f}%), 平均 {fmt(stats['range_mean'], 4)} m, "
          f"標準偏差 {fmt(stats['range_std'], 4)} m")
    print(f"[OK] ADCピーク: {fmt(stats['adc_peak_min'], 1)}〜"
          f"{fmt(stats['adc_peak_max'], 1)} mV")
    print(f"[OK] TH_PWM: {fmt(stats['threshold_min'], 0)}〜"
          f"{fmt(stats['threshold_max'], 0)} mV")
    print(f"[OK] SD整合性: {stats['integrity_pass']}/{stats['files']}")
    print(f"[{'PASS' if stats['overall'] else 'FAIL'}] 総合判定")
    print(f"[OUTPUT] {report_dir.resolve()}")
    return 2 if args.strict and not stats["overall"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
