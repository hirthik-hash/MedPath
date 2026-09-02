"""
MedPath - Healthcare Coordination Platform

Streamlit frontend.

Run:
    streamlit run medpath_app.py

Authentication:
    Streamlit -> api_client.py -> FastAPI -> SQLite

The medical-record prototype data is currently stored locally in:
    data/-s.json

Authentication/user data is NOT read from users.json.
"""

import streamlit as st

import json
import os
import re
import uuid
from datetime import datetime, date
import requests as http_requests
from api_client import (
    register_user,
    login_user,
    get_current_user,
    get_patient_evidence,
    get_patient_evidence_graph,
    get_evidence_details,
    ingest_patient_evidence,
)

import graphviz

# ==============================================================
# CONFIG
# ==============================================================

APP_NAME = "MedPath"
APP_ICON = "🩺"
TAGLINE = "Connecting every step of the healthcare journey."

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

PATIENTS_FILE = os.path.join(DATA_DIR, "patients.json")
#medical document storage 

UPLOADS_DIR = os.path.join(DATA_DIR, "uploads") 
ROLES = ["Patient", "Doctor", "CHW"]

TIMELINE_EVENT_TYPES = [
    "Consultation",
    "Symptom",
    "Diagnosis",
    "Laboratory Test",
    "Prescription",
    "Hospital Visit",
    "Follow-up",
    "Medical Document",
]


st.set_page_config(
    page_title=f"{APP_NAME} - Healthcare Coordination",
    page_icon=APP_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)


# ==============================================================
# LOCAL PATIENT DATA HELPERS
# ==============================================================

def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(UPLOADS_DIR, exist_ok=True)

def load_db(path):
    ensure_data_dir()

    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)

        return data if isinstance(data, dict) else {}

    except (json.JSONDecodeError, FileNotFoundError, OSError):
        return {}


def save_db(path, data):
    ensure_data_dir()

    with open(path, "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False,
            default=str,
        )


def new_id(prefix):
    return f"{prefix}{uuid.uuid4().hex[:8].upper()}"


def create_empty_patient_record(
    patient_id,
    name,
    linked_user_id=None,
    date_of_birth=None,
    gender=None,
    phone=None,
):
    return {
        "patient_id": patient_id,
        "linked_user_id": linked_user_id,
        "name": name,
        "date_of_birth": date_of_birth,
        "age": None,
        "gender": gender,
        "bloodGroup": None,
        "phone": phone,
        "symptoms": [],
        "labs": [],
        "medications": [],
        "documents": [],
        "timeline": [],
        "appointments": [],
        "additional": {},
        "createdAt": datetime.now().isoformat(),
    }


def ensure_patient_record(user):
    """
    Make sure the local prototype patient record exists.

    Authentication comes from FastAPI/SQLite.
    The medical-record prototype currently uses patients.json.
    """

    if not user:
        return None

    patient_id = user.get("patient_id")

    if not patient_id:
        return None

    patients = load_db(PATIENTS_FILE)

    if patient_id not in patients:
        patients[patient_id] = create_empty_patient_record(
            patient_id=patient_id,
            name=user.get("full_name", "Patient"),
            linked_user_id=user.get("user_id"),
            date_of_birth=user.get("date_of_birth"),
            gender=user.get("gender"),
            phone=user.get("mobile"),
        )

        save_db(PATIENTS_FILE, patients)

    return patients[patient_id]


def get_patient_record(patient_id):
    if not patient_id:
        return None, {}

    patients = load_db(PATIENTS_FILE)

    patient = patients.get(patient_id)

    return patient, patients


def save_patient_record(patient_id, record, patients=None):
    if not patient_id:
        return

    if patients is None:
        patients = load_db(PATIENTS_FILE)

    patients[patient_id] = record

    save_db(PATIENTS_FILE, patients)


def clean_atomic_name(text: str, max_words: int = 4) -> str:
    """
    Cleans a clinical finding or entity text to ensure it is atomic, title-cased,
    and strictly concise (<= 3-4 words max).
    """
    if not text:
        return "Clinical Finding"
    # Split on em dash, en dash, colon, semicolon, parenthesis, or square brackets
    cleaned = re.split(r'[—–:\(\[\{;]', str(text))[0].strip()
    cleaned = re.sub(r'[\(\[\{].*?[\)\]\}]', '', cleaned).strip()
    cleaned = re.sub(r'[:;,.]+$', '', cleaned).strip()
    cleaned = re.sub(r'(?i)^(only|presence of|documented|the)\s+', '', cleaned).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)
    words = cleaned.split()
    if not words:
        return "Clinical Finding"
    if len(words) > max_words:
        cleaned = " ".join(words[:max_words])
    return cleaned.title()


def normalize_confidence(conf_val, likelihood=None, is_not_tested=False, is_direct_lab=False, is_negative=False):
    """
    Calculates a realistic, varied confidence rating:
    - NOT_YET_TESTED nodes (missing / recommended) -> 'Medium' or 'Low'
    - Confirmed direct lab measurements with explicit values -> 'High'
    - Qualitative / differential findings -> follows LLM assessment ('High', 'Medium', 'Low')
    """
    if is_not_tested:
        return "Medium"

    if conf_val is not None:
        try:
            val = float(conf_val)
            if val > 1.0:
                if val >= 85:
                    return "High"
                elif val >= 60:
                    return "Medium"
                else:
                    return "Low"
            else:
                if val >= 0.85:
                    return "High"
                elif val >= 0.60:
                    return "Medium"
                else:
                    return "Low"
        except (ValueError, TypeError):
            c_str = str(conf_val).strip()
            if c_str.capitalize() in ["High", "Medium", "Low"]:
                return c_str.capitalize()

    if likelihood:
        l_str = str(likelihood).strip().capitalize()
        if l_str in ["High", "Medium", "Low"]:
            return l_str

    if is_direct_lab:
        return "High"

    return "Medium"


def parse_atomic_findings(raw_ev, pattern=None, source_doc_id=None, source_doc_name=None):
    """
    Extracts atomic, discrete clinical findings from raw prose or n8n evidence strings.
    Converts complex sentences with missing tests or compound findings into individual EvidenceNode dicts.
    """
    findings = []
    text = str(raw_ev).strip()
    if not text:
        return findings

    pat_conf = pattern.get("confidence") if isinstance(pattern, dict) else None
    pat_like = pattern.get("likelihood") if isinstance(pattern, dict) else None

    # Step 0: Semicolon split
    clauses = [c.strip() for c in re.split(r';', text) if c.strip()]
    if len(clauses) > 1:
        for cl in clauses:
            findings.extend(parse_atomic_findings(cl, pattern, source_doc_id, source_doc_name))
        return findings

    # Step 1: Document Reference Check
    if any(w in text.lower() for w in [".pdf", ".docx", "report dated", "laboratory report"]):
        doc_name = "CBC Report" if "cbc" in text.lower() else "Laboratory Report"
        date_match = re.search(r'\d{1,2}\s+[A-Za-z]{3}\s+\d{4}', text)
        val = date_match.group(0) if date_match else "Documented"
        findings.append({
            "name": doc_name,
            "value": val,
            "unit": None,
            "evidence_type": "DOCUMENT",
            "evidence_state": "PRESENT",
            "confidence": normalize_confidence(pat_conf, pat_like)
        })
        return findings

    # Step 2: Clinical Impression
    if text.lower().startswith("impression:") or "clinical impression:" in text.lower():
        clean = re.sub(r'(?i)^(impression|clinical impression):\s*', '', text)
        clean = re.sub(r'\s*\([^)]*\)', '', clean)
        sub_parts = [p.strip() for p in re.split(r'\bwith\b|\band\b|(?<!\d),(?!\d)', clean) if p.strip()]
        for sp in sub_parts:
            atomic_name = clean_atomic_name(sp, max_words=4)
            findings.append({
                "name": atomic_name,
                "value": "Clinical Impression",
                "unit": None,
                "evidence_type": "DIAGNOSIS",
                "evidence_state": "PRESENT",
                "confidence": normalize_confidence(pat_conf, pat_like)
            })
        if findings:
            return findings

    # Step 3: Missing / Not Tested Tests in prose
    if any(k in text.lower() for k in [
        "not documented", "not tested", "not recorded", "not evaluated",
        "no documented", "insufficient data", "not available"
    ]):
        clean_text = re.sub(r'\s*\([^)]*\)', '', text)
        clean = re.sub(r'(?i)^(no documented|there is no documented|not documented|no|history of)\s+', '', clean_text)
        clean = re.sub(r'(?i)\s+(are|is|were)\s+not\s+(documented|tested|recorded|evaluated).*$', '', clean)
        clean = re.sub(r'(?i)\s+(in the record|in record|in medical record).*$', '', clean)
        clean = re.sub(r'(?i)^only\s+', '', clean)

        parts = [p.strip() for p in re.split(r'(?<!\d),(?!\d)|\band\b|\bor\b', clean) if p.strip()]
        for p in parts:
            if not p or len(p) < 2 or p.lower() in ["the", "a", "and", "or", "in", "value is present", "present"]:
                continue
            if "is present" in p.lower() or "present" in p.lower():
                h_name = clean_atomic_name(p.replace("value is present", "").replace("is present", ""), max_words=3)
                findings.append({
                    "name": h_name,
                    "value": "Present",
                    "unit": None,
                    "evidence_type": "LAB_RESULT",
                    "evidence_state": "PRESENT",
                    "confidence": normalize_confidence(pat_conf, pat_like)
                })
                continue

            atomic_name = clean_atomic_name(p, max_words=4)
            p_lower = p.lower()
            if any(w in p_lower for w in ["indices", "count", "studies", "test", "hemoglobin", "lab", "panel", "serum", "trend", "rbc"]):
                etype = "LAB_RESULT"
            elif any(w in p_lower for w in ["symptom", "pain", "fever", "bleeding", "headache", "vital"]):
                etype = "SYMPTOM"
            elif any(w in p_lower for w in ["medication", "drug", "therapy"]):
                etype = "MEDICATION"
            else:
                etype = "CLINICAL_NOTE"

            findings.append({
                "name": atomic_name,
                "value": "Not Documented",
                "unit": None,
                "evidence_type": etype,
                "evidence_state": "NOT_YET_TESTED",
                "confidence": normalize_confidence(pat_conf, pat_like, is_not_tested=True)
            })
        if findings:
            return findings

    # Step 4: Qualitative Lab/Test (e.g. Dengue NS1 Antigen: POSITIVE)
    qual_match = re.search(
        r'([A-Za-z][A-Za-z0-9\s\-/]*?)\s*[:=]\s*(POSITIVE|NEGATIVE|REACTIVE|NON-REACTIVE|DETECTED|NOT DETECTED|NORMAL|LOW|HIGH)',
        text,
        re.IGNORECASE
    )
    if qual_match:
        raw_name = qual_match.group(1).strip()
        val = qual_match.group(2).strip().capitalize()
        atomic_name = clean_atomic_name(raw_name, max_words=4)
        state = "TESTED_NEGATIVE" if val.upper() in ["NEGATIVE", "NOT DETECTED", "NON-REACTIVE"] else "PRESENT"
        findings.append({
            "name": atomic_name,
            "value": val,
            "unit": None,
            "evidence_type": "LAB_RESULT",
            "evidence_state": state,
            "confidence": normalize_confidence(pat_conf, pat_like, is_direct_lab=True)
        })
        return findings

    # Step 5: Negative Symptoms / Findings
    if any(k in text.lower() for k in [
        "no chest pain", "no shortness of breath", "no parasites", "tested_negative",
        "negative for", "absent", "denies", "denied"
    ]):
        neg_parts = [p.strip() for p in re.split(r'\band\b|(?<!\d),(?!\d)', text) if p.strip()]
        for np in neg_parts:
            np_lower = np.lower()
            if "malaria" in np_lower:
                findings.append({
                    "name": "Malaria Smear",
                    "value": "Negative",
                    "unit": None,
                    "evidence_type": "LAB_RESULT",
                    "evidence_state": "TESTED_NEGATIVE",
                    "confidence": normalize_confidence(pat_conf, pat_like, is_direct_lab=True)
                })
            elif "chest pain" in np_lower:
                findings.append({
                    "name": "Chest Pain",
                    "value": "Absent",
                    "unit": None,
                    "evidence_type": "SYMPTOM",
                    "evidence_state": "TESTED_NEGATIVE",
                    "confidence": normalize_confidence(pat_conf, pat_like, is_negative=True)
                })
            elif "shortness of breath" in np_lower or "breath" in np_lower:
                findings.append({
                    "name": "Shortness Of Breath",
                    "value": "Absent",
                    "unit": None,
                    "evidence_type": "SYMPTOM",
                    "evidence_state": "TESTED_NEGATIVE",
                    "confidence": normalize_confidence(pat_conf, pat_like, is_negative=True)
                })
            else:
                np_clean = re.sub(r'(?i)^(no|denies|denied|negative for)\s+', '', np)
                np_clean = re.sub(r'(?i)\s+(reported|seen|documented|present).*$', '', np_clean)
                if np_clean:
                    atomic_name = clean_atomic_name(np_clean, max_words=4)
                    etype = "LAB_RESULT" if any(w in np_lower for w in ["smear", "antigen", "test", "parasite"]) else "SYMPTOM"
                    findings.append({
                        "name": atomic_name,
                        "value": "Absent",
                        "unit": None,
                        "evidence_type": etype,
                        "evidence_state": "TESTED_NEGATIVE",
                        "confidence": normalize_confidence(pat_conf, pat_like, is_negative=True)
                    })
        if findings:
            return findings

    # Step 6: Quantitative Lab Results / Vitals
    non_duration_text = re.sub(r'(?i)\s+for\s+\d+\s+days?', '', text)
    multi_items = [item.strip() for item in re.split(r'\band\b|(?<!\d),(?!\d)', non_duration_text) if re.search(r'\d', item)]
    if not multi_items:
        multi_items = [text]

    matched_quantitative = False
    for item in multi_items:
        lab_match = re.search(
            r'([A-Za-z][A-Za-z0-9\s\-/]*?)\s*(?:is|was|measured|of)?\s*[:=]?\s+([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?(?:/[0-9]{1,3}(?:,[0-9]{3})*)?)\s*([a-zA-Z/%μuµLgdmgkgh\^]+(?:\s*\(.*?\))?)?',
            item
        )
        if lab_match:
            raw_name = lab_match.group(1).strip()
            val = lab_match.group(2).strip()
            unit = lab_match.group(3).strip() if lab_match.group(3) else None
            if unit:
                unit = re.sub(r'\s*\([^)]*\)', '', unit).strip()

            if unit and unit.lower() in ["days", "day", "weeks", "week", "months", "years"]:
                continue

            raw_name = re.sub(r'(?i)^(high|low|mild|severe)\s+', '', raw_name)
            raw_name = re.sub(r'(?i)\s+(with|for|setting|below|above).*$', '', raw_name)
            raw_name = re.sub(r'(?i)^only\s+', '', raw_name)

            atomic_name = clean_atomic_name(raw_name, max_words=4)
            name_lower = atomic_name.lower()

            if "fever" in name_lower or "temp" in name_lower:
                etype = "SYMPTOM"
            else:
                etype = "LAB_RESULT"

            findings.append({
                "name": atomic_name,
                "value": val,
                "unit": unit,
                "evidence_type": etype,
                "evidence_state": "PRESENT",
                "confidence": normalize_confidence(pat_conf, pat_like, is_direct_lab=True)
            })
            matched_quantitative = True

    if matched_quantitative:
        return findings

    # Step 7: Symptoms
    if any(s in text.lower() for s in ["fever", "headache", "pain", "fatigue", "aches", "cough", "nausea", "vomiting", "rash"]):
        s_clean = re.sub(r'(?i)^chief complaint:\s*', '', text)
        s_parts = re.split(r'(?i)\s+with\s+|(?<!\d),(?!\d)|\band\b', s_clean)
        for sp in s_parts:
            sp = sp.strip()
            if not sp or sp.lower() in ["the", "a", "in", "for", "approximately"]:
                continue
            temp_match = re.search(r'(?i)fever.*?([0-9]+\.?[0-9]*)\s*([cCfF])', sp)
            if temp_match:
                findings.append({
                    "name": "Fever",
                    "value": temp_match.group(1),
                    "unit": temp_match.group(2).upper(),
                    "evidence_type": "SYMPTOM",
                    "evidence_state": "PRESENT",
                    "confidence": normalize_confidence(pat_conf, pat_like, is_direct_lab=True)
                })
            else:
                dur_match = re.search(r'(?i)(\d+)\s+days?', sp)
                val_str = f"Present ({dur_match.group(0)})" if dur_match else "Present"
                sp_clean = re.sub(r'(?i)\s+(for\s+approximately\s+\d+\s+days?|for\s+\d+\s+days?).*$', '', sp)
                sp_clean = re.sub(r'(?i)\s+(in the setting of|setting).*$', '', sp_clean)
                atomic_name = clean_atomic_name(sp_clean, max_words=4)
                findings.append({
                    "name": atomic_name,
                    "value": val_str,
                    "unit": None,
                    "evidence_type": "SYMPTOM",
                    "evidence_state": "PRESENT",
                    "confidence": normalize_confidence(pat_conf, pat_like)
                })
        if findings:
            return findings

    # Step 8: Fallback
    atomic_name = clean_atomic_name(text, max_words=4)
    findings.append({
        "name": atomic_name,
        "value": "Documented",
        "unit": None,
        "evidence_type": "CLINICAL_NOTE",
        "evidence_state": "PRESENT",
        "confidence": normalize_confidence(pat_conf, pat_like)
    })
    return findings


