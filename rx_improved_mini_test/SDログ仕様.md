# RX_improved_mini SDログ仕様

## ファイル単位

- 1回の測定ごとに、新しい8.3形式の `S000.CSV` ～ `S999.CSV` を1個作成する。
- 既存ファイルは上書きしない。電源断後も未使用の番号を探索して続行する。
- 1ファイルに距離結果、ENV_ADC、CMP_OUT、使用したTH_PWM閾値をまとめる。
- GPS UTC、時刻ソース、TX時刻シーケンス、時刻パケット受信遅延もメタデータへ保存する。
- USBシリアルが未接続でも、外部電源だけで測定とSD保存を継続する。

## ファイルサイズ対策

- ENV_ADCは22msの測定窓を目標20µs間隔で保存し、最大1,102行とする。
- CMP_OUTはCHANGE割り込みで取得する。200kHz連続反転の最悪条件を考慮し、
  RAMには最大10,000エッジを保持する。
- 異常ノイズ時の肥大化を防ぐため、CMP_OUTのSD記録は5,000行までとする。
- 省略したエッジ数は `# cmp_edges_omitted` に記録する。
- 256KiBを超えた場合は、シリアルへ `FILE_SIZE_WARNING` を出力する。

## CSV形式

先頭にはシーケンス、SYNC時刻、検出時刻、閾値、取得数、省略数のメタデータを置く。
GPS fixとGP14のRS485/SYNC共用時刻フレームが有効な場合は `utc_iso` と
`utc_epoch_us` を保存する。時刻フレームが一時的に欠落した場合はRX内部タイマーで
`RX_HOLDOVER` として継続する。
以降の列は次のとおり。

```csv
record_type,index,t_from_sync_us,env_adc_raw,env_adc_mv,cmp_out,threshold_mv,tof_us,range_m,score,rx_peak_pct,noise_high_pct,status
```

- `SUMMARY`: 距離計測結果
- `ADC`: ENV_ADC定周期サンプルと同時刻のCMP_OUT
- `EDGE`: CMP_OUTの変化エッジ

待受中に `D`、`E`、`C` のいずれかを送ると、最新の統合ファイルをUSBシリアルへ出力する。
