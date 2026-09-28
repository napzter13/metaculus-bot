# Kira setup: getting this fork into the fall 2026 season

The owner's checklist for running `napzter13/metaculus-bot` unattended on GitHub Actions in the
Fall 2026 FutureEval bot tournament (project 33121, opens 2026-09-28), plus MiniBench. Do the steps
in order. Step 1 matters most, because the other steps assume nothing has fired yet.

Metaculus did not serve metaculus.com/futureeval/participate to the session that wrote this (HTTP
403 to automated fetches). Wherever a form name or field is marked *confirm on the page*, the page
is the authority and this doc is a pointer.

## 1. Create the repo with Actions OFF, then push

This clone came from `No-Stream/metaculus-bot` and its remote is now named `upstream`. A new
repository does NOT inherit the upstream's per-workflow enabled/disabled state. GitHub starts a
scheduled workflow as soon as its file is on the default branch, so on the first push all four
schedules go live together:

| Workflow | Cron (UTC) | Mode | Spends and publishes |
|---|---|---|---|
| `run_bot_on_tournament.yaml` | :03 / :23 / :43 hourly | `tournament` | yes |
| `run_bot_on_minibench.yaml` | :08 / :38 hourly | `minibench` | yes |
| `run_bot_on_metaculus_cup.yaml` | :13 / :33 / :53 hourly | `metaculus_cup` | yes |
| `run_bot_on_mantic.yaml` | :05 / :15 / :25 hourly | `mantic` | yes, on personal keys |

MiniBench was `disabled_manually` upstream by the upstream operator's choice. In your repo it
starts enabled unless you disable it.

1. Create an empty `napzter13/metaculus-bot` on GitHub, with no README, so the push is clean.
2. Before pushing: **Settings > Actions > General > Actions permissions > Disable actions.** Or push
   first and immediately run `gh workflow disable <file> --repo napzter13/metaculus-bot` for each
   workflow above. Disabling first is the safe order.
3. Push from the Kira clone:

   ```bash
   git remote add origin https://github.com/napzter13/metaculus-bot.git
   git push -u origin main
   ```

4. The docs and Makefile still say `gh ... --repo No-Stream/metaculus-bot`. For your repo, read
   that as `napzter13/metaculus-bot`.

## 2. Metaculus bot account and METACULUS_TOKEN

At <https://www.metaculus.com/futureeval/participate>, create the bot account. It is a separate
Metaculus account flagged as a bot (*confirm on the page*: username rules and whether the account
must be linked to your personal one). From the bot account, copy its API token. That is
`METACULUS_TOKEN`. It is the only secret without which nothing works: every mode reads questions
and publishes with it.

## 3. Season participation form

On the same page, register the bot for the Fall 2026 season (*confirm on the page*: the form
covers the bot account, contact email and the tournaments entered). Enter FutureEval and MiniBench.
The bot forecasts only questions it can see, so a missing registration shows up as runs that find
zero open questions.

## 4. LLM credit form

Apply for donated LLM credits on the same page (*confirm on the page*). If granted, Metaculus
issues an OpenRouter key. Store it as `OAI_ANTH_OPENROUTER_KEY`. It serves OpenAI, Anthropic and
Google slugs, which covers the whole paid roster (`openai/o3`, `anthropic/claude-sonnet-4.5`,
`openai/gpt-5.6-sol`). Any other vendor answers 404 on it and falls back to `OPENROUTER_API_KEY`.

Until the grant lands, use step 7.

## 5. AskNews

Sign up for AskNews through the free-access route for FutureEval bot makers linked from the
participate page (*confirm on the page*). Create API credentials there. They are
`ASKNEWS_CLIENT_ID` and `ASKNEWS_SECRET`. AskNews is the primary news provider, and the workflows
already throttle it to one concurrent call at 0.2 requests per second.

## 6. Repository secrets

**Settings > Secrets and variables > Actions > Secrets.** The names are exact, including the
lowercase `exa_key`.

