## Implementation

- `naive_keyword_detector`: a flat blocklist, matched case-insensitively anywhere in the text. Included as a baseline specifically to be measured against, not as a strawman dismissed in prose — it genuinely does catch every true injection in this exercise's corpus; its problem is entirely on the false-positive side.
- `InjectionDetector`: six independent, individually-weighted regex signals (`_SIGNAL_PATTERNS`), summed into a score and compared against a threshold (default `1.0`). Two signals are weighted `1.0` (flag alone): `role_marker_impersonation` (a line starting with `System:`/`Assistant:`/`Developer:`, anchored to line-start so "the system: a distributed database" doesn't trip it) and `canonical_override_phrase` (the "ignore/disregard/forget ... previous/prior/above ... instructions" shape). The other four are weighted `0.5`-`0.8` and need to combine with something else to cross the threshold — this asymmetry is deliberate: the two severe signals are specific enough to rarely occur in benign text at all, while the weaker ones (an email address appearing near "send", generic AI-directed language) are individually common enough in ordinary text that requiring combination is what keeps the false-positive rate down.
- `quarantine`: splits flagged text into sentences, redacts only the ones matching a signal that fired, leaves the rest untouched. Verified directly (`test_quarantine_preserves_unrelated_sentences`) that the real weather forecast survives being next to an injected sentence that doesn't.
- `VulnerableAgent`: not an LLM — a single regex (`_ACTION_RE`) that extracts a `send/email/forward/transmit ... to <valid-email>` pattern from whatever text it's handed and treats it as something to act on. This is a deliberate simplification of a real model's behavior, chosen because it's the simplest thing that's *honestly vulnerable* to the exact documented risk (acting on instructions found in tool output) without needing an actual LLM call to demonstrate it.
- `run_agent_turn`: the one function that connects detection to behavior — `defend=True` routes the tool result through `InjectionDetector.scan` and `quarantine` *before* `VulnerableAgent` ever sees it. This is the thing actually being measured in this exercise, more than the detector's score in isolation.
- Not built: a real embedding-based or LLM-based classifier (deliberately scoped to pattern-based detection, matching this repo's "no external model" convention for exercises like this), detection of injected content that isn't text-based (e.g. steganographic/invisible-character tricks), or a defended agent that's also robust to *novel* phrasings outside the signal patterns (see Key Learnings for what this scoping choice costs).

## Complexity

Not an algorithmic-complexity exercise (`InjectionDetector.scan` is O(signals × text length), a handful of regex passes) — the thing measured here is detection and defense *effectiveness*, not asymptotic cost.

- **Recall**: both the naive detector and the multi-signal detector flag **3/3** true injections.
- **False-positive rate on the 6-entry adversarial-benign corpus**: naive detector **5/6** (83%); multi-signal detector **1/6** (17%) — the one false positive is the known hard case (see Key Learnings), not a count that was tuned away.
- **End-to-end agent behavior**, all 3 true injections: undefended `VulnerableAgent` complies with the embedded directive (calls `send_email` with the attacker's address) in **3/3** cases; defended, it complies in **0/3**.
- **Randomized robustness**: 30 template-generated injection variants (random override phrasing, random target address/domain), checked end-to-end — all 30 are flagged, all 30 cause compliance when undefended, all 30 are refused when defended.

## Follow-up Questions

**Why?**
Any agent that can fetch external content (a webpage, a search result, a document, an email) and feed it back into its own context is exposed to indirect prompt injection the moment that content isn't fully trusted — which is nearly always, since the whole point of fetching it is that the agent didn't write it. This is a live, documented class of attack against real tool-calling LLM systems, not a hypothetical.

**How exactly?**
An attacker plants text shaped like an instruction somewhere a tool will retrieve it from. If the model (or, in this simulation, `VulnerableAgent`'s simple pattern match) treats everything in its context as equally authoritative, the planted instruction gets the same weight as the user's actual request. Detection scores how instruction-shaped and override-flavored a piece of text is; quarantine removes exactly the part that scored that way before the model/agent ever reads it.

**Which algorithm?**
Not a named algorithm — a hand-built multi-signal heuristic classifier (weighted regex signals, summed and thresholded), the kind of thing a real system would pair with (not replace by) a trained classifier or the model's own instruction-hierarchy training. Chosen here specifically because it's auditable: every flag traces to a named, inspectable signal, not a black-box score.

**Which library?**
Standard library only — `re`. No ML library, which is itself the point: this measures what pattern-based detection alone can and can't do, with no "a real classifier would have caught it" escape hatch for the corpus actually tested.

**What happens internally?**
Each signal is a compiled regex checked independently; the score is just a sum of weights for whichever ones matched, and `flagged` is a single threshold comparison. There's no interaction between signals beyond that sum — two weak signals matching is treated identically to any other combination that reaches the same total, which is simple but means the detector has no notion of "these two signals together are especially suspicious" beyond their weights adding up.

**How is it implemented?**
See [Implementation](#implementation).

**What if this fails?**
- **Benign text containing exactly one of the detector's trigger words in an innocuous context** ("ignore the banner above"): doesn't flag, by design — no single weak signal alone, and no role-marker/override-phrase match, keeps the score under threshold. Verified across 5 of the 6 benign corpus entries.
- **An article that quotes a real injection example for educational purposes**: this one *does* flag, and is documented as a real, unfixed limitation (`test_multi_signal_detector_does_flag_the_known_hard_case`) rather than a case papered over — a pattern matcher cannot distinguish "this is an attack" from "this is a sentence *about* an attack," because they can be textually identical.
- **An injected directive that targets something other than an email address** ("send the report to the shared drive"): `VulnerableAgent`'s own action regex requires an email-shaped target, so this specific phrasing wouldn't trigger compliance in the first place (verified directly) — a narrower, more honest claim than "catches every possible directive," and a real scope boundary of this toy agent, not of the detector.
- **Many different phrasings of the same override intent**: the 30-seed randomized sweep varies the override phrase, the target username, and the domain — all 30 are caught, which is evidence the signals generalize past the three hand-written examples, though not proof against phrasings outside the three `_OVERRIDE_TEMPLATES` tried.

**What trade-offs did you consider?**
- **Weighted, combinable signals (chosen) vs. a single big regex trying to match "the whole attack shape" at once:** combinable signals let two individually-common things (an email address near "send"; generic AI-directed language) require each other to trigger a flag, which is exactly what keeps the false-positive rate low without also gutting recall — a single all-in-one regex would either be too narrow (misses rephrased attacks) or too broad (matches the same things the naive detector does).
- **Sentence-level quarantine (chosen) vs. flag-the-whole-result-and-discard-it:** discarding the entire tool result on any flag would also throw away legitimate information sitting right next to the injected sentence (the real weather forecast, in the example) — sentence-level redaction keeps the defense's collateral damage local to the actually-suspicious text.
- **A toy regex-based "vulnerable agent" (chosen) vs. wiring this up to a real LLM API:** a real LLM call would make the end-to-end demonstration non-deterministic and dependent on a specific model's training, which would undermine the exact thing this exercise is trying to show reliably and reproducibly: that quarantining changes the outcome. The regex-based agent is a simplification, but an honest one — it's vulnerable for the same structural reason a real model would be (no distinction between "instruction" and "data" once something is in its context), not for an unrelated, toy-specific reason.

**How do you debug it?**
- `DetectionResult.signals` lists exactly which named signals fired — the first thing to check for both a false positive (which signal shouldn't have matched?) and a false negative (which signal was needed but didn't fire, or fired too weakly alone?).
- For a quarantine bug, compare `quarantine(text, result)` against the original sentence-by-sentence — a sentence that should have been redacted but wasn't points at the regex itself; a sentence that got redacted but shouldn't have been points at a signal pattern that's matching too broadly.
- For an end-to-end bug, `VulnerableAgent().decide_next_action(quarantine(text, InjectionDetector().scan(text)))` run by hand isolates whether a defense failure is in detection (never flagged), quarantine (flagged but the action text survived redaction), or the agent's own extraction logic.

**How do you evaluate it?**
- **67 tests, 5/5 consecutive clean runs.**
- **Mutation check, 5 targeted mutations, each applied and reverted in isolation with `PYTHONDONTWRITEBYTECODE=1` set for every invocation** (see 2026-10-11's first problem today for why this specific environment variable, not just clearing the local `__pycache__`, is now required):
  - `DetectionResult.flagged` threshold check `>=` → `>`: 2/67 failed.
  - `quarantine` stops redacting matched sentences (loop body replaced with a no-op): 37/67 failed.
  - Role-marker regex loses its line-start anchor: 1/67 failed.
  - `VulnerableAgent`'s action regex loosened from requiring an email address to accepting any token after "to": **initially 0/67 failed — a real coverage gap, not a benign mutation** (see Key Learnings).
  - Signal score collapsed from a weighted sum to a flat 0/1 (any signal vs. none): 1/67 failed.

## Key Learnings

- **A mutation that broadens a regex can be invisible if nothing in the corpus happens to need the narrower version — found and fixed the same way as 2026-10-11's first problem today, same day, different file.** Loosening `VulnerableAgent`'s action-target pattern from "must be a valid email address" to "any non-whitespace token" passed all 67 tests on the first run, because every single `"... to X"` phrase in the entire corpus (hand-written and randomly generated) happens to use a real email address as `X`. The mutation is genuinely wrong — it would make the toy agent treat `"send the report to the shared drive"` as an actionable email target — but nothing exercised that distinction. Verified the gap directly (confirmed the correct code already returns `None` on that exact phrase) before adding `test_vulnerable_agent_does_not_treat_a_non_email_target_as_an_action`, which now fails correctly on the mutant. Two gaps of this identical shape in one day's two problems is itself worth noting: a regex (or any pattern) being *checked for a specific property* needs a test that specifically varies that property, not just more examples that happen to always satisfy it anyway.
- **An honest detector reports its own known failure mode instead of tuning the corpus around it.** The "article that quotes an injection example for educational purposes" entry was added to the benign corpus expecting it might be a problem, and it is one: pattern-based detection cannot distinguish an attack from a sentence *describing* that attack, because the override phrase itself is the same text either way. Rather than removing this entry (which would make the false-positive-rate number look better while hiding a real, structural limitation) or trying to special-case it (which would just be fitting the detector to one example, not fixing the underlying ambiguity), it's kept in the corpus and asserted as an *expected* flag (`test_multi_signal_detector_does_flag_the_known_hard_case`), with the measured false-positive rate (1/6) reported as what it actually is, not rounded down to a cleaner-looking zero.
