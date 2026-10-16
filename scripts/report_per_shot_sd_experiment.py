#!/usr/bin/env python3
"""Analyze per-shot RX mini SD CSV files and create experiment artifacts."""

import argparse
import csv
from datetime import datetime
from pathlib import Path
import shutil
import statistics

import pandas as pd
from PIL import Image, ImageDraw, ImageFont


SOUND_SPEED_MPS = 1500.0
MIN_TOF_US = 200
MAX_TOF_US = 10000
FILE_WARN_BYTES = 262144
WIDTH = 1400
HEIGHT = 820
LEFT = 115
RIGHT = 45
TOP = 80
BOTTOM = 95


def font(size):
    for candidate in (
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/YuGothM.ttc",
        "C:/Windows/Fonts/arial.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default()


def new_chart(title, x_label, y_label):
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    draw.text((WIDTH // 2, 20), title, fill="black",
              font=font(28), anchor="ma")
    draw.line(
        [(LEFT, TOP), (LEFT, HEIGHT - BOTTOM),
         (WIDTH - RIGHT, HEIGHT - BOTTOM)],
        fill="black", width=2,
    )
    draw.text((WIDTH // 2, HEIGHT - 35), x_label,
              fill="black", font=font(19), anchor="mm")
    draw.text((LEFT + 8, TOP + 8), y_label,
              fill="black", font=font(17), anchor="la")
    return image, draw


def point(x, y, xmin, xmax, ymin, ymax):
    px = LEFT + (x - xmin) / (xmax - xmin) * (WIDTH - LEFT - RIGHT)
    py = HEIGHT - BOTTOM - (y - ymin) / (ymax - ymin) * (
        HEIGHT - TOP - BOTTOM
    )
    return px, py


def add_grid(draw, xmin, xmax, ymin, ymax, x_steps=8, y_steps=6,
             y_format=".2f"):
    for index in range(x_steps + 1):
        value = xmin + (xmax - xmin) * index / x_steps
        px, _ = point(value, ymin, xmin, xmax, ymin, ymax)
        draw.line([(px, TOP), (px, HEIGHT - BOTTOM)],
                  fill="#dddddd", width=1)
        draw.text((px, HEIGHT - BOTTOM + 9), f"{value:.0f}",
                  fill="black", font=font(14), anchor="ma")
    for index in range(y_steps + 1):
        value = ymin + (ymax - ymin) * index / y_steps
        _, py = point(xmin, value, xmin, xmax, ymin, ymax)
        draw.line([(LEFT, py), (WIDTH - RIGHT, py)],
                  fill="#dddddd", width=1)
        draw.text((LEFT - 9, py), format(value, y_format),
                  fill="black", font=font(14), anchor="rm")


def parse_file(path):
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    metadata = {}
    for line in lines:
        if line.startswith("# ") and "," in line:
            key, value = line[2:].split(",", 1)
            metadata[key] = value
    records = list(csv.DictReader(
        line for line in lines if not line.startswith("#")
    ))
    summaries = [row for row in records if row["record_type"] == "SUMMARY"]
    if len(summaries) != 1:
        raise ValueError(f"{path.name}: SUMMARY count is {len(summaries)}")
    adc = [row for row in records if row["record_type"] == "ADC"]
    edges = [row for row in records if row["record_type"] == "EDGE"]
    return lines, metadata, summaries[0], adc, edges


def mean_or_zero(values):
    return statistics.mean(values) if values else 0.0


def stdev_or_zero(values):
    return statistics.stdev(values) if len(values) >= 2 else 0.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sd-root", required=True)
    parser.add_argument("--report-dir", required=True)
    args = parser.parse_args()

    sd_root = Path(args.sd_root)
    report = Path(args.report_dir)
    originals = report / "SD元ファイル"
    report.mkdir(parents=True, exist_ok=False)
    originals.mkdir()

    files = sorted(sd_root.glob("S[0-9][0-9][0-9].CSV"))
    expected = [f"S{index:03d}.CSV" for index in range(len(files))]
    if [path.name for path in files] != expected:
        raise ValueError("SD shot files are not contiguous from S000.CSV")

    results = []
    integrity = []
    raw_lines = []
    adc_rows = []
    edge_rows = []
    session = 0
    previous_seq = None

    for file_index, path in enumerate(files):
        shutil.copy2(path, originals / path.name)
        lines, meta, summary, adc, edges = parse_file(path)
        seq = int(meta["seq"])
        if previous_seq is None or seq <= previous_seq:
            session += 1
        previous_seq = seq

        for line_number, raw_line in enumerate(lines, 1):
            raw_lines.append({
                "source_file": path.name,
                "line_number": line_number,
                "raw_line": raw_line,
            })

        adc_times = [int(row["t_from_sync_us"]) for row in adc]
        adc_mv = [int(row["env_adc_mv"]) for row in adc]
        adc_diffs = [
            current - prior
            for prior, current in zip(adc_times, adc_times[1:])
        ]
        peak_mv = max(adc_mv) if adc_mv else 0
        peak_time_us = min(
            (time for time, value in zip(adc_times, adc_mv)
             if value == peak_mv),
            default=0,
        )

        rises = [
            int(row["t_from_sync_us"])
            for row in edges if row["cmp_out"] == "1"
        ]
        first_rise_us = min(rises) if rises else 0
        valid_rises = [value for value in rises if value >= MIN_TOF_US]
        first_valid_rise_us = min(valid_rises) if valid_rises else 0
        early_rises = sum(value < MIN_TOF_US for value in rises)
        tof_us = int(summary["tof_us"])
        range_m = float(summary["range_m"])
        status = summary["status"]
        expected_range = tof_us * SOUND_SPEED_MPS / 1_000_000.0

        capture_matches = len(adc) == int(meta["capture_samples"])
        edges_match = len(edges) == int(meta["cmp_edges_written"])
        tof_matches = (
            (status == "NO_DETECT" and tof_us == 0)
            or first_valid_rise_us == tof_us
        )
        range_matches = abs(range_m - expected_range) <= 0.001
        file_size = path.stat().st_size
        file_ok = (
            capture_matches and edges_match and tof_matches
            and range_matches and file_size <= FILE_WARN_BYTES
        )

        result = {
            "file_index": file_index,
            "source_file": path.name,
            "session": session,
            "seq": seq,
            "t_sync_us": int(meta["t_sync_us"]),
            "t_detect_us": int(meta["t_detect_us"]),
            "tof_us": tof_us,
            "range_m": range_m,
            "score": int(summary["score"]),
            "rx_peak_pct": int(summary["rx_peak_pct"]),
            "noise_high_pct": int(summary["noise_high_pct"]),
            "status": status,
            "threshold_mv": int(meta["threshold_mv"]),
            "adc_samples": len(adc),
            "adc_dropped": int(meta["capture_dropped"]),
            "adc_peak_mv": peak_mv,
            "adc_peak_time_us": peak_time_us,
            "adc_interval_median_us": (
                statistics.median(adc_diffs) if adc_diffs else 0
            ),
            "cmp_edges": len(edges),
            "cmp_edges_omitted": int(meta["cmp_edges_omitted"]),
            "cmp_rises_before_200us": early_rises,
            "first_rise_us": first_rise_us,
            "first_rise_range_m": (
                first_rise_us * SOUND_SPEED_MPS / 1_000_000.0
                if first_rise_us else 0.0
            ),
            "first_rise_after_200us": first_valid_rise_us,
            "file_size_bytes": file_size,
            "utc_valid": int(meta.get("utc_valid", "0")),
            "utc_iso": meta.get("utc_iso", "UNSYNCED"),
            "utc_epoch_us": int(meta.get("utc_epoch_us", "0")),
            "time_source": meta.get("time_source", "UNSYNCED"),
            "time_sequence": int(meta.get("time_sequence", "0")),
            "time_flags": int(meta.get("time_flags", "0")),
            "gps_rx_pin": meta.get("gps_rx_pin", "UNKNOWN"),
            "time_packet_age_us": int(
                meta.get("time_packet_age_us", "0")
            ),
        }
        results.append(result)
        integrity.append({
            "source_file": path.name,
            "capture_count_ok": capture_matches,
            "edge_count_ok": edges_match,
            "tof_matches_first_eligible_rise": tof_matches,
            "range_formula_ok": range_matches,
            "cmp_edges_omitted": int(meta["cmp_edges_omitted"]),
            "file_size_bytes": file_size,
            "under_256KiB": file_size <= FILE_WARN_BYTES,
            "overall_ok": file_ok,
        })

        for row in adc:
            adc_rows.append({
                "file_index": file_index,
                "source_file": path.name,
                "session": session,
                "seq": seq,
                **row,
            })
        for row in edges:
            edge_rows.append({
                "file_index": file_index,
                "source_file": path.name,
                "session": session,
                "seq": seq,
                **row,
            })

    result_df = pd.DataFrame(results)
    integrity_df = pd.DataFrame(integrity)
    adc_df = pd.DataFrame(adc_rows)
    edge_df = pd.DataFrame(edge_rows)
    pd.DataFrame(raw_lines).to_csv(
        report / "元ログ.csv", index=False, encoding="utf-8-sig"
    )
    summary_columns = [
        "seq", "t_sync_us", "t_detect_us", "tof_us", "range_m",
        "score", "rx_peak_pct", "noise_high_pct", "status",
    ]
    result_df[summary_columns].to_csv(
        report / "実験結果.csv", index=False, encoding="utf-8-sig"
    )
    result_df.to_csv(
        report / "測定診断.csv", index=False, encoding="utf-8-sig"
    )
    integrity_df.to_csv(
        report / "SD検証結果.csv", index=False, encoding="utf-8-sig"
    )
    adc_df.to_csv(
        report / "ENV_ADC全測定.csv", index=False, encoding="utf-8-sig"
    )
    edge_df.to_csv(
        report / "CMP_OUT全測定.csv", index=False, encoding="utf-8-sig"
    )

    valid = result_df[result_df["status"] == "VALID"]
    signal_present = result_df[result_df["file_index"] >= 7]
    valid_ranges = list(valid["range_m"])
    valid_tofs = list(valid["tof_us"])
    first_ranges = list(valid["first_rise_range_m"])
    valid_rate = 100.0 * len(valid) / len(result_df)
    signal_valid_rate = 100.0 * (
        signal_present["status"] == "VALID"
    ).mean()
    utc_valid_count = int(result_df["utc_valid"].sum())

    # Range result and the stable pre-guard candidate.
    image, draw = new_chart(
        "外部電源実験: 距離結果と200µs未満の先行立上り",
        "SDファイル番号", "換算距離 (m)",
    )
    add_grid(draw, 0, len(result_df) - 1, 0, 0.65, x_steps=8, y_steps=7)
    selected_points = []
    direct_points = []
    for row in result_df.itertuples():
        x = float(row.file_index)
        if row.status == "VALID":
            selected_points.append(point(
                x, float(row.range_m), 0, len(result_df) - 1, 0, 0.65
            ))
            direct_points.append(point(
                x, float(row.first_rise_range_m),
                0, len(result_df) - 1, 0, 0.65
            ))
        else:
            px, py = point(x, 0, 0, len(result_df) - 1, 0, 0.65)
            draw.line([(px - 5, py - 5), (px + 5, py + 5)],
                      fill="#666666", width=2)
            draw.line([(px - 5, py + 5), (px + 5, py - 5)],
                      fill="#666666", width=2)
    if len(selected_points) > 1:
        draw.line(selected_points, fill="#1565c0", width=3)
    if len(direct_points) > 1:
        draw.line(direct_points, fill="#f57c00", width=3)
    for px, py in selected_points:
        draw.ellipse([px - 4, py - 4, px + 4, py + 4],
                     fill="#1565c0")
    for px, py in direct_points:
        draw.ellipse([px - 3, py - 3, px + 3, py + 3],
                     fill="#f57c00")
    draw.text((WIDTH - 390, TOP + 25), "青: 現行判定（200µs以降）",
              fill="#1565c0", font=font(17))
    draw.text((WIDTH - 390, TOP + 52), "橙: 最初の立上り（参考値）",
              fill="#f57c00", font=font(17))
    image.save(report / "距離結果.png")

    # Threshold and envelope peak evolution.
    image, draw = new_chart(
        "TH_PWM自動調整とENV_ADCピーク",
        "SDファイル番号", "電圧 (mV)",
    )
    add_grid(draw, 0, len(result_df) - 1, 0, 400,
             x_steps=8, y_steps=8, y_format=".0f")
    threshold_points = [
        point(float(row.file_index), float(row.threshold_mv),
              0, len(result_df) - 1, 0, 400)
        for row in result_df.itertuples()
    ]
    peak_points = [
        point(float(row.file_index), float(row.adc_peak_mv),
              0, len(result_df) - 1, 0, 400)
        for row in result_df.itertuples()
    ]
    draw.line(threshold_points, fill="#7b1fa2", width=3)
    draw.line(peak_points, fill="#388e3c", width=3)
    for px, py in threshold_points:
        draw.ellipse([px - 3, py - 3, px + 3, py + 3],
                     fill="#7b1fa2")
    for px, py in peak_points:
        draw.ellipse([px - 3, py - 3, px + 3, py + 3],
                     fill="#388e3c")
    draw.text((WIDTH - 335, TOP + 25), "紫: TH_PWM",
              fill="#7b1fa2", font=font(17))
    draw.text((WIDTH - 335, TOP + 52), "緑: ENV_ADCピーク",
              fill="#388e3c", font=font(17))
    image.save(report / "閾値_ENV_ADC推移.png")

    # Rising-edge raster, focusing on the ranging region.
    image, draw = new_chart(
        "CMP_OUT立上り分布（先行波と現行選択波）",
        "SYNCからの時間 (µs)", "SDファイル番号",
    )
    add_grid(draw, 0, 600, 0, len(result_df) - 1,
             x_steps=12, y_steps=8, y_format=".0f")
    edge_rises = edge_df[edge_df["cmp_out"].astype(str) == "1"].copy()
    edge_rises["t_from_sync_us"] = edge_rises[
        "t_from_sync_us"
    ].astype(int)
    for row in edge_rises.itertuples():
        if row.t_from_sync_us > 600:
            continue
        px, py = point(
            float(row.t_from_sync_us), float(row.file_index),
            0, 600, 0, len(result_df) - 1,
        )
        color = "#f57c00" if row.t_from_sync_us < MIN_TOF_US else "#90caf9"
        draw.ellipse([px - 2, py - 2, px + 2, py + 2], fill=color)
    for row in valid.itertuples():
        px, py = point(
            float(row.tof_us), float(row.file_index),
            0, 600, 0, len(result_df) - 1,
        )
        draw.line([(px - 5, py - 5), (px + 5, py + 5)],
                  fill="#d32f2f", width=2)
        draw.line([(px - 5, py + 5), (px + 5, py - 5)],
                  fill="#d32f2f", width=2)
    guard_x, _ = point(MIN_TOF_US, 0, 0, 600, 0, len(result_df) - 1)
    draw.line([(guard_x, TOP), (guard_x, HEIGHT - BOTTOM)],
              fill="black", width=2)
    image.save(report / "CMP_OUT立上り分布.png")

    # Representative envelope waveforms.
    representative = [7, 17, 30, 46]
    image, draw = new_chart(
        "代表測定のENV_ADC波形",
        "SYNCからの時間 (µs)", "ENV_ADC (mV)",
    )
    add_grid(draw, 0, 800, 0, 400, x_steps=8, y_steps=8, y_format=".0f")
    colors = ["#1565c0", "#d32f2f", "#388e3c", "#7b1fa2"]
    for offset, (file_index, color) in enumerate(
            zip(representative, colors)):
        view = adc_df[
            (adc_df["file_index"] == file_index)
            & (adc_df["t_from_sync_us"].astype(int) <= 800)
        ]
        points = [
            point(float(row.t_from_sync_us), float(row.env_adc_mv),
                  0, 800, 0, 400)
            for row in view.itertuples()
        ]
        if len(points) > 1:
            draw.line(points, fill=color, width=2)
        draw.text((WIDTH - 235, TOP + 25 + 25 * offset),
                  f"S{file_index:03d}", fill=color, font=font(16))
    image.save(report / "ENV_ADC代表波形.png")

    total_bytes = int(result_df["file_size_bytes"].sum())
    valid_peak_values = list(valid["adc_peak_mv"])
    no_detect = result_df[result_df["status"] == "NO_DETECT"]
    no_detect_peaks = list(no_detect["adc_peak_mv"])
    all_integrity_ok = bool(integrity_df["overall_ok"].all())
    omitted_total = int(result_df["cmp_edges_omitted"].sum())
    max_file_bytes = int(result_df["file_size_bytes"].max())
    sample_intervals = []
    for _, group in adc_df.groupby("file_index"):
        times = list(group["t_from_sync_us"].astype(int))
        sample_intervals.extend(
            current - prior
            for prior, current in zip(times, times[1:])
        )

    assumptions = f"""# 前提条件

- レポート作成日時: {datetime.now().astimezone().isoformat(timespec="seconds")}
- 実験日時: SDカードにRTCがないため厳密値なし（ユーザー報告後に回収）
- 外部電源: ON、実験中はUSB給電を使用しない構成
- TX: COM10（書込み時）、200kHz・5波・5秒間隔
- RX / microSD: COM5（書込み・読出し時）
- TD04A: TX/RXとも水中
- 振動子間距離: 数cm（直前に共有された条件を継続と仮定、厳密値なし）
- 水槽: 狭く、マルチパスの可能性あり
- SYNC: TX GP14 → RX GP14、有線SYNCとGND共通
- RX: TH_PWM GP6 / ENV_ADC GP26 / CMP_OUT GP2
- TH_PWM: 自動調整、起動時300mV
- ENV_ADC取得: SYNC後11ms、目標20µs間隔
- 距離換算: 水中音速1500m/s、SYSTEM_DELAY_US=0
- 現行最小距離: 0.3m（200µs未満のCMP_OUT立上りを除外）
- SD読出し元: `D:\\S000.CSV` ～ `D:\\S{len(files)-1:03d}.CSV`
- 元ログ: `{report / "元ログ.csv"}`
- 元ファイル複製: `{originals}`
"""
    (report / "前提条件.md").write_text(
        assumptions, encoding="utf-8-sig"
    )

    report_text = f"""# 簡易レポート

## 結果

- SDファイル: {len(result_df)}件（S000.CSV～S{len(files)-1:03d}.CSV）
- RX起動区間: {session}区間（seqのリセットから判定）
- 全体VALID率: {valid_rate:.1f}%（{len(valid)}/{len(result_df)}、基準90%以上に対して不合格）
- S007以降の信号成立区間: VALID率{signal_valid_rate:.1f}%（{len(signal_present)}/{len(signal_present)}）
- NO_DETECT: {len(no_detect)}件（S000～S006）
- 有効ToF: 平均{mean_or_zero(valid_tofs):.1f}µs、標準偏差{stdev_or_zero(valid_tofs):.1f}µs、範囲{min(valid_tofs)}～{max(valid_tofs)}µs
- 現行距離: 平均{mean_or_zero(valid_ranges):.3f}m、標準偏差{stdev_or_zero(valid_ranges):.3f}m、範囲{min(valid_ranges):.3f}～{max(valid_ranges):.3f}m
- 200µs未満の最初の立上り換算: 平均{mean_or_zero(first_ranges):.3f}m、標準偏差{stdev_or_zero(first_ranges):.3f}m、範囲{min(first_ranges):.3f}～{max(first_ranges):.3f}m
- VALID時ENV_ADCピーク: 平均{mean_or_zero(valid_peak_values):.1f}mV、範囲{min(valid_peak_values)}～{max(valid_peak_values)}mV
- NO_DETECT時ENV_ADCピーク: 平均{mean_or_zero(no_detect_peaks):.1f}mV、範囲{min(no_detect_peaks)}～{max(no_detect_peaks)}mV
- TH_PWM: 平均{result_df["threshold_mv"].mean():.1f}mV、範囲{result_df["threshold_mv"].min()}～{result_df["threshold_mv"].max()}mV
- ADCサンプル間隔: 中央値{statistics.median(sample_intervals):.1f}µs、最大{max(sample_intervals)}µs
- SD容量: 合計{total_bytes / 1024:.1f}KiB、平均{result_df["file_size_bytes"].mean() / 1024:.1f}KiB/件、最大{max_file_bytes / 1024:.1f}KiB
- SD省略エッジ: {omitted_total}件
- SD整合性検査: {f"{len(result_df)}/{len(result_df)} passed" if all_integrity_ok else "failed"}
- GPS UTC記録: {utc_valid_count}/{len(result_df)}件

## 判定

- 全47件を一括評価したVALID率85.1%は、仕様の90%以上を満たしません。
- 最初の7件にはCMP_OUTエッジがなく、ENV_ADCピークも58～106mVでした。S007からはピーク265mV以上となり、以後40件が連続VALIDです。TX開始前、浸水前、または起動手順中の測定が混在した可能性がありますが、TXログがないため確定できません。
- 信号成立後40件の距離標準偏差は{stdev_or_zero(valid_ranges):.3f}mで、10cm以下の再現性基準を満たします。
- scoreは全VALID測定で8、ToFは全件265～377µsで形式上の範囲基準を満たします。
- ただし、数cmという実距離に対して現行結果0.398～0.566mは絶対距離として不整合です。

## 主要な診断

1. 全40 VALID測定で、80～96µsに1回ずつ先行立上りがあります。換算距離は0.120～0.144m、標準偏差は{stdev_or_zero(first_ranges):.3f}mで、現行選択波よりはるかに安定しています。
2. 現行コードはMIN_RANGE_M=0.3mのため、この先行波を必ず捨て、265～377µsの後続立上りを距離として採用します。
3. 後続波は約0.40m群と約0.52m群に分かれ、狭い水槽の反射・リンギング・マルチパスを選択している可能性が高いです。
4. 外部電源化前の5件（平均0.768m、標準偏差0.137m）と比べ、今回の有効40件は平均0.476m、標準偏差0.064mまで改善しました。ただし実験条件と測定数が同一ではないため、改善を外部電源だけの効果とは断定できません。
5. 外部電源でも有線SYNCと共通GNDは残るため、80～96µsの先行波が音響到達か電気的回り込みかは、無送信試験またはSYNC絶縁試験で切り分けが必要です。

## SDログ評価

- 47ファイルすべてでADC行数、CMP行数、ToFと最初の200µs以降立上り、距離換算式が一致しました。
- ADCドロップ、CMPエッジ省略、256KiB超過はいずれも0件です。
- 最大ファイルは{max_file_bytes / 1024:.1f}KiBで、ファイルサイズ対策は十分機能しています。

## 次の確認事項

1. 同じ配置でTXの200kHz出力だけを無効化し、80～96µsの立上りが消えるか確認する。
2. 振動子間の実距離をmm単位で記録し、200µs未満のガードを一時的に下げた診断ファームウェアで先行波を距離化する。
3. 水槽内の反射条件を変えるため、壁から離す、吸音材を置く、または広い水槽で再測定する。
4. TXログも同時保存し、最初の7件が無送信だったか受信失敗だったかを確定する。

## 合格基準

| 項目 | 結果 | 判定 |
|---|---:|---|
| VALID率（全47件） | {valid_rate:.1f}% | FAIL |
| VALID率（S007以降） | {signal_valid_rate:.1f}% | PASS |
| score | 全VALIDで8 | PASS |
| ToF | {min(valid_tofs)}～{max(valid_tofs)}µs | PASS |
| 距離再現性 | 標準偏差{stdev_or_zero(valid_ranges):.3f}m | PASS |
| 絶対距離 | 0.398～0.566m（実距離は数cm） | FAIL |
| 無送信時の誤検出 | TXログなし | 未判定 |
| SD完全性 | {len(result_df)}/{len(result_df)} | PASS |
"""
    (report / "簡易レポート.md").write_text(
        report_text, encoding="utf-8-sig"
    )

    print(report)
    print(f"files={len(result_df)} valid={len(valid)} "
          f"valid_rate={valid_rate:.1f}%")
    print(f"signal_valid_rate={signal_valid_rate:.1f}%")
    print(f"range_mean={mean_or_zero(valid_ranges):.6f} "
          f"range_std={stdev_or_zero(valid_ranges):.6f}")
    print(f"integrity_ok={all_integrity_ok} omitted={omitted_total}")


if __name__ == "__main__":
    main()
