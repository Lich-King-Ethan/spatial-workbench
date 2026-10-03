#!/usr/bin/env python3
"""Exercise patched upstream C and real UDP with only HID reads substituted."""
import json
import math
from pathlib import Path
import subprocess
import sys
import struct
import tempfile


REPORTS = ((6500, -8300, 4100), (6500, -8300, 4100), (-4300, 12000, 9000),
           (-12000, 9000, 6000), (0, 0, 0), (8192, 0, 0), (0, 8192, 0),
           (0, 0, 8192), (4500, -7200, 11300))

HARNESS = r'''
#define _DEFAULT_SOURCE
#define HIDRAW_H
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <unistd.h>
#include <time.h>
typedef struct { char path[256]; char name[256]; } hid_device_entry;
static const int16_t reports[][3] = { @REPORTS@ };
static int cursor, enable_calls;
static uint64_t read_times[32];
static uint64_t clock_ns(void) {
    struct timespec value; assert(clock_gettime(CLOCK_MONOTONIC, &value) == 0);
    return (uint64_t)value.tv_sec * UINT64_C(1000000000) + (uint64_t)value.tv_nsec;
}
static int hid_open(const char *path) {
    assert(strcmp(path, "/dev/fixture-sony-motion") == 0); return 42;
}
static void hid_close(int fd) { assert(fd == 42); }
static int hid_set_feature(int fd, const uint8_t *data, size_t length) {
    assert(fd == 42 && length == 2 && data[0] == 1 && data[1] == 3);
    enable_calls++; return 2;
}
static int hid_read_report_timeout(int fd, uint8_t *data, size_t capacity, int timeout) {
    assert(fd == 42 && capacity >= 14 && timeout == 2000);
    int index = cursor++;
    memset(data, 0, 14);
    data[0] = 1;
    if (index == 0) return 0;
    if (index == 1) return 13;
    if (index == 2) { data[0] = 2; return 14; }
    index -= 3;
    if (index >= (int)(sizeof(reports) / sizeof(reports[0]))) return -1;
    for (int axis = 0; axis < 3; axis++) {
        uint16_t value = (uint16_t)reports[index][axis];
        data[1 + 2 * axis] = value & 255;
        data[2 + 2 * axis] = value >> 8;
    }
    const int16_t gyro[] = {123, -234, 0};
    for (int axis = 0; axis < 3; axis++) {
        uint16_t value = (uint16_t)gyro[axis];
        data[7 + 2 * axis] = value & 255;
        data[8 + 2 * axis] = value >> 8;
    }
    data[13] = (uint8_t)(254 + index);
    read_times[index] = clock_ns();
    return 14;
}
static int hid_scan_all(hid_device_entry *entries, int count) {
    (void)entries; (void)count; abort();
}
#define main upstream_main
#include "main.c"
#undef main
int main(int argc, char **argv) {
    assert(argc == 2);
    int timestamped = strcmp(argv[1], "timestamped") == 0 || strcmp(argv[1], "both") == 0;
    int absolute = strcmp(argv[1], "absolute") == 0 || strcmp(argv[1], "both") == 0;
    assert(absolute || timestamped || strcmp(argv[1], "legacy") == 0);
    int receiver = socket(AF_INET, SOCK_DGRAM, 0);
    assert(receiver >= 0);
    struct sockaddr_in endpoint = {.sin_family = AF_INET,
                                   .sin_addr.s_addr = htonl(INADDR_LOOPBACK)};
    assert(bind(receiver, (struct sockaddr *)&endpoint, sizeof(endpoint)) == 0);
    socklen_t length = sizeof(endpoint);
    assert(getsockname(receiver, (struct sockaddr *)&endpoint, &length) == 0);
    char port[16];
    snprintf(port, sizeof(port), "%u", ntohs(endpoint.sin_port));
    char *arguments[] = {"sony-tracker", "--device", "/dev/fixture-sony-motion",
                         "--port", port, NULL, NULL, NULL};
    int argument_count = 5;
    if (absolute) arguments[argument_count++] = "--absolute";
    if (timestamped) arguments[argument_count++] = "--timestamped";
    assert(upstream_main(argument_count, arguments) == 0);
    uint64_t completed_ns = clock_ns();
    assert(enable_calls == 1);
    int received = 0;
    uint8_t packet[64];
    uint64_t previous_capture = 0;
    for (;;) {
        struct sockaddr_in sender;
        length = sizeof(sender);
        ssize_t count = recvfrom(receiver, packet, sizeof(packet), MSG_DONTWAIT,
                                 (struct sockaddr *)&sender, &length);
        if (count < 0) { assert(errno == EAGAIN || errno == EWOULDBLOCK); break; }
        assert(sender.sin_addr.s_addr == htonl(INADDR_LOOPBACK));
        if (timestamped) {
            assert(count == 34 && memcmp(packet, "SPT1", 4) == 0);
            uint64_t capture = 0, sequence = 0;
            for (int byte = 0; byte < 8; byte++) {
                capture |= (uint64_t)packet[4 + byte] << (8 * byte);
                sequence |= (uint64_t)packet[12 + byte] << (8 * byte);
            }
            assert(sequence == (uint64_t)received + 1);
            assert(capture >= read_times[received] && capture <= completed_ns);
            assert(capture > previous_capture);
            if (received + 1 < (int)(sizeof(reports) / sizeof(reports[0]))) {
                assert(capture <= read_times[received + 1]);
            }
            previous_capture = capture;
            for (int i = 0; i < count; i++) printf("%02x", packet[i]);
            putchar('\n');
        } else {
            assert(count == 6 * (ssize_t)sizeof(double));
            double pose[6]; memcpy(pose, packet, sizeof(pose));
            for (int i = 0; i < 6; i++) printf(i == 5 ? "%.17g\n" : "%.17g ", pose[i]);
        }
        received++;
    }
    assert(received == (int)(sizeof(reports) / sizeof(reports[0])) - !(absolute || timestamped));
    close(receiver);
    return 0;
}
'''


