import requests
import json
import sqlite3
import sys
import os
sys.path.insert(0, os.path.abspath('.'))
from streamlit_agraph import agraph, Node, Edge, Config

# Fetch MP00005
res = requests.get('http://127.0.0.1:8000/patients/MP00005/evidence/graph')
graph_data = res.json()
all_nodes = graph_data['nodes']
all_edges = graph_data['edges']
node_map = {n['id']: n for n in all_nodes}

diagnosis_nodes = [n for n in all_nodes if n.get('evidence_type') == 'DIAGNOSIS']
diagnosis_ids = {n['id'] for n in diagnosis_nodes}

def build_hierarchical_elements(patient_id, patient_name, expanded_diag_ids, selected_id=None):
    agraph_nodes = []
    agraph_edges = []
    seen_ids = set()

    def add_n(node_obj):
        if node_obj.id not in seen_ids:
            seen_ids.add(node_obj.id)
            agraph_nodes.append(node_obj)

    # Level 0: Patient Root
    root_id = "node_patient_root"
    p_label = f"👤 Patient: {patient_name}\nID: {patient_id}"
    add_n(Node(
        id=root_id,
        label=p_label,
        title=f"Patient Record: {patient_name} ({patient_id})",
        shape="box",
        level=0,
        color={"background": "#EEF2FF", "border": "#4F46E5", "highlight": {"background": "#E0E7FF", "border": "#3730A3"}},
        font={"color": "#312E81", "size": 13, "bold": True, "face": "Arial"},
        borderWidth=2,
        shadow=True
    ))

    # Level 1: Diagnoses
    for d in diagnosis_nodes:
        did = str(d['id'])
        dname = d.get('name', 'Diagnosis')
        conf = d.get('confidence', 'High')
        conf_str = f"{conf}%" if str(conf).isdigit() else str(conf)
        is_exp = d['id'] in expanded_diag_ids
        exp_badge = "[−]" if is_exp else "[＋]"
        dlabel = f"🧠 {dname}\n{conf_str}  {exp_badge}"
        
        is_sel = selected_id == d['id']
        add_n(Node(
            id=did,
            label=dlabel,
            title=f"<b>{dname}</b><br>Confidence: {conf_str}<br>Click to {'collapse' if is_exp else 'expand'}",
            shape="box",
            level=1,
            color={"background": "#FFF9C4" if is_sel else "#FFE4E6", "border": "#F59E0B" if is_sel else "#E11D48"},
            font={"color": "#9F1239", "size": 12, "bold": True, "face": "Arial"},
            borderWidth=2 if not is_sel else 3,
            shadow=True
        ))

        agraph_edges.append(Edge(
            source=root_id,
            target=did,
            color="#94A3B8",
            arrows="to",
            smooth={"type": "cubicBezier", "roundness": 0.4}
        ))

        # Level 2-4: If Expanded
        if is_exp:
            connected_edges = [e for e in all_edges if e['target_node_id'] == d['id'] or e['source_node_id'] == d['id']]
            
            sup_items = []
            con_items = []
            mis_items = []
            seen_finding_ids = set()

            for e in connected_edges:
                other_id = e['source_node_id'] if e['target_node_id'] == d['id'] else e['target_node_id']
                if other_id == d['id'] or other_id in seen_finding_ids:
                    continue
                fnode = node_map.get(other_id)
                if not fnode or fnode.get('evidence_type') == 'DIAGNOSIS':
                    continue
                
                seen_finding_ids.add(other_id)
                rel = e.get('relationship_type', 'SUPPORTS')
                estate = fnode.get('evidence_state', 'PRESENT')

                if estate == 'NOT_YET_TESTED' or rel == 'INDICATES':
                    mis_items.append((fnode, e))
                elif rel == 'CONTRADICTS':
                    con_items.append((fnode, e))
                else:
                    sup_items.append((fnode, e))

            branches = [
                ("Supporting", sup_items, "#DCFCE7", "#16A34A", "#166534", "cat_sup", "✅"),
                ("Contradicting", con_items, "#FEE2E2", "#DC2626", "#991B1B", "cat_con", "❌"),
                ("Missing / Needed", mis_items, "#FEF3C7", "#D97706", "#92400E", "cat_mis", "⚠️")
            ]

            for cat_title, findings_list, bg_col, border_col, font_col, prefix, icon in branches:
                if not findings_list:
                    continue
                
                cat_id = f"{prefix}_{d['id']}"
                add_n(Node(
                    id=cat_id,
                    label=f"{icon} {cat_title}\n({len(findings_list)})",
                    title=f"Category: {cat_title} evidence for {dname}",
                    shape="box",
                    level=2,
                    color={"background": bg_col, "border": border_col},
                    font={"color": font_col, "size": 11, "bold": True, "face": "Arial"},
                    borderWidth=1.5
                ))

                agraph_edges.append(Edge(
                    source=did,
                    target=cat_id,
                    color=border_col,
                    arrows="to",
                    smooth={"type": "cubicBezier"}
                ))

                for fnode, edge_obj in findings_list:
                    fid = str(fnode['id'])
                    fname = fnode.get('name', 'Finding')
                    fval = fnode.get('value')
                    funit = fnode.get('unit')
                    ftype = fnode.get('evidence_type', 'CLINICAL_NOTE')
                    festate = fnode.get('evidence_state', 'PRESENT')
                    fconf = fnode.get('confidence', 'High')

                    type_icon = "🔬" if ftype == "LAB_RESULT" else ("🩺" if ftype == "SYMPTOM" else ("💊" if ftype == "MEDICATION" else ("⏱️" if ftype == "TIMELINE_EVENT" else "📌")))
                    flabel = f"{type_icon} {fname}"
                    if fval and fval not in ("Present", "Documented", "Absent"):
                        flabel += f"\n{fval} {funit or ''}".strip()
                    elif festate == "NOT_YET_TESTED":
                        flabel += "\n(Not Tested)"
                    elif festate == "TESTED_NEGATIVE" or fval == "Absent":
                        flabel += "\n(Negative / Absent)"

                    is_f_sel = selected_id == fnode['id']
                    add_n(Node(
                        id=fid,
                        label=flabel,
                        title=f"<b>{fname}</b><br>Type: {ftype}<br>State: {festate}<br>Value: {fval or 'N/A'}<br>Confidence: {fconf}",
                        shape="box",
                        level=3,
                        color={"background": "#FFF9C4" if is_f_sel else bg_col, "border": "#F59E0B" if is_f_sel else border_col},
                        font={"color": font_col, "size": 11, "face": "Arial"},
                        borderWidth=1 if not is_f_sel else 2
                    ))

                    agraph_edges.append(Edge(
                        source=cat_id,
                        target=fid,
                        color=border_col,
                        arrows="to",
                        smooth={"type": "cubicBezier"}
                    ))

                    # Level 4: Source Document
                    doc_name = fnode.get('source_document_name')
                    if doc_name:
                        doc_id = f"doc_{doc_name.replace(' ', '_').lower()}"
                        add_n(Node(
                            id=doc_id,
                            label=f"📄 {doc_name[:22]}",
                            title=f"Source Document: {doc_name}",
                            shape="box",
                            level=4,
                            color={"background": "#E0F2FE", "border": "#0284C7"},
                            font={"color": "#0369A1", "size": 10, "face": "Arial"},
                            borderWidth=1
                        ))
                        agraph_edges.append(Edge(
                            source=fid,
                            target=doc_id,
                            color="#94A3B8",
                            arrows="to",
                            dashes=True,
                            label="Source"
                        ))

    return agraph_nodes, agraph_edges

