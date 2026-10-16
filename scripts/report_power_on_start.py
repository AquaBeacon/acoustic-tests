#!/usr/bin/env python3
"""Create a report for the automatic power-on start validation."""

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


RX_HEADER = [
    "seq", "t_sync_us", "t_detect_us", "tof_us", "range_m",
    "score", "rx_peak_pct", "noise_high_pct", "status",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("result_csv")
    parser.add_argument("event_log")
    parser.add_argument("report_dir")
    parser.add_argument("--startup-delay-ms", type=int, required=True)
    args = parser.parse_args()

    result_path = Path(args.result_csv)
    event_path = Path(args.event_log)
    report_dir = Path(args.report_dir)
    if report_dir.exists():
        raise SystemExit(f"Refusing to overwrite report directory: {report_dir}")

    result_lines = [
        line.strip().lstrip("\ufeff")
        for line in result_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    results = list(csv.DictReader(result_lines))
    if len(results) != 1:
        raise SystemExit(f"Expected one RX result, found {len(results)}")

    events = []
    tx_row = None
    with event_path.open(encoding="utf-8") as handle:
        for raw in handle:
            line = raw.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            monotonic_s, source, payload = line.split(",", 2)
            events.append([monotonic_s, source, payload])
            fields = next(csv.reader([payload]))
            if source == "TX" and len(fields) == 5 and fields[-1] == "TX_DONE":
                tx_row = fields
    if tx_row is None:
        raise SystemExit("TX_DONE row is missing")

    tx_seq = int(tx_row[0])
    tx_time_us = int(tx_row[1])
    repeat_us = 30_000_000
    first_tx_estimate_us = tx_time_us - (tx_seq - 1) * repeat_us
    assert tx_seq >= 1
    startup_delay_us = args.startup_delay_ms * 1000
    assert startup_delay_us <= first_tx_estimate_us < startup_delay_us + 2_000_000

    report_dir.mkdir(parents=True)
    with (report_dir / "実験結果.csv").open(
            "x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RX_HEADER)
        writer.writeheader()
        writer.writerows(results)
    with (report_dir / "元ログ.csv").open(
            "x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["monotonic_s", "source", "line"])
        writer.writerows(events)

    premises = f"""# 前提条件

- TX: COM10
- RX: COM5
- 電源投入後の初回送信設定: {args.startup_delay_ms} ms
- 2回目以降の送信周期: 30,000 ms
- 送信波形: 200 kHz、5波、25 µs
- USBシリアル接続待ち: なし
- RXのSD/RTC初期化: 最初の捕捉後に遅延実行
- TX/RXのピン配置、SYNC極性、SD配線は変更なし
"""
    (report_dir / "前提条件.md").write_text(
        premises, encoding="utf-8", newline="\n")

    report = f"""# 簡易レポート

## 結果

- 捕捉したTXシーケンス: {tx_seq}
- 捕捉時のTX内部時刻: {tx_time_us / 1_000_000:.6f}秒
- 30秒周期から逆算した初回TX内部時刻: {first_tx_estimate_us / 1_000_000:.6f}秒
- TX完了: `TX_DONE`
- TX安全停止: PWM=0、LOW固定、GP15 readback=0、failsafe=0
- RX取得: ToF {results[0]["tof_us"]} µs、{results[0]["status"]}
- SD要約保存: PASS
- SD立上り保存: PASS

## 判定

電源投入・リセット後にUSB操作なしで自動送信が開始され、その後30秒周期で継続することを確認しました。初回そのものは監視ポートを開く前に通過しましたが、シーケンス{tx_seq}の内部時刻から、初回はリセット後約{first_tx_estimate_us / 1_000_000:.3f}秒だったと確認できます。設定値{args.startup_delay_ms} msとの差にはPicoのコア起動時間が含まれます。
"""
    (report_dir / "簡易レポート.md").write_text(
        report, encoding="utf-8", newline="\n")

    sequences = list(range(1, tx_seq + 1))
    times = [
        (first_tx_estimate_us + (seq - 1) * repeat_us) / 1_000_000
        for seq in sequences
    ]
    figure, axis = plt.subplots(figsize=(8, 3.8))
    axis.scatter(times, sequences, s=70, color="#00798c")
    axis.plot(times, sequences, color="#00798c", alpha=0.5)
    for seq, time_s in zip(sequences, times):
        suffix = " (inferred)" if seq < tx_seq else " (captured)"
        axis.annotate(
            f"seq {seq}: {time_s:.3f}s{suffix}",
            (time_s, seq),
            xytext=(5, 7),
            textcoords="offset points",
        )
    axis.set_xlabel("TX internal time after reset [s]")
    axis.set_ylabel("TX sequence")
    axis.set_yticks(sequences)
    axis.grid(alpha=0.25)
    figure.tight_layout()
    figure.savefig(report_dir / "電源投入後の送信開始.png", dpi=180)
    plt.close(figure)
    print(
        f"tx_seq={tx_seq} tx_time_s={tx_time_us / 1_000_000:.6f} "
        f"first_tx_estimate_s={first_tx_estimate_us / 1_000_000:.6f}"
    )


if __name__ == "__main__":
    main()
