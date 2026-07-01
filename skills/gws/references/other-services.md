# Other services: Docs, Sheets, Slides, Contacts, Tasks — and the general pattern

`gog` covers far more than mail/calendar/drive. Same rules everywhere: pass
`-a/--account`, add `--json` for parsing, and check `gog <service> --help` for
the real verbs and flags. Reading is free; sending/sharing/deleting/writing is
outward-facing or hard to reverse — confirm first.

## The general pattern (works for any service)

```bash
gog --help                    # every service group
gog <service> --help          # every verb in that service
gog schema                    # full machine-readable command + flag schema (best for you)
gog <service> <verb> --help   # exact flags for one verb
```

Services available include: `docs`, `sheets`, `slides`, `contacts`, `tasks`,
`chat`, `meet`, `forms`, `sites`, `keep`, `groups`, `admin`, `appscript`,
`analytics`, `searchconsole`, `youtube`. Don't guess flags — `--help` / `schema`
are authoritative and cheap.

## Docs

```bash
gog -a work docs cat <docId>                         # print the doc as plain text (read it)
gog -a work docs info <docId> --json                 # metadata
gog -a work docs export <docId> --format md --out ~/doc.md   # pdf|docx|txt|md|html
gog -a work docs create "New doc title"              # confirm intent first
gog -a work docs copy <docId> "Copy title"
gog -a work docs comments <command>
```

## Sheets

```bash
gog -a work sheets --help                            # exact read/append/update verbs
# typical: read a range, append rows, update cells — check --help for the range
# argument format (A1 notation) and value flags before writing.
```

Reading a sheet is safe; appending/updating cells mutates the user's data —
preview with `--dry-run` and confirm before writing.

## Slides

```bash
gog -a work slides --help                            # get/export/create/build verbs
```

## Contacts / People

```bash
gog -a work contacts --help                          # search/list/create contacts
gog -a work people me --json                         # your own profile (alias: gog whoami)
```

## Tasks

```bash
gog -a work tasks --help                             # list task lists, list/create/complete tasks
```

## Admin (Workspace org only)

`gog admin …` (Directory API) needs **domain-wide delegation** via a service
account — see `gog admin --help` and the service-account notes in
[setup.md](setup.md). Only relevant for org administrators.
