#include <Arduino.h>
#include <SPI.h>
#include <SD.h>
#include "hardware/clocks.h"
#include "hardware/gpio.h"
#include "hardware/pwm.h"
#include "pico/time.h"
#include "aquabeacon_config.h"
#include "aquabeacon_time_link.h"

#if !RANGING_MODE_BURST5
#error "rx_improved_mini_test requires RANGING_MODE_BURST5=1"
#endif

enum RxMiniState {
    RX_ARM,
    RX_WAIT_SYNC,
    RX_CAPTURE,
    RX_CALCULATE,
    RX_WAIT_TIME,
    RX_SAVE
};

struct CaptureSample {
    uint32_t from_sync_us;
    uint16_t env_adc_raw;
    uint8_t cmp_out;
    uint8_t reserved;
};

struct CmpEdge {
    uint32_t from_sync_us;
    uint8_t level;
    uint8_t reserved[3];
};

static_assert(
    RX_MINI_CAPTURE_CAPACITY * sizeof(CaptureSample) <
        RX_MINI_CAPTURE_RAM_BUDGET_BYTES,
    "RX mini capture buffer exceeds the configured RAM budget");
static_assert(
    RX_MINI_CMP_EDGE_CAPACITY * sizeof(CmpEdge) <=
        RX_MINI_CMP_EDGE_RAM_BUDGET_BYTES,
    "RX mini CMP edge buffer exceeds the configured RAM budget");
static_assert(
    TIME_LINK_SEND_DELAY_US > RX_MINI_CAPTURE_DURATION_US,
    "Shared SYNC time frame must start after RX capture");
static_assert(
    TIME_LINK_RX_TIMEOUT_US >
        TIME_LINK_SEND_DELAY_US + TIME_LINK_MAX_FRAME_US,
    "RX time-frame timeout is too short");

static RxMiniState state = RX_ARM;
static volatile bool sync_armed = false;
static volatile bool sync_pending = false;
static volatile bool capture_active = false;
static volatile uint32_t irq_sync_us = 0;
static volatile uint64_t irq_sync_us64 = 0;
static volatile uint32_t first_valid_rise_us = 0;
static volatile uint32_t burst_rise_count = 0;
static volatile uint32_t cmp_edge_count = 0;
static volatile uint32_t cmp_edge_dropped = 0;
static volatile CmpEdge cmp_edges[RX_MINI_CMP_EDGE_CAPACITY] = {};

static uint32_t seq = 0;
static uint32_t t_sync_us = 0;
static uint32_t t_detect_us = 0;
static uint32_t last_capture_sample_us = 0;
static uint16_t capture_threshold_mv = RX_MINI_TH_INITIAL_MV;
static uint16_t threshold_mv = RX_MINI_TH_INITIAL_MV;
static uint16_t env_peak_raw = 0;
static uint16_t baseline_env_raw = 0;
static uint16_t next_threshold_mv = RX_MINI_TH_INITIAL_MV;
static uint32_t noise_last_sample_us = 0;
static uint32_t noise_sample_count = 0;
static uint32_t noise_high_count = 0;
static uint64_t baseline_adc_sum = 0;
static uint32_t baseline_adc_count = 0;
static float range_m = 0.0f;
static const char *status_text = "NO_DETECT";
static bool auto_threshold_enabled = true;

static CaptureSample capture_samples[RX_MINI_CAPTURE_CAPACITY] = {};
static uint16_t capture_count = 0;
static uint32_t capture_dropped = 0;

static bool sd_ready = false;
static char sd_shot_name[13] = {};
static uint16_t next_sd_shot_index = SD_LOG_FILE_LIMIT;
static uint32_t last_sd_file_size = 0;
static const char *sd_error = "NOT_STARTED";

static volatile bool time_frame_rx_enabled = false;
static volatile bool time_frame_active = false;
static volatile bool time_frame_pulse_active = false;
static volatile uint32_t time_frame_pulse_start_us = 0;
static volatile uint32_t time_frame_last_edge_us = 0;
static volatile uint16_t time_frame_bit_count = 0;
static volatile uint8_t
    time_frame_payload[TIME_LINK_PAYLOAD_BYTES] = {};
static volatile bool time_frame_ready = false;
static volatile uint64_t time_frame_received_local_us = 0;
static bool rx_clock_valid = false;
static uint64_t rx_clock_anchor_epoch_us = 0;
static uint64_t rx_clock_anchor_local_us = 0;
static uint64_t shot_epoch_us = 0;
static uint64_t shot_sync_local_us = 0;
static uint64_t shot_time_packet_age_us = 0;
static uint32_t shot_time_sequence = 0;
static uint8_t shot_time_flags = 0;
static uint8_t shot_gps_rx_pin = TIME_LINK_UNKNOWN_PIN;
static const char *shot_time_source = "UNSYNCED";

