#!/usr/bin/env python3
"""Compare pool-test ADC strength and estimate the 5-25 m receive margin.

The propagation estimate is deliberately separated from the measured waveform
statistics.  The 1 m and 5 m peaks are compressed by the receiver output limit,
so fitting a path-loss exponent to those two peak values would be misleading.
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
import re
import statistics

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


FILE_RE = re.compile(r"S(\d{3})\.CSV", re.IGNORECASE)
DISTANCE_GROUPS = {
    "1m": range(6, 41),
    "5m": range(41, 74),
}
DIRECT_PRE_US = 20.0
DIRECT_POST_US = 500.0
BASELINE_GUARD_US = 100.0
BASELINE_START_US = 100.0
ENV_TO_ADC_RATIO = 2.0
AUTO_SEARCH_FLOOR_ENV_MV = 60.0
HYSTERESIS_ENV_MV = 22.0
CONSERVATIVE_TRIP_ENV_MV = AUTO_SEARCH_FLOOR_ENV_MV + HYSTERESIS_ENV_MV
FREQUENCY_KHZ = 200.0
ANCHOR_DISTANCE_M = 5.0
PREDICTION_DISTANCES_M = (5.0, 10.0, 15.0, 20.0, 25.0)
TEMPERATURES_C = (35.0, 40.0)
EXTRA_FADE_DB = (0.0, 6.0, 12.0)


def median(values: list[float]) -> float:
    if not values:
        raise ValueError("median requires at least one value")
    return statistics.median(values)


def mean(values: list[float]) -> float:
    if not values:
        raise ValueError("mean requires at least one value")
    return statistics.fmean(values)


def stdev(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) >= 2 else 0.0


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires at least one value")
    position = fraction * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def file_number(name: str) -> int:
    match = FILE_RE.fullmatch(name)
    if not match:
        raise ValueError(f"unsupported file name: {name}")
    return int(match.group(1))


def distance_label(name: str) -> str | None:
    number = file_number(name)
    for label, numbers in DISTANCE_GROUPS.items():
        if number in numbers:
            return label
    return None


def find_csv(report_dir: Path, required_columns: set[str]) -> Path:
    for path in report_dir.glob("*.csv"):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, [])
        if required_columns.issubset(header):
            return path
    raise FileNotFoundError(f"CSV with columns {sorted(required_columns)} not found")


def read_measurements(path: Path) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if distance_label(row["source_file"]) and row["status"] == "VALID":
                result[row["source_file"]] = row
    return result


def read_adc(path: Path) -> dict[str, list[tuple[float, float]]]:
    result: dict[str, list[tuple[float, float]]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if distance_label(row["source_file"]):
                result.setdefault(row["source_file"], []).append(
                    (float(row["t_from_sync_us"]), float(row["env_adc_mv"]))
                )
    return result


def integrate_positive(
    samples: list[tuple[float, float]], baseline_mv: float
) -> float:
    area = 0.0
    for (t0, v0), (t1, v1) in zip(samples, samples[1:]):
        y0 = max(0.0, v0 - baseline_mv)
        y1 = max(0.0, v1 - baseline_mv)
        area += 0.5 * (y0 + y1) * (t1 - t0)
    return area


def waveform_metrics(
    measurements: dict[str, dict[str, str]],
    adc: dict[str, list[tuple[float, float]]],
) -> list[dict[str, object]]:
    metrics: list[dict[str, object]] = []
    for source_file, measurement in sorted(
        measurements.items(), key=lambda item: file_number(item[0])
    ):
        tof_us = float(measurement["tof_us"])
        samples = adc[source_file]
        baseline = [
            value
            for time_us, value in samples
            if BASELINE_START_US
            <= time_us
            <= max(BASELINE_START_US, tof_us - BASELINE_GUARD_US)
        ]
        direct = [
            (time_us, value)
            for time_us, value in samples
            if tof_us - DIRECT_PRE_US
            <= time_us
            <= tof_us + DIRECT_POST_US
        ]
        if len(baseline) < 5 or len(direct) < 5:
            raise ValueError(f"insufficient waveform samples in {source_file}")
        baseline_median = median(baseline)
        baseline_p95 = percentile(baseline, 0.95)
        direct_values = [value for _, value in direct]
        peak_mv = max(direct_values)
        peak_excess_mv = peak_mv - baseline_median
        positive = [max(0.0, value - baseline_median) for value in direct_values]
        rms_excess_mv = math.sqrt(mean([value * value for value in positive]))
        area_mv_us = integrate_positive(direct, baseline_median)
        above_10 = [
            time_us
            for time_us, value in direct
            if value >= baseline_median + 10.0
        ]
        width_us = max(above_10) - min(above_10) if len(above_10) >= 2 else 0.0
        plateau_fraction = sum(
            value >= 0.95 * peak_mv for value in direct_values
        ) / len(direct_values)
        metrics.append(
            {
                "distance_label": distance_label(source_file),
                "source_file": source_file,
                "tof_us": tof_us,
                "baseline_median_mv": baseline_median,
                "baseline_p95_mv": baseline_p95,
                "direct_peak_mv": peak_mv,
                "peak_excess_mv": peak_excess_mv,
                "direct_rms_excess_mv": rms_excess_mv,
                "direct_area_mv_us": area_mv_us,
                "width_above_baseline_plus_10mv_us": width_us,
                "plateau_fraction_ge_95pct": plateau_fraction,
            }
        )
    return metrics


def aggregate_metrics(metrics: list[dict[str, object]]) -> list[dict[str, object]]:
    fields = (
        "baseline_median_mv",
        "baseline_p95_mv",
        "direct_peak_mv",
        "peak_excess_mv",
        "direct_rms_excess_mv",
        "direct_area_mv_us",
        "width_above_baseline_plus_10mv_us",
        "plateau_fraction_ge_95pct",
    )
    rows: list[dict[str, object]] = []
    for label in DISTANCE_GROUPS:
        group = [row for row in metrics if row["distance_label"] == label]
        row: dict[str, object] = {
            "distance_label": label,
            "valid_waveforms": len(group),
        }
        for field in fields:
            values = [float(item[field]) for item in group]
            row[f"{field}_mean"] = mean(values)
            row[f"{field}_median"] = median(values)
            row[f"{field}_std"] = stdev(values)
            row[f"{field}_min"] = min(values)
            row[f"{field}_max"] = max(values)
        rows.append(row)
    return rows


def sound_speed_freshwater_mps(temperature_c: float) -> float:
    """NPL/UNESCO shallow-water equation with salinity and depth set to zero."""
    t = temperature_c
    return 1402.5 + 5.0 * t - 0.0544 * t * t + 0.00021 * t * t * t


def pure_water_absorption_db_per_km(temperature_c: float) -> float:
    """Ainslie-McColm pure-water viscous term, f in kHz and depth=0."""
    return (
        0.00049
        * FREQUENCY_KHZ
        * FREQUENCY_KHZ
        * math.exp(-temperature_c / 27.0)
    )


def predictions(
    aggregate: list[dict[str, object]],
) -> tuple[list[dict[str, object]], float, float]:
    anchor = next(row for row in aggregate if row["distance_label"] == "5m")
    baseline_mv = float(anchor["baseline_median_mv_median"])
    excess_mv = float(anchor["peak_excess_mv_median"])
    rows: list[dict[str, object]] = []
    for temperature_c in TEMPERATURES_C:
        absorption_db_per_km = pure_water_absorption_db_per_km(temperature_c)
        for fade_db in EXTRA_FADE_DB:
            for distance_m in PREDICTION_DISTANCES_M:
                spreading_db = 20.0 * math.log10(
                    distance_m / ANCHOR_DISTANCE_M
                )
                absorption_db = (
                    absorption_db_per_km
                    * (distance_m - ANCHOR_DISTANCE_M)
                    / 1000.0
                )
                total_loss_db = spreading_db + absorption_db + fade_db
                predicted_excess_mv = excess_mv * 10.0 ** (-total_loss_db / 20.0)
                predicted_adc_peak_mv = baseline_mv + predicted_excess_mv
                predicted_env_peak_mv = ENV_TO_ADC_RATIO * predicted_adc_peak_mv
                margin_db = 20.0 * math.log10(
                    predicted_env_peak_mv / CONSERVATIVE_TRIP_ENV_MV
                )
                rows.append(
                    {
                        "temperature_c": temperature_c,
                        "sound_speed_mps": sound_speed_freshwater_mps(
                            temperature_c
                        ),
                        "extra_fade_db": fade_db,
                        "distance_m": distance_m,
                        "spreading_loss_from_5m_db": spreading_db,
                        "absorption_loss_from_5m_db": absorption_db,
                        "total_loss_from_5m_db": total_loss_db,
                        "anchor_5m_baseline_adc_mv": baseline_mv,
                        "anchor_5m_excess_adc_mv": excess_mv,
                        "predicted_excess_adc_mv": predicted_excess_mv,
                        "predicted_adc_peak_mv": predicted_adc_peak_mv,
                        "predicted_env_peak_mv": predicted_env_peak_mv,
                        "conservative_trip_env_mv": CONSERVATIVE_TRIP_ENV_MV,
                        "margin_db": margin_db,
                        "predicted_crossing": predicted_env_peak_mv
                        >= CONSERVATIVE_TRIP_ENV_MV,
                    }
                )
    return rows, baseline_mv, excess_mv


def temperature_sensitivity(
    measurements_path: Path,
) -> list[dict[str, object]]:
    groups: dict[str, list[float]] = {"1m": [], "5m": []}
    with measurements_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            label = distance_label(row["source_file"])
            if label and row["status"] == "VALID":
                groups[label].append(float(row["range_m"]))
    result: list[dict[str, object]] = []
    for label, stored_ranges in groups.items():
        expected = float(label.removesuffix("m"))
        for temperature_c in TEMPERATURES_C:
            speed = sound_speed_freshwater_mps(temperature_c)
            corrected = [value * speed / 1500.0 for value in stored_ranges]
            result.append(
                {
                    "distance_label": label,
                    "temperature_c": temperature_c,
                    "sound_speed_mps": speed,
                    "valid_count": len(corrected),
                    "corrected_mean_m": mean(corrected),
                    "corrected_std_m": stdev(corrected),
                    "corrected_min_m": min(corrected),
                    "corrected_max_m": max(corrected),
                    "mean_error_m": mean(corrected) - expected,
                }
            )
    return result


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError(f"no rows for {path}")
    with path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def plot_comparison(
    output: Path, metrics: list[dict[str, object]]
) -> None:
    labels = list(DISTANCE_GROUPS)
    groups = {
        label: [row for row in metrics if row["distance_label"] == label]
        for label in labels
    }
    fig, axes = plt.subplots(1, 3, figsize=(12.5, 4.2))
    plots = (
        ("peak_excess_mv", "Direct peak above baseline", "ADC voltage (mV)"),
        ("direct_area_mv_us", "Direct-window envelope area", "Area (mV us)"),
        (
            "width_above_baseline_plus_10mv_us",
            "Envelope duration",
            "Duration (us)",
        ),
    )
    for axis, (field, title, ylabel) in zip(axes, plots):
        values = [[float(row[field]) for row in groups[label]] for label in labels]
        axis.boxplot(values, tick_labels=labels, showmeans=True)
        axis.set_title(title)
        axis.set_xlabel("Test distance")
        axis.set_ylabel(ylabel)
        axis.grid(axis="y", alpha=0.25)
    fig.suptitle("Pool test: ADC envelope strength (VALID shots only)")
    fig.tight_layout()
    fig.savefig(output, dpi=180)
    plt.close(fig)


def plot_prediction(output: Path, rows: list[dict[str, object]]) -> None:
    fig, axis = plt.subplots(figsize=(8.5, 5.0))
    colors = {0.0: "#176b87", 6.0: "#d97706", 12.0: "#b42318"}
    for fade_db in EXTRA_FADE_DB:
        selected = [
            row
            for row in rows
            if row["temperature_c"] == 35.0
            and row["extra_fade_db"] == fade_db
        ]
        axis.plot(
            [float(row["distance_m"]) for row in selected],
            [float(row["predicted_env_peak_mv"]) for row in selected],
            marker="o",
            color=colors[fade_db],
            label=f"Spherical spreading + {fade_db:.0f} dB extra fade",
        )
    axis.axhline(
        CONSERVATIVE_TRIP_ENV_MV,
        color="#222222",
        linestyle="--",
        label="Conservative trip level (60 + 22 mV)",
    )
    axis.set_xlabel("Distance (m)")
    axis.set_ylabel("Predicted ENV peak (mV)")
    axis.set_title("200 kHz receive-level projection anchored to measured 5 m ADC")
    axis.set_xlim(5, 25)
    axis.set_ylim(bottom=0)
    axis.grid(alpha=0.25)
    axis.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output, dpi=180)
    plt.close(fig)


def write_assessment(
    path: Path,
    aggregate: list[dict[str, object]],
    prediction_rows: list[dict[str, object]],
) -> None:
    one = next(row for row in aggregate if row["distance_label"] == "1m")
    five = next(row for row in aggregate if row["distance_label"] == "5m")
    nominal_25 = next(
        row
        for row in prediction_rows
        if row["temperature_c"] == 35.0
        and row["extra_fade_db"] == 0.0
        and row["distance_m"] == 25.0
    )
    fade6_25 = next(
        row
        for row in prediction_rows
        if row["temperature_c"] == 35.0
        and row["extra_fade_db"] == 6.0
        and row["distance_m"] == 25.0
    )
    fade12_25 = next(
        row
        for row in prediction_rows
        if row["temperature_c"] == 35.0
        and row["extra_fade_db"] == 12.0
        and row["distance_m"] == 25.0
    )
    c35 = sound_speed_freshwater_mps(35.0)
    c40 = sound_speed_freshwater_mps(40.0)
    text = f"""# ADC信号強度比較と25m受信見通し

