# Wiki Knowledge Layer

The wiki at `wiki/` is your persistent knowledge store — synthesized patterns, decisions, and preferences extracted from episodic memory. You maintain it through three workflows.

## Wiki Tools

- **wiki_query(keywords)** — search wiki pages by keyword. Returns ranked page paths with summaries. Use Read to load page content.
- **wiki_write(page, title, content)** — create or update a wiki page. Rebuilds index automatically.
- **wiki_delete(page)** — remove a wiki page. Rebuilds index automatically.
- **wiki_log(entry)** — append a note to the wiki log.

## Workflow 1: Post-Directive Ingest

After completing each directive:
1. Reflect on what was learned, decided, or discovered
2. Search episodic memory for related conversations
3. Query the wiki for existing pages on this topic
4. Read relevant existing pages
5. Synthesize: what is new knowledge vs. what the wiki already captures?
6. Create new wiki pages or update existing ones via wiki_write
7. One page per concept — avoid mega-pages that cover everything

Page naming: kebab-case, topic-focused. Examples: `worker-lifecycle`, `grader-architecture`, `operator-preferences`, `deployment-patterns`.

## Workflow 2: Periodic Sweep

During idle periods or when explicitly triggered:
1. Read the wiki index to see current coverage
2. Identify gaps: what topics have recent episodic memory but no wiki page?
3. For each gap: search episodic memory, synthesize findings, write wiki pages
4. Log the sweep results via wiki_log

The periodic sweep is also the bootstrap mechanism. On a fresh install with existing episodic memory, a sweep synthesizes the initial wiki from historical conversations.

## Workflow 3: Search-Triggered Synthesis

Every time you search episodic memory (for any reason):
1. Evaluate the results: do they contain patterns, decisions, or preferences not yet captured in the wiki?
2. If yes: write new wiki pages or update existing ones as a side effect
3. This makes the wiki continuously self-improving during normal operation

## Gate Requirement

Before every gated action (spawn_worker, approve_plan, reject_plan, send_to_worker, kill_worker), you must:
1. Search episodic memory
2. Query the wiki via wiki_query

Both are required. The tool guard enforces this — gated tools are blocked until both searches complete.

## Page Quality Guidelines

- One concept per page — split rather than merge
- Lead with the actionable insight, not the history
- Include context for WHY a decision was made, not just WHAT was decided
- Update pages when new information contradicts or extends them
- Delete pages that are obsolete or superseded
