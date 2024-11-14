To use forge with pipx on replit, you can run these commands in the replit shell:

```
pip install pipx
pipx run forge-chat ...normal forge args...
```

If you install forge with pipx on replit and try and run it as just `forge` it will crash with a missing `libstdc++.so.6` library.

