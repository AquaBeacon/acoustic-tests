#include <Arduino.h>
#include <SerialPIO.h>
#include "aquabeacon_config.h"
#include "aquabeacon_time_link.h"

#if RANGING_MODE_BURST5

#include "hardware/clocks.h"
#include "hardware/gpio.h"
#include "hardware/pwm.h"
#include "pico/time.h"

#if BURST_FAILSAFE_US <= BURST_DURATION_US
#error "BURST_FAILSAFE_US must exceed BURST_DURATION_US"
#endif

static_assert(
    TIME_LINK_SEND_DELAY_US + TIME_LINK_MAX_FRAME_US <
        (uint64_t)BURST_REPEAT_INTERVAL_MS * 1000ULL,
    "Shared SYNC time frame overlaps the next ranging shot");

enum TxState {
    TX_WAIT_TRIGGER,
    TX_TIME_WAIT,
    TX_BURST,
    TX_SYNC_WAIT,
    TX_LOG,
    TX_TIME_SEND_WAIT,
    TX_DONE
};

static TxState state = TX_WAIT_TRIGGER;
static uint32_t seq = 0;
static uint32_t t_tx_us = 0;
static uint32_t t_carrier_on_us = 0;
static uint32_t t_carrier_off_us = 0;
static uint32_t boot_ms = 0;
static uint32_t last_tx_ms = 0;
static uint tx_pwm_slice = 0;
static uint tx_pwm_chan = 0;
static alarm_id_t carrier_alarm_id = 0;
static volatile bool carrier_failsafe_fired = false;
static bool carrier_off_pin_readback = false;
static const char *tx_status = "TX_DONE";
static SerialPIO gps_serial_0(
    NOPIN, GPS_RX_CANDIDATE_0, 256);
static SerialPIO gps_serial_1(
    NOPIN, GPS_RX_CANDIDATE_1, 256);
static SerialPIO gps_serial_2(
    NOPIN, GPS_RX_CANDIDATE_2, 256);
static SerialPIO *gps_serials[GPS_RX_CANDIDATE_COUNT] = {
    &gps_serial_0, &gps_serial_1, &gps_serial_2
};
static const uint8_t gps_pins[GPS_RX_CANDIDATE_COUNT] = {
    GPS_RX_CANDIDATE_0, GPS_RX_CANDIDATE_1, GPS_RX_CANDIDATE_2
};
static char gps_lines[GPS_RX_CANDIDATE_COUNT][GPS_NMEA_LINE_MAX] = {};
static uint8_t gps_line_lengths[GPS_RX_CANDIDATE_COUNT] = {};
static uint64_t gps_sentence_start_us[GPS_RX_CANDIDATE_COUNT] = {};
static int8_t gps_selected_index = -1;
static bool gps_utc_valid = false;
static bool gps_fix_valid = false;
static uint64_t gps_anchor_epoch_us = 0;
static uint64_t gps_anchor_local_us = 0;
static uint64_t gps_last_rmc_local_us = 0;
static uint64_t gps_last_reported_epoch_us = 0;
static uint32_t gps_checksum_errors = 0;
static volatile uint64_t gps_pps_last_us[GPS_RX_CANDIDATE_COUNT] = {};
static volatile uint64_t gps_pps_previous_us[GPS_RX_CANDIDATE_COUNT] = {};
static volatile uint8_t gps_pps_streak[GPS_RX_CANDIDATE_COUNT] = {};
static int8_t gps_pps_index = -1;
static bool gps_anchor_from_pps = false;
static uint64_t scheduled_sync_us = 0;
static uint64_t scheduled_epoch_us = 0;
static uint8_t scheduled_time_flags = 0;
static int32_t scheduled_sync_error_us = 0;
static uint8_t scheduled_time_payload[TIME_LINK_PAYLOAD_BYTES] = {};

static void recordGpsPps(uint8_t index) {
    const uint64_t now_us = time_us_64();
    const uint64_t previous_us = gps_pps_previous_us[index];
    gps_pps_previous_us[index] = now_us;
    gps_pps_last_us[index] = now_us;
    if (previous_us == 0) {
        gps_pps_streak[index] = 0;
        return;
    }
    const uint64_t interval_us = now_us - previous_us;
    if (interval_us >= GPS_PPS_MIN_INTERVAL_US &&
        interval_us <= GPS_PPS_MAX_INTERVAL_US) {
        if (gps_pps_streak[index] < 255) gps_pps_streak[index]++;
    } else {
        gps_pps_streak[index] = 0;
    }
}

