"""Filter Agent — a SequentialAgent enforcing classification order:
AllSubcategoriesAgent (populate candidates) -> SubcategoryAgent (rank top 5)."""
from google.adk.agents import SequentialAgent

from network_incident_agent.sub_agents.all_subcategories_agent import all_subcategories_agent
from network_incident_agent.sub_agents.subcategory_agent import subcategory_agent

filter_agent = SequentialAgent(
    name="FilterAgent",
    description="Runs the classification sub-agents in order to produce the top-5 subcategories.",
    sub_agents=[all_subcategories_agent, subcategory_agent],
)
