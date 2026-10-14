# Phosphene repo stats — maintainer guide

A tiny, stdlib-only pipeline that snapshots GitHub repo metrics into a local
JSONL on the maintainer's Mac and serves a dashboard through the Phosphene
panel itself. Nothing public, no GitHub Pages, no cloud — this pipeline only
reads GitHub's API and uploads nothing. (Not to be confused with the panel's
anonymous usage analytics, which does send: see `docs/ANALYTICS.md`. The
dashboard's *Usage* section displays that data; it is not what collects it.)

**Open the dashboard:** the Phosphene panel must be running, then visit
**<http://127.0.0.1:8199/stats>** — or serve it read-only without the panel
(see *Read-only dashboard* below).

## The daily fetch does NOT depend on the panel (2026-10-14)

The panel's `stats_fetch_loop` only runs while a panel is up. The owner's
panel was down from 2026-09-29 to 2026-10-14 and nothing fetched; GitHub's
traffic API keeps 14 days. (Sep 24-28 were saved only because other panels on
the same Mac had fetched into their own state/ — merged back on 2026-10-14.)
The archive is now kept by a **launchd job** on the maintainer's Mac:

| | |
|---|---|
| Label | `com.mrbizarro.phosphene-stats-fetch` |
| Plist | `~/Library/LaunchAgents/com.mrbizarro.phosphene-stats-fetch.plist` |
| When | daily 09:15 local, at login, and on wake if a run was missed |
| Runs | `~/AI/projects/phosphene/notes/stats/run_stats_fetch.sh` (private PM hub) |
| Code | `scripts/fetch_repo_stats.py` read from `beta/main` with `git show` — no checkout touched |
| Writes | `phosphene-dev.git/state/stats-data.jsonl` — **append-only** (one new line, or today's last line replaced) |
| Token | `gh auth token` (never logged) |
| Proof | exits 1 with `FATAL` unless the archive's last row is today's UTC date |
| Log | `notes/stats/fetch.log` (+ `launchd.log` for the job's own output) |

```bash
launchctl list | grep phosphene-stats-fetch                  # loaded? last exit code
launchctl kickstart -k gui/$(id -u)/com.mrbizarro.phosphene-stats-fetch   # run now
tail -20 ~/AI/projects/phosphene/notes/stats/fetch.log
```

The dashboard header turns red when the newest snapshot is more than 36 h
old — that is the signal this job stopped.

## Read-only dashboard

`PHOSPHENE_STATS_SOURCE_DIR=<another install's state dir>` makes a panel
read that install's `stats-data.jsonl` and `usage-log.jsonl` and never write
them: no fetch loop, `/stats/refresh` answers 409, its own capture mirror
stays in its own `LTX_STATE_DIR`. Run it on a spare port with
`PHOSPHENE_ANALYTICS_DISABLED=1`.

## User numbers — one definition each

| Tile | Definition |
|---|---|
| Installs that rendered | installs (one anonymous install id each) that finished ≥1 render, all time. The user count. |
| Installs that opened the app | installs that sent ≥1 `app_boot`, all time; the gap to the line above is failed installs, reinstalls that minted a new id, and one-off opens |
| Weekly active installs | installs that opened the app in the last 7×24 h. The growth chart's weekly line is the same window laid back to back ending now, so its last point equals the tile |
| Daily unique cloners, summed (GitHub) | upper bound on cloners, never a user count — GitHub only publishes per-day uniques |

**Owner exclusion.** Every fleet query drops `test_rig` events *and* the ids
/ fingerprints in `analytics-owner-installs.json` (path:
`PHOSPHENE_ANALYTICS_OWNER_FILE`, default `<state>/analytics-owner-installs.json`).
`test_rig` alone excluded nothing: no event had ever carried it, because
nothing that launches a panel on the maintainer's Mac set
`PHOSPHENE_TEST_RIG`. A file `~/.phosphene-test-rig` now tags every install
on that Mac at the source.

## The pieces

| Path | Role | In public repo? |
|---|---|---|
| `scripts/fetch_repo_stats.py` | Stdlib-only fetcher. Hits GitHub's REST API, writes one JSON row per UTC day. | yes (code only, no data) |
| `panel_assets/stats.html` | Dashboard. Loads the JSONL at page load, renders cards + Chart.js charts client-side. No build step. | yes (template only, no data) |
| `state/stats-data.jsonl` | Append-only data store on the maintainer's Mac. | **NO** — gitignored, never leaves your machine |
| Panel routes `/stats` + `/stats/data` | Serve the dashboard + data file. Both 127.0.0.1-only via the panel's standard local-only guard. | n/a |
| Panel `stats_fetch_loop` background thread | **Opt-in only** (`PHOSPHENE_STATS_FETCH=1`). Runs the fetcher once a day (and once at startup if the data file is stale or missing). | n/a |

## The panel's own fetch is OPT-IN — one rule (4.19.3)

> **The panel fetches repo stats only when it was started with
> `PHOSPHENE_STATS_FETCH=1`.** Without it — every user's install, every
> clean room, the owner's own panels too — the panel never starts
> `stats_fetch_loop`, never reads a token variable, never runs `gh`, and
> never logs a `stats:` line. `POST /stats/refresh` answers 409.

Why (issue #106): through 4.19.2 the loop started on **every** install. To
find a token it ran the user's own `gh auth token`, so anyone with the GitHub
CLI logged in had the panel quietly use *their* GitHub credentials once a day
to read *our* repo's traffic; everyone else got "stats: no GitHub token
resolvable …" in their log and, on 4.15 and older, in their bug reports.

Why an environment variable and not the `~/.phosphene-test-rig` marker: the
marker means "tag this Mac's analytics as a test rig" and sits on the
maintainer's Mac, so every clean room built there would have started calling
`gh` with the owner's credentials. Fetching is a separate decision, made per
process. The owner does not need it at all: the **launchd job above** keeps
the archive, and the dashboard (`/stats`, `/stats/data`, `/stats/usage`)
reads the archive the same way with or without the opt-in.

Gate: `test_stats_fetch_opt_in.py` (no `gh` subprocess, no token read, no
thread, no log line without the opt-in; unchanged with it).

## What you actually need to do (only if you opt a panel in)

The launchd job is the normal path. To make a panel fetch as well, start it
with `PHOSPHENE_STATS_FETCH=1`, and make sure a GitHub token is resolvable.
The panel checks these in order, first hit wins:

1. `PHOSPHENE_REPO_STATS_TOKEN` env var (explicit, recommended for Pinokio)
2. `GH_STATS_TOKEN` env var
3. `GH_TOKEN` / `GITHUB_TOKEN` env var
4. `gh auth token` subprocess (when the gh CLI is installed + logged in)

The token needs the `repo` scope (the GitHub Traffic API requires push
access). `gh auth login` gives you that by default.

**Then:** restart the panel with `PHOSPHENE_STATS_FETCH=1` in its
environment. Within 90 seconds the background thread runs the first fetch.
Open `/stats` — done.

## Run the fetcher manually (debug / catch-up)

```bash
GITHUB_TOKEN=$(gh auth token) python3 scripts/fetch_repo_stats.py
```

Useful flags:

```bash
python3 scripts/fetch_repo_stats.py --dry-run             # don't write JSONL
python3 scripts/fetch_repo_stats.py --repo OWNER/NAME     # different repo
```

The fetcher is idempotent — re-running on the same UTC day REPLACES today's
row rather than appending a duplicate — and append-only: earlier lines are
never parsed and re-written, they are kept byte-for-byte.

## What's collected

One JSON object per UTC day. Top-level fields (see the fetcher's docstring
for the canonical schema):

- `date`, `fetched_at`
- `stars`, `forks`, `watchers`, `subscribers`, `open_issues`, `open_prs`
- `clones` (today), `views` (today)
- `clones_window` / `views_window` — full 15-day Traffic API arrays
- `stars_timeline` — cumulative `[{date, count}]`. Logins are dropped, not
  stored — privacy by construction.
- `referrers` — top 10 with `{referrer, count, uniques}`, 14-day aggregate
- `paths` — top 10 with `{path, title, count, uniques}`
- `releases` — `{total, by_release}` over ALL releases (paginated since
  2026-10-14; it used to read only the newest 100). Code tags carry no
  files — Phosphene ships by git — so the dashboard shows them as
  *source-only*; only `weights-*` tags have downloads.

## What's deliberately NOT collected

GitHub's API cannot give you any of these no matter how hard you mine it:

- Apple Silicon tier of installs (M1 / M2 / M3 / M4 family/variant)
- Unified RAM per install
- macOS version
- Whether `pinokio install` actually completed
- Which modes get used (Character vs Base, Q4 vs Q8 HQ, Fast vs Exact)
- Render wall-times in the wild
- Error frequency / crash signatures
- Active vs lapsed users

If you ever want any of those you'd have to ship client-side telemetry,
which we explicitly decided against (the brief 2026-05-21 opt-in experiment
was rolled back the next day). GitHub-data signal is the floor and the
ceiling.

## Pinokio notes

Pinokio publishes NO install / activation stats per app. The only signal
you have that a Pinokio user installed Phosphene is the `clone` count
spike on the repo — indistinguishable from a `git clone` from a human, a
`git pull` in CI, or a researcher poking at the source. Don't read too
much into day-level fluctuations.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Dashboard at /stats is blank | No data yet (first fetch hasn't run) | Wait 90 s after panel boot or run fetcher manually |
| Panel never fetches, Refresh answers 409 | Expected: the panel's own fetch is opt-in | Check the launchd job; or start the panel with `PHOSPHENE_STATS_FETCH=1` |
| Panel stderr (opt-in only): `stats: no GitHub token resolvable` | No token configured anywhere | `gh auth login`, or set `GITHUB_TOKEN` / `PHOSPHENE_REPO_STATS_TOKEN` |
| Panel log: `stats: fetch failed (exit 1)` | Token lacks `repo` scope | `gh auth refresh -s repo` |
| `/stats/data` returns empty body | `state/stats-data.jsonl` doesn't exist yet | Same as "blank" above |
| Charts blank but cards filled | Only one snapshot so far — charts need ≥2 points | Wait for tomorrow's fetch or change system date for testing |
| Dashboard ships data into a public commit | Should be impossible after the 2026-05-22 rewrite — `state/` is gitignored | If you see this, file an issue immediately |

## FAQ

**Why no client-side telemetry?** Mr Bizarro's call on 2026-05-22: "not
going to be well accepted in the open source world." We stand on what
GitHub already publishes about the repo. Stays private; stays simple.

**Why is the data file local and not in the repo?** Daily clone / view
counts + referrer breakdowns are private business signal. If you want a
public dashboard down the road, that's a deliberate later decision — the
machinery is here.

**How long is the history?** Forever, in `state/stats-data.jsonl`. The
GitHub Traffic API only retains 14 days; this file is exactly how we
keep older days from disappearing.

**Can I rebuild old data?** No. The Traffic API's 15-day window is hard;
anything older than today − 14 days is unrecoverable. So don't delete
the JSONL.

**Can a teammate see this?** Only if they have access to your Mac and the
panel is running. The panel binds 127.0.0.1 with no auth — same posture
as the rest of Phosphene.
