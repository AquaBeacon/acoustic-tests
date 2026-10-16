# AGENTS.md

このファイルは AI エージェント（Claude Code / Codex 等）がこのリポジトリで作業するときの
前提・制約・確認手順・完了条件を定義します。作業前に必ず読んでください。

---

## Project Overview

- **Project name**: AquaBeacon
- **Purpose**: Raspberry Pi Pico 2台による200kHz音響測距システム
- **対象ハード**: Raspberry Pi Pico (RP2040) × 2台（TX / RX）
- **開発環境**: Windows 11 / PlatformIO / VS Code
- **言語**: C/C++ (Arduino framework for Pico), Python (ログ解析)
- **実機通信**: USB-シリアル 115200bps

---

## First Steps

作業を始める前に、まず以下を確認してください。

1. `AquaBeacon_仕様書.md`（信号・距離計算・合格基準の一次仕様＝正）
2. `common/aquabeacon_config.h`（全パラメータの唯一の定義場所＝値の正）
3. `CLAUDE.md`（作業ルール・ピン配置の補足。仕様書とconfig.hに従属）
4. `tx/platformio.ini` / `rx/platformio.ini`
5. `tx/src/main.cpp` / `rx/src/main.cpp`
6. `scripts/run_test.bat`（テストループの流れ）
7. `logs/`（直近のCSVログがあれば読む）

実装前に以下を短く整理してください。

- 変更対象ファイルと影響範囲
- パラメータ変更が必要かどうか
- ハードウェア制約（タイミング・GPIO・割り込み）に矛盾しないか
- 不明点

---

## Core Rules

- **パラメータは `common/aquabeacon_config.h` だけで定義する。main.cpp に数値を直書きしない**
- **RXログのCSV列順・列名を変えない**（`analyze_log.py` が依存する）
- 既存の状態機械の構造・命名を尊重する
- 依頼された範囲に集中し、関係ない変更を避ける
- 実在しないコマンドを推測で実行しない（`platformio.ini` と `scripts/` を確認する）
- ハードウェア定数（GPIO番号・周波数・タイミング）を変更する場合は必ず理由を明示する
- 変更はできるだけ小さく、レビューしやすい単位にする
- 最後に、変更内容・検証結果・残課題を報告する

---

## Human Intent

人間が主に指定するべき内容は以下です。

- どの状態（TX / RX / 両方）を変更するか
- 何を実現したいか（例: パターン長変更、スコア閾値調整、PIO移行）
- どの状態になれば完了か（例: VALID率90%以上）
- 現在の症状・ログの異常（FAILの場合は直近CSVを添付すること）
- 変更してよい範囲

目的や完了条件が曖昧で実装方針に大きな差が出る場合は、推測で進めず質問してください。

---

## Tech Stack

- **MCU**: Raspberry Pi Pico (RP2040, 125MHz)
- **Framework**: Arduino for Pico (earlephilhower/arduino-pico)
- **Build tool**: PlatformIO CLI (`pio run`)
- **書き込み**: `pio run -t upload --upload-port COMx`
- **シリアル監視**: Python / pyserial (`scripts/serial_monitor.py`)
- **ログ解析**: Python 3 (`scripts/analyze_log.py`)
- **OS**: Windows 11
- **Shell**: PowerShell / コマンドプロンプト（バッチファイル `.bat`）

---

## Repository Structure

```
aquabeacon/
├── AGENTS.md               ← このファイル
├── CLAUDE.md               ← Claude Code 向け補足（技術仕様・ピン・パラメータ）
├── common/
│   └── aquabeacon_config.h ← 全パラメータ・ピン定義（唯一の真実）
├── tx/
│   ├── platformio.ini
│   └── src/main.cpp        ← TX状態機械
├── rx/
│   ├── platformio.ini
│   └── src/main.cpp        ← RX状態機械
├── scripts/
│   ├── run_test.bat        ← ビルド→書き込み→監視→解析の一括実行
│   ├── flash_tx.bat        ← TX単体書き込み
│   ├── flash_rx.bat        ← RX単体書き込み
│   ├── serial_monitor.py   ← シリアル受信・CSV保存
│   ├── analyze_log.py      ← ログ解析・合否判定
│   └── requirements.txt
└── logs/                   ← 測定ログCSV（git管理外推奨）
```

---

## Commands

```bat
REM TX ビルド
cd tx && pio run

REM RX ビルド
cd rx && pio run

REM TX 書き込み（COMポートは実機に合わせて変更）
cd tx && pio run -t upload --upload-port COM3

REM RX 書き込み
cd rx && pio run -t upload --upload-port COM4

REM シリアル監視（30秒・CSV保存）
python scripts\serial_monitor.py --port COM4 --duration 30

REM 一括テストループ
scripts\run_test.bat

REM ログ解析単体
python scripts\analyze_log.py logs\rx_xxxxxxxx.csv

REM Python依存インストール
pip install -r scripts\requirements.txt
```

存在しないコマンドは実行しないでください。

---

## Hardware Constraints

ハードウェアに関わる変更では以下を守ってください。

