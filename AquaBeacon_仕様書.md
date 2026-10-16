# AquaBeacon 音響測距 TX/RX Pico 処理仕様書

---

## 1. 目的

本仕様書は、Raspberry Pi Pico を送信機（TX）および受信機（RX）のマイコンとして用い、200kHz音響信号による距離推定を行うための処理仕様を定義する。

本方式では、単純な200kHz連続波ではなく、既知のON/OFFパターンで変調した200kHzバーストを送信する。RX側では受信信号の包絡線またはコンパレータ出力を解析し、期待パターンと一致した場合のみ有効な受信として扱う。

---

## 2. システム構成

### 2.1 送信機 TX

TXはRaspberry Pi Picoで200kHzの矩形波を生成し、ゲートドライバまたは送信回路を介して超音波振動子を駆動する。

```
Raspberry Pi Pico
→ 200kHz PWM/PIO出力
→ ゲートドライバ
→ MOSFET等の送信回路
→ TD04A等の超音波振動子
```

### 2.2 受信機 RX

RXは超音波振動子からの信号をアナログ回路で増幅・フィルタリングし、包絡線またはデジタル化された信号をPicoで読み取る。

```
TD04A等の受信振動子
→ 保護回路
→ 低雑音増幅
→ 200kHz中心BPF
→ 包絡線検波
→ コンパレータまたはPico ADC
→ Raspberry Pi Pico
```

---

## 3. 前提条件

### 3.1 時刻同期

距離推定には、TXの送信開始時刻 `t_tx` とRXの受信検出時刻 `t_rx` が必要である。したがって、TX PicoとRX Picoは以下のいずれかの方法で同期する。

推奨順位：

1. TX PicoからRX Picoへ有線SYNC信号を送る
2. 共通の外部トリガ信号でTX/RXを同時開始する
3. GPS PPS等の共通時刻基準を使う

> 独立Pico間で時刻同期なしに動作させる方式は不可。最初の実験では、有線SYNC方式を推奨する。

### 3.2 音速

初期値として水中音速を以下とする。

```
sound_speed = 1500.0 m/s
```

温度・塩分・水深による補正は初期実装では行わない。実海域実験では、既知距離での校正または音速計測により補正する。

---

## 4. 信号仕様

### 4.1 搬送波

| 項目 | 値 |
|------|----|
| 搬送波周波数 | 200kHz |
| 周期 | 5us |
| 出力波形 | 50% duty 矩形波 |

### 4.2 ON/OFFパターン

初期実装では以下の8bitパターンを用いる。

```
pattern = 10110100
```

このパターンは周期的すぎず、0と1が混在しているため、連続ノイズや単純な繰り返しノイズに対して比較的判定しやすい。

### 4.3 bit時間

```
bit_time = 200us
```

200kHzの1周期は5usであるため、1bitあたりの搬送波周期数は以下となる。

```
200us / 5us = 40周期
```

### 4.4 送信時間

8bit、`bit_time = 200us` の場合：

```
tx_duration = 8 * 200us = 1.6ms
```

高速化する場合は以下も許容する。

```
bit_time = 100us
tx_duration = 0.8ms
```

> ただし、bit_timeを短くするとRX側の包絡線判定が難しくなるため、初期実験では200usを使用する。

---

## 5. TX側処理仕様

### 5.1 TX側の役割

TX Picoは以下を行う。

1. 送信開始要求を受ける
2. 送信開始時刻を記録する
3. RXへSYNC信号を出す
4. ON/OFFパターンに従って200kHz搬送波を出力する
5. 送信終了後、搬送波を必ずOFFにする
6. ログ用に送信番号と時刻を保存する

### 5.2 TX状態遷移

```
IDLE
↓
ARMED
↓
SYNC_OUT
↓
TRANSMIT_PATTERN
↓
TX_OFF
↓
WAIT_INTERVAL
↓
IDLE
```

### 5.3 状態定義

#### IDLE
待機状態。送信要求が来るまで搬送波出力はOFF。

#### ARMED
送信準備状態。パターン、bit_time、送信番号を確認する。

#### SYNC_OUT
RXに対して送信開始を知らせるSYNCパルスを出力する。

```
SYNC pulse width = 10us以上
level = 3.3V
```

#### TRANSMIT_PATTERN
pattern を先頭から順に処理する。

```
bit = 1 → 200kHz搬送波ON
bit = 0 → 200kHz搬送波OFF
保持時間 → bit_time
```

