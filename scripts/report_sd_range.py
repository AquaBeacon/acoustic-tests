#!/usr/bin/env python3
"""Create a non-destructive report from a contiguous range of per-shot SD logs."""

from __future__ import annotations

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
FILE_WARN_BYTES = 256 * 1024
WIDTH, HEIGHT = 1500, 850
LEFT, RIGHT, TOP, BOTTOM = 120, 50, 75, 100


def font(size: int):
    for path in (
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/YuGothM.ttc",
        "C:/Windows/Fonts/arial.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def parse_file(path: Path):
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    metadata = {}
    for line in lines:
        if line.startswith("# ") and "," in line:
            key, value = line[2:].split(",", 1)
            metadata[key] = value
    records = list(csv.DictReader(
        line for line in lines if line and not line.startswith("#")
    ))
    summaries = [row for row in records if row["record_type"] == "SUMMARY"]
    if len(summaries) != 1:
        raise ValueError(f"{path.name}: SUMMARY数={len(summaries)}")
    adc = [row for row in records if row["record_type"] == "ADC"]
    edges = [row for row in records if row["record_type"] == "EDGE"]
    return lines, metadata, summaries[0], adc, edges


def chart(title: str, x_label: str, y_label: str, xlim, ylim):
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    draw.text((WIDTH // 2, 28), title, fill="black",
              font=font(28), anchor="ma")
    draw.line([(LEFT, TOP), (LEFT, HEIGHT - BOTTOM),
               (WIDTH - RIGHT, HEIGHT - BOTTOM)], fill="black", width=2)
    draw.text((WIDTH // 2, HEIGHT - 35), x_label, fill="black",
              font=font(19), anchor="mm")
    draw.text((LEFT + 8, TOP + 8), y_label, fill="black",
              font=font(17), anchor="la")

    def xy(x, y):
        px = LEFT + (x - xlim[0]) / (xlim[1] - xlim[0]) * (
            WIDTH - LEFT - RIGHT
        )
        py = HEIGHT - BOTTOM - (y - ylim[0]) / (ylim[1] - ylim[0]) * (
            HEIGHT - TOP - BOTTOM
        )
        return px, py

    for index in range(11):
        x = xlim[0] + (xlim[1] - xlim[0]) * index / 10
        px, _ = xy(x, ylim[0])
        draw.line([(px, TOP), (px, HEIGHT - BOTTOM)],
                  fill="#e0e0e0", width=1)
        draw.text((px, HEIGHT - BOTTOM + 10), f"{x:.0f}",
                  fill="black", font=font(14), anchor="ma")
    for index in range(9):
        y = ylim[0] + (ylim[1] - ylim[0]) * index / 8
        _, py = xy(xlim[0], y)
        draw.line([(LEFT, py), (WIDTH - RIGHT, py)],
                  fill="#e0e0e0", width=1)
        draw.text((LEFT - 10, py), f"{y:.0f}", fill="black",
                  font=font(14), anchor="rm")
    return image, draw, xy


def line_series(draw, xy, values, color, width=3):
    points = [xy(float(x), float(y)) for x, y in values]
    if len(points) > 1:
        draw.line(points, fill=color, width=width)
    for px, py in points:
        draw.ellipse([px - 4, py - 4, px + 4, py + 4], fill=color)


def mean(values):
    return statistics.mean(values) if values else 0.0


def stdev(values):
    return statistics.stdev(values) if len(values) >= 2 else 0.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sd-root", required=True)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    parser.add_argument("--report-dir", required=True)
    args = parser.parse_args()

    sd_root = Path(args.sd_root)
    report = Path(args.report_dir)
    originals = report / "SD元ファイル"
    report.mkdir(parents=True, exist_ok=False)
    originals.mkdir()

    files = [
        sd_root / f"S{index:03d}.CSV"
        for index in range(args.start, args.end + 1)
    ]
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        raise FileNotFoundError("不足ファイル: " + ", ".join(missing))

    results, integrity, raw_lines, adc_rows, edge_rows = [], [], [], [], []
    session, previous_seq = 0, None
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
        adc_values = [int(row["env_adc_mv"]) for row in adc]
        intervals = [
            current - prior
            for prior, current in zip(adc_times, adc_times[1:])
        ]
        peak_mv = max(adc_values) if adc_values else 0
        peak_time = next((
            t for t, value in zip(adc_times, adc_values) if value == peak_mv
        ), 0)
        rises = sorted(
            int(row["t_from_sync_us"])
            for row in edges if str(row["cmp_out"]) == "1"
        )
        eligible_rises = [value for value in rises if value >= MIN_TOF_US]
        first_eligible = eligible_rises[0] if eligible_rises else 0

        tof_us = int(summary["tof_us"])
        range_m = float(summary["range_m"])
        status = summary["status"]
        capture_ok = len(adc) == int(meta["capture_samples"])
        edges_ok = len(edges) == int(meta["cmp_edges_written"])
        tof_ok = (
            (status == "NO_DETECT" and tof_us == 0)
            or first_eligible == tof_us
        )
        range_ok = abs(
            range_m - tof_us * SOUND_SPEED_MPS / 1_000_000.0
        ) <= 0.001
        file_size = path.stat().st_size
        overall_ok = all((
            capture_ok, edges_ok, tof_ok, range_ok,
            int(meta["capture_dropped"]) == 0,
            int(meta["cmp_edges_omitted"]) == 0,
            file_size <= FILE_WARN_BYTES,
        ))

        results.append({
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
            "adc_peak_time_us": peak_time,
            "adc_interval_median_us": (
                statistics.median(intervals) if intervals else 0
            ),
            "cmp_edges": len(edges),
            "cmp_edges_omitted": int(meta["cmp_edges_omitted"]),
            "cmp_rises_before_200us": sum(value < MIN_TOF_US for value in rises),
            "first_rise_us": rises[0] if rises else 0,
            "first_rise_after_200us": first_eligible,
            "file_size_bytes": file_size,
            "utc_valid": int(meta.get("utc_valid", "0")),
            "utc_iso": meta.get("utc_iso", "UNSYNCED"),
            "time_source": meta.get("time_source", "UNSYNCED"),
            "gps_rx_pin": meta.get("gps_rx_pin", "UNKNOWN"),
        })
        integrity.append({
            "source_file": path.name,
            "capture_count_ok": capture_ok,
            "edge_count_ok": edges_ok,
            "tof_matches_first_eligible_rise": tof_ok,
            "range_formula_ok": range_ok,
            "capture_dropped": int(meta["capture_dropped"]),
            "cmp_edges_omitted": int(meta["cmp_edges_omitted"]),
            "file_size_bytes": file_size,
            "under_256KiB": file_size <= FILE_WARN_BYTES,
            "overall_ok": overall_ok,
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
        report / "SD読出し結果.csv", index=False, encoding="utf-8-sig"
    )
    adc_df.to_csv(
        report / "ENV_ADC全測定.csv", index=False, encoding="utf-8-sig"
    )
    edge_df.to_csv(
        report / "CMP_OUT全測定.csv", index=False, encoding="utf-8-sig"
    )

    xmax = max(len(result_df) - 1, 1)
    image, draw, xy = chart(
        "TH_PWM自動調整とENV_ADCピーク",
        f"ファイル順（0=S{args.start:03d}, {len(files)-1}=S{args.end:03d}）",
        "電圧 (mV)", (0, xmax), (0, 400),
    )
    line_series(draw, xy, [
        (row.file_index, row.threshold_mv)
        for row in result_df.itertuples()
    ], "#7b1fa2")
    line_series(draw, xy, [
        (row.file_index, row.adc_peak_mv)
        for row in result_df.itertuples()
    ], "#388e3c")
    draw.text((WIDTH - 330, TOP + 25), "紫: TH_PWM",
              fill="#7b1fa2", font=font(18))
    draw.text((WIDTH - 330, TOP + 55), "緑: ENV_ADCピーク",
              fill="#388e3c", font=font(18))
    image.save(report / "閾値_ENV_ADC推移.png")

    image, draw, xy = chart(
        "検出ToFと換算距離", f"ファイル順（S{args.start:03d}～S{args.end:03d}）",
        "ToF (µs)", (0, xmax), (0, 500),
    )
    valid_rows = result_df[result_df["status"] == "VALID"]
    line_series(draw, xy, [
        (row.file_index, row.tof_us)
        for row in valid_rows.itertuples()
    ], "#1565c0")
    for row in result_df[result_df["status"] != "VALID"].itertuples():
        px, py = xy(row.file_index, 0)
        draw.line([(px - 5, py - 5), (px + 5, py + 5)],
                  fill="#777777", width=2)
        draw.line([(px - 5, py + 5), (px + 5, py - 5)],
                  fill="#777777", width=2)
    draw.text((WIDTH - 390, TOP + 25), "青: VALID、×: NO_DETECT",
              fill="#1565c0", font=font(18))
    image.save(report / "ToF推移.png")

    representatives = [
        0,
        max(0, int(valid_rows.iloc[0]["file_index"])) if len(valid_rows) else 0,
        min(len(result_df) - 1, 18),
        len(result_df) - 1,
    ]
    representatives = list(dict.fromkeys(representatives))
    image, draw, xy = chart(
        "代表ENV_ADC波形", "SYNCからの時間 (µs)",
        "ENV_ADC (mV)", (0, 1000), (0, 400),
    )
    colors = ["#1565c0", "#d32f2f", "#388e3c", "#7b1fa2"]
    for offset, (index, color) in enumerate(zip(representatives, colors)):
        view = adc_df[
            (adc_df["file_index"] == index)
            & (adc_df["t_from_sync_us"].astype(int) <= 1000)
        ]
        points = [
            (int(row.t_from_sync_us), int(row.env_adc_mv))
            for row in view.itertuples()
        ]
        line_series(draw, xy, points, color, width=2)
        label = str(result_df.iloc[index]["source_file"])
        draw.text((WIDTH - 230, TOP + 25 + offset * 28), label,
                  fill=color, font=font(17))
    image.save(report / "ENV_ADC代表波形.png")

    valid_tofs = list(valid_rows["tof_us"].astype(int))
    valid_ranges = list(valid_rows["range_m"].astype(float))
    no_detect_count = int((result_df["status"] == "NO_DETECT").sum())
    valid_rate = 100.0 * len(valid_rows) / len(result_df)
    threshold_values = list(result_df["threshold_mv"].astype(int))
    all_ok = bool(integrity_df["overall_ok"].all())
    utc_count = int(result_df["utc_valid"].sum())
    sessions = int(result_df["session"].max())
    total_kib = result_df["file_size_bytes"].sum() / 1024

    assumptions = f"""# 前提条件

- レポート作成日時: {datetime.now().astimezone().isoformat(timespec="seconds")}
- 対象SDログ: `D:\\S{args.start:03d}.CSV` ～ `D:\\S{args.end:03d}.CSV`
- 元ログは変更せず、`SD元ファイル`へ複製して解析
- TX: COM10、200kHz・5波、5秒周期
- RX: COM5、microSD保存、TH_PWM自動調整
- TD04A: 送受信とも水中。距離は数cm、狭い水槽のためマルチパスあり
- 外部電源で動作。USBはログ取得・書込み時のみ
- 距離換算: 水中音速1500m/s、SYSTEM_DELAY_US=0
- GPS時刻: TX側で受信し、RS485/SYNC線の測距後時間窓でRXへ多重化
- RX使用信号: SYNC GP14、TH_PWM GP6、ENV_ADC GP26、CMP_OUT GP2
"""
    (report / "前提条件.md").write_text(assumptions, encoding="utf-8-sig")

    valid_summary = (
        f"{min(valid_tofs)}～{max(valid_tofs)}µs、平均{mean(valid_tofs):.1f}µs、"
        f"標準偏差{stdev(valid_tofs):.1f}µs"
        if valid_tofs else "なし"
    )
    range_summary = (
        f"{min(valid_ranges):.3f}～{max(valid_ranges):.3f}m、"
        f"平均{mean(valid_ranges):.3f}m、標準偏差{stdev(valid_ranges):.3f}m"
        if valid_ranges else "なし"
    )
    repeatability_pass = stdev(valid_ranges) <= 0.10
    report_text = f"""# 簡易レポート

## 結果

- 対象: {len(result_df)}ファイル、{sessions}セッション
- VALID: {len(valid_rows)}/{len(result_df)}（{valid_rate:.1f}%）
- NO_DETECT: {no_detect_count}/{len(result_df)}
- VALID時score: {sorted(set(valid_rows["score"].astype(int))) if len(valid_rows) else []}
- ToF: {valid_summary}
- 換算距離: {range_summary}
- TH_PWM: {min(threshold_values)}～{max(threshold_values)}mV
- UTC有効: {utc_count}/{len(result_df)}
- SD容量: 合計{total_kib:.1f}KiB、最大{result_df["file_size_bytes"].max()/1024:.1f}KiB/件
- SD整合性: {"全件PASS" if all_ok else "FAILあり"}

## 閾値自動調整の確認

- S066～S081は有効信号がなく、TH_PWMは300mVを保持しています。
- S082のVALID後、次のS083でTH_PWMが318mVへ変化しています。
- 再起動後のS084で300mVへ初期化され、S085以降は
  300→299→269→296→287→315→288→300→301→316→320mVと変化しています。
- よって自動調整は動作しています。信頼できる信号がない場合は閾値を保持する設計です。

## 判定

- TD04A応答はS082、S083、S085～S095の13件で確認でき、すべてscore=8です。
- 全30件のVALID率43.3%は90%基準未達ですが、途中にNO_DETECT区間と再起動を含むため、
  同一条件の連続測距試験としては扱えません。
- VALID距離の標準偏差は{stdev(valid_ranges):.3f}mで、
  10cm基準に対して{"PASS" if repeatability_pass else "FAIL"}です。
- 数cmの実距離に対して0.333～0.688mと大きいため、現在値は絶対距離ではなく、
  RX回路遅延・検出遅延・マルチパスを含んでいます。

## 次の確認

1. 安全な微小信号注入でRX回路遅延を測り、SYSTEM_DELAY_US候補を得る。
2. 入力振幅とTH_PWMを複数点で振り、遅延の振幅依存性を記録する。
3. 同一配置・同一電源条件で10回以上連続測定し、VALID率と距離再現性を再評価する。
"""
    (report / "簡易レポート.md").write_text(
        report_text, encoding="utf-8-sig"
    )

    print(report)
    print(f"files={len(result_df)} valid={len(valid_rows)} "
          f"valid_rate={valid_rate:.1f}%")
    print(f"tof_mean={mean(valid_tofs):.3f} tof_std={stdev(valid_tofs):.3f}")
    print(f"range_mean={mean(valid_ranges):.6f} "
          f"range_std={stdev(valid_ranges):.6f}")
    print(f"threshold_min={min(threshold_values)} "
          f"threshold_max={max(threshold_values)}")
    print(f"integrity_ok={all_ok} utc_valid={utc_count}")


if __name__ == "__main__":
    main()
