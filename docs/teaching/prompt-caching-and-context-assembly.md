# Prompt Caching & the Cache-Aware Context Gap — A Teaching Document

*Built from your own frameworks (sourced via Psyche). Goal: understand why "context
reloading" is mostly a solved billing problem, why memory tools accidentally
un-solve it, and what the real gap is.*

## Frameworks used

| Framework | Source (your library) | Applied here as |
|---|---|---|
| First Principles Thinking | *The Great Mental Models*, Shane Parrish (p. 92) | What an LLM irreducibly must do every call |
| Metaphors / neural reuse | *Learn Like a Pro*, Oakley & Schewe (p. 55) | The amnesiac lawyer; the video-game save point |
| Structure Building | *Make It Stick*, Brown et al. (p. 175) | The three-part cost equation as the scaffold |
| Interleaving | *Make It Stick* (p. 65); *Learn Like a Pro* (p. 32) | mem0 lever vs caching lever, side by side |
| Inversion | *The Great Mental Models* (p. 167); Munger: "invert, always invert" | Design the *worst possible* context manager |
| Feynman Technique | *Learning How To Learn*, Ndlovu (p. 4) | Tiered teach-it-back prompts at the end |

---

## 1. GROUND — First principles

> "First principles thinking identifies the elements that are, in the context of
> any given situation, non-reducible." — Parrish

Strip everything away. What *must* happen on every LLM call?

1. **The model has zero memory.** Every API call starts from absolute scratch.
   "Chat memory" is an illusion created by your client re-sending the history.
2. **To write token N+1, the model must process tokens 1…N.** That's the
   transformer architecture (attention). Non-negotiable physics.
3. **Processing produces scratch work.** As the model reads your prompt, it
   computes intermediate matrices for every token — the **KV cache**. This
   scratch work is what's expensive to produce.

So the *only* two cost levers that can exist:

- **Lever A:** don't re-do scratch work that already exists → **prompt caching**
- **Lever B:** send fewer tokens in the first place → **memory / compaction / RAG (the mem0 family)**

Everything on the market is one of these two. There is no third lever.

## 2. CONNECT — Two metaphors

> "Metaphors take sets of links you've developed for one concept… and use them to
> begin more rapidly building a set of links for the new concept." — *Learn Like a Pro*

### The amnesiac lawyer

You pay a brilliant lawyer by the page. They have total amnesia: every meeting,
they must re-read the entire case file before discussing anything new. A
20-meeting case means page 1 gets read (and billed) 20 times.

**Prompt caching** = the lawyer keeps their *margin annotations* from last time.
If the file is **byte-for-byte identical** from page 1 onward, re-reading
annotated pages costs **10% of the normal rate**. Two catches:

- The annotations are keyed to exact page positions. **Insert one new page at
  page 3** and every annotation from page 3 onward misaligns — the lawyer
  re-reads (and re-bills at full rate) *everything after page 3*. Not just the
  new page. Everything after it.
- The lawyer **shreds the annotations if you don't return within ~5 minutes**
  (the cache TTL). Come back in 6 minutes → full price for the whole file.

### The video-game save point

A save point only helps if you replay the *exact same route*. Change anything
earlier in the run and the save is invalid — you replay from the divergence
point, not from where you wanted to be.

**Where both metaphors break** (the map is not the territory): the lawyer's
annotations contain *understanding*. The KV cache contains no understanding —
it's raw numeric scratch work tied to exact token positions and exact model
weights. That's why even a "harmless" one-word edit invalidates it, why you
can't download it to your PC (it's gigabytes, GPU-resident, useless without the
weights), and why it can't transfer between models.

## 3. BUILD — The cost equation (the structure to keep)

> "Distill the underlying principles; build the structure." — *Make It Stick*

Every turn of a conversation costs:

```
cost = (cached prefix × 0.1)  +  (everything after the first changed byte × 1.0)  +  (new tokens × 1.0)
       └── the discount ──┘      └────────── the silent killer ──────────┘
```

The prompt is matched **from the first byte forward**. The first position where
this turn's prompt differs from last turn's prompt is where the discount dies —
for *all* tokens after that point.

```
Turn N:    [system][tools][memory][msg1][msg2]...[msg40]            ← all cached, 0.1×
Turn N+1:  [system][tools][memory*][msg1][msg2]...[msg40][msg41]
                           ▲
                           memory block changed (new retrieved facts)
                           → msg1…msg41 ALL recompute at 1.0×
```

Concrete numbers: a 50,000-token session where a memory tool injects 200 fresh
tokens near the top each turn. The injection doesn't cost 200 tokens — it
flips ~49,000 tokens from 0.1× back to 1.0×, an effective **~44,000-token
penalty per turn**. Invisible on any dashboard. That's the trap.

