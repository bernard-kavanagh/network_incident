from google.adk.agents import LlmAgent

from network_incident_agent.config import GEMINI_MODEL, gen_config, planner
from network_incident_agent.prompts import GLOBAL_INSTRUCTION, RERANK_PROMPT
from .tools import fetch_all_tickets_and_rerank

rerank_tickets_agent = LlmAgent(
    name="RerankTicketsAgent",
    model=GEMINI_MODEL,
    global_instruction=GLOBAL_INSTRUCTION,
    instruction=RERANK_PROMPT,
    description="Retrieves and ranks the most relevant historical tickets via TiDB hybrid vector + full-text search (replaces the Vertex semantic-ranker).",
    tools=[fetch_all_tickets_and_rerank],
    generate_content_config=gen_config(),
    planner=planner(),
)