- **GPIO電圧は3.3V**。5V信号をGPIOに直結しない
- **TX GP15（200kHz PWM出力）とRX GP15（デジタル入力）は別Picoのピン**
- **SYNC線（TX GP14 → RX GP14）はGND共通が必要**
- `analogWrite` / `analogWriteFreq` はArduino-Pico固有API。Pico SDK直接呼び出しと混在させない
- タイミングクリティカルな処理（`delayMicroseconds`）はloop()の外で呼ばない（他の状態遷移をブロックする）
- `micros()` のオーバーフロー（約70分）を考慮する（差分演算で対処済みであること）
- PIO移行は `CLAUDE.md` の「今後の拡張」に従い、明示的な依頼があった場合のみ行う

---

## Timing Parameters (Read-Only Reference)

変更する場合は `common/aquabeacon_config.h` を編集し、ここは変更しない。

| パラメータ | 値 | 備考 |
|-----------|-----|------|
| 搬送波 | 200kHz | GP15 PWM出力 |
| bit_time | 200us | 1bitあたりの送信時間 |
| pattern | 10110100 | 8bit, MSBファースト |
| tx_duration | 1600us | 8bit × 200us |
| blanking_time | 200us | 受信無効化時間 |
| sound_speed | 1500.0 m/s | 水中（初期値） |
| max_range | 30.0 m | 25m実験 + 5m余裕 |
| min_range | 0.3 m | |
| range_rate | 20 Hz | 送信周期50ms |
| detect_score_valid | 7 | >= 7でVALID |
| search_step | 20 us | スライディング探索ステップ |
| COMP_SAMPLE_US | 5 us | コンパレータサンプリング間隔 |
| COMP_HIGH_THRESH | 20 | 40サンプル中High>=20でbit=1 |

---

## Log Format (Do Not Change)

RXシリアル出力のCSV形式。`analyze_log.py` が依存するため列順・列名を変えない。

```
seq,t_sync_us,t_detect_us,tof_us,range_m,score,rx_peak_pct,noise_high_pct,status
12,1050000,1053420,3420,5.13,7,85,3,VALID
```

| 列 | 内容 |
|----|------|
| seq | 測定シーケンス番号 |
| t_sync_us | SYNC検出時刻 [us] |
| t_detect_us | パターン検出時刻 [us] |
| tof_us | 飛行時間 [us] |
| range_m | 推定距離 [m] |
| score | パターン一致スコア (0〜8) |
| rx_peak_pct | ONビット最大High率 [%] |
| noise_high_pct | ノイズ区間High率 [%] |
| status | VALID / TENTATIVE / INVALID / NO_DETECT / OUT_OF_RANGE / NOISE_HIGH / SATURATED |

TXログ形式（参考）:
```
seq,t_tx_us,pattern,bit_time_us,status
12,1050000,10110100,200,TX_DONE
```

---

## Testing and Verification

変更後は以下の順で確認してください。

1. **ビルド確認**: `cd tx && pio run` / `cd rx && pio run`（エラーゼロであること）
2. **書き込み**: `scripts\flash_tx.bat` / `scripts\flash_rx.bat`
3. **一括テスト**: `scripts\run_test.bat`
4. **ログ解析**: `python scripts\analyze_log.py logs\最新ファイル.csv`

### Experiment Report Artifacts（実機実験ごとに必須）

実機で書き込み・送受信・電源投入・配線変更・SD保存確認・無送信確認などを行った場合は、
成否にかかわらず実験ごとにレポートを作成する。途中で中断した場合も、取得済みデータを残し、
判定を `INCONCLUSIVE` として中断理由を記録する。ビルドのみ、静的解析のみの場合は対象外とする。

#### 保存先と命名

- 実験開始時に `reports/YYYYMMDD_HHMMSS_<実験名>/` を新規作成する
- 同名フォルダが存在する場合は `_01`、`_02` のような連番を付ける
- 既存のレポート、ログ、グラフは削除・上書きしない
- フォルダ名とファイル名は、SDカード側のファイル名制限とは分けて管理する

#### 必須成果物

各実験フォルダには最低限、以下を保存する。

```text
reports/YYYYMMDD_HHMMSS_<実験名>/
├── 実験結果.csv
├── 元ログ.csv
├── 前提条件.md
├── 簡易レポート.md
├── SD読出し.csv        # SDカードを使用した場合
└── *.png               # 複数の数値データがある場合
```

- `実験結果.csv`: 解析対象となる測定結果。RX測定結果では既定の列名・列順を維持する
- `元ログ.csv`: 診断行を含む取得ログ。変換前の非CSVログがある場合は別ファイルで併存させる
- `前提条件.md`: 実験日時とタイムゾーン、目的、配線とGPIO、電源状態、TX/RXファームウェア設定、
  送信回数と間隔、TX/RXのCOMポート、SDカード状態、既知の制約、元ログのパスを記載する
- `簡易レポート.md`: 下記の見出しを使用し、結果と判断根拠を簡潔に記載する
- `SD読出し.csv`: SDカードへ保存した場合、実験後に実カードから読み戻した内容を保存する
- グラフ: 複数の数値データがある場合に作成し、PNGで保存する

