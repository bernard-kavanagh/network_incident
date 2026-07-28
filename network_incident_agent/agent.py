"""Root orchestrator — the ADK entrypoint.

`adk run network_incident_agent` and `adk web` discover `root_agent` here.
Orchestration (per Devoteam's design, backend swapped to TiDB):

    Root Agent (Orchestrator)
    ├── Filter Agent (Sequential: AllSubcategories -> Subcategory)
    ├── Deviation Agent (HTAP volume deviation)
    └── Rerank Tickets Agent (hybrid vector + full-text rerank)
"""
from google.adk.agents import LlmAgent

from .config import ROOT_AGENT_MODEL, ROOT_AGENT_TEMP, gen_config, planner
from .prompts import GLOBAL_INSTRUCTION, ROOT_PROMPT
from .sub_agents.filter_agent import filter_agent
from .sub_agents.deviation_agent import deviation_agent
from .sub_agents.rerank_tickets_agent import rerank_tickets_agent

root_agent = LlmAgent(
    name="NetworkIncidentRootAgent",
    model=ROOT_AGENT_MODEL,
    global_instruction=GLOBAL_INSTRUCTION,
    instruction=ROOT_PROMPT,
    description="Orchestrates network incident classification, deviation analysis, and ticket reranking over a TiDB cognitive foundation.",
    sub_agents=[filter_agent, deviation_agent, rerank_tickets_agent],
    generate_content_config=gen_config(ROOT_AGENT_TEMP),
    planner=planner(),
)
