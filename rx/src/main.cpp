//
// AquaBeacon RX main.cpp
// 仕様書 Section 7, 8, 9, 10, 11 準拠
// 状態: IDLE → WAIT_SYNC → BLANKING → RECEIVE_WINDOW → PATTERN_DETECT → RANGE_CALC → LOG_OUTPUT
//
// 入力方式: 方式A (包絡線 + コンパレータ → デジタルGPIO)
// サンプリング: 5us間隔 (200kS/s相当), 1bitあたり40サンプル
// 仕様書8.1: High数 >= 20 → bit=1, High数 < 20 → bit=0
//

#include <Arduino.h>
#include "aquabeacon_config.h"

// 8bitパターン方式。RANGING_MODE_BURST5=1 のときは burst5.cpp が有効になるため
// このファイル全体を無効化する（setup/loop の二重定義を防ぐ）。
#if !RANGING_MODE_BURST5

// =============================================
// 状態定義
// =============================================
enum RxState {
    RX_IDLE,
    RX_WAIT_SYNC,
    RX_BLANKING,
    RX_RECEIVE_WINDOW,
    RX_PATTERN_DETECT,
    RX_RANGE_CALC,
    RX_LOG_OUTPUT
};

// =============================================
// 受信バッファ
// max_tof + tx_duration + margin = 10ms + 1.6ms + 1ms = 12.6ms
// サンプル数 = 12600us / 5us = 2520サンプル → 余裕をみて3000
// =============================================
#define RX_BUF_SIZE       3000
#define COMP_SAMPLE_US    5     // 仕様書8.1: 5us間隔
#define SAMPLES_PER_BIT   40    // BIT_TIME_US(200us) / COMP_SAMPLE_US(5us)
#define BIT_HIGH_THRESH   20    // 仕様書8.1: High数 >= 20 → bit=1

static uint8_t  rx_buf[RX_BUF_SIZE];      // 0 or 1 (コンパレータ出力)
static uint32_t rx_buf_time[RX_BUF_SIZE]; // 各サンプルの絶対時刻[us]
static int      rx_buf_count = 0;

// =============================================
// グローバル変数
// =============================================
static RxState  state = RX_IDLE;
static uint32_t seq = 0;
static uint32_t t_sync_us = 0;
static uint32_t t_detect_us = 0;
static uint32_t state_enter_us = 0;

static int      noise_high_count = 0;  // ノイズ区間のHigh数
static int      noise_total = 0;       // ノイズ測定サンプル数

static int      detect_score = 0;
static float    range_m = 0;
static int      rx_peak_high_pct = 0;  // 受信窓内の最大High率 [%]
static String   result_status = "NO_DETECT";

// 診断用 (RX_DEBUG)
static uint32_t sync_seen = 0;         // SYNC立ち上がりを検出した累計回数
static uint32_t last_dbg_us = 0;       // 最後にハートビートを出した時刻

// パターン (MSBファースト)
static const uint8_t PATTERN[PATTERN_BITS] = {1,0,1,1,0,1,0,0};

// =============================================
// ノイズ測定 (方式A用)
// コンパレータ出力のHigh率を測定する
// 仕様書9章: 異常にHigh率が高い場合は測定を無効化
// =============================================
void measureNoise() {
    const int N = 200; // 200サンプル × 5us = 1ms
    int high_count = 0;

    for (int i = 0; i < N; i++) {
        if (digitalRead(RX_COMP_PIN) == HIGH) high_count++;
        delayMicroseconds(COMP_SAMPLE_US);
    }

    noise_high_count = high_count;
    noise_total = N;
}