#### TX_OFF
全bit送信後、200kHz出力をOFFにする。

#### WAIT_INTERVAL
次回送信まで待機する。初期実装では20Hz測距を目標とするため、送信周期は50msとする。

---

## 6. TX出力方式

### 6.1 初期実装（PWM）

```
system_clock = 125MHz
PWM frequency = 200kHz
PWM TOP = 624
PWM duty = 312
```

周波数計算：

```
125MHz / (624 + 1) = 200kHz
```

ON/OFFパターンは、PWMの有効/無効をbit時間ごとに切り替えて実現する。

### 6.2 高精度実装（PIO）

最終的にはPIOを用いる。PIOを使用することで、CPU負荷や割り込みの影響を受けにくく、bit時間および搬送波周期数を正確に制御できる。

| 優先順位 | 方式 |
|---------|------|
| 初期実験 | PWM ON/OFF |
| 精度改善 | PIO |

---

## 7. RX側処理仕様

### 7.1 RX側の役割

RX Picoは以下を行う。

1. SYNC信号を受ける
2. 送信開始時刻 `t_tx_ref` を記録する
3. 送信直後のブランキング時間は検出を無効化する
4. 受信時間窓を開く
5. 包絡線またはコンパレータ出力をサンプリングする
6. ON/OFFパターンとの一致度を計算する
7. 一致度がしきい値以上なら検出とする
8. 検出時刻から距離を計算する
9. 測定結果をログ出力する

### 7.2 RX状態遷移

```
IDLE
↓
WAIT_SYNC
↓
BLANKING
↓
RECEIVE_WINDOW
↓
PATTERN_DETECT
↓
RANGE_CALC
↓
LOG_OUTPUT
↓
IDLE
```

### 7.3 状態定義

#### IDLE
初期化後の待機状態。

#### WAIT_SYNC
TXからのSYNC信号を待つ。SYNC立ち上がりを検出した時刻を `t_sync` とする。

#### BLANKING
送信直後の電気的回り込み、振動子のリンギング、近距離飽和を避けるため、一定時間検出を無効化する。

```
blanking_time = 200us
調整範囲: 100us ～ 500us
```

#### RECEIVE_WINDOW
想定最大距離に応じて受信時間窓を開く。

```
max_range    = 30m
sound_speed  = 1500m/s
max_tof      = 30 / 1500 = 20ms

receive_start = t_sync + blanking_time
receive_end   = t_sync + max_tof + tx_duration + margin

margin = 1ms
→ receive_window ≒ 0.2ms ～ 12.6ms
```

#### PATTERN_DETECT
受信データをbit_timeごとに区切り、期待パターンとの一致度を計算する。

#### RANGE_CALC
検出されたパターン先頭時刻を `t_detect` とし、以下で距離を計算する。

```
tof   = t_detect - t_sync - system_delay
range = tof * sound_speed
```

`system_delay` はアナログ回路・BPF・包絡線検波・コンパレータ・Pico入力処理による固定遅延。既知距離実験により校正する。

#### LOG_OUTPUT

以下をログとして保存または送信する。

- sequence_id
- t_sync
- t_detect
- tof
- range
- detect_score
- rx_peak
- noise_level
- status

---

## 8. RX入力方式

### 8.1 方式A: 包絡線 + コンパレータ入力（推奨初期方式）

```
BPF後信号 → 包絡線検波 → コンパレータ → Pico GPIO
```

PicoはGPIOを一定周期で読み、各bit区間のHigh割合を計算する。

```
sample_interval = 5us（200kS/s相当）
1bitあたりサンプル数 = 40

High数 >= 20 → bit = 1
High数 <  20 → bit = 0
```

> CPUで5usごとに読むのは負荷が大きいため、可能であればPIOまたはタイマ割り込みを使う。

### 8.2 方式B: 包絡線 + ADC入力

```
BPF後信号 → 包絡線検波 → Pico ADC
```

```
adc_sample_rate = 20kS/s ～ 50kS/s
bit_time = 200us、50kS/sの場合：
1bitあたりサンプル数 = 10

平均値または最大値 >= threshold → bit = 1
平均値または最大値 <  threshold → bit = 0
```

> ADC方式ではしきい値を動的に決められるため環境ノイズに強い。ただし実装はコンパレータ方式より複雑になる。

---

## 9. ノイズ床測定

RXは各測距の前にノイズ床を推定する。

SYNC前または受信窓開始前に、一定時間ADC値またはコンパレータHigh率を測定する。

