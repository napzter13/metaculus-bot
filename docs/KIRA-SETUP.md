# Kira setup: the napzter13 fork in the fall 2026 season

The owner's checklist for running [napzter13/metaculus-bot](https://github.com/napzter13/metaculus-bot)
unattended on GitHub Actions in the Fall 2026 FutureEval bot tournament (project 33121, opened
2026-09-28) and MiniBench. The repo is public, for the open-source credit bonus. `origin` is the
fork, and `upstream` is No-Stream/metaculus-bot, kept for pulling fixes.

Metaculus returns HTTP 403 to automated fetches of metaculus.com/futureeval/participate, so the
page itself was never read for this doc. Wherever a form's name or fields are marked *confirm on
the page*, the page is the authority and this doc is only a pointer.

## Where things stand (no owner keys yet)

| Workflow | Trigger | With no secrets |
|---|---|---|
| `ci.yaml` (lint, tests, gitleaks secret scan, osv audit) | every push to `main` | runs fully, green; needs no keys |
| `run_bot_on_tournament.yaml` | cron :03 / :23 / :43 hourly | green no-op: preflight finds no secrets, posts a notice, skips install and the bot step |
| `run_bot_on_minibench.yaml` | cron :08 / :38 hourly | green no-op, same gate |
| `run_bot_on_metaculus_cup.yaml` | cron :13 / :33 / :53 hourly | green no-op, same gate |
| `run_bot_on_mantic.yaml` | cron :05 / :15 / :25 hourly | green no-op until `MANTIC_TOKEN` and `OPENROUTER_API_KEY` both exist |
| `test_bot.yaml`, `test_bot_basic.yaml` | manual only | green no-op, same gate |
| `fetch_diagnostic.yaml` | manual only | runs; holds no key by construction |
| `claude.yml` | an `@claude` mention by the repo owner | skipped for everyone else; needs `ANTHROPIC_API_KEY` to do anything |

**The gate.** Each bot workflow's first step, "Check deployment secrets", checks whether its
secrets exist: the Metaculus token plus at least one OpenRouter key (Mantic: `MANTIC_TOKEN` plus
`OPENROUTER_API_KEY`). If they don't, the run ends green with a "Bot not configured" notice, in
seconds: install and the bot step are skipped, the log upload finds nothing, and nothing can be
spent or published. **If they do, the run is live.** Adding the secrets
is what switches a workflow on; no YAML edit is needed. So decide which workflows you want
**before** step 6.

## Owner steps, in order

Times are hands-on estimates. Waiting on Metaculus approvals is not included.

### 1. Metaculus bot account and METACULUS_TOKEN (about 10 min)

At <https://www.metaculus.com/futureeval/participate>, create the bot account. It is a separate
Metaculus account flagged as a bot (*confirm on the page*: username rules, and whether it must be
linked to your personal account). While logged in as the bot, copy its API token. That is
`METACULUS_TOKEN`. Every mode reads questions and publishes with it.

### 2. Season participation form (about 5 min)

Register the bot for the Fall 2026 season on the same page (*confirm on the page*). Enter
FutureEval and MiniBench. The bot forecasts only questions its account can see, so a missing
registration shows up as armed runs that find zero open questions. Mention that the code is public
at the repo URL above if the form asks, since that is the open-source bonus.

### 3. LLM credit form (about 10 min, then wait for approval)

Apply for donated LLM credits on the same page (*confirm on the page*). If granted, Metaculus
issues an OpenRouter key; it becomes the secret `OAI_ANTH_OPENROUTER_KEY`. It serves OpenAI,
Anthropic and Google slugs, which covers the whole paid roster (`openai/o3`,
`anthropic/claude-sonnet-4.5`, `openai/gpt-5.6-sol`).

### 4. OpenRouter personal key (about 5 min)

Create an account and API key at openrouter.ai; it becomes `OPENROUTER_API_KEY`. It is needed even
for the free tier, because OpenRouter requires a key for `:free` models too. The key can stay
unfunded. Whatever balance it holds is spent by any call the donated key does not cover.

### 5. AskNews (about 10 min)

Sign up through the free-access route for FutureEval bot makers linked from the participate page
(*confirm on the page*). Create API credentials: `ASKNEWS_CLIENT_ID` and `ASKNEWS_SECRET`. AskNews
is the primary news provider, and the workflows already throttle it to one call at a time.

### 6. Choose the workflows, then add the secrets (about 10 min)

