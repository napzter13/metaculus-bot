# Kira setup: metaculus-bot on Kira (program of kira-earn)

**What you do, five lines:**

1. Make the accounts on the websites: a Metaculus bot (token), an OpenRouter key, AskNews credentials (details below).
2. Fill in the Metaculus participation and credit form: https://forms.gle/aQdYMq9Pisrf1v7d8
3. On Kira run `ssh -tt kira kira-secrets`, pick `metaculus-bot`, and paste each key. Nothing is echoed.
4. Tell @kira_earnings_bot: `check metaculus`.
5. That is all. The program restarts itself when its keys change. It forecasts at $0 until a grant lands.

This bot runs on Kira, not on GitHub Actions. It is the `metaculus-bot` program (uid 1103) of the
one `kira-earn` container, under the contract in `/projects/kira-earn/SPEC.md`. The repo is public
([napzter13/metaculus-bot](https://github.com/napzter13/metaculus-bot)) for the open-source credit
bonus; `origin` is the fork and `upstream` is No-Stream/metaculus-bot, kept for pulling fixes. GitHub
holds the code and CI only. Never run `gh secret set` for this bot: the keys live on Kira in
`/srv/kira-earn/env/metaculus-bot.env`, written by `kira-secrets`.

Facts about the competition were read on 2026-09-29 from the FutureEval participate page and the
[Resources page](https://www.metaculus.com/notebooks/38928/futureeval-resources-page/) (notebook
38928, last edited 2026-09-28). Where this doc and that page disagree, the page wins.

## What runs

`python -m kira_scheduler` (in `kira_scheduler/`) is the one process. It runs the same commands as the
three workflows it replaced, with the same env mapping and the same 70 minute run timeout. Slots are
UTC minutes past every hour. They stay UTC although the container's `TZ` is Europe/Stockholm, which
is 1 or 2 hours ahead: the scheduler reads the clock as UTC only, and the bot child is pinned to
`TZ=UTC` as it was on Actions:

| Workflow | Slots | Command | Needs (the gate) |
|---|---|---|---|
| tournament | :03 :23 :43 | `main.py` | `METACULUS_TOKEN` and `OPENROUTER_API_KEY` |
| minibench | :08 :38 | `main.py --mode minibench` | `METACULUS_TOKEN` and `OPENROUTER_API_KEY` |
| mantic | :05 :15 :25 | `main.py --mode mantic` | `MANTIC_TOKEN` and `OPENROUTER_API_KEY` |

- **Without the gate keys** a workflow logs `not configured` once per slot and does nothing. The
  program stays healthy, so the container stays healthy. The first run happens as soon as the keys
  exist, because a start (and `kira-secrets` restarts the program) catches up the latest missed slot.
- **Never two runs of one workflow at once.** A slot that arrives mid-run is queued once and starts
  the moment the run ends. A queued slot only counts as handled when its run has started, so a stop
  in between repeats it once and never twice. Different workflows may overlap, as they did on Actions.
- **A run is stopped as a whole process group.** After a timeout, an exit or a shutdown, anything the
  bot left running (a browser, a helper that ignores SIGTERM) is killed, so nothing outlives its run.
- **`kira-earn restart metaculus-bot` (or a SIGTERM)** stops the run in progress cleanly, records it,
  and leaves that slot unhandled so the next start repeats it.
- **The Cup is not run.** It is practice only; bots win nothing there.
- **Two changes from the old Actions gate:** the donated key alone no longer arms a workflow (the
  personal `OPENROUTER_API_KEY` is required, and needed anyway for the free tier), and each run's
  files live on Kira, not in a public Actions artifact.
- **GitHub Actions can no longer run this bot on a timer.** The `schedule:` triggers are gone from the
  three workflows. `workflow_dispatch` remains for a deliberate manual run; with no GitHub secrets
  set it stops at "Bot not configured", so it cannot spend. Keep it that way, and check it:

  ```bash
  gh secret list --repo napzter13/metaculus-bot     # must print nothing
  ```

  As a second lock, set the repository variable `KIRA_OWNS_SCHEDULE=true`. The tournament, MiniBench,
  Mantic and Cup workflows then skip their job outright, so even a secret added to GitHub by mistake
  cannot start a run beside Kira. (This one is a GitHub variable, the only thing here that is.)

**The default mode is the zero-credit posture (M4).** These are the program's `env_defaults`; the env
file overrides them:

| Variable | Default | Effect |
|---|---|---|
| `FORECASTER_FREE_TIER_ENABLED` | `true` | three `:free` forecasters and a free parser |
| `SUPPORT_MODEL_ROUTE` | `free` | text-only support roles on a `:free` model |
| `GAP_FILL_ENABLED`, `GAP_FILL_V2_ENABLED`, `NATIVE_SEARCH_ENABLED` | `false` | the three paid web-research roles off |
| `GEMINI_SEARCH_ENABLED` | `false` | on without a Google key it errors on every question |
| `OPENROUTER_CREDIT_FLOOR_USD` | `15` | early warning sized for a small grant, not upstream's $1,500 |

Mantic is the exception: like its old workflow, it pins the research switches on and does not read
these flags, and it spends your personal OpenRouter key (about $3 a question). It stays idle until
you add `MANTIC_TOKEN`. Only add that on purpose.

### Stacking is off in every mode, and what the log line means

Every run log starts with `Ensemble configured: 3 model(s) | Aggregation: conditional_stacking` and
names an Opus stacker. **That is the configured strategy, not what runs.** The bot's strategy is
hard-coded to conditional stacking, and upstream turns it off per question type with
`BINARY_STACKING_ENABLED`, `MC_STACKING_ENABLED` and `NUMERIC_STACKING_ENABLED`, all `false` (they
default to off in the code as well). The scheduler pins all three to `false` for every workflow, in
every mode, **whatever the env file says and whether or not the keys are funded**, so there is no
variable to turn stacking on and credits arriving later do not turn it on either.

What happens to a question is therefore: the three forecasters answer, and the published forecast is
their **median**. When they disagree strongly, the bot skips the stacker and logs
`Conditional stacking SKIPPED: stacking disabled for this question type` (the published comment then
carries `STACKER_OUTCOME=skipped_config_off`). The crux analysis, the targeted search and the paid
Opus stacker are not called, so none of them can bill a funded or an unfunded key. The stacker object
is only constructed at start-up, which makes no call and costs nothing. Enabling stacking would be a
code change to the pinned values in `kira_scheduler/spec.py`, and upstream measured the stacker as no
better than the median (and worse on numeric questions).

**What M4 spends.** On OpenRouter, nothing: the forecasters, the parser and every text-only support
role run on `:free` models, and the paid research roles, the stacker and its helpers are off or
gated off. This is read from the code and the env the scheduler builds; it has not yet been
confirmed on a live run, because the first runs found no open questions (rc=0).

## Step by step

An agent may not create accounts or enter credentials, even with permission, so these are yours.
`kira-secrets` asks for each value with nothing echoed, and restarts the program when you finish.
Times are hands-on; waiting on Metaculus is not included.

### 1. Metaculus bot account (10 min)

On metaculus.com, sign up as a human (or log in). Then **Settings > My Forecasting Bots > Create a
Bot**, enter the details, and copy the bot's API key. That is `METACULUS_TOKEN`. To see the bot's
forecasts later: Settings > My Bots > "Switch to bot account", then open a question; the bot's
comment is under the "Private" tab (FutureEval makes them public at intervals).

### 2. The form (10 min, then wait)

One Google Form does both jobs: <https://forms.gle/aQdYMq9Pisrf1v7d8>. The first section is the
**required** participation form (three required questions); the rest is the LLM-credit application.
It asks about you, your motivation and your commercial status, so it is yours to fill in. Worth
knowing when you answer:

- Commercial bots (a for-profit with three or more people) get no credits and no prizes unless
  fully open-sourced. This repo is public and open-source.
- Credits cover OpenAI, Anthropic and Google through OpenRouter only. The roster (o3,
  claude-sonnet-4.5, gpt-5.6-sol) is inside that. The page encourages asking for SOTA models.
- The published norm is "$1-$1.5 per question" for competitive bots. This bot measures $1.44 to
  $1.59 in full mode, so size the request with "Cost and credits" below.
- **"If you run out, assume we won't be able to give you more credits."** Re-apply each season.
  Plan for no top-ups.

### 3. OpenRouter key (5 min)

Create an account and key at openrouter.ai (Settings > Keys). It is needed even for the free tier,
because OpenRouter requires a key for `:free` models too. It may stay unfunded; whatever balance it
holds is spent by any call the donated key does not cover. That is `OPENROUTER_API_KEY`.

### 4. AskNews (10 min)

Metaculus partners with AskNews to give bots free news search. Sign up at <https://my.asknews.app>,
then create credentials at Settings > API credentials
(<https://my.asknews.app/en/settings/api-credentials>). They are `ASKNEWS_CLIENT_ID` and
`ASKNEWS_SECRET`. The Resources page's "Getting AskNews Setup" section has bot-maker specifics.

### 5. Paste the keys on Kira (5 min)

```bash
ssh -tt kira kira-secrets
```

Pick `metaculus-bot`, then paste `METACULUS_TOKEN`, `OPENROUTER_API_KEY`, `ASKNEWS_CLIENT_ID` and
`ASKNEWS_SECRET`. **The first two arm the tournament and MiniBench**, in the zero-credit mode. Then tell
@kira_earnings_bot `check metaculus`, or run `docker exec kira-earn kira-earn ps` on Kira and look for
`metaculus-bot` RUNNING with a summary that no longer says "not configured".

### 6. When the grant lands (5 min)

In `kira-secrets` set `OAI_ANTH_OPENROUTER_KEY` (the key Metaculus emails you), then set the mode
flags the grant affords (see "Which mode for which grant"). For example M2, paid forecasters and the
main web research, with MiniBench off:

| Variable | Value |
|---|---|
| `FORECASTER_FREE_TIER_ENABLED` | `false` |
| `NATIVE_SEARCH_ENABLED` | `true` |
| `GAP_FILL_V2_ENABLED` | `true` |
| `WORKFLOW_MINIBENCH_ENABLED` | `false` |

`SUPPORT_MODEL_ROUTE` stays `free` and `GAP_FILL_ENABLED` stays `false` in M2. Saving restarts the
program, which re-reads its env. Check the balance on OpenRouter's key page at any time.

### 7. Optional keys

| Key | Without it |
|---|---|
| `GEMINI_API_KEY` (aistudio.google.com > Get API key) | Gemini grounded search stays off; set `GEMINI_SEARCH_ENABLED=true` once the key exists |
| `EXA_API_KEY` | Gap-fill v2's web search tool reports "not configured" and fails soft. Not covered by donated credits |
| `PERPLEXITY_API_KEY` | That provider is skipped |
| `FRED_API_KEY` | FRED series are skipped; yfinance still runs |
| `SEC_EDGAR_CONTACT_EMAIL` | The EDGAR client declines and the ordinary page fetch takes over |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | Unused by the roster, which goes through OpenRouter |
| `MANTIC_TOKEN` | The mantic workflow stays idle (see the warning above before adding it) |

### 8. End-of-season bot-maker survey (after the season, about 15 min)

Not now. After the season's questions resolve, Metaculus emails the survey (0 to 2 weeks after), bot
makers have about 4 weeks to fill it in, and **prizes are paid only after all surveys are in**, via
Ramp. The page also warns that a prize bot may be asked to demonstrate itself live, to show there is
no human in the loop. What this repo can say in the survey:

- **Forecasters**: `openai/o3` (default effort), `anthropic/claude-sonnet-4.5` (effort high),
  `openai/gpt-5.6-sol` (effort high), via OpenRouter; the published forecast is the median of the
  three, with stacking off (the log line `Aggregation: conditional_stacking` is the configured strategy;
  see "Stacking is off in every mode"). Give the dates of any free-tier or cost-mode period, and that
  the forecasters were the three free models whenever `FORECASTER_FREE_TIER_ENABLED` was true.
- **Research**: AskNews, OpenAI native web search, Gemini grounded search, prediction-market
  snapshots (Polymarket, Kalshi, Manifold), FRED/yfinance, cited resolution sources, and two
  gap-fill passes, as the chosen mode allowed.
- **Code**: public at github.com/napzter13/metaculus-bot, a fork of No-Stream/metaculus-bot on
  `forecasting-tools`.

## Operating it

| Want | Do (as kira on the host) |
|---|---|
| status of every program | `docker exec kira-earn kira-earn ps` |
| this program's log | `docker exec kira-earn kira-earn logs metaculus-bot 100` |
| restart (re-reads the env) | `docker exec kira-earn kira-earn restart metaculus-bot` |
| deploy new code | `kira-earn-host update metaculus-bot` |
| one run's full output | `/var/lib/kira-earn/metaculus-bot/runs/<workflow>/<UTC stamp>.log` in the container |

Under `/var/lib/kira-earn/metaculus-bot/` (`$KIRA_EARN_DATA`): `status.json` (below), `state.json`
(the scheduler's memory of handled slots and totals), `heartbeat`, `runs/<workflow>/*.log`, and
`work/` (the bot's `run_logs/` and `research_outputs/`). Retention: run logs and scratch files 14
days; the research archive (`work/research_outputs/` and `raw_research_*.jsonl`, which the sync tools
read) 120 days, and if it passes 4 GiB the oldest files go first. Files the dashboard reads
(`status.json`, `state.json`, `heartbeat`, the logs) are mode 0640 and the directories 2750 (setgid,
so new files stay in group `kira-earn-read`), so that group can read them. A run's exit code 1 usually means the bot published and then reported degradation events
(the log says "Run completed with N alertable degradation event(s)"); a traceback is a real failure.

### Network blips (DNS or connection failures)

If the bot cannot reach Metaculus at all (a home-network reconnect, an ISP hiccup), it **skips** that
run instead of failing it: it logs one `TRANSIENT_NETWORK_SKIP` warning and exits 0, having spent
nothing, and the next slot tries again. This covers the identity preflight and the question fetch.
The preflight carries no credential, and the authenticated requests go only to the host it vetted,
over verified TLS, so a timeout there can follow a request Metaculus did receive; that is no leak.

Only a pure connectivity failure counts (DNS, refused or reset connection, connect or read timeout),
decided from the error itself and what it was raised `from`, never from what happened to be raised
while handling it. A TLS certificate failure, a wrong host answering, any HTTP error and any bug
still fail the run, because those mean something answered or something is broken. The bot's own
deadlines (the builtin `TimeoutError`) are not blips either.

**A blip is not an outcome.** It leaves `last_rc`, `error` and `last_finished` as the last real run
left them, so a 401 followed by a DNS blip is still a failing workflow. The blip is recorded beside
them: `transient`, `transient_streak` and `transient_note` on the workflow, and `transient: true` on
`last_run` when the most recent finished run was a blip.

The scheduler counts consecutive skipped slots per workflow (a real run of any kind ends the count; a
restart keeps it) and reports:

| Consecutive blips | `health` | Effect |
|---|---|---|
| 1 | `green` | nothing changes except the `transient` fields |
| 2 or 3 | `amber` | the summary starts `AMBER:` |
| 4 or more | `red` | the summary starts `RED: network blips x4; check the connection, or check the configured host (METACULUS_API_BASE_URL)`, and `last_run` shows that workflow with `ok: false`, which is what the dashboard's red rule reads |

At the tournament's 20-minute slots that is amber after about 20 to 40 minutes of outage and red after
about an hour. Red names the configured host because a flat outage and a wrong `METACULUS_API_BASE_URL`
look the same from here. A blip never advances `last_ok`, since no forecast was made.

### status.json

Written atomically at least every 30 seconds. It is one file with two views, the program contract's
and the dashboard's. Timestamps are UTC ISO-8601 strings in the first view and epoch seconds in the
second.

| Field | Meaning |
|---|---|
| `schema` | `1` |
| `updated_at` | ISO time of this write |
| `summary` | one line, at most 200 characters, for `kira-earn ps` and the dashboard |
| `configured` | true when `METACULUS_TOKEN` and `OPENROUTER_API_KEY` are both set |
| `missing` | the gate keys not yet set (names only) |
| `mode` | the seven cost flags as the tournament run sees them |
| `workflows.<tournament\|minibench\|mantic>` | `enabled`, `configured`, `next_slot`, `last_started`, `last_finished`, `last_rc`, `questions_forecast` and `spend_usd` (totals since the data dir began), `error`, `running`, plus `last_questions`, `last_spend_usd`, `degraded`, `transient` (the last run was a skipped blip), `transient_streak`, `transient_note` and `health` |
| `heartbeat` | epoch seconds of the last heartbeat write |
| `started` | epoch seconds the program process started |
| `last_run` | the most recently finished run: `kind`, `ok`, `finished` (epoch), `error`, plus `rc`, `degraded` and `transient`; `null` before any run. A workflow in a red blip streak is shown here in preference, so another workflow's clean run cannot hide it |
| `last_ok` | epoch seconds of the last run that exited 0 and was not a skipped network blip; `null` before one |
| `health` | `green`, `amber` or `red` (the worst enabled workflow), or `waiting` while keys are missing; amber and red come from blip streaks (see "Network blips") or a real failed last run |
| `transient_streak` | the longest current run of consecutive network-blip skips across the workflows |

`last_run.ok` is true only for exit code 0, so a run that published but reported degradation events is
`ok: false` with `degraded: true`. A run stopped by a restart is recorded under its workflow but not
as `last_run`, since a stop is not a failure. No secret value is ever written to this file.

## Cost and credits

All figures here are OpenRouter dollars per question, which is what donated credits pay for. They
were measured on 2026-09-28 from upstream's own run logs (No-Stream/metaculus-bot Actions
artifacts). The `CREDIT_ROLE_SPEND` ledger lines of every run from 2026-09-08 on that forecast
anything: 12 runs, 23 questions, Cup, Mantic and smoke tests. Each run's role rows sum to its own
`CREDIT_RUN_SUMMARY` total to the cent. **Our roster has never run**, so its forecaster figure is
priced from those runs' measured tokens. The inputs are thin (7 questions on today's support
models, 16 on the previous ones), so read every figure as plus or minus 25 percent.
Once your own bot has run, re-measure from a copy of its data dir. The sync tools read an artifact
store, and `scripts/import_kira_runs.py` fills it from Kira's run logs and research files:

```bash
docker cp kira-earn:/var/lib/kira-earn/metaculus-bot ./kira-data     # on Kira, then bring it here
uv run python scripts/import_kira_runs.py --data-dir ./kira-data
make sync_telemetry ARGS="--from-store"
make cost_report
```

All of it is free and read-only, and cost_report prints the same per-role dollars and tokens per
question. `make sync_all ARGS="--from-store"` folds in the research archive as well. Run the import at
least every 14 days: Kira prunes run logs at 14 days, and the store is the durable copy.

### Where the money goes today

| Role | $ per question | Can it move to a free or local model? |
|---|---|---|
| 3 forecasters (o3, sonnet-4.5 high, gpt-5.6-sol high) | **0.48** (0.09 + 0.16 + 0.23) | Only via the free-tier stopgap, at a quality cost |
| gap-fill v2 driver (agentic web research, ~11 to 19 calls) | 0.22 to 0.45 | No: needs web search and tool calls |
| gap-fill v1 resolver + analyzer | 0.35 to 0.57 | Resolver no (web search); the analyzer yes |
| native search | 0.13 to 0.22 | No: OpenRouter's native web search |
| text-only roles (summarizer, gap-fill analyzer, market ranker and query author, page digest, financial classifier, parser) | **0.07 to 0.12** | Yes: `SUPPORT_MODEL_ROUTE=free` |
| **Total, as shipped** | **1.44 to 1.59** | |

What that shows: the text-only support roles are about 5 percent of spend. Nearly all the money
is in the three forecasters and the three web-research roles, and no free or local model can do
web research. So "paid spend for the forecasters only" means turning web research off, not
re-routing it.

The forecaster figure is priced on two assumptions. gpt-5.6-sol at high is measured directly,
because upstream ran exactly that slot until 2026-09-22. o3 is priced at the gpt-5.6-sol slot's
tokens ($2 in, $8 out per million), and Sonnet-4.5 at the old Opus slot's tokens ($3 / $15),
which ran at effective effort high. Upstream's trio cost $0.64 on the same tokens.

Outside OpenRouter, so NOT paid from credits: Gemini grounded search and gap-fill v2's document
reads (your Google AI Studio key), Exa (gap-fill v2's search tool), and AskNews. Upstream measured
these at roughly $0.2 to $0.4 a question on its own accounts. README's "about $2.60 per question"
is upstream's all-in estimate for upstream's roster, not a measurement of this fork.

### The cost modes

Every mode is set through `kira-secrets` (pick `metaculus-bot`, set the variable), so switching
needs no commit. Saving restarts the program, and the new mode applies from the next run.

| Mode | Variables | $ / question | Tournament (300 to 500 q) | + MiniBench (about 420 q) | Questions on $100 | Questions on $1,500 | Quality |
|---|---|---|---|---|---|---|---|
| **M0 full** | defaults: every cost variable deleted | 1.44 to 1.59 | $430 to $800 | $1,040 to $1,460 | 63 to 69 | 940 to 1,040 | As designed |
| **M1 free text roles** | `SUPPORT_MODEL_ROUTE=free` | 1.37 to 1.48 | $410 to $740 | $990 to $1,360 | 68 to 73 | 1,010 to 1,090 | Summarizer, analyzer and ranker on a 31B free model. Every one fails soft |
| **M2 lean** | M1 + `GAP_FILL_ENABLED=false` | 0.94 to 1.06 | $280 to $530 | $680 to $980 | 95 to 106 | 1,420 to 1,590 | Drops gap-fill v1, which upstream kept on beside v2 only as an "overlap window". Unmeasured; `make strip_bench` (paid, about $1.25) measures it |
| **M3 forecasters only** | M2 + `GAP_FILL_V2_ENABLED=false` + `NATIVE_SEARCH_ENABLED=false` | 0.48 | $145 to $240 | $350 to $440 | ~208 | ~3,100 | Real loss: research falls back to AskNews, prediction markets, financial data and cited sources (plus Gemini grounded search if its key exists). Upstream's content audit found 54 percent of native search's content and 59 percent of gap-fill's appeared in no other source |
| **M4 empty wallet** (set now) | M3 + `FORECASTER_FREE_TIER_ENABLED=true` | 0.00 | $0 | $0 | unlimited | unlimited | Weakest. Free forecasters, within OpenRouter's daily free-request cap. Keeps the bot publishing |

Volumes are from the Resources page. The seasonal tournament has 300 to 500 questions over about
100 days (2026-09-28 to 2027-01-06). MiniBench runs back-to-back two-week rounds of about 60
questions, so about 420 over the same 14 weeks. Every mode is repo-wide: both workflows run in
whichever mode the variables say.

### Which mode for which grant

The grant is unknown until Metaculus answers the step 2 form. Upstream's $1,500 grant, cited in
docs/operations.md, was for upstream's bot, not this fork. **Plan for no top-ups.** The page
says to assume none will come.

Prize money per question differs about tenfold. The tournament pays about $50k over 300 to 500
questions, $100 to $170 of pool per question. MiniBench pays about $1k per 60-question round, about
$17. On any grant that cannot cover both, **spend credits on the tournament and disable
MiniBench** (set `WORKFLOW_MINIBENCH_ENABLED=false` in `kira-secrets`).

- **Small grant (about $100).** No paid mode covers a season. Disable MiniBench. Run the tournament
  in **M3**: about 208 questions of paid forecasters, 40 to 70 percent of the tournament. When the
  floor warns, switch to **M4** for the rest. The $15 floor already set is about 30 M3 questions,
  roughly a week of warning.
- **Medium grant ($300 to $600).** Disable MiniBench. Run the tournament in **M2** ($280 to $530),
  dropping to M3 if the balance runs ahead of the calendar.
- **Large grant ($1,500).** **M1** covers the tournament and MiniBench ($990 to $1,360). M0 does
  at the low end of the question range only. Set the floor back to 100.
- **Before any grant.** **M4**, which is what the variables say now.

Check the balance on OpenRouter's key page (or `make check_credits` locally with the keys in
`.env`). Divide the balance by the mode's price per question and compare it with the questions
left.

### When the credits run out mid-season

What the code does, in order:

1. **The balance falls below `OPENROUTER_CREDIT_FLOOR_USD`.** Runs still forecast and publish, then
   exit red. This is the early warning: step down a mode. Ask Metaculus for a top-up if you like,
   but the page says to assume none.
2. **The donated key is empty.** Every forecaster and research call it would have paid gets a
   credit error. The bot retries that call once on `OPENROUTER_API_KEY`. If that key has a
   balance, **it pays**, and each fallback is logged as `PAID PERSONAL-KEY FALLBACK` with the run
   ending red. Keep the personal key empty, or small, if you do not want this.
3. **Both keys are empty.** Research calls fail soft, so briefings get thinner. A question still
   publishes if at least one of the three forecasters answers; if none do, **that question gets
   no forecast**. Every run ends red.
4. **Recovery.** Runs skip only questions already forecast, so an open question missed now is
   retried by every later run while it stays open. Setting `FORECASTER_FREE_TIER_ENABLED=true`
   and `SUPPORT_MODEL_ROUTE=free` (M4), or restoring credits, recovers every still-open question
   with no commit. A question that closes while the bot is broke is lost for good. Upstream's
   notes put a tournament question's open window at about three hours. The Resources page says
   new participants "join in the middle of the leaderboard with a score of 0", which suggests a
   missed question simply adds nothing (an inference from that sentence, not a stated rule).

Nothing stops by itself, so a dry wallet means red runs until you switch mode or refill.

### Kira's own model: sized, not built

Routing text roles to Kira's local model (`primary` through LiteLLM) was sized and deliberately
not built. The bot now runs on Kira, so the reachability objection that applied on GitHub Actions
is gone: the manifest could set `litellm: true` and the container would get `LITELLM_BASE_URL`.
The case is still weak:

- **It saves about $0.06 a question, about $14 a season.** Only the summarizer and the gap-fill
  analyzer fit. The kira-linux engines runbook measures prefill at about 1,000 tokens a second on
  one queue with no prompt cache: about 6 s for the summarizer (6K-token prompt, 300 s wall) and
  about 10 s for the analyzer (10K, 120 s). The market ranker (35K-token prompt, 60 s wall) and
  the page digest (30 s wall) do not fit once two questions share the queue. The runbook's
  worst queue wait, 476 s, breaks every wall.
- **It competes with Kira's own bots for the one GPU queue**, and a stalled queue would stall a
  forecast against a one-hour question window.
- **`SUPPORT_MODEL_ROUTE=free` gets the same effect** on a free cloud model, at no GPU time.

## Tournament, MiniBench, Cup, Mantic, Market Pulse

- **Tournament** ($50k+, three seasons a year, 300 to 500 questions): `TOURNAMENT_ID` is
  `fall-futureeval-2026` (project 33121, forecasting ends 2027-01-06). No change is needed. Only
  the first bot per participant is prize-eligible; extra bot accounts are not.
- **MiniBench** (about $1k per two-week round of about 60 questions): runs `--mode minibench`
  against `MetaculusApi.CURRENT_MINIBENCH_ID` from `forecasting-tools`, so it follows the current
  round with no ID in this repo. Upstream kept it disabled; here it runs whenever the gate keys
  exist. Disable it on a small or medium grant ("Which mode for which grant"). If the library's
  pinned ID lags a new round, the fix is a `forecasting-tools` bump.
- **Metaculus Cup**: practice only; bots are not prize-eligible. Kira does not run it. Its Actions
  workflow is disabled and still carries its old `schedule:`, which cannot spend without GitHub
  secrets; leave it that way.
- **Mantic**: personal keys only, about $3 per question, and it ignores the mode flags; it stays
  idle without `MANTIC_TOKEN`.
- **Market Pulse** (about $7k, bot-eligible): bots update forecasts on numeric group questions
  throughout each question's life. Not supported. The repo has no run mode or workflow for it,
  only a slug probe in `scripts/probe_slugs.py`, and continuous updating is a different loop from
  this bot's forecast-once design. Adding it means a new mode and a new scheduler entry.

## Public-repo notes

- Runs no longer upload artifacts: logs and research stay on Kira, so nothing about a run is
  public except what the bot publishes to Metaculus. Older Actions artifacts, if any, were public.
- CI's `secret_scan` job runs gitleaks on every push. The full history (every commit on every
  branch) was scanned clean on 2026-09-28 with the repo's `.gitleaks.toml`; its allowlist forgives
  only named fixtures by path AND pattern.
- No secret belongs in this repo. On Kira the keys live in `/srv/kira-earn/env/metaculus-bot.env`.
  For local work copy `.env.template` to `.env` (gitignored); local run output (`run_logs/`,
  `research_outputs/`, `scratch/`) is gitignored too.
- The docs and Makefile still say `gh ... --repo No-Stream/metaculus-bot` in places. For this
  fork, read that as `napzter13/metaculus-bot`.

## The cost gate still applies

Once the gate keys exist, every scheduled run on Kira, every manual workflow dispatch, `make run` in
a live mode and every backtest spends and, for bot modes, publishes. AGENTS.md "Cost gate" lists them. The free gates are
`make lint`, `make lint_imports`, `make typecheck` and `make test`.
