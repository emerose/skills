# git-autosync — keep a git checkout fast-forwarded to its upstream

A tiny macOS **menu-bar app** that keeps a local git working tree current with its
upstream branch: when the checkout is cleanly on the tracked branch it
`git merge --ff-only`s; on **any local work it pauses and alerts** instead of
touching anything.

It's a **generic git tool** — it works for any local checkout. The motivating case
is a checkout that lives inside a **cloud-sync folder** (Google Drive
`~/Library/CloudStorage/…`, iCloud, Dropbox): there the working copy is browsed and
consumed *directly* (people/tools read the folder, not the git remote), so when
merges land on the remote the folder silently drifts behind the canonical state
until someone pulls. This app closes that gap automatically.

## Behavior

On launch, every N seconds, and via **Sync Now**:

- **Cleanly on the tracked branch** → `git merge --ff-only <remote>/<branch>`.
- **Any local work** → it **pauses and posts one notification** (de-duped per
  condition), never discarding anything:
  - uncommitted edits / untracked files
  - on a different branch (do feature work elsewhere)
  - local commits not yet pushed (it only fast-forwards *from* upstream; it never
    pushes)
- Folder not mounted/reachable → quietly waits; gitignored data is never touched.

The menu bar shows live state (✓ in sync, ⚠ paused, lock = no access, ✕ error) and
offers **Sync Now · Open Log · Open Folder · Quit**.

## Why a menu-bar app (and the no-Full-Disk-Access payoff)

A *background* launchd agent is denied access to provider-backed folders
(`~/Library/CloudStorage`, …) by macOS TCC — it would need **Full Disk Access**,
and you can only grant that to a binary (e.g. all of `/bin/zsh`), which is far too
broad. A normal **Aqua-session** app has that access natively, so for a checkout in
such a folder this needs **no Full Disk Access grant**. (For a plain local checkout
the access question never arises; you still get the live status + on-demand sync.)

## Install

Requires macOS 11+ and the Xcode Command Line Tools (`xcode-select --install`, for
`swiftc`). `git-lfs`, if the repo uses it, just needs to be on `PATH`
(`/opt/homebrew/bin` is included by default).

```sh
./install.sh --repo "/absolute/path/to/your/working/tree"
# options: --name GitSync  --remote origin  --branch main  --interval 900
```

Run it once per repo you want kept in sync, giving each a distinct `--name` (the
name is the menu-bar app, its bundle id, and its log/status filenames). The
installer compiles the Swift source, assembles + ad-hoc-signs a `.app` in
`~/Applications`, writes config, and installs a login `LaunchAgent`. It's
re-runnable.

## Configuration

The installer writes `~/Library/Application Support/<Name>/config.json`; the app
reads it at launch (or `$GITAUTOSYNC_CONFIG` if set). Only `repo` is required:

```json
{ "repo": "/abs/path/to/working/tree",
  "remote": "origin", "branch": "main", "intervalSeconds": 900 }
```

Edit it and restart the app (or **Sync Now**) to change settings.

## Status / logs / uninstall

```sh
cat "$HOME/Library/Logs/<Name>.status"   # one line: current state
"$HOME/Library/Logs/<Name>.log"          # history of syncs/pauses
./uninstall.sh --name "<Name>"           # add --keep-logs to retain logs
```

## Notes

- **Keep the checkout on the tracked branch.** Direct edits or a feature branch make
  it pause-and-alert by design — do feature work in separate worktrees.
- **Ad-hoc signed.** No paid Apple ID needed; rebuilding changes the signature,
  which is harmless here since no Full Disk Access is tied to it.
- **Notifications** use `UNUserNotificationCenter`; on first launch macOS asks to
  allow notifications for the app. If denied, the menu-bar icon still shows state.
