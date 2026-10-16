//
// AquaBeacon TX main.cpp
// 仕様書 Section 5, 6 準拠
// 状態: IDLE → ARMED → SYNC_OUT → TRANSMIT_PATTERN → TX_OFF → WAIT_INTERVAL
//

#include <Arduino.h>
#include "aquabeacon_config.h"

// 8bitパターン方式。RANGING_MODE_BURST5=1 のときは burst5.cpp が有効になるため
// このファイル全体を無効化する（setup/loop の二重定義を防ぐ）。
#if !RANGING_MODE_BURST5

#if !TX_ENVELOPE_BENCH
// 実機運用: wave/ と同じハードウェアPWMで実200kHz矩形波を生成する
#include "hardware/pwm.h"
#include "hardware/clocks.h"
#endif

// =============================================
// 状態定義
// =============================================
enum TxState {
    TX_IDLE,
    TX_ARMED,
    TX_SYNC_OUT,
    TX_SIM_DELAY,
    TX_TRANSMIT_PATTERN,
    TX_OFF,
    TX_WAIT_INTERVAL
};

// =============================================
// グローバル変数
// =============================================
static TxState state = TX_IDLE;
static uint32_t seq = 0;
static uint32_t t_tx_us = 0;
static uint32_t state_enter_us = 0;
static int      bit_index = 0;
static uint32_t bit_start_us = 0;

// パターン配列 (MSBファースト)
static const uint8_t PATTERN[PATTERN_BITS] = {1,0,1,1,0,1,0,0};

// =============================================
// 搬送波 ON/OFF
//  TX_ENVELOPE_BENCH=0: 実200kHz矩形波 (ハードウェアPWM, トランスデューサ駆動)
//  TX_ENVELOPE_BENCH=1: 直結ベンチ用にON=HIGH固定 (コンパレータ出力相当)
//
// 実機運用(BENCH=0)では wave/ と同じハードウェアPWMを使う。
// setup()で wrap/duty を一度だけ設定し、ON/OFFは pwm_set_enabled() で
// スライスをゲートする。これにより bit_time ごとの正確な搬送波切替を
// 仕様書6.1「PWMの有効/無効をbit時間ごとに切り替えて実現する」通りに行う。
// =============================================
#if !TX_ENVELOPE_BENCH
static uint tx_pwm_slice = 0;
static uint tx_pwm_chan  = 0;

// 搬送波PWMの初期化 (setup()から一度だけ呼ぶ)
void carrierInit() {
    gpio_set_function(TX_CARRIER_PIN, GPIO_FUNC_PWM);
    tx_pwm_slice = pwm_gpio_to_slice_num(TX_CARRIER_PIN);
    tx_pwm_chan  = pwm_gpio_to_channel(TX_CARRIER_PIN);

    // wrap = f_sys / CARRIER_FREQ_HZ - 1  (例: 150MHz/200kHz-1 = 749)
    uint32_t f_sys = clock_get_hz(clk_sys);
    uint32_t wrap  = (f_sys / CARRIER_FREQ_HZ) - 1;

    pwm_set_wrap(tx_pwm_slice, wrap);
    pwm_set_chan_level(tx_pwm_slice, tx_pwm_chan, (wrap + 1) / 2); // 50% duty
    pwm_set_enabled(tx_pwm_slice, false);                          // 初期はOFF
}
#endif

void carrierOn() {
#if TX_ENVELOPE_BENCH
    // 直結ベンチ: 包絡線/コンパレータ出力相当のクリーンなHIGH
    digitalWrite(TX_CARRIER_PIN, HIGH);
#else
    pwm_set_enabled(tx_pwm_slice, true);   // 200kHz搬送波を出力
#endif
}

void carrierOff() {
#if TX_ENVELOPE_BENCH
    digitalWrite(TX_CARRIER_PIN, LOW);
#else
    pwm_set_enabled(tx_pwm_slice, false);  // 搬送波停止 (OFFビット=無振動)
#endif
}

