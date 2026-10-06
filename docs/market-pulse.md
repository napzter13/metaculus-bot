# Market Pulse: research, expected value and build size

Written 2026-10-06 from first-hand reads of the Metaculus API (the bot's own token, read-only GETs) and
the FutureEval pages. Nothing here is built. The live season is **Market Pulse Challenge 26Q4**
(project 33131), which opens **2026-10-10 21:00Z** and has no questions yet.

**Verdict: NO-GO to build now. GO for one free step first**: a $0 backtest of a model-free anchor on the
resolved Q2 and Q3 groups (section 7). Numbers: free-model bot EV about $7 a quarter, a full-coverage
average bot about $50, a strong paid bot about $100 for about $62 of credits, and a build of about
1,000 lines.

## 1. Rules for the season that is live (26Q4)

- **Prize pool $7,500**, 12 question groups with 65 sub-questions in total, all resolving by
  2026-12-31. The tournament runs 2026-10-10 to 2026-12-31 (forecasting end), project closes 2027-01-06.
- **Spot scoring.** "Only predictions at the close time of each question count for tournament
  standings", and the tournament score is the **sum of the spot scores** of the individual questions.
  If a question resolves early, the spot time is the last moment before resolution.
- **Bots may compete for prizes, once.** "You can only enter the competition once, either as a bot or as
  a normal user." The bot account is therefore the single entry; the owner must not also enter as a
  human. It is a solo entry, so our account qualifies.
- **What the page asks of bots** (Resources page): handle **numeric group questions** and
  "continuously update forecasts during the question lifetime (our normal FutureEval tournaments do not
  require updating)".
- **Not covered by the donated credits.** The credit offer is stated for the Seasonal Bot Tournament
  only. Market Pulse inference is the bot maker's own cost unless Metaculus says otherwise.

**Open risk (check on 2026-10-10).** The Q4 project record still carries launch defaults:
`score_type: peer_tournament`, `bot_leaderboard_status: exclude_and_show`, `visibility: unlisted`. Q2 and
Q3 read `spot_peer_tournament` and `include`. The Q4 description promises spot scoring and bot
eligibility, so this looks unconfigured, not decided, but if `exclude_and_show` persists at launch bots
would be shown and excluded from prizes, which makes the expected value zero.

## 2. How scoring treats updating

Spot scoring means **continuous updating earns nothing by itself**. Only the forecast standing at each
sub-question's close counts. Updating matters in two ways only: the forecast must exist at close (an
earlier forecast is the insurance), and a refresh shortly before close lets the forecast use the latest
market price. The best design is one early forecast plus one or two refreshes in the last hours.

Entries are scored per question, so **coverage is decisive**. In Q3, 18 of the 25 paid entries covered
nearly all 67 scored questions, and the median coverage of paid entries was the full 67. An entry with
low coverage scores 0 on the rest.

## 3. Schedule and question shape (from Q3, 12 weeks)

- Groups launch periodically, about 5 to 6 sub-questions a week (68 sub-questions were listed, 65
  announced and 67 scored). Each sub-question is open 4 to 15 days, 6 days for 43 of the 68, and
  **closes before the measurement period begins** (for example the VIX
  maximum for "Sep 7 to Sep 18" closed Sep 7 03:00Z). So each forecast is an ahead-of-time forecast of a
  market quantity, best informed by the current market price.
- The Q3 groups: first reported revenues and EPS of named companies (8 to 9 sub-questions each),
  returns of stocks, futures and gold relative to the S&P 500, the maximum intraday VIX, the ending UST
  10Y yield, the high-yield OAS, and NVIDIA guidance. Types are `numeric` and `discrete`, with
  open bounds, mostly 6 sub-questions per group.
- Typical forecasters make about 2 forecasts per question (30,114 forecasts, 214 forecasters, 65
  questions in Q3).

## 4. Who competes

| | 26Q2 | 26Q3 |
|---|---|---|
| Entrants | 189 | 216 |
| Bots | 100 | 140 |
| Humans | 89 | 76 |
| Paid entries | 26 | 25 |
| Paid bots (prize money) | 8 ($3,253) | 12 ($3,782) |
| Top prize | $1,030 (a bot) | $816 (a human) |

