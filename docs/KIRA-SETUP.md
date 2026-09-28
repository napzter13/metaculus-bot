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
