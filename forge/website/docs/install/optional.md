---
parent: Installation
nav_order: 20
---

# Optional steps
{: .no_toc }

The steps below are completely optional.

- TOC
{:toc}


## Store your api keys 

You can [store your api keys in a .env file](/docs/config/dotenv.html)
and they will be loaded automatically whenever you run forge.

## Enable Playwright 

forge supports adding web pages to the chat with the `/web <url>` command.
When you add a url to the chat, forge fetches the page and scrapes its
content.

By default, forge uses the `httpx` library to scrape web pages, but this only
works on a subset of web pages.
Some sites explicitly block requests from tools like httpx.
Others rely heavily on javascript to render the page content,
which isn't possible using only httpx.

forge works best with all web pages if you install
Playwright's chromium browser and its dependencies:

```
playwright install --with-deps chromium
```

See the
[Playwright for Python documentation](https://playwright.dev/python/docs/browsers#install-system-dependencies)
for additional information.


## Enable voice coding 

forge supports 
[coding with your voice](https://forge.chat/docs/usage/voice.html)
using the in-chat `/voice` command.
forge uses the [PortAudio](http://www.portaudio.com) library to
capture audio.
Installing PortAudio is completely optional, but can usually be accomplished like this:

- For Windows, there is no need to install PortAudio.
- For Mac, do `brew install portaudio`
- For Linux, do `sudo apt-get install libportaudio2`

## Add forge to your editor 

Other projects have integrated forge into some IDE/editors.
It's not clear if they are tracking the latest
versions of forge,
so it may be best to just run the latest
forge in a terminal alongside your editor.

### NeoVim

[joshuavial](https://github.com/joshuavial) provided a NeoVim plugin for forge:

[https://github.com/joshuavial/forge.nvim](https://github.com/joshuavial/forge.nvim)

### VS Code

joshuavial also confirmed that forge works inside a VS Code terminal window.
forge detects if it is running inside VSCode and turns off pretty/color output,
since the VSCode terminal doesn't seem to support it well.

### Other editors

If you are interested in creating an forge plugin for your favorite editor,
please let me know by opening a
[GitHub issue](https://github.com/forge-AI/forge/issues).


## Install the development version of forge 

If you want the very latest development version of forge
you can install directly from GitHub:

```
python -m pip install --upgrade git+https://github.com/forge-AI/forge.git
```

If you've git cloned the forge repository already, you can install "live" from your local copy. This is mostly useful if you are developing forge and want your current modifications to take effect immediately.

```
python -m pip install -e .
```