def multiply(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def transpose(a):
    return list(zip(*a))


def expected_absolute(raw):
    # Independent Rodrigues construction, rather than the C quaternion formulas.
    scaled = [(-314159264 + (value + 32767) / 65534 * 628318529) * 1e-8 for value in raw]
    vector = (-scaled[1], scaled[0], -scaled[2])
    angle = math.hypot(*vector)
    if angle < 1e-12:
        return [[float(i == j) for j in range(3)] for i in range(3)]
    x, y, z = (value / angle for value in vector)
    skew = ((0, -z, y), (z, 0, -x), (-y, x, 0))
    square = multiply(skew, skew)
    return [[float(i == j) + math.sin(angle) * skew[i][j]
             + (1 - math.cos(angle)) * square[i][j] for j in range(3)] for i in range(3)]


def observed_matrix(packet):
    assert len(packet) == 6 and all(math.isfinite(value) for value in packet)
    assert packet[:3] == [0.0, 0.0, 0.0]
    yaw, pitch, roll = map(math.radians, packet[3:])
    cy, sy, cp, sp, cr, sr = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch), math.cos(roll), math.sin(roll)
    return ((cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr),
            (sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr),
            (-sp, cp * sr, cp * cr))


def main():
    source = Path(sys.argv[1]).resolve()
    help_text = subprocess.check_output([str(source / "sony-tracker"), "--help"], text=True, timeout=5)
    assert all(flag in help_text for flag in ("--absolute", "--timestamped", "--device", "--all"))
    rejected = subprocess.run([str(source / "sony-tracker"), "--not-a-real-option"], capture_output=True, timeout=5)
    assert rejected.returncode != 0
    evidence = {}
    with tempfile.TemporaryDirectory(prefix="sony-native-test-") as directory:
        test = Path(directory) / "test.c"
        test.write_text(HARNESS.replace("@REPORTS@", ",".join(
            "{" + ",".join(map(str, report)) + "}" for report in REPORTS)))
        binary = Path(directory) / "test"
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                        "-I", str(source), str(test), "-o", str(binary), "-lm", "-pthread"], check=True)
        absolute = [expected_absolute(report) for report in REPORTS]
        for mode in ("legacy", "absolute", "timestamped", "both"):
            result = subprocess.run([str(binary), mode], check=True, capture_output=True, text=True, timeout=10)
            if mode in ("timestamped", "both"):
                packets = [bytes.fromhex(line) for line in result.stdout.splitlines()]
                assert len(packets) == len(REPORTS)
                previous_capture = 0
                for index, (packet, report) in enumerate(zip(packets, REPORTS)):
                    assert len(packet) == 34 and packet[:4] == b"SPT1"
                    capture, sequence = struct.unpack_from("<QQ", packet, 4)
                    assert sequence == index + 1 and capture > previous_capture
                    previous_capture = capture
                    expected = struct.pack("<B6hB", 1, *report, 123, -234, 0, (254 + index) % 256)
                    assert packet[20:] == expected
                evidence[mode] = {"packets": len(packets), "wire_bytes": 34,
                                  "capture_clock": "host CLOCK_MONOTONIC",
                                  "capture_within_HID_read_boundaries": True,
                                  "raw_reports_preserved": True, "feature_writes": 1}
                continue
            packets = [list(map(float, line.split())) for line in result.stdout.splitlines()]
            expected = absolute if mode == "absolute" else [
                multiply(transpose(absolute[0]), current) for current in absolute[1:]]
            assert len(packets) == len(expected)
            maximum = 0.0
            for packet, matrix in zip(packets, expected):
                actual = observed_matrix(packet)
                error = max(abs(actual[i][j] - matrix[i][j]) for i in range(3) for j in range(3))
                assert error < 1e-10, (mode, packet, error)
                maximum = max(maximum, error)
            evidence[mode] = {"packets": len(packets), "maximum_matrix_error": maximum}
    print(json.dumps({"status": "passed", "hardware_validated": False,
                      "native_C_and_UDP": evidence}, indent=2))


if __name__ == "__main__":
    main()
