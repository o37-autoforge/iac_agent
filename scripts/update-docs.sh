#!/bin/bash

# exit when any command fails
set -e

if [ -z "$1" ]; then
  ARG=-r
else
  ARG=$1
fi

if [ "$ARG" != "--check" ]; then
  tail -1000 ~/.forge/analytics.jsonl > forge/website/assets/sample-analytics.jsonl
fi

# README.md before index.md, because index.md uses cog to include README.md
cog $ARG \
    README.md \
    forge/website/index.md \
    forge/website/HISTORY.md \
    forge/website/docs/usage/commands.md \
    forge/website/docs/languages.md \
    forge/website/docs/config/dotenv.md \
    forge/website/docs/config/options.md \
    forge/website/docs/config/forge_conf.md \
    forge/website/docs/config/adv-model-settings.md \
    forge/website/docs/leaderboards/index.md \
    forge/website/docs/llms/other.md \
    forge/website/docs/more/infinite-output.md \
    forge/website/docs/legal/privacy.md