static uint th_pwm_slice = 0;
static uint th_pwm_channel = 0;
static uint32_t th_pwm_period_counts = 0;

static constexpr uint32_t minTofUs() {
    return (uint32_t)((MIN_RANGE_M / SOUND_SPEED_MPS) * 1000000.0f);
}

static uint16_t adcRawToMv(uint16_t raw) {
    return (uint16_t)(
        ((uint32_t)raw * PICO_LOGIC_SUPPLY_MV + PICO_ADC_MAX_RAW / 2UL) /
        PICO_ADC_MAX_RAW);
}

static uint16_t envAdcMvToComparatorMv(uint16_t env_adc_mv) {
    return (uint16_t)(
        ((uint32_t)env_adc_mv * RX_MINI_ENV_TO_COMP_NUM) /
        RX_MINI_ENV_TO_COMP_DEN);
}

static void onSyncLineChange() {
    const uint32_t now_us = time_us_32();
    const bool active =
        gpio_get(RX_SYNC_PIN) == (uint)SYNC_ACTIVE_LEVEL;

    if (time_frame_rx_enabled) {
        if (time_frame_active &&
            (uint32_t)(now_us - time_frame_last_edge_us) >
                TIME_LINK_EDGE_TIMEOUT_US) {
            time_frame_active = false;
            time_frame_bit_count = 0;
        }
        time_frame_last_edge_us = now_us;
        if (active) {
            time_frame_pulse_start_us = now_us;
            time_frame_pulse_active = true;
            return;
        }
        if (!time_frame_pulse_active) return;
        time_frame_pulse_active = false;
        const uint32_t width_us =
            now_us - time_frame_pulse_start_us;
        if (!time_frame_active) {
            if (width_us >= TIME_LINK_PREAMBLE_MIN_US &&
                width_us <= TIME_LINK_PREAMBLE_MAX_US) {
                time_frame_active = true;
                time_frame_bit_count = 0;
                for (uint8_t index = 0;
                     index < TIME_LINK_PAYLOAD_BYTES; ++index) {
                    time_frame_payload[index] = 0;
                }
            }
            return;
        }

        if (width_us < TIME_LINK_BIT_ZERO_US / 2 ||
            width_us > TIME_LINK_BIT_ONE_US +
                TIME_LINK_BIT_ZERO_US / 2) {
            time_frame_active = false;
            time_frame_bit_count = 0;
            return;
        }
        const uint16_t bit_index = time_frame_bit_count++;
        if (width_us >= TIME_LINK_BIT_THRESHOLD_US) {
            time_frame_payload[bit_index / 8] |=
                (uint8_t)(0x80U >> (bit_index % 8));
        }
        if (time_frame_bit_count >= TIME_LINK_PAYLOAD_BITS) {
            time_frame_received_local_us = time_us_64();
            time_frame_ready = true;
            time_frame_active = false;
            time_frame_rx_enabled = false;
        }
        return;
    }

    if (!active) return;
    if (!sync_armed || sync_pending) return;
    const uint64_t sync_us = time_us_64();
    irq_sync_us64 = sync_us;
    irq_sync_us = (uint32_t)sync_us;
    first_valid_rise_us = 0;
    burst_rise_count = 0;
    cmp_edge_count = 0;
    cmp_edge_dropped = 0;
    capture_active = true;
    sync_pending = true;
    sync_armed = false;
}

static void latchShotTime(uint64_t sync_local_us) {
    shot_epoch_us = 0;
    shot_sync_local_us = sync_local_us;
    shot_time_packet_age_us = 0;
    shot_time_sequence = 0;
    shot_time_flags = 0;
    shot_gps_rx_pin = TIME_LINK_UNKNOWN_PIN;
    shot_time_source = "UNSYNCED";

    if (rx_clock_valid && sync_local_us >= rx_clock_anchor_local_us) {
        shot_epoch_us =
            rx_clock_anchor_epoch_us +
            (sync_local_us - rx_clock_anchor_local_us);
        shot_time_flags = TIME_FLAG_UTC_VALID | TIME_FLAG_RX_HOLDOVER;
        shot_time_source = "RX_HOLDOVER";
    }
}