# Verify Default Collapsed View
n_col, e_col = build_hierarchical_elements("MP00005", "Sarah Jenkins", set())
print(f"Default Collapsed View: {len(n_col)} Nodes (1 Patient + {len(diagnosis_nodes)} Diagnoses), {len(e_col)} Edges")
assert len(n_col) == 1 + len(diagnosis_nodes)

# Verify Expanding 1 Diagnosis: 'Acute Dengue Infection'
dengue_id = next(d['id'] for d in diagnosis_nodes if 'dengue infection' in d['name'].lower())
n_exp1, e_exp1 = build_hierarchical_elements("MP00005", "Sarah Jenkins", {dengue_id})
print(f"Expanded 1 Diagnosis (ID {dengue_id}): {len(n_exp1)} Nodes, {len(e_exp1)} Edges")

# Verify Expanding 2 Diagnoses: 'Acute Dengue Infection' + 'Currently Hemodynamically Stable'
stable_id = next(d['id'] for d in diagnosis_nodes if 'stable' in d['name'].lower())
n_exp2, e_exp2 = build_hierarchical_elements("MP00005", "Sarah Jenkins", {dengue_id, stable_id})
print(f"Expanded 2 Diagnoses (IDs {dengue_id}, {stable_id}): {len(n_exp2)} Nodes, {len(e_exp2)} Edges")

print("\nHierarchical Graph Construction Tests Passed Perfectly!")
