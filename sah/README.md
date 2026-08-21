# sah — Spark AI Hub launcher

One CLI command per OpenAI-compatible client. `sah opencode` launches
OpenCode wired to whatever LLM the Spark AI Hub is currently serving.
Switch models in the Hub UI; clients keep working without reconfiguration.

## Install (on a client laptop on the same LAN)

```sh
curl http://192.168.3.16:9000/sah/install.sh | sh -s -- --key sah-xxxxxxxx
```

The key is your own — copy the whole line from the Hub's **Connect a device**
panel, or find the key on your **Account** page. A Hub running with
authentication turned off does not need one.

Override the Hub URL:

```sh
curl http://192.168.3.16:9000/sah/install.sh | sh -s -- --hub http://other-host:9000
```

## Usage

```sh
sah info                # show Hub URL and current model
sah integrations        # list integrations, support mode, and lifecycle commands
sah env                 # print OPENAI_BASE_URL / OPENAI_API_KEY exports
sah env --anthropic     # same but ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN

sah claude              # launch Claude Code against the Hub
sah hermes              # launch Hermes Agent against the Hub
sah openclaw            # launch OpenClaw against the Hub
sah opencode            # launch OpenCode against the Hub
sah hermes-desktop      # launch Hermes Desktop against the Hub
sah codex               # launch Codex against the Hub
sah copilot             # launch GitHub Copilot CLI against the Hub
sah omp                 # launch OMP against the Hub
sah cline               # launch Cline against the Hub
sah droid --install     # wire Factory Droid to the Hub
sah dsh                 # launch DeepSeek Harness against the Hub
sah pi                  # launch Pi Coding Agent against the Hub
sah pool                # launch Poolside's pool against the Hub
sah qwen                # launch Qwen Code against the Hub
sah kimi                # launch Kimi Code CLI against the Hub
sah muse                # launch Muse Code against the Hub
sah chatgpt --install         # wire the ChatGPT desktop app to the Hub
sah claude-desktop --install  # wire Claude Desktop to the Hub

sah <client> --install  # configure the plain client to use the Hub, without launching
sah <client> --status   # show the client's Spark AI Hub wiring status
sah <client> --restore  # put the client's original settings back
sah <client> -- <args>  # pass args to the underlying client

sah set-hub http://1.2.3.4:9000   # change the Hub URL
sah set-key sah-xxxxxxxx          # save your Hub API key on this device
```

## Authentication

Every request sah makes to the Hub carries your API key. It is read from
`$SAH_API_KEY` if set, otherwise from `~/.config/sah/key`, which the installer
writes (mode 600) when you pass `--key`. If the Hub rejects it, sah says so and
tells you to run `sah set-key`.

## How it works

The Hub exposes an OpenAI-compatible proxy at `http://<hub>:9000/v1` (and an
Anthropic-shaped one at the root) that forwards to whichever LLM is loaded on
its upstream slot. The `model` field on incoming requests is rewritten to the
actually-loaded model, so clients don't need to know or care which model is
current.

`sah <client>` wires the client to the Hub and execs it. Clients configured
purely through environment variables or an inline config flag (`claude`,
`opencode`, `codex`, `copilot`, `pool`, `kimi`, `muse`, `dsh`) leave the plain
client's own settings untouched. Clients that can only be configured through
their config file (`hermes`, `openclaw`, `omp`, `cline`, `droid`, `pi`, `qwen`)
have that file rewritten — the original is backed up on the first write, and
`sah <client> --restore` puts it back byte for byte. `sah integrations` shows
which is which under Mode.

## Integration set

The integrations mirror Ollama's launcher registry
([`cmd/launch/`](https://github.com/ollama/ollama/tree/main/cmd/launch)) — same
names, same aliases, same display order, and the same per-client wiring — so a
client that works with `ollama launch <x>` works with `sah <x>`.

One of theirs is deliberately absent. `vscode` reaches models only through
Copilot Chat's built-in `ollama` vendor, which speaks the Ollama native API
(`/api/tags`, `/api/chat`) and cannot be pointed at an OpenAI-compatible
endpoint. Every integration here talks to the Spark AI Hub and nothing else;
none of them needs an Ollama server running. For the same reason `cline` is
wired through Cline's built-in `openai-compatible` provider rather than the
`ollama` one Ollama's launcher writes.

Where sah has to differ from Ollama it is because the environment differs, not
by preference, and each case is commented in the source:

- **`claude-desktop`** needs a loopback TCP forwarder. Claude Desktop's 3p mode
  requires HTTPS or `127.0.0.1`; Ollama always satisfies that because its
  server *is* localhost, while the Hub is across the LAN over plain http.
- **`muse`** gets a literal IPv4 base URL when the Hub is reached by an mDNS
  `.local` name, because Muse's HTTP client does not resolve those. Tunnel and
  Tailscale hostnames are passed through untouched.
- **`muse`** is also launched with `--reasoning-effort medium`: its default of
  `high` is rejected by the Hub's SGLang backend, and Muse silently retry-loops
  on that error rather than reporting it.
- **`hermes`** config is merged as text rather than with a YAML parser, because
  sah is stdlib-only Python.

Two integrations cannot be launched by sah at all, because they are desktop
apps with no CLI entry point: `chatgpt` and `claude-desktop` are
`--install` / `--status` / `--restore` only, and are macOS/Windows only.

`codex` writes a self-contained `~/.codex/sah.config.toml` and launches with
`--profile sah`, so the root `config.toml` is never read or written. That is
also why it cannot collide with `sah chatgpt`, which does write the root table
because the desktop app reads its model from there. `codex` 0.134.0 or newer is
required for profile config files.