static void onGpsPps0() { recordGpsPps(0); }
static void onGpsPps1() { recordGpsPps(1); }
static void onGpsPps2() { recordGpsPps(2); }

static bool nmeaChecksumValid(const char *line) {
    if (line == nullptr || line[0] != '$') return false;
    const char *asterisk = strchr(line, '*');
    if (asterisk == nullptr || asterisk[1] == '\0' ||
        asterisk[2] == '\0') {
        return false;
    }
    const int high = aqHexNibble(asterisk[1]);
    const int low = aqHexNibble(asterisk[2]);
    if (high < 0 || low < 0) return false;
    return aqTimeChecksum(line + 1, (size_t)(asterisk - line - 1)) ==
        (uint8_t)((high << 4) | low);
}

static int parseTwoDigits(const char *text) {
    if (text == nullptr ||
        text[0] < '0' || text[0] > '9' ||
        text[1] < '0' || text[1] > '9') {
        return -1;
    }
    return (text[0] - '0') * 10 + (text[1] - '0');
}

static bool copyNmeaField(
        const char *line, uint8_t target_index,
        char *output, size_t output_size) {
    if (line == nullptr || output == nullptr || output_size == 0) {
        return false;
    }
    uint8_t field_index = 0;
    const char *field_start = line;
    for (const char *cursor = line; ; ++cursor) {
        if (*cursor == ',' || *cursor == '*' || *cursor == '\0') {
            if (field_index == target_index) {
                const size_t length = (size_t)(cursor - field_start);
                if (length >= output_size) return false;
                memcpy(output, field_start, length);
                output[length] = '\0';
                return true;
            }
            if (*cursor != ',') return false;
            field_index++;
            field_start = cursor + 1;
        }
    }
}

static bool parseRmcUtc(
        const char *line, uint64_t local_sentence_us) {
    char sentence_type[8] = {};
    char utc_text[20] = {};
    char status_text[4] = {};
    char date_text[12] = {};
    if (!copyNmeaField(line, 0, sentence_type, sizeof(sentence_type)) ||
        !copyNmeaField(line, 1, utc_text, sizeof(utc_text)) ||
        !copyNmeaField(line, 2, status_text, sizeof(status_text)) ||
        !copyNmeaField(line, 9, date_text, sizeof(date_text)) ||
        strlen(sentence_type) < 6 ||
        strcmp(sentence_type + 3, "RMC") != 0 ||
        strlen(utc_text) < 6 || strlen(date_text) != 6) {
        return false;
    }

    const int hour = parseTwoDigits(utc_text);
    const int minute = parseTwoDigits(utc_text + 2);
    const int second = parseTwoDigits(utc_text + 4);
    const int day = parseTwoDigits(date_text);
    const int month = parseTwoDigits(date_text + 2);
    const int short_year = parseTwoDigits(date_text + 4);
    if (hour < 0 || hour > 23 || minute < 0 || minute > 59 ||
        second < 0 || second > 59 || day < 1 || day > 31 ||
        month < 1 || month > 12 || short_year < 0) {
        return false;
    }

    unsigned fractional_us = 0;
    const char *decimal = strchr(utc_text, '.');
    if (decimal != nullptr) {
        unsigned scale = 100000;
        for (const char *digit = decimal + 1;
             *digit >= '0' && *digit <= '9' && scale > 0;
             ++digit, scale /= 10) {
            fractional_us += (unsigned)(*digit - '0') * scale;
        }
    }
    const int year =
        short_year >= 80 ? 1900 + short_year : 2000 + short_year;
    const uint64_t epoch_us = aqUnixUsFromUtc(
        year, (unsigned)month, (unsigned)day,
        (unsigned)hour, (unsigned)minute, (unsigned)second,
        fractional_us);
    if (epoch_us == 0) return false;

    gps_fix_valid = status_text[0] == 'A';
    gps_last_reported_epoch_us = epoch_us;
    if (gps_fix_valid) {
        uint64_t pps_us = 0;
        if (gps_pps_index >= 0) {
            noInterrupts();
            pps_us = gps_pps_last_us[(uint8_t)gps_pps_index];
            interrupts();
        }
        const uint64_t now_us = time_us_64();
        gps_anchor_from_pps =
            pps_us != 0 && now_us >= pps_us &&
            now_us - pps_us <= GPS_PPS_RMC_MAX_AGE_US;
        if (gps_anchor_from_pps) {
            gps_anchor_epoch_us =
                epoch_us - (epoch_us % 1000000ULL);
            gps_anchor_local_us = pps_us;
        } else {
            gps_anchor_epoch_us = epoch_us;
            gps_anchor_local_us = local_sentence_us;
        }
        gps_last_rmc_local_us = time_us_64();
        gps_utc_valid = true;
    }
    return true;
}

