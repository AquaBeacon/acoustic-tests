#include <Arduino.h>
#include <SPI.h>
#include <SD.h>
#include <Wire.h>
#include <hardware/gpio.h>
#include <pico/platform.h>
#include <pico/time.h>
#include "aquabeacon_config.h"

#if RANGING_MODE_BURST5

#if POST_DETECT_CAPTURE_MS == 0 || RX_ECHO_GROUP_MAX == 0
#error "Post-detection capture parameters must be non-zero"
#endif

#if RX_EDGE_RING_CAPACITY == 0 || \
    (RX_EDGE_RING_CAPACITY & (RX_EDGE_RING_CAPACITY - 1)) != 0
#error "RX_EDGE_RING_CAPACITY must be a non-zero power of two"
#endif

#if RX_RISE_EVENT_CAPACITY == 0
#error "RX_RISE_EVENT_CAPACITY must be non-zero"
#endif

enum RxState {
    RX_IDLE,
    RX_WAIT_SYNC,
    RX_BLANKING,
    RX_DETECT,
    RX_POST_CAPTURE,
    RX_RANGE_CALC,
    RX_LOG_OUTPUT,
    RX_DONE
};

struct EchoGroup {
    uint32_t start_us;
    uint32_t end_us;
    uint32_t rising_edges;
    uint32_t high_samples;
};

struct GpioEdgeEvent {
    uint32_t time_us;
    uint8_t level;
    uint8_t reserved[3];
};

static RxState state = RX_IDLE;
static uint32_t seq = 0;
static uint32_t t_sync_us = 0;
static uint32_t t_detect_us = 0;
static uint32_t state_enter_us = 0;
static uint32_t last_sample_us = 0;
static uint32_t cand_edge_us = 0;
static uint32_t post_capture_start_us = 0;
static int high_streak = 0;
static int detect_confirm_samples = 0;
static int previous_level = LOW;
static uint32_t detect_sample_count = 0;
static uint32_t detect_high_samples = 0;
static uint32_t detect_rising_edges = 0;
static uint32_t detect_first_high_us = 0;
static int detect_max_high_streak = 0;
static volatile uint32_t comp_rising_irq_count = 0;
static volatile uint32_t comp_first_rising_us = 0;
static volatile uint32_t comp_last_rising_us = 0;
static uint32_t comp_rising_irq_at_sync = 0;
static volatile bool rise_capture_active = false;
static volatile uint32_t rise_event_count = 0;
static volatile uint32_t rise_event_dropped = 0;
static volatile uint32_t rise_event_us[RX_RISE_EVENT_CAPACITY] = {};
static volatile GpioEdgeEvent edge_ring[RX_EDGE_RING_CAPACITY] = {};
static volatile uint32_t edge_total = 0;
static uint32_t edge_total_at_sync = 0;
static uint32_t edge_snapshot_total = 0;
static GpioEdgeEvent polled_edge_ring[RX_EDGE_RING_CAPACITY] = {};
static uint32_t polled_edge_total = 0;
static uint32_t polled_edge_total_at_sync = 0;
static uint32_t polled_edge_snapshot_total = 0;
static uint8_t polled_previous_level = 0;
static uint32_t raw_previous_gpio = 0;
static uint32_t raw_seen_high_mask = 0;
static uint32_t raw_rising_mask = 0;
static uint32_t raw_sample_count = 0;
static uint32_t digital_raw_mismatch_count = 0;
static uint32_t raw_rising_count[RX_DIAG_GPIO_COUNT] = {};
static float range_m = 0;
static bool detected = false;
static String result_status = "NO_DETECT";
static bool sd_ready = false;
static bool picowbell_rtc_ready = false;
static bool picowbell_rtc_probed = false;
static char sd_log_name[13] = {};
static char sd_edge_log_name[13] = {};
static const char *sd_error = "NOT_STARTED";
static uint8_t sd_error_code = 0;
static uint8_t sd_error_data = 0;

static EchoGroup echo_groups[RX_ECHO_GROUP_MAX];
static uint16_t echo_group_count = 0;
static uint32_t echo_group_dropped = 0;
static bool echo_active = false;
static EchoGroup current_echo = {};
static uint32_t current_echo_last_rise_us = 0;

