# Why Cached Input Costs 0.1× — The Mechanism, From First Principles

*A teaching document built from your own frameworks (sourced via Psyche). This is
the "under the hood" companion to `prompt-caching-and-context-assembly.md`. That
doc told you **what** to do (append-only, stable prefix). This one explains
**why** the physics forces it — so the rules stop being arbitrary and become
obvious.*

## Frameworks used

| Framework | Source (your library) | Applied here as |
|---|---|---|
| First Principles Thinking | *The Great Mental Models*, Parrish (p. 92) | What the model irreducibly recomputes per call |
| Metaphors / neural reuse | *Learn Like a Pro*, Oakley & Schewe (p. 55) | The chef's mise en place; the spreadsheet recalc |
| Structure Building | *Make It Stick*, Brown et al. (p. 168) | The two-phase cost model as the scaffold |
| Interleaving | *Make It Stick* (p. 293) | Cached-read vs fresh-input, same token, side by side |
| Map ≠ Territory | *The Great Mental Models*, Parrish (p. 49) | Where "0.1× discount" misleads |
| Feynman Technique | *Learning How To Learn*, Ndlovu (p. 4) | Tiered teach-it-back at the end |

---

## 1. GROUND — First principles: what *must* the model do?

> "First principles thinking identifies the elements that are, in the context of
> any given situation, non-reducible." — Parrish, p. 92

Forget pricing pages. Ask the irreducible question: **when the model reads your
prompt, what work physically happens, and which part is expensive?**

Strip it down with the Five Whys:

1. To produce the next token, the model runs your tokens through ~100 layers of
   matrix math (attention). *Why is that expensive?* →
2. Because **every token must "look at" every token before it** — that's what
   attention *is*. For token #50,000 to be understood in context, the model
   computes its relationship to all 49,999 before it. *Why does that matter for cost?* →
3. Because this produces, for every token, two big vectors — a **Key** and a
   **Value** — that summarize "what this token means in this context." Computing
   them is matrix multiplication across the whole network. *Why compute and keep them?* →
4. Because the **next** token will need to look back at all of them. So the model
   stores every token's K and V in a scratchpad: the **KV cache**. *Why is this the cost center?* →
5. Because **producing those K/V vectors is ~90% of the compute. Reading them
   back is nearly free.**

That's the whole secret, hit at the bottom of the Five Whys:

> **The expensive thing is not *storing* your tokens or *sending* them. It is
> the matrix math that turns each token into its K/V vectors. "Processing a
> prompt" *means* computing the KV cache for it.**

So the irreducible cost model has exactly two operations:

```
WRITE the KV cache (compute K,V for each token)   ← expensive  → priced ~1.0×
READ  an existing KV cache (reuse stored K,V)     ← cheap      → priced ~0.1×
```

Prompt caching is one sentence: **if the K/V vectors for a span of tokens were
already computed on a previous call and are still in GPU memory, skip the math
and reuse them.** You're billed for the read (~0.1×), not the write (~1.0×).
There is no magic — you're being charged less because the provider literally
does less work.

---

## 2. CONNECT — Two metaphors

> "Metaphors take sets of links you've developed for one concept… and use them to
> begin more rapidly building a set of links for the new concept." — *Learn Like a Pro*, p. 55

### The chef's mise en place

A restaurant order = your prompt. Before the chef can plate anything, they do
**prep**: chop the onions, make the stock, portion the proteins. Prep is the
slow, costly part; the final assembly is fast.

- **No caching:** every single order, the chef re-chops every onion from a whole
  onion, even if the last 40 orders used identical prep. They bill you for prep
  *and* plating every time. That's paying 1.0× to re-WRITE the KV cache.
- **Caching:** the chef keeps the prepped ingredients in labeled containers (the
  KV cache). The next identical order, they skip straight to plating. They bill
  you a tiny "fridge fee" (~0.1×) for the prep they reused.

**The catch that explains everything:** the containers are stacked in a line, in
order. The chef grabs them **front to back without re-reading the labels** —
they trust the order. So if you insert a *new* prep step in the middle, every
container behind it is now in the wrong position relative to the new recipe. The
chef can't trust the line anymore from that point on, so they **re-prep
everything from the insertion point to the end.** Not just the new step.
Everything after it.

### The spreadsheet recalc

A 50,000-row spreadsheet where each row's formula references every row above it.
Change a value in **row 3**, and the spreadsheet must recalculate rows 3 through
50,000 — because they all depend on what came before. Change **row 49,999** and
only two rows recalc. *Same one-cell edit. Wildly different cost — decided
entirely by how early the change was.* Hold onto that; it's the entire billing
story in section 4.

