import requests
import json
import sqlite3
import sys
import os
sys.path.insert(0, os.path.abspath('.'))
from streamlit_agraph import agraph, Node, Edge, Config

# Fetch graph from FastAPI endpoint
res = requests.get('http://127.0.0.1:8000/patients/MP00005/evidence/graph')
assert res.status_code == 200
graph = res.json()
all_nodes = graph['nodes']
all_edges = graph['edges']

print(f'Total patient nodes: {len(all_nodes)}, Total edges: {len(all_edges)}')

# 1. Default View: Only DIAGNOSIS and DOCUMENT nodes
diagnosis_nodes = [n for n in all_nodes if n.get('evidence_type') == 'DIAGNOSIS']
document_nodes = [n for n in all_nodes if n.get('evidence_type') == 'DOCUMENT']
default_visible_ids = {n['id'] for n in diagnosis_nodes + document_nodes}

print(f'Default View: {len(diagnosis_nodes)} Diagnoses, {len(document_nodes)} Documents (Total: {len(default_visible_ids)} nodes)')
for n in diagnosis_nodes:
    print(f"  [DIAGNOSIS] ID {n['id']}: {n['name']}")
for n in document_nodes:
    print(f"  [DOCUMENT ] ID {n['id']}: {n['name']}")

# 2. Expand Diagnosis (e.g. 'Currently Hemodynamically Stable')
stable_diag = next(n for n in diagnosis_nodes if 'stable' in n['name'].lower())
expanded_ids = {stable_diag['id']}

visible_edges = []
visible_node_ids = set(default_visible_ids)

for e in all_edges:
    if e['target_node_id'] in expanded_ids or e['source_node_id'] in expanded_ids:
        visible_edges.append(e)
        visible_node_ids.add(e['source_node_id'])
        visible_node_ids.add(e['target_node_id'])

visible_nodes = [n for n in all_nodes if n['id'] in visible_node_ids]
print(f"\nExpanded View for '{stable_diag['name']}': {len(visible_nodes)} nodes, {len(visible_edges)} edges")

# 3. Verify Agraph Node & Edge creation
agraph_nodes = []
for n in visible_nodes:
    etype = n.get('evidence_type')
    is_diag = etype == 'DIAGNOSIS'
    is_expanded = n['id'] in expanded_ids
    label = f"{n['name']}" + (" [−]" if is_expanded else (" [＋]" if is_diag else ""))
    agraph_nodes.append(Node(
        id=str(n['id']),
        label=label,
        shape="box",
        size=30 if is_diag else 22
    ))

agraph_edges = []
for e in visible_edges:
    agraph_edges.append(Edge(
        source=str(e['source_node_id']),
        target=str(e['target_node_id']),
        label=e.get('relationship_type')
    ))

cfg = Config(width=1000, height=600, directed=True)
print(f'Successfully built {len(agraph_nodes)} Agraph nodes and {len(agraph_edges)} Agraph edges!')