static bool initSdLog();

static bool probePicowbellRtc() {
    Wire.setSDA(PICOWBELL_SDA_PIN);
    Wire.setSCL(PICOWBELL_SCL_PIN);
    Wire.begin();
    Wire.beginTransmission(PICOWBELL_RTC_ADDR);
    return Wire.endTransmission() == 0;
}

static void initializeDeferredStorage() {
    if (!picowbell_rtc_probed) {
        picowbell_rtc_ready = probePicowbellRtc();
        picowbell_rtc_probed = true;
    }
    if (!sd_ready && strcmp(sd_error, "DEFERRED") == 0) {
        sd_ready = initSdLog();
    }
    Serial.print("# PICOWBELL,RTC_");
    Serial.println(picowbell_rtc_ready ? "OK" : "NOT_FOUND");
    Serial.print("# SD,");
    Serial.print(sd_ready ? "READY," : "ERROR,");
    Serial.println(sd_ready ? sd_log_name : sd_error);
    if (sd_ready) {
        Serial.print("# SD,EDGE_READY,"); Serial.println(sd_edge_log_name);
    }
}

static bool initSdLog() {
    if (!SPI.setRX(SD_MISO_PIN) || !SPI.setTX(SD_MOSI_PIN) ||
        !SPI.setSCK(SD_SCK_PIN)) {
        sd_error = "INVALID_SPI_PIN";
        return false;
    }
    // Configure the PiCowbell SPI0 pins, then let SD.begin(CS) start SPI.
    if (!SD.begin(SD_CS_PIN)) {
        // Some cards and longer jumper paths do not initialize reliably at
        // 4 MHz. Retry at 1 MHz before reporting a hardware/media failure.
        SD.end();
        if (!SD.begin(SD_CS_PIN, SPI_EIGHTH_SPEED, SPI)) {
            SdFs diagnostic_fs;
            const SdSpiConfig diagnostic_config(
                SD_CS_PIN, SHARED_SPI, SD_SCK_MHZ(1), &SPI);
            if (!diagnostic_fs.begin(diagnostic_config)) {
                sd_error_code = diagnostic_fs.sdErrorCode();
                sd_error_data = diagnostic_fs.sdErrorData();
            }
            diagnostic_fs.end();
            sd_error = "MOUNT_FAILED";
            return false;
        }
    }

    for (uint16_t index = 0; index < SD_LOG_FILE_LIMIT; ++index) {
        snprintf(sd_log_name, sizeof(sd_log_name), "AQB%03u.CSV", index);
        snprintf(
            sd_edge_log_name, sizeof(sd_edge_log_name), "AQE%03u.CSV", index);
        if (SD.exists(sd_log_name) || SD.exists(sd_edge_log_name)) continue;
        File log_file = SD.open(sd_log_name, FILE_WRITE);
        if (!log_file) {
            sd_error = "CREATE_FAILED";
            return false;
        }
        log_file.println(
            "seq,t_sync_us,t_detect_us,tof_us,range_m,score,rx_peak_pct,noise_high_pct,status");
        log_file.flush();
        const bool ok = !log_file.getWriteError();
        log_file.close();
        if (!ok) {
            sd_error = "HEADER_WRITE_FAILED";
            return false;
        }

        File edge_file = SD.open(sd_edge_log_name, FILE_WRITE);
        if (!edge_file) {
            sd_error = "EDGE_CREATE_FAILED";
            return false;
        }
        edge_file.println(
            "seq,t_sync_us,rise_index,rise_us,rise_from_sync_us,region,dropped_total");
        edge_file.flush();
        const bool edge_ok = !edge_file.getWriteError();
        edge_file.close();
        sd_error = edge_ok ? "NONE" : "EDGE_HEADER_WRITE_FAILED";
        return edge_ok;
    }
    sd_log_name[0] = '\0';
    sd_edge_log_name[0] = '\0';
    sd_error = "NO_FREE_83_NAME";
    return false;
}

