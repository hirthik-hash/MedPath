import requests
import json
import sqlite3
import sys
import os
sys.path.insert(0, os.path.abspath('.'))
from medpath_app import sync_patient_evidence_to_backend

test_doc_n8n = {
    'patterns': [
        {
            'name': 'Iron deficiency anemia',
            'likelihood': 'High',
            'confidence': 92,
            'evidence': [
                'Hemoglobin 8.4 g/dL (LOW)',
                'Serum Ferritin: 6.2 ng/mL (LOW)',
                'Only hemoglobin and ferritin documented; reticulocyte count, TIBC, and stool occult blood are not documented',
                'Patient denies hematochezia and denies melena'
            ]
        },
        {
            'name': 'Possible occult gastrointestinal bleeding risk',
            'likelihood': 'Medium',
            'confidence': 55,
            'evidence': [
                'Severe microcytic anemia with ferritin 6.2 ng/mL',
                'No documented upper endoscopy or colonoscopy in medical record'
            ]
        }
    ],
    'next_best_test': {
        'name': 'Fecal occult blood test and GI endoscopy evaluation',
        'reason': 'To rule out occult gastrointestinal blood loss.'
    }
}

test_patient_record = {
    'id': 'MP_TEST_01',
    'name': 'Test Quality Review Patient',
    'symptoms': [{'name': 'Chronic fatigue', 'severity': 'Moderate', 'onsetDate': '2026-08-15'}],
    'labs': [{'name': 'Hemoglobin', 'value': '8.4', 'unit': 'g/dL', 'date': '2026-08-20'}],
    'medications': [{'name': 'Oral Iron Supplement', 'dosage': '325 mg daily'}],
    'documents': [{'id': 'DOC_TEST_01', 'name': 'Iron_Panel_Report.pdf', 'type': 'PDF'}],
    'timeline': [
        {'title': 'Document Uploaded', 'description': 'Uploaded medical document: Iron_Panel_Report.pdf', 'date': '2026-08-20'},
        {'title': 'Laboratory Review', 'description': 'Laboratory evaluation documented Evidence: Hemoglobin 8.4 g/dL and Serum Ferritin: 6.2 ng/mL', 'date': '2026-08-20'}
    ]
}

# Clean any previous test run for MP_TEST_01 in DB
conn = sqlite3.connect('medpath.db')
c = conn.cursor()
c.execute('DELETE FROM evidence_edges WHERE patient_id = ?', ('MP_TEST_01',))
c.execute('DELETE FROM evidence_nodes WHERE patient_id = ?', ('MP_TEST_01',))
conn.commit()

# Sync evidence to backend
sync_patient_evidence_to_backend(
    'MP_TEST_01',
    test_patient_record,
    n8n_result=test_doc_n8n,
    source_doc_id='DOC_TEST_01',
    source_doc_name='Iron_Panel_Report.pdf'
)

# Fetch evidence graph from FastAPI endpoint
res = requests.get('http://127.0.0.1:8000/patients/MP_TEST_01/evidence/graph')
print('FastAPI status:', res.status_code)
graph = res.json()
print('Total nodes:', len(graph['nodes']), '| Total edges:', len(graph['edges']))

print('\n================ ALL GENERATED EVIDENCE NODES ================')
max_words = 0
for i, n in enumerate(graph['nodes'], 1):
    name = n['name']
    wc = len(name.split())
    if wc > max_words:
        max_words = wc
    val = str(n['value'])[:20] if n['value'] is not None else 'None'
    unit = str(n['unit']) if n['unit'] is not None else 'None'
    print(f"{i:2d}. [{n['evidence_type']:14s}] {name:32s} ({wc} words) | Val: {val:18s} | Unit: {unit:8s} | State: {n['evidence_state']:15s} | Conf: {n['confidence']}")

print(f'\nMaximum node name word count: {max_words} words')
assert max_words <= 6, f'Error: max words {max_words} exceeds 6 words'
print('SUCCESS: All node names are strictly atomic and within word count limits!')