| Secret | Status | Without it |
|---|---|---|
| `METACULUS_TOKEN` | **mandatory** | Nothing runs |
| `OPENROUTER_API_KEY` | **mandatory** | No forecaster has a key when the donated one is absent. The free tier needs it too, even unfunded |
| `OAI_ANTH_OPENROUTER_KEY` | mandatory once granted | Every paid call bills `OPENROUTER_API_KEY` |
| `ASKNEWS_CLIENT_ID`, `ASKNEWS_SECRET` | strongly recommended | The primary news provider falls back to another provider or none |
| `GEMINI_API_KEY` (a Google AI Studio key; the workflows map it to `GOOGLE_API_KEY`) | optional | The Gemini grounded-search provider errors on every question. The question still publishes, but logs fill with errors and the run may exit red. Either add the key or set `GEMINI_SEARCH_ENABLED: 'false'` in the workflows. The url_context fetch rung skips quietly |
| `exa_key` | optional | Gap-fill v2's web search tool reports "not configured". Fails soft |
| `PERPLEXITY_API_KEY` | optional | That provider is skipped |
| `FRED_API_KEY` | optional | FRED series fetches are skipped. yfinance still runs |
| `SEC_EDGAR_CONTACT_EMAIL` | optional (any real email) | The EDGAR client declines and the page fetch takes over |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | optional | Unused by the current roster, which goes through OpenRouter |
| `MANTIC_TOKEN` | only for the Mantic workflow | `--mode mantic` fails at startup |

The tournament, MiniBench and cup workflows read the same set. Native search, gap-fill, the
page-digest extractor, the AskNews summarizer and the prediction-market ranker are LLM calls
through OpenRouter. They bill the same keys as the forecasters and fail soft when those keys have
no credit.

## 7. Free-tier stopgap until credits land

**Settings > Secrets and variables > Actions > Variables** (a variable, not a secret): set
`FORECASTER_FREE_TIER_ENABLED` = `true`.

This swaps the three forecasters and the parser to OpenRouter `:free` models. The median of three
is kept. The bot can then publish at zero LLM cost with an unfunded `OPENROUTER_API_KEY`. It is a
stopgap: the models are weaker, rate-limited, and capped per day by OpenRouter (the cap is higher
once the account has ever bought credits; *confirm on openrouter.ai*). Research and support calls
stay paid and fail soft. **Delete the variable or set it to `false` the day the credit grant
lands.** Details are in docs/constants.md "forecaster_free_tier_enabled".

## 8. Enable the workflows

Re-enable Actions (step 1). Then enable exactly the workflows you want:

- **Tournament**: yes. `TOURNAMENT_ID` is already `fall-futureeval-2026` (project 33121,
  forecasting ends 2027-01-06), so no code change is needed.
- **MiniBench**: yes, if entering it. The workflow runs `--mode minibench` against
  `MetaculusApi.CURRENT_MINIBENCH_ID` from `forecasting-tools`, so it follows the current MiniBench
  with no ID in this repo. Enabling it is only the Actions UI toggle (or
  `gh workflow enable run_bot_on_minibench.yaml --repo napzter13/metaculus-bot`), plus the same
  secrets as the tournament. If the library's pinned ID ever lags a new MiniBench, the fix is a
  `forecasting-tools` bump, not a constant here.
- **Metaculus Cup**: optional. A human-plus-bot cup where bots are shown but excluded from the
  human leaderboard.
- **Mantic**: leave it disabled unless you have a `MANTIC_TOKEN` and want to spend personal keys
  (about $3 per question).
- **Market Pulse**: not supported. The repo has no run mode or workflow for it, only a slug probe
  in `scripts/probe_slugs.py`. Adding it means a new mode plus a new paid cron, which is a
  separate, deliberate change.

GitHub drops many scheduled firings. Upstream used an external cron-job.org dispatcher for that
(docs/operations.md "Scheduling reliability"). That dispatcher belongs to the upstream account, so
set up your own only if your runs show missed questions.

## 9. Per-season bot-maker survey

Metaculus asks bot makers to fill in a survey each season (*confirm on the page*: timing, and
whether it is a condition of prize eligibility). The answers this repo can give:

- **Forecasters**: `openai/o3` (default effort), `anthropic/claude-sonnet-4.5` (effort high),
  `openai/gpt-5.6-sol` (effort high), via OpenRouter. The final forecast is the median of the
  three. Stacking is off.
- **Research**: AskNews, OpenAI native web search, Gemini grounded search, prediction-market
  snapshots (Polymarket, Kalshi, Manifold), FRED/yfinance, cited resolution sources, and two
  gap-fill passes.
- **Code**: a fork of No-Stream/metaculus-bot on `forecasting-tools`.
- If the free-tier flag was on for part of the season, say so and give the dates.

## The cost gate still applies

Every workflow firing, `make run` in any live mode, backtest and dispatch spends money and, for
bot modes, publishes. AGENTS.md "Cost gate" lists them. The free gates are `make lint`,
`make lint_imports`, `make typecheck` and `make test`.
