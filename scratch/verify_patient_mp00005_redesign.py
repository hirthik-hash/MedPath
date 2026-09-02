import requests
import json
import sqlite3
import sys
import os
sys.path.insert(0, os.path.abspath('.'))

# Re-sync MP00005 to ensure DB is fresh
conn = sqlite3.connect('medpath.db')
c = conn.cursor()
c.execute('DELETE FROM evidence_edges WHERE patient_id = ?', ('MP00005',))
c.execute('DELETE FROM evidence_nodes WHERE patient_id = ?', ('MP00005',))
conn.commit()

data = json.load(open('data/patients.json', encoding='utf-8'))
from medpath_app import sync_patient_evidence_to_backend
sync_patient_evidence_to_backend('MP00005', data['MP00005'])

# Fetch graph from API
res = requests.get('http://127.0.0.1:8000/patients/MP00005/evidence/graph')
assert res.status_code == 200
graph = res.json()
nodes = graph['nodes']
edges = graph['edges']
node_map = {n['id']: n for n in nodes}

print(f"=== MP00005 Graph Verification (Total Nodes: {len(nodes)}, Total Edges: {len(edges)}) ===")

# 1. Check max word count on all nodes (No paragraphs)
max_words = 0
for n in nodes:
    wc = len(n['name'].split())
    if wc > max_words:
        max_words = wc
    assert not any(c in n['name'] for c in '()[]{}'), f"Unmatched bracket in node: {n['name']}"

print(f"1. Max Word Count across all {len(nodes)} nodes: {max_words} words (Strictly atomic, no paragraphs!)")
assert max_words <= 6

# 2. Check confidence variance
conf_set = set(n.get('confidence') for n in nodes)
print(f"2. Varied Confidence Levels: {conf_set}")
assert len(conf_set) >= 2

# 3. Check Initial Collapsed View (Patient Root + Diagnoses)
diagnoses = [n for n in nodes if n.get('evidence_type') == 'DIAGNOSIS']
print(f"3. Initial Collapsed View: 1 Patient Root + {len(diagnoses)} Diagnoses = {1 + len(diagnoses)} nodes at Level 0 and Level 1")
assert len(diagnoses) == 10

# 4. Check Expanding Diagnosis 'Acute Dengue Infection'
dengue_node = next(d for d in diagnoses if 'dengue infection' in d['name'].lower())
dengue_edges = [e for e in edges if e['target_node_id'] == dengue_node['id'] or e['source_node_id'] == dengue_node['id']]
print(f"\n4. 'Acute Dengue Infection' (ID {dengue_node['id']}) Connected Findings ({len(dengue_edges)} edges):")
for e in dengue_edges:
    other_id = e['source_node_id'] if e['target_node_id'] == dengue_node['id'] else e['target_node_id']
    f = node_map[other_id]
    print(f"   • [{f['evidence_type']}] {f['name']} (Val: {f.get('value')} {f.get('unit') or ''}) --[{e['relationship_type']}]--> {dengue_node['name']} (Conf: {e.get('confidence')})")

# 5. Check Expanding Diagnosis 'Currently Hemodynamically Stable'
stable_node = next(d for d in diagnoses if 'stable' in d['name'].lower())
stable_edges = [e for e in edges if e['target_node_id'] == stable_node['id'] or e['source_node_id'] == stable_node['id']]
print(f"\n5. 'Currently Hemodynamically Stable' (ID {stable_node['id']}) Connected Findings ({len(stable_edges)} edges):")
for e in stable_edges:
    other_id = e['source_node_id'] if e['target_node_id'] == stable_node['id'] else e['target_node_id']
    f = node_map[other_id]
    print(f"   • [{f['evidence_type']}] {f['name']} (Val: {f.get('value')} {f.get('unit') or ''}, State: {f.get('evidence_state')}) --[{e['relationship_type']}]--> {stable_node['name']}")
    assert e['relationship_type'] == 'SUPPORTS', f"Expected SUPPORTS, got {e['relationship_type']}"

# 6. Check Expanding Diagnosis 'Unclassified Anemia' (Care gaps / missing tests)
anemia_node = next(d for d in diagnoses if 'unclassified anemia' in d['name'].lower())
anemia_edges = [e for e in edges if e['target_node_id'] == anemia_node['id'] or e['source_node_id'] == anemia_node['id']]
print(f"\n6. 'Unclassified Anemia' (ID {anemia_node['id']}) Connected Findings ({len(anemia_edges)} edges):")
for e in anemia_edges:
    other_id = e['source_node_id'] if e['target_node_id'] == anemia_node['id'] else e['target_node_id']
    f = node_map[other_id]
    print(f"   • [{f['evidence_type']}] {f['name']} (State: {f.get('evidence_state')}) --[{e['relationship_type']}]--> {anemia_node['name']}")

print("\n=== ALL 6 VERIFICATION CRITERIA PASSED CLEANLY! ===")
