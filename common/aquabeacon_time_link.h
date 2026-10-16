#pragma once

#include <Arduino.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "aquabeacon_config.h"

struct AquaBeaconTimePacket {
    uint32_t sequence;
    uint64_t shot_epoch_us;
    uint8_t flags;
    uint8_t gps_rx_pin;
};

static inline void aqPackTimePacket(
        const AquaBeaconTimePacket &packet,
        uint8_t output[TIME_LINK_PAYLOAD_BYTES]) {
    for (uint8_t index = 0; index < 4; ++index) {
        output[index] =
            (uint8_t)(packet.sequence >> ((3 - index) * 8));
    }
    for (uint8_t index = 0; index < 8; ++index) {
        output[4 + index] =
            (uint8_t)(packet.shot_epoch_us >> ((7 - index) * 8));
    }
    output[12] = packet.flags;
    output[13] = packet.gps_rx_pin;
    uint8_t checksum = 0;
    for (uint8_t index = 0; index < 14; ++index) {
        checksum ^= output[index];
    }
    output[14] = checksum;
}

static inline bool aqUnpackTimePacket(
        const uint8_t input[TIME_LINK_PAYLOAD_BYTES],
        AquaBeaconTimePacket *packet) {
    if (input == nullptr || packet == nullptr) return false;
    uint8_t checksum = 0;
    for (uint8_t index = 0; index < 14; ++index) {
        checksum ^= input[index];
    }
    if (checksum != input[14]) return false;
    packet->sequence = 0;
    for (uint8_t index = 0; index < 4; ++index) {
        packet->sequence =
            (packet->sequence << 8) | input[index];
    }
    packet->shot_epoch_us = 0;
    for (uint8_t index = 0; index < 8; ++index) {
        packet->shot_epoch_us =
            (packet->shot_epoch_us << 8) | input[4 + index];
    }
    packet->flags = input[12];
    packet->gps_rx_pin = input[13];
    return true;
}

static inline uint8_t aqTimeChecksum(
        const char *data, size_t length) {
    uint8_t checksum = 0;
    for (size_t index = 0; index < length; ++index) {
        checksum ^= (uint8_t)data[index];
    }
    return checksum;
}

static inline int aqHexNibble(char value) {
    if (value >= '0' && value <= '9') return value - '0';
    if (value >= 'A' && value <= 'F') return value - 'A' + 10;
    if (value >= 'a' && value <= 'f') return value - 'a' + 10;
    return -1;
}

static inline bool aqFormatTimePacket(
        const AquaBeaconTimePacket &packet, char *output, size_t size) {
    char payload[TIME_LINK_LINE_MAX] = {};
    const int payload_length = snprintf(
        payload, sizeof(payload), "AQT,%lu,%llu,%u,%u",
        (unsigned long)packet.sequence,
        (unsigned long long)packet.shot_epoch_us,
        (unsigned int)packet.flags,
        (unsigned int)packet.gps_rx_pin);
    if (payload_length <= 0 ||
        (size_t)payload_length >= sizeof(payload)) {
        return false;
    }
    const uint8_t checksum =
        aqTimeChecksum(payload, (size_t)payload_length);
    const int output_length = snprintf(
        output, size, "$%s*%02X\r\n", payload, checksum);
    return output_length > 0 && (size_t)output_length < size;
}

