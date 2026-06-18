#!/usr/bin/env bash
set -euo pipefail

SRC="${1:-main_ff1ff000_ff209000.c}"
OUT="${2:-main.bin}"

v850-elf-gcc -fPIC -ffreestanding -c "$SRC" -o main.o
v850-elf-objcopy -O binary -j .text main.o "$OUT"
ls -lh "$OUT"
