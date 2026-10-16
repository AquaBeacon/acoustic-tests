# SD全データ現場解析

外部パッケージは不要です。Python 3だけで、SDカードの `S000.CSV` 形式を全件解析します。
元のSDファイルは変更せず、実行ごとに新しいレポートフォルダを作成します。

## 現場での実行

SDカードが `D:` の場合、リポジトリ直下で実行します。

```powershell
python scripts\analyze_sd_experiment.py --sd-root 'D:\'
```

別ドライブの場合は `--sd-root` を変更してください。単一ファイルや退避フォルダも指定できます。

```powershell
python scripts\analyze_sd_experiment.py --input 'D:\S036.CSV'
python scripts\analyze_sd_experiment.py --input 'D:\backup'
```

通常は解析処理に成功すれば終了コード0です。合格基準を自動試験にも使う場合は
`--strict` を付けると、総合FAIL時に終了コード2を返します。

## 出力

`reports\YYYYMMDD_HHMMSS_sd_all_data_analysis\` に次を保存します。

- `実験結果.csv`: 既存の9列形式を維持した距離測定結果
- `測定統合解析.csv`: 距離、ADC、閾値、CMP_OUT、時刻、保存状態を1測定1行に統合
- `ADC全データ.csv`: ADCの全サンプル
- `ADC解析.csv`: ADCピーク、平均、中央値、取得間隔、欠落数
- `閾値解析.csv`: 使用閾値、次回閾値、変化量、RAISE/LOWER/HOLD
- `CMP_OUT全エッジ.csv`: CMP_OUTの全エッジ
- `CMP_OUT解析.csv`: 最初の立上り、200us以降の最初の立上り、エッジ欠落数
- `SD読出し結果.csv`: 行数整合、ドロップ、ファイル容量
- `元ログ.csv`: 元CSVをファイル名・行番号付きで保持
- `入力ファイル一覧.csv`: 各ファイル名と元の絶対パスの対応
- `簡易レポート.md`, `前提条件.md`
- `距離_ADC_閾値推移.svg`, `ADC代表波形.svg`

判定値は `common/aquabeacon_config.h` から読み取るため、解析スクリプト側へ
ファームウェアの距離・スコア・ファイル容量パラメータを重複定義していません。