static bool consumeShotTimeFrame() {
    uint8_t payload[TIME_LINK_PAYLOAD_BYTES] = {};
    uint64_t received_local_us = 0;
    noInterrupts();
    if (!time_frame_ready) {
        interrupts();
        return false;
    }
    for (uint8_t index = 0;
         index < TIME_LINK_PAYLOAD_BYTES; ++index) {
        payload[index] = time_frame_payload[index];
    }
    received_local_us = time_frame_received_local_us;
    time_frame_ready = false;
    interrupts();

    AquaBeaconTimePacket packet = {};
    if (!aqUnpackTimePacket(payload, &packet)) {
        Serial.println("# TIME_LINK,CRC_ERROR");
        return false;
    }

    shot_time_packet_age_us =
        received_local_us >= shot_sync_local_us
        ? received_local_us - shot_sync_local_us : 0;
    shot_time_sequence = packet.sequence;
    shot_time_flags = packet.flags;
    shot_gps_rx_pin = packet.gps_rx_pin;
    if ((packet.flags & TIME_FLAG_UTC_VALID) != 0 &&
        packet.shot_epoch_us != 0) {
        shot_epoch_us = packet.shot_epoch_us;
        rx_clock_valid = true;
        rx_clock_anchor_epoch_us = shot_epoch_us;
        rx_clock_anchor_local_us = shot_sync_local_us;
        shot_time_source =
            (packet.flags & TIME_FLAG_GPS_PPS) != 0
            ? "GPS_PPS" :
            ((packet.flags & TIME_FLAG_TX_HOLDOVER) != 0
                ? "TX_HOLDOVER" : "GPS_NMEA");
    }
    Serial.print("# TIME_LINK,RX,seq=");
    Serial.print(packet.sequence);
    Serial.print(",epoch_us=");
    Serial.print((unsigned long long)packet.shot_epoch_us);
    Serial.print(",flags=");
    Serial.println(packet.flags);
    return true;
}

static void onComparatorChange() {
    if (!capture_active) return;

    const uint32_t edge_us = time_us_32();
    const uint8_t level = gpio_get(RX_MINI_COMP_PIN) ? 1 : 0;
    const uint32_t from_sync_us = edge_us - irq_sync_us;

    const uint32_t edge_index = cmp_edge_count;
    if (edge_index < RX_MINI_CMP_EDGE_CAPACITY) {
        cmp_edges[edge_index].from_sync_us = from_sync_us;
        cmp_edges[edge_index].level = level;
        cmp_edge_count = edge_index + 1;
    } else {
        cmp_edge_dropped++;
    }

    if (level == 0) return;
    if (from_sync_us < minTofUs()) return;
    if (from_sync_us > (uint32_t)RX_MINI_CAPTURE_DURATION_US) return;

    if (first_valid_rise_us == 0) {
        first_valid_rise_us = edge_us;
        burst_rise_count = 1;
    } else if ((uint32_t)(edge_us - first_valid_rise_us) <=
               RX_MINI_BURST_SCORE_WINDOW_US) {
        burst_rise_count++;
    }
}

static void setThresholdMv(uint16_t requested_mv) {
    if (requested_mv < RX_MINI_TH_MIN_MV) requested_mv = RX_MINI_TH_MIN_MV;
    if (requested_mv > RX_MINI_TH_MAX_MV) requested_mv = RX_MINI_TH_MAX_MV;
    threshold_mv = requested_mv;

    uint32_t level =
        ((uint32_t)threshold_mv * th_pwm_period_counts +
         PICO_LOGIC_SUPPLY_MV / 2UL) / PICO_LOGIC_SUPPLY_MV;
    if (level >= th_pwm_period_counts) level = th_pwm_period_counts - 1;
    pwm_set_chan_level(th_pwm_slice, th_pwm_channel, level);
}

static void initThresholdPwm() {
    gpio_set_function(RX_MINI_TH_PWM_PIN, GPIO_FUNC_PWM);
    th_pwm_slice = pwm_gpio_to_slice_num(RX_MINI_TH_PWM_PIN);
    th_pwm_channel = pwm_gpio_to_channel(RX_MINI_TH_PWM_PIN);

    th_pwm_period_counts =
        clock_get_hz(clk_sys) / (uint32_t)RX_MINI_TH_PWM_FREQ_HZ;
    if (th_pwm_period_counts < RX_MINI_TH_PWM_MIN_COUNTS) {
        th_pwm_period_counts = RX_MINI_TH_PWM_MIN_COUNTS;
    }

    pwm_config config = pwm_get_default_config();
    pwm_config_set_clkdiv(&config, 1.0f);
    pwm_config_set_wrap(&config, th_pwm_period_counts - 1UL);
    pwm_init(th_pwm_slice, &config, true);
    setThresholdMv(threshold_mv);
}

static void selectNextSdShotName(uint16_t start_index) {
    next_sd_shot_index = SD_LOG_FILE_LIMIT;
    for (uint16_t index = start_index; index < SD_LOG_FILE_LIMIT; ++index) {
        char candidate[13] = {};
        snprintf(candidate, sizeof(candidate), "S%03u.CSV", index);
        if (!SD.exists(candidate)) {
            next_sd_shot_index = index;
            return;
        }
    }
}

