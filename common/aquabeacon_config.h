#pragma once

// =============================================
// AquaBeacon 共通パラメータ
// 仕様書 Section 17 準拠
// =============================================

// --- 搬送波 ---
#define CARRIER_FREQ_HZ     200000UL    // 200kHz
#define CARRIER_PERIOD_US   5           // 1周期 = 5us

// --- ON/OFFパターン ---
// 10110100 (MSBファースト)
#define PATTERN_BITS        8
#define PATTERN_VALUE       0b10110100  // = 0xB4
#define BIT_TIME_US         200         // 1bitあたりの時間

// 送信時間 = 8bit × 200us = 1600us
#define TX_DURATION_US      (PATTERN_BITS * BIT_TIME_US)

// --- タイミング ---
#define BLANKING_TIME_US    0           // SYNC直後から受信開始
#define RECEIVE_MARGIN_US   2000UL      // 30m到達後の受信窓マージン
#define SYNC_PULSE_WIDTH_US 50          // SYNCパルス幅

// --- 直結ベンチテスト用 擬似飛行遅延 ---
// TXがSYNC立ち上がりからパターン送信を開始するまでの遅延[us]。
// 直結(音響飛行なし)では伝搬遅延が0のため、パターンがブランキング中に
// 到着してしまい先頭ビットが欠落する。この遅延で擬似的な飛行時間を作り、
// フルパイプライン(SYNC→パターン検出→距離計算)を直結のまま検証する。
//   range ≈ SIM_FLIGHT_DELAY_US * 1e-6 * SOUND_SPEED_MPS
//   2000us → 約3.0m (BLANKING_TIME_US=200us より十分後に到着)
//   実機(実音響飛行)で運用するときは 0 にする。
// 実機運用: 実際の音響伝搬遅延を測るため 0。
#define SIM_FLIGHT_DELAY_US 0

// --- 直結ベンチ用 包絡線エミュレーション ---
// 実回路では BPF→包絡線検波→コンパレータ→GPIO により、ONバースト中は
// クリーンなHIGH、OFF中はLOW がRXのGPIOに入る(生の200kHzは入らない)。
// 直結ベンチで生の200kHzを入れるとONビットのHigh率が duty=50%付近=閾値20前後に
// 乗って不安定になるため、ベンチ時はTXがONビットでGP15をHIGH固定し、
// コンパレータ出力相当のクリーンなON/OFFを出す。
//   1 = 包絡線エミュレーション(直結ベンチ向け, ONビット=HIGH固定)
//   0 = 実200kHz搬送波出力(実回路/トランスデューサ駆動, 実運用)
// 実機運用: ハードウェアPWMで実200kHz矩形波を出力(wave/ と同方式)。
#define TX_ENVELOPE_BENCH   0

// =============================================
// 測距方式の切替
//   1 = 5波バースト方式 (シンプル: SYNC→5波送信→初到達エッジでToF計測)
//   0 = 8bitパターン方式 (仕様書準拠: 10110100 をスコア相関検出)
// 8bitパターン方式のコードは tx/rx の main.cpp に残してある。
// BURST5=1 のときは main.cpp は #if で無効化され burst5.cpp が有効になる。
// =============================================
#define RANGING_MODE_BURST5 1

// --- 5波バースト方式パラメータ ---
#define BURST_WAVE_COUNT    5           // 連続波数
// バースト長 = 5波 × 5us = 25us
#define BURST_DURATION_US   (BURST_WAVE_COUNT * CARRIER_PERIOD_US)
#define BURST_SYNC_TO_CARRIER_DELAY_US 0UL    // SYNC直後に搬送波ON
#define BURST_FAILSAFE_US   100UL       // independent maximum carrier ON time
#define BURST_REPEAT_INTERVAL_MS 5000UL  // 5 seconds between automatic shots
// 検出ポーリング間隔[us]。1.5mm/us なので 2us で約3mm量子化。
#define DETECT_SAMPLE_US    2
// 初到達確認: 単発ノイズ除外のため連続HIGHをこの数だけ確認して検出確定。
#define DETECT_CONFIRM_N    3
#define POST_DETECT_CAPTURE_MS 3000UL   // preserve later arrivals/reflections
#define ECHO_GROUP_GAP_US      100UL    // LOW gap that starts a new echo group
#define RX_ECHO_GROUP_MAX      256      // bounded RAM use during capture
#define RX_EDGE_RING_CAPACITY  16384    // continuous RX_COMP_PIN CHANGE-edge ring (power of two)
#define RX_RISE_EVENT_CAPACITY 8192     // GP20 rising timestamps saved per shot
#define RX_ELECTRICAL_GUARD_US 200UL    // classify early coupling; does not discard it

// --- 5波バースト実験の制御文字と安全間隔 ---
// 通常は電源投入後、設定周期ごとに自動送信する。Tによる手動送信にも
// 同じ最小間隔を適用し、?は状態確認、Rは旧試験スクリプトとの互換用。
#define BURST_TRIGGER_CHAR  'T'
#define BURST_IDENTIFY_CHAR '?'
#define BURST_REARM_CHAR    'R'
#define BURST_MIN_INTERVAL_MS     BURST_REPEAT_INTERVAL_MS
#define BURST_AUTO_START_DELAY_MS 250UL // external-power start; brief RX GPIO arm guard