static bool appendSdResult(uint32_t tof_us, int score) {
    if (!sd_ready || sd_log_name[0] == '\0') return false;
    File log_file = SD.open(sd_log_name, FILE_WRITE);
    if (!log_file) return false;
    log_file.print(seq); log_file.print(',');
    log_file.print(t_sync_us); log_file.print(',');
    log_file.print(detected ? t_detect_us : 0); log_file.print(',');
    log_file.print(tof_us); log_file.print(',');
    log_file.print(range_m, 3); log_file.print(',');
    log_file.print(score); log_file.print(',');
    log_file.print(detect_confirm_samples); log_file.print(',');
    log_file.print(0); log_file.print(',');
    log_file.println(result_status);
    log_file.flush();
    const bool ok = !log_file.getWriteError();
    log_file.close();
    return ok;
}

static bool appendSdRiseEvents() {
    if (!sd_ready || sd_edge_log_name[0] == '\0') return false;
    File edge_file = SD.open(sd_edge_log_name, FILE_WRITE);
    if (!edge_file) return false;

    const uint32_t captured = rise_event_count;
    const uint32_t dropped = rise_event_dropped;
    if (captured == 0) {
        edge_file.print(seq); edge_file.print(',');
        edge_file.print(t_sync_us); edge_file.print(',');
        edge_file.print(-1); edge_file.print(',');
        edge_file.print(0); edge_file.print(',');
        edge_file.print(0); edge_file.print(',');
        edge_file.print("NO_EDGE"); edge_file.print(',');
        edge_file.println(dropped);
    } else {
        for (uint32_t index = 0; index < captured; ++index) {
            const uint32_t rise_us = rise_event_us[index];
            const uint32_t from_sync_us = rise_us - t_sync_us;
            const char *region = from_sync_us < RX_ELECTRICAL_GUARD_US
                ? "EARLY_GUARD"
                : (from_sync_us <= MAX_TOF_US + RECEIVE_MARGIN_US
                    ? "RANGE_WINDOW" : "POST_CAPTURE");
            edge_file.print(seq); edge_file.print(',');
            edge_file.print(t_sync_us); edge_file.print(',');
            edge_file.print(index); edge_file.print(',');
            edge_file.print(rise_us); edge_file.print(',');
            edge_file.print(from_sync_us); edge_file.print(',');
            edge_file.print(region); edge_file.print(',');
            edge_file.println(dropped);
        }
    }
    edge_file.flush();
    const bool ok = !edge_file.getWriteError();
    edge_file.close();
    return ok;
}

static void dumpSdFile(
        const char *file_name, const char *begin_tag, const char *end_tag) {
    if (!sd_ready) {
        Serial.print("# SD,DUMP_ERROR,"); Serial.println(sd_error);
        return;
    }
    File log_file = SD.open(file_name, FILE_READ);
    if (!log_file) {
        Serial.println("# SD,DUMP_ERROR,OPEN_FAILED");
        return;
    }
    Serial.print(begin_tag); Serial.println(file_name);
    while (log_file.available()) Serial.write(log_file.read());
    log_file.close();
    Serial.print(end_tag); Serial.println(file_name);
}

static void onCompChange() {
    const uint32_t edge_us = time_us_32();
    const uint8_t level = gpio_get(RX_COMP_PIN) ? 1 : 0;
    const uint32_t edge_seq = edge_total;
    const uint32_t edge_index = edge_seq & (RX_EDGE_RING_CAPACITY - 1);
    edge_ring[edge_index].time_us = edge_us;
    edge_ring[edge_index].level = level;
    edge_total = edge_seq + 1;

    if (level != 0) {
        if (comp_rising_irq_count == 0) comp_first_rising_us = edge_us;
        comp_last_rising_us = edge_us;
        comp_rising_irq_count++;
        if (rise_capture_active) {
            const uint32_t capture_index = rise_event_count;
            if (capture_index < RX_RISE_EVENT_CAPACITY) {
                rise_event_us[capture_index] = edge_us;
                rise_event_count = capture_index + 1;
            } else {
                rise_event_dropped++;
            }
        }
    }
}

static void recordPolledInput(uint32_t sample_us) {
    const uint8_t level = gpio_get(RX_COMP_PIN) ? 1 : 0;
    if (level == polled_previous_level) return;

    const uint32_t edge_index = polled_edge_total & (RX_EDGE_RING_CAPACITY - 1);
    polled_edge_ring[edge_index].time_us = sample_us;
    polled_edge_ring[edge_index].level = level;
    polled_edge_total++;
    polled_previous_level = level;
}

