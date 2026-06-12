# Summit Outreach - Implementation

Full architecture guide at `../IMPLEMENTATION.md`. This file covers Summit-specific details.

## Data flow

```
Google Sheet "Summit Outreach 2026" → generate_summit_emails.py → same sheet (cols G-J) → summit_send.js → Gmail
```

## Key features

### Active Relating Rule
The Summit prompt (`summit_prompt.py`) requires every Summit feature mentioned to be explicitly connected back to the recipient. Pattern: "[Summit thing] → [why this matters for YOU specifically]." See `summit_prompt.py` for detailed examples.

### Multilingual subjects + body translation
If the recipient's Language field isn't English, the script:
1. Generates the email in English (system prompt specifies subject language)
2. Calls `translate_email()` for a second LLM translation
3. Writes English, translation, and a combined version to the sheet

The subject line is written in the recipient's language. The combined column has translation first, then English.

### No external research
Unlike SMN and Soirée, Summit uses only the profile data already in the sheet (Career Interests, Ideal Job, Language). No website scraping.

### Translation fallback
If the JSON-structured translation call fails, a raw LLM call without JSON structure is attempted. This ensures translations don't block the whole pipeline.

## Maintenance

| What | Where |
|---|---|
| Change event date/venue | `summit_prompt.py` - SUMMIT_SYSTEM_PROMPT and SUMMIT_CONTEXT |
| Update speaker bios | `summit_prompt.py` - SUMMIT_CONTEXT |
| Update employer list | `summit_prompt.py` - EMPLOYERS & INDUSTRIES section |
| Change registration link | `summit_prompt.py` - zeffy.com URL in SUMMIT_CONTEXT |
| Change sender name | `summit_prompt.py` - edit signature block |
| Change AI model | `../shared/config.py` |
