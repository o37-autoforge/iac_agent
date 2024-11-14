---
parent: Connecting to LLMs
nav_order: 500
---

# Azure

forge can connect to the OpenAI models on Azure.

```
python -m pip install -U forge-chat

# Mac/Linux:                                           
export AZURE_API_KEY=<key>
export AZURE_API_VERSION=2023-05-15
export AZURE_API_BASE=https://myendpt.openai.azure.com

# Windows
setx AZURE_API_KEY <key>
setx AZURE_API_VERSION 2023-05-15
setx AZURE_API_BASE https://myendpt.openai.azure.com
# ... restart your shell after setx commands

forge --model azure/<your_deployment_name>

# List models available from Azure
forge --list-models azure/
```

Note that forge will also use environment variables
like `AZURE_OPENAI_API_xxx`.