Bots are a growing majority (+40% in a quarter) and take about half the money: the Q3 average prize is
$27 per bot against $49 per human. Prizes run from $816 down to $51 over 25 places. The 12th paid place scored 726 and the 25th
scored 366, against a bot median of 63, a 75th percentile of 265 and a 90th percentile of 528, so the
last paid place is roughly the 80th percentile of bots.

## 5. API for the group questions

- List: `GET /api/posts/?tournaments=market-pulse-26q4&statuses=open&with_cp=true`. A group post carries
  `group_of_questions.questions[]`, each a normal question with `id`, `label`, `type`
  (`numeric` or `discrete`), `scaling` (`range_min`, `range_max`, `zero_point`), open bounds,
  `open_time`, `scheduled_close_time` and `my_forecasts`.
- The bot's existing fetch already unpacks sub-questions (`group_question_mode="unpack_subquestions"`),
  and its numeric and discrete CDF pipeline already forecasts them as independent questions.
- Forecast and update: the same `POST` the bot uses for numeric questions. Repeat forecasts are allowed;
  the standing one at close is what scores.
- The framework skips questions already forecast (`skip_previously_forecasted_questions`), which is
  exactly what a refresh policy has to turn off for this mode.

## 6. Expected value, per quarter

Base rate from Q3: 62 bots had near-full coverage; 7 of them (11%) were paid, averaging $442 when paid
($49.90 across all 62).

| Case | P(paid) | EV | Cost per quarter |
|---|---|---|---|
| Free-model ensemble (1 to 2 of 3 forecasters answer, rate-limited summarizer) | about 1.5% | **about $7** | $0 |
| Average full-coverage bot | 11% | about $50 | n/a |
| Strong paid ensemble (top quartile of bots) | 20 to 25% | about $90 to $110 | about $62 (M3, two passes) |

The probabilities for the free and paid cases are estimates, not measurements. The cost line is 65
sub-questions, 2 passes, $0.48 a forecast (the measured M3 forecaster price). At M2 research, about
$1.00 a forecast, it is about $130. A $100 grant also has to fund the tournament (about $45 a quarter at
its present pace), so Market Pulse at M3 does not fit a $100 grant with room to spare.

## 7. Build size, and the one free step first

Reusing the ensemble, free-tier models, the credit gate and the blip handling:

| Piece | Lines |
|---|---|
| `market_pulse` run mode, tournament constant re-pointed each quarter, date check | about 40 |
| Update policy: forecast if never forecast; refresh if close is near and the last forecast is old | about 120 |
| CLI wiring: bypass the already-forecast skip for this mode, cap questions per run | about 60 |
| `kira_scheduler` entry, env mapping, `run_bot_on_market_pulse.yaml` (dispatch-only clone with the Kira guard) | about 260 |
| Tests (policy, wiring, cadence, workflow set, drift) | about 450 |
| Docs | about 100 |

About **1,000 lines, about half a day**; the logic is about 250 of them. The credit gate, the blip
streak and the status fields come free from the shared paths. An optional research cache per group
(six sub-questions share one topic) would cut research cost about 80% for another half day.

**The free step:** the bot already has a model-free anchor (an empirical band from FRED and yfinance).
For market quantities a model-free forecast centred on the market price may reach the prize band with
no LLM at all, which would change the free-cost EV from about $7 to something worth building for. A
backtest of that anchor on the resolved Q2 and Q3 groups, scored against the final leaderboards, costs
$0 and about half a day, and turns the guess in section 6 into a number.

## Sources (read 2026-10-06)

- `GET /api/projects/tournaments/33131/` (26Q4), `/33066/` (26Q3), `/33013/` (26Q2): dates, prize pool,
  scoring type, bot status, counts, and the Q4 description with the spot-scoring and bot rules.
- `GET /api/leaderboards/project/33066/` and `/33013/`: entries, bot flags, scores, coverage, prizes.
- `GET /api/posts/44533/`: the 26Q3 announcement (rules, $7,500, 12 groups, 65 questions).
- `GET /api/posts/?tournaments=33066&with_cp=true`: the Q3 groups and sub-question fields.
- FutureEval Resources notebook 38928 (bots must handle numeric groups and update continuously) and
  the FutureEval participate page (Market Pulse listed as about $7k and bot-eligible).
- Cited by the announcement and not read: the spot-score FAQ,
  https://www.metaculus.com/help/scores-faq/#spot-score.