static bool initSdCard() {
    pinMode(SD_CS_PIN, OUTPUT);
    digitalWrite(SD_CS_PIN, HIGH);
    if (!SPI.setRX(SD_MISO_PIN) || !SPI.setTX(SD_MOSI_PIN) ||
        !SPI.setSCK(SD_SCK_PIN)) {
        sd_error = "INVALID_SPI_PIN";
        return false;
    }
    if (!SD.begin(SD_CS_PIN)) {
        SD.end();
        if (!SD.begin(SD_CS_PIN, SPI_EIGHTH_SPEED, SPI)) {
            sd_error = "MOUNT_FAILED";
            return false;
        }
    }

    sd_shot_name[0] = '\0';
    for (uint16_t index = 0; index < SD_LOG_FILE_LIMIT; ++index) {
        char candidate[13] = {};
        snprintf(candidate, sizeof(candidate), "S%03u.CSV", index);
        if (SD.exists(candidate)) {
            snprintf(sd_shot_name, sizeof(sd_shot_name), "%s", candidate);
        }
    }
    selectNextSdShotName(0);
    if (next_sd_shot_index >= SD_LOG_FILE_LIMIT) {
        sd_error = "NO_FREE_SHOT_NAME";
        return false;
    }
    sd_error = "NONE";
    return true;
}

static bool writeShotFile(
        uint32_t tof_us, int score, int peak_pct, int noise_pct) {
    if (!sd_ready) return false;
    if (next_sd_shot_index >= SD_LOG_FILE_LIMIT) {
        sd_error = "NO_FREE_SHOT_NAME";
        return false;
    }

    char new_name[13] = {};
    snprintf(new_name, sizeof(new_name), "S%03u.CSV", next_sd_shot_index);
    if (SD.exists(new_name)) {
        sd_error = "SHOT_NAME_COLLISION";
        return false;
    }

    File file = SD.open(new_name, FILE_WRITE);
    if (!file) {
        sd_error = "SHOT_CREATE_FAILED";
        return false;
    }

    const uint32_t edge_count = cmp_edge_count;
    const uint32_t edge_rows =
        edge_count < RX_MINI_SD_EDGE_ROWS_MAX
        ? edge_count : RX_MINI_SD_EDGE_ROWS_MAX;
    const uint32_t edge_omitted =
        (edge_count - edge_rows) + cmp_edge_dropped;

    file.println("# AquaBeacon RX mini shot");
    file.print("# seq,"); file.println(seq);
    file.print("# t_sync_us,"); file.println(t_sync_us);
    file.print("# t_detect_us,"); file.println(t_detect_us);
    file.print("# threshold_mv,"); file.println(capture_threshold_mv);
    file.print("# threshold_auto,");
    file.println(auto_threshold_enabled ? 1 : 0);
    file.print("# threshold_next_mv,"); file.println(next_threshold_mv);
    file.print("# capture_duration_us,");
    file.println(RX_MINI_CAPTURE_DURATION_US);
    file.print("# max_range_m,"); file.println(MAX_RANGE_M, 1);
    file.print("# capture_samples,"); file.println(capture_count);
    file.print("# capture_dropped,"); file.println(capture_dropped);
    file.print("# cmp_edges_captured,"); file.println(edge_count);
    file.print("# cmp_edges_written,"); file.println(edge_rows);
    file.print("# cmp_edges_omitted,"); file.println(edge_omitted);
    char utc_iso[32] = {};
    const bool utc_valid =
        aqFormatIso8601(shot_epoch_us, utc_iso, sizeof(utc_iso));
    file.print("# utc_valid,"); file.println(utc_valid ? 1 : 0);
    file.print("# utc_iso,");
    file.println(utc_valid ? utc_iso : "UNSYNCED");
    file.print("# utc_epoch_us,");
    file.println((unsigned long long)shot_epoch_us);
    file.print("# time_source,"); file.println(shot_time_source);
    file.print("# time_sequence,"); file.println(shot_time_sequence);
    file.print("# time_flags,"); file.println(shot_time_flags);
    file.print("# gps_rx_pin,");
    if (shot_gps_rx_pin == TIME_LINK_UNKNOWN_PIN) {
        file.println("UNKNOWN");
    } else {
        file.print("GP");
        file.println(shot_gps_rx_pin);
    }
    file.print("# time_packet_age_us,");
    file.println((unsigned long long)shot_time_packet_age_us);
    file.print("# rx_sync_local_us,");
    file.println((unsigned long long)shot_sync_local_us);
    file.println(
        "record_type,index,t_from_sync_us,env_adc_raw,env_adc_mv,"
        "cmp_out,threshold_mv,tof_us,range_m,score,rx_peak_pct,"
        "noise_high_pct,status");
    file.print("SUMMARY,0,0,,,,");
    file.print(capture_threshold_mv); file.print(',');
    file.print(tof_us); file.print(',');
    file.print(range_m, 3); file.print(',');
    file.print(score); file.print(',');
    file.print(peak_pct); file.print(',');
    file.print(noise_pct); file.print(',');
    file.println(status_text);

    for (uint16_t index = 0; index < capture_count; ++index) {
        const CaptureSample &sample = capture_samples[index];
        file.print("ADC,");
        file.print(index); file.print(',');
        file.print(sample.from_sync_us); file.print(',');
        file.print(sample.env_adc_raw); file.print(',');
        file.print(adcRawToMv(sample.env_adc_raw)); file.print(',');
        file.print(sample.cmp_out); file.print(',');
        file.print(capture_threshold_mv);
        file.println(",,,,,,");
    }

    for (uint32_t index = 0; index < edge_rows; ++index) {
        file.print("EDGE,");
        file.print(index); file.print(',');
        file.print(cmp_edges[index].from_sync_us); file.print(',');
        file.print(",,");
        file.print(cmp_edges[index].level); file.print(',');
        file.print(capture_threshold_mv);
        file.println(",,,,,,");
    }

    file.flush();
    const bool ok = !file.getWriteError();
    last_sd_file_size = file.size();
    file.close();

    // A partially written file is retained and its name is never reused.
    snprintf(sd_shot_name, sizeof(sd_shot_name), "%s", new_name);
    selectNextSdShotName(next_sd_shot_index + 1);
    sd_error = ok ? "NONE" : "SHOT_WRITE_FAILED";
    return ok;
}

