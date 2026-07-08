# Brooks Moore

I direct AI systems to build software I couldn't write by hand — and build the verification structure that makes that safe.

Sales consultant by day (PulteGroup, Chicago), Michigan Ross MM. Since March 2026 I've been running a small fleet of autonomous trading agents built entirely through multi-model AI orchestration: one model builds, a second audits independently, disagreements get arbitrated. Deterministic risk gates sit between every model and every broker. A read-only supervisory layer governs the fleet.

**The write-up worth reading:** [How I run a multi-model AI build process as a non-engineer](https://github.com/brooksmoore/ai-orchestration-case-study) — including how a pre-committed kill criterion retired one of my own strategies on a definitive 154-trade negative result, for under $5 of compute.

## The fleet

| Repo | What it is | Status |
|---|---|---|
| [multi-agent-llm-trading-platform](https://github.com/brooksmoore/multi-agent-llm-trading-platform) | Four Claude models, differentiated mandates, deterministic guardrail layer | Paper trading |
| [pure-arb-bot](https://github.com/brooksmoore/pure-arb-bot) | Cross-venue Kalshi/Polymarket structural arbitrage, 480+ tests | Paper, live-gated |
| [hood-ai-trading-agent](https://github.com/brooksmoore/hood-ai-trading-agent) | LLM reasoning on small-cap SEC filings, forward-only calibration gate | Paper, OOS clock running |
| [truleo-13f-mirror](https://github.com/brooksmoore/truleo-13f-mirror) | Deterministic 13F mirror-basket agent, ring-fenced account | Live since June 2026 |
| [kalshi-market-maker](https://github.com/brooksmoore/kalshi-market-maker) | Weather-market trader | Retired by pre-committed test |
| [bar-generator](https://github.com/brooksmoore/bar-generator) | Free web toy on Cloudflare Workers | Deployed |

📫 brcmoore@umich.edu · [LinkedIn](https://linkedin.com/in/brooks-moore)
