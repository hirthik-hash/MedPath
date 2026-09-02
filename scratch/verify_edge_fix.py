import requests
import json
import sqlite3
import sys
import os
sys.path.insert(0, os.path.abspath('.'))
from medpath_app import sync_patient_evidence_to_backend

# Clean previous DB records for MP00005 to re-sync cleanly
conn = sqlite3.connect('medpath.db')
c = conn.cursor()
c.execute('DELETE FROM evidence_edges WHERE patient_id = ?', ('MP00005',))
c.execute('DELETE FROM evidence_nodes WHERE patient_id = ?', ('MP00005',))
conn.commit()

# Load patient data
data = json.load(open('data/patients.json', encoding='utf-8'))
p_record = data['MP00005']

# Re-sync
sync_patient_evidence_to_backend('MP00005', p_record)

# Query FastAPI graph endpoint
res = requests.get('http://127.0.0.1:8000/patients/MP00005/evidence/graph')
print('FastAPI status:', res.status_code)
graph = res.json()
nodes = graph['nodes']
edges = graph['edges']

node_map = {n['id']: n for n in nodes}

print(f'\nTotal Nodes: {len(nodes)} | Total Edges: {len(edges)}')

print('\n=== EDGES CONNECTED TO "Currently Hemodynamically Stable" / Stability Nodes ===')
found_target_edges = []
for e in edges:
    src_node = node_map.get(e['source_node_id'], {})
    tgt_node = node_map.get(e['target_node_id'], {})
    src_name = src_node.get('name', 'Unknown')
    tgt_name = tgt_node.get('name', 'Unknown')
    rel = e.get('relationship_type')
    conf = e.get('confidence')
    
    if 'stable' in tgt_name.lower() or 'hemodynamically' in tgt_name.lower():
        found_target_edges.append((src_name, rel, tgt_name, conf, src_node.get('value'), src_node.get('evidence_state')))
        print(f"Edge: [{src_name}] (Val: {src_node.get('value')}, State: {src_node.get('evidence_state')}) --[{rel}]--> [{tgt_name}] (Conf: {conf})")

print('\n=== ALL EDGES IN MP00005 EVIDENCE GRAPH ===')
for e in edges:
    src_node = node_map.get(e['source_node_id'], {})
    tgt_node = node_map.get(e['target_node_id'], {})
    print(f"[{src_node.get('name')}] --[{e.get('relationship_type')}]--> [{tgt_node.get('name')}] (Conf: {e.get('confidence')})")

# Assertions
chest_pain_edge = next((edge for edge in found_target_edges if edge[0] == 'Chest Pain'), None)
sob_edge = next((edge for edge in found_target_edges if edge[0] == 'Shortness Of Breath'), None)

assert chest_pain_edge is not None, "Chest Pain edge not found"
assert chest_pain_edge[1] == "SUPPORTS", f"Expected SUPPORTS for Chest Pain, got {chest_pain_edge[1]}"

assert sob_edge is not None, "Shortness of Breath edge not found"
assert sob_edge[1] == "SUPPORTS", f"Expected SUPPORTS for Shortness of Breath, got {sob_edge[1]}"

print('\nASSERTION SUCCESS: Chest Pain and Shortness of Breath correctly have SUPPORTS edges toward Currently Hemodynamically Stable!')