**ADC方式の場合：**

```
noise_mean = ノイズ区間の平均値
noise_std  = ノイズ区間の標準偏差
threshold  = noise_mean + k * noise_std
k = 5（初期値）
```

**コンパレータ方式の場合：**

コンパレータしきい値はアナログ側で設定する。Pico側では受信前のHigh率を測り、異常にHigh率が高い場合は測定を無効化する。

---

## 10. パターン検出アルゴリズム

### 10.1 完全一致は使用しない

コンパレータの一瞬の誤検知や包絡線の立ち上がり遅れにより、1bit程度の誤りが発生する可能性があるため、完全一致判定は使用しない。

### 10.2 スコア判定

期待パターンと受信bit列を比較し、一致数をスコアとする。

```
expected = 10110100
received = 10111100
score    = 7 / 8
```

| score | 判定 |
|-------|------|
| >= 7 | valid |
| == 6 | tentative |
| <= 5 | invalid |

> 初期実装では `score >= 7` のみ有効検出とする。

### 10.3 0区間も評価する

期待bitが0の区間で受信がHighになっている場合は不一致として扱う。これにより、200kHzノイズが連続して入っているだけの状態を除外する。

```
expected = 10110100
received = 11111111
score    = 4 / 8  → invalid
```

### 10.4 スライディング検出

受信波形の開始位置は不明であるため、受信窓内で開始位置を少しずつずらしながらスコアを計算する。

```
search_step = 20us（初期値）
高精度化: 5us ～ 10us
```

最も高いスコアを持つ開始位置を `t_detect` とする。

---

## 11. 距離計算

### 11.1 基本式

```
range = (t_detect - t_sync - system_delay) * sound_speed
```

### 11.2 単位

| 変数 | 単位 |
|------|------|
| time | seconds |
| sound_speed | m/s |
| range | m |

### 11.3 固定遅延補正

`system_delay` に含まれる遅延：

- TX側のSYNCから実際の音響出力までの遅延
- 送信回路遅延
- 振動子の立ち上がり遅れ
- 受信振動子の応答遅れ
- BPF群遅延
- 包絡線検波遅延
- コンパレータ遅延
- Pico入力処理遅延

**校正方法：**

```
既知距離 d_known で測定
theoretical_tof = d_known / sound_speed
measured_tof    = t_detect - t_sync
system_delay    = measured_tof - theoretical_tof
```

既知距離を複数点測定し、平均値を使用する。

---

## 12. 測距周期

```
range_rate = 20Hz
period     = 50ms
```

採用理由：
- 最大距離30mのToF約20msに対し、22msの受信窓で2msの余裕がある
- 反射・残響が収まる時間を確保しやすい
- 光学追跡30Hz程度との統合がしやすい
- 初期実験でログ確認しやすい

安定後の拡張目標：50Hz → 80Hz → 100Hz

> 浅い水槽やプールでは反射が多いため、高更新周期化よりも安定検出を優先する。

---

## 13. エラー処理

### 13.1 検出なし

```
status = NO_DETECT
range  = NaN
```

### 13.2 複数候補

同じ受信窓内に複数の有効候補がある場合、初期実装では**最も早い候補を採用**する。直接波は反射波より早く到達するため。

> 最も早い候補のスコアが低く後続候補のスコアが高い場合はログに残し、後処理で確認する。

### 13.3 飽和

```
status = SATURATED
```

ADC値が上限付近、またはコンパレータHigh率が異常に高い場合。

### 13.4 ノイズ過大

```
status = NOISE_HIGH
```

送信前ノイズ区間でHigh率またはADCノイズが高い場合。測距結果を無効化する。

### 13.5 物理的に不可能な距離

```
range < min_range → 無効
range > max_range → 無効

min_range = 0.3m
max_range = 30m
```

---

## 14. ピン割り当て

### 14.1 TX Pico

| 機能 | GPIO | 備考 |
|------|------|------|
| 200kHz PWM/PIO出力 | GP15 | ゲートドライバ入力へ |
| SYNC出力 | GP14 | RX Picoへ |
| UART TX | GP0 | ログ出力用 |
| UART RX | GP1 | 設定受信用 |
| LED | GP25 | 動作確認 |

### 14.2 RX Pico

| 機能 | GPIO | 備考 |
|------|------|------|
| SYNC入力 | GP14 | TX Picoから |
| コンパレータ入力 | GP15 | 方式A |
| ADC入力 | GP26 / ADC0 | 方式B |
| UART TX | GP0 | ログ出力用 |
| UART RX | GP1 | 設定受信用 |
| LED | GP25 | 検出表示 |

