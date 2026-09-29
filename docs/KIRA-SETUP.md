# Kira setup: the napzter13 fork in the fall 2026 season

The owner's checklist for running [napzter13/metaculus-bot](https://github.com/napzter13/metaculus-bot)
unattended on GitHub Actions in the Fall 2026 FutureEval bot tournament (project 33121, opened
2026-09-28) and MiniBench. The repo is public, for the open-source credit bonus. `origin` is the
fork, and `upstream` is No-Stream/metaculus-bot, kept for pulling fixes.

Facts about the competition were read on 2026-09-29 from the FutureEval participate page and the
[Resources page](https://www.metaculus.com/notebooks/38928/futureeval-resources-page/) (notebook
38928, last edited 2026-09-28). Where this doc and that page disagree, the page wins.

## Where things stand (no owner keys yet)

| Workflow | State | With no secrets |
|---|---|---|
| `ci.yaml` (lint, tests, gitleaks secret scan, osv audit) | active, every push to `main` | runs fully, green; needs no keys |
| `run_bot_on_tournament.yaml` | active, cron :03 / :23 / :43 hourly | green no-op: preflight finds no secrets, posts a notice, skips install and the bot step |
| `run_bot_on_minibench.yaml` | active, cron :08 / :38 hourly | green no-op, same gate |
| `run_bot_on_metaculus_cup.yaml` | **disabled** (2026-09-29): practice only, bots win no prizes, and it would spend credits | does not run |
| `run_bot_on_mantic.yaml` | active, cron :05 / :15 / :25 hourly | green no-op until `MANTIC_TOKEN` and `OPENROUTER_API_KEY` both exist |
| `test_bot.yaml`, `test_bot_basic.yaml` | manual only | green no-op, same gate |
| `fetch_diagnostic.yaml` | manual only | runs; holds no key by construction |
| `claude.yml` | an owner-written `@claude` mention | skipped for everyone else; needs `ANTHROPIC_API_KEY` to do anything |

**The gate.** Each bot workflow's first step, "Check deployment secrets", checks whether its
secrets exist: the Metaculus token plus at least one OpenRouter key (Mantic: `MANTIC_TOKEN` plus
`OPENROUTER_API_KEY`). If they don't, the run ends green with a "Bot not configured" notice, in
seconds: install and the bot step are skipped, the log upload finds nothing, and nothing can be
spent or published. **If they do, the run is live.** Adding the secrets is what switches a workflow
on; no YAML edit is needed.

**Repository variables already set (2026-09-29): the zero-credit posture, mode M4.** Until a grant
lands, the first armed runs forecast at $0 of OpenRouter spend:

| Variable | Value | Effect |
|---|---|---|
| `FORECASTER_FREE_TIER_ENABLED` | `true` | three `:free` forecasters and a free parser |
| `SUPPORT_MODEL_ROUTE` | `free` | text-only support roles on a `:free` model |
| `GAP_FILL_ENABLED`, `GAP_FILL_V2_ENABLED`, `NATIVE_SEARCH_ENABLED` | `false` | the three paid web-research roles off |
| `GEMINI_SEARCH_ENABLED` | `false` | no Google AI Studio key yet; on without one, it errors on every question |
| `OPENROUTER_CREDIT_FLOOR_USD` | `15` | early warning sized for a small grant, not upstream's $1,500 |

Research in this posture is AskNews (once its keys exist), prediction-market snapshots,
FRED/yfinance and cited resolution sources. Change the variables to the mode your grant affords
the day it lands (see "Cost and credits"). A variable takes effect at the next run, with no commit.

## What only the owner can do

Account creation, passwords, and pasting keys are yours. An agent is not allowed to create
accounts or enter credentials, even with permission. Run each `gh secret set` yourself: it
prompts for the value, so the key never appears in a terminal log or in this repo.

Times are hands-on estimates. Waiting on Metaculus is not included.

### 1. Metaculus bot account and METACULUS_TOKEN (about 10 min)

On metaculus.com, sign up as a human (or log in). Then **Settings > My Forecasting Bots > Create a
Bot**, enter the details, and copy the bot's API key. Then:

```bash
gh secret set METACULUS_TOKEN --repo napzter13/metaculus-bot
```

To see the bot's forecasts later: Settings > My Bots > "Switch to bot account", then open a
question. The bot's comment is under the "Private" comment tab; FutureEval makes them public at
intervals.

### 2. Participation and credit form (about 10 min, then wait)

One Google Form does both jobs: <https://forms.gle/aQdYMq9Pisrf1v7d8>. The first section is the
**required** participation form (three required questions); the rest is the LLM-credit
application. It asks about you, your motivation and your commercial status, so it is yours to
fill in. Worth knowing when you answer:

- Commercial bots (a for-profit with three or more people) get no credits and no prizes unless
  fully open-sourced. This repo is public and open-source.
