# acoustic-tests

TD04A送受信・波形・同期試験。

pio run -d tx と pio run -d rx でビルドします。rx_improved_mini_test、tx_5wave_test、wave は各platformio.iniを持つ試験対象です。Python依存は scripts/requirements.txt を使用します。

- [tx/platformio.ini](tx/platformio.ini)
- [rx/platformio.ini](rx/platformio.ini)
- [common/aquabeacon_config.h](common/aquabeacon_config.h)

## 開発とライセンス

[開発ガイド](DEVELOPMENT.md)にセットアップ・入口・確認方法をまとめています。自作部分は [Apache License 2.0](LICENSE)。[第三者素材・依存の扱い](THIRD_PARTY.md)も参照してください。