static void dumpSdFile(
        const char *name, const char *begin_tag, const char *end_tag) {
    if (!sd_ready) {
        Serial.print("# SD,DUMP_ERROR,");
        Serial.println(sd_error);
        return;
    }
    if (name[0] == '\0') {
        Serial.println("# SD,DUMP_ERROR,NO_SHOT_FILE");
        return;
    }
    File file = SD.open(name, FILE_READ);
    if (!file) {
        Serial.println("# SD,DUMP_ERROR,OPEN_FAILED");
        return;
    }
    Serial.print(begin_tag);
    Serial.println(name);
    while (file.available()) Serial.write(file.read());
    file.close();
    Serial.print(end_tag);
    Serial.println(name);
}

static uint16_t calculateNextThresholdMv() {
    const uint16_t baseline_adc_mv = adcRawToMv(baseline_env_raw);
    const uint16_t peak_adc_mv = adcRawToMv(env_peak_raw);
    const uint16_t baseline_comp_mv =
        envAdcMvToComparatorMv(baseline_adc_mv);
    const uint16_t peak_comp_mv = envAdcMvToComparatorMv(peak_adc_mv);
    const uint16_t noise_floor_mv =
        baseline_comp_mv + RX_MINI_AUTO_NOISE_MARGIN_MV;

    // 無給電・未接続時の浮遊ADCやSYNC回り込みでは調整しない。
    const bool credible_signal =
        t_detect_us != 0 &&
        peak_adc_mv >= RX_MINI_AUTO_MIN_PEAK_MV &&
        peak_adc_mv >=
            (uint16_t)(baseline_adc_mv + RX_MINI_AUTO_MIN_SIGNAL_MV);
    if (!credible_signal) {
        // NO_DETECTでは、長距離の微弱信号を探すためにしきい値を下げる。
        // ベースラインから求めたノイズ床と固定安全下限の高い方で止める。
        if (t_detect_us != 0) return threshold_mv;

        uint16_t search_floor_mv = noise_floor_mv;
        if (search_floor_mv < RX_MINI_AUTO_SEARCH_MIN_MV) {
            search_floor_mv = RX_MINI_AUTO_SEARCH_MIN_MV;
        }
        if (search_floor_mv > RX_MINI_TH_MAX_MV) {
            search_floor_mv = RX_MINI_TH_MAX_MV;
        }
        if (threshold_mv <= search_floor_mv) return threshold_mv;
        if (threshold_mv >
            search_floor_mv + RX_MINI_AUTO_NO_DETECT_STEP_MV) {
            return threshold_mv - RX_MINI_AUTO_NO_DETECT_STEP_MV;
        }
        return search_floor_mv;
    }

    uint16_t target_mv = threshold_mv;
    const uint32_t signal_span_mv = peak_comp_mv - baseline_comp_mv;
    target_mv = baseline_comp_mv +
        (signal_span_mv * RX_MINI_AUTO_THRESHOLD_PERCENT) / 100UL;
    if (target_mv < noise_floor_mv) target_mv = noise_floor_mv;

    if (target_mv > threshold_mv + RX_MINI_AUTO_MAX_STEP_MV) {
        target_mv = threshold_mv + RX_MINI_AUTO_MAX_STEP_MV;
    } else if (threshold_mv > target_mv + RX_MINI_AUTO_MAX_STEP_MV) {
        target_mv = threshold_mv - RX_MINI_AUTO_MAX_STEP_MV;
    }
    if (target_mv < RX_MINI_TH_MIN_MV) target_mv = RX_MINI_TH_MIN_MV;
    if (target_mv > RX_MINI_TH_MAX_MV) target_mv = RX_MINI_TH_MAX_MV;
    return target_mv;
}