static uint32_t recordRawGpioSample() {
    const uint32_t levels = gpio_get_all();
    const uint32_t rising = levels & ~raw_previous_gpio;
    raw_seen_high_mask |= levels;
    raw_rising_mask |= rising;
    raw_sample_count++;

    uint32_t pending = rising;
    while (pending != 0) {
        const uint32_t pin = (uint32_t)__builtin_ctz(pending);
        if (pin < RX_DIAG_GPIO_COUNT) raw_rising_count[pin]++;
        pending &= pending - 1;
    }
    raw_previous_gpio = levels;
    return levels;
}

static void resetEchoCapture() {
    echo_group_count = 0;
    echo_group_dropped = 0;
    echo_active = false;
    current_echo = {};
    current_echo_last_rise_us = 0;
}

static void finishCurrentEcho() {
    if (!echo_active) return;
    if (echo_group_count < RX_ECHO_GROUP_MAX) {
        echo_groups[echo_group_count++] = current_echo;
    } else {
        echo_group_dropped++;
    }
    echo_active = false;
}

static void startEcho(uint32_t rise_us, uint32_t high_samples) {
    current_echo.start_us = rise_us;
    current_echo.end_us = rise_us;
    current_echo.rising_edges = 1;
    current_echo.high_samples = high_samples;
    current_echo_last_rise_us = rise_us;
    echo_active = true;
}

static void recordEchoSample(uint32_t now, int level) {
    if (level == HIGH && previous_level == LOW) {
        if (echo_active &&
            (uint32_t)(now - current_echo_last_rise_us) > ECHO_GROUP_GAP_US) {
            finishCurrentEcho();
            startEcho(now, 1);
        } else if (echo_active) {
            current_echo.rising_edges++;
            current_echo.high_samples++;
            current_echo.end_us = now;
            current_echo_last_rise_us = now;
        } else {
            startEcho(now, 1);
        }
    } else if (level == HIGH && echo_active) {
        current_echo.high_samples++;
        current_echo.end_us = now;
    }
    previous_level = level;
}

void setup() {
    Serial.begin(UART_BAUD);
    pinMode(RX_SYNC_PIN, INPUT);
    pinMode(RX_COMP_PIN, RX_COMP_PULL_MODE == 2
        ? INPUT_PULLUP
        : (RX_COMP_PULL_MODE == 1 ? INPUT_PULLDOWN : INPUT));
    polled_previous_level = gpio_get(RX_COMP_PIN) ? 1 : 0;
    attachInterrupt(digitalPinToInterrupt(RX_COMP_PIN), onCompChange, CHANGE);
    pinMode(LED_BUILTIN, OUTPUT);
    // Pico 2 W drives LED_BUILTIN through CYW43. Do this before arming;
    // never call it in the timing-critical interval immediately after SYNC.
    digitalWrite(LED_BUILTIN, HIGH);

    sd_error = "DEFERRED";

    Serial.println("# AquaBeacon RX Ready (5-wave burst with echo capture)");
    Serial.println("# PICOWBELL,RTC_DEFERRED");
    Serial.println("# SD,DEFERRED_UNTIL_FIRST_CAPTURE");
    Serial.print("# DIAG,RX_EDGE_RECORDER,pin="); Serial.print(RX_COMP_PIN);
    Serial.print(",mode=CHANGE,capacity="); Serial.println(RX_EDGE_RING_CAPACITY);
    Serial.print("# DIAG,RX_CHIP,rp2350_revision=");
    Serial.println(rp2350_chip_version());
    Serial.println("# seq,t_sync_us,t_detect_us,tof_us,range_m,score,rx_peak,noise,status");
}