static void selectGpsCandidate(uint8_t index) {
    if (gps_selected_index >= 0) return;
    gps_selected_index = (int8_t)index;
    for (uint8_t candidate = 0;
         candidate < GPS_RX_CANDIDATE_COUNT; ++candidate) {
        if (candidate != index) {
            gps_serials[candidate]->end();
            pinMode(gps_pins[candidate], INPUT_PULLDOWN);
        }
    }
    if (index != 0) {
        attachInterrupt(
            digitalPinToInterrupt(gps_pins[0]), onGpsPps0, RISING);
    }
    if (index != 1) {
        attachInterrupt(
            digitalPinToInterrupt(gps_pins[1]), onGpsPps1, RISING);
    }
    if (index != 2) {
        attachInterrupt(
            digitalPinToInterrupt(gps_pins[2]), onGpsPps2, RISING);
    }
    Serial.print("# GPS,NMEA_PIN,GP");
    Serial.println(gps_pins[index]);
}

static void updateGpsPpsSelection() {
    if (gps_pps_index >= 0 || gps_selected_index < 0) return;
    for (uint8_t index = 0;
         index < GPS_RX_CANDIDATE_COUNT; ++index) {
        if ((int8_t)index == gps_selected_index) continue;
        noInterrupts();
        const uint8_t streak = gps_pps_streak[index];
        interrupts();
        if (streak >= 2) {
            gps_pps_index = (int8_t)index;
            Serial.print("# GPS,PPS_PIN,GP");
            Serial.println(gps_pins[index]);
            return;
        }
    }
}

static void processGpsSentence(
        uint8_t index, const char *line, uint64_t start_us) {
    if (!nmeaChecksumValid(line)) {
        gps_checksum_errors++;
        return;
    }
    if (strlen(line) < 7 ||
        (strncmp(line, "$GP", 3) != 0 &&
         strncmp(line, "$GN", 3) != 0)) {
        return;
    }
    selectGpsCandidate(index);
    if ((int8_t)index != gps_selected_index) return;
    if (parseRmcUtc(line, start_us)) {
        char iso[32] = {};
        aqFormatIso8601(
            gps_last_reported_epoch_us, iso, sizeof(iso));
        Serial.print("# GPS,RMC,");
        Serial.print(iso);
        Serial.print(",fix=");
        Serial.println(gps_fix_valid ? 1 : 0);
    }
}

static void pollGpsCandidate(uint8_t index) {
    SerialPIO *port = gps_serials[index];
    while (port->available() > 0) {
        const char value = (char)port->read();
        if (value == '$') {
            gps_line_lengths[index] = 0;
            gps_lines[index][gps_line_lengths[index]++] = value;
            gps_sentence_start_us[index] = time_us_64();
        } else if (value == '\n') {
            if (gps_line_lengths[index] > 0) {
                gps_lines[index][gps_line_lengths[index]] = '\0';
                processGpsSentence(
                    index, gps_lines[index],
                    gps_sentence_start_us[index]);
                gps_line_lengths[index] = 0;
            }
        } else if (value != '\r' && gps_line_lengths[index] > 0) {
            if (gps_line_lengths[index] <
                GPS_NMEA_LINE_MAX - 1) {
                gps_lines[index][gps_line_lengths[index]++] = value;
            } else {
                gps_line_lengths[index] = 0;
            }
        }
    }
}

static void pollGps() {
    if (gps_selected_index >= 0) {
        pollGpsCandidate((uint8_t)gps_selected_index);
        updateGpsPpsSelection();
        return;
    }
    for (uint8_t index = 0;
         index < GPS_RX_CANDIDATE_COUNT; ++index) {
        pollGpsCandidate(index);
        if (gps_selected_index >= 0) break;
    }
    updateGpsPpsSelection();
}