def sync_patient_evidence_to_backend(patient_id, record, n8n_result=None, source_doc_id=None, source_doc_name=None):
    """
    Syncs structured clinical findings, symptoms, laboratory test results, medications,
    documents, timeline events, and n8n AI analysis into the Clinical Evidence Graph.
    Atomizes all evidence into concise nodes with discrete values, units, states, and
    calibrated confidence levels.
    """
    if not patient_id or not record:
        return

    nodes = []
    edges = []
    seen_nodes = set()

    def add_node(node_dict):
        key = (node_dict["evidence_type"], node_dict["name"], str(node_dict.get("value")), node_dict.get("evidence_state"))
        if key not in seen_nodes:
            seen_nodes.add(key)
            nodes.append(node_dict)
            return node_dict["id"]
        for n in nodes:
            if (n["evidence_type"], n["name"], str(n.get("value")), n.get("evidence_state")) == key:
                return n["id"]
        return node_dict["id"]

    # 1. Symptoms
    for sym in record.get("symptoms", []):
        if isinstance(sym, dict):
            sname = clean_atomic_name(sym.get("name") or sym.get("symptom") or "Symptom", max_words=4)
            sval = sym.get("severity") or sym.get("status") or "Present"
            sdate = sym.get("onsetDate") or sym.get("date")
        else:
            sname = clean_atomic_name(str(sym), max_words=4)
            sval = "Present"
            sdate = None

        add_node({
            "id": f"sym_{sname}_{sval}",
            "evidence_type": "SYMPTOM",
            "name": sname,
            "value": sval,
            "unit": None,
            "date": sdate,
            "source_type": "MANUAL_ENTRY",
            "confidence": "High",
            "verification_status": "Confirmed",
            "evidence_state": "PRESENT"
        })

    # 2. Labs
    for lab in record.get("labs", []):
        if isinstance(lab, dict):
            lname = clean_atomic_name(lab.get("name") or lab.get("test") or "Lab Test", max_words=4)
            lval = str(lab.get("value") or lab.get("result") or "")
            lunit = lab.get("unit")
            ldate = lab.get("date")
        else:
            lname = clean_atomic_name(str(lab), max_words=4)
            lval = None
            lunit = None
            ldate = None

        add_node({
            "id": f"lab_{lname}_{lval}",
            "evidence_type": "LAB_RESULT",
            "name": lname,
            "value": lval,
            "unit": lunit,
            "date": ldate,
            "source_document_id": source_doc_id,
            "source_document_name": source_doc_name,
            "source_type": "DOCUMENT_EXTRACT" if source_doc_name else "MANUAL_ENTRY",
            "confidence": "High",
            "verification_status": "Confirmed",
            "evidence_state": "PRESENT"
        })

    # 3. Medications
    for med in record.get("medications", []):
        if isinstance(med, dict):
            mname = clean_atomic_name(med.get("name") or med.get("medication") or "Medication", max_words=4)
            mval = med.get("dosage") or med.get("frequency")
            mdate = med.get("startDate") or med.get("date")
        else:
            mname = clean_atomic_name(str(med), max_words=4)
            mval = None
            mdate = None

        add_node({
            "id": f"med_{mname}_{mval}",
            "evidence_type": "MEDICATION",
            "name": mname,
            "value": mval,
            "unit": None,
            "date": mdate,
            "source_type": "MANUAL_ENTRY",
            "confidence": "High",
            "verification_status": "Confirmed",
            "evidence_state": "PRESENT"
        })

    # 4. Documents
    for doc in record.get("documents", []):
        if isinstance(doc, dict):
            dname = clean_atomic_name(doc.get("name") or "Medical Document", max_words=4)
            did = doc.get("id")
            ddate = doc.get("uploadedAt")

            add_node({
                "id": f"doc_{did or dname}",
                "evidence_type": "DOCUMENT",
                "name": dname,
                "value": doc.get("type", "PDF"),
                "unit": None,
                "date": ddate,
                "source_document_id": did,
                "source_document_name": dname,
                "source_type": "DOCUMENT_EXTRACT",
                "confidence": "High",
                "verification_status": "Confirmed",
                "evidence_state": "PRESENT"
            })

    # 5. Timeline Events
    for evt in record.get("timeline", []):
        if isinstance(evt, dict):
            raw_title = evt.get("title") or ""
            raw_desc = evt.get("description") or ""
            edate = evt.get("date")
            raw_combined = raw_title or raw_desc or "Timeline Event"

            if "Evidence:" in raw_combined:
                parts = raw_combined.split("Evidence:", 1)
                event_name_raw = parts[0].strip()
                embedded_ev = parts[1].strip()
            elif "evidence:" in raw_combined:
                parts = raw_combined.split("evidence:", 1)
                event_name_raw = parts[0].strip()
                embedded_ev = parts[1].strip()
            else:
                event_name_raw = raw_combined
                embedded_ev = evt.get("evidence")

            if "uploaded medical document:" in event_name_raw.lower():
                doc_filename = event_name_raw.lower().split("uploaded medical document:")[-1].strip()
                ename = "Document Uploaded"
                eval_val = doc_filename or "Document Upload"
            else:
                ename = clean_atomic_name(event_name_raw, max_words=4)
                eval_val = str(evt.get("type") or "Timeline Event")

            evt_node_id = f"evt_{ename}_{edate or ''}"
            actual_evt_id = add_node({
                "id": evt_node_id,
                "evidence_type": "TIMELINE_EVENT",
                "name": ename,
                "value": eval_val,
                "unit": None,
                "date": edate,
                "source_type": "MANUAL_ENTRY" if evt.get("source") != "ai" else "N8N_AI_ANALYSIS",
                "confidence": "High",
                "verification_status": "Confirmed",
                "evidence_state": "PRESENT"
            })

            # If timeline event contains embedded evidence, parse it into atomic finding nodes
            if embedded_ev and isinstance(embedded_ev, str) and len(embedded_ev.strip()) > 2:
                embedded_findings = parse_atomic_findings(embedded_ev, source_doc_id=source_doc_id, source_doc_name=source_doc_name)
                for f in embedded_findings:
                    ev_node_id = f"ev_{f['name']}_{f.get('value')}_{f.get('evidence_state')}"
                    actual_ev_id = add_node({
                        "id": ev_node_id,
                        "evidence_type": f["evidence_type"],
                        "name": f["name"],
                        "value": f["value"],
                        "unit": f.get("unit"),
                        "date": edate,
                        "source_document_id": source_doc_id,
                        "source_document_name": source_doc_name,
                        "source_type": "DOCUMENT_EXTRACT" if source_doc_name else "N8N_AI_ANALYSIS",
                        "confidence": f["confidence"],
                        "verification_status": "Confirmed",
                        "evidence_state": f["evidence_state"]
                    })
                    edges.append({
                        "source_node_id": actual_ev_id,
                        "target_node_id": actual_evt_id,
                        "relationship_type": "DERIVED_FROM" if f["evidence_state"] == "PRESENT" else "ASSOCIATED_WITH",
                        "confidence": f["confidence"]
                    })

    # 6. Structured n8n AI Analysis
    n8n_dict_list = []
    if isinstance(n8n_result, dict):
        n8n_dict_list.append((n8n_result, source_doc_id, source_doc_name))
    else:
        for doc in record.get("documents", []):
            if isinstance(doc, dict) and isinstance(doc.get("analysis_data"), dict):
                n8n_dict_list.append((doc["analysis_data"], doc.get("id"), doc.get("name")))

    for n8n_item, s_id, s_name in n8n_dict_list:
        n8n_dict = n8n_item
        if isinstance(n8n_item.get("text"), str) and n8n_item.get("text").strip().startswith("{"):
            try:
                parsed_text = json.loads(n8n_item["text"])
                if isinstance(parsed_text, dict):
                    n8n_dict = parsed_text
            except Exception:
                pass

        patterns = n8n_dict.get("patterns", []) or n8n_dict.get("clinical_patterns", [])
        if isinstance(patterns, list):
            for pat in patterns:
                if isinstance(pat, dict):
                    raw_pname = pat.get("name") or "Clinical Pattern"
                    atomic_pname = clean_atomic_name(raw_pname, max_words=4)
                    pat_conf = normalize_confidence(pat.get("confidence"), pat.get("likelihood"))
                    pnode_id = f"diag_{atomic_pname}"

                    actual_pnode_id = add_node({
                        "id": pnode_id,
                        "evidence_type": "DIAGNOSIS",
                        "name": atomic_pname,
                        "value": str(pat.get("description") or pat.get("likelihood") or "Pattern Detected"),
                        "unit": None,
                        "source_document_id": s_id,
                        "source_document_name": s_name,
                        "source_type": "N8N_AI_ANALYSIS",
                        "confidence": pat_conf,
                        "verification_status": "Pending",
                        "evidence_state": "PRESENT"
                    })

                    ev_list = pat.get("evidence", [])
                    if isinstance(ev_list, list):
                        for ev in ev_list:
                            findings = parse_atomic_findings(ev, pattern=pat, source_doc_id=s_id, source_doc_name=s_name)
                            for f in findings:
                                ev_node_id = f"ev_{f['name']}_{f.get('value')}_{f.get('evidence_state')}"
                                actual_ev_id = add_node({
                                    "id": ev_node_id,
                                    "evidence_type": f["evidence_type"],
                                    "name": f["name"],
                                    "value": f["value"],
                                    "unit": f.get("unit"),
                                    "source_document_id": s_id,
                                    "source_document_name": s_name,
                                    "source_type": "N8N_AI_ANALYSIS",
                                    "confidence": f["confidence"],
                                    "verification_status": "Confirmed",
                                    "evidence_state": f["evidence_state"]
                                })

                                if f["evidence_state"] == "NOT_YET_TESTED":
                                    rel_type = "INDICATES"
                                elif f["evidence_state"] == "TESTED_NEGATIVE":
                                    rel_type = "CONTRADICTS" if "unlikely" not in raw_pname.lower() and "ruled out" not in raw_pname.lower() else "SUPPORTS"
                                else:
                                    rel_type = "CONTRADICTS" if "unlikely" in raw_pname.lower() or "ruled out" in raw_pname.lower() else "SUPPORTS"

                                edges.append({
                                    "source_node_id": actual_ev_id,
                                    "target_node_id": actual_pnode_id,
                                    "relationship_type": rel_type,
                                    "confidence": f["confidence"]
                                })

        # Next best test
        next_test = n8n_dict.get("next_best_test")
        if next_test:
            tname = next_test if isinstance(next_test, str) else next_test.get("name", "Recommended Test") if isinstance(next_test, dict) else "Recommended Test"
            atomic_tname = clean_atomic_name(tname, max_words=4)
            add_node({
                "id": f"test_{atomic_tname}",
                "evidence_type": "LAB_RESULT",
                "name": atomic_tname,
                "value": "Recommended Test",
                "unit": None,
                "source_document_id": s_id,
                "source_document_name": s_name,
                "source_type": "N8N_AI_ANALYSIS",
                "confidence": "Medium",
                "verification_status": "Pending",
                "evidence_state": "NOT_YET_TESTED"
            })

    payload = {
        "nodes": nodes,
        "edges": edges
    }

    try:
        ingest_patient_evidence(patient_id, payload)
    except Exception:
        pass



# ==============================================================
# CSS / DESIGN
# ==============================================================

def inject_css():
    st.markdown(
        """
        <style>

        :root {
            --mp-blue: #1565C0;
            --mp-blue-light: #E3F2FD;
            --mp-blue-dark: #0D47A1;
            --mp-grey: #607D8B;
        }

        .mp-logo {
            font-size: 1.9rem;
            font-weight: 800;
            color: var(--mp-blue-dark);
        }

        .mp-tagline {
            color: var(--mp-grey);
            font-size: 0.95rem;
            margin-top: -8px;
        }

        .mp-card {
            background: #ffffff;
            border: 1px solid #E0E6ED;
            border-radius: 14px;
            padding: 1.1rem 1.3rem;
            margin-bottom: 1rem;
            box-shadow: 0 1px 3px rgba(20,40,80,0.06);
        }

        .mp-card h4 {
            margin-top: 0;
            color: var(--mp-blue-dark);
        }

        .mp-empty {
            color: var(--mp-grey);
            font-style: italic;
            padding: 0.6rem 0;
        }

        .mp-badge-high {
            background: #FFEBEE;
            color: #C62828;
            padding: 2px 10px;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 600;
        }

        .mp-badge-medium {
            background: #FFF8E1;
            color: #F9A825;
            padding: 2px 10px;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 600;
        }

        .mp-badge-low {
            background: #E8F5E9;
            color: #2E7D32;
            padding: 2px 10px;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 600;
        }

        .mp-alert {
            background: #FFF3E0;
            border-left: 4px solid #FB8C00;
            padding: 0.7rem 1rem;
            border-radius: 8px;
            margin-bottom: 0.6rem;
        }

        .mp-metric {
            background: var(--mp-blue-light);
            border-radius: 12px;
            padding: 0.9rem 1rem;
            text-align: center;
        }

        .mp-disclaimer {
            font-size: 0.78rem;
            color: var(--mp-grey);
            margin-top: 0.4rem;
        }

        .stButton > button {
            border-radius: 10px;
        }

        </style>
        """,
        unsafe_allow_html=True,
    )


def render_logo(subtitle=None):
    st.markdown(
        f'<div class="mp-logo">{APP_ICON} {APP_NAME}</div>',
        unsafe_allow_html=True,
    )

    if subtitle:
        st.markdown(
            f'<div class="mp-tagline">{subtitle}</div>',
            unsafe_allow_html=True,
        )


def empty_state(text):
    st.markdown(
        f'<div class="mp-empty">{text}</div>',
        unsafe_allow_html=True,
    )


# ==============================================================
# SESSION STATE
# ==============================================================

def init_session_state():

    defaults = {
        "auth": False,
        "user_id": None,
        "user": None,
        "role": None,
        "page": "login",
        "selected_patient_id": None,
        "analyzed": {},
        "chatbot_history": {},
        "symptom_name":"",
        "medication_name":"",
        "medication_dosage":"",
        "medication_frequency":"",
        "appointment_reason":"",
        "timeline_description":""
    }

    for key, value in defaults.items():

        if key not in st.session_state:
            st.session_state[key] = value


# ==============================================================
# CURRENT USER
# ==============================================================

def current_user():

    user_id = st.session_state.get("user_id")

    if not user_id:
        return None

    try:

        response = get_current_user(user_id)

        if response.status_code == 200:

            user = response.json()
        

            if not isinstance(user, dict):
                return None

            # Keep session user synchronized.
            st.session_state.user = user

            # Safely update role.
            role = user.get("role")

            if role:
                st.session_state.role = str(role).capitalize()

            return user

        return None

    except Exception as error:

        st.error(
            f"Unable to connect to the MedPath backend: {error}"
        )

        return None


# ==============================================================
# LOGOUT
# ==============================================================

def logout():

    st.session_state.auth = False
    st.session_state.user_id = None
    st.session_state.user = None
    st.session_state.role = None
    st.session_state.page = "login"
    st.session_state.selected_patient_id = None


# ==============================================================
# AUTHENTICATION
# ==============================================================

def login_page():

    col1, col2, col3 = st.columns([1, 1.3, 1])

    with col2:

        st.write("")
        st.write("")

        render_logo(TAGLINE)

        st.write("")

        tabs = st.tabs(
            [
                "Sign In",
                "Register",
            ]
        )

        # ==========================================================
        # SIGN IN
        # ==========================================================

        with tabs[0]:

            st.markdown("##### I am a")

            role = st.radio(
                "Role",
                ROLES,
                horizontal=True,
                label_visibility="collapsed",
                key="login_role",
            )

            email = st.text_input(
                "Email",
                key="login_email",
                placeholder="Enter your registered email",
            )

            password = st.text_input(
                "Password",
                type="password",
                key="login_password",
                placeholder="Enter your password",
            )

            if st.button(
                "Sign In",
                use_container_width=True,
                type="primary",
            ):

                email = email.strip()

                if not email or not password:

                    st.warning(
                        "Please enter both email and password."
                    )

                    return

                login_data = {
                    "email": email,
                    "password": password,
                    "role": role.lower(),
                }

                try:

                    response = login_user(login_data)

                except Exception as error:

                    st.error(
                        f"Unable to connect to the MedPath backend: {error}"
                    )

                    return

                if response.status_code == 200:

                    try:
                        user = response.json()
                    except Exception:
                        st.error(
                            "The server returned an invalid login response."
                        )
                        return

                    if not isinstance(user, dict):

                        st.error(
                            "The server returned invalid user information."
                        )

                        return

                    user_id = user.get("user_id")
                    returned_role = user.get("role")

                    if not user_id:

                        st.error(
                            "Login succeeded, but the server did not return user_id."
                        )

                        st.json(user)

                        return

                    if not returned_role:

                        st.error(
                            "Login succeeded, but the server did not return role."
                        )

                        st.json(user)

                        return

                    # Store authentication state.
                    st.session_state.auth = True
                    st.session_state.user_id = user_id
                    st.session_state.user = user
                    st.session_state.role = user["role"].strip().upper()

                    if st.session_state.role == "patient":
                        st.session_state.role = "Patient"
                    elif st.session_state.role == "doctor":
                        st.session_state.role = "Doctor"
                    elif st.session_state.role == "chw":
                        st.session_state.role = "CHW"

                    st.session_state.page = "dashboard"

                    # If patient, make sure local prototype record exists.
                    if str(returned_role).lower() == "patient":

                        ensure_patient_record(user)

                    st.success("Login successful!")

                    st.rerun()

                else:

                    try:

                        data = response.json()

                        detail = data.get(
                            "detail",
                            "Login failed.",
                        )

                    except Exception:

                        detail = (
                            f"Login failed "
                            f"(HTTP {response.status_code})."
                        )

                    st.error(detail)

            st.write("")

            c1, c2 = st.columns(2)

            with c1:
                st.caption("Forgot Password?")

            with c2:
                st.caption(
                    "Don't have an account? Use the Register tab →"
                )

        # ==========================================================
        # REGISTER
        # ==========================================================

        with tabs[1]:

            register_form()


# ==============================================================
# REGISTRATION
# ==============================================================

def register_form():

    st.markdown("##### Create an account as")

    role = st.radio(
        "Register role",
        ROLES,
        horizontal=True,
        label_visibility="collapsed",
        key="reg_role",
    )

    with st.form(
        "register_form",
        clear_on_submit=False,
    ):

        full_name = st.text_input(
            "Full Name",
            placeholder="Enter your full name",
        )

        email = st.text_input(
            "Email",
            placeholder="Enter your email",
        )

        mobile = st.text_input(
            "Mobile Number",
            placeholder="Enter your mobile number",
        )

        dob = st.date_input(
            "Date of Birth",
            min_value=date(1900, 1, 1),
            max_value=date.today(),
        )

        extra = {}

        # ----------------------------------------------------------
        # PATIENT
        # ----------------------------------------------------------

        if role == "Patient":

            extra["gender"] = st.selectbox(
                "Gender",
                [
                    "Female",
                    "Male",
                    "Other",
                    "Prefer not to say",
                ],
            )

            extra["profilePhoto"] = st.file_uploader(
                "Profile Photo (optional)",
                type=[
                    "png",
                    "jpg",
                    "jpeg",
                ],
            )

        # ----------------------------------------------------------
        # DOCTOR
        # ----------------------------------------------------------

        elif role == "Doctor":

            extra["specialization"] = st.text_input(
                "Medical Specialization"
            )

            extra["licenseNumber"] = st.text_input(
                "Medical Registration / License Number"
            )

            extra["hospital"] = st.text_input(
                "Hospital / Clinic"
            )

        # ----------------------------------------------------------
        # CHW
        # ----------------------------------------------------------

        elif role == "CHW":

            extra["employeeId"] = st.text_input(
                "CHW / Employee ID"
            )

            extra["organization"] = st.text_input(
                "Organization / Health Center"
            )

            extra["region"] = st.text_input(
                "Area / Region"
            )

        password = st.text_input(
            "Password",
            type="password",
        )

        confirm = st.text_input(
            "Confirm Password",
            type="password",
        )

        submitted = st.form_submit_button(
            f"Create {role} Account",
            use_container_width=True,
            type="primary",
        )

    if not submitted:
        return

    # ==========================================================
    # VALIDATION
    # ==========================================================

    full_name = full_name.strip()
    email = email.strip()
    mobile = mobile.strip()

    if not full_name:

        st.warning("Please enter your full name.")
        return

    if not email:

        st.warning("Please enter your email.")
        return

    if not mobile:

        st.warning("Please enter your mobile number.")
        return

    if not password:

        st.warning("Please enter a password.")
        return

    if password != confirm:

        st.error("Passwords do not match.")
        return

    # ==========================================================
    # API DATA
    # ==========================================================

    user_data = {
        "role": role.lower(),
        "full_name": full_name,
        "email": email,
        "mobile": mobile,
        "date_of_birth": str(dob),
        "password": password,
        "gender": extra.get("gender"),
        "specialization": extra.get("specialization"),
        "license_number": extra.get("licenseNumber"),
        "hospital": extra.get("hospital"),
        "employee_id": extra.get("employeeId"),
        "organization": extra.get("organization"),
        "region": extra.get("region"),
    }

    # ==========================================================
    # REGISTER THROUGH FASTAPI
    # ==========================================================

    try:

        response = register_user(user_data)

    except Exception as error:

        st.error(
            f"Unable to connect to the MedPath backend: {error}"
        )

        return

    # FastAPI may return 200 or 201.
    if response.status_code in (200, 201):

        try:

            user = response.json()

        except Exception:

            st.error(
                "Registration succeeded, but the server returned invalid user data."
            )

            return

        if not isinstance(user, dict):

            st.error(
                "Registration succeeded, but the server returned invalid user information."
            )

            return

        user_id = user.get("user_id")
        returned_role = user.get("role")

        if not user_id:

            st.error(
                "Registration succeeded, but the server did not return user_id."
            )

            st.json(user)

            return

        if not returned_role:

            st.error(
                "Registration succeeded, but the server did not return role."
            )

            st.json(user)

            return

        # ======================================================
        # STORE SESSION
        # ======================================================

        st.session_state.auth = True
        st.session_state.user_id = user_id
        st.session_state.user = user
        st.session_state.role = user["role"].strip().lower()

        if st.session_state.role == "patient":
            st.session_state.role = "Patient"
        elif st.session_state.role == "doctor":
            st.session_state.role = "Doctor"
        elif st.session_state.role == "chw":
            st.session_state.role = "CHW"

        st.session_state.page = "dashboard"

        # ======================================================
        # PATIENT RECORD
        # ======================================================

        if str(returned_role).lower() == "patient":

            patient_id = user.get("patient_id")

            if patient_id:

                ensure_patient_record(user)

            else:

                st.warning(
                    "Account created successfully, but the backend "
                    "did not return a patient_id. Please check backend/main.py."
                )

        st.success(
            f"{role} account created successfully!"
        )

        st.rerun()

    else:

        try:

            data = response.json()

            detail = data.get(
                "detail",
                "Registration failed.",
            )

        except Exception:

            detail = (
                f"Registration failed "
                f"(HTTP {response.status_code})."
            )

        st.error(detail)


# ==============================================================
# PATIENT HEADER
# ==============================================================

def patient_header(user):

    st.markdown(
        f'<div class="mp-logo">{APP_ICON} {APP_NAME}</div>',
        unsafe_allow_html=True,
    )

    full_name = user.get(
        "full_name",
        "Patient",
    )

    st.markdown(
        f"### Welcome, {full_name}"
    )


# ==============================================================
# PATIENT DASHBOARD
# ==============================================================

