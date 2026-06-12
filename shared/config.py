"""
shared/config.py — One place for all API keys, model selection, and sheet config.

Import this from any campaign script. Change keys or models here and every
campaign picks it up automatically.

Usage:
    from shared.config import *
"""

import os, sys

# ═══════════════════════════════════════════════════
# PATHS
# ═══════════════════════════════════════════════════

SHARED_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR   = os.path.dirname(SHARED_DIR)                     # autoemail/
ENV_DIR    = os.path.join(ROOT_DIR, 'speakhire-outreach', 'speakhire-outreach-simple')

# ═══════════════════════════════════════════════════
# ENV
# ═══════════════════════════════════════════════════

from dotenv import load_dotenv
load_dotenv(os.path.join(ENV_DIR, '.env'))

# ═══════════════════════════════════════════════════
# LLM — OpenRouter free model first, DeepSeek fallback
# ═══════════════════════════════════════════════════

OPENROUTER_KEY = os.getenv('OPENROUTER_API_KEY', '')
if OPENROUTER_KEY:
    API_KEY   = OPENROUTER_KEY
    BASE_URL  = 'https://openrouter.ai/api/v1'
    LLM_MODEL = 'google/gemma-4-31b-it:free'
else:
    API_KEY   = os.getenv('DEEPSEEK_API_KEY')
    BASE_URL  = os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')
    LLM_MODEL = 'deepseek-chat'

# ═══════════════════════════════════════════════════
# GOOGLE SHEETS
# ═══════════════════════════════════════════════════

SHEET_URL  = os.getenv('GOOGLE_SHEET_URL')
CREDS_PATH = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')

# ═══════════════════════════════════════════════════
# HTTP
# ═══════════════════════════════════════════════════

BOT_HEADERS = {"User-Agent": "Mozilla/5.0 (SpeakHire Outreach Bot; nonprofit use)"}
