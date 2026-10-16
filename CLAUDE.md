# AquaBeacon TX/RX プロジェクト

## First Steps
作業を始める前に、毎回以下を必ず読むこと。
1. `AGENTS.md`（作業ルール・完了条件）
2. `AquaBeacon_仕様書.md`（信号・距離計算・合格基準の一次仕様）
3. `common/aquabeacon_config.h`（全パラメータの唯一の定義場所）

## プロジェクト概要
Raspberry Pi Pico 2台を用いた200kHz音響測距システム。
TX PicoがON/OFFパターン変調した200kHzバーストを送信し、RX Picoが受信・距離計算する。

## 環境
- OS: Windows 11
- ビルドツール: PlatformIO (VS Code拡張 + CLI両方)
- 言語: C/C++ (Arduino framework for Pico)
- シリアル通信: 115200bps

## COMポート設定（実機接続後に変更すること）
TX_PORT=COM3
RX_PORT=COM4

確認方法: デバイスマネージャー →「ポート (COMとLPT)」→ "USB Serial Device (COMx)"

## ディレクトリ構成
```
aquabeacon/
├── CLAUDE.md               ← このファイル（必ず最初に読む）
├── tx/
│   ├── platformio.ini      ← TX用PlatformIO設定
│   └── src/
│       └── main.cpp        ← TX実装
├── rx/
│   ├── platformio.ini      ← RX用PlatformIO設定
│   └── src/
│       └── main.cpp        ← RX実装
├── common/
│   └── aquabeacon_config.h ← 共通パラメータ
├── scripts/
│   ├── flash_tx.bat        ← TX書き込み
│   ├── flash_rx.bat        ← RX書き込み
│   ├── monitor_rx.bat      ← RXシリアル監視
│   └── analyze_log.py      ← ログ解析・合否判定
└── logs/                   ← 測定ログ保存先
```

## ビルドコマンド
```
# TX
cd tx && pio run

# RX
cd rx && pio run
```

## 書き込みコマンド
```
# TX (COMポートは実際の値に変更)
cd tx && pio run -t upload --upload-port COM3

# RX
cd rx && pio run -t upload --upload-port COM4
```

## シリアルモニター
```
pio device monitor --port COM4 --baud 115200
```

## 自動テストスクリプト
```
scripts\run_test.bat
```
このスクリプトがビルド→書き込み→監視→解析→合否判定を一括実行する。

## 実機実験レポート（実験ごとに必須）

実機実験を行った場合は、成功・失敗・中断にかかわらず、実験ごとに新しいレポートを作成する。
詳細な規則は `AGENTS.md` の「Experiment Report Artifacts」を正とし、この節は実作業時の手順と
形式を示す。ビルドのみ、静的解析のみの場合は実験レポートの対象外とする。

### 保存先

実験開始時に次の一意なフォルダを作成する。

```text
reports/YYYYMMDD_HHMMSS_<実験名>/
```

同名が存在する場合は `_01`、`_02` のような連番を付け、既存ファイルを上書きしない。

### 必須ファイル

```text
reports/YYYYMMDD_HHMMSS_<実験名>/
├── 実験結果.csv
├── 元ログ.csv
├── 前提条件.md
├── 簡易レポート.md
├── SD読出し.csv        # SDカードを使用した場合
└── *.png               # 複数の数値データがある場合
```

- `実験結果.csv`: 解析対象データ。RXの既定CSV列名・列順は変更しない
- `元ログ.csv`: 診断行を含む取得ログ
- `前提条件.md`: 日時、目的、配線、GPIO、電源、設定、送信回数と間隔、COMポート、
  SDカード状態、既知の制約、元ログのパス
- `簡易レポート.md`: 結果、判定、異常、次の確認事項
- `SD読出し.csv`: 実験後に実際のSDカードから読み戻した内容
- `*.png`: ToF、距離、score、High率などの比較可能なデータを可視化したグラフ

### 実施順

1. 実験フォルダを新規作成し、`前提条件.md` に実験前の条件を記録する
2. シリアル等の元ログを保存し、解析対象を `実験結果.csv` に保存する
3. SDカードを使用した場合は、実カードからファイルを読み戻して `SD読出し.csv` に保存する
4. 測定行数、VALID率、status、ToF、距離、score、SD読出し件数を集計する
5. 複数の数値データがあれば適切なグラフを作り、画像を目視確認する
6. `簡易レポート.md` を作成し、`PASS` / `FAIL` / `INCONCLUSIVE` を明記する

