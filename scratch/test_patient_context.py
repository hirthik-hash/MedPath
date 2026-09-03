import sqlite3
import json
import os

def assemble_patient_context(patient_id: str):
    conn = sqlite3.connect('medpath.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM evidence_nodes WHERE patient_id = ?', (patient_id,))
    nodes = [dict(r) for r in c.fetchall()]
    c.execute('SELECT * FROM evidence_edges WHERE patient_id = ?', (patient_id,))
    edges = [dict(r) for r in c.fetchall()]
    conn.close()

    patient_record = {}
    if os.path.exists('data/patients.json'):
        with open('data/patients.json', 'r', encoding='utf-8') as f:
            patients = json.load(f)
            patient_record = patients.get(patient_id, {})

    # Categorize nodes
    diagnoses = [n for n in nodes if n.get('evidence_type') == 'DIAGNOSIS']
    labs = [n for n in nodes if n.get('evidence_type') == 'LAB_RESULT']
    symptoms = [n for n in nodes if n.get('evidence_type') == 'SYMPTOM']
    meds = [n for n in nodes if n.get('evidence_type') == 'MEDICATION']
    timeline_evts = [n for n in nodes if n.get('evidence_type') == 'TIMELINE_EVENT']
    notes_nodes = [n for n in nodes if n.get('evidence_type') == 'CLINICAL_NOTE']

    pname = patient_record.get('name', 'Unknown')
    dob = patient_record.get('date_of_birth', 'Not documented')
    gender = patient_record.get('gender', 'Not documented')

    lines = []
    lines.append(f"=== PATIENT CLINICAL RECORD: {pname} (ID: {patient_id}) ===")
    lines.append(f"Demographics: DOB: {dob} | Gender: {gender}")
    lines.append("")
    
    lines.append("--- EVIDENCE-SUPPORTED DIAGNOSTIC HYPOTHESES (AI Patterns) ---")
    if diagnoses:
        for d in diagnoses:
            conf = d.get('confidence', 'Medium')
            lines.append(f"• Hypothesis: {d['name']} (Confidence: {conf}, Status: {d.get('verification_status', 'Pending')})")
    else:
        lines.append("• No diagnostic hypotheses recorded.")
    lines.append("")

    lines.append("--- DOCUMENTED CLINICAL EVIDENCE (Facts) ---")
    lines.append("[Laboratory & Diagnostic Findings]:")
    tested_labs = [l for l in labs if l.get('evidence_state') != 'NOT_YET_TESTED']
    if tested_labs:
        for l in tested_labs:
            val = f": {l.get('value')} {l.get('unit') or ''}".strip() if l.get('value') else ""
            lines.append(f"  - {l['name']}{val} (State: {l.get('evidence_state', 'PRESENT')}, Source: {l.get('source_document_name') or 'Clinical Record'})")
    else:
        lines.append("  - None recorded.")

    lines.append("[Documented Symptoms]:")
    tested_symps = [s for s in symptoms if s.get('evidence_state') != 'NOT_YET_TESTED']
    if tested_symps:
        for s in tested_symps:
            val = f" ({s.get('value')})" if s.get('value') else ""
            lines.append(f"  - {s['name']}{val} (State: {s.get('evidence_state', 'PRESENT')})")
    else:
        lines.append("  - None recorded.")

    lines.append("[Medications]:")
    if meds:
        for m in meds:
            lines.append(f"  - {m['name']} (State: {m.get('evidence_state', 'PRESENT')})")
    else:
        lines.append("  - None recorded.")
    lines.append("")

    lines.append("--- MISSING TESTS & CARE GAPS (Not Yet Tested) ---")
    missing_nodes = [n for n in nodes if n.get('evidence_state') == 'NOT_YET_TESTED']
    if missing_nodes:
        for m in missing_nodes:
            lines.append(f"• Missing/Needed: {m['name']} (Type: {m.get('evidence_type')})")
    else:
        lines.append("• No missing tests explicitly flagged.")
    
    if patient_record.get('next_best_test'):
        lines.append(f"• Recommended Next Best Test: {patient_record['next_best_test']}")
    lines.append("")

    lines.append("--- EVIDENCE RELATIONSHIPS (Graph Provenance) ---")
    node_dict = {n['id']: n for n in nodes}
    if edges:
        for e in edges[:15]:
            s_node = node_dict.get(e['source_node_id'], {})
            t_node = node_dict.get(e['target_node_id'], {})
            lines.append(f"• [{s_node.get('name', 'Evidence')}] --{e.get('relationship_type')}--> [{t_node.get('name', 'Diagnosis')}]")
    lines.append("")

    lines.append("--- DOCTOR & CLINICAL NOTES ---")
    doc_notes = patient_record.get('doctor_notes_history', [])
    if doc_notes:
        for dn in doc_notes:
            lines.append(f"• [{dn.get('created_at', '')}] {dn.get('author', 'Doctor')}: \"{dn.get('text', '')}\"")
    elif patient_record.get('doctor_notes'):
        lines.append(f"• Doctor Note: \"{patient_record.get('doctor_notes')}\"")
    else:
        lines.append("• No doctor notes recorded.")
    
    if patient_record.get('clinical_notes'):
        lines.append(f"• AI Clinical Note Summary: {patient_record.get('clinical_notes')}")
    lines.append("")

    lines.append("--- DOCUMENTS & TIMELINE ---")
    for doc in patient_record.get('documents', []):
        lines.append(f"• Document: {doc.get('name')} (Type: {doc.get('type')}, Uploaded: {doc.get('uploadedAt')})")
    for evt in patient_record.get('timeline', []):
        lines.append(f"• Event: {evt.get('date')} - {evt.get('description')}")

    return "\n".join(lines)

print(assemble_patient_context('MP00005'))
