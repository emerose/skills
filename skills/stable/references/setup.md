# Setup — API key, environment, connectivity

## Get an API key

The Stable API requires a key and is available on plans with API access
(**Grow** and up). Keys are **not self-serve**: request one from Stable at
**priority@usestable.com** (per [docs.usestable.com](https://docs.usestable.com/reference/authentication)).
The key is sent as the `x-api-key` header on every request. Treat it like a
password — never commit it or paste it into shared logs.

## Where the CLI looks for the key

`scripts/stable_cli.py` resolves the key in this order (first wins):

1. `--api-key <key>` on the command line (avoid — ends up in shell history).
2. `STABLE_API_KEY` in the real environment.
3. A `STABLE_API_KEY=…` line in a `.env` file: `./.env`, then any parent of the
   script, then **`~/.env`**. Real env vars always win over `.env`.

### Preferred: macOS Keychain + direnv

This repo keeps secrets out of git entirely by loading them from the login
Keychain via [direnv](https://direnv.net) — the same pattern as the
`infrastructure` repo. The repo's committed [`.envrc`](../../../.envrc) exports
`STABLE_API_KEY` from a Keychain item; the key value never touches a file.

One-time setup:

```bash
# 1. store the key in the login Keychain (run it yourself so it isn't logged):
security add-generic-password -s stable-api -a "$USER" -w '<key>'
# 2. trust the repo's .envrc (once per checkout):
direnv allow
```

After that, `cd`-ing into the repo exports `STABLE_API_KEY` automatically and the
`stable` CLI just works. To rotate: `security add-generic-password -U -s stable-api
-a "$USER" -w '<new-key>'`.

> **Agents: direnv will NOT auto-load for you.** direnv only exports the key in an
> *interactive* shell that has the direnv hook installed. A one-off `bash -c` /
> tool-invoked command (the usual way an agent runs things) never triggers the
> hook, so `STABLE_API_KEY` will look "missing" even though the key is safely in
> the Keychain. Don't conclude the key is lost — pull it yourself:
>
> ```bash
> # export it from Keychain for the current command/session (works in any shell):
> export STABLE_API_KEY=$(security find-generic-password -s stable-api -a "$USER" -w)
> stable mail list --since 7d
>
> # …or let direnv resolve it for a single command, from the repo root:
> direnv exec /Users/sq/Development/skills  stable mail list --since 7d
> ```
>
> Quick check that the Keychain item exists (prints the length, not the secret):
> `K=$(security find-generic-password -s stable-api -a "$USER" -w 2>/dev/null); echo ${#K}`
> — a non-zero length (≈53) means it's there. If it prints `0`/errors, the item is
> genuinely missing and needs the one-time store above.

### Alternative: ~/.env

If you don't use direnv, drop it in `~/.env` (chmod 600):

```bash
printf 'STABLE_API_KEY=stbl_prod_...\n' >> ~/.env
chmod 600 ~/.env
```

Optional override: `STABLE_API_BASE` changes the API root (defaults to
`https://api.usestable.com`) — useful only for a sandbox/proxy.

## Verify connectivity

```bash
stable locations list          # cheapest authenticated call; prints your mailboxes
# exit 0 = ok, exit 4 = auth problem (bad/missing key), exit 3 = authed but empty
```

If you get exit `4` / an "HTTP 401/403" message, the key is missing from the
environment or wrong. Most often (for agents) it's not *missing* — it's in the
Keychain but direnv didn't load it; export it with the Keychain one-liner above
first. Otherwise re-check `~/.env` and that the key is active. Don't retry blindly
on `4`.

## Dependencies

The only runtime dependency is `httpx`, pulled automatically by `uv` from the
script's PEP-723 header. Nothing to install if you run via
`uv run …/stable_cli.py` or the `bin/stable` shim.

To run the tests (offline, no key needed — they use `httpx.MockTransport`):

```bash
uv run --with pytest --with httpx pytest skills/stable/tests -q
```
