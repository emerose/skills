# Reading, searching, and triaging mail

All commands here are read-only. Add `--json` to get raw API objects for parsing;
omit it for a compact human view.

## Filters

| Flag | Where applied | Notes |
|------|---------------|-------|
| `--location <id>` | server | mailbox location; get ids from `stable locations list` |
| `--since` / `--until` | server | receipt-date window; accepts ISO, or `7d`/`24h`/`2w`/`today`/`yesterday` |
| `--scan-status {processing,completed}` | server | state of the interior scan |
| `--limit <n>` (default 50) / `--all` | client | cap results; `--all` pages everything |
| `--unread` | client | `readAt` is null |
| `--returned` | client | `isReturnedToSender` is true |
| `--with-checks` | client | item contains ≥1 transcribed check |
| `--tag <name>` / `--team <name>` | client | repeatable; matches by name |

**Server vs client filters matter for correctness.** Server filters (`location`,
date, `scan-status`) are applied by the API before pagination. Client filters
(`unread`, `returned`, `with-checks`, `tag`, `team`) are applied to whatever pages
were fetched — the CLI widens its fetch ~5× when a client filter is active, but on
a very large unbounded mailbox they can still under-return. Always pair them with a
`--since`/`--limit` window and say so if completeness matters.

## Commands

```bash
stable mail list [filters] [--json]      # one dense line per item
stable mail summary [filters] [--json]   # multi-line digest incl. AI scan summary
stable mail get <id> [--json]            # a single item in full
stable mail image <id> [--out PATH]      # download the envelope photo
stable mail scan <id> [--out PATH]       # download the interior scan, if present
```

`mail list` line format:

```
<id8>  <date>  <from>  → <recipient>  [flags]  #tags
```

Flags summarize state: `unread`, `returned`, `N✓` (N checks), `scan:<status>`,
`fwd:<status>`, `archived`.

`mail summary` / `mail get` show the from/to, location, status flags, any
**scan-notice type** (why mail couldn't be fully scanned — e.g. `currency`,
`bookletOrBoundItem`, `exceedsPageLimit`), the **AI `scanDetails.summary`**, and a
line per check.

## The mail item object (fields you'll actually use)

Retrieved via `GET /v1/mail-items` (list, Relay-style connection with
`edges[].node`, `pageInfo`, `totalCount`) and `GET /v1/mail-items/{id}` (single).

- `id`, `from`, `createdAt`, `archivedAt`, `readAt`, `clearAt` (when it leaves storage)
- `recipients.line1.text`, `recipients.business.{id,name}`, `recipients.individual.{id,firstName,lastName}`
- `imageUrl` — short-lived **signed** URL to the envelope image
- `scanDetails` — `{ status, imageUrl, ocrResultUrls[], summary, scanNoticeType }`
- `forwardDetails` — `{ status, trackingNumber, cost }` (read-only; forwarding is triggered in the dashboard)
- `depositDetails` — `{ status, trackingNumber }`
- `shredDetails` — `{ status }`
- `checks[]` — see below
- `tags[]`, `teams[]` — assigned labels (see references/organize.md)
- `isReturnedToSender` — boolean

Signed URLs (`imageUrl`, `scanDetails.imageUrl`, `ocrResultUrls`) expire; fetch
them promptly. The CLI's download commands fetch them **without** the `x-api-key`
header (sending it can break the URL signature).

### Parsing example

```bash
# subjects + one-line AI summary of unread mail this week, as TSV
stable mail summary --unread --since 7d --json \
  | python3 -c 'import sys,json;
for i in json.load(sys.stdin):
    s=(i.get("scanDetails") or {}).get("summary","")
    print(i["id"], i.get("from"), s[:80], sep="\t")'
```

## Checks

Checks are transcribed from inside mail and live on `mailItem.checks[]`. There is
no standalone checks endpoint — `stable checks list` iterates mail items (respecting
`--location`/`--since`/`--until`/`--limit`/`--all`) and collects their checks.

```bash
stable checks list --since 30d              # human: one line per check + total
stable checks list --status completed --json
```

Check fields: `amount`, `currency`, `payer`, `payee`, `memo`, `checkNumber`,
`issueDate`, `voidDate`, `routingNumber`, `accountNumber`, `status`
(`notRequested` | `processing` | `completed` | `failed`), `destinationAccount`
(`{name,last4,type}` — where a deposit was sent), `failureDetails` (`{code,description}`),
and `images[]` (signed URLs). See
[failure reason codes](https://docs.usestable.com/docs/failure-reason-codes).

**Depositing a check is a dashboard action, not an API call.** `status` and
`destinationAccount` reflect a deposit that was already requested in the dashboard;
this skill reports them, it does not initiate deposits.
