---
nav_order: 55
has_children: true
description: Information on all of forge's settings and how to use them.
---

# Configuration

forge has many options which can be set with
command line switches.
Most options can also be set in an `.forge.conf.yml` file
which can be placed in your home directory or at the root of
your git repo. 
Or by setting environment variables like `forge_xxx`
either in your shell or a `.env` file.

Here are 4 equivalent ways of setting an option. 

With a command line switch:

```
$ forge --dark-mode
```

Using a `.forge.conf.yml` file:

```yaml
dark-mode: true
```

By setting an environment variable:

```
export forge_DARK_MODE=true
```

Using an `.env` file:

```
forge_DARK_MODE=true
```

{% include env-keys-tip.md %}