`簡易レポート.md` は次の形式とする。

```md
# 簡易レポート

## 目的

## 実施内容

## 結果

## 判定

## 異常・考察

## 次の確認事項
```

#### 結果と判定に含める内容

- 送信予定回数、実送信回数、受信件数、status別件数
- VALID率、score、ToF、距離。複数回測定では平均、標準偏差、最小、最大
- TXの送信完了状態と、外部電源のみで起動・送信できたか
- SDへの書き込み件数、実カードからの読出し件数、ファイル名、書込み・読出しエラー
- 判定は仕様書の合格基準に基づき `PASS` / `FAIL` / `INCONCLUSIVE` のいずれかを明記する
- 未確認項目を成功扱いにしない。確認できなかった理由と次の確認方法を記載する

#### SDカード検証

SDカードを使用した実験では、シリアル出力だけで保存成功と判断してはならない。
実験後にSDカード内の対象ファイルを実際に読み取り、期待した測定行数・内容と照合する。
同名ファイルが存在する場合は上書きせず、SD側のファイル名最大長と使用ライブラリの制約を守って
短い連番名へ変更する。読み戻せなかった場合は、レポートに「SD未検証」と理由を明記する。

#### グラフ作成

- ToF、距離、score、High率など、比較可能な複数点がある場合に作成する
- 単発値や順序に意味のないカテゴリを、不適切な折れ線グラフにしない
- 必要に応じて許容範囲や閾値を併記し、軸名・単位・凡例を付ける
- 出力後に画像を目視確認し、文字化けやデータ欠落がないことを確認する

作業完了時の報告には、作成した実験フォルダへのリンク、判定、SD読出し結果を含める。

ビルドが通らない変更はコミット候補にしない。

### 合格基準（仕様書19章）

| 項目 | 基準 |
|------|------|
| VALID率 | >= 90%（10回中9回以上） |
| score | >= 7 |
| tof_us | 200〜20000us（0.3m〜30m相当） |
| 距離再現性 | 標準偏差10cm以下 |
| 誤検出 | 無送信時にVALIDが出ないこと |

---

## Error Diagnosis Guide

ログのstatusごとの原因と対処:

| status | 原因の候補 | 対処 |
|--------|-----------|------|
| NOISE_HIGH | ノイズ過大 / コンパレータ閾値低すぎ | ノイズ環境確認・閾値調整 |
| INVALID | パターン不一致 / タイミングずれ | search_step縮小・bit_time確認 |
| TENTATIVE | 1bit誤り | SNR改善・スコア閾値一時的に6に下げて原因調査 |
| OUT_OF_RANGE | system_delay未校正 / 反射波誤検出 | system_delay校正・blanking_time延長 |
| NO_DETECT | SYNC未着 / 受信窓短すぎ | SYNC配線確認・max_range拡大 |

---

## Prohibited Actions

明示的な許可なしに以下を行わないでください。

- `common/aquabeacon_config.h` 以外の場所にパラメータ数値をハードコードする
- RXログのCSV列順・列名を変更する
- GPIO番号を変更する（ハード配線と不整合になる）
- PIO実装へ移行する（初期実装はPWM固定）
- TX・RX両方を同時に大きく変更する（片方ずつ確認する）
- `pio run -t upload` を両台同時実行する（COMポート競合の恐れ）
- ログファイルを削除・上書きする（`logs/` は追記のみ）

---

## When to Ask Questions

以下の場合は実装前に質問してください。

- ハードウェア仕様（GPIO・タイミング）が変わる変更
- パターン長・bit_time・sound_speedなど基本パラメータの変更
- PIO移行・複数TX/RX対応など大規模な拡張
- ビルドエラーの原因がコードだけでなく環境（PlatformIO・ドライバ）にある疑いがある場合
- 直近のログCSVで異常が続いており原因が特定できない場合

---

## When You May Decide Autonomously

以下は依頼範囲内であれば自律的に判断して構いません。

- 明らかなコンパイルエラー・警告の修正
- コメントの補足・整理
- `analyze_log.py` の表示改善（CSV列変更なし）
- ログに現れたstatusに基づく閾値の微調整（変更量と理由を報告すること）
- `scripts/` 内のバッチ・Pythonスクリプトの軽微な改善

---

## Definition of Done

作業完了条件は以下です。

- 要求された変更が実装されている
- `pio run` でTX・RXともビルドが成功する
- `scripts\run_test.bat` を実行しPASSする、またはFAIL原因が特定されている
- `common/aquabeacon_config.h` 以外にパラメータ数値が直書きされていない
- RXログCSV形式が維持されている
- 未解決の問題・確認できなかった項目が明示されている
- 最後に変更内容と検証結果が報告されている

---

## Final Response Format

作業完了時は以下の形式で報告してください。

```md
## Summary

- 変更内容1
- 変更内容2

## Verification

- TX ビルド: passed / failed / not run
- RX ビルド: passed / failed / not run
- run_test.bat: passed / failed / not run
- analyze_log.py: VALID率xx% / not run

## Notes

- 残課題
- 注意点（ハードウェア・タイミングに関わるものを優先して書く）
- 次にやるとよいこと
```
