# GenAI / Agentic Engineering Roadmap

A staged topic list for the `genai-agentic` category, kept separate from the
day-by-day [Progress Tracker](README.md#progress-tracker). Use it to pick the
next `genai-agentic` day instead of reaching for whatever's top of mind: find
an unchecked sub-topic in the earliest stage that still has one, or deliberately
jump ahead if a later stage is more relevant to current work.

Checking a sub-topic off means a day's problem exercised it directly and
measurably — not that it was mentioned in passing while solving something
else. "Touched by" notes below mark **partial** overlap from existing days;
they are not checked off on that basis alone.

## Stage 1: Context Engineering & Token Budgets

- [x] Context compression / overflow prevention — [2026-10-24](2026-10-24/problem.md)
- [ ] Dynamic context assembly
- [x] Token allocation strategies across competing context needs — [2026-11-18](2026-11-18/problem.md)
- [ ] System prompt architecture
- [ ] Pre-flight routing (deciding what goes in context before the call is made)

Touched by: [2026-09-17 (2nd)](2026-09-17b/problem.md) (rolling summarizer — overlaps compression), [2026-10-24](2026-10-24/problem.md) (truncation strategies, checked above), [2026-11-18](2026-11-18/problem.md) (allocation across competing pools, checked above).

## Stage 2: Advanced RAG & Hybrid Search

- [ ] BM25 + dense vector hybrid scoring
- [ ] ColBERT late-interaction retrieval
- [ ] Cross-encoder reranking
- [ ] Metadata filtering
- [ ] Contextual retrieval (Anthropic-style chunk-context prepending)

Touched by: [2026-09-26](2026-09-26/problem.md) (RAG retrieval pipeline + eval harness — baseline retrieval, not hybrid/reranked).

## Stage 3: GraphRAG & Knowledge Extraction

- [ ] Entity resolution
- [ ] Relationship mapping
- [ ] Community summaries
- [ ] Multi-hop graph traversal
- [ ] Neo4j / FalkorDB-backed storage
- [ ] Unstructured-to-graph extraction pipelines

Touched by: none yet.

## Stage 4: Agentic RAG & Self-Correction

- [ ] Query rewriting
- [ ] Multi-step retrieval
- [ ] Citation grounding
- [ ] Abstention logic (knowing when not to answer)
- [ ] Hallucination detection
- [ ] Self-reflective search loops

Touched by: none yet.

## Stage 5: Durable Agent Orchestration

- [ ] State machines for agent control flow
- [ ] LangGraph-style graph orchestration
- [ ] Temporal.io-style durable execution
- [ ] Checkpointing
- [ ] Crash recovery
- [ ] Human-in-the-loop (HITL) gates
- [ ] Cyclic workflows

Touched by: [2026-10-23](2026-10-23/problem.md) (circuit breaker — state machine, but not agent orchestration specifically).

## Stage 6: Tool Execution & MCP (Model Context Protocol)

- [ ] JSON-RPC
- [ ] Strict Pydantic schemas for tool I/O
- [ ] E2B sandboxing
- [ ] Least-privilege tool scoping
- [ ] Dynamic tool discovery
- [ ] Raw MCP server implementation

Touched by: [2026-09-16](2026-09-16/problem.md) (tool-calling agent loop from scratch — tool execution without MCP's wire protocol).

## Stage 7: 3-Tier Memory Systems

- [ ] Working scratchpads
- [ ] Episodic session compression
- [ ] Semantic vector recall
- [ ] Automated TTL eviction
- [ ] Importance scoring

Touched by: [2026-09-17 (2nd)](2026-09-17b/problem.md) (rolling conversation-history summarizer), [2026-10-24](2026-10-24/problem.md) (importance-weighted retention — overlaps importance scoring).

## Stage 8: Multi-Agent Swarms & A2A Handoffs

- [ ] Agent-to-Agent (A2A) protocols
- [ ] Typed state passing between agents
- [ ] Supervisor topologies
- [ ] Consensus voting
- [ ] Conflict resolution
- [ ] Inter-agent trust boundaries

Touched by: none yet.

## Stage 9: RAG & Trajectory Evals

- [ ] Hit rate, MRR, NDCG
- [ ] Step-level trajectory grading
- [ ] Golden DAGs
- [ ] LLM-as-a-judge calibration
- [ ] CI regression gates

Touched by: [2026-09-26](2026-09-26/problem.md) (RAG retrieval pipeline + eval harness — retrieval metrics, not trajectory grading).

## Stage 10: Security & Guardrails

- [x] Indirect prompt injection defense — [2026-10-11 (2nd)](2026-10-11b/problem.md)
- [ ] PII UUID masking
- [ ] Inter-agent sanitization
- [ ] Grammar-constrained outputs
- [ ] Citation spoofing detection

Touched by: [2026-10-11 (2nd)](2026-10-11b/problem.md), checked above.

## Stage 11: Model Routing & FinOps

- [ ] System-One/System-Two triage routing
- [x] Semantic caching — [2026-10-11](2026-10-11/problem.md)
- [ ] Cost kill-switches
- [ ] Graceful degradation chains
- [ ] Shadow traffic routing

Touched by: [2026-09-17](2026-09-17/problem.md) (LiteLLM-style unified gateway — routing infra, not cost-aware triage), [2026-10-11](2026-10-11/problem.md), checked above.

## Stage 12: Public Teardowns & Portfolios

- [ ] Architecture decision records (ADRs)
- [ ] Cost-per-task analysis
- [ ] Reproducible benchmarks
- [ ] System diagramming
- [ ] Public methodology publishing

Touched by: none yet — this stage is about packaging/presenting prior work, so it likely draws on whatever's already been built rather than needing a new standalone problem.
