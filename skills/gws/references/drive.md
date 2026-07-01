# Drive

`gog drive <verb>` (alias `drv`). Every command takes `-a/--account`. Add
`--json` for anything you parse. Several verbs have top-level aliases:
`gog ls`, `gog search`, `gog download`, `gog upload`.

## Browse and find

```bash
gog -a work drive ls --json                          # list root (or --parent <folderId>)
gog -a work drive ls --parent <folderId> --json
gog -a work drive tree --parent <folderId> --depth 2 # read-only folder tree
gog -a work drive search 'name contains "Q3 deck"' --json
gog -a work drive search 'fullText contains "budget" and mimeType = "application/pdf"' --json
gog -a work drive get <fileId> --json                # file metadata
gog -a work drive raw <fileId>                       # lossless raw API JSON
gog -a work drive du --parent <folderId>             # folder size summary
gog -a work drive inventory --json                   # read-only inventory export
```

Drive **search** uses the Drive query grammar: `name contains "…"`,
`fullText contains "…"`, `mimeType = "…"`, `'<folderId>' in parents`,
`starred = true`, `trashed = false`, `modifiedTime > '2026-01-01'`, joined with
`and`/`or`.

## Get files in/out

```bash
gog -a work drive download <fileId> --out ~/Downloads          # exports Google-native formats automatically
gog -a personal drive upload ~/report.pdf --parent <folderId>
```

## Organize (mutations — mkdir/move/rename/copy are low-risk; delete is not)

```bash
gog -a work drive mkdir "New Folder" --parent <parentId>
gog -a work drive move   <fileId> --parent <destFolderId>
gog -a work drive rename <fileId> "New name.pdf"
gog -a work drive copy   <fileId> "Copy name"
gog -a work drive delete <fileId>                    # → Trash (reversible)
gog -a work drive delete <fileId> --permanent        # gone forever — confirm explicitly first
```

## Sharing — outward-facing, confirm first

Sharing changes who can access a file. Show the user the file, the grantee, and
the role, and get a go-ahead. Preview with `--dry-run`.

```bash
gog -a work drive permissions <fileId> --json                       # who has access now
gog -a work drive share <fileId> --to user --email alice@x.com --role writer
gog -a work drive share <fileId> --to domain --domain company.com --role reader
gog -a work drive share <fileId> --to anyone --role reader           # link-shared — be careful
gog -a work drive unshare <fileId> <permissionId>                    # revoke a permission
```

`share` roles: `reader` | `writer` | `commenter`. `--to`: `user` (needs
`--email`) | `domain` (needs `--domain`) | `anyone`. `--discoverable` allows
search discovery for anyone/domain shares. Full flags: `gog drive share --help`.

## More

```bash
gog -a work drive drives --json                      # shared drives (Team Drives)
gog -a work drive comments <command>                 # file comments
gog -a work drive activity <command>                 # Drive Activity audit events
gog -a work drive changes <command>                  # change tracking for sync
gog -a work drive url <fileId>                        # printable web URL(s)
```
