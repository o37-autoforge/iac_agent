---
parent: Installation
nav_order: 100
---

# forge with docker

forge is available as 2 docker images:

- `paulgauthier/forge` installs the forge core, a smaller image that's good to get started quickly.
- `paulgauthier/forge-full` installs forge will all the optional extras.

The full image has support for features like interactive help, the
browser GUI and support for using Playwright to scrape web pages.  The
core image can still use these features, but they will need to be
installed the first time you access them. Since containers are
ephemeral, the extras will need to be reinstalled the next time you
launch the forge core container.

### forge core 

```
docker pull paulgauthier/forge
docker run -it --user $(id -u):$(id -g) --volume $(pwd):/app paulgauthier/forge --openai-api-key $OPENAI_API_KEY [...other forge args...]
```

### Full version

```
docker pull paulgauthier/forge-full
docker run -it --user $(id -u):$(id -g) --volume $(pwd):/app paulgauthier/forge-full --openai-api-key $OPENAI_API_KEY [...other forge args...]
```

## How to use it

You should run the above commands from the root of your git repo,
since the `--volume` arg maps your current directory into the
docker container.
Given that, you need to be in the root of your git repo for forge to be able to
see the repo and all its files.

You should be sure your that
git repo config contains your user name and email, since the
docker container won't have your global git config.
Run these commands while in your git repo, before
you do the `docker run` command:

```
git config user.email "you@example.com"
git config user.name "Your Name"
```


## Limitations

- When you use the in-chat `/run` command, it will be running shell commands *inside the docker container*. So those commands won't be running in your local environment, which may make it tricky to `/run` tests, etc for your project.
- The `/voice` command won't work unless you can figure out how to give the docker container access to your host audio device. The container has libportaudio2 installed, so it should work if you can do that.