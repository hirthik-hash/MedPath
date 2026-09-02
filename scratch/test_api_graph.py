import requests
import json

res = requests.get('http://127.0.0.1:8000/patients/MP00005/evidence/graph')
print('HTTP status:', res.status_code)
data = res.json()
nodes = data['nodes']
edges = data['edges']
print(f'Total nodes: {len(nodes)}, Total edges: {len(edges)}')

# Verify word count for all nodes
for n in nodes:
    wc = len(n['name'].split())
    if wc > 6:
        print('FAILED on node:', n)
    assert wc <= 6, f'Node name {n["name"]} has {wc} words'

print('ASSERTION PASSED: All nodes have name <= 6 words.')

# Print sample nodes
print('\nSAMPLE EVIDENCE NODES:')
samples = [
    next(n for n in nodes if n['name'] == 'Hemoglobin'),
    next(n for n in nodes if n['name'] == 'Platelet Count'),
    next(n for n in nodes if n['name'] == 'Iron Studies'),
    next(n for n in nodes if n['name'] == 'Chest Pain'),
    next(n for n in nodes if n['name'] == 'Spo2'),
    next(n for n in nodes if n['evidence_type'] == 'DIAGNOSIS'),
    next(n for n in nodes if n['evidence_type'] == 'TIMELINE_EVENT')
]

for s in samples:
    wc = len(s['name'].split())
    print(f"- Name: {s['name']!r} ({wc} words) | Type: {s['evidence_type']} | Value: {s['value']!r} | Unit: {s['unit']!r} | State: {s['evidence_state']} | Conf: {s['confidence']}")