- Credits cover OpenAI, Anthropic and Google through OpenRouter only. The roster (o3,
  claude-sonnet-4.5, gpt-5.6-sol) is inside that. The page encourages asking for SOTA models.
- The published norm is "$1-$1.5 per question" for competitive bots. This bot measures $1.44 to
  $1.59 in full mode, so size the request with "Cost and credits" below.
- **"If you run out, assume we won't be able to give you more credits."** Re-apply each season.
  Plan for no top-ups.

### 3. OpenRouter personal key (about 5 min)

Create an account and key at openrouter.ai. It is needed even for the free tier: OpenRouter
requires a key for `:free` models too. It can stay unfunded; whatever balance it holds is spent by
any call the donated key does not cover.

```bash
gh secret set OPENROUTER_API_KEY --repo napzter13/metaculus-bot
```

**Adding this secret together with step 1 arms the tournament and MiniBench workflows**, in the
zero-credit posture above.

### 4. AskNews (about 10 min)

Metaculus partners with AskNews to give bots free news search. Sign up at <https://my.asknews.app>,
then create credentials at Settings > API credentials
(<https://my.asknews.app/en/settings/api-credentials>). The Resources page's "Getting AskNews
Setup" section has any bot-maker specifics.

```bash
gh secret set ASKNEWS_CLIENT_ID --repo napzter13/metaculus-bot
gh secret set ASKNEWS_SECRET --repo napzter13/metaculus-bot
```

### 5. When the grant lands (about 5 min)

```bash
gh secret set OAI_ANTH_OPENROUTER_KEY --repo napzter13/metaculus-bot
```

Then pick the mode the grant affords (see "Which mode for which grant" below), for example M2:

```bash
R=napzter13/metaculus-bot
gh variable set FORECASTER_FREE_TIER_ENABLED --repo $R --body false
gh variable set NATIVE_SEARCH_ENABLED --repo $R --body true
gh variable set GAP_FILL_V2_ENABLED --repo $R --body true
# GAP_FILL_ENABLED stays false in M2; SUPPORT_MODEL_ROUTE stays free.
```

Check the balance on OpenRouter's key page at any time.

### 6. Optional keys

| Secret | Without it |
|---|---|
| `GEMINI_API_KEY` (Google AI Studio; mapped to `GOOGLE_API_KEY`) | Gemini grounded search stays off (`GEMINI_SEARCH_ENABLED=false`); set the variable to `true` once the key exists |
| `exa_key` | Gap-fill v2's web search tool reports "not configured" and fails soft. Exa is NOT covered by donated credits |
| `PERPLEXITY_API_KEY` | That provider is skipped |
| `FRED_API_KEY` | FRED series are skipped; yfinance still runs |
| `SEC_EDGAR_CONTACT_EMAIL` | The EDGAR client declines and the ordinary page fetch takes over |
| `ANTHROPIC_API_KEY` | `claude.yml` cannot run; the roster does not use it |
| `MANTIC_TOKEN` | The Mantic workflow stays gated |

Secrets never reach a fork's run: GitHub withholds them from workflows triggered by forks' pull
requests, and the secret-leakage audit noted in the workflows found no logger that emits a
credential.

### 7. Confirm the first armed run (about 10 min)

After the next cron minute, or after running `gh workflow run run_bot_on_tournament.yaml --repo
napzter13/metaculus-bot` (free in the M4 posture, a paid run once you switch mode), open the run:
"Check deployment secrets" shows no notice, "Run bot" runs, and the log lists the questions it
forecast. Check the bot's Metaculus profile (step 1) for the forecasts and private comments. Zero
open questions usually means the participation form (step 2) has not been processed.

GitHub drops many scheduled firings. Upstream compensated with an external cron-job.org dispatcher
(docs/operations.md "Scheduling reliability"), which belongs to the upstream account. Set up your
own only if runs show missed questions.

### 8. End-of-season bot-maker survey (after the season, about 15 min)

Not now. After the season's questions resolve, Metaculus emails the survey (0 to 2 weeks after),
bot makers have about 4 weeks to fill it in, and **prizes are paid only after all surveys are
in**, via Ramp. The page also warns that a prize bot may be asked to demonstrate itself live, to
show there is no human in the loop. What this repo can say in the survey:

- **Forecasters**: `openai/o3` (default effort), `anthropic/claude-sonnet-4.5` (effort high),
  `openai/gpt-5.6-sol` (effort high), via OpenRouter; the published forecast is the median of the
  three, with stacking off. Give the dates of any free-tier (`FORECASTER_FREE_TIER_ENABLED`) or
  cost-mode period, from the variables' history.
- **Research**: AskNews, OpenAI native web search, Gemini grounded search, prediction-market
  snapshots (Polymarket, Kalshi, Manifold), FRED/yfinance, cited resolution sources, and two
  gap-fill passes, as the chosen mode allowed.
- **Code**: public at github.com/napzter13/metaculus-bot, a fork of No-Stream/metaculus-bot on
  `forecasting-tools`.

## Cost and credits

All figures here are OpenRouter dollars per question, which is what donated credits pay for. They
were measured on 2026-09-28 from upstream's own run logs (No-Stream/metaculus-bot Actions
artifacts). The `CREDIT_ROLE_SPEND` ledger lines of every run from 2026-09-08 on that forecast
anything: 12 runs, 23 questions, Cup, Mantic and smoke tests. Each run's role rows sum to its own
`CREDIT_RUN_SUMMARY` total to the cent. **Our roster has never run**, so its forecaster figure is
priced from those runs' measured tokens. The inputs are thin (7 questions on today's support
models, 16 on the previous ones), so read every figure as plus or minus 25 percent.
Once your own bot has run, re-measure with `make sync_telemetry ARGS="--repo napzter13/metaculus-bot"`
then `make cost_report`. Both are free and read-only, and cost_report prints the same per-role
dollars and tokens per question.

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

