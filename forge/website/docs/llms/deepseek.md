---
parent: Connecting to LLMs
nav_order: 500
---

# DeepSeek

forge can connect to the DeepSeek.com API.
The DeepSeek Coder V2 model has a top score on forge's code editing benchmark.

```
python -m pip install -U forge-chat

export DEEPSEEK_API_KEY=<key> # Mac/Linux
setx   DEEPSEEK_API_KEY <key> # Windows, restart shell after setx

# Use DeepSeek Coder V2
forge --deepseek
```