// =============================================
// setup
// =============================================
void setup() {
    Serial.begin(UART_BAUD);
    delay(500);

    pinMode(TX_SYNC_PIN, OUTPUT);
    pinMode(TX_LED_PIN, OUTPUT);

#if TX_ENVELOPE_BENCH
    pinMode(TX_CARRIER_PIN, OUTPUT);   // 直結ベンチ: GPIO出力でON/OFF
#else
    carrierInit();                     // 実機: GP15をPWM機能に設定して搬送波生成
#endif

    carrierOff();
    digitalWrite(TX_SYNC_PIN, LOW);
    digitalWrite(TX_LED_PIN, LOW);

    Serial.println("# AquaBeacon TX Ready");
    Serial.println("# seq,t_tx_us,pattern,bit_time_us,status");

    state = TX_IDLE;
}

// =============================================
// loop - 状態機械
// =============================================
void loop() {
    uint32_t now = micros();

    switch (state) {

    // ---- IDLE ----
    case TX_IDLE:
        // 自動起動: 常にARMEDへ遷移
        seq++;
        state = TX_ARMED;
        state_enter_us = now;
        break;

    // ---- ARMED ----
    case TX_ARMED:
        // パラメータ確認（ここでは即遷移）
        state = TX_SYNC_OUT;
        state_enter_us = now;

        // SYNC立ち上げ
        digitalWrite(TX_SYNC_PIN, HIGH);
        t_tx_us = micros();
        digitalWrite(TX_LED_PIN, HIGH);
        break;

    // ---- SYNC_OUT ----
    case TX_SYNC_OUT:
        // SYNCパルス幅を保持 (基準はSYNC立ち上がり時刻 t_tx_us)
        if (now - t_tx_us >= SYNC_PULSE_WIDTH_US) {
            digitalWrite(TX_SYNC_PIN, LOW);
            state = TX_SIM_DELAY;
        }
        break;

    // ---- SIM_DELAY ----
    // 直結ベンチ用の擬似飛行遅延。SYNC立ち上がり(t_tx_us)からの経過で判定し、
    // 擬似tof ≈ SIM_FLIGHT_DELAY_US になるようパターン送信開始を遅らせる。
    // SIM_FLIGHT_DELAY_US <= SYNC_PULSE_WIDTH_US の場合は即座にパターンへ移る。
    case TX_SIM_DELAY:
        if (now - t_tx_us >= (uint32_t)SIM_FLIGHT_DELAY_US) {
            bit_index = 0;
            bit_start_us = micros();
            state = TX_TRANSMIT_PATTERN;
        }
        break;

    // ---- TRANSMIT_PATTERN ----
    case TX_TRANSMIT_PATTERN:
        if (bit_index < PATTERN_BITS) {
            // 現在bitの搬送波ON/OFF
            if (PATTERN[bit_index] == 1) {
                carrierOn();
            } else {
                carrierOff();
            }

            // bit_time経過後に次のbitへ
            if (now - bit_start_us >= BIT_TIME_US) {
                bit_index++;
                bit_start_us = micros();
            }
        } else {
            // 全bit送信完了
            state = TX_OFF;
            state_enter_us = now;
        }
        break;

    // ---- TX_OFF ----
    case TX_OFF:
        carrierOff();
        digitalWrite(TX_LED_PIN, LOW);

        // ログ出力
        Serial.print(seq);
        Serial.print(",");
        Serial.print(t_tx_us);
        Serial.print(",");
        Serial.print("10110100");
        Serial.print(",");
        Serial.print(BIT_TIME_US);
        Serial.println(",TX_DONE");

        state = TX_WAIT_INTERVAL;
        state_enter_us = now;
        break;

    // ---- WAIT_INTERVAL ----
    case TX_WAIT_INTERVAL:
        // 送信周期 50ms を守る
        if (now - t_tx_us >= (uint32_t)(RANGE_PERIOD_MS * 1000UL)) {
            state = TX_IDLE;
        }
        break;
    }
}

#endif // !RANGING_MODE_BURST5