static inline bool aqParseTimePacket(
        const char *line, AquaBeaconTimePacket *packet) {
    if (line == nullptr || packet == nullptr ||
        strncmp(line, "$AQT,", 5) != 0) {
        return false;
    }
    const char *asterisk = strchr(line, '*');
    if (asterisk == nullptr || asterisk[1] == '\0' ||
        asterisk[2] == '\0') {
        return false;
    }
    const int high = aqHexNibble(asterisk[1]);
    const int low = aqHexNibble(asterisk[2]);
    if (high < 0 || low < 0) return false;
    const uint8_t expected = (uint8_t)((high << 4) | low);
    const uint8_t actual =
        aqTimeChecksum(line + 1, (size_t)(asterisk - line - 1));
    if (expected != actual) return false;

    char payload[TIME_LINK_LINE_MAX] = {};
    const size_t payload_length = (size_t)(asterisk - (line + 1));
    if (payload_length >= sizeof(payload)) return false;
    memcpy(payload, line + 1, payload_length);
    payload[payload_length] = '\0';

    char *save = nullptr;
    char *token = strtok_r(payload, ",", &save);
    if (token == nullptr || strcmp(token, "AQT") != 0) return false;
    token = strtok_r(nullptr, ",", &save);
    if (token == nullptr) return false;
    packet->sequence = strtoul(token, nullptr, 10);
    token = strtok_r(nullptr, ",", &save);
    if (token == nullptr) return false;
    packet->shot_epoch_us = strtoull(token, nullptr, 10);
    token = strtok_r(nullptr, ",", &save);
    if (token == nullptr) return false;
    packet->flags = (uint8_t)strtoul(token, nullptr, 10);
    token = strtok_r(nullptr, ",", &save);
    if (token == nullptr) return false;
    packet->gps_rx_pin = (uint8_t)strtoul(token, nullptr, 10);
    return true;
}

static inline int64_t aqDaysFromCivil(
        int year, unsigned month, unsigned day) {
    year -= month <= 2;
    const int era = (year >= 0 ? year : year - 399) / 400;
    const unsigned year_of_era =
        (unsigned)(year - era * 400);
    const unsigned adjusted_month =
        month > 2 ? month - 3 : month + 9;
    const unsigned day_of_year =
        (153 * adjusted_month + 2) / 5 + day - 1;
    const unsigned day_of_era =
        year_of_era * 365 + year_of_era / 4 -
        year_of_era / 100 + day_of_year;
    return (int64_t)era * 146097 + (int64_t)day_of_era - 719468;
}

static inline uint64_t aqUnixUsFromUtc(
        int year, unsigned month, unsigned day,
        unsigned hour, unsigned minute, unsigned second,
        unsigned fractional_us) {
    const int64_t days = aqDaysFromCivil(year, month, day);
    if (days < 0) return 0;
    const uint64_t seconds =
        (uint64_t)days * 86400ULL +
        (uint64_t)hour * 3600ULL +
        (uint64_t)minute * 60ULL +
        (uint64_t)second;
    return seconds * 1000000ULL + fractional_us;
}

static inline void aqCivilFromDays(
        int64_t days, int *year, unsigned *month, unsigned *day) {
    days += 719468;
    const int era = (days >= 0 ? days : days - 146096) / 146097;
    const unsigned day_of_era =
        (unsigned)(days - (int64_t)era * 146097);
    const unsigned year_of_era =
        (day_of_era - day_of_era / 1460 + day_of_era / 36524 -
         day_of_era / 146096) / 365;
    int result_year = (int)year_of_era + era * 400;
    const unsigned day_of_year =
        day_of_era - (365 * year_of_era + year_of_era / 4 -
                      year_of_era / 100);
    const unsigned month_prime = (5 * day_of_year + 2) / 153;
    const unsigned result_day =
        day_of_year - (153 * month_prime + 2) / 5 + 1;
    const unsigned result_month =
        month_prime < 10 ? month_prime + 3 : month_prime - 9;
    result_year += result_month <= 2;
    *year = result_year;
    *month = result_month;
    *day = result_day;
}

static inline bool aqFormatIso8601(
        uint64_t epoch_us, char *output, size_t size) {
    if (epoch_us == 0 || output == nullptr || size == 0) return false;
    const uint64_t total_seconds = epoch_us / 1000000ULL;
    const unsigned micros = (unsigned)(epoch_us % 1000000ULL);
    const int64_t days = (int64_t)(total_seconds / 86400ULL);
    const uint64_t day_seconds = total_seconds % 86400ULL;
    int year = 0;
    unsigned month = 0;
    unsigned day = 0;
    aqCivilFromDays(days, &year, &month, &day);
    const unsigned hour = (unsigned)(day_seconds / 3600ULL);
    const unsigned minute =
        (unsigned)((day_seconds % 3600ULL) / 60ULL);
    const unsigned second = (unsigned)(day_seconds % 60ULL);
    const int written = snprintf(
        output, size,
        "%04d-%02u-%02uT%02u:%02u:%02u.%06uZ",
        year, month, day, hour, minute, second, micros);
    return written > 0 && (size_t)written < size;
}
