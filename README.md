# Daily Code Lab

A daily engineering practice repository. Two coding problems, solved and documented, every day — building depth in Python, backend engineering, data structures & algorithms, system design, and GenAI / Agentic AI engineering for high-level software and AI engineering interviews.

## The Rule

**Exactly 2 problems per day. No exceptions, no skipped days without a logged reason.**

- Quality over quantity — each problem is solved, tested, and documented before moving on.
- Consistency compounds. Two problems a day, done daily, produces a large and well-reasoned body of work over months.
- If a day is missed, it is logged in the [progress tracker](#progress-tracker), not silently skipped.

## Learning Goals

- Build fluency in Python idioms, standard library, and performance-aware code.
- Develop pattern recognition across core DSA problem classes (arrays, graphs, DP, trees, etc.).
- Practice backend engineering fundamentals: APIs, concurrency, data modeling, distributed systems basics.
- Build system design judgment through recurring design exercises and trade-off analysis.
- Apply GenAI/Agentic AI engineering concepts: LLM tool use, agent architectures, RAG, evaluation.
- Produce a durable, searchable reference of solved problems with reasoning, not just code.

## Problem Categories

| Category | Focus |
|---|---|
| `dsa` | Arrays, strings, trees, graphs, DP, greedy, heaps, backtracking |
| `python` | Language internals, idioms, standard library, performance |
| `backend` | APIs, databases, concurrency, caching, messaging, auth |
| `system-design` | Scalability, distributed systems, trade-off write-ups |
| `genai-agentic` | LLM tool use, agent design, RAG, prompt/context engineering, evals |

## Repository Structure

Organized by day, with each day containing two independently solved problems.

```
daily-code-lab/
├── README.md
├── 2026-08-12/
│   ├── problem-1-<slug>/
│   │   ├── problem.md
│   │   ├── solution.py
│   │   └── notes.md
│   └── problem-2-<slug>/
│       ├── problem.md
│       ├── solution.py
│       └── notes.md
├── 2026-08-13/
│   ├── problem-1-<slug>/
│   └── problem-2-<slug>/
└── ...
```

- Day folders are named `YYYY-MM-DD`.
- Each problem folder is named `problem-N-<short-slug>` (e.g. `problem-1-two-sum`).
- `problem.md` and `notes.md` together follow the [problem format](#problem-format) below.
- Solutions are written in the language most relevant to the category (Python by default, unless the problem specifically calls for another stack).

## Problem Format

Every problem is documented consistently across two files:

**`problem.md`**
1. **Problem** — statement, constraints, and category tag.
2. **Approach** — reasoning before code: brute force, then optimization path.

**`notes.md`**
3. **Implementation** — key design decisions, edge cases handled.
4. **Complexity** — time and space complexity, with justification.
5. **Follow-up Questions** — variations, scaling considerations, interview-style extensions.
6. **Key Learnings** — what was new, what was reinforced, mistakes made and corrected.

This format is non-negotiable — it's what turns solved problems into a reusable reference instead of a pile of scripts.

## Progress Tracker

| Date | Problem 1 | Problem 2 | Category | Status |
|---|---|---|---|---|
| YYYY-MM-DD | — | — | — | — |

Update this table on the same day a problem is solved. `Status` is one of: `done`, `in-progress`, `skipped (reason)`.

## Guidelines for Consistency

- Commit daily. Each day's work should land in version control the day it's done, not batched later.
- No copy-pasted solutions without independent implementation and understanding — the goal is retained skill, not a checked box.
- Revisit weak categories deliberately rather than defaulting to comfortable ones.
- If a problem is reattempted later (e.g. after learning a better approach), add a new dated entry rather than editing history — the tracker should reflect actual progress over time.
- Keep write-ups terse and technical. No filler, no motivational language — this is a working reference, not a journal.
