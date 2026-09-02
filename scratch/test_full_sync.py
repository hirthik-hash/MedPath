import json
import re
import sys

def normalize_confidence(conf_val, likelihood=None, is_not_tested=False, is_direct_lab=False, is_negative=False):
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


def clean_atomic_name(text: str, max_words: int = 4) -> str:
    cleaned = re.split(r'[—–:\(\[\{;]', text)[0].strip()
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


def parse_atomic_findings(raw_ev, pattern=None, source_doc_id=None, source_doc_name=None):
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


def build_evidence_payload(patient_id, record, n8n_result=None, source_doc_id=None, source_doc_name=None):
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

    # 5. Structured n8n AI Analysis
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

    return {"nodes": nodes, "edges": edges}


if __name__ == "__main__":
    data = json.load(open("data/patients.json", encoding="utf-8"))
    payload = build_evidence_payload("MP00005", data["MP00005"])

    max_wc = 0
    for i, n in enumerate(payload["nodes"]):
        wc = len(n["name"].split())
        if wc > max_wc: max_wc = wc
        safe_name = n["name"].encode("ascii", "replace").decode("ascii")
        safe_unit = (n["unit"] or "").encode("ascii", "replace").decode("ascii")
        etype = n["evidence_type"]
        estate = n["evidence_state"]
        conf = n["confidence"]
        val = str(n["value"])[:25]
        print(f"{i+1:2d}. [{etype:14s}] {safe_name:30s} ({wc} w) | Val: {val:25s} | Unit: {safe_unit:6s} | State: {estate:15s} | Conf: {conf}")

    print(f"\nMax Word Count across all nodes: {max_wc}")
    conf_counts = {}
    for n in payload["nodes"]:
        c = n["confidence"]
        conf_counts[c] = conf_counts.get(c, 0) + 1
    print("Confidence distribution:", conf_counts)
