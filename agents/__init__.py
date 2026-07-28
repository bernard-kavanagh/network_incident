"""Standalone cognitive-foundation agents that share the lib/ substrate and the
network_incident adapter — proving multi-agent reuse of one TiDB cluster:

  triage.py       Triage & Correlation  (episodic + anomaly@ingestion + HTAP)
  remediation.py  Remediation           (procedural memory + write-back)

The ADK Investigation agent (network_incident_agent/) is the third agent. All
three import the SAME lib.memory + lib.tidb + adapter — the 50-agent blueprint.
"""