## 結論

- VALID時のENV_ADCピーク中央値は、1mで{float(one['direct_peak_mv_median']):.1f}mV、5mで{float(five['direct_peak_mv_median']):.1f}mVだった。距離が5倍でも同等なのは、受信信号が減衰していないからではなく、NJM2137の出力スイングと包絡線回路により約0.29V付近で圧縮されている可能性が高い。
- したがって1m/5mのピーク比から減衰係数を回帰することはできない。25m予測は、5mの観測値を起点に「球面拡散（圧力1/r）+ 純水の200kHz吸収」で計算した。
- 25mの名目予測はENVピーク{float(nominal_25['predicted_env_peak_mv']):.1f}mV、保守的な発火レベル82mVに対する余裕{float(nominal_25['margin_db']):.1f}dBで、理想的な配置なら検出可能性はある。
- しかし追加フェージング6dBでは{float(fade6_25['predicted_env_peak_mv']):.1f}mV（余裕{float(fade6_25['margin_db']):.1f}dB）、12dBでは{float(fade12_25['predicted_env_peak_mv']):.1f}mV（余裕{float(fade12_25['margin_db']):.1f}dB）となる。今回1m/5mでもNO_DETECTが多いため、現構成で25mを安定検出できるとはまだ判定できない。

## ADC波形の比較

