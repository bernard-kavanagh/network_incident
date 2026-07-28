from google.adk.agents import LlmAgent

from network_incident_agent.config import GEMINI_MODEL, gen_config, planner
from network_incident_agent.prompts import GLOBAL_INSTRUCTION, DEVIATION_PROMPT
from .tools import get_ticket_counts_for_deviation

deviation_agent = LlmAgent(
    name="DeviationAgent",
    model=GEMINI_MODEL,
    global_instruction=GLOBAL_INSTRUCTION,
    instruction=DEVIATION_PROMPT,
    description="Detects abnormal incident-volume deviations for a subcategory using TiDB HTAP (TiFlash) baselines.",
    tools=[get_ticket_counts_for_deviation],
    generate_content_config=gen_config(),
    planner=planner(),
)
