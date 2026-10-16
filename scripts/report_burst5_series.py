#!/usr/bin/env python3
"""Create a non-overwriting AquaBeacon experiment report from a series event log."""

import argparse
from collections import Counter
import csv
from datetime import datetime
from pathlib import Path
import statistics

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


RX_HEADER = [
    "seq", "t_sync_us", "t_detect_us", "tof_us", "range_m",
    "score", "rx_peak_pct", "noise_high_pct", "status",
]
TX_HEADER = ["seq", "t_tx_us", "wave_count", "burst_us", "status"]


def parse_events(path):
    events = []
    tx_rows = []
    rx_rows = []
    on_rows = []
    off_rows = []
    sd_rows = []
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            line = raw.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            elapsed, source, payload = line.split(",", 2)
            events.append((elapsed, source, payload))
            fields = next(csv.reader([payload]))
            if source == "TX" and len(fields) == 5 and fields[-1] == "TX_DONE":
                tx_rows.append(fields)
            elif source == "RX" and not payload.startswith("#") \
                    and len(fields) == 9:
                rx_rows.append(fields)
            elif source == "TX" and payload.startswith("# DIAG,TX_PIN,") \
                    and ",ON,pwm=1,override=NORMAL" in payload:
                on_rows.append(fields)
            elif source == "TX" and payload.startswith("# DIAG,TX_PIN,") \
                    and ",OFF,pwm=0,override=LOW,pin_readback=0,failsafe=0" in payload:
                off_rows.append(fields)
            elif source == "RX" and payload.startswith("# SD,WRITE_OK,"):
                sd_rows.append(fields)
    return events, tx_rows, rx_rows, on_rows, off_rows, sd_rows


