#!/usr/bin/env bash
set -euo pipefail

docker build -t v850-gcc .
docker run --rm -v "$(pwd):/src" v850-gcc ./build.sh main_ff1ff000_ff209000.c main.bin