// =============================================
// スライディング パターン検出 (方式A用)
// bit区間のHigh数で0/1を判定
// 戻り値: 最高スコア (0〜8)、t_detect_us を更新
// =============================================
int slideDetect() {
    int best_score = 0;
    uint32_t best_time = 0;
    int max_high_in_bit = 0;

    // 探索ステップ = SEARCH_STEP_US / COMP_SAMPLE_US = 20/5 = 4サンプル
    const int step_samples = SEARCH_STEP_US / COMP_SAMPLE_US;
    const int pattern_samples = PATTERN_BITS * SAMPLES_PER_BIT; // 8 * 40 = 320

    for (int start = 0; start + pattern_samples <= rx_buf_count; start += step_samples) {
        int score = 0;

        for (int b = 0; b < PATTERN_BITS; b++) {
            int offset = start + b * SAMPLES_PER_BIT;

            // bit区間のHigh数をカウント
            int high_count = 0;
            for (int s = 0; s < SAMPLES_PER_BIT; s++) {
                high_count += rx_buf[offset + s];
            }

            // 最大High数を記録 (rx_peak_high_pct用)
            if (high_count > max_high_in_bit) max_high_in_bit = high_count;

            // 仕様書8.1: High数 >= 20 → bit=1
            int received_bit = (high_count >= BIT_HIGH_THRESH) ? 1 : 0;
            if (received_bit == PATTERN[b]) score++;
        }

        if (score > best_score) {
            best_score = score;
            best_time  = rx_buf_time[start];
        }
    }

    // rx_peak: ONビット区間における最大High率 [%]
    rx_peak_high_pct = (max_high_in_bit * 100) / SAMPLES_PER_BIT;

    t_detect_us = best_time;
    return best_score;
}

// =============================================
// setup
// =============================================
void setup() {
    Serial.begin(UART_BAUD);
    delay(500);

    pinMode(RX_SYNC_PIN, INPUT);
    pinMode(RX_COMP_PIN, INPUT);   // コンパレータ出力 (デジタル入力)
    pinMode(RX_LED_PIN,  OUTPUT);

    digitalWrite(RX_LED_PIN, LOW);

    Serial.println("# AquaBeacon RX Ready (方式A: コンパレータ入力)");
    Serial.println("# seq,t_sync_us,t_detect_us,tof_us,range_m,score,rx_peak_pct,noise_high_pct,status");

    state = RX_IDLE;
}

