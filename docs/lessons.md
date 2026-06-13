# Lessons

## 2026-06-14 — Claimed the Strava MCP was free without verifying

**Type:** Mistake (wrong factual assertion)

**Incident:** During brainstorming I told the user the official `claude.ai Strava` MCP
connector was "absolutely free" and needed no subscription, with explicit confidence.
When the user opened `/mcp` to authenticate, Claude showed a "Subscribe to access the
Strava MCP" paywall. The user had already stated they could not pay.

**Root cause:** I conflated two different things — the *Strava API* (genuinely free for a
registered personal app) and the *claude.ai Strava MCP connector* (a paid Anthropic
product that wraps it). I asserted the connector was free from assumption, not from
verification, violating the rule "never hallucinate to fill a gap; verify facts first."

**Consequence:** The entire data-source layer of the approved plan (Task 8) was built on
a paid dependency the user explicitly ruled out. Required a mid-execution re-plan.

**Correct interpretation / new rule:** A paid wrapper around a free API is not free.
Before claiming any external dependency is free/available, verify the *specific product
being connected* — not the underlying service. The free path here is the direct Strava
API (already proven by the retired `legacy/strava.py` + `legacy/auth_server.py`, which
used `STRAVA_CLIENT_ID`/`STRAVA_CLIENT_SECRET` over `https://www.strava.com/api/v3`).
