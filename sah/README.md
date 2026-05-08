# sah — Spark AI Hub launcher

One CLI command per OpenAI-compatible client. `sah opencode` launches
OpenCode wired to whatever LLM the Spark AI Hub is currently serving.
Switch models in the Hub UI; clients keep working without reconfiguration.

## Install (on a client laptop on the same LAN)

```sh
curl http://192.168.3.16:9000/sah/install.sh | sh
```

Override the Hub URL:

```sh
curl http://192.168.3.16:9000/sah/install.sh | sh -s -- --hub http://other-host:9000
```

## Usage

```sh
sah info                # show Hub URL and current model
sah integrations        # list supported clients and lifecycle commands
sah env                 # print OPENAI_BASE_URL / OPENAI_API_KEY exports
sah env --anthropic     # same but ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN

sah opencode            # launch OpenCode against the Hub
sah codex               # launch Codex against the Hub
sah hermes              # launch Hermes Agent against the Hub
sah qwen                # launch Qwen Code against the Hub
sah openclaw            # launch OpenClaw against the Hub
sah claude              # launch Claude Code against the Hub  (needs Anthropic-compat endpoint, WIP)
sah claude-desktop --install  # wire Claude Desktop to the Hub

sah <client> --install  # make the plain client use the Hub, where supported
sah <client> --status   # show whether the plain client is wired to the Hub
sah <client> --restore  # restore the plain client to its previous settings, where supported
sah <client> -- <args>  # pass args to the underlying client

sah set-hub http://1.2.3.4:9000   # change the Hub URL
```

## How it works

The Hub exposes an OpenAI-compatible proxy at `http://<hub>:9000/v1` that
forwards to whichever LLM is loaded on its upstream slot. The `model`
field on incoming requests is rewritten to the actually-loaded model, so
clients don't need to know or care which model is current.

`sah <client>` sets the right env vars (and patches the right config
files for clients that need a config-file approach, like Codex), then
execs the client. Zero per-client setup beyond running `sah` once.

`sah <client>` is non-invasive: it wires the spawned process to the Hub and
leaves the plain client untouched. For clients with supported config files,
`sah <client> --install` backs up the original settings and rewrites them so
plain `opencode`, `codex`, `qwen`, `openclaw`, or `claude` use the
Hub until `sah <client> --restore` puts the original settings back.
Hermes currently supports the non-invasive launcher only.
