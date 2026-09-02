import requests
import json
import sqlite3
import sys
import os
sys.path.insert(0, os.path.abspath('.'))
from streamlit_agraph import Node, Edge, Config

# Fetch graph data for MP00005
res = requests.get('http://127.0.0.1:8000/patients/MP00005/evidence/graph')
graph_data = res.json()
all_nodes = graph_data['nodes']
all_edges = graph_data['edges']

node_map = {n['id']: n for n in all_nodes}
diagnosis_nodes = [n for n in all_nodes if n.get('evidence_type') == 'DIAGNOSIS']
document_nodes = [n for n in all_nodes if n.get('evidence_type') == 'DOCUMENT']

print(f"Total Diagnoses: {len(diagnosis_nodes)}")
for d in diagnosis_nodes:
    print(f"  ID {d['id']}: {d['name']} (Conf: {d.get('confidence')})")

def build_hierarchical_graph(expanded_diag_ids, selected_node_id=None):
    nodes = []
    edges = []
    seen_node_ids = set()

    def add_node(n_obj):
        if n_obj.id not in seen_node_ids:
            seen_node_ids.add(n_obj.id)
            nodes.append(n_obj)

    # 1. Level 0: Patient Root Node
    root_id = "patient_root"
    add_node(Node(
        id=root_id,
        label="Patient: Sarah Jenkins\n[MP00005]",
        shape="box",
        level=0,
        color={"background": "#EEF2FF", "border": "#4F46E5", "highlight": {"background": "#E0E7FF", "border": "#4338CA"}},
        font={"color": "#312E81", "size": 14, "bold": True, "face": "Arial"},
        borderWidth=2,
        shadow=True
    ))

    # 2. Level 1: Diagnosis Nodes
    for d in diagnosis_nodes:
        did = str(d['id'])
        dname = d.get('name', 'Diagnosis')
        conf = d.get('confidence', 'High')
        conf_str = f"{conf}%" if str(conf).isdigit() else str(conf)
        is_exp = d['id'] in expanded_diag_ids
        exp_indicator = "[-]" if is_exp else "[+]"
        dlabel = f"{dname}\nConf: {conf_str}  {exp_indicator}"

        is_selected = selected_node_id == d['id']
        add_node(Node(
            id=did,
            label=dlabel,
            shape="box",
            level=1,
            color={"background": "#FFE4E6" if not is_selected else "#FFF9C4", "border": "#E11D48" if not is_selected else "#D97706"},
            font={"color": "#9F1239", "size": 12, "bold": True, "face": "Arial"},
            borderWidth=2,
            shadow=True
        ))

        edges.append(Edge(
            source=root_id,
            target=did,
            color="#94A3B8",
            arrows="to",
            smooth={"type": "cubicBezier", "roundness": 0.5}
        ))

        # 3. If Diagnosis is expanded, build Level 2 (Categories), Level 3 (Evidence), Level 4 (Source)
        if is_exp:
            # Find all connected edges
            connected_edges = [e for e in all_edges if e['target_node_id'] == d['id'] or e['source_node_id'] == d['id']]
            
            sup_findings = []
            con_findings = []
            mis_findings = []
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
                    mis_findings.append((fnode, e))
                elif rel == 'CONTRADICTS':
                    con_findings.append((fnode, e))
                else:
                    sup_findings.append((fnode, e))

            # Helper to add category branch
            categories = [
                ("Supporting", sup_findings, "#DCFCE7", "#16A34A", "#166534", "cat_sup"),
                ("Contradicting", con_findings, "#FEE2E2", "#DC2626", "#991B1B", "cat_con"),
                ("Missing / Needed", mis_findings, "#FEF3C7", "#D97706", "#92400E", "cat_mis")
            ]

            for cat_title, findings_list, bg_col, border_col, font_col, cat_prefix in categories:
                if not findings_list:
                    continue
                
                cat_id = f"{cat_prefix}_{d['id']}"
                add_node(Node(
                    id=cat_id,
                    label=f"{cat_title}\n({len(findings_list)})",
                    shape="box",
                    level=2,
                    color={"background": bg_col, "border": border_col},
                    font={"color": font_col, "size": 11, "bold": True, "face": "Arial"},
                    borderWidth=1.5
                ))

                edges.append(Edge(
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

                    flabel = f"[{ftype[:3]}] {fname}"
                    if fval and fval not in ("Present", "Documented"):
                        flabel += f"\n{fval} {funit or ''}".strip()
                    elif festate == "NOT_YET_TESTED":
                        flabel += "\n(Not Tested)"
                    elif festate == "TESTED_NEGATIVE":
                        flabel += "\n(Absent/Neg)"

                    add_node(Node(
                        id=fid,
                        label=flabel,
                        shape="box",
                        level=3,
                        color={"background": bg_col, "border": border_col},
                        font={"color": font_col, "size": 11, "face": "Arial"},
                        borderWidth=1
                    ))

                    edges.append(Edge(
                        source=cat_id,
                        target=fid,
                        color=border_col,
                        arrows="to",
                        smooth={"type": "cubicBezier"}
                    ))

                    # Level 4: Source Document
                    doc_name = fnode.get('source_document_name')
                    if doc_name:
                        doc_id = f"doc_{doc_name.replace(' ', '_')}"
                        add_node(Node(
                            id=doc_id,
                            label=f"Source:\n{doc_name[:20]}",
                            shape="box",
                            level=4,
                            color={"background": "#F0FDF4", "border": "#059669"},
                            font={"color": "#065F46", "size": 10, "face": "Arial"},
                            borderWidth=1
                        ))
                        edges.append(Edge(
                            source=fid,
                            target=doc_id,
                            color="#94A3B8",
                            arrows="to",
                            dashes=True
                        ))

    return nodes, edges

# Test Initial Collapsed View (Empty expanded set)
init_nodes, init_edges = build_hierarchical_graph(set())
print(f"\nInitial Collapsed Graph: {len(init_nodes)} Nodes, {len(init_edges)} Edges")
for n in init_nodes:
    print(f"  Level {n.to_dict().get('level')}: {n.to_dict().get('label')[:30]}")

# Test Expanding One Diagnosis (e.g. Acute Dengue Infection)
dengue_diag = next(d for d in diagnosis_nodes if 'dengue' in d['name'].lower())
exp_nodes, exp_edges = build_hierarchical_graph({dengue_diag['id']})
print(f"\nExpanded Graph for '{dengue_diag['name']}': {len(exp_nodes)} Nodes, {len(exp_edges)} Edges")
levels = {}
for n in exp_nodes:
    lvl = n.to_dict().get('level')
    levels[lvl] = levels.get(lvl, 0) + 1
print(f"Node counts by level: {levels}")
