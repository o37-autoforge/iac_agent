---
parent: Configuration
nav_order: 15
description: How to configure forge with a yaml config file.
---

# YAML config file

Most of forge's options can be set in an `.forge.conf.yml` file.
forge will look for a this file in these locations and
load whichever is found first.

- As specified with the `--config <filename>` parameter.
- The current directory.
- The root of your git repo.
- Your home directory.

## Storing LLM keys

{% include special-keys.md %}

{% include env-keys-tip.md %}

## A note on lists

Lists of values can be specified either as a bulleted list:

```
read:
  - CONVENTIONS.md
  - anotherfile.txt
  - thirdfile.py
```

Or lists can be specified using commas and square brackets:

```
read: [CONVENTIONS.md, anotherfile.txt, thirdfile.py]
```

## Sample YAML config file

Below is a sample of the YAML config file, which you
can also
[download from GitHub](https://github.com/forge-AI/forge/blob/main/forge/website/assets/sample.forge.conf.yml).

<!--[[[cog
from forge.args import get_sample_yaml
from pathlib import Path
text=get_sample_yaml()
Path("forge/website/assets/sample.forge.conf.yml").write_text(text)
cog.outl("```")
cog.out(text)
cog.outl("```")
]]]-->
```
##########################################################
# Sample .forge.conf.yml
# This file lists *all* the valid configuration entries.
# Place in your home dir, or at the root of your git repo.
##########################################################

# Note: You can only put OpenAI and Anthropic API keys in the yaml
# config file. Keys for all APIs can be stored in a .env file
# https://forge.chat/docs/config/dotenv.html

##########
# options:

## show this help message and exit
#help: xxx

#######
# Main:

## Specify the OpenAI API key
#openai-api-key: xxx

## Specify the Anthropic API key
#anthropic-api-key: xxx

## Specify the model to use for the main chat
#model: xxx

## Use claude-3-opus-20240229 model for the main chat
#opus: false

## Use claude-3-5-sonnet-20241022 model for the main chat
#sonnet: false

## Use claude-3-5-haiku-20241022 model for the main chat
#haiku: false

## Use gpt-4-0613 model for the main chat
#4: false

## Use gpt-4o-2024-08-06 model for the main chat
#4o: false

## Use gpt-4o-mini model for the main chat
#mini: false

## Use gpt-4-1106-preview model for the main chat
#4-turbo: false

## Use gpt-3.5-turbo model for the main chat
#35turbo: false

## Use deepseek/deepseek-coder model for the main chat
#deepseek: false

## Use o1-mini model for the main chat
#o1-mini: false

## Use o1-preview model for the main chat
#o1-preview: false

#################
# Model Settings:

## List known models which match the (partial) MODEL name
#list-models: xxx

## Specify the api base url
#openai-api-base: xxx

## Specify the api_type
#openai-api-type: xxx

## Specify the api_version
#openai-api-version: xxx

## Specify the deployment_id
#openai-api-deployment-id: xxx

## Specify the OpenAI organization ID
#openai-organization-id: xxx

## Specify a file with forge model settings for unknown models
#model-settings-file: .forge.model.settings.yml

## Specify a file with context window and costs for unknown models
#model-metadata-file: .forge.model.metadata.json

## Verify the SSL cert when connecting to models (default: True)
#verify-ssl: true

## Specify what edit format the LLM should use (default depends on model)
#edit-format: xxx

## Use architect edit format for the main chat
#architect: false

## Specify the model to use for commit messages and chat history summarization (default depends on --model)
#weak-model: xxx

## Specify the model to use for editor tasks (default depends on --model)
#editor-model: xxx

## Specify the edit format for the editor model (default: depends on editor model)
#editor-edit-format: xxx

## Only work with models that have meta-data available (default: True)
#show-model-warnings: true

## Soft limit on tokens for chat history, after which summarization begins. If unspecified, defaults to the model's max_chat_history_tokens.
#max-chat-history-tokens: xxx

## Specify the .env file to load (default: .env in git root)
#env-file: .env

#################
# Cache Settings:

## Enable caching of prompts (default: False)
#cache-prompts: false

## Number of times to ping at 5min intervals to keep prompt cache warm (default: 0)
#cache-keepalive-pings: false

###################
# Repomap Settings:

## Suggested number of tokens to use for repo map, use 0 to disable (default: 1024)
#map-tokens: xxx

## Control how often the repo map is refreshed. Options: auto, always, files, manual (default: auto)
#map-refresh: auto

## Multiplier for map tokens when no files are specified (default: 2)
#map-multiplier-no-files: true

################
# History Files:

## Specify the chat input history file (default: .forge.input.history)
#input-history-file: .forge.input.history

## Specify the chat history file (default: .forge.chat.history.md)
#chat-history-file: .forge.chat.history.md

## Restore the previous chat history messages (default: False)
#restore-chat-history: false

## Log the conversation with the LLM to this file (for example, .forge.llm.history)
#llm-history-file: xxx

##################
# Output Settings:

## Use colors suitable for a dark terminal background (default: False)
#dark-mode: false

## Use colors suitable for a light terminal background (default: False)
#light-mode: false

## Enable/disable pretty, colorized output (default: True)
#pretty: true

## Enable/disable streaming responses (default: True)
#stream: true

## Set the color for user input (default: #00cc00)
#user-input-color: #00cc00

## Set the color for tool output (default: None)
#tool-output-color: xxx

## Set the color for tool error messages (default: #FF2222)
#tool-error-color: #FF2222

## Set the color for tool warning messages (default: #FFA500)
#tool-warning-color: #FFA500

## Set the color for assistant output (default: #0088ff)
#assistant-output-color: #0088ff

## Set the color for the completion menu (default: terminal's default text color)
#completion-menu-color: xxx

## Set the background color for the completion menu (default: terminal's default background color)
#completion-menu-bg-color: xxx

## Set the color for the current item in the completion menu (default: terminal's default background color)
#completion-menu-current-color: xxx

## Set the background color for the current item in the completion menu (default: terminal's default text color)
#completion-menu-current-bg-color: xxx

## Set the markdown code theme (default: default, other options include monokai, solarized-dark, solarized-light)
#code-theme: default

## Show diffs when committing changes (default: False)
#show-diffs: false

###############
# Git Settings:

## Enable/disable looking for a git repo (default: True)
#git: true

## Enable/disable adding .forge* to .gitignore (default: True)
#gitignore: true

## Specify the forge ignore file (default: .forgeignore in git root)
#forgeignore: .forgeignore

## Only consider files in the current subtree of the git repository
#subtree-only: false

## Enable/disable auto commit of LLM changes (default: True)
#auto-commits: true

## Enable/disable commits when repo is found dirty (default: True)
#dirty-commits: true

## Attribute forge code changes in the git author name (default: True)
#attribute-author: true

## Attribute forge commits in the git committer name (default: True)
#attribute-committer: true

## Prefix commit messages with 'forge: ' if forge authored the changes (default: False)
#attribute-commit-message-author: false

## Prefix all commit messages with 'forge: ' (default: False)
#attribute-commit-message-committer: false

## Commit all pending changes with a suitable commit message, then exit
#commit: false

## Specify a custom prompt for generating commit messages
#commit-prompt: xxx

## Perform a dry run without modifying files (default: False)
#dry-run: false

## Skip the sanity check for the git repository (default: False)
#skip-sanity-check-repo: false

########################
# Fixing and committing:

## Lint and fix provided files, or dirty files if none provided
#lint: false

## Specify lint commands to run for different languages, eg: "python: flake8 --select=..." (can be used multiple times)
#lint-cmd: xxx
## Specify multiple values like this:
#lint-cmd:
#  - xxx
#  - yyy
#  - zzz

## Enable/disable automatic linting after changes (default: True)
#auto-lint: true

## Specify command to run tests
#test-cmd: xxx

## Enable/disable automatic testing after changes (default: False)
#auto-test: false

## Run tests and fix problems found
#test: false

############
# Analytics:

## Enable/disable analytics for one session (default: False)
#analytics: false

## Specify a file to log analytics events
#analytics-log: xxx

## Permanently disable analytics
#analytics-disable: false

#################
# Other Settings:

## specify a file to edit (can be used multiple times)
#file: xxx
## Specify multiple values like this:
#file:
#  - xxx
#  - yyy
#  - zzz

## specify a read-only file (can be used multiple times)
#read: xxx
## Specify multiple values like this:
#read:
#  - xxx
#  - yyy
#  - zzz

## Use VI editing mode in the terminal (default: False)
#vim: false

## Specify the language to use in the chat (default: None, uses system settings)
#chat-language: xxx

## Show the version number and exit
#version: xxx

## Check for updates and return status in the exit code
#just-check-update: false

## Check for new forge versions on launch
#check-update: true

## Install the latest version from the main branch
#install-main-branch: false

## Upgrade forge to the latest version from PyPI
#upgrade: false

## Apply the changes from the given file instead of running the chat (debug)
#apply: xxx

## Apply clipboard contents as edits using the main model's editor format
#apply-clipboard-edits: false

## Always say yes to every confirmation
#yes-always: false

## Enable verbose output
#verbose: false

## Print the repo map and exit (debug)
#show-repo-map: false

## Print the system prompts and exit (debug)
#show-prompts: false

## Do all startup activities then exit before accepting user input (debug)
#exit: false

## Specify a single message to send the LLM, process reply then exit (disables chat mode)
#message: xxx

## Specify a file containing the message to send the LLM, process reply, then exit (disables chat mode)
#message-file: xxx

## Load and execute /commands from a file on launch
#load: xxx

## Specify the encoding for input and output (default: utf-8)
#encoding: utf-8

## Specify the config file (default: search for .forge.conf.yml in git root, cwd or home directory)
#config: xxx

## Run forge in your browser (default: False)
#gui: false

## Enable/disable suggesting shell commands (default: True)
#suggest-shell-commands: true

## Enable/disable fancy input with history and completion (default: True)
#fancy-input: true

#################
# Voice Settings:

## Audio format for voice recording (default: wav). webm and mp3 require ffmpeg
#voice-format: wav

## Specify the language for voice using ISO 639-1 code (default: auto)
#voice-language: en
```
<!--[[[end]]]-->