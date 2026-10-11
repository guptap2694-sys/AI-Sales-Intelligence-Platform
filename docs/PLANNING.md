# Planning: problem, users, use cases, scope

## The problem

A cybersecurity vendor's sales team faces thousands of companies and has capacity to work a few
hundred. The question is: **"Which ones need our services right now, and how do we reach them
first?"** Firmographic databases answer *who exists*. They don't answer *who is exposed, who is
obligated, and who is likely to buy this quarter*. Reps fall back on gut feel or alphabetical lists,
and sales leaders can't see or adjust the logic behind any ranking. Our prototype turns raw
firmographics into a **ranked, explained, filterable account list** with grounded first-touch outreach.

## Users and jobs-to-be-done

| User | Job to be done | What they need from us |
|---|---|---|
| **SDR / BDR** | "Fill my week with the accounts most likely to take a meeting, and give me a reason to call." | A prioritised list for their territory, the top 2–3 reasons per account, a persona to target, a draft opener |
| **Account Executive** | "Before a first meeting, understand why this account should care." | A one-paragraph account brief: obligations, exposure, timing |
| **Sales manager / RevOps** | "Point the team at the right segments, and trust the scoring enough to enforce it." | A tunable ICP (fit vs intent weights), segment/territory view, export to CRM, a transparent model |

## Use cases we chose (and why)

1. **"Monday morning: who do I call first in my territory?"** Filter by state, industry and size
   band, sorted by priority, with tier A/B/C/D. This is the core daily loop. Research shows trigger
   timing and speed of contact drive conversion (RESEARCH §1).
2. **"Why is this account hot?"** The account drawer shows the fit and intent breakdowns and every
   signal with the evidence that fired it. Reps ignore black-box scores. A score they can explain in
   one sentence is a score they will act on and pass on to the prospect.
3. **"Tune the ICP as a sales leader."** Sliders for fit and intent weights re-rank accounts live
   in the browser. Every platform we reviewed keeps fit and intent separate and expects teams to
   calibrate thresholds against their own outcomes. There is no universal setting.
4. **"Where are the under-served segments?"** A market heatmap of industry × size band (or state)
   showing account count and average priority. This supports territory planning and spotting
   segments such as mid-market health or SMB professional services below the security poverty line.
5. **"Draft first-touch outreach grounded in signals."** A persona-aware brief and email (Sonnet)
   for top-tier accounts only. Each draft must cite at least one real signal's evidence, so there
   are no invented facts. Buyers give vendors little time, so relevance has to come first.
6. **"Get it into my CRM."** CSV export of the filtered view with scores, tier, play, persona and
   top signals. This is the cheapest integration that fits every CRM.

## What we deliberately did NOT build

- **Live intent feeds (Bombora, G2, website visitors) and news/breach monitoring.** These are
  paid, licensed and real-time. We approximate intent with deterministic triggers from the
  dataset's fields. The `Signal` contract is source-agnostic, so a feed can be added later as
  another signal producer.
- **CRM sync (Salesforce/HubSpot write-back) and dedupe against existing pipeline.** This needs
  customer credentials and field mapping. CSV export covers the prototype.
- **Auth, multi-user, saved territories.** The app is a static single-file prototype, and nothing
  sensitive is stored.
- **Contact-level data and email sending or sequencing.** We recommend a persona. We don't scrape
  people or automate sends, which carries privacy and spam-law risk (Australian Spam Act).
- **A trained predictive model.** We have no closed-won/lost labels. Transparent rules plus an
  evaluated LLM classifier are the honest choice until outcome data exists.

## Rules vs LLM

Rules handle everything that must be **cheap, deterministic, auditable and run on every row**:
parsing, size bands, regulated-industry and tech-stack signals, fit and intent scores, tiers. These
run over millions of rows for close to $0, and every point traces back to a field. The LLM handles
what rules do poorly: **reading free-text descriptions** to judge real need (e.g. "clinic
management software" counts as handling health data even when the industry label says "Software").
That work goes to **Haiku**, only on the shortlist the rules produce, and is evaluated against
hand labels. **Sonnet** writes the brief and outreach for the few accounts a rep will actually
contact. The rules decide who reaches the shortlist, and the LLM adds nuance and words at the top.
This keeps cost bounded and means the system still runs with the LLM switched off.

## Data-dependent decisions (to confirm once dataset columns are profiled)

- Which growth, funding, job-posting and tech-stack fields exist. Signals marked † in RESEARCH §5
  are dropped or degraded gracefully if a field is absent.
- Whether revenue exists. If it doesn't, the A$3M Privacy Act threshold is proxied by employee count.
- Industry taxonomy (free-text vs ANZSIC/NAICS), which decides how `regulated_industry` is matched.
- Geographic mix (AU-only vs global), which decides whether NIS2/HIPAA hooks are worth showing.
- Description coverage, which decides how many accounts the LLM classifier can usefully score.

## Success metrics for a real rollout

- **Precision of tier A:** share of tier-A accounts that sales accepts as qualified. Target well
  above the base rate of an unranked list. Offline proxy: the "high"-class precision@k from our eval.
- **Meetings booked per 100 accounts worked**, tier A vs tier C, and vs a pre-rollout baseline.
  This is the primary lift metric.
- **Connect / reply rate** on signal-grounded outreach vs generic templates (A/B by rep or week).
- **Time to first touch** after a signal appears, and **rep adoption** (weekly active reps,
  accounts exported).
- **Pipeline and win rate by tier and play.** Over time this produces the labels needed to replace
  hand-set weights with learned ones.
- **Cost per qualified account** (LLM spend / tier-A accounts), from the tracing logs.
