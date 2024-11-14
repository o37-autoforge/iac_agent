#!/bin/bash

docker run \
       -it --rm \
       -v `pwd`:/forge \
       -v `pwd`/tmp.benchmarks/.:/benchmarks \
       -e OPENAI_API_KEY=$OPENAI_API_KEY \
       -e HISTFILE=/forge/.bash_history \
       -e forge_DOCKER=1 \
       -e forge_BENCHMARK_DIR=/benchmarks \
       forge-benchmark \
       bash
