# MMK Trading Bot — LLM Wiki Instructions

You are the maintainer of this wiki. This is a persistent, structured knowledge base about the MMK
trading bot project. Your job is to keep it accurate, interlinked, and up to date.

---

## What This Wiki Is

A curated collection of markdown pages that capture everything discovered about the broker API,
trading strategies, PSX market rules, and the bot's own architecture. Unlike code comments or raw
notes, these pages are synthesized — cross-linked, deduplicated, and written to be immediately
useful when queried.

---

## Directory Layout

```
wiki/
  CLAUDE.md        ← this file (schema + instructions)
  index.md         ← full catalog of all pages, one-line summary each
  log.md           ← append-only activity log (ingest / query / lint events)
  sources/         ← raw input documents (articles, API dumps, decompiled code notes)
  broker_api.md
  socket_messages.md
  fix_protocol.md
  order_flow.md
  market_rules.md
  daily_trader.md
  circuit_limits.md
  authentication.md
  known_issues.md
```

---

## Operations

### INGEST
When given a new source (API response dump, decompiled code finding, bug report, etc.):
1. Read the source carefully.
2. Identify which existing wiki pages it touches (check index.md).
3. Update each affected page — add facts, correct errors, add cross-links.
4. Create new pages for topics that don't exist yet.
5. Update index.md with any new pages.
6. Append an entry to log.md.

### QUERY
When asked a question:
1. Check index.md to find relevant pages.
2. Read those pages.
3. Synthesize a direct answer with inline citations (`[page](page.md)`).
4. If the answer reveals a gap, create or update a page.

### LINT
Periodically check for:
- Contradictions between pages
- Orphan pages not linked from index.md
- Claims that are now known to be wrong
- Missing cross-links between related pages

---

## Page Format

Every page must start with:
```markdown
# Title

**Last updated:** YYYY-MM-DD
**Related:** [page1](page1.md), [page2](page2.md)

One-line summary of what this page covers.

---
```

Use `##` sections, bullet lists for enumerated facts, and inline code for field names / values.
Prefer concise precision over prose. If something is uncertain, say so explicitly.

---

## Log Format

Each log.md entry:
```
## [YYYY-MM-DD] <operation> | <subject>
<one paragraph: what was ingested/queried/fixed and what changed>
```

---

## Conventions

- Field names and API keys always in `backticks`
- Socket message types always in `**bold**` (e.g. **ht**, **mf**, **pm**)
- FIX tags in format `tag 39` (lowercase "tag", then number)
- PKR amounts always include currency: `810.22 PKR`
- Times always in PKT (Pakistan Standard Time, UTC+5)
- Status strings always in `UPPER_CASE` (e.g. `OPENED`, `CLOSED`)