def patient_dashboard_page(user):

    patient_header(user)

    patient_id = user.get("patient_id")

    if not patient_id:

        st.warning(
            "Your account is registered, but a Patient ID has not been assigned."
        )

        st.info(
            "The backend should create a Patient ID such as MP00001 "
            "when a Patient account is registered."
        )

        st.write("Current account information:")
        st.json(user)

        return

    patient, patients = get_patient_record(patient_id)

    if patient is None:

        patient = create_empty_patient_record(
            patient_id=patient_id,
            name=user.get("full_name", "Patient"),
            linked_user_id=user.get("user_id"),
            date_of_birth=user.get("date_of_birth"),
            gender=user.get("gender"),
            phone=user.get("mobile"),
        )

        patients[patient_id] = patient

        save_db(
            PATIENTS_FILE,
            patients,
        )

    # Safety defaults.
    patient.setdefault("symptoms", [])
    patient.setdefault("labs", [])
    patient.setdefault("documents", [])
    patient.setdefault("timeline", [])
    patient.setdefault("medications", [])
    patient.setdefault("appointments", [])

    has_any_data = bool(
        patient["symptoms"]
        or patient["labs"]
        or patient["documents"]
        or patient["timeline"]
        or patient["medications"]
    )

    if not has_any_data:

        st.markdown(
            '<div class="mp-card">',
            unsafe_allow_html=True,
        )

        st.markdown(
            "#### Your healthcare journey starts here"
        )

        st.write(
            "Add your medical information or upload your "
            "first medical document to begin building your health record."
        )

        c1, c2 = st.columns(2)

        with c1:

            if st.button(
                "Add Medical Information",
                type="primary",
            ):

                st.session_state.page = "My Profile"
                st.rerun()

        with c2:

            if st.button("Upload Document"):

                st.session_state.page = "Documents"
                st.rerun()

        st.markdown(
            "</div>",
            unsafe_allow_html=True,
        )

    # ----------------------------------------------------------
    # METRICS
    # ----------------------------------------------------------

    cols = st.columns(4)

    metric_data = [
        (
            "My Health Journey",
            len(patient["timeline"]),
        ),
        (
            "Timeline",
            len(patient["timeline"]),
        ),
        (
            "Medical Documents",
            len(patient["documents"]),
        ),
        (
            "Symptoms",
            len(patient["symptoms"]),
        ),
    ]

    for column, (label, count) in zip(
        cols,
        metric_data,
    ):

        with column:

            st.markdown(
                f"""
                <div class="mp-metric">
                    <h3>{count}</h3>
                    {label}
                </div>
                """,
                unsafe_allow_html=True,
            )

    st.write("")

    # ----------------------------------------------------------
    # RECENT TIMELINE
    # ----------------------------------------------------------

    col1, col2 = st.columns(2)

    with col1:

        st.markdown(
            '<div class="mp-card"><h4>Recent Timeline</h4>',
            unsafe_allow_html=True,
        )

        if patient["timeline"]:

            events = sorted(
                patient["timeline"],
                key=lambda x: x.get("date", ""),
                reverse=True,
            )

            for event in events[:5]:

                st.write(
                    f"**{event.get('date', '')}** — "
                    f"{event.get('type', '')}: "
                    f"{event.get('description', '')}"
                )

        else:

            empty_state(
                "No medical events yet"
            )

        st.markdown(
            "</div>",
            unsafe_allow_html=True,
        )

    # ----------------------------------------------------------
    # MEDICATIONS
    # ----------------------------------------------------------

    with col2:

        st.markdown(
            '<div class="mp-card"><h4>Medications</h4>',
            unsafe_allow_html=True,
        )

        if patient["medications"]:

            for medication in patient["medications"]:

                st.write(
                    f"- {medication.get('name', '')} "
                    f"({medication.get('dosage', '')}, "
                    f"{medication.get('frequency', '')})"
                )

        else:

            empty_state(
                "No medications on record."
            )

        st.markdown(
            "</div>",
            unsafe_allow_html=True,
        )

    st.write("")
    render_clinical_evidence_dashboard_card(patient_id, patient)



# ==============================================================
# PATIENT PROFILE
# ==============================================================

def patient_profile_page(user):

    patient_header(user)

    patient_id = user.get("patient_id")

    if not patient_id:

        st.error(
            "Patient ID is missing from your account."
        )

        return

    patient, patients = get_patient_record(patient_id)

    if patient is None:

        patient = create_empty_patient_record(
            patient_id,
            user.get("full_name", "Patient"),
            user.get("user_id"),
            user.get("date_of_birth"),
            user.get("gender"),
            user.get("mobile"),
        )

        patients[patient_id] = patient

        save_db(
            PATIENTS_FILE,
            patients,
        )

    st.markdown(
        '<div class="mp-card"><h4>Account Information</h4>',
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)

    c1.write(
        f"**Full Name**\n\n"
        f"{user.get('full_name', '-')}"
    )

    c1.write(
        f"**Email**\n\n"
        f"{user.get('email', '-')}"
    )

    c2.write(
        f"**Mobile**\n\n"
        f"{user.get('mobile', '-')}"
    )

    c2.write(
        f"**Date of Birth**\n\n"
        f"{user.get('date_of_birth', '-')}"
    )

    c3.write(
        f"**Gender**\n\n"
        f"{user.get('gender', '-')}"
    )

    c3.write(
        f"**Patient ID**\n\n"
        f"{patient_id}"
    )

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )

    # ----------------------------------------------------------
    # ADDITIONAL HEALTH INFORMATION
    # ----------------------------------------------------------

    st.markdown(
        '<div class="mp-card"><h4>Additional Health Information</h4>',
        unsafe_allow_html=True,
    )

    additional = patient.get(
        "additional",
        {},
    )

    with st.form(
        "additional_info_form"
    ):

        c1, c2 = st.columns(2)

        blood_group = c1.text_input(
            "Blood Group",
            value=patient.get(
                "bloodGroup"
            ) or "",
        )

        height = c1.text_input(
            "Height",
            value=additional.get(
                "height",
                "",
            ),
        )

        weight = c1.text_input(
            "Weight",
            value=additional.get(
                "weight",
                "",
            ),
        )

        allergies = c2.text_input(
            "Allergies",
            value=additional.get(
                "allergies",
                "",
            ),
        )

        conditions = c2.text_input(
            "Existing Medical Conditions",
            value=additional.get(
                "conditions",
                "",
            ),
        )

        emergency_contact = st.text_input(
            "Emergency Contact",
            value=additional.get(
                "emergency_contact",
                "",
            ),
        )

        emergency_number = st.text_input(
            "Emergency Contact Number",
            value=additional.get(
                "emergency_number",
                "",
            ),
        )

        address = st.text_area(
            "Address",
            value=additional.get(
                "address",
                "",
            ),
        )

        preferred_hospital = st.text_input(
            "Preferred Hospital / Clinic",
            value=additional.get(
                "preferred_hospital",
                "",
            ),
        )

        save_clicked = st.form_submit_button(
            "Save Additional Information",
            type="primary",
        )

    if save_clicked:

        patient["bloodGroup"] = (
            blood_group or None
        )

        patient["additional"] = {
            "height": height,
            "weight": weight,
            "allergies": allergies,
            "conditions": conditions,
            "emergency_contact": emergency_contact,
            "emergency_number": emergency_number,
            "address": address,
            "preferred_hospital": preferred_hospital,
        }

        save_patient_record(
            patient_id,
            patient,
            patients,
        )

        st.success(
            "Additional information saved."
        )

        st.rerun()

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )
def timeline_page(patient_id, editable=True):

    if not patient_id:
        st.error("No patient selected.")
        return

    patient, patients = get_patient_record(patient_id)

    if patient is None:
        empty_state("Patient record not found.")
        return

    patient.setdefault("timeline", [])
    patient.setdefault("documents", [])

    # ==========================================================
    # SYNC AI TIMELINE FROM LATEST N8N ANALYSIS
    # ==========================================================

    for document in patient["documents"]:

        ai_timeline = document.get(
            "ai_timeline",
            []
        )

        if not isinstance(ai_timeline, list):
            continue

        for ai_event in ai_timeline:

            if not isinstance(ai_event, dict):
                continue

            event_date = ai_event.get(
                "date",
                ""
            )

            event_name = ai_event.get(
                "event",
                "AI-generated medical event"
            )

            evidence = ai_event.get(
                "evidence",
                ""
            )

            # Prevent duplicate AI events
            already_exists = any(
                event.get("source") == "ai"
                and event.get("date") == event_date
                and event.get("description") == event_name
                for event in patient["timeline"]
            )

            if already_exists:
                continue

            patient["timeline"].append({

                "id": new_id("EVT"),

                "type": "AI Clinical Event",

                "date": event_date,

                "description": event_name,

                "evidence": evidence,

                "source": "ai",

                "ai_generated": True,
            })

    # ==========================================================
    # SAVE UPDATED TIMELINE
    # ==========================================================

    save_patient_record(
        patient_id,
        patient,
        patients,
    )

    # ==========================================================
    # PAGE HEADER
    # ==========================================================

    st.markdown(
        '<div class="mp-card"><h4>📅 Healthcare Timeline</h4>',
        unsafe_allow_html=True,
    )

    st.caption(
        "A chronological view of the patient's healthcare journey, "
        "including documented and AI-generated clinical events."
    )

    # ==========================================================
    # ADD MANUAL EVENT
    # ==========================================================

    if editable:

        with st.expander("➕ Add Event"):

            with st.form(
                f"add_event_form_{patient_id}",
                clear_on_submit=True
            ):

                event_type = st.selectbox(
                    "Event Type",
                    TIMELINE_EVENT_TYPES,
                )

                event_date = st.date_input(
                    "Date",
                    value=date.today(),
                )

                description = st.text_area(
                    "Description",
                    placeholder="Enter event details",
                )

                submitted = st.form_submit_button(
                    "Add to Timeline",
                    type="primary",
                )

                if submitted:

                    if not description.strip():

                        st.warning(
                            "Please enter a description."
                        )

                    else:

                        patient["timeline"].append({

                            "id": new_id("EVT"),

                            "type": event_type,

                            "date": str(event_date),

                            "description": description.strip(),

                            "source": "manual",

                            "ai_generated": False,
                        })

                        save_patient_record(
                            patient_id,
                            patient,
                            patients,
                        )

                        st.success(
                            "Event added to timeline."
                        )

                        st.rerun()

    # ==========================================================
    # DISPLAY TIMELINE
    # ==========================================================

    if patient["timeline"]:

        events = sorted(
            patient["timeline"],
            key=lambda x: x.get("date", ""),
            reverse=True,
        )

        st.markdown(
            "### Medical History"
        )

        for event in events:

            event_type = event.get(
                "type",
                "Medical Event",
            )

            event_date = event.get(
                "date",
                "",
            )

            description = event.get(
                "description",
                "",
            )

            evidence = event.get(
                "evidence",
                "",
            )

            source = event.get(
                "source",
                "manual",
            )

            # ==================================================
            # EVENT TYPE / SOURCE
            # ==================================================

            if (
                source == "ai"
                or event.get("ai_generated") is True
            ):

                icon = "🤖"
                source_label = "AI-generated"

            elif event_type == "Medical Document":

                icon = "📄"
                source_label = "Document"

            elif event_type == "Symptom":

                icon = "🩺"
                source_label = "Symptom"

            elif event_type == "Medication":

                icon = "💊"
                source_label = "Medication"

            elif event_type == "Laboratory Test":

                icon = "🧪"
                source_label = "Laboratory"

            elif event_type == "Appointment":

                icon = "📅"
                source_label = "Appointment"

            else:

                icon = "📌"
                source_label = "Manual"

            # ==================================================
            # EVENT
            # ==================================================

            st.markdown(
                f"### {icon} {event_type}"
            )

            st.write(
                f"**Date:** {event_date}"
            )

            st.write(
                description
            )

            if evidence:

                st.caption(
                    f"Clinical evidence: {evidence}"
                )

            st.caption(
                f"Source: {source_label}"
            )

            st.markdown("---")

    else:

        empty_state(
            "No medical events yet."
        )

    # ==========================================================
    # CLOSE CARD
    # ==========================================================

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )


def find_n8n_value(data, target_key):
    import json
    if isinstance(data, dict):
        if target_key in data:
            value = data.get(target_key)
            if value is not None:
                if isinstance(value, str):
                    if value.strip():
                        return value
                elif value != "":
                    return value
        for value in data.values():
            result = find_n8n_value(value, target_key)
            if result is not None:
                return result
    elif isinstance(data, list):
        for item in data:
            result = find_n8n_value(item, target_key)
            if result is not None:
                return result
    elif isinstance(data, str):
        text = data.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
            if parsed != data:
                result = find_n8n_value(parsed, target_key)
                if result is not None:
                    return result
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return None


def run_n8n_analysis_for_document(patient_id, document, patient, patients):
    """
    Triggers/retries n8n AI review for a document stored on disk,
    updates document analysis status, and syncs to Evidence Graph.
    """
    import os
    import json
    import requests as http_requests
    from datetime import datetime

    N8N_WEBHOOK_URL = (
        "https://kavyaas.app.n8n.cloud/"
        "webhook/medpath/patient-review"
    )

    filename = document.get("name", "document.pdf")
    rel_path = document.get("file_path", "")

    possible_paths = [
        rel_path,
        os.path.abspath(rel_path) if rel_path else "",
        os.path.join(UPLOADS_DIR, str(patient_id), filename),
        os.path.join("uploads", str(patient_id), filename),
        os.path.join(DATA_DIR, "uploads", str(patient_id), filename),
        filename
    ]

    actual_file_path = None
    for p in possible_paths:
        if p and os.path.exists(p) and os.path.isfile(p):
            actual_file_path = p
            break

    if not actual_file_path:
        st.error(f"❌ Document file '{filename}' was not found on disk. Please re-upload the document.")
        return False

    try:
        with st.spinner(f"⚡ Running n8n AI workflow for {filename}..."):
            with open(actual_file_path, "rb") as pdf_file:
                response = http_requests.post(
                    N8N_WEBHOOK_URL,
                    files={
                        "patient_record": (
                            filename,
                            pdf_file,
                            document.get("type") or "application/pdf"
                        )
                    },
                    timeout=120
                )

        if response.ok:
            raw_response = response.text
            try:
                n8n_result = response.json()
            except (ValueError, json.JSONDecodeError):
                try:
                    n8n_result = json.loads(raw_response)
                except Exception:
                    n8n_result = raw_response

            if isinstance(n8n_result, dict):
                inner_text = n8n_result.get("text")
                if isinstance(inner_text, str) and inner_text.strip().startswith("{"):
                    try:
                        parsed_inner = json.loads(inner_text)
                        if isinstance(parsed_inner, dict):
                            for k, v in parsed_inner.items():
                                if k not in n8n_result or not n8n_result[k]:
                                    n8n_result[k] = v
                    except Exception:
                        pass

            analysis_html = find_n8n_value(n8n_result, "html")
            overall_summary = n8n_result.get("overall_summary") or find_n8n_value(n8n_result, "overall_summary")
            patterns = n8n_result.get("patterns") or n8n_result.get("clinical_patterns") or find_n8n_value(n8n_result, "patterns") or find_n8n_value(n8n_result, "clinical_patterns") or []
            alerts = n8n_result.get("alerts") or find_n8n_value(n8n_result, "alerts") or []
            missing_data = n8n_result.get("missing_data") or find_n8n_value(n8n_result, "missing_data") or []
            care_gaps = n8n_result.get("care_gaps") or find_n8n_value(n8n_result, "care_gaps") or []
            next_best_test = n8n_result.get("next_best_test") or find_n8n_value(n8n_result, "next_best_test")
            timeline = n8n_result.get("timeline") or n8n_result.get("timeline_events") or find_n8n_value(n8n_result, "timeline") or []
            clinical_notes = n8n_result.get("clinical_notes") or find_n8n_value(n8n_result, "clinical_notes")
            evidence_graph = n8n_result.get("evidence_graph") or find_n8n_value(n8n_result, "evidence_graph")

            raw_text = clinical_notes or overall_summary
            if isinstance(raw_text, str):
                analysis_text = raw_text.strip()
            elif isinstance(raw_text, (dict, list)):
                analysis_text = json.dumps(raw_text, indent=2)
            else:
                analysis_text = str(raw_text) if raw_text else None

            analysis_available = bool(
                analysis_html
                or analysis_text
                or overall_summary
                or (isinstance(patterns, list) and len(patterns) > 0)
                or (isinstance(alerts, list) and len(alerts) > 0)
                or timeline
                or clinical_notes
                or evidence_graph
            )

            # Update document record
            document["processing_status"] = "Completed"
            document["analysis_status"] = "Completed" if analysis_available else "Not analyzed"
            document["n8n_status"] = "Connected"
            document["analysis"] = analysis_text
            document["analysis_html"] = analysis_html
            document["analysis_data"] = n8n_result
            document["overall_summary"] = overall_summary
            document["patterns"] = patterns if isinstance(patterns, list) else []
            document["alerts"] = alerts if isinstance(alerts, list) else []
            document["missing_data"] = missing_data if isinstance(missing_data, list) else []
            document["care_gaps"] = care_gaps if isinstance(care_gaps, list) else []
            document["next_best_test"] = next_best_test
            document["clinical_notes"] = clinical_notes
            document["evidence_graph"] = evidence_graph
            document["ai_timeline"] = timeline if isinstance(timeline, list) else []
            document["processedAt"] = datetime.now().isoformat()

            save_patient_record(patient_id, patient, patients)

            # Update session state to ensure fresh data render
            st.session_state["patients"] = patients
            if st.session_state.get("selected_patient_id") == patient_id:
                st.session_state["patient_record"] = patient

            sync_patient_evidence_to_backend(
                patient_id,
                patient,
                n8n_result=n8n_result,
                source_doc_id=document.get("id"),
                source_doc_name=filename
            )

            st.success(f"✅ AI Analysis for '{filename}' completed and Evidence Graph updated!")
            return True
        else:
            st.error(f"AI analysis could not be completed. HTTP {response.status_code}: {response.text}")
            return False

    except Exception as e:
        st.error(f"AI analysis could not be completed: {e}")
        return False


