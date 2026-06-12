# General Outreach - Implementation

Full architecture guide at `../IMPLEMENTATION.md`. This file covers the general outreach engine specifics.

## Architecture

```
Google Sheet "Outreach Tracker" → generate.py → outreach_worker.py → sheet (DRAFTED) → outreach_send.js → Gmail
```

Unlike the simpler campaign scripts, this system uses a worker-based architecture:
- `generate.py` - orchestrator: reads rows, patches prompts, calls worker functions
- `outreach_worker.py` - engine: 1796 lines covering research, LLM, sheet I/O, email sending, and a FastAPI server
- `outreach_prompt.py` - prompts for all three campaign types

## 37-column schema

This is the most complex sheet in the codebase. Key columns:
- **STATUS** flows: `READY_FOR_RESEARCH` → `DRAFTED` → `APPROVED` → `SENT`
- **EVIDENCE_*** columns track what the AI researched for personalization
- **CAMPAIGN_TYPE** dropdown controls which prompt is used

## Runtime prompt patching

`generate.py` dynamically patches `worker.SYSTEM_PROMPT` per row based on the `CAMPAIGN_TYPE` dropdown. This means the worker engine is stateless - the prompt changes per-row without restarting.

## Three-tier LLM fallback

`outreach_worker.generate_draft()`:
1. LangChain `with_structured_output(OutreachDraft)` - Pydantic-validated (most reliable)
2. Direct HTTP + JSON parse + Pydantic validation
3. Hardcoded fallback template

## FastAPI server

The worker doubles as a web server. Endpoints: `/generate`, `/approve`, `/send`, `/init-sheet`, `/import-old-data`, `/health`. Protected by `X-API-Key` header. Run via `uvicorn outreach_worker:app`.

## Maintenance

| What | Where |
|---|---|
| Change prompts | `outreach_prompt.py` |
| Change AI model / API keys | `../shared/config.py` |
| Change sheet schema | `generate.py` - ROW_COLUMNS list |
| Fix LLM calling bug | `outreach_worker.py` - generate_draft() |
| Change email sending (SMTP/SendGrid) | `outreach_worker.py` - _send() |