static void printThreshold() {
    Serial.print("# THRESHOLD,mV=");
    Serial.print(threshold_mv);
    Serial.print(",auto=");
    Serial.print(auto_threshold_enabled ? 1 : 0);
    Serial.print(",pwm_hz=");
    Serial.println(RX_MINI_TH_PWM_FREQ_HZ);
}

static void handleSerial() {
    while (Serial.available() > 0) {
        const int command = Serial.read();
        if (command == '+') {
            auto_threshold_enabled = false;
            setThresholdMv(threshold_mv + RX_MINI_TH_STEP_MV);
            printThreshold();
        } else if (command == '-') {
            auto_threshold_enabled = false;
            setThresholdMv(threshold_mv > RX_MINI_TH_STEP_MV
                ? threshold_mv - RX_MINI_TH_STEP_MV
                : RX_MINI_TH_MIN_MV);
            printThreshold();
        } else if (command == 'A') {
            auto_threshold_enabled = !auto_threshold_enabled;
            printThreshold();
        } else if ((command == SD_DUMP_CHAR ||
                    command == SD_EDGE_DUMP_CHAR ||
                    command == RX_MINI_CMP_DUMP_CHAR) &&
                   state == RX_WAIT_SYNC) {
            dumpSdFile(
                sd_shot_name,
                "# SD,SHOT_DUMP_BEGIN,", "# SD,SHOT_DUMP_END,");
        } else if (command == SD_REINIT_CHAR &&
                   state == RX_WAIT_SYNC && !sd_ready) {
            sd_ready = initSdCard();
            Serial.print("# SD,");
            Serial.print(sd_ready ? "READY," : "ERROR,");
            Serial.println(sd_ready
                ? (sd_shot_name[0] == '\0' ? "NO_PREVIOUS_SHOT"
                                           : sd_shot_name)
                : sd_error);
        } else if (command == BURST_IDENTIFY_CHAR) {
            Serial.print("# ID:RX_MINI;STATE=");
            Serial.println(state == RX_WAIT_SYNC ? "ARMED" : "BUSY");
            printThreshold();
            Serial.print("# SD,");
            Serial.print(sd_ready ? "READY," : "ERROR,");
            Serial.println(sd_ready
                ? (sd_shot_name[0] == '\0' ? "NO_PREVIOUS_SHOT"
                                           : sd_shot_name)
                : sd_error);
            Serial.print("# TIME_LINK,SHARED_SYNC=GP");
            Serial.print(RX_SYNC_PIN);
            Serial.print(",clock_valid=");
            Serial.println(rx_clock_valid ? 1 : 0);
        }
    }
}

void setup() {
    Serial.begin(UART_BAUD);
    analogReadResolution(12);

    pinMode(RX_SYNC_PIN, INPUT);
    pinMode(RX_MINI_COMP_PIN, INPUT);
    pinMode(RX_MINI_ENV_ADC_PIN, INPUT);
    pinMode(LED_BUILTIN, OUTPUT);
    digitalWrite(LED_BUILTIN, LOW);

    initThresholdPwm();
    attachInterrupt(
        digitalPinToInterrupt(RX_SYNC_PIN), onSyncLineChange, CHANGE);
    attachInterrupt(
        digitalPinToInterrupt(RX_MINI_COMP_PIN), onComparatorChange, CHANGE);

    // 起動直後のSYNCをSD初期化で逃さないよう、最初の測定後に初期化する。
    sd_error = "DEFERRED_UNTIL_FIRST_MEASUREMENT";

    Serial.println("# AquaBeacon RX_improved_mini 5-wave capture");
    Serial.println(
        "# seq,t_sync_us,t_detect_us,tof_us,range_m,score,"
        "rx_peak_pct,noise_high_pct,status");
    Serial.print("# PINS,SYNC=GP"); Serial.print(RX_SYNC_PIN);
    Serial.print(",TH_PWM=GP"); Serial.print(RX_MINI_TH_PWM_PIN);
    Serial.print(",ENV_ADC=GP"); Serial.print(RX_MINI_ENV_ADC_PIN);
    Serial.print(",CMP_OUT=GP"); Serial.println(RX_MINI_COMP_PIN);
    Serial.print("# TIME_LINK,SHARED_SYNC=GP");
    Serial.print(RX_SYNC_PIN);
    Serial.print(",send_delay_us=");
    Serial.println((unsigned long)TIME_LINK_SEND_DELAY_US);
    Serial.print("# CAPTURE,sample_us=");
    Serial.print(RX_MINI_CAPTURE_SAMPLE_US);
    Serial.print(",duration_us=");
    Serial.print(RX_MINI_CAPTURE_DURATION_US);
    Serial.print(",capacity=");
    Serial.println(RX_MINI_CAPTURE_CAPACITY);
    printThreshold();
}

