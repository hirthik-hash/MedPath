import requests
import json
import sqlite3
import sys
import os
sys.path.insert(0, os.path.abspath('.'))
from medpath_app import sync_patient_evidence_to_backend, clean_atomic_name, determine_relationship_type

# 1. Test clean_atomic_name on parenthesis test cases
print('=== 1. PARENTHESIS STRIPPING TESTS ===')
test_strings = ['Malaria)', '(Malaria)', 'Malaria (smear negative)', 'Rdw)', '(MCV, RDW)', 'Hemoglobin (g/dL)']
for ts in test_strings:
    cleaned = clean_atomic_name(ts)
    print(f"'{ts}' -> '{cleaned}'")
    assert not any(c in cleaned for c in '()[]{}'), f"Unmatched bracket found in {cleaned}"

print('SUCCESS: All parenthesis test cases stripped cleanly!')

# 2. Test determine_relationship_type for Fatigue and Acute Febrile Illness
print('\n=== 2. RELATIONSHIP LOGIC TESTS (Fatigue -> Febrile Illness) ===')
fatigue_finding = {'name': 'Fatigue', 'value': 'Present', 'evidence_state': 'PRESENT'}
fever_finding = {'name': 'Fever', 'value': 'Present (3 days)', 'evidence_state': 'PRESENT'}
dengue_neg_finding = {'name': 'Dengue Ns1 Antigen', 'value': 'Negative', 'evidence_state': 'TESTED_NEGATIVE'}
pattern_raw = 'Acute febrile illness without laboratory evidence of dengue or malaria'
pattern_atomic = 'Acute Febrile Illness'

rel_fatigue = determine_relationship_type(fatigue_finding, pattern_raw, pattern_atomic)
rel_fever = determine_relationship_type(fever_finding, pattern_raw, pattern_atomic)
rel_dengue_neg = determine_relationship_type(dengue_neg_finding, pattern_raw, pattern_atomic)

print(f"Fatigue --[{rel_fatigue}]--> {pattern_atomic}")
print(f"Fever   --[{rel_fever}]--> {pattern_atomic}")
print(f"Dengue NS1 (Neg) --[{rel_dengue_neg}]--> {pattern_atomic}")

assert rel_fatigue == 'SUPPORTS', f"Expected SUPPORTS for Fatigue, got {rel_fatigue}"
assert rel_fever == 'SUPPORTS', f"Expected SUPPORTS for Fever, got {rel_fever}"
assert rel_dengue_neg == 'SUPPORTS', f"Expected SUPPORTS for Dengue NS1 Neg, got {rel_dengue_neg}"
print('SUCCESS: Clinical relationship logic verified!')

# 3. Clean and Re-sync MP00005 in database
print('\n=== 3. RE-SYNC MP00005 DATABASE & API TEST ===')
conn = sqlite3.connect('medpath.db')
c = conn.cursor()
c.execute('DELETE FROM evidence_edges WHERE patient_id = ?', ('MP00005',))
c.execute('DELETE FROM evidence_nodes WHERE patient_id = ?', ('MP00005',))
conn.commit()

data = json.load(open('data/patients.json', encoding='utf-8'))
sync_patient_evidence_to_backend('MP00005', data['MP00005'])

res = requests.get('http://127.0.0.1:8000/patients/MP00005/evidence/graph')
assert res.status_code == 200
graph = res.json()
nodes = graph['nodes']
edges = graph['edges']
print(f'Total Ingested Nodes: {len(nodes)}, Total Ingested Edges: {len(edges)}')

# Verify no node names contain stray parens
for n in nodes:
    assert not any(c in n['name'] for c in '()[]{}'), f"Unmatched bracket in node: {n['name']}"

# Verify Default View vs Expanded View counts
diag_nodes = [n for n in nodes if n.get('evidence_type') == 'DIAGNOSIS']
doc_nodes = [n for n in nodes if n.get('evidence_type') == 'DOCUMENT']
default_count = len(diag_nodes) + len(doc_nodes)
print(f'Default Collapsed View: {len(diag_nodes)} Diagnoses + {len(doc_nodes)} Documents = {default_count} High-Level Nodes (hidden: {len(nodes) - default_count} atomic nodes)')

print('\nALL VERIFICATIONS PASSED CLEANLY!')