Every mode is set with repository **variables** (Settings > Secrets and variables > Actions >
Variables), so switching needs no commit. Each takes effect at the next run.

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
MiniBench** (`gh workflow disable run_bot_on_minibench.yaml --repo napzter13/metaculus-bot`).

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
not built:

- **It only works if the bot runs on Kira.** `LITELLM_BASE_URL` is a private address, which GitHub's
  runners cannot reach. The bot would have to leave Actions for a cron on Kira, with the secrets
  held there.
- **It saves about $0.06 a question, about $14 a season.** Only the summarizer and the gap-fill
  analyzer fit. The kira-linux engines runbook measures prefill at about 1,000 tokens a second on
  one queue with no prompt cache: about 6 s for the summarizer (6K-token prompt, 300 s wall) and
  about 10 s for the analyzer (10K, 120 s). The market ranker (35K-token prompt, 60 s wall) and
  the page digest (30 s wall) do not fit once two questions share the queue. The runbook's
  worst queue wait, 476 s, breaks every wall.
- **`SUPPORT_MODEL_ROUTE=free` gets the same effect on Actions**, at no GPU time.

If the bot ever moves to Kira for other reasons, this is a small addition to the same builder.

## Tournament, MiniBench, Cup, Mantic, Market Pulse

- **Tournament** ($50k+, three seasons a year, 300 to 500 questions): `TOURNAMENT_ID` is
  `fall-futureeval-2026` (project 33121, forecasting ends 2027-01-06). No change is needed. Only
  the first bot per participant is prize-eligible; extra bot accounts are not.
- **MiniBench** (about $1k per two-week round of about 60 questions): runs `--mode minibench`
  against `MetaculusApi.CURRENT_MINIBENCH_ID` from `forecasting-tools`, so it follows the current
  round with no ID in this repo. Upstream kept it disabled; here it is enabled and arms with the
  secrets. Disable it on a small or medium grant ("Which mode for which grant"). If the library's
  pinned ID lags a new round, the fix is a `forecasting-tools` bump.
- **Metaculus Cup**: practice only; bots are not prize-eligible. **Disabled here on 2026-09-29.**
  Re-enable with `gh workflow enable run_bot_on_metaculus_cup.yaml --repo napzter13/metaculus-bot`
  if you want the human-comparison benchmark and have credits to spare.
- **Mantic**: personal keys only, about $3 per question; stays idle without `MANTIC_TOKEN`.
- **Market Pulse** (about $7k, bot-eligible): bots update forecasts on numeric group questions
  throughout each question's life. Not supported. The repo has no run mode or workflow for it,
  only a slug probe in `scripts/probe_slugs.py`, and continuous updating is a different loop from
  this bot's forecast-once design. Adding it means a new mode and a new paid cron.

## Public-repo notes

- Each bot run uploads its logs and research as a 90-day Actions artifact, readable by anyone.
  They hold question URLs, predictions and research text (already public on Metaculus), never keys.
- CI's `secret_scan` job runs gitleaks on every push. The full history (every commit on every
  branch) was scanned clean on 2026-09-28 with the repo's `.gitleaks.toml`; its allowlist forgives
  only named fixtures by path AND pattern.
- `.env` is gitignored; copy `.env.template` for local work. Local run output (`run_logs/`,
  `research_outputs/`, `scratch/`) is gitignored too.
- The docs and Makefile still say `gh ... --repo No-Stream/metaculus-bot` in places. For this
  fork, read that as `napzter13/metaculus-bot`.

## The cost gate still applies

Once secrets exist, every bot workflow firing, dispatch, `make run` in a live mode and backtest
spends and, for bot modes, publishes. AGENTS.md "Cost gate" lists them. The free gates are
`make lint`, `make lint_imports`, `make typecheck` and `make test`.