void loop() {
    const uint32_t now = micros();
    handleSerial();

    switch (state) {
    case RX_ARM:
        noInterrupts();
        sync_armed = false;
        sync_pending = false;
        capture_active = false;
        first_valid_rise_us = 0;
        burst_rise_count = 0;
        cmp_edge_count = 0;
        cmp_edge_dropped = 0;
        interrupts();

        t_sync_us = 0;
        t_detect_us = 0;
        capture_count = 0;
        capture_dropped = 0;
        env_peak_raw = 0;
        baseline_env_raw = 0;
        baseline_adc_sum = 0;
        baseline_adc_count = 0;
        noise_last_sample_us = now;
        noise_sample_count = 0;
        noise_high_count = 0;
        range_m = 0.0f;
        status_text = "NO_DETECT";
        shot_epoch_us = 0;
        shot_sync_local_us = 0;
        shot_time_packet_age_us = 0;
        shot_time_sequence = 0;
        shot_time_flags = 0;
        shot_gps_rx_pin = TIME_LINK_UNKNOWN_PIN;
        shot_time_source = "UNSYNCED";

        // Active-low SYNCの途中で再アームしない。
        if (digitalRead(RX_SYNC_PIN) == SYNC_IDLE_LEVEL) {
            noInterrupts();
            sync_armed = true;
            interrupts();
            state = RX_WAIT_SYNC;
            digitalWrite(LED_BUILTIN, HIGH);
        }
        break;

    case RX_WAIT_SYNC:
        if ((uint32_t)(now - noise_last_sample_us) >=
            RX_MINI_NOISE_SAMPLE_US) {
            noise_last_sample_us = now;
            const uint16_t raw = analogRead(RX_MINI_ENV_ADC_PIN);
            baseline_adc_sum += raw;
            baseline_adc_count++;
            noise_sample_count++;
            if (digitalRead(RX_MINI_COMP_PIN) == HIGH) noise_high_count++;
        }

        if (sync_pending) {
            noInterrupts();
            t_sync_us = irq_sync_us;
            const uint64_t sync_local_us = irq_sync_us64;
            sync_pending = false;
            interrupts();

            seq++;
            latchShotTime(sync_local_us);
            baseline_env_raw = baseline_adc_count == 0 ? 0 :
                (uint16_t)(baseline_adc_sum / baseline_adc_count);
            capture_threshold_mv = threshold_mv;
            last_capture_sample_us = t_sync_us - RX_MINI_CAPTURE_SAMPLE_US;
            state = RX_CAPTURE;
        }
        break;

    case RX_CAPTURE:
        if ((uint32_t)(now - last_capture_sample_us) >=
            RX_MINI_CAPTURE_SAMPLE_US) {
            last_capture_sample_us = now;
            const uint16_t env_raw = analogRead(RX_MINI_ENV_ADC_PIN);
            const uint8_t cmp = digitalRead(RX_MINI_COMP_PIN) == HIGH ? 1 : 0;
            if (env_raw > env_peak_raw) env_peak_raw = env_raw;

            if (capture_count < RX_MINI_CAPTURE_CAPACITY) {
                CaptureSample &sample = capture_samples[capture_count++];
                sample.from_sync_us = now - t_sync_us;
                sample.env_adc_raw = env_raw;
                sample.cmp_out = cmp;
                sample.reserved = 0;
            } else {
                capture_dropped++;
            }
        }

        if ((uint32_t)(now - t_sync_us) >=
            (uint32_t)RX_MINI_CAPTURE_DURATION_US) {
            noInterrupts();
            capture_active = false;
            t_detect_us = first_valid_rise_us;
            interrupts();
            state = RX_CALCULATE;
        }
        break;

    case RX_CALCULATE:
        if (t_detect_us == 0) {
            status_text = "NO_DETECT";
        } else {
            const uint32_t raw_tof_us = t_detect_us - t_sync_us;
            if (raw_tof_us <= SYSTEM_DELAY_US) {
                status_text = "OUT_OF_RANGE";
            } else {
                const uint32_t tof_us = raw_tof_us - SYSTEM_DELAY_US;
                range_m = tof_us * (SOUND_SPEED_MPS / 1000000.0f);
                status_text =
                    (range_m < MIN_RANGE_M || range_m > MAX_RANGE_M)
                    ? "OUT_OF_RANGE" : "VALID";
            }
        }

        next_threshold_mv = auto_threshold_enabled
            ? calculateNextThresholdMv() : threshold_mv;
        noInterrupts();
        time_frame_ready = false;
        time_frame_active = false;
        time_frame_pulse_active = false;
        time_frame_bit_count = 0;
        time_frame_rx_enabled = true;
        interrupts();
        state = RX_WAIT_TIME;
        break;

    case RX_WAIT_TIME:
        if (consumeShotTimeFrame()) {
            state = RX_SAVE;
        } else if (time_us_64() - shot_sync_local_us >=
                   TIME_LINK_RX_TIMEOUT_US) {
            noInterrupts();
            time_frame_rx_enabled = false;
            time_frame_active = false;
            time_frame_pulse_active = false;
            interrupts();
            Serial.println("# TIME_LINK,TIMEOUT");
            state = RX_SAVE;
        }
        break;

    case RX_SAVE: {
        const uint32_t raw_tof_us =
            t_detect_us == 0 ? 0 : (t_detect_us - t_sync_us);
        const uint32_t tof_us =
            raw_tof_us > SYSTEM_DELAY_US ? raw_tof_us - SYSTEM_DELAY_US : 0;
        const int score =
            status_text[0] == 'V' ? BURST5_VALID_SCORE : 0;
        const int peak_pct =
            (env_peak_raw * 100UL) / PICO_ADC_MAX_RAW;
        const int noise_pct = noise_sample_count == 0 ? 0
            : (noise_high_count * 100UL) / noise_sample_count;

        if (!sd_ready &&
            strcmp(sd_error, "DEFERRED_UNTIL_FIRST_MEASUREMENT") == 0) {
            sd_ready = initSdCard();
        }

        const bool shot_ok =
            writeShotFile(tof_us, score, peak_pct, noise_pct);

        Serial.print(seq); Serial.print(',');
        Serial.print(t_sync_us); Serial.print(',');
        Serial.print(t_detect_us); Serial.print(',');
        Serial.print(tof_us); Serial.print(',');
        Serial.print(range_m, 3); Serial.print(',');
        Serial.print(score); Serial.print(',');
        Serial.print(peak_pct); Serial.print(',');
        Serial.print(noise_pct); Serial.print(',');
        Serial.println(status_text);

        Serial.print("# DIAG,CAPTURE,seq="); Serial.print(seq);
        Serial.print(",samples="); Serial.print(capture_count);
        Serial.print(",dropped="); Serial.print(capture_dropped);
        Serial.print(",baseline_adc_mv=");
        Serial.print(adcRawToMv(baseline_env_raw));
        Serial.print(",peak_adc_mv="); Serial.print(adcRawToMv(env_peak_raw));
        Serial.print(",cmp_rises="); Serial.print((uint32_t)burst_rise_count);
        Serial.print(",cmp_edges="); Serial.print((uint32_t)cmp_edge_count);
        Serial.print(",cmp_dropped=");
        Serial.println((uint32_t)cmp_edge_dropped);
        Serial.print("# DIAG,AUTO_THRESHOLD,seq="); Serial.print(seq);
        Serial.print(",enabled="); Serial.print(auto_threshold_enabled ? 1 : 0);
        Serial.print(",used_mv="); Serial.print(capture_threshold_mv);
        Serial.print(",next_mv="); Serial.println(next_threshold_mv);
        char shot_iso[32] = {};
        const bool shot_utc_valid =
            aqFormatIso8601(
                shot_epoch_us, shot_iso, sizeof(shot_iso));
        Serial.print("# TIME,seq="); Serial.print(seq);
        Serial.print(",utc=");
        Serial.print(shot_utc_valid ? shot_iso : "UNSYNCED");
        Serial.print(",source="); Serial.print(shot_time_source);
        Serial.print(",tx_seq="); Serial.print(shot_time_sequence);
        Serial.print(",packet_age_us=");
        Serial.println(
            (unsigned long long)shot_time_packet_age_us);
        Serial.print("# SD,SHOT_");
        Serial.print(shot_ok ? "WRITE_OK," : "WRITE_ERROR,");
        Serial.print(shot_ok ? sd_shot_name : sd_error);
        Serial.print(",bytes=");
        Serial.println(last_sd_file_size);
        if (shot_ok &&
            last_sd_file_size > RX_MINI_SD_FILE_WARN_BYTES) {
            Serial.print("# SD,FILE_SIZE_WARNING,");
            Serial.print(sd_shot_name);
            Serial.print(",bytes=");
            Serial.println(last_sd_file_size);
        }

        if (auto_threshold_enabled) setThresholdMv(next_threshold_mv);
        digitalWrite(LED_BUILTIN, LOW);
        state = RX_ARM;
        break;
    }
    }
}