def documents_page(patient_id, editable=True):

    # ==========================================================
    # DEBUG
    # ==========================================================

    st.error("New document_page CODE IS RUNNING")
    st.write("documents_page reached successfully")

    # ==========================================================
    # IMPORTS
    # ==========================================================

    import json
    import os
    from datetime import datetime, date

    # ==========================================================
    # DEFENSIVELY GET PATIENT ID
    # ==========================================================

    if not patient_id:
        patient_id = st.session_state.get(
            "selected_patient_id"
        )

    if not patient_id:
        st.error("Patient ID not found.")
        return

    patient, patients = get_patient_record(
        patient_id
    )

    if patient is None:
        st.error("Patient record not found.")
        return

    # ==========================================================
    # REQUIRED LISTS
    # ==========================================================

    patient.setdefault(
        "documents",
        []
    )

    patient.setdefault(
        "timeline",
        []
    )

    # ==========================================================
    # HELPER:
    # FIND svghtml / svgtext ANYWHERE IN N8N RESPONSE
    #
    # Handles:
    #   - dictionaries
    #   - lists
    #   - JSON strings
    #   - nested JSON strings
    # ==========================================================

    def find_n8n_value(
        data,
        target_key
    ):

        # ------------------------------------------------------
        # DICTIONARY
        # ------------------------------------------------------

        if isinstance(
            data,
            dict
        ):

            # Direct key
            if target_key in data:

                value = data.get(
                    target_key
                )

                if value is not None:

                    if isinstance(
                        value,
                        str
                    ):

                        if value.strip():

                            return value

                    elif value != "":

                        return value

            # Search all dictionary values
            for value in data.values():

                result = find_n8n_value(
                    value,
                    target_key
                )

                if result is not None:

                    return result

        # ------------------------------------------------------
        # LIST
        # ------------------------------------------------------

        elif isinstance(
            data,
            list
        ):

            for item in data:

                result = find_n8n_value(
                    item,
                    target_key
                )

                if result is not None:

                    return result

        # ------------------------------------------------------
        # STRING
        #
        # n8n can sometimes return:
        #
        # "{\"svghtml\":\"...\",\"svgtext\":\"...\"}"
        # ------------------------------------------------------

        elif isinstance(
            data,
            str
        ):

            text = data.strip()

            if not text:

                return None

            try:

                parsed = json.loads(
                    text
                )

                # Make sure we don't recurse forever
                if parsed != data:

                    result = find_n8n_value(
                        parsed,
                        target_key
                    )

                    if result is not None:

                        return result

            except (
                json.JSONDecodeError,
                TypeError,
                ValueError
            ):

                pass

        return None

    # ==========================================================
    # PAGE HEADER
    # ==========================================================

    st.markdown(
        f'<div class="mp-logo">{APP_ICON} {APP_NAME}</div>',
        unsafe_allow_html=True
    )

    st.markdown(
        "### Medical Documents"
    )

    st.caption(
        "Upload and organize your healthcare documents. "
        "Uploaded documents are processed through the "
        "MedPath AI workflow."
    )

    # ==========================================================
    # UPLOAD DOCUMENT CARD
    # ==========================================================

    st.markdown(
        '<div class="mp-card">'
        '<h4>📤 Upload Medical Document</h4>',
        unsafe_allow_html=True
    )

    if editable:

        uploaded = st.file_uploader(
            "Choose a medical document",
            type=[
                "pdf",
                "jpg",
                "jpeg",
                "png",
                "doc",
                "docx"
            ],
            help=(
                "Supported formats: PDF, JPG, JPEG, "
                "PNG, DOC and DOCX."
            ),
            key=(
                f"medical_document_upload_"
                f"{patient_id}"
            )
        )

        if uploaded is not None:

            st.write(
                f"**Selected file:** {uploaded.name}"
            )

            selected_size_kb = round(
                len(
                    uploaded.getvalue()
                ) / 1024,
                1
            )

            st.caption(
                f"{uploaded.type or 'Unknown file type'} "
                f"• {selected_size_kb} KB"
            )

            # ==================================================
            # UPLOAD BUTTON
            # ==================================================

            if st.button(
                "📤 Upload Document",
                type="primary",
                use_container_width=True,
                key=(
                    f"confirm_document_upload_"
                    f"{patient_id}"
                )
            ):

                st.error(
                    "🔥 BUTTON CLICKED"
                )

                # ==================================================
                # DUPLICATE CHECK
                # ==================================================

                duplicate_doc = next(
                    (d for d in patient["documents"] if d.get("name") == uploaded.name),
                    None
                )

                if duplicate_doc:

                    st.warning(
                        f"⚠️ A document named '{uploaded.name}' is already "
                        "present in this patient's record."
                    )
                    if st.button(f"🔄 Re-analyze '{uploaded.name}' with n8n AI", key="btn_reanalyze_dup"):
                        if run_n8n_analysis_for_document(patient_id, duplicate_doc, patient, patients):
                            st.rerun()

                else:

                    # ==================================================
                    # DOCUMENT ID
                    # ==================================================

                    document_id = new_id(
                        "DOC"
                    )

                    # ==================================================
                    # SAVE FILE
                    # ==================================================

                    patient_upload_dir = os.path.join(
                        UPLOADS_DIR,
                        str(patient_id)
                    )

                    os.makedirs(
                        patient_upload_dir,
                        exist_ok=True
                    )

                    safe_filename = os.path.basename(
                        uploaded.name
                    )

                    file_path = os.path.join(
                        patient_upload_dir,
                        safe_filename
                    )

                    with open(
                        file_path,
                        "wb"
                    ) as f:

                        f.write(
                            uploaded.getbuffer()
                        )

                    relative_file_path = os.path.relpath(
                        file_path,
                        BASE_DIR
                    )

                    file_size_kb = round(
                        os.path.getsize(
                            file_path
                        ) / 1024,
                        1
                    )

                    # ==================================================
                    # N8N WEBHOOK
                    # ==================================================

                    N8N_WEBHOOK_URL = (
                        "https://kavyaas.app.n8n.cloud/"
                        "webhook/medpath/patient-review"
                    )

                    # ==================================================
                    # INITIAL VALUES
                    # ==================================================

                    n8n_result = None
                    raw_n8n_response = ""

                    analysis_html = None

                    analysis_text = None

                    analysis_available = False

                    # ==================================================
                    # SEND DOCUMENT TO N8N
                    # ==================================================

                    try:

                        with open(
                            file_path,
                            "rb"
                        ) as pdf_file:

                            response = http_requests.post(
                                N8N_WEBHOOK_URL,
                                files={
                                    "patient_record": (
                                        safe_filename,
                                        pdf_file,
                                        uploaded.type
                                        or "application/pdf"
                                    )
                                },
                                timeout=120
                            )

                        # ==================================================
                        # HTTP STATUS
                        # ==================================================

                        st.write(
                            "n8n HTTP Status:",
                            response.status_code
                        )

                        # ==================================================
                        # SUCCESS
                        # ==================================================

                        if response.ok:

                            st.success(
                                "✅ Document successfully "
                                "processed by n8n."
                            )

                            # ------------------------------------------
                            # RAW RESPONSE
                            # ------------------------------------------

                            raw_response = response.text
                            raw_n8n_response = raw_response

                            # ------------------------------------------
                            # PARSE RESPONSE
                            # ------------------------------------------

                            try:

                                n8n_result = response.json()

                            except (
                                ValueError,
                                json.JSONDecodeError
                            ):

                                try:

                                    n8n_result = json.loads(
                                        raw_response
                                    )

                                except (
                                    ValueError,
                                    json.JSONDecodeError,
                                    TypeError
                                ):

                                    n8n_result = raw_response

                            # If n8n returns dict with nested 'text' JSON string, parse it
                            if isinstance(n8n_result, dict):
                                inner_text = n8n_result.get("text")
                                if isinstance(inner_text, str) and inner_text.strip().startswith("{"):
                                    try:
                                        parsed_inner = json.loads(inner_text)
                                        if isinstance(parsed_inner, dict):
                                            for k, v in parsed_inner.items():
                                                if k not in n8n_result or not n8n_result[k]:
                                                    n8n_result[k] = v
                                    except Exception:
                                        pass

                            # ------------------------------------------
                            # PARSED RESULT
                            # ------------------------------------------

                            st.write(
                                "PARSED N8N RESULT:"
                            )

                            if isinstance(
                                n8n_result,
                                (dict, list)
                            ):

                                st.json(
                                    n8n_result
                                )

                            else:

                                st.code(
                                    str(n8n_result)
                                )

                            # ==================================================
                            # EXTRACT ANALYSIS FIELDS
                            # ==================================================

                            analysis_html = find_n8n_value(
                                n8n_result,
                                "html"
                            )

                            overall_summary = (
                                n8n_result.get("overall_summary")
                                or find_n8n_value(n8n_result, "overall_summary")
                            )

                            patterns = (
                                n8n_result.get("patterns")
                                or n8n_result.get("clinical_patterns")
                                or find_n8n_value(n8n_result, "patterns")
                                or find_n8n_value(n8n_result, "clinical_patterns")
                                or []
                            )

                            alerts = (
                                n8n_result.get("alerts")
                                or find_n8n_value(n8n_result, "alerts")
                                or []
                            )

                            missing_data = (
                                n8n_result.get("missing_data")
                                or find_n8n_value(n8n_result, "missing_data")
                                or []
                            )

                            care_gaps = (
                                n8n_result.get("care_gaps")
                                or find_n8n_value(n8n_result, "care_gaps")
                                or []
                            )

                            next_best_test = (
                                n8n_result.get("next_best_test")
                                or find_n8n_value(n8n_result, "next_best_test")
                            )

                            timeline = (
                                n8n_result.get("timeline")
                                or n8n_result.get("timeline_events")
                                or n8n_result.get("diagnostic_timeline")
                                or find_n8n_value(n8n_result, "timeline")
                                or []
                            )

                            clinical_notes = (
                                n8n_result.get("clinical_notes")
                                or find_n8n_value(n8n_result, "clinical_notes")
                            )

                            evidence_graph = (
                                n8n_result.get("evidence_graph")
                                or find_n8n_value(n8n_result, "evidence_graph")
                            )

                            raw_text = clinical_notes or overall_summary

                            if isinstance(raw_text, str):
                                analysis_text = raw_text.strip()
                            elif isinstance(raw_text, (dict, list)):
                                analysis_text = json.dumps(raw_text, indent=2)
                            else:
                                analysis_text = str(raw_text) if raw_text else None

                            analysis_available = bool(
                                analysis_html
                                or analysis_text
                                or overall_summary
                                or (isinstance(patterns, list) and len(patterns) > 0)
                                or (isinstance(alerts, list) and len(alerts) > 0)
                                or timeline
                                or clinical_notes
                                or evidence_graph
                            )

                            # ==================================================
                            # EXTRACTION DEBUG
                            # ==================================================

                            st.write(
                                "DEBUG — svghtml found:",
                                bool(analysis_html)
                            )

                            st.write(
                                "DEBUG — svgtext found:",
                                bool(analysis_text)
                            )

                            # ==================================================
                            # SHOW EXTRACTED ANALYSIS
                            # ==================================================

                            if analysis_html:

                                st.write(
                                    "DEBUG — HTML analysis "
                                    "successfully extracted."
                                )

                            elif analysis_text:

                                st.write(
                                    "DEBUG — TEXT analysis "
                                    "successfully extracted."
                                )

                            else:

                                st.warning(
                                    "⚠️ n8n returned HTTP 200, "
                                    "but svghtml/svgtext could "
                                    "not be found."
                                )

                                st.write(
                                    "The complete n8n response "
                                    "is shown above."
                                )

                        # ==================================================
                        # N8N HTTP ERROR
                        # ==================================================

                        else:

                            st.error(
                                f"❌ n8n returned HTTP "
                                f"{response.status_code}"
                            )

                            st.code(
                                response.text
                            )

                    # ==================================================
                    # CONNECTION ERROR
                    # ==================================================

                    except Exception as e:

                        st.error(
                            "❌ Failed to communicate with n8n: "
                            f"{type(e).__name__}: {e}"
                        )

                        n8n_result = None

                    # ==================================================
                    # CREATE DOCUMENT RECORD
                    #
                    # IMPORTANT:
                    # This is INSIDE the upload ELSE block.
                    # ==================================================

                    document = {

                        "id": document_id,

                        "name": uploaded.name,

                        "type": (
                            uploaded.type
                            or "unknown"
                        ),

                        "size_kb": file_size_kb,

                        "uploadedAt": (
                            datetime.now().isoformat()
                        ),

                        # ------------------------------------------
                        # LOCAL FILE
                        # ------------------------------------------

                        "file_path": (
                            relative_file_path
                        ),

                        # ------------------------------------------
                        # UPLOAD STATUS
                        # ------------------------------------------

                        "status": "Uploaded",

                        # ------------------------------------------
                        # WORKFLOW STATUS
                        # ------------------------------------------

                        "processing_status": (
                            "Completed"
                            if n8n_result is not None
                            else "Waiting for workflow"
                        ),

                        # ------------------------------------------
                        # ANALYSIS STATUS
                        # ------------------------------------------

                        "analysis_status": (
                            "Completed"
                            if analysis_available
                            else "Not analyzed"
                        ),

                        # ------------------------------------------
                        # N8N STATUS
                        # ------------------------------------------

                        "n8n_status": (
                            "Connected"
                            if n8n_result is not None
                            else "Not connected"
                        ),

                        # ------------------------------------------
                        # ACTUAL AI OUTPUT
                        # ------------------------------------------

                        "analysis": analysis_text,

                        "analysis_html": analysis_html,

                        "analysis_data": n8n_result,

                        # ==================================================
                        # STRUCTURED N8N CLINICAL ANALYSIS
                        # ==================================================

                        "overall_summary": (
                            n8n_result.get("overall_summary")
                            if isinstance(n8n_result, dict)
                            else None
                        ),

                        "patterns": (
                            n8n_result.get("patterns", [])
                            if isinstance(n8n_result, dict)
                            else []
                        ),

                        "alerts": (
                            n8n_result.get("alerts", [])
                            if isinstance(n8n_result, dict)
                            else []
                        ),

                        "missing_data": (
                            n8n_result.get("missing_data", [])
                            if isinstance(n8n_result, dict)
                            else []
                        ),

                        "care_gaps": (
                            n8n_result.get("care_gaps", [])
                            if isinstance(n8n_result, dict)
                            else []
                        ),

                        "next_best_test": (
                            n8n_result.get("next_best_test")
                            if isinstance(n8n_result, dict)
                            else None
                        ),

                        "clinical_notes": (
                            n8n_result.get("clinical_notes")
                            if isinstance(n8n_result, dict)
                            else None
                        ),

                        "evidence_graph": (
                            n8n_result.get("evidence_graph")
                            if isinstance(n8n_result, dict)
                            else None
                        ),

                        "ai_timeline": (
                            n8n_result.get("timeline", [])
                            if isinstance(n8n_result, dict)
                            else []
                        ),

                        # ==================================================
                        # LEGACY / FUTURE STRUCTURED DATA
                        # ==================================================

                        "insights": [],

                        "timeline_events": [],

                        "missing_information": [],

                        # ------------------------------------------
                        # WORKFLOW INFORMATION
                        # ------------------------------------------

                        "workflow_id": None,

                        "processedAt": (
                            datetime.now().isoformat()
                            if n8n_result is not None
                            else None
                        )
                    }

                    # ==================================================
                    # SAVE DOCUMENT
                    # ==================================================

                    patient["documents"].append(
                        document
                    )

                    # ==================================================
                    # ADD DOCUMENT TO TIMELINE
                    # ==================================================

                    # Add the uploaded document itself
                    patient["timeline"].append({

                        "id": new_id("EVT"),

                        "type": "Medical Document",

                        "date": str(date.today()),

                        "description": (
                            f"Uploaded medical document: "
                            f"{uploaded.name}"
                        ),

                        "source": "document",

                        "ai_generated": False,
                    })

                    # ==================================================
                    # ADD AI-GENERATED TIMELINE EVENTS
                    # ==================================================

                    ai_timeline = document.get(
                        "ai_timeline",
                        []
                    )

                    if isinstance(ai_timeline, list):

                        for ai_event in ai_timeline:

                            if not isinstance(ai_event, dict):
                                continue

                            event_date = ai_event.get(
                                "date",
                                str(date.today())
                            )

                            event_description = ai_event.get(
                                "event",
                                ai_event.get(
                                    "description",
                                    "AI-generated clinical event"
                                )
                            )

                            event_evidence = ai_event.get(
                                "evidence",
                                ""
                            )

                            if event_evidence:

                                event_description = (
                                    f"{event_description} "
                                    f"Evidence: {event_evidence}"
                                )

                            patient["timeline"].append({

                                "id": new_id("EVT"),

                                "type": "AI Clinical Evidence",

                                "date": str(event_date),

                                "description": str(
                                    event_description
                                ),

                                "source": "ai",

                                "ai_generated": True,

                                "document_id": document.get(
                                    "id"
                                ),
                            })

                    # ==================================================
                    # SAVE PATIENT RECORD
                    # ==================================================

                    save_patient_record(
                        patient_id,
                        patient,
                        patients
                    )

                    st.session_state["patients"] = patients
                    if st.session_state.get("selected_patient_id") == patient_id:
                        st.session_state["patient_record"] = patient

                    sync_patient_evidence_to_backend(
                        patient_id,
                        patient,
                        n8n_result=n8n_result,
                        source_doc_id=document.get("id"),
                        source_doc_name=uploaded.name
                    )


                    # ==================================================
                    # FINAL UPLOAD MESSAGE
                    # ==================================================

                    if analysis_available:

                        st.success(
                            "✅ Medical document uploaded "
                            "and AI analysis received successfully."
                        )

                    elif n8n_result is not None:

                        st.warning(
                            "⚠️ Document was processed by n8n, "
                            "but svghtml/svgtext analysis "
                            "could not be extracted."
                        )

                    else:

                        st.warning(
                            "⚠️ Document uploaded, but the "
                            "AI workflow did not return a result."
                        )

                    # ==================================================
                    # REFRESH
                    # ==================================================

                    st.rerun()

    # ==========================================================
    # CLOSE UPLOAD CARD
    # ==========================================================

    st.markdown(
        '</div>',
        unsafe_allow_html=True
    )

    # ==========================================================
    # UPLOADED DOCUMENTS
    # ==========================================================

    st.markdown(
        '<div class="mp-card">'
        '<h4>📚 Uploaded Documents</h4>',
        unsafe_allow_html=True
    )

    documents = patient.get(
        "documents",
        []
    )

    if not documents:

        empty_state(
            "No medical documents uploaded yet."
        )

    else:

        st.caption(
            f"{len(documents)} document(s) "
            "in this patient's record."
        )

        # ======================================================
        # NEWEST DOCUMENT FIRST
        # ======================================================

        for index, document in enumerate(
            reversed(documents)
        ):

            name = document.get(
                "name",
                "Unnamed document"
            )

            status = document.get(
                "status",
                "Uploaded"
            )

            processing_status = document.get(
                "processing_status",
                "Waiting for workflow"
            )

            # ==================================================
            # DETERMINE ACTUAL ANALYSIS AVAILABILITY
            # ==================================================

            stored_analysis_html = document.get(
                "analysis_html"
            )

            stored_analysis_text = document.get(
                "analysis"
            )

            actual_analysis_available = bool(
                document.get("overall_summary")
                or document.get("patterns")
                or document.get("alerts")
                or document.get("missing_data")
                or document.get("care_gaps")
                or document.get("next_best_test")
                or document.get("timeline_events")
                or document.get("clinical_notes")
                or document.get("evidence_graph")
            )

            if actual_analysis_available:

                analysis_status = "Completed"

            else:

                analysis_status = document.get(
                    "analysis_status",
                    "Not analyzed"
                )

            uploaded_at = document.get(
                "uploadedAt",
                "-"
            )

            file_type = document.get(
                "type",
                "Unknown"
            )

            size_kb = document.get(
                "size_kb",
                "-"
            )

            # ==================================================
            # DOCUMENT HEADER
            # ==================================================

            st.markdown(
                f"### 📄 {name}"
            )

            st.caption(
                f"{file_type} • {size_kb} KB"
            )

            # ==================================================
            # STATUS COLUMNS
            # ==================================================

            c1, c2, c3, c4 = st.columns([1, 1, 1, 1.2])

            # --------------------------------------------------
            # UPLOAD STATUS
            # --------------------------------------------------

            with c1:

                st.write(
                    "**Upload Status**"
                )

                if status == "Uploaded":

                    st.success(
                        "✅ Uploaded"
                    )

                else:

                    st.info(
                        status
                    )

            # --------------------------------------------------
            # WORKFLOW STATUS
            # --------------------------------------------------

            with c2:

                st.write(
                    "**Workflow Status**"
                )

                if processing_status == "Completed":

                    st.success(
                        "✅ Completed"
                    )

                elif processing_status == "Processing":

                    st.warning(
                        "⚙️ Processing"
                    )

                elif processing_status == "Waiting for workflow":

                    st.info(
                        "⏳ Waiting for AI workflow"
                    )

                else:

                    st.info(
                        processing_status
                    )

            # --------------------------------------------------
            # ANALYSIS STATUS
            # --------------------------------------------------

            with c3:

                st.write(
                    "**Analysis**"
                )

                if analysis_status == "Completed":

                    st.success(
                        "✅ Available"
                    )

                elif analysis_status == "Processing":

                    st.warning(
                        "⚙️ Processing"
                    )

                else:

                    st.info(
                        "⏳ Not analyzed"
                    )

            # --------------------------------------------------
            # ACTION / RETRY BUTTON
            # --------------------------------------------------

            with c4:

                st.write(
                    "**Action**"
                )

                doc_id = document.get("id", f"doc_{index}")

                if actual_analysis_available and processing_status == "Completed":
                    btn_label = "🔄 Re-run AI Analysis"
                else:
                    btn_label = "⚡ Run AI Analysis"

                if st.button(
                    btn_label,
                    key=f"btn_retry_col_{doc_id}_{index}",
                    help="Trigger or retry n8n AI review for this document"
                ):
                    if run_n8n_analysis_for_document(patient_id, document, patient, patients):
                        st.rerun()

            # ==================================================
            # DOCUMENT DETAILS
            # ==================================================

            with st.expander(
                f"View details — {name}",
                expanded=False
            ):

                st.write(
                    f"**Document ID:** "
                    f"{document.get('id', '-')}"
                )

                st.write(
                    f"**File type:** "
                    f"{file_type}"
                )

                st.write(
                    f"**File size:** "
                    f"{size_kb} KB"
                )

                st.write(
                    f"**Uploaded:** "
                    f"{uploaded_at}"
                )

                st.write(
                    f"**Processing:** "
                    f"{processing_status}"
                )

                st.write(
                    f"**Analysis:** "
                    f"{analysis_status}"
                )

                # ==================================================
                # WORKFLOW STATUS
                # ==================================================

                st.markdown("---")

                st.write(
                    "**MedPath AI Workflow**"
                )

                st.write(
                    "1. ✅ Document uploaded"
                )

                if processing_status == "Completed":

                    st.write(
                        "2. ✅ Document processed"
                    )

                    st.write(
                        "3. ✅ Medical information structured"
                    )

                    st.write(
                        "4. ✅ Clinical review generated"
                    )

                    if actual_analysis_available:

                        st.write(
                            "5. ✅ Analysis available"
                        )

                    else:

                        st.write(
                            "5. ⏳ Analysis not available"
                        )

                    st.write(
                        "6. 🔄 AI chatbot integration"
                    )

                else:

                    st.write(
                        "2. ⏳ Document processing"
                    )

                    st.write(
                        "3. ⏳ Medical information structuring"
                    )

                    st.write(
                        "4. ⏳ Clinical review generation"
                    )

                    st.write(
                        "5. ⏳ Analysis and insights"
                    )

                    st.write(
                        "6. ⏳ AI chatbot availability"
                    )

                st.caption(
                    "Completed stages are based on the "
                    "response received from the MedPath "
                    "automation workflow."
                )

                st.markdown("---")
                if st.button(
                    "⚡ Run / Retry n8n AI Analysis Now",
                    key=f"btn_exp_retry_{doc_id}_{index}",
                    type="primary"
                ):
                    if run_n8n_analysis_for_document(patient_id, document, patient, patients):
                        st.rerun()

                # ==================================================
                # MEDPATH STRUCTURED ANALYSIS
                # ==================================================

                if actual_analysis_available:

                    st.markdown("---")
                    st.markdown("## 🤖 MedPath AI Clinical Review")

                    # ------------------------------------------------------
                    # OVERALL SUMMARY
                    # ------------------------------------------------------

                    summary = document.get("overall_summary")

                    if summary:

                        st.markdown("### 📋 Overall Summary")
                        st.write(summary)

                    # ------------------------------------------------------
                    # CLINICAL PATTERNS
                    # ------------------------------------------------------

                    patterns_data = document.get("patterns", [])

                    if patterns_data:

                        st.markdown("### 🔬 Clinical Patterns & Confidence")

                        for i, pattern in enumerate(patterns_data, 1):

                            if not isinstance(pattern, dict):
                                continue

                            name = pattern.get(
                                "name",
                                "Clinical pattern"
                            )

                            likelihood = pattern.get(
                                "likelihood",
                                "Unknown"
                            )

                            confidence = pattern.get(
                                "confidence"
                            )

                            st.markdown(
                                f"**{i}. {name}**"
                            )

                            st.write(
                                f"Likelihood: **{likelihood}**"
                            )

                            if confidence is not None:

                                st.progress(
                                    min(
                                        max(
                                            int(confidence),
                                            0
                                        ),
                                        100
                                    )
                                    / 100
                                )

                                st.caption(
                                    f"Evidence confidence: {confidence}%"
                                )

                            evidence = pattern.get(
                                "evidence",
                                []
                            )

                            if evidence:

                                st.write(
                                    "**Supporting evidence:**"
                                )

                                for item in evidence:

                                    st.write(
                                        f"• {item}"
                                    )

                    # ------------------------------------------------------
                    # ALERTS
                    # ------------------------------------------------------

                    alerts_data = document.get(
                        "alerts",
                        []
                    )

                    if alerts_data:

                        st.markdown("### 🚨 Alerts")

                        for alert in alerts_data:

                            if isinstance(alert, dict):

                                level = alert.get(
                                    "level",
                                    "Alert"
                                )

                                message = alert.get(
                                    "message",
                                    ""
                                )

                                evidence = alert.get(
                                    "evidence",
                                    ""
                                )

                                st.warning(
                                    f"**{level} — {message}**"
                                )

                                if evidence:

                                    st.caption(
                                        f"Evidence: {evidence}"
                                    )

                    # ------------------------------------------------------
                    # MISSING DATA
                    # ------------------------------------------------------

                    missing_data = document.get(
                        "missing_data",
                        []
                    )

                    if missing_data:

                        st.markdown(
                            "### 🔎 Missing Data & Care Gaps"
                        )

                        for item in missing_data:

                            st.write(
                                f"• {item}"
                            )

                    # ------------------------------------------------------
                    # CARE GAPS
                    # ------------------------------------------------------

                    care_gaps = document.get(
                        "care_gaps",
                        []
                    )

                    if care_gaps:

                        st.markdown(
                            "### ⚠️ Potential Care Gaps"
                        )

                        for item in care_gaps:

                            st.write(
                                f"• {item}"
                            )

                    # ------------------------------------------------------
                    # NEXT BEST TEST
                    # ------------------------------------------------------

                    next_test = document.get(
                        "next_best_test"
                    )

                    if next_test:

                        st.markdown(
                            "### 🔬 Next Best Test"
                        )

                        if isinstance(
                            next_test,
                            dict
                        ):

                            st.write(
                                f"**{next_test.get('name', 'Recommended test')}**"
                            )

                            if next_test.get("reason"):

                                st.write(
                                    next_test["reason"]
                                )

                        else:

                            st.write(next_test)

                    # ------------------------------------------------------
                    # TIMELINE
                    # ------------------------------------------------------

                    timeline_data = document.get(
                        "timeline_events",
                        []
                    )

                    if timeline_data:

                        st.markdown(
                            "### 📅 Timeline Overview"
                        )

                        for event in timeline_data:

                            if not isinstance(event, dict):
                                continue

                            st.markdown(
                                f"**{event.get('date', '-')}**"
                            )

                            st.write(
                                event.get(
                                    "event",
                                    ""
                                )
                            )

                            if event.get("evidence"):

                                st.caption(
                                    f"Evidence: {event['evidence']}"
                                )

                    # ------------------------------------------------------
                    # CLINICAL NOTES
                    # ------------------------------------------------------

                    notes = document.get(
                        "clinical_notes"
                    )

                    if notes:

                        st.markdown(
                            "### 📝 Clinical Notes"
                        )

                        st.write(notes)

                    # ------------------------------------------------------
                    # EVIDENCE GRAPH
                    # ------------------------------------------------------

                    graph = document.get(
                        "evidence_graph"
                    )

                    if graph:

                        st.markdown(
                            "### 🧠 Clinical Evidence Graph"
                        )

                        if isinstance(graph, dict):

                            st.write(
                                f"**Evidence nodes:** "
                                f"{graph.get('nodes', 0)}"
                            )

                            st.write(
                                f"**Evidence relationships:** "
                                f"{graph.get('relationships', 0)}"
                            )

                else:

                    st.info(
                        "⏳ AI analysis is not available yet."
                    )

            # ==================================================
            # SEPARATOR
            # ==================================================

            if index < len(documents) - 1:

                st.markdown(
                    "---"
                )

    # ==========================================================
    # CLOSE DOCUMENT CARD
    # ==========================================================

    st.markdown(
        '</div>',
        unsafe_allow_html=True
    )

    # ==========================================================
    # AI PROCESSING INFORMATION
    # ==========================================================

    st.markdown(
        '<div class="mp-card">'
        '<h4>🤖 AI Processing</h4>',
        unsafe_allow_html=True
    )

    st.write(
        "MedPath sends uploaded medical documents "
        "through the connected AI automation workflow."
    )

    st.write(
        "The workflow analyzes the document and returns "
        "a clinical review. The returned analysis is stored "
        "with the patient's document and displayed in "
        "the document details."
    )

    st.write(
        "The processed information can later be used "
        "for healthcare timeline generation, insights, "
        "and the MedPath AI Assistant."
    )

    st.caption(
        "AI-generated information should be verified by "
        "a healthcare professional."
    )

    st.markdown(
        '</div>',
        unsafe_allow_html=True
    )

