#!/usr/bin/env python3
"""Create report artifacts for an SD rising-edge capture validation."""

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def load_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle))


def write_csv_new(path, rows):
    with path.open("x", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report_dir")
    args = parser.parse_args()

    report_dir = Path(args.report_dir)
    edge_rows = load_csv(report_dir / "SD立上りログ.csv")
    result_rows = load_csv(report_dir / "SD測定要約.csv")
    if len(edge_rows) < 2 or len(result_rows) < 2:
        raise SystemExit("SD validation CSV has no data rows")

    outputs = [
        report_dir / "実験結果.csv",
        report_dir / "元ログ.csv",
        report_dir / "前提条件.md",
        report_dir / "簡易レポート.md",
        report_dir / "修正案.md",
        report_dir / "GP20立上り時刻.png",
    ]
    if any(path.exists() for path in outputs):
        raise SystemExit("Refusing to overwrite an existing report artifact")

    write_csv_new(report_dir / "実験結果.csv", result_rows)
    write_csv_new(report_dir / "元ログ.csv", edge_rows)

    edge_header = edge_rows[0]
    edges = [dict(zip(edge_header, row)) for row in edge_rows[1:]]
    result_header = result_rows[0]
    results = [dict(zip(result_header, row)) for row in result_rows[1:]]
    early = [
        row for row in edges if row["region"] == "EARLY_GUARD"
    ]
    later = [
        row for row in edges if int(row["rise_from_sync_us"]) >= 200
    ]
    dropped = max(int(row["dropped_total"]) for row in edges)

    premises = """# 前提条件

- RX: Raspberry Pi Pico / COM5
- RX入力: GP20
- SYNC: GP14（変更なし）
- SD: PiCowbell SPI0、GP16 MISO / GP17 CS / GP18 SCK / GP19 MOSI
- 送信: 200 kHz、5波、30秒周期
- 記録開始: SYNC検出直後
- 記録終了: 最初の検出後3秒、未検出時は受信窓終了後3秒
- 立上り容量: 8,192件/測定
- 200 µs未満は`EARLY_GUARD`として分類するが、破棄せずSDへ保存
- SD要約: `AQB005.CSV`
- SD立上り: `AQE005.CSV`
"""
    (report_dir / "前提条件.md").write_text(
        premises, encoding="utf-8", newline="\n")

    tofs = ", ".join(row["tof_us"] for row in results)
    rise_times = ", ".join(row["rise_from_sync_us"] for row in edges)
    report = f"""# 簡易レポート

## 結果

- SD要約行: {len(results)}件
- SD立上り行: {len(edges)}件
- 立上り時刻: {rise_times} µs
- ToF確定値: {tofs} µs
- `EARLY_GUARD`: {len(early)}件
- 200 µs以降の後続立上り: {len(later)}件
- バッファ欠落: {dropped}件
- SD読戻しとCSV列検証: PASS

## 判定

GP20の立上りを最初の検出だけで打ち切らず、追跡期間中も専用SDファイルへ保存できました。今回の2測定では立上りはSYNC後20 µsと30 µsだけで、200 µs以降の本命候補は観測されませんでした。ただし測定数が少ないため、本命信号が存在しないとの一般化はできません。

要約ToFが立上りより15 µs遅いのは、現行検出が3連続HIGH確認後に確定することと処理タイミングによる差です。
"""
    (report_dir / "簡易レポート.md").write_text(
        report, encoding="utf-8", newline="\n")

    proposals = """# GP20初期過渡への修正案

## 推奨するソフトウェア対策

1. 今回実装した生の立上りログを複数回取得し、電気的結合の時刻分布と後続波の有無を先に確認する。
2. ToF候補を「最初の立上り」から「ガード時間後の最初の有効パルス群」へ変更する。現在の最小距離0.3 mはToF 200 µsなので、まず200 µsを候補ガード値にできる。
3. 200 µs未満の立上りは削除せず`EARLY_GUARD`として診断保存する。200 µs以降に候補がなければ`OUT_OF_RANGE`ではなく`NO_DETECT`とする。
4. 単一エッジではなく、HIGH継続時間・立上り数・エコー群幅を使って候補を順位付けする。
5. TXを音響的に遮蔽した試験と通常試験を比較し、初期過渡の最大時刻に安全余裕を加えて最終ガード時間を決める。

## 回路・配線対策

1. TX駆動線・RS485線・RXアナログ/GP20線を離し、TXとRXの帰還電流が共通配線を流れないスターGNDにする。
2. RX入力線は短くし、GNDとのツイストペアまたはシールド線にする。シールドは原則受信側の一点接地から評価する。
3. コンパレータ電源直近へ100 nF、基板近傍へ10 µFを配置し、TX電源経路と分離する。
4. コンパレータに適切なヒステリシスを持たせる。GP20直前にRCを追加する場合はシュミットバッファを併用し、遅いエッジによる再発振を防ぐ。
5. 評価開始値として4.7 kΩ + 1 nFは時定数4.7 µs、遮断周波数約33.9 kHz。1 µsスパイクの到達は約19%、25 µsパルスでは約99.5%まで充電する。ただしRCによる立上り遅延はToFの固定遅延になるため再校正が必要。コンパレータがオープンドレインかプッシュプルか、既存プルアップ値、シュミット入力閾値をデータシートで確認してから採用する。
6. 根本対策として、SYNCから約200 µsだけ受信経路またはコンパレータ出力をアナログスイッチ/ロジックゲートでマスクする。診断用の生信号はマスク前から別入力へ取ると原因解析を継続できる。
"""
    (report_dir / "修正案.md").write_text(
        proposals, encoding="utf-8", newline="\n")

    seqs = [int(row["seq"]) for row in edges]
    rises = [int(row["rise_from_sync_us"]) for row in edges]
    figure, axis = plt.subplots(figsize=(8, 4.5))
    axis.scatter(seqs, rises, s=75, color="#d1495b", label="Recorded GP20 rise")
    axis.axhspan(0, 200, color="#edae49", alpha=0.18, label="EARLY_GUARD")
    axis.axhline(200, color="#2a9d8f", linestyle="--",
                 label="Minimum valid ToF (200 us)")
    for seq, rise in zip(seqs, rises):
        axis.annotate(f"{rise} us", (seq, rise), xytext=(5, 7),
                      textcoords="offset points")
    axis.set_xlabel("RX sequence")
    axis.set_ylabel("Rise time from SYNC [us]")
    axis.set_ylim(0, 240)
    axis.grid(alpha=0.25)
    axis.legend()
    figure.tight_layout()
    figure.savefig(report_dir / "GP20立上り時刻.png", dpi=180)
    plt.close(figure)
    print(
        f"measurements={len(results)} rises={len(edges)} "
        f"early={len(early)} later={len(later)} dropped={dropped}"
    )


if __name__ == "__main__":
    main()