VALID波形だけを使い、到来時刻の100us以上前を基線、到来20us前から500us後までを直接波窓として集計した。

| 指標 | 1m | 5m |
|---|---:|---:|
| VALID波形数 | {int(one['valid_waveforms'])} | {int(five['valid_waveforms'])} |
| 基線中央値の中央値 [mV] | {float(one['baseline_median_mv_median']):.2f} | {float(five['baseline_median_mv_median']):.2f} |
| 直接波ピーク中央値 [mV] | {float(one['direct_peak_mv_median']):.1f} | {float(five['direct_peak_mv_median']):.1f} |
| 基線超過ピーク中央値 [mV] | {float(one['peak_excess_mv_median']):.1f} | {float(five['peak_excess_mv_median']):.1f} |
| 直接波RMS超過値中央値 [mV] | {float(one['direct_rms_excess_mv_median']):.1f} | {float(five['direct_rms_excess_mv_median']):.1f} |
| 包絡線面積中央値 [mV·us] | {float(one['direct_area_mv_us_median']):.0f} | {float(five['direct_area_mv_us_median']):.0f} |
| 基線+10mV以上の幅中央値 [us] | {float(one['width_above_baseline_plus_10mv_us_median']):.0f} | {float(five['width_above_baseline_plus_10mv_us_median']):.0f} |