// =============================================
// loop - 状態機械
// =============================================
void loop() {
    uint32_t now = micros();

#if RX_DEBUG
    // 約1秒ごとに診断ハートビートを出力 (SYNC検出回数・ピン状態)
    if (now - last_dbg_us >= 1000000UL) {
        last_dbg_us = now;
        Serial.print("# DBG state=");   Serial.print((int)state);
        Serial.print(" gp14=");          Serial.print(digitalRead(RX_SYNC_PIN));
        Serial.print(" comp_gp");        Serial.print(RX_COMP_PIN);
        Serial.print("=");               Serial.print(digitalRead(RX_COMP_PIN));
        Serial.print(" sync_seen=");     Serial.println(sync_seen);
    }
#endif

    switch (state) {

    // ---- IDLE ----
    case RX_IDLE:
        seq++;
        rx_buf_count = 0;
        detect_score = 0;
        range_m = 0;
        rx_peak_high_pct = 0;
        result_status = "NO_DETECT";

        // ノイズ測定
        measureNoise();

        // ノイズ過大チェック: High率 > 50% は異常
        // (200kHzノイズが常時入っているなど)
        if (noise_high_count > noise_total / 2) {
            Serial.print(seq);
            Serial.print(",0,0,0,0.00,0,0,");
            Serial.print((noise_high_count * 100) / noise_total);
            Serial.println(",NOISE_HIGH");
            delay(RANGE_PERIOD_MS);
            break;
        }

        state = RX_WAIT_SYNC;
        break;

    // ---- WAIT_SYNC ----
    case RX_WAIT_SYNC: {
        static uint32_t sync_wait_start = 0;
        if (sync_wait_start == 0) sync_wait_start = now;

        if (digitalRead(RX_SYNC_PIN) == HIGH) {
            t_sync_us = micros();
            sync_seen++;
            sync_wait_start = 0;
            state = RX_BLANKING;
            state_enter_us = micros();
            digitalWrite(RX_LED_PIN, HIGH);
        }
        // 100ms以上SYNCが来なければIDLEへ
        // ※else if 必須: SYNC検出と同じ反復で sync_wait_start=0 にした直後に
        //   この判定が走ると now-0 が必ず閾値超えとなり、BLANKINGをIDLEに
        //   上書きして受信パイプラインを毎回スキップしてしまう。
        else if (now - sync_wait_start > 100000UL) {
            sync_wait_start = 0;
            state = RX_IDLE;
        }
        break;
    }

    // ---- BLANKING ----
    case RX_BLANKING:
        // 送信直後の電気的回り込み・リンギングを無視する
        if (now - state_enter_us >= BLANKING_TIME_US) {
            state = RX_RECEIVE_WINDOW;
            state_enter_us = micros();
            rx_buf_count = 0;
        }
        break;

    // ---- RECEIVE_WINDOW ----
    case RX_RECEIVE_WINDOW: {
        uint32_t window_end = t_sync_us + MAX_TOF_US + TX_DURATION_US + RECEIVE_MARGIN_US;

        if (micros() < window_end && rx_buf_count < RX_BUF_SIZE) {
            // コンパレータ出力を5usごとにサンプリング
            static uint32_t last_sample_us = 0;
            if (now - last_sample_us >= COMP_SAMPLE_US) {
                rx_buf[rx_buf_count] = (digitalRead(RX_COMP_PIN) == HIGH) ? 1 : 0;
                rx_buf_time[rx_buf_count] = now;
                rx_buf_count++;
                last_sample_us = now;
            }
        } else {
            state = RX_PATTERN_DETECT;
        }
        break;
    }

    // ---- PATTERN_DETECT ----
    case RX_PATTERN_DETECT:
        detect_score = slideDetect();

        if (detect_score >= SCORE_VALID) {
            result_status = "VALID";
        } else if (detect_score >= SCORE_TENTATIVE) {
            result_status = "TENTATIVE";
        } else {
            result_status = "INVALID";
        }

        state = RX_RANGE_CALC;
        break;

    // ---- RANGE_CALC ----
    case RX_RANGE_CALC: {
        if (result_status == "VALID") {
            uint32_t measured_tof = t_detect_us - t_sync_us - SYSTEM_DELAY_US;
            range_m = (measured_tof / 1000000.0f) * SOUND_SPEED_MPS;

            if (range_m < MIN_RANGE_M || range_m > MAX_RANGE_M) {
                result_status = "OUT_OF_RANGE";
            }
        }
        state = RX_LOG_OUTPUT;
        break;
    }

    // ---- LOG_OUTPUT ----
    case RX_LOG_OUTPUT: {
        uint32_t tof_us = (result_status == "VALID")
                          ? (t_detect_us - t_sync_us - SYSTEM_DELAY_US)
                          : 0;

        int noise_pct = (noise_total > 0)
                        ? (noise_high_count * 100) / noise_total
                        : 0;

        digitalWrite(RX_LED_PIN, LOW);

        // CSV出力
        Serial.print(seq);              Serial.print(",");
        Serial.print(t_sync_us);        Serial.print(",");
        Serial.print(t_detect_us);      Serial.print(",");
        Serial.print(tof_us);           Serial.print(",");
        Serial.print(range_m, 3);       Serial.print(",");
        Serial.print(detect_score);     Serial.print(",");
        Serial.print(rx_peak_high_pct); Serial.print(",");  // ONビット最大High率[%]
        Serial.print(noise_pct);        Serial.print(",");  // ノイズHigh率[%]
        Serial.println(result_status);

        state = RX_IDLE;
        break;
    }

    } // switch
}

#endif // !RANGING_MODE_BURST5
