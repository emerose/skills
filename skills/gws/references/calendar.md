# Calendar

`gog calendar <verb>` (alias `cal`). Every command takes `-a/--account`. Add
`--json` for anything you parse.

## List and read events

```bash
gog -a work calendar calendars --json                 # list this account's calendars (get their IDs)
gog -a work calendar events --today --json
gog -a work calendar events --tomorrow --json
gog -a work calendar events --week --week-start mon --json
gog -a work calendar events --days 14 --json          # next N days
gog -a work calendar events --from 2026-07-01 --to 2026-07-08 --json
gog -a work calendar events --query "standup" --json
gog -a work calendar event <calendarId> <eventId> --json   # one event, full detail
gog -a work calendar raw   <calendarId> <eventId>          # lossless raw API JSON
```

Time ranges accept **relative words** (`today`, `tomorrow`, `monday`), plain
dates, or RFC3339 with timezone. `--today`/`--tomorrow`/`--week`/`--days N` are
timezone-aware shortcuts. `--max` caps results, `--all-pages` fetches everything,
`--fail-empty` exits `3` if nothing matches (handy for "is anything scheduled?").

### Multiple calendars and accounts

```bash
gog -a work calendar events --all --json              # across all of this account's calendars
gog -a work calendar events --cal "Team" --cal "me@company.com" --json
gog -a work calendar users --json                     # workspace users (their email = their calendar ID)
gog -a work calendar team group@company.com --today   # events for everyone in a Google Group
```

To compare **work vs personal**, run once per account (`-a work` then
`-a personal`) and label the two result sets — they're separate identities.

## Availability

```bash
gog -a work calendar freebusy me@company.com colleague@company.com --from 2026-07-02T09:00:00-07:00 --to 2026-07-02T17:00:00-07:00 --json
gog -a work calendar conflicts --week --json          # find double-bookings
```

## Create / update / delete — confirm invitations first

Creating or updating an event with **guests sends invitations** — that's
outward-facing. Show the user the summary, time, and guest list, and get a
go-ahead before running it. Use `--dry-run` to preview.

```bash
gog -a work calendar create primary \
  --summary "Review" --from "2026-07-06T10:00:00-07:00" --to "2026-07-06T10:30:00-07:00"
# add guests / details via flags — check `gog calendar create --help` for
# --attendee, --location, --description, --all-day, --recurrence, etc.

gog -a work calendar update <calendarId> <eventId> --summary "Review (moved)" --from "…"
gog -a work calendar move   <calendarId> <eventId> <destinationCalendarId>
gog -a work calendar delete <calendarId> <eventId>              # confirm first; cancels for guests
gog -a work calendar respond <calendarId> <eventId> --response accepted   # RSVP (accepted|declined|tentative)
```

## Blocks

```bash
gog -a work calendar focus-time      --from "…" --to "…"
gog -a work calendar out-of-office   --from "…" --to "…"
gog -a work calendar working-location --from "…" --to "…" --type home
```

Always confirm exact flag names with `gog calendar <verb> --help` — the create/
update surface has many optional flags (attendees, reminders, visibility,
conferencing) not all listed here.