First, disable any bot workflow you do NOT want live, because the secrets arm all of them at once:

```bash
gh workflow disable run_bot_on_metaculus_cup.yaml --repo napzter13/metaculus-bot   # if skipping the Cup
gh workflow disable run_bot_on_minibench.yaml --repo napzter13/metaculus-bot       # if skipping MiniBench
```

If credits have not landed yet, do step 7 now as well, before any secret exists.

Then add the secrets under **Settings > Secrets and variables > Actions > Secrets**. The names are
exact, including the lowercase `exa_key`.

| Secret | Status | Without it |
|---|---|---|
| `METACULUS_TOKEN` | **mandatory** | Gate stays closed; nothing runs |
| `OPENROUTER_API_KEY` | **mandatory** | Gate stays closed unless the donated key exists; the free tier cannot run |
| `OAI_ANTH_OPENROUTER_KEY` | once granted | Every paid call bills `OPENROUTER_API_KEY` |
| `ASKNEWS_CLIENT_ID`, `ASKNEWS_SECRET` | strongly recommended | The primary news provider falls back to another provider or none |
| `GEMINI_API_KEY` (Google AI Studio; the workflows map it to `GOOGLE_API_KEY`) | optional | The Gemini grounded-search provider errors on every question. The forecast still publishes, but logs fill with errors and runs may end red. Either add the key or set `GEMINI_SEARCH_ENABLED: 'false'` in the workflows |
| `exa_key` | optional | Gap-fill v2's web search tool reports "not configured" and fails soft |
| `PERPLEXITY_API_KEY` | optional | That provider is skipped |
| `FRED_API_KEY` | optional | FRED series are skipped; yfinance still runs |
| `SEC_EDGAR_CONTACT_EMAIL` | optional (a real email, sent as the SEC User-Agent) | The EDGAR client declines and the ordinary page fetch takes over |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | optional | Unused by the roster, which goes through OpenRouter. `ANTHROPIC_API_KEY` also powers `claude.yml` |
| `MANTIC_TOKEN` | only for Mantic | Mantic gate stays closed |

Secrets never reach a fork's or a stranger's run: GitHub withholds them from workflows triggered by
forks' pull requests, and the secret-leakage audit noted in the workflows found no logger that emits a credential.

### 7. Free tier until credits land (about 2 min)

Under **Settings > Secrets and variables > Actions > Variables** (a variable, not a secret), set
`FORECASTER_FREE_TIER_ENABLED` = `true`.

This swaps the three forecasters and the parser to OpenRouter `:free` models; it is still a median
of three. It is a stopgap: the models are weaker, rate-limited, and capped per day by OpenRouter
(*confirm on openrouter.ai* for the current cap). Research and support calls (native search, the
AskNews summarizer, gap-fill) stay on paid models. On an unfunded key they fail soft, so the
forecast publishes on thinner research, and the run may end red on those degradation alerts.
**Delete the variable or set it to `false` the day the credit grant lands.** Details are in
docs/constants.md "forecaster_free_tier_enabled".
For a zero-credit posture that also stops the paid research calls, see mode M4 under "Cost and
credits" below. For a small grant, also set `OPENROUTER_CREDIT_FLOOR_USD` now (same section).

### 8. Confirm the first armed run (about 10 min)

After the next cron minute (or run `gh workflow run run_bot_on_tournament.yaml --repo
napzter13/metaculus-bot`, which spends like any live run), open the run. The "Check deployment
secrets" step shows no notice, "Run bot" runs, and the log lists the questions it forecast. On the
bot account's Metaculus profile, check that the forecasts and comments appear. If the step reports
zero open questions, recheck step 2.

GitHub drops many scheduled firings. Upstream compensated with an external cron-job.org dispatcher
(docs/operations.md "Scheduling reliability"), which belongs to the upstream account. Set up your
own only if runs show missed questions.

### 9. Per-season bot-maker survey (about 15 min)

Metaculus asks bot makers for a survey each season (*confirm on the page*: timing, and whether it
is a condition of prize eligibility). What this repo can say:

- **Forecasters**: `openai/o3` (default effort), `anthropic/claude-sonnet-4.5` (effort high),
  `openai/gpt-5.6-sol` (effort high), via OpenRouter; the published forecast is the median of the
  three, with stacking off. If the free tier was on for part of the season, give the dates.