def symptoms_medications_appointments_page(patient_id, editable=True):
    patient, patients = get_patient_record(patient_id)

    # Make sure required lists always exist
    patient.setdefault("symptoms", [])
    patient.setdefault("medications", [])
    patient.setdefault("appointments", [])
    patient.setdefault("timeline", [])

    # ==========================================================
    # SYMPTOMS
    # ==========================================================

    st.markdown(
        '<div class="mp-card"><h4>Symptoms</h4>',
        unsafe_allow_html=True
    )

    if editable:

        with st.form("add_symptom_form", clear_on_submit=True):

            c1, c2, c3 = st.columns(3)

            name = c1.text_input(
                "Symptom",
                placeholder="Enter symptom"
            )

            severity = c2.selectbox(
                "Severity",
                ["Mild", "Moderate", "Severe"]
            )

            onset = c3.date_input(
                "Onset / Reported Date",
                value=date.today()
            )

            submitted = st.form_submit_button(
                "Add Symptom",
                type="primary"
            )

            if submitted:

                if not name.strip():
                    st.warning("Please enter a symptom.")
                else:

                    patient["symptoms"].append({
                        "id": new_id("SYM"),
                        "name": name.strip(),
                        "severity": severity,
                        "onset": str(onset)
                    })

                    patient["timeline"].append({
                        "id": new_id("EVT"),
                        "type": "Symptom",
                        "date": str(onset),
                        "description": f"{name.strip()} ({severity})"
                    })

                    save_patient_record(
                        patient_id,
                        patient,
                        patients
                    )

                    st.success("Symptom added successfully.")

                    st.rerun()

    if patient["symptoms"]:

        for s in patient["symptoms"]:

            st.write(
                f"- {s.get('name', '-')}"
                f" — {s.get('severity', '-')}"
                f" (since {s.get('onset', '-')})"
            )

    else:

        empty_state(
            "No symptoms recorded yet."
        )

    st.markdown('</div>', unsafe_allow_html=True)

    # ==========================================================
    # MEDICATIONS
    # ==========================================================

    st.markdown(
        '<div class="mp-card"><h4>Medications</h4>',
        unsafe_allow_html=True
    )

    if editable:

        with st.form(
            "add_medication_form",
            clear_on_submit=True
        ):

            c1, c2, c3 = st.columns(3)

            mname = c1.text_input(
                "Medicine Name",
                placeholder="Enter medicine name"
            )

            dosage = c2.text_input(
                "Dosage",
                placeholder="Example: 500 mg"
            )

            frequency = c3.text_input(
                "Frequency",
                placeholder="Example: Twice daily"
            )

            submitted = st.form_submit_button(
                "Add Medication",
                type="primary"
            )

            if submitted:

                if not mname.strip():

                    st.warning(
                        "Please enter a medicine name."
                    )

                else:

                    patient["medications"].append({
                        "id": new_id("MED"),
                        "name": mname.strip(),
                        "dosage": dosage.strip(),
                        "frequency": frequency.strip(),
                        "status": "Active"
                    })

                    save_patient_record(
                        patient_id,
                        patient,
                        patients
                    )

                    st.success(
                        "Medication added successfully."
                    )

                    st.rerun()

    if patient["medications"]:

        for m in patient["medications"]:

            st.write(
                f"- {m.get('name', '-')}"
                f" — {m.get('dosage', '-')}, "
                f"{m.get('frequency', '-')}"
                f" ({m.get('status', 'Active')})"
            )

    else:

        empty_state(
            "No medications on record."
        )

    st.markdown('</div>', unsafe_allow_html=True)

    # ==========================================================
    # APPOINTMENTS
    # ==========================================================

    st.markdown(
        '<div class="mp-card"><h4>Appointments</h4>',
        unsafe_allow_html=True
    )

    if editable:

        with st.form(
            "add_appt_form",
            clear_on_submit=True
        ):

            c1, c2 = st.columns(2)

            adate = c1.date_input(
                "Appointment Date",
                value=date.today()
            )

            reason = c2.text_input(
                "Reason",
                placeholder="Example: Follow-up consultation"
            )

            submitted = st.form_submit_button(
                "Add Appointment",
                type="primary"
            )

            if submitted:

                if not reason.strip():

                    st.warning(
                        "Please enter the appointment reason."
                    )

                else:

                    patient["appointments"].append({
                        "id": new_id("APT"),
                        "date": str(adate),
                        "reason": reason.strip(),
                        "status": "Pending"
                    })

                    save_patient_record(
                        patient_id,
                        patient,
                        patients
                    )

                    st.success(
                        "Appointment added successfully."
                    )

                    st.rerun()

    if patient["appointments"]:

        for a in patient["appointments"]:

            st.write(
                f"- {a.get('date', '-')}"
                f" — {a.get('reason', '-')}"
                f" ({a.get('status', 'Pending')})"
            )

    else:

        empty_state(
            "No appointments on record."
        )

    st.markdown('</div>', unsafe_allow_html=True)
#==============================================================
def generate_hypotheses(patient):
    """
    MedPath rule-based analysis engine.

    Uses ONLY information already stored in the patient's record:
    - Symptoms
    - Laboratory results
    - Medications
    - Timeline
    - Appointments
    - Documents

    It does not read/OCR documents and does not invent medical facts.
    """

    if not patient:
        return {
            "hypotheses": [],
            "missing_test": None,
            "summary": "No patient record is available."
        }

    symptoms = patient.get("symptoms") or []
    labs = patient.get("labs") or []
    medications = patient.get("medications") or []
    timeline = patient.get("timeline") or []
    appointments = patient.get("appointments") or []
    documents = patient.get("documents") or []

    # ----------------------------------------------------------
    # NORMALIZE DATA
    # ----------------------------------------------------------

    symptom_names = [
        str(s.get("name", "")).strip().lower()
        for s in symptoms
        if s.get("name")
    ]

    lab_map = {}

    for lab in labs:
        test_name = str(
            lab.get("test", "")
        ).strip().lower()

        if test_name:
            lab_map[test_name] = lab

    hypotheses = []
    missing_test = None

    # ----------------------------------------------------------
    # SYMPTOM FLAGS
    # ----------------------------------------------------------

    fatigue = any(
        "fatigue" in s or
        "tired" in s or
        "weakness" in s or
        "weak" in s
        for s in symptom_names
    )

    breathlessness = any(
        "breath" in s or
        "shortness of breath" in s or
        "breathlessness" in s
        for s in symptom_names
    )

    dizziness = any(
        "dizziness" in s or
        "dizzy" in s
        for s in symptom_names
    )

    headache = any(
        "headache" in s or
        "head pain" in s
        for s in symptom_names
    )

    thirst = any(
        "thirst" in s
        for s in symptom_names
    )

    frequent_urination = any(
        "urination" in s or
        "urinating" in s or
        "frequent urine" in s
        for s in symptom_names
    )

    blurred_vision = any(
        "blurred vision" in s or
        "blurry vision" in s or
        "vision" in s
        for s in symptom_names
    )

    # ----------------------------------------------------------
    # ANEMIA-RELATED PATTERN
    # ----------------------------------------------------------

    anemia_evidence = []

    if fatigue:
        anemia_evidence.append("Fatigue recorded")

    if breathlessness:
        anemia_evidence.append(
            "Breathlessness recorded"
        )

    if dizziness:
        anemia_evidence.append(
            "Dizziness recorded"
        )

    hemoglobin = None

    for name, lab in lab_map.items():

        if (
            "hemoglobin" in name
            or name == "hb"
            or name == "haemoglobin"
        ):
            try:
                hemoglobin = float(
                    lab.get("value")
                )
            except (TypeError, ValueError):
                hemoglobin = None

            break

    if hemoglobin is not None:

        anemia_evidence.append(
            f"Hemoglobin {hemoglobin} "
            f"{lab_map.get(name, {}).get('unit', '')}".strip()
        )

        if hemoglobin < 12:

            hypotheses.append({
                "condition": "Possible anemia pattern",
                "likelihood": "Medium",
                "evidence": anemia_evidence
            })

        elif fatigue or breathlessness or dizziness:

            hypotheses.append({
                "condition": "Symptoms requiring further evaluation",
                "likelihood": "Low",
                "evidence": anemia_evidence
            })

    elif fatigue and (
        breathlessness or dizziness
    ):

        hypotheses.append({
            "condition": "Possible anemia pattern",
            "likelihood": "Low",
            "evidence": anemia_evidence
        })

        missing_test = (
            "Complete Blood Count (CBC) / Hemoglobin"
        )

    # ----------------------------------------------------------
    # BLOOD-SUGAR-RELATED PATTERN
    # ----------------------------------------------------------

    glucose_evidence = []

    if thirst:
        glucose_evidence.append(
            "Increased thirst recorded"
        )

    if frequent_urination:
        glucose_evidence.append(
            "Frequent urination recorded"
        )

    if blurred_vision:
        glucose_evidence.append(
            "Blurred vision recorded"
        )

    glucose_value = None

    for name, lab in lab_map.items():

        if (
            "glucose" in name
            or "blood sugar" in name
            or "fasting sugar" in name
        ):

            try:
                glucose_value = float(
                    lab.get("value")
                )
            except (TypeError, ValueError):
                glucose_value = None

            if glucose_value is not None:

                glucose_evidence.append(
                    f"{lab.get('test', 'Glucose')} "
                    f"{glucose_value} "
                    f"{lab.get('unit', '')}".strip()
                )

            break

    if (
        len(glucose_evidence) >= 2
        and glucose_value is not None
        and glucose_value >= 100
    ):

        hypotheses.append({
            "condition": "Possible blood glucose abnormality pattern",
            "likelihood": "Medium",
            "evidence": glucose_evidence
        })

    elif len(glucose_evidence) >= 2:

        hypotheses.append({
            "condition": "Symptoms requiring blood glucose evaluation",
            "likelihood": "Low",
            "evidence": glucose_evidence
        })

        if missing_test is None:
            missing_test = "Blood glucose / HbA1c"

    # ----------------------------------------------------------
    # HEADACHE / DIZZINESS PATTERN
    # ----------------------------------------------------------

    if headache and dizziness:

        hypotheses.append({
            "condition": "Headache and dizziness pattern",
            "likelihood": "Low",
            "evidence": [
                "Headache recorded",
                "Dizziness recorded"
            ]
        })

    # ----------------------------------------------------------
    # GENERAL SYMPTOM ANALYSIS
    # ----------------------------------------------------------

    if not hypotheses and symptoms:

        evidence = [
            f"{s.get('name')} ({s.get('severity', 'severity not recorded')})"
            for s in symptoms
            if s.get("name")
        ]

        hypotheses.append({
            "condition": "Symptoms recorded — further evaluation required",
            "likelihood": "Low",
            "evidence": evidence
        })

    # ----------------------------------------------------------
    # NO MEDICAL DATA
    # ----------------------------------------------------------

    if not symptoms and not labs:

        hypotheses.append({
            "condition": "No clinical pattern identified yet",
            "likelihood": "Low",
            "evidence": [
                "No symptoms or laboratory results recorded"
            ]
        })

    # ----------------------------------------------------------
    # SUMMARY
    # ----------------------------------------------------------

    summary_parts = []

    if symptoms:
        summary_parts.append(
            f"{len(symptoms)} symptom(s)"
        )

    if labs:
        summary_parts.append(
            f"{len(labs)} laboratory result(s)"
        )

    if medications:
        summary_parts.append(
            f"{len(medications)} medication(s)"
        )

    if documents:
        summary_parts.append(
            f"{len(documents)} document(s)"
        )

    if timeline:
        summary_parts.append(
            f"{len(timeline)} timeline event(s)"
        )

    if appointments:
        pending = [
            a for a in appointments
            if str(a.get("status", "")).lower()
            == "pending"
        ]

        if pending:
            summary_parts.append(
                f"{len(pending)} pending appointment(s)"
            )

    if summary_parts:

        summary = (
            "Current patient record contains: "
            + ", ".join(summary_parts)
            + "."
        )

    else:

        summary = (
            "No medical information has been "
            "recorded for this patient yet."
        )

    # ----------------------------------------------------------
    # FINAL RESULT
    # ----------------------------------------------------------

    return {
        "hypotheses": hypotheses,
        "missing_test": missing_test,
        "summary": summary
    }