---

## 15. 通信・ログ仕様

### 15.1 UART設定

```
baudrate  = 115200
data_bits = 8
parity    = none
stop_bits = 1
```

### 15.2 RXログ形式（CSV）

```
seq,t_sync_us,t_detect_us,tof_us,range_m,score,rx_peak,noise,status
```

例：
```
12,1050000,1053420,3420,5.13,7,2380,120,VALID
```

### 15.3 TXログ形式（CSV）

```
seq,t_tx_us,pattern,bit_time_us,status
```

例：
```
12,1050000,10110100,200,TX_DONE
```

---

## 16. 実装言語

初期実装からC/C++ Pico SDKを使用する。

MicroPythonは以下の理由により使用しない：

- タイミングジッタが大きい
- 割り込み遅延が読みにくい
- 高速サンプリングやPIO制御に不利

---

## 17. 初期パラメータ一覧

| パラメータ | 値 |
|-----------|-----|
| carrier_freq | 200000 Hz |
| pattern | 10110100 |
| bit_time | 200 us |
| tx_duration | 1600 us |
| blanking_time | 200 us |
| sound_speed | 1500.0 m/s |
| max_range | 30.0 m（25m実験 + 5m余裕） |
| receive_margin | 2000 us |
| range_rate | 20 Hz |
| detect_score_valid | 7 |
| detect_score_tentative | 6 |
| search_step | 20 us |

---

## 18. 試験手順

### 18.1 TX単体試験

1. Picoから200kHzが出ていることをオシロで確認する
2. dutyが約50%であることを確認する
3. `10110100` のON/OFFパターンになっていることを確認する
4. bit_timeが200usであることを確認する
5. 送信後に出力がOFFになることを確認する

### 18.2 RXアナログ試験

1. 受信振動子出力を見る
2. 増幅後波形を見る
3. BPF後に200kHz成分が残っていることを確認する
4. 包絡線検波後にON/OFFパターンが見えることを確認する
5. コンパレータ出力が包絡線に対応していることを確認する

### 18.3 TX/RX結合試験

1. 空中または水中でTX/RXを近距離に配置する
2. SYNC信号と受信包絡線を同時にオシロで見る
3. 受信パターンが送信パターンと一致することを確認する
4. Picoログに `VALID` が出ることを確認する
5. 既知距離で `system_delay` を校正する

### 18.4 距離校正試験

既知距離で以下を測定する（各距離で10回以上、平均誤差と標準偏差を記録）：

- 0.5m
- 1m
- 2m
- 5m
- 10m

---

## 19. 合格基準

### 19.1 初期合格基準

| 項目 | 基準 |
|------|------|
| 200kHz出力 | ±2%以内 |
| bit_time | ±5%以内 |
| パターン検出 | 近距離で90%以上VALID |
| 距離再現性 | 同一距離で標準偏差10cm以下 |
| 誤検出 | 無送信時にVALIDが出ないこと |

### 19.2 改善後目標

| 項目 | 目標 |
|------|------|
| 測距周期 | 20Hz以上 |
| 最大距離 | 10m以上 |
| 距離再現性 | 標準偏差5cm以下 |
| 誤検出率 | 1%未満 |
| 検出率 | 95%以上 |

---

## 20. 今後の拡張

初期実装が安定した後、以下を検討する。

1. PWMからPIOへの移行
2. パターン長の最適化
3. bit_timeの短縮
4. ADCによる包絡線強度取得
5. 動的しきい値制御
6. 複数pingの中央値フィルタ
7. 光学追跡結果との整合性チェック
8. CHIRP信号への移行
9. 複数TX/RXによる測位拡張

---

## 21. 注意事項

- PicoのGPIOは3.3Vであるため、5V信号を直接入力してはならない
- TX駆動回路とRXアナログ回路のGND設計に注意する
- TXの大電流パルスがRX電源・GND・しきい値基準に回り込まないようにする
- RXアナログ部はPico電源から抵抗またはフェライトビーズで分離し、ローカルにデカップリングする
- コンパレータのしきい値は、3.3V分圧をそのまま使うのではなく、十分に平滑化する
- 距離計算には必ず固定遅延補正を入れる
- 水槽・プールでは反射が強いため、最初に到達した候補を優先し、後続反射をログに残す