// --- GPS UTC and dedicated TX -> RX time link ---
// GPS NMEA input is auto-detected because the current wiring may use GP2/3/4.
// SerialPIO permits UART RX on these non-hardware-UART pins.
#define GPS_NMEA_BAUD             9600UL
#define GPS_RX_CANDIDATE_0        3
#define GPS_RX_CANDIDATE_1        4
#define GPS_RX_CANDIDATE_2        2
#define GPS_RX_CANDIDATE_COUNT    3
#define GPS_NMEA_LINE_MAX         128
#define GPS_DISCOVERY_GRACE_MS    3000UL
#define GPS_HOLDOVER_MAX_US       21600000000ULL // 6 hours
#define GPS_RMC_FRESH_US          2000000ULL
#define GPS_PPS_MIN_INTERVAL_US   900000ULL
#define GPS_PPS_MAX_INTERVAL_US   1100000ULL
#define GPS_PPS_RMC_MAX_AGE_US    800000ULL

// GPS時刻は既存のRS485/SYNC回線(GP14)へ、測距後に時分割多重する。
// RXは測距キャプチャ完了後だけ時刻フレームを受けるため、測距SYNCを妨げない。
#define TIME_LINK_LEAD_US         30000ULL
#define TIME_LINK_LINE_MAX        96
#define TIME_LINK_UNKNOWN_PIN     255
#define TIME_LINK_SEND_DELAY_US   100000ULL
#define TIME_LINK_RX_TIMEOUT_US   300000ULL
#define TIME_LINK_PREAMBLE_US     1500UL
#define TIME_LINK_PREAMBLE_MIN_US 1200UL
#define TIME_LINK_PREAMBLE_MAX_US 1800UL
#define TIME_LINK_PREAMBLE_GAP_US 500UL
#define TIME_LINK_BIT_ZERO_US     100UL
#define TIME_LINK_BIT_ONE_US      200UL
#define TIME_LINK_BIT_THRESHOLD_US 150UL
#define TIME_LINK_BIT_GAP_US      100UL
#define TIME_LINK_PAYLOAD_BYTES   15
#define TIME_LINK_PAYLOAD_BITS    (TIME_LINK_PAYLOAD_BYTES * 8)
#define TIME_LINK_EDGE_TIMEOUT_US 3000UL
#define TIME_LINK_MAX_FRAME_US    \
    (TIME_LINK_PREAMBLE_US + TIME_LINK_PREAMBLE_GAP_US + \
     TIME_LINK_PAYLOAD_BITS * \
         (TIME_LINK_BIT_ONE_US + TIME_LINK_BIT_GAP_US))

#define TIME_FLAG_UTC_VALID       0x01
#define TIME_FLAG_GPS_FIX         0x02
#define TIME_FLAG_GPS_PPS         0x04
#define TIME_FLAG_TX_HOLDOVER     0x08
#define TIME_FLAG_RX_HOLDOVER     0x10

// --- PiCowbell Adalogger microSD (RX, SPI0) ---
// Card detect is not required; SD presence is checked by initialization.
#define SD_MISO_PIN          16
#define SD_CS_PIN            17
#define SD_SCK_PIN           18
#define SD_MOSI_PIN          19
#define SD_LOG_FILE_LIMIT    1000       // AQB000.CSV .. AQB999.CSV
#define SD_DUMP_CHAR         'D'
#define SD_EDGE_DUMP_CHAR    'E'
#define SD_REINIT_CHAR       'I'
#define PICOWBELL_SDA_PIN     4
#define PICOWBELL_SCL_PIN     5
#define PICOWBELL_RTC_ADDR    0x68

// --- 距離・音速 ---
#define SOUND_SPEED_MPS     1500.0f     // 水中音速 [m/s]
#define MAX_RANGE_M         30.0f       // 25m実験 + 5m余裕
#define MIN_RANGE_M         0.3f

// TOF上限 = 30m / 1500m/s = 20ms
#define MAX_TOF_US          20000UL     // = MAX_RANGE_M / SOUND_SPEED * 1e6

// --- 測距周期 ---
#define RANGE_RATE_HZ       20
#define RANGE_PERIOD_MS     50          // 1000 / RANGE_RATE_HZ

// --- パターン検出スコア ---
#define SCORE_VALID         7           // >= 7 → VALID
#define SCORE_TENTATIVE     6           // == 6 → TENTATIVE

// --- スライディング検出 ---
#define SEARCH_STEP_US      20          // 探索ステップ

// --- ピン定義 TX ---
#define TX_CARRIER_PIN      15          // GP15: 200kHz出力
#define TX_SYNC_PIN         14          // GP14: SYNC出力
#define SYNC_IDLE_LEVEL     HIGH        // RS485/UART idle polarity
#define SYNC_ACTIVE_LEVEL   LOW         // active-low pulse through auto-direction RS485
#define TX_LED_PIN          25

