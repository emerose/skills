# Setup: installing `gog` and authorizing accounts

`gog` is [openclaw/gogcli](https://github.com/openclaw/gogcli), an external tool
this skill drives. It is not bundled. This page covers getting it installed and
getting each Google account authorized — the two prerequisites before any other
reference is useful.

## 1. Install the binary

```bash
gog version              # already installed? prints e.g. "0.16.0 (Homebrew …)"
```

If it's missing:

```bash
brew install openclaw/tap/gogcli          # macOS / Linux (Homebrew)
# or Docker: ghcr.io/openclaw/gogcli:latest
# or download a release binary from https://github.com/openclaw/gogcli/releases
```

Config and stored tokens live under the OS app-config dir (macOS:
`~/Library/Application Support/gogcli/config.json`) with refresh tokens in the
system keyring. `gog auth status` prints the exact paths and keyring backend.

## 2. See what's already authorized

```bash
gog auth list            # email · client · scopes · added · method
gog auth list --json     # machine-readable
gog auth services        # every service gog can authorize, with OAuth scopes
```

Each row is one authorized account. The **scopes** column matters: an account
authorized only for `gmail` can't touch Drive. Re-run `auth add` with more
services to widen scope.

## 3. Authorize an account (OAuth)

```bash
gog auth add you@gmail.com --services gmail,calendar,drive
# aliases: `gog login you@gmail.com`
```

This opens a browser consent flow and stores a refresh token. `--services`
selects which APIs to request scopes for (comma-separated; see `gog auth
services` for the full list). A good full-featured grant:

```bash
gog auth add you@gmail.com --services gmail,calendar,drive,docs,sheets,contacts,tasks,people
```

Note `people` — it grants the OIDC `profile` scope that `gog whoami` / `gog
people me` need; without it those return **403 (permission_denied, exit 6)** even
though everything else works. Add more services later by re-running `auth add`
with the wider list.

Remove an account with `gog auth remove you@gmail.com` (alias `gog logout`).

## 4. Multiple accounts (the whole point)

Authorize as many accounts as you like — personal Gmail, one or more Workspace
orgs — and address each one explicitly per command:

```bash
gog auth add me@gmail.com      --services gmail,calendar,drive
gog auth add me@company.com    --services gmail,calendar,drive,docs,sheets

gog -a me@gmail.com   gmail search 'is:unread'
gog -a me@company.com calendar events --today
```

`--account` accepts an email or an alias. `GOG_ACCOUNT` sets a default for a
shell, but **in this skill prefer explicit `-a`** so there's never ambiguity
about which identity ran a command.

### Enable the no-send guard (recommended for every account)

Turn on gog's persistent send guard so email never goes out without a deliberate,
consented step:

```bash
gog config no-send set you@gmail.com
gog config no-send set me@company.com
gog config no-send list          # confirm which accounts are guarded
```

With this on, drafting still works but `send`/`forward`/`autoreply`/`drafts send`
are blocked until the guard is lifted. See the sending-policy section of
[SKILL.md](../SKILL.md) for how consented sends work. (The guard lives in gog's
local `config.json`, so it's a per-machine setup step, not part of the skill repo.)

### Aliases (do this — it makes everything readable)

```bash
gog auth alias set work     me@company.com
gog auth alias set personal me@gmail.com
gog auth alias list

gog -a work     gmail search 'is:unread'
gog -a personal calendar events --week
```

### Custom OAuth clients (per-org credentials)

Some Workspace orgs require you to use their own OAuth client. Store a client's
credentials under a name and select it with `--client`:

```bash
gog auth credentials set ~/Downloads/work-oauth-client.json --client work
gog --client work auth add me@company.com --services gmail,calendar,drive
gog --client work -a me@company.com gmail search 'is:unread'
gog auth credentials list
```

Service accounts with domain-wide delegation (for Admin/Directory work) are also
supported — see `gog auth --help` and `gog admin --help`.

## 5. Troubleshooting

- **Exit code 4 (`auth_required`)** from any command → that account's token is
  missing/expired or lacks the needed scope. Re-run `gog auth add … --services
  …` for it. Don't retry the failing command blindly.
- **Exit code 6 (`permission_denied`)** → authorized but the scope for that API
  wasn't granted; widen `--services` and re-add.
- `gog auth doctor` diagnoses auth, keyring, and refresh-token problems and
  suggests fixes.
- `gog auth keyring [backend]` switches/inspects the keyring backend (OS keyring
  vs encrypted file) if token storage is failing (e.g. headless boxes).

### "Access blocked: … can only be used within its organization"

The OAuth **client** you're authorizing with has its consent screen set to User
Type = **Internal**, which only lets accounts *in that client's own Workspace org*
authorize. Authorizing an account from a different org fails with this message.
This bites when one client is shared across orgs (e.g. an emerose-owned Internal
client can't authorize an `@othercorp.com` account). Fixes, best first:

1. **Give each org its own Internal client (durable).** Create a Desktop OAuth
   client in a Cloud project under *that* org, consent screen = Internal, store it
   under a name and use it for that org's account:
   `gog auth credentials set ~/otherorg-client.json --client otherorg` then
   `gog --client otherorg auth add you@othercorp.com --services …`. Internal apps
   need no verification and their refresh tokens don't expire.
2. **Make the existing client External + Testing (quick).** In the client's Cloud
   project, switch the consent screen to External and add the outside account
   under **Test users**. Works in minutes, but with sensitive scopes (Gmail/Drive)
   an External app left in **Testing** status **expires refresh tokens after 7
   days** — you'll re-auth that account weekly. Moving to production removes the
   7-day limit but requires Google's OAuth verification (a CASA security
   assessment for restricted Gmail/Drive scopes) — heavy for personal use, which
   is why per-org Internal clients (option 1) are preferred.

### `whoami` / `people me` returns 403

That account wasn't authorized with the `people` service (OIDC `profile` scope).
Everything else still works; re-run `gog auth add … --services …,people` to fix.

### Manual / remote (browserless) authorization

If the normal browser flow can't complete (headless box, or the localhost
callback keeps timing out), use the two-step remote flow — it decouples getting
the code from exchanging it:

```bash
gog auth add you@x.com --services … --remote --step 1          # prints an auth_url
# open the URL, approve; the browser lands on a 127.0.0.1/oauth2/callback?code=… page
# that won't load — copy that whole address-bar URL, then:
gog auth add you@x.com --services … --remote --step 2 --auth-url '<pasted redirect URL>'
```

`--manual` is the interactive single-command variant (paste the redirect URL when
prompted); `--timeout` extends the wait (manual flows default to 5m).
