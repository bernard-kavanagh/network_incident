from google.adk.agents import LlmAgent

from network_incident_agent.config import GEMINI_MODEL, gen_config, planner
from network_incident_agent.prompts import GLOBAL_INSTRUCTION, SUBCATEGORY_PROMPT
from .tools import get_subcategories_from_state, set_top5_subcategories

subcategory_agent = LlmAgent(
    name="SubcategoryAgent",
    model=GEMINI_MODEL,
    global_instruction=GLOBAL_INSTRUCTION,
    instruction=SUBCATEGORY_PROMPT,
    description="Classifies the incident and selects the top-5 subcategories, aided by TiDB vector similarity over the incident catalog.",
    tools=[get_subcategories_from_state, set_top5_subcategories],
    generate_content_config=gen_config(),
    planner=planner(),
)