// --- ピン定義 RX ---
#define RX_SYNC_PIN         14          // GP14: SYNC入力
#define RX_COMP_PIN         20          // GP20: 受信回路出力 (デジタル入力、GP15との干渉回避)
#define RX_COMP_PULL_MODE   0           // 0=なし, 1=プルダウン, 2=プルアップ診断
                                        // ※直結テスト時はTX GP15から配線
#define RX_LED_PIN          25

// --- RX_improved_mini 基板用テストピン ---
// 基板仕様書 J2: TH_PWM=GP6, ENV_ADC=GP26/ADC0, CMP_OUT=GP2。
// 既存RX実験(GP20)を壊さないよう、専用テストプロジェクトだけが使用する。
#define RX_MINI_TH_PWM_PIN       6
#define RX_MINI_ENV_ADC_PIN      26
#define RX_MINI_COMP_PIN         2
#define PICO_LOGIC_SUPPLY_MV     3300UL
#define PICO_ADC_MAX_RAW         4095UL

// LM393しきい値生成。基板上のR15/C8を125kHz PWMで駆動する。
// 実機でENV_ADC=300mVを確認。ENVは分圧前なので約600mV相当であり、
// 初期しきい値は信号振幅の約50%となる300mVから開始する。
// 実機ではシリアルの '+' / '-' で10mVずつ調整して決定する。
#define RX_MINI_TH_PWM_FREQ_HZ   125000UL
#define RX_MINI_TH_PWM_MIN_COUNTS 1000UL
#define RX_MINI_TH_INITIAL_MV    300
#define RX_MINI_TH_STEP_MV       10
#define RX_MINI_TH_MIN_MV        20
#define RX_MINI_TH_MAX_MV        850

// 送信直後のENV_ADC/CMP_OUT連続取得と自動しきい値調整。
#define RX_MINI_CAPTURE_SAMPLE_US       20UL
#define RX_MINI_CAPTURE_DURATION_US     (MAX_TOF_US + RECEIVE_MARGIN_US)
#define RX_MINI_CAPTURE_CAPACITY        \
    (RX_MINI_CAPTURE_DURATION_US / RX_MINI_CAPTURE_SAMPLE_US + 2UL)
#define RX_MINI_CAPTURE_RAM_BUDGET_BYTES 12288UL
#define RX_MINI_CMP_EDGE_CAPACITY       10000UL
#define RX_MINI_CMP_EDGE_RAM_BUDGET_BYTES 98304UL
#define RX_MINI_SD_EDGE_ROWS_MAX        5000UL
#define RX_MINI_SD_FILE_WARN_BYTES      262144UL
#define RX_MINI_CMP_DUMP_CHAR           'C'
#define RX_MINI_ENV_TO_COMP_NUM         2UL
#define RX_MINI_ENV_TO_COMP_DEN         1UL
#define RX_MINI_AUTO_THRESHOLD_PERCENT  50UL
#define RX_MINI_AUTO_NOISE_MARGIN_MV    40UL
#define RX_MINI_AUTO_MIN_SIGNAL_MV      20UL
#define RX_MINI_AUTO_MIN_PEAK_MV        100UL
#define RX_MINI_AUTO_MAX_STEP_MV        50UL
#define RX_MINI_AUTO_NO_DETECT_STEP_MV  20UL
#define RX_MINI_AUTO_SEARCH_MIN_MV      60UL

// 最初の有効エッジ後、この時間だけ後続キャリアエッジを数える。
#define RX_MINI_BURST_SCORE_WINDOW_US 60UL
#define RX_MINI_NOISE_SAMPLE_US       100UL
#define BURST5_VALID_SCORE       8

// --- コンパレータサンプリング (方式A: 包絡線+コンパレータ→GPIO) ---
// 仕様書8.1: 5us間隔, 1bitあたり40サンプル, High>=20でbit=1
#define COMP_SAMPLE_US       5          // サンプル間隔
#define COMP_SAMPLES_PER_BIT 40         // BIT_TIME_US / COMP_SAMPLE_US
#define COMP_HIGH_THRESH     20         // bit=1判定のHigh数しきい値

// --- ノイズ推定 (方式A用) ---
#define NOISE_HIGH_RATE_MAX  50         // ノイズ区間High率上限[%] 超えたらNOISE_HIGH

// --- UART ---
#define UART_BAUD           115200

// --- system_delay補正 ---
// 空気中40cm・5波バーストのVALID 2回から得た暫定平均固定遅延。
// 5回中3回がNO_DETECTで、VALID間のばらつきも大きいため再校正が必要。
#define SYSTEM_DELAY_US     0

// --- RX診断ハートビート (一時的なデバッグ用) ---
// 1にするとRXが約1秒ごとに "# DBG ..." を出力し、SYNC検出回数とピン状態を可視化する。
// 配線/SYNC受信の切り分けが終わったら 0 に戻す。
#define RX_DEBUG            0

// --- RX生GPIO診断 ---
#define RX_DIAG_GPIO_COUNT  30          // Picoヘッダ上のGP0..GP29を監視