### Where both metaphors break (Map ≠ Territory)

The prepped ingredients and the spreadsheet cells both contain *meaning* a human
could read. The KV cache contains **no meaning you could inspect** — it's raw
numbers tied to exact token positions *and* the exact model weights. That's why
you can't download it, can't move it between models, and why even a one-character
edit upstream invalidates it. More on that boundary at the end.

---

## 3. BUILD — The structure to keep: two-phase cost

> "Extracting the salient ideas and constructing a coherent mental framework out
> of them… high structure-builders learn new material better." — *Make It Stick*, p. 168

Here is the scaffold. Every billed call splits your input tokens into two piles:

```
                 ┌─────────────────────────────────────────────┐
   YOUR PROMPT   │  [ matched prefix ]   [ everything after ]   │
                 └─────────────────────────────────────────────┘
                          │                       │
                  KV already on GPU?       must compute KV now
                          │                       │
                       READ (0.1×)            WRITE (1.0×)
```

- **Matched prefix** = the longest run of tokens, *from the very first token*,
  that is byte-for-byte identical to a previous call whose KV cache is still
  alive. These are READ. ~0.1×.
- **Everything after the first difference** = WRITE. ~1.0×. New tokens too.

Two non-negotiable rules fall out of this structure, and you now know *why*:

1. **Matching is from the first token, forward, and stops at the first mismatch.**
   (The chef grabs containers front-to-back and stops trusting the line at the
   first one out of place.) The cache cannot "skip over" a changed token and
   resume — position N's K/V depends on positions 1…N-1, so once one upstream
   token differs, every downstream vector is *a different number* and must be
   recomputed.
2. **The cache is volatile.** It's GPU memory, evicted after a TTL (Anthropic
   ~5 min). No live cache = the prefix is a WRITE again, full 1.0×.

---

## 4. CONTRAST — Interleave: the same token, two prices

> Learners who **contrasted two problems** were likelier to extract the general
> rule than those who studied one. — *Make It Stick*, p. 293

Take **one identical token** — say the word `"convention"` sitting at position
4,000 of your system prompt — and watch it get two different prices on two turns:

| | **Turn N** | **Turn N+1, nothing changed above it** | **Turn N+1, one word changed at position 50** |
|---|---|---|---|
| Is its KV already on GPU? | No (first time) | Yes | Yes, but now *invalid* |
| Does position 50 match? | — | Yes, prefix intact up to 4,000 | **No** — mismatch at 50 |
| Is `"convention"`'s KV still correct? | n/a | Yes — positions 1…3,999 unchanged | **No** — its KV depended on position 50, which moved |
| Price for this one token | 1.0× (WRITE) | **0.1× (READ)** | **1.0× (re-WRITE)** |

Look at the third column. **You did not touch the word `"convention"`.** It is
byte-identical. Yet it gets re-billed at full price — because a token *thousands
of positions upstream* changed, and this token's K/V vector was computed *in
relation to* that upstream token. Its meaning-in-context literally changed, so
its numbers changed, so it must be recomputed.

**This is the answer to your exact question** — "why is it getting charged 1×
instead of 0.1× if the model has a cache price for it?" The cache price *exists*,
but it only applies to tokens whose KV is **still valid**, and validity is
**positional and cumulative**: a token is cacheable only if *every token before
it* is also unchanged. The discount isn't a property of the token. It's a
property of the **unbroken prefix leading up to it.**

### The worked example (your "what's the real number" question)

A 50,000-token session. A memory tool injects 200 fresh tokens near the **top**
(position ~100) each turn. Anthropic numbers: cached read = 0.1×, write = 1.0×.

**Turn where the injection is stable (good):**
```
positions 1…49,800  identical to last turn   → READ  → 49,800 × 0.1 = 4,980 "effective"
positions 49,801…50,000  new this turn       → WRITE →    200 × 1.0 =   200
                                                          billed ≈  5,180 effective tokens
```

**Turn where the injection changed at position ~100 (bad):**
```
positions 1…99   identical                    → READ  →     99 × 0.1 ≈     10
positions 100…50,000  prefix broken here      → WRITE → 49,901 × 1.0 = 49,901
                                                          billed ≈ 49,911 effective tokens
```

