# ADC波形ビューアー

`ADC波形ビューアー起動.bat` をダブルクリックすると、2026-08-10プール実験のSxxx.CSVを一覧表示します。

## できること

- 実験条件またはSxxx.CSVを選択
- 0～22 msに記録された全ADCサンプルを表示
- ADC実測点、7点移動平均、しきい値、直接波、マルチパス候補を重ねて表示
- Matplotlibツールバーによる拡大、移動、表示範囲の初期化
- 全ADCサンプルを表で確認（SYNC後時刻、導出UTC/JST、ADC raw、mV、コンパレータ値）
- 選択波形をPNGまたはCSVへ出力
- 表示条件に属する全ファイルのADCを1つのCSVへ結合
- 波形上の任意の2点をクリックし、時間差と推定距離差を表示
- 実験水温を入力し、1500 m/sと水温補正音速の両方で距離差を比較

## 2点間の時間差・距離差

1. 上部の「水温」に実験時水温を入力します（初期値32.5 ℃）。
2. 波形上の1点目と2点目を順にクリックします。クリック位置は最寄りのADCサンプルへ吸着します。
3. `Δt`、1500 m/sでの距離差、水温補正音速での距離差が上部に表示されます。
4. 3点目をクリックすると新しいP1から選択し直します。「選択解除」でもリセットできます。
5. 「2点結果CSV」で、選択点、導出UTC/JST、時間差、両音速での距離差を保存できます。

距離差は `|P2時刻-P1時刻| × 音速` で計算します。水温補正音速には、0～100 ℃・大気圧の純水を対象とするBilaniuk-Wong式を使用しています。プール水の塩素濃度や圧力による差は含みません。

## コマンドライン出力

```bat
python scripts\adc_waveform_viewer.py --export-preview analysis_input\pool_20260810\S051.CSV S051_adc.png
python scripts\adc_waveform_viewer.py --export-csv analysis_input\pool_20260810\S051.CSV S051_all_adc.csv
python scripts\adc_waveform_viewer.py --input analysis_input\pool_20260810 --export-all-csv all_adc_samples.csv
```

UTC/JST列は、各ファイルの`utc_iso`をSYNC時刻として、`t_from_sync_us`を加算した導出時刻です。
