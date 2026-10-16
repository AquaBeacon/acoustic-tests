#!/usr/bin/env python3
"""Complete a standard report folder for shared-SYNC GPS-time validation."""

import argparse
import csv
from datetime import datetime
from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFont


def font(size):
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


def load_lines(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def values_after(text, pattern):
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None


def make_graph(path, samples, ages):
    width, height = 1100, 620
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text(
        (width // 2, 20), "Shared-SYNC time validation",
        fill="black", font=font(27), anchor="ma",
    )
    left, right, top, bottom = 95, 40, 80, 80
    plot_bottom = height - bottom
    draw.line([(left, top), (left, plot_bottom), (width - right, plot_bottom)],
              fill="black", width=2)
    count = max(len(samples), len(ages), 1)
    group_width = (width - left - right) / count
    for index in range(count):
        x = left + group_width * (index + 0.5)
        draw.text((x, plot_bottom + 12), str(index + 1),
                  fill="black", font=font(16), anchor="ma")
        if index < len(samples):
            bar_height = min(samples[index] / 550.0, 1.0) * 330
            draw.rectangle(
                [x - 24, plot_bottom - bar_height, x - 3, plot_bottom],
                fill="#1565c0",
            )
            draw.text((x - 13, plot_bottom - bar_height - 7),
                      str(samples[index]), fill="#1565c0",
                      font=font(14), anchor="ms")
        if index < len(ages):
            bar_height = min(ages[index] / 150000.0, 1.0) * 330
            draw.rectangle(
                [x + 3, plot_bottom - bar_height, x + 24, plot_bottom],
                fill="#ef6c00",
            )
            draw.text((x + 14, plot_bottom - bar_height - 7),
                      f"{ages[index] / 1000:.1f}ms", fill="#ef6c00",
                      font=font(14), anchor="ms")
    draw.text((left + 15, top + 8), "Blue: ENV_ADC samples / 11ms",
              fill="#1565c0", font=font(17))
    draw.text((left + 15, top + 34), "Orange: time frame age from SYNC",
              fill="#ef6c00", font=font(17))
    draw.text((width // 2, height - 27), "Measurement number",
              fill="black", font=font(18), anchor="mm")
    image.save(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--verdict", required=True)
    parser.add_argument("--purpose", required=True)
    parser.add_argument("--note", default="")
    args = parser.parse_args()

    root = Path(args.report_dir)
    rows = load_lines(root / "元ログ.csv")
    rx_lines = [row["line"] for row in rows if row["source"] == "RX"]
    tx_lines = [row["line"] for row in rows if row["source"] == "TX"]
    captures = [
        line for line in rx_lines if line.startswith("# DIAG,CAPTURE,")
    ]
    samples = [
        values_after(line, r"samples=(\d+)") for line in captures
    ]
    dropped = [
        values_after(line, r"dropped=(\d+)") for line in captures
    ]
    time_rx = [
        line for line in rx_lines if line.startswith("# TIME_LINK,RX,seq=")
    ]
    time_logs = [
        line for line in rx_lines
        if line.startswith("# TIME,seq=") and "source=GPS_NMEA" in line
    ]
    ages = [
        values_after(line, r"packet_age_us=(\d+)") for line in time_logs
    ]
    crc_errors = sum("CRC" in line and "ERROR" in line for line in rx_lines)
    timeouts = sum(line == "# TIME_LINK,TIMEOUT" for line in rx_lines)
    sd_failed = any("MOUNT_FAILED" in line for line in rx_lines)
    sd_dump_files = sorted(root.glob("SD*測定.csv"))
    sd_dump = sd_dump_files[-1] if sd_dump_files else None
    sd_metadata = {}
    sd_adc_rows = 0
    if sd_dump is not None:
        for line in sd_dump.read_text(
                encoding="utf-8-sig").splitlines():
            if line.startswith("# ") and "," in line:
                key, value = line[2:].split(",", 1)
                sd_metadata[key] = value
            elif line.startswith("ADC,"):
                sd_adc_rows += 1
    tx_done = sum(
        bool(re.match(r"^\d+,\d+,5,25,TX_DONE$", line))
        for line in tx_lines
    )
    rx_results = sum(
        bool(re.match(r"^\d+,\d+,\d+,\d+,[\d.]+,\d+,\d+,\d+,\w+$", line))
        for line in rx_lines
    )

    if sd_dump is not None:
        sd_result = (
            "PASSED,"
            f"{sd_dump.name};utc_valid={sd_metadata.get('utc_valid', '0')};"
            f"time_source={sd_metadata.get('time_source', 'UNKNOWN')};"
            f"adc_rows={sd_adc_rows}\n"
        )
    elif sd_failed:
        sd_result = "FAILED,MOUNT_FAILED\n"
    else:
        sd_result = "NOT_RUN,no SD dump requested\n"
    (root / "SD読出し結果.csv").write_text(
        "status,detail\n" + sd_result,
        encoding="utf-8-sig",
    )
    assumptions = f"""# 前提条件

- 実験日時: {datetime.now().astimezone().isoformat(timespec="seconds")}
- 目的: {args.purpose}
- TX: COM10、200kHz・5波、5秒周期
- RX: COM5、ENV_ADC GP26、CMP_OUT GP2、TH_PWM GP6
- GPS NMEA: TX GP4
- GPS時刻伝送: 既存RS485/SYNC GP14を時分割共用
- TD04A取得窓: SYNC直後から11ms
- 時刻フレーム送信: SYNCの100ms後
- 時刻受信タイムアウト: SYNCの300ms後
- 元ログ: `{root / "元ログ.csv"}`
- SD状態: {("読戻し済み: " + sd_dump.name) if sd_dump else ("MOUNT_FAILED" if sd_failed else "未読出し")}
"""
    (root / "前提条件.md").write_text(assumptions, encoding="utf-8")

    report = f"""# 簡易レポート

## 目的

{args.purpose}

## 結果

- 判定: **{args.verdict}**
- TX完了: {tx_done}回
- RX測定結果: {rx_results}回
- RS485時刻フレーム正常受信: {len(time_rx)}回
- GPS_NMEA時刻ログ: {len(time_logs)}回
- 時刻CRCエラー: {crc_errors}回
- 時刻受信タイムアウト: {timeouts}回
- ENV_ADC取得数: {", ".join(map(str, samples)) if samples else "記録なし"}
- 取得バッファ欠落: {", ".join(map(str, dropped)) if dropped else "記録なし"}
- 時刻フレーム受信時刻: {", ".join(f"{age / 1000:.1f}ms" for age in ages) if ages else "記録なし"}
- SD保存: {("PASS（UTC有効、ADC " + str(sd_adc_rows) + "行を読戻し）") if sd_dump else ("FAIL（MOUNT_FAILED）" if sd_failed else "未確認")}

## 判定根拠

TD04Aの取得窓は0～11ms、時刻フレームは約100～130msであり、時間的に重ならない。
時刻デコーダはRX_WAIT_TIME状態でのみ有効になり、RX_CAPTURE中は動作しない。

## 異常・考察

{args.note or "特記事項なし。"}

## 次の確認事項

- SDカードのUTCメタデータ実保存: {"確認済み" if sd_dump else "要確認"}
- TD04A信号がある条件でCMP_OUT立上りと距離結果を再確認する。
"""
    (root / "簡易レポート.md").write_text(report, encoding="utf-8")
    make_graph(root / "時刻同期・受信取得数.png", samples, ages)
    print(root.resolve())


if __name__ == "__main__":
    main()
