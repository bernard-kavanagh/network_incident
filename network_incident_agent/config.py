"""Centralised configuration (mirrors Devoteam's config.py + .env contract)."""
import os
from dotenv import load_dotenv

load_dotenv()

# Models — reasoning stays on Gemini/Vertex per the Google Cloud remit.
ROOT_AGENT_MODEL = os.getenv("ROOT_AGENT_MODEL", "gemini-2.5-flash")
ROOT_AGENT_TEMP = float(os.getenv("ROOT_AGENT_TEMP", "0.01"))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_TEMP = float(os.getenv("GEMINI_TEMP", "0.01"))
GEMINI_TOP_P = float(os.getenv("GEMINI_TOP_P", "0.95"))
GEMINI_MAX_TOKENS = int(os.getenv("GEMINI_MAX_TOKENS", "2000"))
THINKING_BUDGET = int(os.getenv("THINKING_BUDGET", "0"))

# Output sizing
RERANK_RETRIEVE_TOP_N = int(os.getenv("RERANK_RETRIEVE_TOP_N", "10"))
OUTPUT_N_TICKETS = int(os.getenv("OUTPUT_N_TICKETS", "10"))


def gen_config(temp: float = None):
    """Build a GenerateContentConfig with the shared Gemini settings."""
    from google.genai import types
    return types.GenerateContentConfig(
        temperature=GEMINI_TEMP if temp is None else temp,
        top_p=GEMINI_TOP_P,
        max_output_tokens=GEMINI_MAX_TOKENS,
    )


def planner(thinking_budget: int = None):
    """BuiltInPlanner with the configured thinking budget (0 = fast/production)."""
    from google.adk.planners import BuiltInPlanner
    from google.genai import types
    budget = THINKING_BUDGET if thinking_budget is None else thinking_budget
    return BuiltInPlanner(thinking_config=types.ThinkingConfig(thinking_budget=budget))
