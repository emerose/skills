# Organizing mail — tags & teams

Tags and teams are the **only write surface** in the Stable API. Both are labels
you attach to mail items; tags are free-form, teams typically map to who should
handle a piece of mail. The CLI treats them identically.

## Tags

```bash
stable tags list [--json]
stable tags create <name> [<name> ...] [--json]   # POST /v1/tags
stable tags rename <name-or-id> <new-name>        # PUT /v1/tags/{id}
stable tags delete <name-or-id> [<name-or-id> ...]# DELETE /v1/tags
stable tags assign <name-or-id> [...] --mail <id> [<id> ...]   # POST /v1/mail-items/tags (isApplied=true)
stable tags remove <name-or-id> [...] --mail <id> [<id> ...]   # POST /v1/mail-items/tags (isApplied=false)
```

## Teams

Identical verbs against `/v1/teams` and `/v1/mail-items/teams`:

```bash
stable teams list [--json]
stable teams create <name> [...] [--json]
stable teams rename <name-or-id> <new-name>
stable teams delete <name-or-id> [...]
stable teams assign <name-or-id> [...] --mail <id> [...]
stable teams remove <name-or-id> [...] --mail <id> [...]
```

`teams list` excludes system teams (Admin, Read-Only).

## Name-or-id resolution

`assign`/`remove`/`rename`/`delete` accept a tag/team **name or id**. The CLI
fetches the current list and resolves names (case-insensitive) to ids before the
call; an unknown name/id errors with exit `5` (not found) rather than silently
no-op'ing. So you can write the readable `stable tags assign IRS --mail <id>`
instead of hunting for the id first.

## Assign/remove wire format

Both assign and remove hit the same endpoint with an `isApplied` boolean:

```json
POST /v1/mail-items/tags
{ "mailItemIds": ["m1","m2"], "tags": [ {"id":"t1","isApplied":true} ] }
```

`isApplied:true` assigns, `false` removes. One call can set multiple tags across
multiple mail items. `set_mail_item_teams` is the same shape under `"teams"`.

## Safety

- **Deleting** a tag/team removes it from **every** mail item it was on across the
  account — confirm with the user before running a delete.
- Creating and assigning are low-stakes; just do them when asked.
- Filtering mail by tag/team on the read side (`stable mail list --tag IRS`) is
  **client-side** — see references/mail.md; pair with a `--since`/`--limit` window.