def insights_page(patient_id):

    patient, _ = get_patient_record(patient_id)

    if patient is None:
        empty_state("Patient record not found.")
        return

    # ==========================================================
    # GET LATEST AI ANALYSIS FROM DOCUMENTS
    # ==========================================================

    documents = patient.get("documents", [])

    analysis_data = None

    for document in reversed(documents):

        if not isinstance(document, dict):
            continue

        data = document.get("analysis_data")

        if isinstance(data, dict) and (
            data.get("overall_summary")
            or data.get("patterns")
            or data.get("alerts")
            or data.get("timeline")
            or data.get("clinical_notes")
            or data.get("evidence_graph")
        ):
            analysis_data = data
            break

    # ==========================================================
    # PAGE HEADER
    # ==========================================================

    st.markdown(
        '<div class="mp-card"><h4>🧠 Clinical Insights</h4>',
        unsafe_allow_html=True,
    )

    st.caption(
        "Explainable AI insights generated from the patient's "
        "documented medical evidence."
    )

    if not analysis_data:

        st.info(
            "No AI analysis is available yet. "
            "Upload and process a medical document first."
        )

        st.markdown(
            '<div class="mp-disclaimer">'
            'AI-generated information should be verified by '
            'a healthcare professional.'
            '</div>',
            unsafe_allow_html=True,
        )

        st.markdown(
            "</div>",
            unsafe_allow_html=True,
        )

        return

    # ==========================================================
    # GET ALL ANALYSIS COMPONENTS
    # ==========================================================

    overall_summary = analysis_data.get(
        "overall_summary",
        "No overall summary available."
    )

    patterns = analysis_data.get(
        "patterns",
        []
    )

    alerts = analysis_data.get(
        "alerts",
        []
    )

    missing_data = analysis_data.get(
        "missing_data",
        []
    )

    care_gaps = analysis_data.get(
        "care_gaps",
        []
    )

    next_test = analysis_data.get(
        "next_best_test"
    )

    timeline = analysis_data.get(
        "timeline",
        []
    )

    clinical_notes = analysis_data.get(
        "clinical_notes",
        ""
    )

    evidence_graph = analysis_data.get(
        "evidence_graph",
        {}
    )

    # ==========================================================
    # OVERALL SUMMARY
    # ==========================================================

    st.markdown("### 🧾 Overall Summary")

    st.info(
        overall_summary
    )

    # ==========================================================
    # PATTERN ANALYSIS
    # ==========================================================

    st.markdown(
        "### 🔎 Patterns & Observations"
    )

    if patterns:

        for index, pattern in enumerate(
            patterns,
            start=1
        ):

            if not isinstance(
                pattern,
                dict
            ):
                continue

            name = pattern.get(
                "name",
                "Clinical pattern"
            )

            likelihood = pattern.get(
                "likelihood",
                "Not specified"
            )

            confidence = pattern.get(
                "confidence"
            )

            evidence = pattern.get(
                "evidence",
                []
            )

            st.markdown(
                f"#### {index}. {name}"
            )

            c1, c2 = st.columns(2)

            with c1:

                st.write(
                    f"**Likelihood:** {likelihood}"
                )

            with c2:

                if confidence is not None:

                    try:

                        confidence_value = float(
                            confidence
                        )

                        st.write(
                            f"**Evidence Confidence:** "
                            f"{confidence_value:g}%"
                        )

                        st.progress(
                            min(
                                max(
                                    confidence_value,
                                    0
                                ),
                                100
                            ) / 100
                        )

                    except (
                        ValueError,
                        TypeError
                    ):

                        st.write(
                            f"**Evidence Confidence:** "
                            f"{confidence}%"
                        )

                else:

                    st.write(
                        "**Evidence Confidence:** "
                        "Not available"
                    )

            if evidence:

                st.markdown(
                    "**Clinical Evidence:**"
                )

                if not isinstance(
                    evidence,
                    list
                ):

                    evidence = [
                        evidence
                    ]

                for item in evidence:

                    st.markdown(
                        f"- {item}"
                    )

            st.markdown(
                "---"
            )

    else:

        st.info(
            "No clinical patterns identified."
        )

    # ==========================================================
    # ALERT SUMMARY
    # ==========================================================

    st.markdown(
        "### 🚨 Alert Summary"
    )

    high_count = 0
    medium_count = 0
    low_count = 0

    valid_alerts = []

    if isinstance(
        alerts,
        list
    ):

        for alert in alerts:

            if isinstance(
                alert,
                dict
            ):

                valid_alerts.append(
                    alert
                )

                level = str(
                    alert.get(
                        "level",
                        ""
                    )
                ).lower()

                if level == "high":

                    high_count += 1

                elif level == "medium":

                    medium_count += 1

                elif level == "low":

                    low_count += 1

    c1, c2, c3 = st.columns(3)

    with c1:

        st.metric(
            "🔴 High Alerts",
            high_count
        )

    with c2:

        st.metric(
            "🟠 Medium Alerts",
            medium_count
        )

    with c3:

        st.metric(
            "🟢 Low Alerts",
            low_count
        )

    # ==========================================================
    # ALERT DETAILS
    # ==========================================================

    if valid_alerts:

        for index, alert in enumerate(
            valid_alerts,
            start=1
        ):

            level = str(
                alert.get(
                    "level",
                    "Information"
                )
            )

            message = alert.get(
                "message",
                ""
            )

            evidence = alert.get(
                "evidence",
                ""
            )

            if level.lower() == "high":

                st.error(
                    f"🚨 **{level} Alert**\n\n"
                    f"{message}"
                )

            elif level.lower() == "medium":

                st.warning(
                    f"⚠️ **{level} Alert**\n\n"
                    f"{message}"
                )

            else:

                st.info(
                    f"ℹ️ **{level} Alert**\n\n"
                    f"{message}"
                )

            if evidence:

                st.caption(
                    f"Evidence: {evidence}"
                )

    else:

        st.success(
            "No active alerts identified."
        )

    # ==========================================================
    # MISSING DATA
    # ==========================================================

    st.markdown(
        "### ⚠️ Missing Data"
    )

    if missing_data:

        if not isinstance(
            missing_data,
            list
        ):

            missing_data = [
                missing_data
            ]

        for item in missing_data:

            st.markdown(
                f"- {item}"
            )

    else:

        st.success(
            "No important missing data identified."
        )

    # ==========================================================
    # CARE GAPS
    # ==========================================================

    st.markdown(
        "### 🏥 Potential Care Gaps"
    )

    if care_gaps:

        if not isinstance(
            care_gaps,
            list
        ):

            care_gaps = [
                care_gaps
            ]

        for gap in care_gaps:

            st.warning(
                f"• {gap}"
            )

    else:

        st.success(
            "No potential care gaps identified."
        )

    # ==========================================================
    # NEXT BEST TEST
    # ==========================================================

    st.markdown(
        "### 🔬 Next Best Test"
    )

    if isinstance(
        next_test,
        dict
    ):

        test_name = next_test.get(
            "name",
            "Not specified"
        )

        reason = next_test.get(
            "reason",
            ""
        )

        st.markdown(
            f"**Recommended investigation:** "
            f"{test_name}"
        )

        if reason:

            st.info(
                reason
            )

    elif next_test:

        st.info(
            str(next_test)
        )

    else:

        st.info(
            "No next best test identified."
        )

    # ==========================================================
    # TIMELINE OVERVIEW
    # ==========================================================

    st.markdown(
        "### 📅 Timeline Overview"
    )

    if timeline:

        for event in timeline:

            if not isinstance(
                event,
                dict
            ):
                continue

            event_date = event.get(
                "date",
                ""
            )

            event_name = event.get(
                "event",
                "Medical event"
            )

            evidence = event.get(
                "evidence",
                ""
            )

            st.markdown(
                f"**{event_date}** — "
                f"{event_name}"
            )

            if evidence:

                st.caption(
                    f"Evidence: {evidence}"
                )

    else:

        st.info(
            "No AI-generated timeline events available."
        )

    # ==========================================================
    # CLINICAL NOTES
    # ==========================================================

    st.markdown(
        "### 📝 Clinical Notes"
    )

    if clinical_notes:

        st.info(
            clinical_notes
        )

    else:

        st.info(
            "No clinical notes available."
        )

    # ==========================================================
    # CLINICAL EVIDENCE GRAPH
    # ==========================================================

    st.markdown(
        '<div class="mp-card">'
        '<h4>🧠 Clinical Evidence Graph</h4>',
        unsafe_allow_html=True,
    )

    # ----------------------------------------------------------
    # GRAPH COUNTS
    # ----------------------------------------------------------

    graph_nodes = 0
    graph_relationships = 0

    if isinstance(
        evidence_graph,
        dict
    ):

        graph_nodes = evidence_graph.get(
            "nodes",
            0
        )

        graph_relationships = evidence_graph.get(
            "relationships",
            0
        )

    # ----------------------------------------------------------
    # CREATE VISUAL GRAPH
    # ----------------------------------------------------------

    dot = graphviz.Digraph(
        comment="Clinical Evidence Graph"
    )

    dot.attr(
        rankdir="LR"
    )

    dot.attr(
        "node",
        shape="box",
        style="rounded,filled",
        fontname="Arial"
    )

    evidence_index = 0
    pattern_index = 0
    alert_index = 0

    # ----------------------------------------------------------
    # EVIDENCE → PATTERN
    # ----------------------------------------------------------

    for pattern in patterns:

        if not isinstance(
            pattern,
            dict
        ):
            continue

        pattern_name = str(
            pattern.get(
                "name",
                "Clinical Pattern"
            )
        )

        pattern_id = (
            f"pattern_{pattern_index}"
        )

        dot.node(
            pattern_id,
            f"🧠 {pattern_name}",
            fillcolor="lightblue"
        )

        pattern_evidence = pattern.get(
            "evidence",
            []
        )

        if not isinstance(
            pattern_evidence,
            list
        ):

            pattern_evidence = [
                pattern_evidence
            ]

        for evidence_item in pattern_evidence:

            evidence_text = str(
                evidence_item
            ).strip()

            if not evidence_text:
                continue

            evidence_id = (
                f"evidence_{evidence_index}"
            )

            dot.node(
                evidence_id,
                f"📌 {evidence_text}",
                fillcolor="lightyellow"
            )

            dot.edge(
                evidence_id,
                pattern_id,
                label="supports"
            )

            evidence_index += 1

        pattern_index += 1

    # ----------------------------------------------------------
    # PATTERN → ALERT
    # ----------------------------------------------------------

    for alert in valid_alerts:

        level = str(
            alert.get(
                "level",
                "Information"
            )
        )

        message = str(
            alert.get(
                "message",
                "Clinical alert"
            )
        )

        alert_id = (
            f"alert_{alert_index}"
        )

        dot.node(
            alert_id,
            f"🚨 {level.upper()}\n{message}",
            fillcolor="mistyrose"
        )

        alert_evidence = alert.get(
            "evidence",
            ""
        )

        if alert_evidence:

            alert_evidence_id = (
                f"alert_evidence_{alert_index}"
            )

            dot.node(
                alert_evidence_id,
                f"📌 {alert_evidence}",
                fillcolor="lightyellow"
            )

            dot.edge(
                alert_evidence_id,
                alert_id,
                label="supports"
            )

        alert_index += 1

    # ----------------------------------------------------------
    # DISPLAY GRAPH
    # ----------------------------------------------------------

    if (
        evidence_index > 0
        or pattern_index > 0
        or alert_index > 0
    ):

        st.graphviz_chart(
            dot,
            use_container_width=True
        )

        # ------------------------------------------------------
        # GRAPH METRICS
        # ------------------------------------------------------

        c1, c2 = st.columns(2)

        with c1:

            st.metric(
                "Evidence Nodes",
                (
                    graph_nodes
                    if graph_nodes
                    else evidence_index
                )
            )

        with c2:

            st.metric(
                "Evidence Relationships",
                (
                    graph_relationships
                    if graph_relationships
                    else evidence_index
                )
            )

    else:

        st.info(
            "No evidence relationships are available "
            "in the current analysis."
        )

    st.caption(
        "The graph connects documented clinical evidence "
        "to AI-identified patterns and alerts."
    )

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )

    # ==========================================================
    # DISCLAIMER
    # ==========================================================

    st.markdown(
        '<div class="mp-disclaimer">'
        'AI-generated information is based on documented '
        'patient data and should be reviewed and verified '
        'by a qualified healthcare professional. '
        'It is not a diagnosis.'
        '</div>',
        unsafe_allow_html=True,
    )

    # ==========================================================
    # CLOSE MAIN CARD
    # ==========================================================

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )
def alerts_page(patient_id):

    patient, _ = get_patient_record(patient_id)

    if patient is None:
        empty_state("Patient record not found.")
        return

    # ==========================================================
    # GET LATEST AI ANALYSIS
    # ==========================================================

    documents = patient.get("documents", [])

    analysis_data = None

    for document in reversed(documents):

        data = document.get("analysis_data")

        if isinstance(data, dict) and (
            data.get("alerts")
            or data.get("overall_summary")
            or data.get("patterns")
        ):
            analysis_data = data
            break

    # ==========================================================
    # PAGE HEADER
    # ==========================================================

    st.markdown(
        '<div class="mp-card"><h4>🚨 Alerts</h4>',
        unsafe_allow_html=True,
    )

    st.caption(
        "Important clinical findings identified from "
        "the documented patient information."
    )

    if not analysis_data:

        empty_state(
            "No AI-generated alerts available yet."
        )

        st.markdown(
            "</div>",
            unsafe_allow_html=True,
        )

        return

    # ==========================================================
    # ALERTS
    # ==========================================================

    alerts = analysis_data.get(
        "alerts",
        []
    )

    if alerts:

        for index, alert in enumerate(
            alerts,
            start=1
        ):

            # --------------------------------------------------
            # Handle unexpected alert format safely
            # --------------------------------------------------

            if isinstance(alert, str):

                level = "Information"
                message = alert
                evidence = ""

            elif isinstance(alert, dict):

                level = alert.get(
                    "level",
                    "Information"
                )

                message = alert.get(
                    "message",
                    "No alert message available."
                )

                evidence = alert.get(
                    "evidence",
                    ""
                )

            else:

                continue

            # --------------------------------------------------
            # HIGH ALERT
            # --------------------------------------------------

            if str(level).lower() == "high":

                st.error(
                    f"🚨 **HIGH ALERT**\n\n"
                    f"{message}"
                )

            # --------------------------------------------------
            # MEDIUM ALERT
            # --------------------------------------------------

            elif str(level).lower() == "medium":

                st.warning(
                    f"⚠️ **MEDIUM ALERT**\n\n"
                    f"{message}"
                )

            # --------------------------------------------------
            # LOW / INFORMATION
            # --------------------------------------------------

            else:

                st.info(
                    f"ℹ️ **{level}**\n\n"
                    f"{message}"
                )

            # --------------------------------------------------
            # CLINICAL EVIDENCE
            # --------------------------------------------------

            if evidence:

                with st.expander(
                    f"🔎 View supporting evidence — Alert {index}"
                ):

                    st.write(
                        evidence
                    )

            if index < len(alerts):

                st.markdown("---")

    else:

        st.success(
            "✅ No active clinical alerts identified "
            "from the available patient data."
        )

    # ==========================================================
    # ALERT SUMMARY
    # ==========================================================

    st.markdown("---")

    st.markdown(
        "### 🧠 Alert Summary"
    )

    high_count = 0
    medium_count = 0
    low_count = 0

    for alert in alerts:

        if not isinstance(alert, dict):
            continue

        level = str(
            alert.get(
                "level",
                ""
            )
        ).lower()

        if level == "high":

            high_count += 1

        elif level == "medium":

            medium_count += 1

        elif level == "low":

            low_count += 1

    c1, c2, c3 = st.columns(3)

    with c1:

        st.metric(
            "High",
            high_count
        )

    with c2:

        st.metric(
            "Medium",
            medium_count
        )

    with c3:

        st.metric(
            "Low",
            low_count
        )

    # ==========================================================
    # DISCLAIMER
    # ==========================================================

    st.markdown(
        '<div class="mp-disclaimer">'
        'Alerts are AI-generated from documented patient '
        'information and are intended to support clinical '
        'review. They are not a diagnosis.'
        '</div>',
        unsafe_allow_html=True,
    )

    # ==========================================================
    # CLOSE CARD
    # ==========================================================

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )


# ==============================================================
# CLINICAL EVIDENCE GRAPH FRONTEND COMPONENTS
# ==============================================================

def render_clinical_evidence_dashboard_card(patient_id, patient_record):
    st.markdown('<div class="mp-card"><h4>🕸️ Clinical Evidence</h4>', unsafe_allow_html=True)
    
    if patient_id and patient_record:
        sync_patient_evidence_to_backend(patient_id, patient_record)
        
    res = get_patient_evidence_graph(patient_id)
    if res.status_code == 200:
        graph_data = res.json()
        nodes = graph_data.get("nodes", [])
        total_nodes = len(nodes)
        symptoms_cnt = len([n for n in nodes if n.get("evidence_type") == "SYMPTOM"])
        labs_cnt = len([n for n in nodes if n.get("evidence_type") == "LAB_RESULT"])
        meds_cnt = len([n for n in nodes if n.get("evidence_type") == "MEDICATION"])
        docs_cnt = len([n for n in nodes if n.get("evidence_type") == "DOCUMENT"])
        
        last_update = nodes[-1].get("created_at", "N/A")[:10] if nodes else "N/A"
        
        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("Total Nodes", total_nodes)
            st.metric("Symptoms", symptoms_cnt)
        with c2:
            st.metric("Lab Findings", labs_cnt)
            st.metric("Medications", meds_cnt)
        with c3:
            st.metric("Documents", docs_cnt)
            st.caption(f"Last Update: {last_update}")
    else:
        st.write("Evidence node data loading...")

    if st.button("View Evidence Graph", key=f"btn_view_graph_{patient_id}", use_container_width=True):
        st.session_state.page = "Clinical Evidence Graph"
        st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)


def render_clinical_evidence_graph_page(patient_id, patient_record=None):
    st.markdown("## 🕸️ Clinical Evidence Graph")

    st.info(
        "⚠️ **AI Decision Support Notice**: This Clinical Evidence Graph visually connects patient findings, symptoms, laboratory tests, diagnoses, and medical documents. "
        "All graph relationships are intended strictly as clinical decision-support and must be verified by a healthcare professional."
    )

    if not patient_id:
        st.error("No patient selected.")
        return

    if patient_record:
        sync_patient_evidence_to_backend(patient_id, patient_record)

    res = get_patient_evidence_graph(patient_id)
    if res.status_code != 200:
        st.error("Failed to fetch evidence graph from backend API.")
        return

    graph_data = res.json()
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    if not nodes:
        empty_state("No evidence nodes found yet for this patient. Upload a document or record symptoms/labs to populate the evidence graph.")
        return

    # Metrics Summary Row
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total Evidence Nodes", len(nodes))
    m2.metric("Symptoms", len([n for n in nodes if n.get("evidence_type") == "SYMPTOM"]))
    m3.metric("Lab Results", len([n for n in nodes if n.get("evidence_type") == "LAB_RESULT"]))
    m4.metric("Medications", len([n for n in nodes if n.get("evidence_type") == "MEDICATION"]))
    m5.metric("Documents", len([n for n in nodes if n.get("evidence_type") == "DOCUMENT"]))

    st.write("")

    # Visual Graphviz Digraph
    dot = graphviz.Digraph(comment="Clinical Evidence Graph")
    dot.attr(rankdir="LR", bgcolor="transparent")
    dot.attr("node", shape="box", style="rounded,filled", fontname="Arial", fontsize="10")

    color_map = {
        "SYMPTOM": "#FFF3E0",
        "LAB_RESULT": "#E3F2FD",
        "MEDICATION": "#E8F5E9",
        "DIAGNOSIS": "#FFEBEE",
        "CLINICAL_NOTE": "#F3E5F5",
        "DOCUMENT": "#E0F2F1",
        "TIMELINE_EVENT": "#ECEFF1"
    }

    font_color_map = {
        "SYMPTOM": "#E65100",
        "LAB_RESULT": "#0D47A1",
        "MEDICATION": "#1B5E20",
        "DIAGNOSIS": "#B71C1C",
        "CLINICAL_NOTE": "#4A148C",
        "DOCUMENT": "#004D40",
        "TIMELINE_EVENT": "#37474F"
    }

    for n in nodes:
        nid = f"n_{n['id']}"
        etype = n.get("evidence_type", "CLINICAL_NOTE")
        name = n.get("name", "Unnamed")
        val = n.get("value")
        unit = n.get("unit")
        estate = n.get("evidence_state", "PRESENT")

        lbl = f"{name}\n[{etype}]"
        if val:
            lbl += f"\nValue: {val} {unit or ''}".strip()
        if estate == "NOT_YET_TESTED":
            lbl += "\n(Not Yet Tested)"

        bg = color_map.get(etype, "#FFFFFF")
        fg = font_color_map.get(etype, "#000000")
        dot.node(nid, label=lbl, fillcolor=bg, fontcolor=fg, color=fg)

    for e in edges:
        src = f"n_{e['source_node_id']}"
        tgt = f"n_{e['target_node_id']}"
        rel = e.get("relationship_type", "ASSOCIATED_WITH")
        dot.edge(src, tgt, label=rel, fontname="Arial", fontsize="8", color="#546E7A")

    st.graphviz_chart(dot, use_container_width=True)

    st.markdown("---")
    st.markdown("### 📋 Evidence Detail Panel")

    node_options = {f"[{n.get('evidence_type')}] {n.get('name')} (ID: {n['id']})": n['id'] for n in nodes}
    selected_option = st.selectbox("Select an evidence node to view detailed provenance and clinical metadata:", list(node_options.keys()))

    if selected_option:
        sel_id = node_options[selected_option]
        det_res = get_evidence_details(patient_id, sel_id)

        if det_res.status_code == 200:
            det = det_res.json()
            n_data = det.get("node", {})
            out_edges = det.get("outgoing_edges", [])
            in_edges = det.get("incoming_edges", [])
            rel_nodes = det.get("related_nodes", [])

            c_left, c_right = st.columns(2)

            with c_left:
                st.markdown(f"#### Evidence: {n_data.get('name') or 'Not available'}")
                st.write(f"**Value:** {n_data.get('value') if n_data.get('value') is not None else 'Not available'} {n_data.get('unit') or ''}")
                st.write(f"**Date:** {n_data.get('date') if n_data.get('date') is not None else 'Not available'}")
                st.write(f"**Type:** {n_data.get('evidence_type') or 'Not available'}")
                st.write(f"**Evidence state:** `{n_data.get('evidence_state') or 'Not available'}`")

            with c_right:
                st.write(f"**Source Document:** {n_data.get('source_document_name') if n_data.get('source_document_name') is not None else 'Not available'}")
                st.write(f"**Source Type:** {n_data.get('source_type') if n_data.get('source_type') is not None else 'Not available'}")
                st.write(f"**Confidence:** {n_data.get('confidence') if n_data.get('confidence') is not None else 'Not available'}")
                st.write(f"**Verification:** {n_data.get('verification_status') if n_data.get('verification_status') is not None else 'Not available'}")
                st.write(f"**Recorded Date:** {n_data.get('created_at') if n_data.get('created_at') is not None else 'Not available'}")

            st.markdown("##### 🔗 Related Evidence & Relationships")
            if not out_edges and not in_edges:
                st.write("Not available (No active graph relationships)")
            else:
                for edge in out_edges:
                    tgt_item = next((r for r in rel_nodes if r['id'] == edge['target_node_id']), None)
                    tgt_name = tgt_item.get('name') if tgt_item else f"Node #{edge['target_node_id']}"
                    st.write(f"- **{n_data.get('name')}** --[`{edge.get('relationship_type')}`]--> **{tgt_name}** (Confidence: {edge.get('confidence', 'High')})")
                for edge in in_edges:
                    src_item = next((r for r in rel_nodes if r['id'] == edge['source_node_id']), None)
                    src_name = src_item.get('name') if src_item else f"Node #{edge['source_node_id']}"
                    st.write(f"- **{src_name}** --[`{edge.get('relationship_type')}`]--> **{n_data.get('name')}** (Confidence: {edge.get('confidence', 'High')})")


# ==============================================================
# DOCTOR DASHBOARD
# ==============================================================