static bool gpsTimeAt(
        uint64_t local_us, uint64_t *epoch_us, uint8_t *flags) {
    if (!gps_utc_valid || local_us < gps_anchor_local_us ||
        local_us - gps_anchor_local_us > GPS_HOLDOVER_MAX_US) {
        *epoch_us = 0;
        *flags = 0;
        return false;
    }
    *epoch_us =
        gps_anchor_epoch_us + (local_us - gps_anchor_local_us);
    *flags = TIME_FLAG_UTC_VALID;
    if (gps_fix_valid) *flags |= TIME_FLAG_GPS_FIX;
    if (gps_anchor_from_pps) *flags |= TIME_FLAG_GPS_PPS;
    if (time_us_64() - gps_last_rmc_local_us > GPS_RMC_FRESH_US) {
        *flags |= TIME_FLAG_TX_HOLDOVER;
    }
    return true;
}

static void prepareShotTime() {
    // Pico 2 W LED access can take about 1ms. Complete it before defining
    // the future SYNC epoch so it cannot shift the transmitted timestamp.
    digitalWrite(LED_BUILTIN, HIGH);
    scheduled_sync_us = time_us_64() + TIME_LINK_LEAD_US;
    gpsTimeAt(
        scheduled_sync_us, &scheduled_epoch_us,
        &scheduled_time_flags);
    AquaBeaconTimePacket packet = {
        seq,
        scheduled_epoch_us,
        scheduled_time_flags,
        gps_selected_index >= 0
            ? gps_pins[(uint8_t)gps_selected_index]
            : (uint8_t)TIME_LINK_UNKNOWN_PIN
    };
    aqPackTimePacket(packet, scheduled_time_payload);
}

static void sendShotTimeFrame() {
    // GP14 is shared with the existing auto-direction RS485 SYNC channel.
    // The RX measurement window is already closed when this frame is sent.
    digitalWrite(TX_SYNC_PIN, SYNC_ACTIVE_LEVEL);
    delayMicroseconds(TIME_LINK_PREAMBLE_US);
    digitalWrite(TX_SYNC_PIN, SYNC_IDLE_LEVEL);
    delayMicroseconds(TIME_LINK_PREAMBLE_GAP_US);

    for (uint16_t bit_index = 0;
         bit_index < TIME_LINK_PAYLOAD_BITS; ++bit_index) {
        const uint8_t byte_value =
            scheduled_time_payload[bit_index / 8];
        const bool one =
            (byte_value & (uint8_t)(0x80U >> (bit_index % 8))) != 0;
        digitalWrite(TX_SYNC_PIN, SYNC_ACTIVE_LEVEL);
        delayMicroseconds(
            one ? TIME_LINK_BIT_ONE_US : TIME_LINK_BIT_ZERO_US);
        digitalWrite(TX_SYNC_PIN, SYNC_IDLE_LEVEL);
        delayMicroseconds(TIME_LINK_BIT_GAP_US);
    }
}

static void printGpsStatus() {
    Serial.print("# GPS,pin=");
    if (gps_selected_index >= 0) {
        Serial.print("GP");
        Serial.print(gps_pins[(uint8_t)gps_selected_index]);
    } else {
        Serial.print("SEARCHING");
    }
    Serial.print(",utc=");
    Serial.print(gps_utc_valid ? 1 : 0);
    Serial.print(",fix=");
    Serial.print(gps_fix_valid ? 1 : 0);
    Serial.print(",pps=");
    if (gps_pps_index >= 0) {
        Serial.print("GP");
        Serial.print(gps_pins[(uint8_t)gps_pps_index]);
    } else {
        Serial.print("NONE");
    }
    Serial.print(",checksum_errors=");
    Serial.println(gps_checksum_errors);
}

// The GPIO override is applied before PWM is disabled. This guarantees that a
// stopped PWM counter cannot leave the MOSFET gate HIGH.
static void carrierForceLow() {
    gpio_set_outover(TX_CARRIER_PIN, GPIO_OVERRIDE_LOW);
    pwm_set_enabled(tx_pwm_slice, false);
    pwm_set_counter(tx_pwm_slice, 0);
}

static int64_t carrierFailsafe(alarm_id_t, void *) {
    carrierForceLow();
    carrier_failsafe_fired = true;
    t_carrier_off_us = time_us_32();
    return 0;
}

static void carrierOn() {
    pwm_set_counter(tx_pwm_slice, 0);
    pwm_set_enabled(tx_pwm_slice, true);
    gpio_set_outover(TX_CARRIER_PIN, GPIO_OVERRIDE_NORMAL);
}

static void logLiveCarrierState() {
    const bool pwm_enabled =
        (pwm_hw->slice[tx_pwm_slice].csr & PWM_CH0_CSR_EN_BITS) != 0;
    Serial.print("# DIAG,TX_LIVE,gp15=");
    Serial.print(gpio_get(TX_CARRIER_PIN) ? 1 : 0);
    Serial.print(",pwm=");
    Serial.print(pwm_enabled ? 1 : 0);
    Serial.print(",failsafe=");
    Serial.println(carrier_failsafe_fired ? 1 : 0);
}

