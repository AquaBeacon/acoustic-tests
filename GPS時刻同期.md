# GPS時刻同期

## 確定した配線

| 信号 | 接続 |
|---|---|
| GPS NMEA TX | TX Pico GP4（物理pin 6） |
| GPSのもう一方の信号 | TX Pico GP3（用途未確定、1PPSは未検出） |
| 測距SYNC | TX GP14 → RX GP14（従来どおり） |
| GPS時刻通信 | 測距SYNCと同じRS485回線（TX/RX GP14） |
| GND | GPS / TX / RXで共通 |

GPSとRS485変換モジュールのロジック側は3.3Vにする。5V信号をPico GPIOへ直結しない。

## 動作

1. TXはPIO UART 9600bpsでGP2、GP3、GP4を監視し、チェックサムが正しいNMEAを受信したピンを自動選択する。
2. 実機ではGP4がNMEA入力として検出された。
3. RMCが `A`（有効測位）のときだけUTCを有効とする。無効測位中は誤った絶対時刻をSDへ残さない。
4. TXは最後の有効RMC時刻と内部64bitマイクロ秒タイマーをアンカーにして、次のSYNC予定UTCを計算する。
5. 測距SYNCの100ms後に、チェックサム付き時刻フレームを同じRS485回線へ送る。
6. RXは11msの測距波形取得が終わった後だけ時刻受信を有効にし、その回のSYNC割込み時刻へUTCを割り当てる。
7. 次回パケットが欠落した場合は、最後のUTCアンカーとRX内部タイマーから `RX_HOLDOVER` として時刻を継続する。

時刻フレームは測距SYNC、5波送信、11ms受信窓がすべて終わった100ms後に送る。
RXは時刻受信中に測距SYNCを受け付けないため、時刻フレームを測距開始と誤認しない。
次の測距は5秒後なので回線利用時間は重ならない。
TX/RXは別々に再起動されるため、SDの `seq` と `time_sequence` は一致を必須としない。
時刻フレームは測距ごとの限定受信窓とチェックサムで対応付ける。

## SDメタデータ

各 `Snnn.CSV` の先頭に次を追加する。既存の測距CSV列は変更しない。

```text
# utc_valid,1
# utc_iso,2026-07-27T14:45:25.275909Z
# utc_epoch_us,1785163525275909
# time_source,GPS_NMEA
# time_sequence,2
# time_flags,3
# gps_rx_pin,GP4
# time_packet_age_us,138000
# rx_sync_local_us,12345678
```

`utc_valid=0` と `utc_iso=UNSYNCED` の場合、GPS fixまたはRS485時刻フレームを確認する。

## 実機確認結果

- GPS NMEA: GP4
- NMEA checksum errors: 0
- GPS fix: 1
- PPS: GP2/GP3では未検出
- TX時刻パケット: UTC有効、flags=3（UTC_VALID + GPS_FIX）
- GPS時刻計算用の内部タイマー先行時間: 30ms
- 時刻フレーム送信: 測距SYNCの100ms後
- 時刻受信タイムアウト: 測距SYNCの300ms後
- TXのSYNC予定誤差: 9～10µs（確認測定）
