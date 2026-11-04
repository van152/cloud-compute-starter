#!/usr/bin/env python3
"""Offline CPU+RAM demo with a 3-second duration; safe for smoke testing."""
import time

buffer = bytearray(16 * 1024 * 1024)
for i in range(0, len(buffer), 4096):
    buffer[i] = 1
start = time.monotonic()
iterations = 0
while time.monotonic() - start < 3.0:
    for n in range(10000):
        iterations += (n * n) % 97
print('Demo finished; checksum:', iterations, 'memory bytes:', len(buffer))

