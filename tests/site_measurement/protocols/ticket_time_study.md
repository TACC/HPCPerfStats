# Ticket-time study (AUTHOR_NOTES item 7)

The hung-job write-up currently uses a **staff-reported order of magnitude**
(hour → minutes). That is not a timed ticket study.

## What to record

Use `templates/ticket_time.csv`. One row per ticket:

- `ticket_id` — RT / Jira / email id
- `date` — when the ticket was worked
- `system` — stampede3 / horizon / …
- `jid` — Slurm job if applicable
- `had_hps_packet` — `yes` if Job Detail was used as the primary packet
- `review_minutes` — wall-clock minutes the consultant spent on diagnosis
- `outcome` — resolved / waiting-on-user / hardware / unknown
- `notes`

Do not fill `review_minutes` from memory of “it felt faster”. If the clock was
not recorded, leave the cell empty.

## Summarize

```bash
python3 bin/summarize_ticket_time.py \
  --csv templates/ticket_time.csv \
  --out results/ticket_time.json
```

Example rows whose `ticket_id` starts with `example` are ignored.
