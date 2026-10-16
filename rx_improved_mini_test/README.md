# RX_improved_mini 5波測距テスト

`RX_improved_mini`基板仕様書と、既存のAquaBeacon 5波・SD実験コードを
流用した独立テストです。既存の`tx/`・`rx/`は変更しません。

## 配線

### TX

- GP15 → TXゲートドライバ入力（GPIOから振動子を直接駆動しない）
- GP14 → SYNC伝送回路 → RX GP14
- TX GND ↔ RX GND

### RX_improved_mini J2

| J2 | 信号 | Pico 2 W | 物理ピン | 用途 |
|---|---|---|---|---|
| 1 | V3A | 接続しない | — | C-PWRの3.3Vアナログ電源から供給 |
| 2 | GND | GND | 例: pin 3 | TX/RX/C-PWR共通GND |
| 3 | TH_PWM | GP6 | pin 9 | 125kHz PWMで比較器しきい値を生成 |
| 4 | ENV_ADC | GP26 / ADC0 | pin 31 | 包絡線電圧の連続ADC取得 |
| 5 | NC | 接続しない | — | 基板内部でも未接続 |
| 6 | CMP_OUT | GP2 | pin 4 | 比較器デジタル値と先頭エッジ取得 |

J2の電気的に使用可能な信号はすべて使用します。J2.5は基板仕様上NCなので、
マイコンへ接続しません。

### そのほかのRXマイコン配線

| 機能 | Pico 2 W | 物理ピン |
|---|---|---|
| TXからのSYNC | GP14 | pin 19 |
| SD MISO | GP16 | pin 21 |
| SD CS | GP17 | pin 22 |
| SD SCK | GP18 | pin 24 |
| SD MOSI | GP19 | pin 25 |

TD04A受信振動子は基板J1.1 `XD_IN` とJ1.2 `GND`へ接続します。

基板仕様書に記載されたJ2とC-MAIN J_RX1の不整合は、書き込み前に配線で
解消してください。CMP_OUTは基板上で10kΩプルアップ済みです。

### PiCowbell Adalogger microSD

- MISO GP16
- CS GP17
- SCK GP18
- MOSI GP19

## ビルド

```powershell
pio run -d tx_5wave_test
pio run -d rx_improved_mini_test
```

書き込みは同時に行わず、実機のCOMポートに合わせます。

```powershell
pio run -d tx_5wave_test -t upload --upload-port COM10
pio run -d rx_improved_mini_test -t upload --upload-port COM5
```

RXシリアル監視:

```powershell
pio device monitor --port COM5 --baud 115200
```

TXはGPS探索用の起動待ち3秒後に初回を送信し、現在の共通設定では5秒間隔で5波を
送信します。GP15は送信後LOW固定され、100µsの独立フェイルセーフがあります。

## RX操作

- `?`: 状態、しきい値、SD状態を表示
- `+`: 比較器しきい値を10mV上げる
- `-`: 比較器しきい値を10mV下げる
- `A`: 自動しきい値調整のON/OFF
- `D`: 待受中にSDの距離サマリをUSBシリアルへダンプ
- `E`: 待受中にSDのENV_ADC定周期ログをダンプ
- `C`: 待受中にSDのCMP_OUT全エッジログをダンプ
- `I`: SD初期化失敗後、待受中に再初期化

実測したENV_ADCピーク300mVは、1/2分圧前の比較器入力ENVでは約600mVです。
初期TH_PWMはその約50%の300mVです。各測定後に待受ベースラインと受信ピークの
中点を計算し、最大50mV/回で次回しきい値へ追従します。`+`または`-`で手動変更
すると自動調整はOFFになります。`NO_DETECT`では長距離の微弱信号を探索するため、
20mV/回でしきい値を下げます。ただし、待受ベースラインのコンパレータ換算値+40mV、
または60mVの高い方を下限とします。有効エッジがある場合は、ENV_ADCピークが
100mV以上の測定だけを通常の自動追従に使用します。
最初のSYNCを逃さないため、SDカードの初期化は最初の測定終了後に行います。

## 距離とログ

SYNCから0.3m相当（200µs）未満の立ち上がりは電気的回り込みとして候補から
除外し、200～22000µs内の最初のCMP_OUT立ち上がりで片道距離を求めます。
最大有効距離は30mで、25m実験に対して5mの余裕と2msの受信マージンがあります。

```text
range_m = (t_detect_us - t_sync_us - SYSTEM_DELAY_US) * SOUND_SPEED_MPS / 1e6
```

USBシリアルとSDの`RXMnnn.CSV`には既存解析互換の距離サマリ9列を保存します。
`rx_peak_pct`はENV_ADCのADCフルスケール比、`noise_high_pct`はSYNC待受中に
100µs間隔で測ったCMP_OUTのHigh率です。

`RXRnnn.CSV`にはSYNC直後から22ms間、目標20µs間隔でENV_ADCと、その時点の
CMP_OUT値を保存します。

```text
seq,t_sync_us,sample_index,t_from_sync_us,env_adc_raw,env_adc_mv,cmp_out,threshold_mv
```

ADCとGPIOはまずRAMへ保存し、22msの取得完了後にSDへ書き出します。これにより
SD書込み遅延が距離測定やサンプリングを止めません。実際のサンプル間隔は
`t_from_sync_us`から確認できます。

CMP_OUTは約200kHzで変化し得るため、20µs周期値とは別にCHANGE割り込みで
全エッジを取得し、`RXCnnn.CSV`へ保存します。

```text
seq,t_sync_us,edge_index,t_from_sync_us,cmp_out
```

10000エッジ分のRAMを確保しており、22ms受信窓のCMP_OUT変化を記録します。
距離計算には200µs以降の最初の
立ち上がりを使いますが、`RXCnnn.CSV`には200µs未満の電気的回り込みも残します。

## 実機確認順

1. 振動子を外し、無送信時に`VALID`が出ないことを確認する。
2. オシロスコープでGP15が約25µs後にLOW固定されることを確認する。
3. TH_PWMが約125kHz、平滑後VTHが初期約300mVであることを確認する。
4. `E`と`C`でENV_ADC約300mVとCMP_OUTの時間関係を読み戻す。
5. 既知距離で10回測定し、VALID率90%以上・標準偏差10cm以下を確認する。
6. `D`で距離サマリを読み戻し、シリアル行と一致することを確認する。
