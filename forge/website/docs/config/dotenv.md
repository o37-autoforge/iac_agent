---
parent: Configuration
nav_order: 900
description: Using a .env file to store LLM API keys for forge.
---

# Config with .env

You can use a `.env` file to store API keys and other settings for the
models you use with forge.
You can also set many general forge options
in the `.env` file.

forge will look for a `.env` file in these locations:

- Your home directory.
- The root of your git repo.
- The current directory.
- As specified with the `--env-file <filename>` parameter.

If the files above exist, they will be loaded in that order. Files loaded last will take priority.

## Storing LLM keys

{% include special-keys.md %}

## Sample .env file

Below is a sample `.env` file, which you
can also
[download from GitHub](https://github.com/forge-AI/forge/blob/main/forge/website/assets/sample.env).

<!--[[[cog
from forge.args import get_sample_dotenv
from pathlib import Path
text=get_sample_dotenv()
Path("forge/website/assets/sample.env").write_text(text)
cog.outl("```")
cog.out(text)
cog.outl("```")
]]]-->
```
##########################################################
# Sample forge .env file.
# Place at the root of your git repo.
# Or use `forge --env <fname>` to specify.
##########################################################

#################
# LLM parameters:
#
# Include xxx_API_KEY parameters and other params needed for your LLMs.
# See https://forge.chat/docs/llms.html for details.

## OpenAI
#OPENAI_API_KEY=

## Anthropic
#ANTHROPIC_API_KEY=

##...

#######
# Main:

## Specify the OpenAI API key
#OPENAI_API_KEY=

## Specify the Anthropic API key
#ANTHROPIC_API_KEY=

## Specify the model to use for the main chat
#forge_MODEL=

## Use claude-3-opus-20240229 model for the main chat
#forge_OPUS=

## Use claude-3-5-sonnet-20241022 model for the main chat
#forge_SONNET=

## Use claude-3-5-haiku-20241022 model for the main chat
#forge_HAIKU=

## Use gpt-4-0613 model for the main chat
#forge_4=

## Use gpt-4o-2024-08-06 model for the main chat
#forge_4O=

## Use gpt-4o-mini model for the main chat
#forge_MINI=

## Use gpt-4-1106-preview model for the main chat
#forge_4_TURBO=

## Use gpt-3.5-turbo model for the main chat
#forge_35TURBO=

## Use deepseek/deepseek-coder model for the main chat
#forge_DEEPSEEK=

## Use o1-mini model for the main chat
#forge_O1_MINI=

## Use o1-preview model for the main chat
#forge_O1_PREVIEW=

#################
# Model Settings:

## List known models which match the (partial) MODEL name
#forge_LIST_MODELS=

## Specify the api base url
#OPENAI_API_BASE=

## Specify the api_type
#OPENAI_API_TYPE=

## Specify the api_version
#OPENAI_API_VERSION=

## Specify the deployment_id
#OPENAI_API_DEPLOYMENT_ID=

## Specify the OpenAI organization ID
#OPENAI_ORGANIZATION_ID=

## Specify a file with forge model settings for unknown models
#forge_MODEL_SETTINGS_FILE=.forge.model.settings.yml

## Specify a file with context window and costs for unknown models
#forge_MODEL_METADATA_FILE=.forge.model.metadata.json

## Verify the SSL cert when connecting to models (default: True)
#forge_VERIFY_SSL=true

## Specify what edit format the LLM should use (default depends on model)
#forge_EDIT_FORMAT=

## Use architect edit format for the main chat
#forge_ARCHITECT=

## Specify the model to use for commit messages and chat history summarization (default depends on --model)
#forge_WEAK_MODEL=

## Specify the model to use for editor tasks (default depends on --model)
#forge_EDITOR_MODEL=

## Specify the edit format for the editor model (default: depends on editor model)
#forge_EDITOR_EDIT_FORMAT=

## Only work with models that have meta-data available (default: True)
#forge_SHOW_MODEL_WARNINGS=true

## Soft limit on tokens for chat history, after which summarization begins. If unspecified, defaults to the model's max_chat_history_tokens.
#forge_MAX_CHAT_HISTORY_TOKENS=

## Specify the .env file to load (default: .env in git root)
#forge_ENV_FILE=.env

#################
# Cache Settings:

## Enable caching of prompts (default: False)
#forge_CACHE_PROMPTS=false

## Number of times to ping at 5min intervals to keep prompt cache warm (default: 0)
#forge_CACHE_KEEPALIVE_PINGS=false

###################
# Repomap Settings:

## Suggested number of tokens to use for repo map, use 0 to disable (default: 1024)
#forge_MAP_TOKENS=

## Control how often the repo map is refreshed. Options: auto, always, files, manual (default: auto)
#forge_MAP_REFRESH=auto

## Multiplier for map tokens when no files are specified (default: 2)
#forge_MAP_MULTIPLIER_NO_FILES=true

################
# History Files:

## Specify the chat input history file (default: .forge.input.history)
#forge_INPUT_HISTORY_FILE=.forge.input.history

## Specify the chat history file (default: .forge.chat.history.md)
#forge_CHAT_HISTORY_FILE=.forge.chat.history.md

## Restore the previous chat history messages (default: False)
#forge_RESTORE_CHAT_HISTORY=false

## Log the conversation with the LLM to this file (for example, .forge.llm.history)
#forge_LLM_HISTORY_FILE=

##################
# Output Settings:

## Use colors suitable for a dark terminal background (default: False)
#forge_DARK_MODE=false

## Use colors suitable for a light terminal background (default: False)
#forge_LIGHT_MODE=false

## Enable/disable pretty, colorized output (default: True)
#forge_PRETTY=true

## Enable/disable streaming responses (default: True)
#forge_STREAM=true

## Set the color for user input (default: #00cc00)
#forge_USER_INPUT_COLOR=#00cc00

## Set the color for tool output (default: None)
#forge_TOOL_OUTPUT_COLOR=

## Set the color for tool error messages (default: #FF2222)
#forge_TOOL_ERROR_COLOR=#FF2222

## Set the color for tool warning messages (default: #FFA500)
#forge_TOOL_WARNING_COLOR=#FFA500

## Set the color for assistant output (default: #0088ff)
#forge_ASSISTANT_OUTPUT_COLOR=#0088ff

## Set the color for the completion menu (default: terminal's default text color)
#forge_COMPLETION_MENU_COLOR=

## Set the background color for the completion menu (default: terminal's default background color)
#forge_COMPLETION_MENU_BG_COLOR=

## Set the color for the current item in the completion menu (default: terminal's default background color)
#forge_COMPLETION_MENU_CURRENT_COLOR=

## Set the background color for the current item in the completion menu (default: terminal's default text color)
#forge_COMPLETION_MENU_CURRENT_BG_COLOR=

## Set the markdown code theme (default: default, other options include monokai, solarized-dark, solarized-light)
#forge_CODE_THEME=default

## Show diffs when committing changes (default: False)
#forge_SHOW_DIFFS=false

###############
# Git Settings:

## Enable/disable looking for a git repo (default: True)
#forge_GIT=true

## Enable/disable adding .forge* to .gitignore (default: True)
#forge_GITIGNORE=true

## Specify the forge ignore file (default: .forgeignore in git root)
#forge_forgeIGNORE=.forgeignore

## Only consider files in the current subtree of the git repository
#forge_SUBTREE_ONLY=false

## Enable/disable auto commit of LLM changes (default: True)
#forge_AUTO_COMMITS=true

## Enable/disable commits when repo is found dirty (default: True)
#forge_DIRTY_COMMITS=true

## Attribute forge code changes in the git author name (default: True)
#forge_ATTRIBUTE_AUTHOR=true

## Attribute forge commits in the git committer name (default: True)
#forge_ATTRIBUTE_COMMITTER=true

## Prefix commit messages with 'forge: ' if forge authored the changes (default: False)
#forge_ATTRIBUTE_COMMIT_MESSAGE_AUTHOR=false

## Prefix all commit messages with 'forge: ' (default: False)
#forge_ATTRIBUTE_COMMIT_MESSAGE_COMMITTER=false

## Commit all pending changes with a suitable commit message, then exit
#forge_COMMIT=false

## Specify a custom prompt for generating commit messages
#forge_COMMIT_PROMPT=

## Perform a dry run without modifying files (default: False)
#forge_DRY_RUN=false

## Skip the sanity check for the git repository (default: False)
#forge_SKIP_SANITY_CHECK_REPO=false

########################
# Fixing and committing:

## Lint and fix provided files, or dirty files if none provided
#forge_LINT=false

## Specify lint commands to run for different languages, eg: "python: flake8 --select=..." (can be used multiple times)
#forge_LINT_CMD=

## Enable/disable automatic linting after changes (default: True)
#forge_AUTO_LINT=true

## Specify command to run tests
#forge_TEST_CMD=

## Enable/disable automatic testing after changes (default: False)
#forge_AUTO_TEST=false

## Run tests and fix problems found
#forge_TEST=false

############
# Analytics:

## Enable/disable analytics for one session (default: False)
#forge_ANALYTICS=false

## Specify a file to log analytics events
#forge_ANALYTICS_LOG=

## Permanently disable analytics
#forge_ANALYTICS_DISABLE=false

#################
# Other Settings:

## specify a file to edit (can be used multiple times)
#forge_FILE=

## specify a read-only file (can be used multiple times)
#forge_READ=

## Use VI editing mode in the terminal (default: False)
#forge_VIM=false

## Specify the language to use in the chat (default: None, uses system settings)
#forge_CHAT_LANGUAGE=

## Check for updates and return status in the exit code
#forge_JUST_CHECK_UPDATE=false

## Check for new forge versions on launch
#forge_CHECK_UPDATE=true

## Install the latest version from the main branch
#forge_INSTALL_MAIN_BRANCH=false

## Upgrade forge to the latest version from PyPI
#forge_UPGRADE=false

## Apply the changes from the given file instead of running the chat (debug)
#forge_APPLY=

## Apply clipboard contents as edits using the main model's editor format
#forge_APPLY_CLIPBOARD_EDITS=false

## Always say yes to every confirmation
#forge_YES_ALWAYS=

## Enable verbose output
#forge_VERBOSE=false

## Print the repo map and exit (debug)
#forge_SHOW_REPO_MAP=false

## Print the system prompts and exit (debug)
#forge_SHOW_PROMPTS=false

## Do all startup activities then exit before accepting user input (debug)
#forge_EXIT=false

## Specify a single message to send the LLM, process reply then exit (disables chat mode)
#forge_MESSAGE=

## Specify a file containing the message to send the LLM, process reply, then exit (disables chat mode)
#forge_MESSAGE_FILE=

## Load and execute /commands from a file on launch
#forge_LOAD=

## Specify the encoding for input and output (default: utf-8)
#forge_ENCODING=utf-8

## Run forge in your browser (default: False)
#forge_GUI=false

## Enable/disable suggesting shell commands (default: True)
#forge_SUGGEST_SHELL_COMMANDS=true

## Enable/disable fancy input with history and completion (default: True)
#forge_FANCY_INPUT=true

#################
# Voice Settings:

## Audio format for voice recording (default: wav). webm and mp3 require ffmpeg
#forge_VOICE_FORMAT=wav

## Specify the language for voice using ISO 639-1 code (default: auto)
#forge_VOICE_LANGUAGE=en
```
<!--[[[end]]]-->