シリアルにSD書込み成功と表示されただけでは、SD保存の検証完了としない。
SD内に同名ファイルがある場合は上書きせず、SD側のファイル名最大長を守った短い連番名を使う。
読出し不能や実験中断時は成功扱いにせず、理由と次の確認方法を残す。

### `簡易レポート.md` の形式

```md
# 簡易レポート

## 目的

## 実施内容

## 結果

## 判定

## 異常・考察

## 次の確認事項
```

結果には、送信予定・実績回数、受信件数、status別件数、VALID率、ToF・距離・scoreの統計、
TX送信完了、外部電源のみでの起動、SD書込み・読出し結果を、該当する範囲で含める。
複数回測定では平均、標準偏差、最小、最大を記載する。

---

## 信号仕様（仕様書より）

### 搬送波
- 周波数: 200kHz
- 波形: 50% duty 矩形波

### ON/OFFパターン
- pattern = 10110100 (8bit)
- bit_time = 200us
- tx_duration = 1600us (8bit × 200us)

### SYNC信号
- TX GP14 → RX GP14
- pulse width >= 10us, 3.3V

### ピン割り当て
| 機能 | TX GPIO | RX GPIO |
|------|---------|---------|
| 200kHz出力 | GP15 | - |
| SYNC | GP14(出力) | GP14(入力) |
| コンパレータ入力 | - | GP15(デジタル入力) |
| UART TX | GP0 | GP0 |
| LED | GP25 | GP25 |

## 測距方式の切替（common/aquabeacon_config.h）
- `RANGING_MODE_BURST5 = 1` … 5波バースト方式（現在の実験方式）
  - TX: `tx/src/burst5.cpp`（SYNC→5波25us送信）
  - RX: `rx/src/burst5.cpp`（SYNC受信→ブランキング→初到達エッジ→ToF/距離）
  - 相関検出は使わず、最初のHIGH（DETECT_CONFIRM_N=3連続）を初到達とする
- `RANGING_MODE_BURST5 = 0` … 8bitパターン方式（`tx/rx` の main.cpp、従来どおり残置）

## 実機配線（5波・音響飛行）
TX GP14 → RX GP14 (SYNC, 時刻基準)
TX GP15 → ゲートドライバ → MOSFET → TD04A(送信)   ※GPIO直結不可
TD04A(受信) → 保護 → 増幅 → 200kHz BPF → 包絡線検波 → コンパレータ → RX GP15
TX GND  ↔ RX GND （必ず共通化。アナログ前段GNDの回り込みに注意）
※LEDは Pico 2 W では LED_BUILTIN を使用（GP25直では光らない）

## 直結テスト時の配線（回路なし・8bitパターン方式向け）
TX GP14 → RX GP14 (SYNC)
TX GP15 → RX GP15 (200kHz矩形波をデジタル入力へ直結)
TX GND  → RX GND

---

## 初期パラメータ（仕様書17章）
```
carrier_freq    = 200000 Hz
pattern         = 10110100 (binary)
bit_time        = 200 us
tx_duration     = 1600 us
blanking_time   = 200 us
sound_speed     = 1500.0 m/s
max_range       = 30.0 m（25m実験 + 5m余裕）
min_range       = 0.3 m
receive_margin  = 2000 us
range_rate      = 20 Hz (50ms周期)
detect_score_valid      = 7
detect_score_tentative  = 6
search_step     = 20 us
```

---

## RXログ形式（CSV）
```
seq,t_sync_us,t_detect_us,tof_us,range_m,score,rx_peak,noise,status
12,1050000,1053420,3420,5.13,7,2380,120,VALID
```

## テスト合格基準（仕様書19章）
- VALID率 >= 90% (10回中9回以上)
- score >= 7
- tof_us が物理的妥当範囲: 200us〜20000us (0.3m〜30m相当)
- 無送信時にVALIDが出ないこと

---

## Claude Codeへの作業指示

### 初回セットアップ時
1. common/aquabeacon_config.h を作成する
2. tx/src/main.cpp を実装する
3. rx/src/main.cpp を実装する
4. 各platformio.ini を作成する
5. scripts/ 以下のbat/pyスクリプトを作成する

### テストループ時
1. `scripts\run_test.bat` を実行する
2. logs/ に出力されたCSVを analyze_log.py で解析する
3. FAILの場合、エラー内容を読んでコードを修正し再度実行する
4. PASSするまで繰り返す

### 修正時の注意
- パラメータ変更は必ず common/aquabeacon_config.h で行う
- main.cppに直接数値を書かない
- シリアル出力のCSV形式を変えない（analyze_log.pyが依存する）
