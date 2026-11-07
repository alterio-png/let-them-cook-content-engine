#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python3 -m unittest discover -s tests -v
python3 -m engine.cli demo --workspace workspace --tenant demo
python3 -m engine.cli render --workspace workspace --tenant demo --key smoke-v1 \
 --input workspace/inputs/demo/synthetic_demo.mp4 --caption-srt workspace/inputs/demo/synthetic_demo.srt \
 --duration-seconds 1.2 --quality smoke