## 4. CONTRAST — Interleave the two levers

> "Interleaving gives you practice in choosing the right set of links." — *Learn Like a Pro*

Same scenario — long coding session, agent needs your project conventions —
through both lenses:

| | **Lever B: memory layer (mem0 family)** | **Lever A: prompt caching** |
|---|---|---|
| Strategy | Send *less* (extract facts, drop history) | Re-send *cheaply* (identical prefix → 0.1×) |
| Best case | 90% fewer tokens sent | 90% discount on tokens sent |
| Fails when | retrieval misses → model "forgets" | any prefix edit, >5-min gap, reordered tools |
| Blind spot | **breaks the cache while saving tokens** | context still grows; doesn't persist across sessions |
| Failure mode is | visible (user notices forgetting) | **invisible (bill is silently 10× on the tail)** |

The punchline of interleaving these: **the two levers fight each other.**
Memory tools optimize Lever B while unknowingly sabotaging Lever A, because
they inject *changing* content into the *stable* part of the prompt. Nobody is
optimizing both simultaneously. That joint optimization is the gap —
**cache-aware context assembly**:

1. **Stable-prefix layout**: changing content (retrieved memories, new info)
   goes at the *end* of the prompt, never the beginning or middle.
2. **Append-only discipline**: never edit or reorder what was already sent.
3. **TTL keep-alive**: ping before the 5-minute shred.
4. **Measurement**: report cache-hit rate and $ lost to prefix breaks per session.

## 5. INVERT — Design the worst context manager

> "Avoiding stupidity is easier than seeking brilliance." — Parrish
> "Invert, always invert." — Jacobi, via Munger

To *maximize* your bill while sending the same tokens, you would:

| Sabotage | Real-world version of this bug |
|---|---|
| Put a timestamp in the system prompt | "Current time: 14:32:07" → breaks cache at byte ~20, every turn |
| Inject retrieved memories at the top | Default behavior of most memory/RAG integrations |
| Re-sort tool definitions per request | Serializing tools from an unordered dict/set |
| Summarize old messages in place | Rewrites the prefix → full reprocess of the rewritten span |
| Let sessions idle 6 minutes mid-task | TTL expiry → next message pays full price for everything |

Every row is a bug that exists in shipping products today. None shows up as an
error — only as a quietly 5–10× larger bill.

## 6. UNIFY — The mental model to carry

```
                 Is this content identical every turn?
                          │
              ┌─── yes ───┴─── no ───┐
              ▼                      ▼
     Put it EARLY in the      Put it at the END,
     prompt. Never touch      or accept paying 1.0×
     it again. It rides       for everything after it.
     at 0.1× forever.
              │
              ▼
     Keep the session warm (<5 min gaps).
     Append, never edit. Measure cache_read vs input tokens.
```

One sentence: **a prompt is an append-only log, not a document you edit — and
anything that violates append-only silently costs 10× on everything downstream.**

## 7. TEST — Feynman prompts

> "Explain it step-by-step, using analogies and simple language. If you encounter
> difficulty, return to the material." — your learning notes (Ndlovu)

- **Level 1 (define):** What is the KV cache, and why can't you store Claude's on your own PC?
- **Level 2 (compare):** mem0 saves 90% of tokens; caching discounts 90% of tokens. Why aren't these the same thing, and when does each fail?
- **Level 3 (apply):** Your agent injects "user preferences" into the system prompt each turn, refreshed from a database. Sessions are 60k tokens. Estimate the hidden cost and propose the fix.
- **Level 4 (second-order):** If providers extended cache TTL to 24 hours, which products in the memory-layer market lose their reason to exist, and which gap remains?

## 8. RETAIN — Spaced retrieval

| When | Action |
|---|---|
| Day 1 | Answer Level 1–2 prompts from memory, out loud |
| Day 3 | Redraw the cost equation and the decision tree without looking |
| Day 7 | Answer Level 3; explain the amnesiac lawyer to someone else |
| Day 14 | Answer Level 4; find one prefix-breaking bug in a real tool's source |
| Day 30 | **Build it**: the 200-line logging proxy — log `cache_read_input_tokens` vs `input_tokens` per call on your own workload and compute dollars lost to breaks |

> [!WARNING] Map ≠ territory, twice over
> 1. "90% savings" marketing (both from caching and from mem0) describes best
>    cases. Real savings depend on hit rates and retrieval quality. Measure.
> 2. Caching details differ per provider (Anthropic: explicit/automatic breakpoints,
>    ~5-min TTL, 0.1× reads with a 1.25× write surcharge; OpenAI: automatic,
>    0.5× reads; Gemini: explicit + storage fees). The *principles* above hold
>    everywhere; the numbers don't. Abstracting these differences is exactly
>    why a model-agnostic tool has room to exist.