ピークは圧縮されているが、面積や幅は反射、振動子のリンギング、姿勢にも依存する。今回の1mと5mは同一条件の距離掃引ではなく、VALIDになった期間だけを比較しているため、これらも送受信感度の絶対校正値には使えない。

## 25mまでの推定方法

- 基準: 5m VALID波形の基線中央値と基線超過ピーク中央値。
- 拡散: 自由音場の球面拡散として、音圧振幅を距離に反比例（5m→25mで1/5、約-14.0dB）。
- 吸収: Ainslie–McColmの純水粘性項。35℃・200kHzでは5m→25mの追加損失は約{pure_water_absorption_db_per_km(35.0) * 0.020:.3f}dBで、幾何拡散より十分小さい。
- 受信判定: ファームウェアの自動探索下限60mV（ENV側）に、基板仕様のヒステリシス22mVを全量加えた82mVを保守的な発火レベルとした。実際のヒステリシス遷移点は状態により変わるため、この扱いは安全側。
- 追加フェージング: 表面・底面反射、振動子の指向性、姿勢ずれを一つの損失として0/6/12dBの3ケースで示した。

## 水温の不確かさ

浅水・淡水としてNPL式を用いると音速は35℃で{c35:.1f}m/s、40℃で{c40:.1f}m/sとなる。水温が35℃より高ければ、1500m/sで保存した距離はさらに上方補正される。35→40℃の音速差は約{100.0 * (c40 / c35 - 1.0):.2f}%なので、5m級では数cmの差であり、信号強度予測への影響は吸収差も含めて小さい。一方、正確な距離校正には次回、振動子深度の水温を同時記録する必要がある。

