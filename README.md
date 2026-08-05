# Brooks Moore

I'm a sales consultant who builds software by directing AI models, and who spends most of that time verifying their work carefully enough to trust it.

Since March 2026 I've run a small fleet of autonomous trading agents, built almost entirely through multi-model orchestration. One model writes the code. A different model audits it, and isn't allowed to edit the tests it's being judged against. When the two disagree, a measurement settles it rather than my preference. Deterministic risk checks sit between every model and every broker, and a read-only supervisory layer watches the fleet as a whole.

Everything runs on paper. No live capital is at risk today.

Getting models to write code turned out to be the easy part. The harder problem, and the one I find genuinely interesting, is how often confident output is quietly wrong. A test that passes because it can't fail. A metric that only moves in one direction. A gate that skips instead of failing and reports green. Most of my engineering effort now goes into catching that class of problem, because it doesn't announce itself.

## Public work

| Project | What it is |
|---|---|
| [LoopEngineering](https://github.com/brooksmoore/LoopEngineering) | How I run a multi-model build process without being able to read the code fluently, and the verification playbook behind it |
| [aissistant](https://github.com/brooksmoore/aissistant) | Personal AI assistant on Telegram with a Claude brain. Local-first, multi-instance, guarded against its own hallucinations. Two daily users since July 2026 |
| [Quotable](https://github.com/brooksmoore/Quotable) | AI caption generator. Cloudflare Worker with a KV cache and a provider-agnostic LLM layer |
| [TunnelPong](https://github.com/brooksmoore/TunnelPong) | 2.5D tunnel Pong for iOS, built in SpriteKit |

The trading repositories are private. They hold strategy research that would lose its value if I published it.

## An example of the method

In June I built a weather-market trading strategy on Kalshi and wrote down what would kill it before I ran it: a metric, a sample size, and a threshold, all fixed in advance.

It hit that threshold. Net of fees, the edge was negative across 154 settled trades, and the strategy was retired on the criterion I'd committed to rather than on a judgment call. Total compute cost of finding out: under five dollars.

That result is in the fleet's graveyard alongside ten others, each with the reason it died. Being able to end things cheaply and definitively has been worth more to me than any single strategy.

## How I work

**Nothing is trusted on one model's say-so.** Writer and auditor are always different models, and a "done, tests pass" report is treated as a claim, not a result, until it's independently re-run.

**Tests have to be able to fail.** A test that can't fail against broken code isn't coverage, it's decoration. Every new check is shown red before it's shown green.

**Kill criteria are written before the window opens.** Deciding what counts as failure after seeing the data isn't a decision, it's a rationalization.

**Paper trading uses the same code path as live**, differing only at order submission. A strategy validated against mock prices has validated the plumbing, not the idea.

---

brcmoore@umich.edu · [LinkedIn](https://linkedin.com/in/brooks-moore) · Michigan Ross MM · Chicago
