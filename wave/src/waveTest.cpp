//
// 200kHz 連続波テスト
//   GP15 から 50% duty の 200kHz 矩形波を出力し続ける (停止しない)。
//   オシロでの搬送波確認・回路調整用。
//   周波数/ピンは common/aquabeacon_config.h を参照 (直書きしない)。
//
#include <Arduino.h>
#include "hardware/pwm.h"
#include "hardware/clocks.h"
#include "aquabeacon_config.h"   // CARRIER_FREQ_HZ, TX_CARRIER_PIN, UART_BAUD

void setup() {
    Serial.begin(UART_BAUD);
    delay(2000);
    Serial.println("--- 200kHz Continuous Wave Test ---");

    // GP15 を PWM 機能に設定
    gpio_set_function(TX_CARRIER_PIN, GPIO_FUNC_PWM);
    uint slice_num = pwm_gpio_to_slice_num(TX_CARRIER_PIN);
    uint chan      = pwm_gpio_to_channel(TX_CARRIER_PIN);

    // システムクロックから搬送波周波数の wrap を計算
    //   wrap = f_sys / CARRIER_FREQ_HZ - 1
    //   例: 150MHz / 200kHz - 1 = 749  → 実周波数 150e6/750 = 200kHz
    uint32_t f_sys = clock_get_hz(clk_sys);
    uint32_t wrap  = (f_sys / CARRIER_FREQ_HZ) - 1;

    pwm_set_wrap(slice_num, wrap);
    pwm_set_chan_level(slice_num, chan, (wrap + 1) / 2); // 50% duty
    pwm_set_enabled(slice_num, true);                    // 連続出力開始 (止まらない)

    Serial.printf("Output pin   : GP%u\n", TX_CARRIER_PIN);
    Serial.printf("System Clock : %lu Hz\n", f_sys);
    Serial.printf("Target carrier: %lu Hz\n", (unsigned long)CARRIER_FREQ_HZ);
    Serial.printf("PWM wrap     : %lu (actual %lu Hz)\n",
                  (unsigned long)wrap, (unsigned long)(f_sys / (wrap + 1)));
}

void loop() {
    // 連続出力中。loop では何もしない。
}