## 現構成で25mを捉えられるか

判定は **INCONCLUSIVE（可能性はあるが、安定検出は未証明）**。

名目モデルでは25mでも閾値を超える。ただし今回のVALID率は1m 31.4%、5m 39.4%で、NO_DETECT時はCMP_OUT立上りがゼロだった。これは距離減衰以前に、姿勢・指向性・マルチパスのヌル・利得設定・閾値探索開始条件のどれかで信号を取り逃していることを示す。さらに、約0.29Vのピーク圧縮により実際の5m受信余裕を測定できていない。

25m実験前に優先する確認:

1. RX基板の2個の利得トリマ位置を記録し、200kHz・5波バーストを既知振幅で入力して、ENV_ADCが圧縮し始める入力電圧と最小検出入力を実測する。
2. 5mで送受信器を固定治具に載せ、角度を変えずに30回以上測り、閾値60mV到達後のVALID率を確認する。
3. 10m、15m、20m、25mの順に距離を伸ばし、各距離でENV_ADCピークが圧縮域を抜けた後の実減衰を取得する。
4. 25m開始時に閾値300mVからNO_DETECTが続くと、20mV/送信ずつ60mVまで下げるのに12送信、5秒周期で約60秒かかる。評価区間は探索完了後から数える。

## 参照

- NPL, Speed of sound in sea-water: https://resource.npl.co.uk/acoustics/techguides/soundseawater/underlying-phys.html
- U.S. Navy, Fundamentals of Naval Weapons Systems, underwater propagation: https://man.fas.org/dod-101/navy/docs/fun/part08.htm
- Ainslie and McColm (1998), JASA 103(3), DOI 10.1121/1.421258: https://marineinfo.org/en/doc/publication/254927
"""
    path.write_text(text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("report_dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report_dir = args.report_dir.resolve()
    measurement_path = find_csv(
        report_dir, {"source_file", "tof_us", "range_m", "status"}
    )
    adc_path = find_csv(
        report_dir, {"source_file", "t_from_sync_us", "env_adc_mv"}
    )
    measurements = read_measurements(measurement_path)
    adc = read_adc(adc_path)
    metrics = waveform_metrics(measurements, adc)
    aggregate = aggregate_metrics(metrics)
    prediction_rows, baseline_mv, excess_mv = predictions(aggregate)
    temperature_rows = temperature_sensitivity(measurement_path)

    assert len(metrics) == 24, f"expected 24 VALID waveforms, got {len(metrics)}"
    assert 0.0 < baseline_mv < 30.0
    assert 200.0 < excess_mv < 330.0
    assert 1510.0 < sound_speed_freshwater_mps(35.0) < 1530.0
    assert 0.0 < pure_water_absorption_db_per_km(35.0) < 10.0
    assert all(
        float(row["predicted_env_peak_mv"]) > 0.0 for row in prediction_rows
    )

    write_csv(report_dir / "ADC信号強度_波形別.csv", metrics)
    write_csv(report_dir / "ADC信号強度比較.csv", aggregate)
    write_csv(report_dir / "25m信号強度予測.csv", prediction_rows)
    write_csv(report_dir / "水温感度_35_40C.csv", temperature_rows)
    plot_comparison(report_dir / "ADC信号強度比較.png", metrics)
    plot_prediction(report_dir / "25m信号強度予測.png", prediction_rows)
    write_assessment(
        report_dir / "信号強度_25m評価.md", aggregate, prediction_rows
    )
    print(f"report_dir={report_dir}")
    print(f"valid_waveforms={len(metrics)}")
    print(f"anchor_5m_baseline_adc_mv={baseline_mv:.3f}")
    print(f"anchor_5m_excess_adc_mv={excess_mv:.3f}")
    for fade_db in EXTRA_FADE_DB:
        row = next(
            item
            for item in prediction_rows
            if item["temperature_c"] == 35.0
            and item["extra_fade_db"] == fade_db
            and item["distance_m"] == 25.0
        )
        print(
            f"25m_fade_{fade_db:.0f}dB_env_mv="
            f"{float(row['predicted_env_peak_mv']):.3f},"
            f"margin_db={float(row['margin_db']):.3f},"
            f"crossing={row['predicted_crossing']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
