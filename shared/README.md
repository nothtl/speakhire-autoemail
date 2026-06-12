# Shared Modules

Common code used by all SpeakHire email campaigns. Change something here and every campaign picks it up.

## Files

| File | Purpose |
|---|---|
| `config.py` | API keys, AI model selection, Google Sheet URL. Edit this to swap models or update credentials. |
| `generator.py` | Engine: LLM caller, text cleaners, sheet helpers, CLI argument parser. |

## How campaigns use this

Every `generate_*.py` starts with:

```python
sys.path.insert(0, os.path.join(SCRIPT_DIR, '..'))
from shared.config import *
from shared.generator import *
```

After that, `call_llm()`, `clean_email()`, `safe_str()`, `connect_sheet()`, and `parse_args()` are all available.

## Deep dive

See `implementation.md` for a line-by-line walkthrough of every function.