**Same 50,000 tokens sent both times. ~5,180 vs ~49,911 — a ~9.6× swing**,
caused entirely by *where* the 200-token change landed. Move that injection to
the **end** and it costs 200 tokens of WRITE; put it near the **top** and it
costs ~44,000 tokens of re-WRITE. The injection's *size* is irrelevant; its
*position* is everything.

> This is precisely why Psyche's session-start block must be byte-stable: it sits
> near the top. One reordered fact there flips the whole session tail from 0.1×
> to 1.0×, invisibly.

---

## 5. BOUND — Map ≠ Territory: where "0.1× discount" misleads

> "The map of reality is not reality. Even the best maps are imperfect, because
> they are reductions of what they represent." — Parrish, p. 49

Four places the clean "cached = 0.1×" map diverges from the territory:

| The map says | The territory adds |
|---|---|
| "Cached reads are 0.1×." | **Only Anthropic.** OpenAI ≈ 0.5×, Gemini explicit + a storage fee. The *mechanism* is universal; the *number* is per-provider. "All models have a cache price" is true — but they are **different prices**, and none of them is your 0.1× unless you're on Anthropic. |
| "Caching saves money." | Anthropic **charges 1.25× to WRITE** a cache entry. If a prefix is written but reused fewer than ~2 times before the TTL evicts it, caching can cost *more*. It pays off only on **reuse**. |
| "The prefix is cached." | Only if you're back **within the ~5-min TTL**. Idle 6 minutes mid-task → the whole prefix is a fresh WRITE again. |
| "Cache is per-token." | Cache is **per-prefix**. A token's discount is hostage to every token before it. There is no such thing as caching one token in isolation. |

The deepest divergence, and the answer to "will it connect across models?": the
KV cache is **numbers tied to one model's exact weights**. Claude's KV vectors
are meaningless to GPT — different weights produce different numbers for the same
token. So a cache **cannot** be shared across models, or across providers, or
downloaded to you. It is per-model, per-prefix, per-5-minutes. Always.

---

## 6. UNIFY — The mental model to carry

```
        Cost of an input token  =  WRITE its KV (1.0×)  unless  it can READ an existing KV (0.1×)

        A token can READ (get the discount) ONLY IF
                          │
              every token BEFORE it is byte-identical to a previous call
                          │            AND
              that previous call's cache is still alive (< ~5 min)
                          │
        First upstream mismatch  ──►  discount dies for THAT token and ALL after it
```

One sentence: **the discount is not on the token — it's on the unbroken,
still-warm prefix in front of it; break the prefix early and you re-pay full
price for the entire tail, even for tokens you never touched.**

---

## 7. TEST — Feynman prompts

> "Explain it step-by-step using analogies and simple language; if you encounter
> difficulty, return to the material." — Ndlovu, p. 4

- **Level 1 (define):** In one sentence, what is the KV cache, and which is the
  expensive operation — writing it or reading it?
- **Level 2 (compare):** Two tokens are byte-identical across two turns. One is
  billed 0.1×, the other 1.0×. What single fact about their *neighbours*
  decides which is which?
- **Level 3 (apply):** Your agent injects a `"last updated: 14:32"` timestamp at
  the top of a 60k-token system prompt every turn. Walk through, in effective
  tokens, what that one line costs you per turn — and where to move it.
- **Level 4 (second-order):** A provider raises cache TTL from 5 min to 24 hours
  *and* drops the write surcharge to 1.0×. Which becomes the dominant cost-saving
  lever — caching or memory/compaction — and what new failure mode appears for
  long-lived caches?

## 8. RETAIN — Spaced retrieval

| When | Action |
|---|---|
| Day 1 | Answer L1–L2 out loud without looking. |
| Day 3 | Redraw the two-phase WRITE/READ diagram and the prefix-break rule from memory. |
| Day 7 | Answer L3 with real numbers; explain the chef metaphor to someone. |
| Day 14 | Answer L4; find one prefix-breaking line in a real prompt (yours or a tool's). |
| Day 30 | **Build it:** log `cache_read_input_tokens` vs `input_tokens` on a real workload and compute the dollars lost to one early-injection break. |

> [!WARNING] Map ≠ territory, twice
> 1. Every number here (0.1×, 1.25×, 5 min) is Anthropic's. The *mechanism*
>    (WRITE-once, READ-cheap, prefix-matched) holds everywhere; the *constants*
>    do not. Measure on your provider.
> 2. "Processing" and "sending" tokens are different things. You always *send*
>    all tokens over the wire. Caching changes only whether the provider
>    *recomputes* their KV — that's the cost, and that's all the discount touches.
</content>
</invoke>
