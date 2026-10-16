# テスト手順とカバレッジ（直結ベンチ）

仕様書19.1の合格基準を、どこで・どう確認するかをまとめる。
合格基準のうち **ログで自動判定できるもの** と **オシロ等で手動確認が必要なもの** を分離している。

## 構成前提（直結ベンチ）

`CLAUDE.md` の直結配線（回路なし）で実施する:

```
TX GP14 → RX GP14 (SYNC)
TX GP15 → RX GP15 (200kHz矩形波を直結)
TX GND  → RX GND
```

直結では音響飛行がないため、TX側で擬似飛行遅延 `SIM_FLIGHT_DELAY_US`
（`common/aquabeacon_config.h`）を入れて疑似的な距離を作る。

- 既定 `SIM_FLIGHT_DELAY_US = 2000` → `range ≈ 3.0 m`、`tof ≈ 2000us`
- ブランキング(200us)より十分後にパターンが届くので先頭ビット欠落が起きない

また直結では生の200kHzではなくコンパレータ出力相当のクリーンな信号を入れるため、
TX側で包絡線エミュレーション `TX_ENVELOPE_BENCH`（`common/aquabeacon_config.h`）を使う。

- `TX_ENVELOPE_BENCH = 1`（既定）: ONビットでGP15をHIGH固定 → RXのHigh数が 40/0 に
  分かれ、閾値 `COMP_HIGH_THRESH=20` が実回路同様に堅牢になる
- `TX_ENVELOPE_BENCH = 0`: 実200kHz矩形波出力（トランスデューサ駆動）

### 実機（実音響飛行・実回路）へ戻すとき

直結ベンチ用の2つのノブを必ず戻す:

| パラメータ | ベンチ | 実運用 |
|---|---|---|
| `SIM_FLIGHT_DELAY_US` | 2000 | 0 |
| `TX_ENVELOPE_BENCH` | 1 | 0 |

## 合格基準のカバレッジ

| 仕様19.1 基準 | 確認方法 | 自動 |
|---|---|---|
| パターン検出 近距離で90%以上VALID | `run_test.bat` → `analyze_log.py`（VALID率） | ✅ |
| score >= 7 | `analyze_log.py`（VALID行のmin score） | ✅ |
| tof 200〜10000us | `analyze_log.py`（TOF範囲） | ✅ |
| 距離再現性 std <= 10cm | `analyze_log.py`（range標準偏差） | ✅ |
| 無送信時にVALIDが出ない | `run_notx_test.bat` → `analyze_log.py --no-tx` | ✅ |
| 200kHz出力 ±2% | **オシロで手動確認**（GP15周波数） | ❌ 手動 |
| bit_time ±5% | **オシロで手動確認**（ONバースト幅 200us） | ❌ 手動 |

> 200kHz/bit_time の精度はシリアルログに現れないため、ループでは判定不能。
> 仕様書18.1（TX単体試験）に従いオシロで確認すること。

## 実行手順

### 1. 通常テスト（VALID率・スコア・TOF・再現性）

```bat
scripts\run_test.bat
```

ビルド→TX/RX書き込み→RX監視→`analyze_log.py` を一括実行。
`SIM_FLIGHT_DELAY_US` が有効なので、直結でも `range≈3m` でVALIDが出るはず。

### 2. 無送信（誤検出）テスト

```bat
scripts\run_notx_test.bat
```

TXの電源を切る（または信号線を外す）よう促されるので、その状態でRXを監視。
`analyze_log.py --no-tx` が **VALID=0 ならPASS**。
※RX入力がフローティングして誤検出するのを避けるため、必要なら入力をGNDへプルダウンする。

### 3. オシロ確認（手動・ループ外）

仕様書18.1 に従い、TX GP15 で以下を確認:
- 200kHz（周期5us）±2%
- duty 約50%
- `10110100` のON/OFFパターン
- 各ONバーストが bit_time=200us
- 送信後にOFFへ戻る

## 注意

- `analyze_log.py` は最低 `MIN_SAMPLES=10` 回を要求する（10回未満はFAIL）。
  `run_test.bat` の `MONITOR_SEC` は20Hz測距に対し十分長く取る（既定30秒）。

## 直結ベンチと実回路の信号の違い（重要）

実回路の信号経路は `BPF → 包絡線検波 → コンパレータ → GPIO`（仕様書8.1）。
コンパレータ出力は **ONバースト中ずっとHIGH / OFF中ずっとLOW** のクリーンな
デジタル信号で、1bitあたりHigh数は ≈40(ON)/≈0(OFF) に明確に分かれる。
よって閾値 `COMP_HIGH_THRESH=20` は実回路では十分に堅牢で、**生の200kHzがGPIOに
入ることはない**。

生の200kHz矩形波を直結すると、ONビットのHigh率が duty=50% 付近=20前後に乗り、
閾値ぎりぎりで不安定になりうる。これは実回路の欠陥ではなく、生の搬送波が
コンパレータ出力（包絡線）を模擬できていないことによる。

本プロジェクトではこれを避けるため、直結ベンチでは `TX_ENVELOPE_BENCH=1`（既定）で
TXがONビットにGP15をHIGH固定し、コンパレータ出力相当のクリーンなON/OFFを出す。
これによりHigh数が 40/0 に分かれ、閾値20が実回路同様に堅牢になる。
実200kHz出力で確認したいとき（オシロ等）は `TX_ENVELOPE_BENCH=0` にする。