static void carrierInit() {
    gpio_init(TX_CARRIER_PIN);
    gpio_set_dir(TX_CARRIER_PIN, GPIO_OUT);
    gpio_put(TX_CARRIER_PIN, 0);
    gpio_set_outover(TX_CARRIER_PIN, GPIO_OVERRIDE_LOW);
    gpio_set_function(TX_CARRIER_PIN, GPIO_FUNC_PWM);

    tx_pwm_slice = pwm_gpio_to_slice_num(TX_CARRIER_PIN);
    tx_pwm_chan = pwm_gpio_to_channel(TX_CARRIER_PIN);
    const uint32_t wrap = (clock_get_hz(clk_sys) / CARRIER_FREQ_HZ) - 1;
    pwm_set_wrap(tx_pwm_slice, wrap);
    pwm_set_chan_level(tx_pwm_slice, tx_pwm_chan, (wrap + 1) / 2);
    carrierForceLow();
}

void setup() {
    Serial.begin(UART_BAUD);
    for (uint8_t index = 0;
         index < GPS_RX_CANDIDATE_COUNT; ++index) {
        gps_serials[index]->begin(GPS_NMEA_BAUD);
        gpio_pull_up(gps_pins[index]);
    }

    pinMode(TX_SYNC_PIN, OUTPUT);
    pinMode(LED_BUILTIN, OUTPUT);
    carrierInit();
    digitalWrite(TX_SYNC_PIN, SYNC_IDLE_LEVEL);
    digitalWrite(LED_BUILTIN, LOW);
    boot_ms = millis();

    Serial.println("# AquaBeacon TX Ready (automatic 5-wave burst)");
    Serial.println("# seq,t_tx_us,wave_count,burst_us,status");
    Serial.print("# AUTO_WAIT: first shot in ");
    Serial.print(GPS_DISCOVERY_GRACE_MS);
    Serial.print(" ms, then every ");
    Serial.print(BURST_REPEAT_INTERVAL_MS);
    Serial.println(" ms");
    Serial.print("# TIME_LINK,SHARED_SYNC=GP");
    Serial.print(TX_SYNC_PIN);
    Serial.print(",send_delay_us=");
    Serial.println((unsigned long)TIME_LINK_SEND_DELAY_US);
    Serial.print("# GPS,SEARCHING,GP");
    Serial.print(GPS_RX_CANDIDATE_0);
    Serial.print("|GP");
    Serial.print(GPS_RX_CANDIDATE_1);
    Serial.print("|GP");
    Serial.println(GPS_RX_CANDIDATE_2);
}