def doctor_dashboard_page(user):

    # ==========================================================
    # HEADER
    # ==========================================================

    st.markdown(
        f'<div class="mp-logo">{APP_ICON} {APP_NAME}</div>',
        unsafe_allow_html=True,
    )

    st.markdown("### Doctor Dashboard")

    st.caption(
        f"Welcome back, "
        f"{user.get('full_name', 'Doctor')}"
    )

    patients = load_db(PATIENTS_FILE)



    # ==========================================================
    # PATIENT IDENTIFICATION
    # ==========================================================

    st.markdown(
        '<div class="mp-card"><h4>Patient Identification</h4>',
        unsafe_allow_html=True,
    )

    options = {}

    for patient_id, patient_record in patients.items():

        name = patient_record.get(
            "name",
            "Unknown Patient",
        )

        options[
            f"{name} ({patient_id})"
        ] = patient_id

    if options:

        option_labels = list(options.keys())

        current_patient_id = (
            st.session_state.get(
                "selected_patient_id"
            )
        )

        current_index = 0

        if current_patient_id:

            for index, label in enumerate(option_labels):

                if options[label] == current_patient_id:

                    current_index = index
                    break

        choice = st.selectbox(
            "Select Patient",
            option_labels,
            index=current_index,
        )

        if st.button(
            "🔍 Load Patient"
        ):

            st.session_state.selected_patient_id = (
                options[choice]
            )

            st.rerun()

    else:

        empty_state(
            "No patients available yet."
        )

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )

    # ==========================================================
    # SELECTED PATIENT
    # ==========================================================

    patient_id = st.session_state.get(
        "selected_patient_id"
    )

    patient = patients.get(
        patient_id
    ) if patient_id else None

    # ==========================================================
    # FIND LATEST AI ANALYSIS
    # ==========================================================

    analysis_data = None

    if patient:

        documents = patient.get(
            "documents",
            []
        )

        for document in reversed(documents):

            if not isinstance(
                document,
                dict
            ):
                continue

            data = document.get(
                "analysis_data"
            )

            if isinstance(data, dict) and (
                data.get("overall_summary")
                or data.get("patterns")
                or data.get("alerts")
                or data.get("timeline")
                or data.get("evidence_graph")
                or data.get("clinical_notes")
            ):

                analysis_data = data
                break

    # ==========================================================
    # COMMON ANALYSIS DATA
    # ==========================================================

    if analysis_data:

        overall_summary = analysis_data.get(
            "overall_summary",
            ""
        )

        patterns = analysis_data.get(
            "patterns",
            []
        )

        alerts = analysis_data.get(
            "alerts",
            []
        )

        missing_data = analysis_data.get(
            "missing_data",
            []
        )

        care_gaps = analysis_data.get(
            "care_gaps",
            []
        )

        next_best_test = analysis_data.get(
            "next_best_test"
        )

        timeline = analysis_data.get(
            "timeline",
            []
        )

        clinical_notes = analysis_data.get(
            "clinical_notes",
            ""
        )

        evidence_graph = analysis_data.get(
            "evidence_graph"
        )

    else:

        overall_summary = ""
        patterns = []
        alerts = []
        missing_data = []
        care_gaps = []
        next_best_test = None
        timeline = []
        clinical_notes = ""
        evidence_graph = None

    # ==========================================================
    # METRICS
    # ==========================================================

    analyzed_patient_count = 0
    high_priority = 0

    for other_patient_id, other_patient in patients.items():

        other_analysis = None

        for document in reversed(
            other_patient.get(
                "documents",
                []
            )
        ):

            if not isinstance(
                document,
                dict
            ):
                continue

            data = document.get(
                "analysis_data"
            )

            if isinstance(data, dict) and (
                data.get("overall_summary")
                or data.get("patterns")
                or data.get("alerts")
            ):

                other_analysis = data
                break

        if other_analysis:

            analyzed_patient_count += 1

            other_alerts = other_analysis.get(
                "alerts",
                []
            )

            for alert in other_alerts:

                if not isinstance(
                    alert,
                    dict
                ):
                    continue

                if str(
                    alert.get(
                        "level",
                        ""
                    )
                ).lower() == "high":

                    high_priority += 1

    m1, m2, m3, m4 = st.columns(4)

    with m1:

        st.markdown(
            f'<div class="mp-metric">'
            f'<h3>{len(patients)}</h3>'
            f'Total Patients'
            f'</div>',
            unsafe_allow_html=True,
        )

    with m2:

        st.markdown(
            f'<div class="mp-metric">'
            f'<h3>{analyzed_patient_count}</h3>'
            f'Analyzed Patients'
            f'</div>',
            unsafe_allow_html=True,
        )

    with m3:

        st.markdown(
            f'<div class="mp-metric">'
            f'<h3>{len(alerts)}</h3>'
            f'Active Alerts'
            f'</div>',
            unsafe_allow_html=True,
        )

    with m4:

        st.markdown(
            f'<div class="mp-metric">'
            f'<h3>{high_priority}</h3>'
            f'High Priority'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.write("")

    # ==========================================================
    # PATIENT OVERVIEW
    # ==========================================================

    st.markdown(
        '<div class="mp-card"><h4>Patient Overview</h4>',
        unsafe_allow_html=True,
    )

    if not patient:

        empty_state(
            "Select a patient to view their overview."
        )

    else:

        st.write(
            f"**Patient ID:** "
            f"{patient.get('patient_id', patient_id)}"
        )

        c1, c2, c3, c4 = st.columns(4)

        with c1:

            st.write(
                f"**Name**\n\n"
                f"{patient.get('name', '-')}"
            )

        with c2:

            st.write(
                f"**Age**\n\n"
                f"{patient.get('age', '-')}"
            )

        with c3:

            st.write(
                f"**Gender**\n\n"
                f"{patient.get('gender', '-')}"
            )

        with c4:

            st.write(
                f"**Blood Group**\n\n"
                f"{patient.get('bloodGroup') or 'Not recorded'}"
            )

        current_condition = "Not identified"

        if patterns:

            first_pattern = patterns[0]

            if isinstance(
                first_pattern,
                dict
            ):

                current_condition = first_pattern.get(
                    "name",
                    "Not identified"
                )

        c1, c2, c3, c4 = st.columns(4)

        with c1:

            st.write(
                f"**Current Condition**\n\n"
                f"{current_condition}"
            )

        with c2:

            st.write(
                "**Department**\n\n"
                "Not recorded"
            )

        with c3:

            st.write(
                f"**Attending Doctor**\n\n"
                f"{user.get('full_name', '-')}"
            )

        with c4:

            st.write(
                "**Next Appointment**\n\n"
                "Not recorded"
            )

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )

    if patient and patient_id:
        st.write("")
        render_clinical_evidence_dashboard_card(patient_id, patient)


    # ==========================================================
    # AI CLINICAL SUMMARY
    # ==========================================================

    st.markdown(
        '<div class="mp-card"><h4>🧠 AI Clinical Summary</h4>',
        unsafe_allow_html=True,
    )

    if patient and analysis_data:

        if overall_summary:

            st.info(
                overall_summary
            )

        else:

            st.info(
                "No overall AI summary is available."
            )

    elif patient:

        st.info(
            "No document analysis is available for this patient yet. "
            "Upload and process a medical document first."
        )

    else:

        empty_state(
            "Select a patient to view the AI clinical summary."
        )

    st.markdown(
        '<div class="mp-disclaimer">'
        'AI-generated information should be verified before clinical use.'
        '</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )

    # ==========================================================
    # AI ANALYSIS
    # ==========================================================

    st.markdown(
        '<div class="mp-card"><h4>🔎 AI Analysis</h4>',
        unsafe_allow_html=True,
    )

    if not patient:

        empty_state(
            "Select a patient to view AI analysis."
        )

    elif not analysis_data:

        st.info(
            "No AI analysis is available yet. "
            "Upload and process a medical document first."
        )

    else:

        # ------------------------------------------------------
        # PATTERNS
        # ------------------------------------------------------

        st.markdown(
            "#### Clinical Patterns & Observations"
        )

        if patterns:

            for index, pattern in enumerate(
                patterns,
                start=1
            ):

                if not isinstance(
                    pattern,
                    dict
                ):
                    continue

                name = pattern.get(
                    "name",
                    "Clinical pattern"
                )

                likelihood = pattern.get(
                    "likelihood",
                    "Not specified"
                )

                confidence = pattern.get(
                    "confidence"
                )

                evidence = pattern.get(
                    "evidence",
                    []
                )

                st.markdown(
                    f"**{index}. {name}**"
                )

                c1, c2 = st.columns(2)

                with c1:

                    st.write(
                        f"**Likelihood:** "
                        f"{likelihood}"
                    )

                with c2:

                    if confidence is not None:

                        st.write(
                            f"**Confidence:** "
                            f"{confidence}%"
                        )

                        try:

                            confidence_value = (
                                float(confidence)
                            )

                            st.progress(
                                min(
                                    max(
                                        confidence_value,
                                        0
                                    ),
                                    100
                                ) / 100
                            )

                        except (
                            ValueError,
                            TypeError
                        ):

                            pass

                if evidence:

                    st.markdown(
                        "**Evidence:**"
                    )

                    if isinstance(
                        evidence,
                        list
                    ):

                        for item in evidence:

                            st.markdown(
                                f"- {item}"
                            )

                    else:

                        st.markdown(
                            f"- {evidence}"
                        )

                st.markdown("---")

        else:

            st.info(
                "No clinical patterns were identified."
            )

        # ------------------------------------------------------
        # ALERTS
        # ------------------------------------------------------

        st.markdown(
            "#### 🚨 Clinical Alerts"
        )

        if alerts:

            for index, alert in enumerate(
                alerts,
                start=1
            ):

                if isinstance(
                    alert,
                    str
                ):

                    level = "Information"
                    message = alert
                    evidence = ""

                elif isinstance(
                    alert,
                    dict
                ):

                    level = alert.get(
                        "level",
                        "Information"
                    )

                    message = alert.get(
                        "message",
                        "No alert message available."
                    )

                    evidence = alert.get(
                        "evidence",
                        ""
                    )

                else:

                    continue

                level_text = str(
                    level
                ).lower()

                if level_text == "high":

                    st.error(
                        f"🚨 **HIGH ALERT**\n\n"
                        f"{message}"
                    )

                elif level_text == "medium":

                    st.warning(
                        f"⚠️ **MEDIUM ALERT**\n\n"
                        f"{message}"
                    )

                else:

                    st.info(
                        f"ℹ️ **{level}**\n\n"
                        f"{message}"
                    )

                if evidence:

                    st.caption(
                        f"Evidence: {evidence}"
                    )

        else:

            st.success(
                "No active clinical alerts identified."
            )

        # ------------------------------------------------------
        # MISSING DATA
        # ------------------------------------------------------

        st.markdown(
            "#### ⚠️ Missing Data"
        )

        if missing_data:

            for item in missing_data:

                st.markdown(
                    f"- {item}"
                )

        else:

            st.success(
                "No important missing data identified."
            )

        # ------------------------------------------------------
        # CARE GAPS
        # ------------------------------------------------------

        st.markdown(
            "#### 🏥 Potential Care Gaps"
        )

        if care_gaps:

            for gap in care_gaps:

                st.warning(
                    f"• {gap}"
                )

        else:

            st.success(
                "No potential care gaps identified."
            )

        # ------------------------------------------------------
        # NEXT BEST TEST
        # ------------------------------------------------------

        st.markdown(
            "#### 🔬 Next Best Test"
        )

        if isinstance(
            next_best_test,
            dict
        ):

            test_name = next_best_test.get(
                "name",
                "Not specified"
            )

            reason = next_best_test.get(
                "reason",
                ""
            )

            st.write(
                f"**Recommended investigation:** "
                f"{test_name}"
            )

            if reason:

                st.info(
                    reason
                )

        elif next_best_test:

            st.info(
                str(next_best_test)
            )

        else:

            st.info(
                "No next best test identified."
            )

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )

    # ==========================================================
    # CLINICAL TRENDS
    # ==========================================================

    st.markdown(
        '<div class="mp-card"><h4>📈 Clinical Trends</h4>',
        unsafe_allow_html=True,
    )

    if not patient:

        empty_state(
            "Select a patient to view clinical trends."
        )

    elif not analysis_data:

        st.info(
            "Clinical trends will appear after document analysis."
        )

    else:

        trend_data = (
            analysis_data.get(
                "clinical_trends"
            )
            or analysis_data.get(
                "trends"
            )
            or []
        )

        if isinstance(
            trend_data,
            dict
        ):

            # Support formats such as:
            # {"hemoglobin": [{"date": "...", "value": 9.2}]}

            for metric_name, metric_values in trend_data.items():

                if not isinstance(
                    metric_values,
                    list
                ):
                    continue

                valid_points = []

                for point in metric_values:

                    if not isinstance(
                        point,
                        dict
                    ):
                        continue

                    date_value = (
                        point.get("date")
                        or point.get("timestamp")
                        or point.get("time")
                    )

                    numeric_value = (
                        point.get("value")
                    )

                    if (
                        date_value is not None
                        and isinstance(
                            numeric_value,
                            (int, float)
                        )
                    ):

                        valid_points.append({
                            "Date": str(
                                date_value
                            ),
                            metric_name: numeric_value
                        })

                if valid_points:

                    st.markdown(
                        f"**{metric_name}**"
                    )

                    trend_frame = (
                        __import__(
                            "pandas"
                        ).DataFrame(
                            valid_points
                        )
                    )

                    st.line_chart(
                        trend_frame.set_index(
                            "Date"
                        )
                    )

        elif isinstance(
            trend_data,
            list
        ):

            valid_rows = []

            for point in trend_data:

                if not isinstance(
                    point,
                    dict
                ):
                    continue

                date_value = (
                    point.get("date")
                    or point.get("timestamp")
                    or point.get("time")
                )

                metric_name = (
                    point.get("metric")
                    or point.get("name")
                    or point.get("test")
                    or point.get("parameter")
                )

                numeric_value = (
                    point.get("value")
                )

                if (
                    date_value is not None
                    and metric_name
                    and isinstance(
                        numeric_value,
                        (int, float)
                    )
                ):

                    valid_rows.append({
                        "Date": str(
                            date_value
                        ),
                        "Metric": str(
                            metric_name
                        ),
                        "Value": numeric_value
                    })

            if valid_rows:

                trend_frame = (
                    __import__(
                        "pandas"
                    ).DataFrame(
                        valid_rows
                    )
                )

                for metric_name in (
                    trend_frame["Metric"]
                    .unique()
                ):

                    metric_frame = (
                        trend_frame[
                            trend_frame["Metric"]
                            == metric_name
                        ][
                            ["Date", "Value"]
                        ]
                    )

                    if len(metric_frame) >= 1:

                        st.markdown(
                            f"**{metric_name}**"
                        )

                        st.line_chart(
                            metric_frame.set_index(
                                "Date"
                            )
                        )

        # ------------------------------------------------------
        # FALLBACK TO TIMELINE
        # ------------------------------------------------------

        if not trend_data and timeline:

            st.markdown(
                "**Timeline-based clinical progression**"
            )

            for event in timeline:

                if not isinstance(
                    event,
                    dict
                ):
                    continue

                event_date = event.get(
                    "date",
                    ""
                )

                event_name = event.get(
                    "event",
                    "Medical event"
                )

                st.markdown(
                    f"**{event_date}** — "
                    f"{event_name}"
                )

        elif not trend_data and not timeline:

            st.info(
                "No structured clinical trend data is available "
                "in the current AI analysis."
            )

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )

    # ==========================================================
    # AI INSIGHTS SUMMARY
    # ==========================================================

    st.markdown(
        '<div class="mp-card"><h4>💡 AI Insights Summary</h4>',
        unsafe_allow_html=True,
    )

    if not patient:

        empty_state(
            "Select a patient to view AI insights."
        )

    elif not analysis_data:

        st.info(
            "AI insights will appear after document analysis."
        )

    else:

        insight_rows = []

        symptoms = patient.get(
            "symptoms",
            []
        )

        medications = patient.get(
            "medications",
            []
        )

        if isinstance(
            symptoms,
            list
        ):

            symptom_names = []

            for symptom in symptoms:

                if isinstance(
                    symptom,
                    dict
                ):

                    symptom_names.append(
                        str(
                            symptom.get(
                                "name",
                                ""
                            )
                        )
                    )

                else:

                    symptom_names.append(
                        str(symptom)
                    )

            symptom_text = ", ".join(
                x for x in symptom_names
                if x
            )

        else:

            symptom_text = str(
                symptoms
            )

        if isinstance(
            medications,
            list
        ):

            medication_names = []

            for medication in medications:

                if isinstance(
                    medication,
                    dict
                ):

                    medication_names.append(
                        str(
                            medication.get(
                                "name",
                                ""
                            )
                        )
                    )

                else:

                    medication_names.append(
                        str(medication)
                    )

            medication_text = ", ".join(
                x for x in medication_names
                if x
            )

        else:

            medication_text = str(
                medications
            )

        first_pattern = "Not identified"

        if patterns:

            if isinstance(
                patterns[0],
                dict
            ):

                first_pattern = patterns[0].get(
                    "name",
                    "Not identified"
                )

        insight_rows.append(
            (
                "Symptoms Identified",
                symptom_text or "None recorded"
            )
        )

        insight_rows.append(
            (
                "Possible Clinical Pattern",
                first_pattern
            )
        )

        insight_rows.append(
            (
                "Medications",
                medication_text or "None recorded"
            )
        )

        allergies = (
            patient.get(
                "additional",
                {}
            )
            .get(
                "allergies",
                "Not recorded"
            )
            if isinstance(
                patient.get(
                    "additional",
                    {}
                ),
                dict
            )
            else "Not recorded"
        )

        insight_rows.append(
            (
                "Allergies",
                allergies or "Not recorded"
            )
        )

        insight_rows.append(
            (
                "Active Alerts",
                str(len(alerts))
            )
        )

        insight_rows.append(
            (
                "Missing Information",
                (
                    ", ".join(
                        str(x)
                        for x in missing_data
                    )
                    if isinstance(
                        missing_data,
                        list
                    ) and missing_data
                    else "None flagged"
                )
            )
        )

        insight_rows.append(
            (
                "Potential Care Gaps",
                (
                    ", ".join(
                        str(x)
                        for x in care_gaps
                    )
                    if isinstance(
                        care_gaps,
                        list
                    ) and care_gaps
                    else "None identified"
                )
            )
        )

        for label, value in insight_rows:

            c1, c2 = st.columns(
                [2, 3]
            )

            with c1:

                st.write(
                    f"**{label}**"
                )

            with c2:

                st.write(
                    value
                )

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )

    # ==========================================================
    # CLINICAL EVIDENCE GRAPH
    # ==========================================================

    st.markdown(
        '<div class="mp-card"><h4>🧠 Clinical Evidence Graph</h4>',
        unsafe_allow_html=True,
    )

    if not patient:

        empty_state(
            "Select a patient to view the evidence graph."
        )

    elif not analysis_data:

        st.info(
            "The evidence graph will appear after document analysis."
        )

    else:

        if isinstance(
            evidence_graph,
            dict
        ):

            nodes = evidence_graph.get(
                "nodes",
                0
            )

            relationships = evidence_graph.get(
                "relationships",
                0
            )

            c1, c2 = st.columns(2)

            with c1:

                st.metric(
                    "Evidence Nodes",
                    nodes
                )

            with c2:

                st.metric(
                    "Evidence Relationships",
                    relationships
                )

        graph_relationships = []

        for pattern in patterns:

            if not isinstance(
                pattern,
                dict
            ):
                continue

            pattern_name = pattern.get(
                "name",
                "Clinical Pattern"
            )

            pattern_evidence = pattern.get(
                "evidence",
                []
            )

            if not isinstance(
                pattern_evidence,
                list
            ):

                pattern_evidence = [
                    str(
                        pattern_evidence
                    )
                ]

            for evidence_item in pattern_evidence:

                graph_relationships.append({
                    "evidence": str(
                        evidence_item
                    ),
                    "pattern": str(
                        pattern_name
                    )
                })

        for alert in alerts:

            if not isinstance(
                alert,
                dict
            ):
                continue

            alert_message = alert.get(
                "message",
                ""
            )

            alert_evidence = alert.get(
                "evidence",
                ""
            )

            if alert_evidence:

                graph_relationships.append({
                    "evidence": str(
                        alert_evidence
                    ),
                    "pattern": (
                        f"Alert: "
                        f"{alert_message}"
                    )
                })

        if graph_relationships:

            st.markdown(
                "**Evidence relationships identified "
                "from the patient record:**"
            )

            for relation in graph_relationships:

                st.markdown(
                    f"""
                    <div style="
                        padding:12px 15px;
                        margin-bottom:8px;
                        border:1px solid #e5e7eb;
                        border-radius:8px;
                        background:#f9fafb;
                    ">
                        <b>📌 Evidence</b><br>
                        {relation["evidence"]}
                        <br><br>
                        <b>↓ Supports</b><br>
                        🧠 {relation["pattern"]}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        else:

            st.info(
                "No evidence relationships are available "
                "in the current analysis."
            )

        st.caption(
            "The evidence graph connects documented clinical "
            "evidence to AI-identified patterns and alerts."
        )

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )

    # ==========================================================
    # CLINICAL NOTES
    # ==========================================================

    st.markdown(
        '<div class="mp-card"><h4>📝 Clinical Notes</h4>',
        unsafe_allow_html=True,
    )

    if not patient:

        empty_state(
            "Select a patient to view clinical notes."
        )

    else:

        if clinical_notes:

            st.info(
                clinical_notes
            )

        else:

            st.info(
                "No AI-generated clinical notes are available."
            )

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )

    # ==========================================================
    # DOCTOR NOTES
    # ==========================================================

    st.markdown(
        '<div class="mp-card"><h4>✍️ Doctor Notes</h4>',
        unsafe_allow_html=True,
    )

    if not patient:

        empty_state(
            "Select a patient to add doctor notes."
        )

    else:

        existing_doctor_notes = patient.get(
            "doctor_notes",
            ""
        )

        note_key = (
            f"doctor_notes_{patient_id}"
        )

        if note_key not in st.session_state:

            st.session_state[note_key] = (
                existing_doctor_notes
            )

        doctor_note = st.text_area(
            "Clinical note",
            value=st.session_state[note_key],
            height=160,
            key=note_key,
            placeholder=(
                "Enter clinical observations, "
                "follow-up notes, treatment decisions, "
                "or other doctor notes..."
            ),
        )

        if st.button(
            "💾 Save Doctor Notes",
            key=f"save_doctor_notes_{patient_id}"
        ):

            patients[patient_id][
                "doctor_notes"
            ] = doctor_note

            with open(
                PATIENTS_FILE,
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    patients,
                    file,
                    indent=2,
                    ensure_ascii=False
                )

            st.success(
                "Doctor notes saved successfully."
            )

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )

    # ==========================================================
    # DOCUMENTS
    # ==========================================================

    st.markdown(
        '<div class="mp-card"><h4>📄 Documents & Reports</h4>',
        unsafe_allow_html=True,
    )

    if patient and patient.get(
        "documents"
    ):

        for document in patient[
            "documents"
        ]:

            document_name = document.get(
                "name",
                "Document"
            )

            document_type = document.get(
                "type",
                "unknown"
            )

            has_analysis = isinstance(
                document.get(
                    "analysis_data"
                ),
                dict
            )

            analysis_status = (
                "✅ Analysis Available"
                if has_analysis
                else "⏳ Analysis Not Available"
            )

            st.write(
                f"- **{document_name}** "
                f"({document_type}) — "
                f"{analysis_status}"
            )

    else:

        empty_state(
            "Uploaded reports, prescriptions, "
            "and scans will appear here."
        )

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )

    # ==========================================================
    # DISCLAIMER
    # ==========================================================

    st.markdown(
        '<div class="mp-disclaimer">'
        'AI-generated information is based on documented '
        'patient data and should be reviewed and verified '
        'by a qualified healthcare professional. '
        'It is not a diagnosis.'
        '</div>',
        unsafe_allow_html=True,
    )
# ==============================================================
# DOCTOR / CHW ADD PATIENT
# ==============================================================

def doctor_add_patient_form():

    st.markdown(
        '<div class="mp-card"><h4>Add / Register Patient</h4>',
        unsafe_allow_html=True,
    )

    with st.form(
        "doctor_add_patient"
    ):

        name = st.text_input(
            "Full Name"
        )

        c1, c2, c3 = st.columns(3)

        age = c1.number_input(
            "Age",
            min_value=0,
            max_value=120,
            step=1,
        )

        gender = c2.selectbox(
            "Gender",
            [
                "Female",
                "Male",
                "Other",
            ],
        )

        phone = c3.text_input(
            "Phone"
        )

        submitted = st.form_submit_button(
            "Create Patient Record",
            type="primary",
        )

    if submitted:

        if not name.strip():

            st.warning(
                "Please enter the patient's name."
            )

            return

        patients = load_db(
            PATIENTS_FILE
        )

        patient_id = new_id(
            "MP"
        )

        patients[patient_id] = {
            "patient_id": patient_id,
            "linked_user_id": None,
            "name": name.strip(),
            "age": int(age),
            "gender": gender,
            "bloodGroup": None,
            "phone": phone.strip(),
            "symptoms": [],
            "labs": [],
            "medications": [],
            "documents": [],
            "timeline": [],
            "appointments": [],
            "additional": {},
            "createdAt": datetime.now().isoformat(),
        }

        save_db(
            PATIENTS_FILE,
            patients,
        )

        st.success(
            f"Patient created with ID {patient_id}."
        )

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )


# ==============================================================
# CHW DASHBOARD
# ==============================================================

def chw_dashboard_page(user):

    st.markdown(
        f'<div class="mp-logo">{APP_ICON} {APP_NAME}</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        "### CHW Dashboard"
    )

    st.caption(
        f"Welcome, "
        f"{user.get('full_name', 'CHW')}"
    )

    patients = load_db(
        PATIENTS_FILE
    )

    col1, col2 = st.columns(2)

    with col1:

        st.markdown(
            '<div class="mp-card"><h4>Patient Coordination</h4>',
            unsafe_allow_html=True,
        )

        if patients:

            for patient_id, patient in patients.items():

                st.write(
                    f"- {patient.get('name', 'Unknown')} "
                    f"({patient_id})"
                )

        else:

            empty_state(
                "No patients assigned yet."
            )

        st.markdown(
            "</div>",
            unsafe_allow_html=True,
        )

    with col2:

        st.markdown(
            '<div class="mp-card"><h4>Follow-Up</h4>',
            unsafe_allow_html=True,
        )

        pending_appointments = []

        missing_documents = []

        for patient_id, patient in patients.items():

            for appointment in patient.get(
                "appointments",
                [],
            ):

                if appointment.get(
                    "status"
                ) == "Pending":

                    pending_appointments.append(
                        (
                            patient_id,
                            appointment,
                        )
                    )

            if not patient.get(
                "documents"
            ):

                missing_documents.append(
                    (
                        patient_id,
                        patient,
                    )
                )

        if pending_appointments:

            st.write(
                "**Upcoming Follow-ups**"
            )

            for patient_id, appointment in pending_appointments:

                patient = patients.get(
                    patient_id,
                    {},
                )

                st.write(
                    f"- {patient.get('name', 'Unknown')}: "
                    f"{appointment.get('reason', '')} "
                    f"on {appointment.get('date', '')}"
                )

        else:

            empty_state(
                "No follow-ups available."
            )

        if missing_documents:

            st.write(
                "**Documents Awaiting Upload**"
            )

            for patient_id, patient in missing_documents:

                st.write(
                    f"- {patient.get('name', 'Unknown')}"
                )

        else:

            empty_state(
                "No pending documents."
            )

        st.markdown(
            "</div>",
            unsafe_allow_html=True,
        )

    doctor_add_patient_form()


# ==============================================================
# CHATBOT
# ==============================================================

CHATBOT_SUGGESTIONS = {

    "Patient": [
        "Summarize my medical history",
        "What information is missing from my health record?",
        "Explain my timeline",
    ],

    "Doctor": [
        "Summarize this patient's medical history",
        "What follow-ups are pending?",
        "What information is missing?",
    ],

    "CHW": [
        "Which documents are still missing?",
        "When is the patient's next follow-up?",
        "Show pending coordination tasks",
    ],
}


def chatbot_reply(
    role,
    patient,
    question,
):

    if patient is None:

        return (
            "I don't have a patient selected yet, "
            "so I don't have any medical records to analyze."
        )

    has_data = bool(
        patient.get("symptoms")
        or patient.get("labs")
        or patient.get("documents")
        or patient.get("timeline")
        or patient.get("medications")
    )

    if not has_data:

        return (
            "I don't have any medical records to analyze yet."
        )

    question = question.lower()

    if "symptom" in question:

        symptoms = patient.get(
            "symptoms",
            [],
        )

        if symptoms:

            names = ", ".join(
                s.get("name", "")
                for s in symptoms
            )

            return (
                f"Recorded symptoms: {names}."
            )

        return (
            "No symptoms are recorded yet."
        )

    if "medication" in question:

        medications = patient.get(
            "medications",
            [],
        )

        if medications:

            names = ", ".join(
                m.get("name", "")
                for m in medications
            )

            return (
                f"Current/past medications on record: {names}."
            )

        return (
            "No medications are recorded yet."
        )

    if "missing" in question:

        missing = []

        if not patient.get(
            "labs"
        ):
            missing.append(
                "laboratory results"
            )

        if not patient.get(
            "documents"
        ):
            missing.append(
                "uploaded documents"
            )

        if not patient.get(
            "appointments"
        ):
            missing.append(
                "appointment history"
            )

        if missing:

            return (
                "Based on the current record, missing "
                "information includes: "
                + ", ".join(missing)
                + "."
            )

        return (
            "The record looks reasonably complete "
            "based on what's been entered."
        )

    if (
        "timeline" in question
        or "history" in question
        or "summar" in question
    ):

        timeline = patient.get(
            "timeline",
            [],
        )

        if timeline:

            events = "; ".join(
                f"{event.get('date', '')}: "
                f"{event.get('type', '')} - "
                f"{event.get('description', '')}"
                for event in timeline[-5:]
            )

            return (
                f"Here are the most recent recorded events: "
                f"{events}."
            )

        return (
            "There are no timeline events recorded yet."
        )

    if (
        "follow" in question
        or "pending" in question
    ):

        pending = [
            appointment
            for appointment in patient.get(
                "appointments",
                [],
            )
            if appointment.get(
                "status"
            ) == "Pending"
        ]

        if pending:

            return (
                f"There are {len(pending)} "
                "pending follow-up item(s) on record."
            )

        return (
            "No pending follow-ups are recorded."
        )

    if "document" in question:

        documents = patient.get(
            "documents",
            [],
        )

        if documents:

            names = ", ".join(
                d.get("name", "")
                for d in documents
            )

            return (
                f"Documents on record: {names}."
            )

        return (
            "No documents have been uploaded yet."
        )

    return (
        "I can only answer using what's actually "
        "recorded for this patient. Try asking about "
        "symptoms, medications, the timeline, "
        "or missing information."
    )


def render_chatbot():

    user = current_user()

    if not user:
        return

    role = st.session_state.get(
        "role"
    )

    patient = None

    if role == "Patient":

        patient_id = user.get(
            "patient_id"
        )

        if patient_id:

            patient, _ = get_patient_record(
                patient_id
            )

    else:

        selected_patient_id = (
            st.session_state.get(
                "selected_patient_id"
            )
        )

        if selected_patient_id:

            patient, _ = get_patient_record(
                selected_patient_id
            )

    with st.sidebar:

        st.markdown("---")

        with st.expander(
            "💬 Ask MedPath — AI Assistant",
            expanded=False,
        ):

            st.caption(
                f"{APP_ICON} MedPath AI Assistant"
            )

            st.caption(
                "Ask questions about your MedPath health information."
            )

            history_key = str(
                st.session_state.get(
                    "user_id"
                )
            )

            history = (
                st.session_state
                .chatbot_history
                .setdefault(
                    history_key,
                    [],
                )
            )

            for who, text in history[-8:]:

                if who == "user":

                    st.markdown(
                        f"**You:** {text}"
                    )

                else:

                    st.markdown(
                        f"**MedPath AI:** {text}"
                    )

            st.caption(
                "Suggested:"
            )

            for index, chip in enumerate(
                CHATBOT_SUGGESTIONS.get(
                    role,
                    [],
                )
            ):

                if st.button(
                    chip,
                    key=f"chat_chip_{index}",
                ):

                    history.append(
                        (
                            "user",
                            chip,
                        )
                    )

                    history.append(
                        (
                            "assistant",
                            chatbot_reply(
                                role,
                                patient,
                                chip,
                            ),
                        )
                    )

                    st.rerun()

            question = st.text_input(
                "Type a question…",
                key="chat_input",
            )

            col_a, col_b = st.columns(2)

            with col_a:

                if st.button(
                    "Send",
                    key="chat_send",
                ) and question:

                    history.append(
                        (
                            "user",
                            question,
                        )
                    )

                    history.append(
                        (
                            "assistant",
                            chatbot_reply(
                                role,
                                patient,
                                question,
                            ),
                        )
                    )

                    st.rerun()

            with col_b:

                if st.button(
                    "Clear conversation",
                    key="chat_clear",
                ):

                    st.session_state.chatbot_history[
                        history_key
                    ] = []

                    st.rerun()


# ==============================================================
# SIDEBAR
# ==============================================================

NAV = {

    "Patient": [
        "Dashboard",
        "Clinical Evidence Graph",
        "My Profile",
        "My Timeline",
        "Documents",
        "Insights",
        "Alerts",
        "Settings",
    ],

    "Doctor": [
        "Dashboard",
        "Clinical Evidence Graph",
        "Patients",
        "Search Patient",
        "Timeline",
        "Documents",
        "Notes",
        "Insights",
        "Alerts",
        "Profile",
    ],

    "CHW": [
        "Dashboard",
        "Clinical Evidence Graph",
        "Patients",
        "Add Patient",
        "Search Patient",
        "Documents",
        "Follow-ups",
        "Notes",
        "Timeline",
        "Alerts",
        "Profile",
    ],
}



def sidebar_nav():

    user = current_user()

    role = str(
        st.session_state.get("role", "")
    ).strip().lower()

    role_display = {
        "patient": "Patient",
        "doctor": "Doctor",
        "chw": "CHW"
    }.get(role, role)

    with st.sidebar:

        render_logo()

        st.caption(
            f"{role_display} workspace"
        )

        st.markdown("---")

        for item in NAV.get(
            role_display,
            []
        ):

            if st.button(
                item,
                key=f"nav_{item}",
                use_container_width=True
            ):

                st.session_state.page = item

                st.rerun()

        st.markdown("---")

        if st.button(
            "Logout",
            use_container_width=True
        ):

            logout()

            st.rerun()

    render_chatbot()

# ==============================================================
# PATIENT ROUTER
# ==============================================================

def route_patient(
    page,
    user,
):

    patient_id = user.get(
        "patient_id"
    )

    if page in (
        "dashboard",
        "Dashboard",
    ):

        patient_dashboard_page(
            user
        )

    elif page == "Clinical Evidence Graph":

        if patient_id:
            patient, _ = get_patient_record(patient_id)
            render_clinical_evidence_graph_page(patient_id, patient)
        else:
            st.error("Patient ID is missing.")

    elif page == "My Profile":


        patient_profile_page(
            user
        )

    elif page == "My Timeline":

        if patient_id:
            timeline_page(
                patient_id
            )
        else:
            st.error(
                "Patient ID is missing."
            )

    elif page == "Documents":

        if patient_id:
            documents_page(
                patient_id
            )
        else:
            st.error(
                "Patient ID is missing."
            )

    elif page == "Insights":

        if patient_id:
            insights_page(
                patient_id
            )
        else:
            st.error(
                "Patient ID is missing."
            )

    elif page == "Alerts":

        if patient_id:
            alerts_page(
                patient_id
            )
        else:
            st.error(
                "Patient ID is missing."
            )

    elif page == "Settings":

        if patient_id:
            symptoms_medications_appointments_page(
                patient_id
            )
        else:
            st.error(
                "Patient ID is missing."
            )

    else:

        patient_dashboard_page(
            user
        )


# ==============================================================
# DOCTOR ROUTER
# ==============================================================

def route_doctor(
    page,
    user,
):

    if page in (
        "dashboard",
        "Dashboard",
        "Patients",
        "Search Patient",
    ):

        doctor_dashboard_page(
            user
        )

    elif page == "Clinical Evidence Graph":

        patient_id = (
            st.session_state.get(
                "selected_patient_id"
            )
        )

        if patient_id:
            patient, _ = get_patient_record(patient_id)
            render_clinical_evidence_graph_page(
                patient_id,
                patient
            )
        else:
            empty_state(
                "Select a patient from the Dashboard first."
            )

    elif page == "Timeline":


        patient_id = (
            st.session_state.get(
                "selected_patient_id"
            )
        )

        if patient_id:

            timeline_page(
                patient_id,
                editable=False,
            )

        else:

            empty_state(
                "Select a patient from the Dashboard first."
            )

    elif page == "Documents":

        patient_id = (
            st.session_state.get(
                "selected_patient_id"
            )
        )

        if patient_id:

            documents_page(
                patient_id,
                editable=True,
            )

        else:

            empty_state(
                "Select a patient from the Dashboard first."
            )

    elif page == "Notes":

        st.markdown(
            '<div class="mp-card"><h4>Notes</h4>',
            unsafe_allow_html=True,
        )

        empty_state(
            "No notes recorded yet."
        )

        st.markdown(
            "</div>",
            unsafe_allow_html=True,
        )

    elif page == "Insights":

        patient_id = (
            st.session_state.get(
                "selected_patient_id"
            )
        )

        if patient_id:

            insights_page(
                patient_id
            )

        else:

            empty_state(
                "Select a patient from the Dashboard first."
            )

    elif page == "Alerts":

        patient_id = (
            st.session_state.get(
                "selected_patient_id"
            )
        )

        if patient_id:

            alerts_page(
                patient_id
            )

        else:

            empty_state(
                "Select a patient from the Dashboard first."
            )

    elif page == "Profile":

        st.markdown(
            '<div class="mp-card"><h4>Doctor Profile</h4>',
            unsafe_allow_html=True,
        )

        st.write(
            f"**Name:** "
            f"{user.get('full_name', '-')}"
        )

        st.write(
            f"**Email:** "
            f"{user.get('email', '-')}"
        )

        st.write(
            f"**Specialization:** "
            f"{user.get('specialization', '-')}"
        )

        st.write(
            f"**License Number:** "
            f"{user.get('license_number', '-')}"
        )

        st.write(
            f"**Hospital / Clinic:** "
            f"{user.get('hospital', '-')}"
        )

        st.markdown(
            "</div>",
            unsafe_allow_html=True,
        )

    else:

        doctor_dashboard_page(
            user
        )


# ==============================================================
# CHW ROUTER
# ==============================================================

def route_chw(
    page,
    user,
):

    if page in (
        "dashboard",
        "Dashboard",
        "Patients",
        "Search Patient",
    ):

        chw_dashboard_page(
            user
        )

    elif page == "Clinical Evidence Graph":

        patients = load_db(PATIENTS_FILE)
        options = {}

        for p_id, p_rec in patients.items():
            options[f"{p_rec.get('name', 'Unknown')} ({p_id})"] = p_id

        if options:
            choice = st.selectbox(
                "Select Patient for Evidence Graph",
                list(options.keys())
            )
            sel_pid = options[choice]
            render_clinical_evidence_graph_page(
                sel_pid,
                patients[sel_pid]
            )
        else:
            empty_state("No patients assigned yet.")


    elif page == "Add Patient":

        doctor_add_patient_form()

    elif page == "Documents":

        patients = load_db(
            PATIENTS_FILE
        )

        options = {}

        for patient_id, patient in patients.items():

            options[
                f"{patient.get('name', 'Unknown')} "
                f"({patient_id})"
            ] = patient_id

        if options:

            choice = st.selectbox(
                "Select Patient",
                list(options.keys()),
            )

            documents_page(
                options[choice]
            )

        else:

            empty_state(
                "No patients assigned yet."
            )

    elif page == "Follow-ups":

        chw_dashboard_page(
            user
        )

    elif page == "Notes":

        st.markdown(
            '<div class="mp-card"><h4>Notes</h4>',
            unsafe_allow_html=True,
        )

        empty_state(
            "No notes recorded yet."
        )

        st.markdown(
            "</div>",
            unsafe_allow_html=True,
        )

    elif page == "Timeline":

        patients = load_db(
            PATIENTS_FILE
        )

        options = {}

        for patient_id, patient in patients.items():

            options[
                f"{patient.get('name', 'Unknown')} "
                f"({patient_id})"
            ] = patient_id

        if options:

            choice = st.selectbox(
                "Select Patient",
                list(options.keys()),
            )

            timeline_page(
                options[choice]
            )

        else:

            empty_state(
                "No patients assigned yet."
            )

    elif page == "Alerts":

        st.markdown(
            '<div class="mp-card"><h4>Alerts</h4>',
            unsafe_allow_html=True,
        )

        empty_state(
            "No alerts"
        )

        st.markdown(
            "</div>",
            unsafe_allow_html=True,
        )

    elif page == "Profile":

        st.markdown(
            '<div class="mp-card"><h4>CHW Profile</h4>',
            unsafe_allow_html=True,
        )

        st.write(
            f"**Name:** "
            f"{user.get('full_name', '-')}"
        )

        st.write(
            f"**Email:** "
            f"{user.get('email', '-')}"
        )

        st.write(
            f"**Employee ID:** "
            f"{user.get('employee_id', '-')}"
        )

        st.write(
            f"**Organization:** "
            f"{user.get('organization', '-')}"
        )

        st.write(
            f"**Region:** "
            f"{user.get('region', '-')}"
        )

        st.markdown(
            "</div>",
            unsafe_allow_html=True,
        )

    else:

        chw_dashboard_page(
            user
        )


# ==============================================================
# MAIN
# ==============================================================

def main():

    init_session_state()

    inject_css()

    # ----------------------------------------------------------
    # NOT LOGGED IN
    # ----------------------------------------------------------

    if not st.session_state.auth:

        login_page()

        return

    # ----------------------------------------------------------
    # LOAD USER FROM FASTAPI
    # ----------------------------------------------------------

    user = current_user()

    if user is None:

        st.session_state.auth = False
        st.session_state.user_id = None
        st.session_state.user = None
        st.session_state.role = None
        st.session_state.page = "login"

        st.warning(
            "Your session has expired. Please sign in again."
        )

        st.rerun()

        return

    # ----------------------------------------------------------
    # KEEP PATIENT RECORD AVAILABLE
    # ----------------------------------------------------------

    if str(user.get("role", "")).strip().lower() == "patient":

        ensure_patient_record(user)

    # ----------------------------------------------------------
    # GET ROLE SAFELY
    # ----------------------------------------------------------

    role = str(user.get("role", "")).strip().lower()

    # Keep session state consistent
    st.session_state.role = role

    # ----------------------------------------------------------
    # SIDEBAR
    # ----------------------------------------------------------

    sidebar_nav()

    page = st.session_state.get(
        "page",
        "dashboard",
    )

    # ----------------------------------------------------------
    # ROUTING
    # ----------------------------------------------------------

    if role == "patient":

        route_patient(
            page,
            user,
        )

    elif role == "doctor":

        route_doctor(
            page,
            user,
        )

    elif role == "chw":

        route_chw(
            page,
            user,
        )

    else:

        st.error(
            f"Unknown user role: {user.get('role')}"
        )


# ==============================================================
# START APPLICATION
# ==============================================================

if __name__ == "__main__":
    main()