- **Research**: AskNews, OpenAI native web search, Gemini grounded search, prediction-market
  snapshots (Polymarket, Kalshi, Manifold), FRED/yfinance, cited resolution sources, two gap-fill
  passes.
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

| Mode | Variables | $ / question | 256-question season | Questions on $100 | Questions on $1,500 | Quality |
|---|---|---|---|---|---|---|
| **M0 full** | none (defaults) | 1.44 to 1.59 | $370 to $410 | 63 to 69 | 940 to 1,040 | As designed |
| **M1 free text roles** | `SUPPORT_MODEL_ROUTE=free` | 1.37 to 1.48 | $350 to $380 | 68 to 73 | 1,010 to 1,090 | Summarizer, analyzer and ranker on a 31B free model. Every one fails soft. Saves only $17 to $30 a season |
| **M2 lean** | M1 + `GAP_FILL_ENABLED=false` | 0.94 to 1.06 | $240 to $270 | 95 to 106 | 1,420 to 1,590 | Drops gap-fill v1, which upstream kept on beside v2 only as an "overlap window". Unmeasured; `make strip_bench` (paid, about $1.25) measures it |
| **M3 forecasters only** | M2 + `GAP_FILL_V2_ENABLED=false` + `NATIVE_SEARCH_ENABLED=false` | 0.48 | $125 | ~208 | ~3,100 | Real loss: research falls back to AskNews, Gemini grounded search, prediction markets, financial data and cited sources. Upstream's content audit found 54 percent of native search's content and 59 percent of gap-fill's appeared in no other source |
| **M4 empty wallet** | M3 + `FORECASTER_FREE_TIER_ENABLED=true` | 0.00 | $0 | unlimited | unlimited | Weakest. Free forecasters (see step 7), within OpenRouter's daily free-request cap. Keeps the bot publishing |

A 256-question tournament runs about 100 days (2026-09-28 to 2027-01-06), roughly 2.6 questions a
day. MiniBench and the Cup add their own questions at the same per-question price.

### Which mode for which grant

The grant is unknown until Metaculus answers the step 3 form. Upstream's $1,500 grant, cited in
docs/operations.md, was for upstream's bot, not this fork.

- **Small grant (about $100, then top-ups).** Start in **M2**. $100 covers about 100 questions,
  roughly the first five weeks. A 256-question season in M2 needs about $150 to $170 more in
  top-ups. If a top-up is refused, go to M3 (about $0.48 a question, 208 questions per $100), and
  go to M4 only once nothing is left. Set `OPENROUTER_CREDIT_FLOOR_USD=20`, about a week of M2
  runway. Otherwise the upstream default of $100 turns every run red from day one.
- **Large grant ($400 or more).** **M0** fits the tournament ($370 to $410). $1,500 covers the
  tournament, MiniBench and the Cup in M0. Keep the $100 floor.
- **Before any grant.** Use M4, or M3 with `FORECASTER_FREE_TIER_ENABLED=true`, on an unfunded
  `OPENROUTER_API_KEY`.

A budget that assumes the donated credits cover all inference holds only when the grant is at
least the season figure for the chosen mode.

### When the credits run out mid-season

What the code does, in order:

1. **The balance falls below `OPENROUTER_CREDIT_FLOOR_USD`.** Runs still forecast and publish, then
   exit red. This is the early warning: ask Metaculus for a top-up, or step down a mode.
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
   notes put a tournament question's open window at about three hours; *confirm on the page*
   how the leaderboard counts a missed question.

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

## Minibench, Cup, Mantic, Market Pulse

- **MiniBench** runs `--mode minibench` against `MetaculusApi.CURRENT_MINIBENCH_ID` from
  `forecasting-tools`, so it follows the current MiniBench with no ID in this repo. Upstream kept
  it disabled; here it arms with the secrets unless disabled in step 6. If the library's pinned ID
  lags a new MiniBench, the fix is a `forecasting-tools` bump.
- **Tournament**: `TOURNAMENT_ID` is `fall-futureeval-2026` (project 33121, forecasting ends
  2027-01-06). No change is needed.
- **Metaculus Cup**: human-plus-bot; bots are shown but kept off the human leaderboard.
- **Mantic**: personal keys only, about $3 per question; stays idle without `MANTIC_TOKEN`.
- **Market Pulse**: not supported. The repo has no run mode or workflow for it, only a slug probe
  in `scripts/probe_slugs.py`. Adding it means a new mode and a new paid cron.

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