void loop() {
    const uint32_t now = micros();
    recordPolledInput(now);

    while (Serial.available() > 0) {
        const int command = Serial.read();
        if (command == BURST_IDENTIFY_CHAR) {
            Serial.println(state == RX_DONE ? "# ID:RX;STATE=DONE"
                                            : "# ID:RX;STATE=ARMED");
            Serial.print("# DIAG,RX_CHIP,rp2350_revision=");
            Serial.println(rp2350_chip_version());
            Serial.print("# DIAG,RX_SYNC_LEVEL,gp14=");
            Serial.println(digitalRead(RX_SYNC_PIN));
            Serial.print("# PICOWBELL,RTC_");
            Serial.println(!picowbell_rtc_probed
                ? "DEFERRED"
                : (picowbell_rtc_ready ? "OK" : "NOT_FOUND"));
            Serial.print("# SD,DIAG,code=0x"); Serial.print(sd_error_code, HEX);
            Serial.print(",data=0x"); Serial.println(sd_error_data, HEX);
        } else if (command == BURST_REARM_CHAR && state == RX_DONE) {
            digitalWrite(LED_BUILTIN, HIGH);
            state = RX_IDLE;
            Serial.println("# ID:RX;STATE=ARMED");
        } else if (command == SD_DUMP_CHAR &&
                   (state == RX_IDLE || state == RX_WAIT_SYNC || state == RX_DONE)) {
            dumpSdFile(
                sd_log_name, "# SD,DUMP_BEGIN,", "# SD,DUMP_END,");
        } else if (command == SD_EDGE_DUMP_CHAR &&
                   (state == RX_IDLE || state == RX_WAIT_SYNC || state == RX_DONE)) {
            dumpSdFile(
                sd_edge_log_name,
                "# SD,EDGE_DUMP_BEGIN,", "# SD,EDGE_DUMP_END,");
        } else if (command == SD_REINIT_CHAR && !sd_ready &&
                   (state == RX_IDLE || state == RX_WAIT_SYNC || state == RX_DONE)) {
            sd_ready = initSdLog();
            Serial.print("# SD,");
            Serial.print(sd_ready ? "READY," : "ERROR,");
            Serial.println(sd_ready ? sd_log_name : sd_error);
        }
    }

    switch (state) {
    case RX_IDLE:
        seq++;
        detected = false;
        range_m = 0;
        result_status = "NO_DETECT";
        high_streak = 0;
        detect_confirm_samples = 0;
        detect_sample_count = 0;
        detect_high_samples = 0;
        detect_rising_edges = 0;
        detect_first_high_us = 0;
        detect_max_high_streak = 0;
        comp_rising_irq_count = 0;
        comp_first_rising_us = 0;
        comp_last_rising_us = 0;
        comp_rising_irq_at_sync = 0;
        raw_previous_gpio = gpio_get_all();
        raw_seen_high_mask = raw_previous_gpio;
        raw_rising_mask = 0;
        raw_sample_count = 0;
        digital_raw_mismatch_count = 0;
        for (uint32_t pin = 0; pin < RX_DIAG_GPIO_COUNT; ++pin) {
            raw_rising_count[pin] = 0;
        }
        resetEchoCapture();
        noInterrupts();
        rise_capture_active = false;
        rise_event_count = 0;
        rise_event_dropped = 0;
        interrupts();
        state = RX_WAIT_SYNC;
        state_enter_us = now;
        break;

    case RX_WAIT_SYNC:
        if (digitalRead(RX_SYNC_PIN) == SYNC_ACTIVE_LEVEL) {
            t_sync_us = micros();
            edge_total_at_sync = edge_total;
            polled_edge_total_at_sync = polled_edge_total;
            comp_rising_irq_at_sync = comp_rising_irq_count;
            noInterrupts();
            rise_event_count = 0;
            rise_event_dropped = 0;
            rise_capture_active = true;
            interrupts();
            state = RX_BLANKING;
            state_enter_us = t_sync_us;
        } else if ((uint32_t)(now - state_enter_us) > 100000UL) {
            state = RX_IDLE;
        }
        break;

    case RX_BLANKING:
        if ((uint32_t)(now - t_sync_us) >= (uint32_t)BLANKING_TIME_US) {
            high_streak = 0;
            cand_edge_us = 0;
            last_sample_us = now;
            previous_level = digitalRead(RX_COMP_PIN);
            state = RX_DETECT;
        }
        break;

    case RX_DETECT: {
        const uint32_t window_end = t_sync_us + MAX_TOF_US + RECEIVE_MARGIN_US;
        if ((uint32_t)(now - last_sample_us) >= (uint32_t)DETECT_SAMPLE_US) {
            last_sample_us = now;
            const uint32_t raw_levels = recordRawGpioSample();
            const int digital_level = digitalRead(RX_COMP_PIN);
            const int raw_level = (int)((raw_levels >> RX_COMP_PIN) & 1UL);
            if (digital_level != raw_level) digital_raw_mismatch_count++;
            const int level = digital_level;
            detect_sample_count++;
            if (level == HIGH) {
                detect_high_samples++;
                if (detect_first_high_us == 0) detect_first_high_us = now;
                if (previous_level == LOW) detect_rising_edges++;
                if (high_streak == 0) cand_edge_us = now;
                high_streak++;
                if (high_streak > detect_max_high_streak) {
                    detect_max_high_streak = high_streak;
                }
                if (high_streak >= DETECT_CONFIRM_N) {
                    t_detect_us = cand_edge_us;
                    detected = true;
                    detect_confirm_samples = high_streak;
                    startEcho(t_detect_us, high_streak);
                    current_echo.end_us = now;
                    previous_level = HIGH;
                    post_capture_start_us = now;
                    state = RX_POST_CAPTURE;
                    break;
                }
            } else {
                high_streak = 0;
            }
            previous_level = level;
        }
        if ((int32_t)(now - window_end) >= 0) {
            detected = false;
            post_capture_start_us = now;
            state = RX_POST_CAPTURE;
        }
        break;
    }

    case RX_POST_CAPTURE:
        if ((uint32_t)(now - last_sample_us) >= (uint32_t)DETECT_SAMPLE_US) {
            last_sample_us = now;
            const uint32_t raw_levels = recordRawGpioSample();
            const int digital_level = digitalRead(RX_COMP_PIN);
            const int raw_level = (int)((raw_levels >> RX_COMP_PIN) & 1UL);
            if (digital_level != raw_level) digital_raw_mismatch_count++;
            recordEchoSample(now, digital_level);
        }
        if ((uint32_t)(now - post_capture_start_us) >=
            (uint32_t)(POST_DETECT_CAPTURE_MS * 1000UL)) {
            rise_capture_active = false;
            finishCurrentEcho();
            edge_snapshot_total = edge_total;
            polled_edge_snapshot_total = polled_edge_total;
            state = RX_RANGE_CALC;
        }
        break;

    case RX_RANGE_CALC:
        if (detected) {
            const uint32_t raw_tof_us = t_detect_us - t_sync_us;
            if (raw_tof_us <= SYSTEM_DELAY_US) {
                range_m = 0;
                result_status = "OUT_OF_RANGE";
            } else {
                const uint32_t tof_us = raw_tof_us - SYSTEM_DELAY_US;
                range_m = (tof_us / 1000000.0f) * SOUND_SPEED_MPS;
                result_status = (range_m < MIN_RANGE_M || range_m > MAX_RANGE_M)
                    ? "OUT_OF_RANGE" : "VALID";
            }
        } else {
            result_status = "NO_DETECT";
        }
        state = RX_LOG_OUTPUT;
        break;

    case RX_LOG_OUTPUT: {
        const uint32_t raw_tof_us = detected ? (t_detect_us - t_sync_us) : 0;
        const uint32_t tof_us = (detected && raw_tof_us > SYSTEM_DELAY_US)
            ? (raw_tof_us - SYSTEM_DELAY_US) : 0;
        const int score = (result_status == "VALID") ? 8 : 0;
        digitalWrite(LED_BUILTIN, LOW);
        if (!sd_ready && strcmp(sd_error, "DEFERRED") == 0) {
            initializeDeferredStorage();
        }

        Serial.print("# DIAG,RX_CAPTURE,"); Serial.print(seq);
        Serial.print(",sync_us="); Serial.print(t_sync_us);
        Serial.print(",detect_us="); Serial.print(detected ? t_detect_us : 0);
        Serial.print(",duration_ms="); Serial.print(POST_DETECT_CAPTURE_MS);
        Serial.print(",groups="); Serial.print(echo_group_count);
        Serial.print(",dropped="); Serial.println(echo_group_dropped);
        Serial.print("# DIAG,RX_SD_RISE_CAPTURE,"); Serial.print(seq);
        Serial.print(",captured="); Serial.print((uint32_t)rise_event_count);
        Serial.print(",dropped="); Serial.println((uint32_t)rise_event_dropped);
        const bool sd_edge_write_ok = appendSdRiseEvents();
        Serial.print("# SD,");
        Serial.print(sd_edge_write_ok ? "EDGE_WRITE_OK," : "EDGE_WRITE_ERROR,");
        Serial.print(sd_edge_log_name);
        Serial.print(",count="); Serial.print((uint32_t)rise_event_count);
        Serial.print(",dropped="); Serial.println((uint32_t)rise_event_dropped);
        Serial.print("# DIAG,RX_DETECT,"); Serial.print(seq);
        Serial.print(",samples="); Serial.print(detect_sample_count);
        Serial.print(",high_samples="); Serial.print(detect_high_samples);
        Serial.print(",rising_edges="); Serial.print(detect_rising_edges);
        Serial.print(",max_high_streak="); Serial.print(detect_max_high_streak);
        Serial.print(",irq_rising_edges=");
        Serial.print((uint32_t)(comp_rising_irq_count - comp_rising_irq_at_sync));
        Serial.print(",first_high_from_sync_us=");
        Serial.println(detect_first_high_us == 0 ? 0 : detect_first_high_us - t_sync_us);
        Serial.print("# DIAG,RX_RAW_GPIO,"); Serial.print(seq);
        Serial.print(",raw_samples="); Serial.print(raw_sample_count);
        Serial.print(",seen_high_mask=0x"); Serial.print(raw_seen_high_mask, HEX);
        Serial.print(",rising_mask=0x"); Serial.print(raw_rising_mask, HEX);
        Serial.print(",digital_raw_mismatch="); Serial.print(digital_raw_mismatch_count);
        Serial.print(",irq_before_sync="); Serial.print(comp_rising_irq_at_sync);
        Serial.print(",irq_total="); Serial.print(comp_rising_irq_count);
        Serial.print(",first_rise_from_sync_us=");
        Serial.print(comp_first_rising_us == 0
            ? 0 : (int32_t)(comp_first_rising_us - t_sync_us));
        Serial.print(",last_rise_from_sync_us=");
        Serial.println(comp_last_rising_us == 0
            ? 0 : (int32_t)(comp_last_rising_us - t_sync_us));
        for (uint32_t pin = 0; pin < RX_DIAG_GPIO_COUNT; ++pin) {
            if (raw_rising_count[pin] == 0) continue;
            Serial.print("# DIAG,RX_GPIO_EDGE,"); Serial.print(seq);
            Serial.print(",pin="); Serial.print(pin);
            Serial.print(",rising_edges="); Serial.println(raw_rising_count[pin]);
        }
        for (uint16_t i = 0; i < echo_group_count; ++i) {
            const EchoGroup &echo = echo_groups[i];
            Serial.print("# DIAG,RX_ECHO,"); Serial.print(seq);
            Serial.print(",index="); Serial.print(i);
            Serial.print(",start_from_sync_us="); Serial.print(echo.start_us - t_sync_us);
            Serial.print(",end_from_sync_us="); Serial.print(echo.end_us - t_sync_us);
            Serial.print(",rising_edges="); Serial.print(echo.rising_edges);
            Serial.print(",high_samples="); Serial.println(echo.high_samples);
        }

        const uint32_t edge_available = edge_snapshot_total < RX_EDGE_RING_CAPACITY
            ? edge_snapshot_total : RX_EDGE_RING_CAPACITY;
        const uint32_t edge_first_seq = edge_snapshot_total - edge_available;
        const uint32_t edge_overwritten = edge_snapshot_total - edge_available;
        const uint32_t edge_pre_sync = edge_total_at_sync > edge_first_seq
            ? edge_total_at_sync - edge_first_seq : 0;
        Serial.print("# DIAG,RX_EDGE_RING,"); Serial.print(seq);
        Serial.print(",capacity="); Serial.print(RX_EDGE_RING_CAPACITY);
        Serial.print(",snapshot_total="); Serial.print(edge_snapshot_total);
        Serial.print(",available="); Serial.print(edge_available);
        Serial.print(",overwritten="); Serial.print(edge_overwritten);
        Serial.print(",pre_sync_available="); Serial.println(edge_pre_sync);
        for (uint32_t edge_seq = edge_first_seq;
             edge_seq < edge_snapshot_total; ++edge_seq) {
            const uint32_t edge_index = edge_seq & (RX_EDGE_RING_CAPACITY - 1);
            const uint32_t edge_us = edge_ring[edge_index].time_us;
            const uint8_t edge_level = edge_ring[edge_index].level;
            Serial.print("# EDGE,"); Serial.print(seq);
            Serial.print(","); Serial.print(edge_seq);
            Serial.print(","); Serial.print((int32_t)(edge_us - t_sync_us));
            Serial.print(","); Serial.println(edge_level);
        }
        Serial.print("# DIAG,RX_EDGE_DUMP_END,"); Serial.print(seq);
        Serial.print(",new_edges_during_dump=");
        Serial.println((uint32_t)(edge_total - edge_snapshot_total));

        const uint32_t polled_available =
            polled_edge_snapshot_total < RX_EDGE_RING_CAPACITY
                ? polled_edge_snapshot_total : RX_EDGE_RING_CAPACITY;
        const uint32_t polled_first_seq =
            polled_edge_snapshot_total - polled_available;
        const uint32_t polled_overwritten =
            polled_edge_snapshot_total - polled_available;
        const uint32_t polled_pre_sync =
            polled_edge_total_at_sync > polled_first_seq
                ? polled_edge_total_at_sync - polled_first_seq : 0;
        Serial.print("# DIAG,RX_POLLED_EDGE_RING,"); Serial.print(seq);
        Serial.print(",capacity="); Serial.print(RX_EDGE_RING_CAPACITY);
        Serial.print(",snapshot_total="); Serial.print(polled_edge_snapshot_total);
        Serial.print(",available="); Serial.print(polled_available);
        Serial.print(",overwritten="); Serial.print(polled_overwritten);
        Serial.print(",pre_sync_available="); Serial.println(polled_pre_sync);
        for (uint32_t edge_seq = polled_first_seq;
             edge_seq < polled_edge_snapshot_total; ++edge_seq) {
            const uint32_t edge_index = edge_seq & (RX_EDGE_RING_CAPACITY - 1);
            const GpioEdgeEvent &edge = polled_edge_ring[edge_index];
            Serial.print("# POLLEDGE,"); Serial.print(seq);
            Serial.print(","); Serial.print(edge_seq);
            Serial.print(",");
            Serial.print((int32_t)(edge.time_us - t_sync_us));
            Serial.print(","); Serial.println(edge.level);
        }
        Serial.print("# DIAG,RX_POLLED_EDGE_DUMP_END,"); Serial.print(seq);
        Serial.print(",new_edges_during_dump=");
        Serial.println((uint32_t)(polled_edge_total - polled_edge_snapshot_total));

        Serial.print(seq); Serial.print(",");
        Serial.print(t_sync_us); Serial.print(",");
        Serial.print(detected ? t_detect_us : 0); Serial.print(",");
        Serial.print(tof_us); Serial.print(",");
        Serial.print(range_m, 3); Serial.print(",");
        Serial.print(score); Serial.print(",");
        Serial.print(detect_confirm_samples); Serial.print(",");
        Serial.print(0); Serial.print(",");
        Serial.println(result_status);
        const bool sd_write_ok = appendSdResult(tof_us, score);
        Serial.print("# SD,");
        Serial.print(sd_write_ok ? "WRITE_OK," : "WRITE_ERROR,");
        Serial.println(sd_log_name);
        state = RX_DONE;
        break;
    }

    case RX_DONE:
        // Re-arm only after the RS485-delivered
        // SYNC pulse has returned LOW, preventing one pulse from being counted
        // twice while allowing unattended operation.
        if (digitalRead(RX_SYNC_PIN) == SYNC_IDLE_LEVEL) {
            digitalWrite(LED_BUILTIN, HIGH);
            state = RX_IDLE;
        }
        break;
    }
}

#endif