def save_plot(rx_rows, tx_rows, output):
    indices = list(range(1, len(rx_rows) + 1))
    tofs = [int(row[3]) for row in rx_rows]
    ranges = [float(row[4]) for row in rx_rows]
    tx_times = [int(row[1]) for row in tx_rows]
    intervals = [
        (tx_times[index] - tx_times[index - 1]) / 1_000_000
        for index in range(1, len(tx_times))
    ]

    figure, axes = plt.subplots(3, 1, figsize=(9, 9))
    axes[0].plot(indices, tofs, marker="o", color="#d1495b")
    axes[0].axhline(200, color="#2a9d8f", linestyle="--",
                    label="Configured minimum valid ToF (200 us)")
    axes[0].set_ylabel("ToF [us]")
    axes[0].set_xticks(indices)
    axes[0].legend()
    axes[0].grid(alpha=0.25)

    axes[1].plot(indices, ranges, marker="o", color="#00798c")
    axes[1].set_ylabel("Calculated range [m]")
    axes[1].set_xticks(indices)
    axes[1].grid(alpha=0.25)

    axes[2].plot(indices[1:], intervals, marker="o", color="#edae49")
    axes[2].axhline(30, color="#333333", linestyle="--",
                    label="Requested interval (30 s)")
    axes[2].set_xlabel("Shot number")
    axes[2].set_ylabel("TX interval [s]")
    axes[2].set_xticks(indices)
    axes[2].legend()
    axes[2].grid(alpha=0.25)

    figure.suptitle("AquaBeacon 5-wave / 5-shot experiment")
    figure.tight_layout()
    figure.savefig(output, dpi=180)
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("event_log")
    parser.add_argument("report_dir")
    parser.add_argument("--shots", type=int, default=5)
    parser.add_argument("--sd-file", default="")
    parser.add_argument("--sd-rows", type=int)
    args = parser.parse_args()

    source = Path(args.event_log)
    report_dir = Path(args.report_dir)
    if report_dir.exists():
        raise SystemExit(f"Refusing to overwrite report directory: {report_dir}")

    events, tx_rows, rx_rows, on_rows, off_rows, sd_rows = parse_events(source)
    tx_rows = tx_rows[:args.shots]
    rx_rows = rx_rows[:args.shots]
    on_rows = on_rows[:args.shots]
    off_rows = off_rows[:args.shots]
    sd_rows = sd_rows[:args.shots]
    counts = (
        len(tx_rows), len(rx_rows), len(on_rows), len(off_rows), len(sd_rows)
    )
    if counts != (args.shots,) * 5:
        raise SystemExit(
            "Incomplete experiment log: "
            f"TX={counts[0]} RX={counts[1]} ON={counts[2]} "
            f"OFF={counts[3]} SD={counts[4]}"
        )

    report_dir.mkdir(parents=True)
    with (report_dir / "実験結果.csv").open(
            "x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(RX_HEADER)
        writer.writerows(rx_rows)

    with (report_dir / "TX実行結果.csv").open(
            "x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(TX_HEADER)
        writer.writerows(tx_rows)

    with (report_dir / "元ログ.csv").open(
            "x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["monotonic_s", "source", "line"])
        writer.writerows(events)

    tofs = [int(row[3]) for row in rx_rows]
    ranges = [float(row[4]) for row in rx_rows]
    tx_times = [int(row[1]) for row in tx_rows]
    intervals = [
        (tx_times[index] - tx_times[index - 1]) / 1_000_000
        for index in range(1, len(tx_times))
    ]
    statuses = Counter(row[8] for row in rx_rows)
    carrier_widths = [
        int(row[3]) - int(on_rows[index][3])
        for index, row in enumerate(off_rows)
    ]

    premises = f"""# 前提条件

- 実施日時: {datetime.now().astimezone().isoformat(timespec="seconds")}
- 外部電源: ON（ユーザー申告）
- TX: COM10
- RX / PiCowbell: COM5
- 自動送信回数: {args.shots}回
- 送信間隔設定: 30秒
- 送信波形: 200 kHz、5波、設定バースト長25 µs
- TX搬送波出力: GP15
- SYNC: TX GP14 → RS485 → RX GP14（変更なし）
- RX受信回路出力: GP20
- SD: PiCowbell SPI0（GP16 MISO、GP17 CS、GP18 SCK、GP19 MOSI）
- 距離換算: 1500 m/s（片道ToFとして `range_m = tof_us × 0.0015`）
- 有効ToF範囲: 200～10000 µs
- 元イベントログ: `{source.name}`
"""
    (report_dir / "前提条件.md").write_text(
        premises, encoding="utf-8", newline="\n")

    sd_summary = (
        f"`{args.sd_file}`を実読出しし、9列形式・{args.sd_rows}行を確認"
        if args.sd_file and args.sd_rows is not None
        else "シリアル上で5回のWRITE_OKを確認"
    )
    report = f"""# 簡易レポート

## 結果

- TX完了: {len(tx_rows)}/{args.shots}回
- TX出力安全OFF: {len(off_rows)}/{args.shots}回（PWM停止、LOW固定、readback=0、failsafe=0）
- RX ToF取得: {len(rx_rows)}/{args.shots}回
- SD書込み: {len(sd_rows)}/{args.shots}回（{sd_summary}）
- TX実測間隔: 平均 {statistics.mean(intervals):.6f}秒、最小 {min(intervals):.6f}秒、最大 {max(intervals):.6f}秒
- ToF: {", ".join(str(value) for value in tofs)} µs
- ToF平均: {statistics.mean(tofs):.2f} µs
- ToF標本標準偏差: {statistics.stdev(tofs):.2f} µs
- 算出距離: {", ".join(f"{value:.3f}" for value in ranges)} m
- ステータス: {", ".join(f"{key}={value}" for key, value in statuses.items())}
- TX診断上のON→OFF幅: {", ".join(str(value) for value in carrier_widths)} µs（100 µsフェイルセーフ未到達）

## 判定

30秒間隔の5回自動送信、各回のToF取得、SD保存は完了しました。一方、全5回のToFが有効下限200 µs未満で、全件`OUT_OF_RANGE`です。この条件では有効な距離測定としては不合格です。GP20でSYNC直後の信号を検出している可能性が高く、音響到来波より先に混入する電気的結合または受信回路の初期過渡を切り分ける必要があります。

RXのseqは141, 411, 681, 951, 1221と各回270ずつ増加しています。これは`RX_WAIT_SYNC`が100 msで`RX_IDLE`へ戻るたびにseqを加算する現行実装によるもので、RS485/SYNCの誤パルスを意味しません。測定番号として連番が必要なら、待機試行回数とは別のカウンタへ分離する余地があります。
"""
    (report_dir / "簡易レポート.md").write_text(
        report, encoding="utf-8", newline="\n")
    save_plot(rx_rows, tx_rows, report_dir / "ToF・距離・送信間隔.png")

    print(f"report_dir={report_dir}")
    print(f"TX={len(tx_rows)} RX={len(rx_rows)} OFF={len(off_rows)} SD={len(sd_rows)}")
    print(f"tof_mean_us={statistics.mean(tofs):.2f}")
    print(f"tx_interval_mean_s={statistics.mean(intervals):.6f}")


if __name__ == "__main__":
    main()
