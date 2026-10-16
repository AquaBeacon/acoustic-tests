#include <Arduino.h>
#include "aquabeacon_config.h"

#include "hardware/clocks.h"
#include "hardware/gpio.h"
#include "hardware/pwm.h"
#include "pico/time.h"

#if BURST_FAILSAFE_US <= BURST_DURATION_US
#error "BURST_FAILSAFE_US must exceed BURST_DURATION_US"
#endif

static uint pwm_slice = 0;
static uint pwm_chan = 0;
static alarm_id_t failsafe_alarm_id = 0;
static volatile bool failsafe_fired = false;
static volatile uint32_t failsafe_off_us = 0;
static uint32_t next_burst_us = 0;
static uint32_t sequence = 0;

static void carrierForceLow() {
    // Force the physical pad LOW before freezing the PWM counter.
    gpio_set_outover(TX_CARRIER_PIN, GPIO_OVERRIDE_LOW);
    pwm_set_enabled(pwm_slice, false);
    pwm_set_counter(pwm_slice, 0);
}

static int64_t failsafeCallback(alarm_id_t, void *) {
    carrierForceLow();
    failsafe_off_us = time_us_32();
    failsafe_fired = true;
    return 0;
}

static void carrierOn() {
    pwm_set_counter(pwm_slice, 0);
    pwm_set_enabled(pwm_slice, true);
    gpio_set_outover(TX_CARRIER_PIN, GPIO_OVERRIDE_NORMAL);
}

static void carrierInit() {
    gpio_init(TX_CARRIER_PIN);
    gpio_set_dir(TX_CARRIER_PIN, GPIO_OUT);
    gpio_put(TX_CARRIER_PIN, 0);
    gpio_set_outover(TX_CARRIER_PIN, GPIO_OVERRIDE_LOW);
    gpio_set_function(TX_CARRIER_PIN, GPIO_FUNC_PWM);

    pwm_slice = pwm_gpio_to_slice_num(TX_CARRIER_PIN);
    pwm_chan = pwm_gpio_to_channel(TX_CARRIER_PIN);
    const uint32_t wrap = (clock_get_hz(clk_sys) / CARRIER_FREQ_HZ) - 1;
    pwm_set_wrap(pwm_slice, wrap);
    pwm_set_chan_level(pwm_slice, pwm_chan, (wrap + 1) / 2);
    carrierForceLow();
}

static void transmitBurst() {
    failsafe_fired = false;
    failsafe_off_us = 0;
    carrierForceLow();

    // Pico 2 W LED access may block, so keep it before the protected interval.
    digitalWrite(LED_BUILTIN, HIGH);
    failsafe_alarm_id = add_alarm_in_us(
        BURST_FAILSAFE_US, failsafeCallback, nullptr, true);
    if (failsafe_alarm_id < 0) {
        failsafe_alarm_id = 0;
        digitalWrite(LED_BUILTIN, LOW);
        Serial.println("# DIAG,BURST_ABORT,alarm_allocation_failed");
        return;
    }

    const uint32_t on_us = micros();
    carrierOn();
    delayMicroseconds(BURST_DURATION_US);
    carrierForceLow();
    uint32_t off_us = micros();
    if (failsafe_alarm_id > 0) {
        cancel_alarm(failsafe_alarm_id);
        failsafe_alarm_id = 0;
    }
    if (failsafe_fired) off_us = failsafe_off_us;
    digitalWrite(LED_BUILTIN, LOW);

    sequence++;
    Serial.print("# DIAG,BURST,"); Serial.print(sequence);
    Serial.print(",on_us="); Serial.print(on_us);
    Serial.print(",off_us="); Serial.print(off_us);
    Serial.print(",duration_us="); Serial.print(off_us - on_us);
    Serial.print(",gp15="); Serial.print(gpio_get(TX_CARRIER_PIN) ? 1 : 0);
    Serial.print(",pwm=0,failsafe="); Serial.println(failsafe_fired ? 1 : 0);
}

void setup() {
    Serial.begin(UART_BAUD);
    delay(500);
    pinMode(LED_BUILTIN, OUTPUT);
    digitalWrite(LED_BUILTIN, LOW);
    carrierInit();

    next_burst_us = micros() + (BURST_REPEAT_INTERVAL_MS * 1000UL);
    Serial.println("# AquaBeacon automatic 5-wave TX test");
    Serial.print("# interval_ms="); Serial.print(BURST_REPEAT_INTERVAL_MS);
    Serial.print(",carrier_hz="); Serial.print(CARRIER_FREQ_HZ);
    Serial.print(",waves="); Serial.println(BURST_WAVE_COUNT);
    Serial.println("# GP15 is forced LOW between bursts");
}

void loop() {
    const uint32_t now = micros();
    if ((int32_t)(now - next_burst_us) >= 0) {
        // Advance from the prior deadline so serial logging does not accumulate drift.
        next_burst_us += BURST_REPEAT_INTERVAL_MS * 1000UL;
        transmitBurst();
    }
}