void loop() {
    // Do not parse/print a GPS sentence during the final 30ms before SYNC.
    if (state != TX_TIME_WAIT && state != TX_BURST) pollGps();
    switch (state) {
    case TX_WAIT_TRIGGER:
        carrierForceLow();
        {
        const uint32_t now_ms = millis();
        const bool auto_due = seq == 0
            ? (uint32_t)(now_ms - boot_ms) >= GPS_DISCOVERY_GRACE_MS
            : (uint32_t)(now_ms - last_tx_ms) >= BURST_REPEAT_INTERVAL_MS;
        bool manual_trigger = false;
        if (Serial.available() > 0) {
            const int command = Serial.read();
            if (command == BURST_TRIGGER_CHAR) {
                manual_trigger = seq == 0 ||
                    (uint32_t)(now_ms - last_tx_ms) >= BURST_MIN_INTERVAL_MS;
                if (!manual_trigger) Serial.println("# TRIGGER_REJECTED:COOLDOWN");
            } else if (command == BURST_IDENTIFY_CHAR) {
                Serial.println("# ID:TX;STATE=AUTO_WAIT");
                logLiveCarrierState();
                printGpsStatus();
            }
        }
        if (auto_due || manual_trigger) {
            seq++;
            last_tx_ms = now_ms;
            prepareShotTime();
            state = TX_TIME_WAIT;
        }
        }
        break;

    case TX_TIME_WAIT:
        if ((int64_t)(time_us_64() - scheduled_sync_us) >= 0) {
            state = TX_BURST;
        }
        break;

    case TX_BURST:
        carrierForceLow();
        carrier_failsafe_fired = false;
        carrier_off_pin_readback = false;
        t_carrier_on_us = 0;
        t_carrier_off_us = 0;
        tx_status = "TX_DONE";

        scheduled_sync_error_us =
            (int32_t)(time_us_64() - scheduled_sync_us);
        digitalWrite(TX_SYNC_PIN, SYNC_ACTIVE_LEVEL);
        t_tx_us = micros();
        state = TX_SYNC_WAIT;
        break;

    case TX_SYNC_WAIT:
        if ((uint32_t)(micros() - t_tx_us) <
            (uint32_t)BURST_SYNC_TO_CARRIER_DELAY_US) {
            break;
        }

        // Arm immediately before carrier ON. The alarm is independent of the
        // normal delayMicroseconds() OFF path.
        carrier_alarm_id = add_alarm_in_us(
            BURST_FAILSAFE_US, carrierFailsafe, nullptr, true);
        if (carrier_alarm_id < 0) {
            carrier_alarm_id = 0;
            tx_status = "TX_ABORTED_ALARM";
            digitalWrite(TX_SYNC_PIN, SYNC_IDLE_LEVEL);
            digitalWrite(LED_BUILTIN, LOW);
            state = TX_LOG;
            break;
        }
        t_carrier_on_us = micros();
        carrierOn();
        delayMicroseconds(BURST_DURATION_US);
        carrierForceLow();
        t_carrier_off_us = micros();
        if (carrier_alarm_id > 0) {
            cancel_alarm(carrier_alarm_id);
            carrier_alarm_id = 0;
        }
        if (carrier_failsafe_fired) tx_status = "TX_FAILSAFE";
        digitalWrite(TX_SYNC_PIN, SYNC_IDLE_LEVEL);
        digitalWrite(LED_BUILTIN, LOW);
        state = TX_LOG;
        break;

    case TX_LOG:
        carrier_off_pin_readback = gpio_get(TX_CARRIER_PIN);
        if (carrier_failsafe_fired) tx_status = "TX_FAILSAFE";
        Serial.print("# DIAG,TX_PIN,"); Serial.print(seq);
        Serial.print(","); Serial.print(t_carrier_on_us);
        Serial.println(",ON,pwm=1,override=NORMAL");
        Serial.print("# DIAG,TX_PIN,"); Serial.print(seq);
        Serial.print(","); Serial.print(t_carrier_off_us);
        Serial.print(",OFF,pwm=0,override=LOW,pin_readback=");
        Serial.print(carrier_off_pin_readback ? 1 : 0);
        Serial.print(",failsafe="); Serial.println(carrier_failsafe_fired ? 1 : 0);
        Serial.print(seq); Serial.print(",");
        Serial.print(t_tx_us); Serial.print(",");
        Serial.print(BURST_WAVE_COUNT); Serial.print(",");
        Serial.print(BURST_DURATION_US); Serial.print(",");
        Serial.println(tx_status);
        Serial.print("# TIME,seq="); Serial.print(seq);
        Serial.print(",epoch_us=");
        Serial.print((unsigned long long)scheduled_epoch_us);
        Serial.print(",flags=");
        Serial.print(scheduled_time_flags);
        Serial.print(",sync_error_us=");
        Serial.println(scheduled_sync_error_us);
        state = TX_TIME_SEND_WAIT;
        break;

    case TX_TIME_SEND_WAIT:
        if ((int64_t)(time_us_64() -
            (scheduled_sync_us + TIME_LINK_SEND_DELAY_US)) >= 0) {
            sendShotTimeFrame();
            Serial.print("# TIME_LINK,SENT,seq=");
            Serial.println(seq);
            state = TX_WAIT_TRIGGER;
        }
        break;

    case TX_DONE:
        carrierForceLow();
        digitalWrite(TX_SYNC_PIN, SYNC_IDLE_LEVEL);
        digitalWrite(LED_BUILTIN, LOW);
        while (Serial.available() > 0) {
            const int command = Serial.read();
            if (command == BURST_IDENTIFY_CHAR) {
                Serial.println("# ID:TX;STATE=AUTO_WAIT");
                logLiveCarrierState();
                printGpsStatus();
            } else if (command == BURST_REARM_CHAR) {
                if ((uint32_t)(millis() - last_tx_ms) < BURST_MIN_INTERVAL_MS) {
                    Serial.println("# REARM_REJECTED:COOLDOWN");
                } else {
                    state = TX_WAIT_TRIGGER;
                    Serial.println("# ID:TX;STATE=AUTO_WAIT");
                }
            }
        }
        break;
    }
}

#endif
