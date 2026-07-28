from google.adk.agents import LlmAgent

from network_incident_agent.config import GEMINI_MODEL, gen_config, planner
from network_incident_agent.prompts import GLOBAL_INSTRUCTION, ALL_SUBCATEGORIES_PROMPT
from .tools import fetch_all_subcategories

all_subcategories_agent = LlmAgent(
    name="AllSubcategoriesAgent",
    model=GEMINI_MODEL,
    global_instruction=GLOBAL_INSTRUCTION,
    instruction=ALL_SUBCATEGORIES_PROMPT,
    description="Retrieves the distinct incident subcategories present in TiDB for a time range/region.",
    tools=[fetch_all_subcategories],
    generate_content_config=gen_config(),
    planner=planner(),
)
