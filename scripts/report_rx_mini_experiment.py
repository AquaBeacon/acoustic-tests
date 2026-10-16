#!/usr/bin/env python3
"""Create the standard report artifacts for an RX mini water experiment."""

import argparse
from pathlib import Path

import pandas as pd
from PIL import Image, ImageDraw, ImageFont


WIDTH = 1200
HEIGHT = 700
MARGIN_L = 105
MARGIN_R = 35
MARGIN_T = 70
MARGIN_B = 85


def font(size):
    candidates = [
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default()


def canvas(title, x_label, y_label):
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    draw.text((WIDTH // 2, 18), title, fill="black",
              font=font(28), anchor="ma")
    draw.line(
        [(MARGIN_L, MARGIN_T), (MARGIN_L, HEIGHT - MARGIN_B),
         (WIDTH - MARGIN_R, HEIGHT - MARGIN_B)],
        fill="black", width=2,
    )
    draw.text(
        (WIDTH // 2, HEIGHT - 35), x_label,
        fill="black", font=font(20), anchor="mm",
    )
    draw.text(
        (MARGIN_L + 8, MARGIN_T + 8), y_label,
        fill="black", font=font(17), anchor="la",
    )
    return image, draw


def xy(value_x, value_y, xmin, xmax, ymin, ymax):
    plot_w = WIDTH - MARGIN_L - MARGIN_R
    plot_h = HEIGHT - MARGIN_T - MARGIN_B
    px = MARGIN_L + (value_x - xmin) / (xmax - xmin) * plot_w
    py = HEIGHT - MARGIN_B - (value_y - ymin) / (ymax - ymin) * plot_h
    return px, py


def grid(
        draw, xmin, xmax, ymin, ymax, x_ticks=6, y_ticks=6,
        show_y_labels=True):
    for index in range(x_ticks + 1):
        value = xmin + (xmax - xmin) * index / x_ticks
        px, _ = xy(value, ymin, xmin, xmax, ymin, ymax)
        draw.line(
            [(px, MARGIN_T), (px, HEIGHT - MARGIN_B)],
            fill="#dddddd", width=1,
        )
        draw.text(
            (px, HEIGHT - MARGIN_B + 10), f"{value:.0f}",
            fill="black", font=font(15), anchor="ma",
        )
    for index in range(y_ticks + 1):
        value = ymin + (ymax - ymin) * index / y_ticks
        _, py = xy(xmin, value, xmin, xmax, ymin, ymax)
        draw.line(
            [(MARGIN_L, py), (WIDTH - MARGIN_R, py)],
            fill="#dddddd", width=1,
        )
        if show_y_labels:
            draw.text(
                (MARGIN_L - 10, py), f"{value:.2f}",
                fill="black", font=font(15), anchor="rm",
            )


def dataframe_markdown(frame):
    table = frame.reset_index()
    columns = [str(column) for column in table.columns]
    rows = [
        [str(value) for value in row]
        for row in table.itertuples(index=False, name=None)
    ]
    output = [
        "| " + " | ".join(columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
    ]
    output.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", required=True)
    args = parser.parse_args()
    root = Path(args.report_dir)

    result = pd.read_csv(root / "実験結果.csv")
    sd_summary = pd.read_csv(root / "SD読み戻し.csv")
    adc_all = pd.read_csv(root / "SD_ENV_ADC.csv")
    cmp_all = pd.read_csv(root / "SD_CMP_OUT.csv")
    seqs = set(result["seq"])
    adc = adc_all[adc_all["seq"].isin(seqs)].copy()
    cmp_data = cmp_all[cmp_all["seq"].isin(seqs)].copy()
    adc.to_csv(root / "ENV_ADC抽出.csv", index=False, encoding="utf-8-sig")
    cmp_data.to_csv(
        root / "CMP_OUT抽出.csv", index=False, encoding="utf-8-sig"
    )

    valid_rate = 100.0 * (result["status"] == "VALID").mean()
    range_mean = result["range_m"].mean()
    range_std = result["range_m"].std(ddof=1)
    tof_mean = result["tof_us"].mean()
    tof_std = result["tof_us"].std(ddof=1)
    sd_matches = result.merge(
        sd_summary,
        how="inner",
        on=list(result.columns),
    ).shape[0]

    rising = cmp_data[cmp_data["cmp_out"] == 1]
    early = rising[rising["t_from_sync_us"] < 200]
    ranged = rising[rising["t_from_sync_us"] >= 200]
    adc_stats = adc.groupby("seq").agg(
        adc_samples=("sample_index", "count"),
        peak_adc_mv=("env_adc_mv", "max"),
        threshold_mv=("threshold_mv", "first"),
        median_sample_us=("t_from_sync_us", lambda x: x.diff().median()),
    )
    cmp_stats = cmp_data.groupby("seq").agg(
        cmp_edges=("edge_index", "count"),
        first_edge_us=("t_from_sync_us", "min"),
        last_edge_us=("t_from_sync_us", "max"),
    )

    image, draw = canvas(
        "RX mini: measured range", "RX sequence", "Range (m)"
    )
    xmin = float(result["seq"].min())
    xmax = float(result["seq"].max())
    if xmin == xmax:
        xmax += 1
    grid(draw, xmin, xmax, 0, 1.05, x_ticks=4, y_ticks=7)
    _, ref_y = xy(xmin, 0.15, xmin, xmax, 0, 1.05)
    draw.rectangle(
        [(MARGIN_L, ref_y), (WIDTH - MARGIN_R, HEIGHT - MARGIN_B)],
        fill="#e6f4e6",
    )
    points = [
        xy(float(row.seq), float(row.range_m), xmin, xmax, 0, 1.05)
        for row in result.itertuples()
    ]
    draw.line(points, fill="#1565c0", width=4)
    for point in points:
        draw.ellipse(
            [point[0] - 6, point[1] - 6, point[0] + 6, point[1] + 6],
            fill="#1565c0",
        )
    image.save(root / "距離結果.png")

    image, draw = canvas(
        "ENV_ADC immediately after transmission",
        "Time from SYNC (us)", "ENV_ADC (mV)",
    )
    grid(draw, 0, 1000, 0, 330, x_ticks=10, y_ticks=6)
    colors = ["#1565c0", "#d32f2f", "#388e3c", "#7b1fa2", "#f57c00"]
    for color, (seq, group) in zip(colors, adc.groupby("seq")):
        view = group[group["t_from_sync_us"] <= 1000]
        points = [
            xy(float(row.t_from_sync_us), float(row.env_adc_mv),
               0, 1000, 0, 330)
            for row in view.itertuples()
        ]
        if len(points) > 1:
            draw.line(points, fill=color, width=2)
        draw.text(
            (WIDTH - 170, MARGIN_T + 25 * colors.index(color)),
            f"seq {seq}", fill=color, font=font(16),
        )
    guard_x, _ = xy(200, 0, 0, 1000, 0, 330)
    draw.line(
        [(guard_x, MARGIN_T), (guard_x, HEIGHT - MARGIN_B)],
        fill="black", width=2,
    )
    image.save(root / "ENV_ADC波形.png")

    image, draw = canvas(
        "CMP_OUT rising edges (red X = selected ToF)",
        "Rising edge time from SYNC (us)", "Measurement",
    )
    sorted_seqs = sorted(seqs)
    grid(draw, 0, 1200, -0.5, len(sorted_seqs) - 0.5,
         x_ticks=12, y_ticks=max(1, len(sorted_seqs) - 1),
         show_y_labels=False)
    for row_index, seq in enumerate(sorted_seqs):
        group = rising[
            (rising["seq"] == seq) &
            (rising["t_from_sync_us"] <= 1200)
        ]
        for edge_us in group["t_from_sync_us"]:
            px, py = xy(float(edge_us), row_index, 0, 1200,
                        -0.5, len(sorted_seqs) - 0.5)
            draw.ellipse([px - 3, py - 3, px + 3, py + 3],
                         fill="#1565c0")
        detected = result.loc[result["seq"] == seq, "tof_us"].iloc[0]
        px, py = xy(float(detected), row_index, 0, 1200,
                    -0.5, len(sorted_seqs) - 0.5)
        draw.line([(px - 7, py - 7), (px + 7, py + 7)],
                  fill="red", width=3)
        draw.line([(px - 7, py + 7), (px + 7, py - 7)],
                  fill="red", width=3)
        draw.text((MARGIN_L - 12, py), f"seq {seq}",
                  fill="black", font=font(15), anchor="rm")
    guard_x, _ = xy(200, 0, 0, 1200, -0.5, len(sorted_seqs) - 0.5)
    draw.line(
        [(guard_x, MARGIN_T), (guard_x, HEIGHT - MARGIN_B)],
        fill="black", width=2,
    )
    image.save(root / "CMP_OUTエッジ分布.png")

    assumptions = f"""# 前提条件

- 実施日時: 2026-07-27 19:12:25 JST
- 外部電源: ON
- TX: COM10
- RX / microSD: COM5
- TD04A: TX/RXとも水中
- 振動子間距離: 数cm（ユーザー申告、厳密値未計測）
- 水槽: 狭く、マルチパスの可能性あり
- 送信: 200kHz、5波、5秒間隔
- SYNC: TX GP14 → RX GP14
- RX: TH_PWM GP6 / ENV_ADC GP26 / CMP_OUT GP2
- TH_PWM: 自動調整、初期300mV
- ENV_ADC取得: SYNC後11ms、目標20us間隔
- 距離換算: 1500m/s、片道ToF
- 現行最小距離: 0.3m（200us未満のCMP_OUT立ち上がりを除外）
- 元ログ: `{root / "元ログ.csv"}`
- SD読出し: RXM001.CSV / RXR001.CSV / RXC001.CSV
"""
    (root / "前提条件.md").write_text(assumptions, encoding="utf-8")

    sd_report = f"""# SD照合結果

- 距離サマリ読出し: {len(sd_summary)}行
- ENV_ADC読出し: {len(adc_all)}行
- CMP_OUT読出し: {len(cmp_all)}行
- 今回対象の距離行: {len(result)}行
- SD内で完全一致した距離行: {sd_matches}/{len(result)}行
- 今回対象のENV_ADC行: {len(adc)}行
- 今回対象のCMP_OUTエッジ行: {len(cmp_data)}行
- 結果: {"PASS" if sd_matches == len(result) else "FAIL"}
"""
    (root / "SD照合結果.md").write_text(sd_report, encoding="utf-8")

    lines = [
        "# 簡易レポート",
        "",
        "## 結果",
        "",
        f"- 測定回数: {len(result)}回",
        f"- VALID率: {valid_rate:.1f}%",
        f"- ToF平均: {tof_mean:.1f}us",
        f"- ToF標本標準偏差: {tof_std:.1f}us",
        f"- 距離平均: {range_mean:.3f}m",
        f"- 距離標本標準偏差: {range_std:.3f}m",
        f"- 測定距離範囲: {result['range_m'].min():.3f}〜"
        f"{result['range_m'].max():.3f}m",
        f"- ENV_ADCピーク: {adc_stats['peak_adc_mv'].min():.0f}〜"
        f"{adc_stats['peak_adc_mv'].max():.0f}mV",
        f"- ENV_ADCサンプル間隔中央値: "
        f"{adc_stats['median_sample_us'].median():.1f}us",
        f"- CMP_OUT早期立ち上がり（200us未満）: {len(early)}件",
        f"- CMP_OUT測距窓立ち上がり（200us以上）: {len(ranged)}件",
        f"- SD距離行一致: {sd_matches}/{len(result)}",
        "",
        "## 判定",
        "",
        "- 測定回数5回は正式合格判定に必要な10回未満です。",
        "- VALID率100%は合格基準を満たします。",
        f"- 距離標準偏差{range_std:.3f}mは10cm基準を超えるため不合格です。",
        "- 実距離が数cmであるのに0.613〜0.974mを示しており、"
        "絶対距離は不整合です。",
        "- 現行コードは0.3m未満を対象外とし、200us未満のエッジを"
        "距離候補から除外します。今回の条件は仕様範囲外です。",
        "- ENV_ADCの主ピークは約108〜195us付近にあり、CMP_OUTにも"
        "200us未満の立ち上がりがあります。直達波または送信回り込みの"
        "候補を除外した後、後続の反射波を採用した可能性が高いです。",
        "",
        "## 次の確認事項",
        "",
        "- 振動子間距離を実測して記録する。",
        "- 無送信試験で200us未満のエッジが電気的回り込みか確認する。",
        "- 近距離測定を正式に対象とする場合、MIN_RANGE_Mとガード時間を"
        "実測に基づいて変更する。",
        "- 広い水槽または吸音条件で同じ測定を行い、マルチパスを比較する。",
        "",
        "## 診断表",
        "",
        "### ENV_ADC",
        "",
        dataframe_markdown(adc_stats),
        "",
        "### CMP_OUT",
        "",
        dataframe_markdown(cmp_stats),
        "",
    ]
    (root / "簡易レポート.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )

    print(f"VALID_RATE={valid_rate:.1f}%")
    print(f"RANGE_MEAN={range_mean:.3f}m RANGE_STD={range_std:.3f}m")
    print(f"SD_MATCH={sd_matches}/{len(result)}")
    print(f"REPORT_DIR={root.resolve()}")


if __name__ == "__main__":
    main()
