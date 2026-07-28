"""Shared cognitive-foundation library.

Domain-agnostic substrate layer reused by every agent (the 50-agent blueprint):
  - tidb.py        connection + SQL + vector search primitives
  - embeddings.py  local all-MiniLM-L6-v2 encoder (runs air-gapped)
  - text_bander.py row -> embeddable text (single source of truth)
  - memory.py      three-tier memory + the five custodial duties + routing
"""
