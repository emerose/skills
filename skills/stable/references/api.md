# Stable API — endpoint map & data model

Base URL `https://api.usestable.com`. Auth: `x-api-key: <key>` header on every
request. Canonical docs: <https://docs.usestable.com> (the machine-readable index
is <https://docs.usestable.com/llms.txt>). This file summarizes what the API
exposes and — importantly — what it does **not**.

## The read-only reality

The physical-mail *actions* a virtual mailbox performs — scan the interior of a
piece of mail, forward it, shred it, deposit a check — are **triggered in the
Stable dashboard, not via the API**. The API surfaces only their resulting status
as fields on a mail item (`scanDetails`, `forwardDetails`, `shredDetails`,
`depositDetails`). There is no `POST` to request them. So this skill can find mail
and report where each action stands, but cannot start one. When a user asks you to
forward/shred/scan/deposit, say so and point them to the dashboard.

## Endpoints this skill uses

| Method & path | Purpose | CLI |
|---|---|---|
| `GET /v1/mail-items` | list/search mail (filters below; Relay pagination) | `mail list`, `mail summary`, `checks list` |
| `GET /v1/mail-items/{id}` | one mail item in full | `mail get`, `mail image`, `mail scan` |
| `GET /v1/tags` | list tags | `tags list` |
| `POST /v1/tags` | create tags `{tags:[{name}]}` | `tags create` |
| `PUT /v1/tags/{id}` | rename `{name}` | `tags rename` |
| `DELETE /v1/tags` | delete `{tags:[{id}]}` | `tags delete` |
| `POST /v1/mail-items/tags` | assign/remove `{mailItemIds,tags:[{id,isApplied}]}` | `tags assign`/`remove` |
| `GET/POST/PUT/DELETE /v1/teams…` | teams (same shapes as tags) | `teams …` |
| `POST /v1/mail-items/teams` | assign/remove teams | `teams assign`/`remove` |
| `GET /v1/locations` | list mailbox locations | `locations list` |

### `GET /v1/mail-items` query params

Server-side: `id`, `locationId`, `createdAt_gt` / `_gte` / `_lt` / `_lte`,
`scan.status`, `scan.createdAt` (+ `_gt/_gte/_lt/_lte`). Pagination is
Relay-cursor: `first` + `after` (and `last` + `before`). The response is a
`MailItemsConnection`: `edges[].{cursor,node}`, `pageInfo.{hasNextPage,endCursor,…}`,
`totalCount`. The client's `iter_mail_items()` walks `first`/`after` until
`hasNextPage` is false.

## Endpoints this skill does NOT wrap (documented for completeness)

v1 of this skill is the **mail-triage core**. These exist in the API but are
setup/admin operations better done in the dashboard; wrap them here only if a real
need arises (add a client method + CLI command + test, then update this table and
SKILL.md's "can and cannot" section):

- **Companies** — `GET/POST /v1/companies`, `GET/PATCH /v1/companies/{id}`,
  `POST /v1/companies/{id}/deactivate`. A company is a legal entity; required to
  create registered-agent locations.
- **Locations (write) & onboarding** — `POST /v1/locations`,
  `POST /v1/locations/{id}/deactivate`, and the USPS-1583 onboarding flow
  (`…/onboard/prefill`, `…/onboard/session`, `…/onboard/signature-packet`,
  `…/onboard/upload-urls`). CMRA vs `registeredAgent` location types;
  onboarding status runs `authorize → sign → verify → complete`. See
  <https://docs.usestable.com/docs/create-and-onboard-a-new-location>.

## Webhooks

Configured in the Stable **dashboard** (Developer section), delivered via Svix —
there is no API to manage them. If you want push notifications for new mail /
completed scans / deposited checks, set up a webhook endpoint there. Event-type
catalog: the Svix page linked from
<https://docs.usestable.com/docs/webhooks>. (This skill does not run a receiver.)

## Errors & exit codes

API errors return `{ "message": ... }` with standard HTTP statuses (400 Bad
Request, 403 Forbidden, 404 Not Found, 429 rate limited, 500 internal). The CLI
maps them to exit codes: `0` ok, `3` empty, `4` auth (401/403), `5` not found,
`7` rate limited, `1` other.
