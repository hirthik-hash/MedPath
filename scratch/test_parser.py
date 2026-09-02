import re
import json
import sys

def normalize_confidence(conf_val, likelihood=None, is_not_tested=False, is_direct_lab=False, is_negative=False):
    """
    Computes a realistic, varied confidence rating:
    - NOT_YET_TESTED nodes (missing / recommended) -> 'Medium' or 'Low'
    - Confirmed direct lab measurements with explicit numbers -> 'High' (or pattern confidence if calibrated)
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


def clean_atomic_name(text: str, max_words: int = 5) -> str:
    """
    Cleans up a raw clinical name to ensure it is atomic, title-cased, and strictly <= max_words (4-6 words max).
    """
    # Remove parenthetical details e.g. (MCV, RDW)
    cleaned = re.sub(r'[\(\[\{].*?[\)\]\}]', '', text).strip()
    # Remove trailing punctuation or qualifiers
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
    """
    Extracts atomic, discrete clinical findings from raw prose or n8n evidence strings.
    """
    findings = []
    text = str(raw_ev).strip()
    if not text:
        return findings

    pat_conf = pattern.get("confidence") if isinstance(pattern, dict) else None
    pat_like = pattern.get("likelihood") if isinstance(pattern, dict) else None

    # Step 0: Handle semicolon-separated compound clauses
    # (e.g. 'Only hemoglobin value is present; RBC indices (MCV, RDW), reticulocyte count, and iron studies are not documented')
    clauses = [c.strip() for c in re.split(r';', text) if c.strip()]
    if len(clauses) > 1:
        for cl in clauses:
            findings.extend(parse_atomic_findings(cl, pattern, source_doc_id, source_doc_name))
        return findings

    # Step 1: Document Reference Check
    # (e.g. 'Laboratory report dated 29 Aug 2026 (CBC_Report.pdf)')
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

    # Step 2: Clinical Impression / Diagnosis in evidence string
    # (e.g., 'Impression: Acute Dengue Fever with Thrombocytopenia')
    if text.lower().startswith("impression:") or "clinical impression:" in text.lower():
        clean = re.sub(r'(?i)^(impression|clinical impression):\s*', '', text)
        clean = re.sub(r'\s*\([^)]*\)', '', clean)
        sub_parts = [p.strip() for p in re.split(r'\bwith\b|\band\b|,', clean) if p.strip()]
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
    # (e.g., 'RBC indices (MCV, RDW), reticulocyte count, and iron studies are not documented')
    # (e.g., 'No documented history of bleeding, chronic disease, medications, or prior hemoglobin trend')
    # (e.g., 'No documented assessment, symptoms, or management plan in the record')
    if any(k in text.lower() for k in [
        "not documented", "not tested", "not recorded", "not evaluated",
        "no documented", "insufficient data", "not available"
    ]):
        # Strip parentheticals first so '(MCV, RDW)' doesn't break comma splitting
        clean_text = re.sub(r'\s*\([^)]*\)', '', text)
        clean = re.sub(r'(?i)^(no documented|there is no documented|not documented|no|history of)\s+', '', clean_text)
        clean = re.sub(r'(?i)\s+(are|is|were)\s+not\s+(documented|tested|recorded|evaluated).*$', '', clean)
        clean = re.sub(r'(?i)\s+(in the record|in record|in medical record).*$', '', clean)
        clean = re.sub(r'(?i)^only\s+', '', clean)
        
        # Split into individual missing items by comma, 'and', 'or'
        parts = [p.strip() for p in re.split(r',|\band\b|\bor\b', clean) if p.strip()]
        for p in parts:
            if not p or len(p) < 2 or p.lower() in ["the", "a", "and", "or", "in", "value is present", "present"]:
                continue
            # Handle clause like "hemoglobin value is present"
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

    # Step 4: Qualitative Lab/Test (Check BEFORE numeric regex to avoid NS1 being captured as 1)
    # (e.g., 'Dengue NS1 Antigen: POSITIVE', 'Malaria Blood Smear TESTED_NEGATIVE - No parasites seen.')
    qual_match = re.search(
        r'([A-Za-z0-9\s\-/]+?)\s*[:=]\s*(POSITIVE|NEGATIVE|REACTIVE|NON-REACTIVE|DETECTED|NOT DETECTED|NORMAL|LOW|HIGH)',
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

    # Step 5: Explicit Negative Symptoms / Findings
    # (e.g., 'No chest pain reported and no shortness of breath reported')
    # (e.g., 'Malaria Blood Smear TESTED_NEGATIVE - No parasites seen.')
    if any(k in text.lower() for k in [
        "no chest pain", "no shortness of breath", "no parasites", "tested_negative",
        "negative for", "absent", "denies", "denied"
    ]):
        neg_parts = [p.strip() for p in re.split(r'\band\b|,', text) if p.strip()]
        for np in neg_parts:
            np_lower = np.lower()
            if "malaria" in np_lower:
                findings.append({
                    "name": "Malaria Blood Smear",
                    "value": "Negative (No Parasites)",
                    "unit": None,
                    "evidence_type": "LAB_RESULT",
                    "evidence_state": "TESTED_NEGATIVE",
                    "confidence": normalize_confidence(pat_conf, pat_like, is_direct_lab=True)
                })
            elif "chest pain" in np_lower:
                findings.append({
                    "name": "Chest Pain",
                    "value": "Absent / Denied",
                    "unit": None,
                    "evidence_type": "SYMPTOM",
                    "evidence_state": "TESTED_NEGATIVE",
                    "confidence": normalize_confidence(pat_conf, pat_like, is_negative=True)
                })
            elif "shortness of breath" in np_lower or "breath" in np_lower:
                findings.append({
                    "name": "Shortness Of Breath",
                    "value": "Absent / Denied",
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
                        "value": "Absent / Negative",
                        "unit": None,
                        "evidence_type": etype,
                        "evidence_state": "TESTED_NEGATIVE",
                        "confidence": normalize_confidence(pat_conf, pat_like, is_negative=True)
                    })
        if findings:
            return findings

    # Step 6: Quantitative Lab Results / Vitals with numeric value and unit
    # (e.g., 'Hemoglobin 9.2 g/dL', 'Platelet Count: 80,000 /uL (LOW)', 'WBC Count: 3,200 /μL')
    # (e.g., 'Blood Pressure: 110/70 mmHg and SpO2: 98%')
    # Note: exclude pure duration numbers like 'fever for 4 days'
    non_duration_text = re.sub(r'(?i)\s+for\s+\d+\s+days?', '', text)
    multi_items = [item.strip() for item in re.split(r'\band\b|,', non_duration_text) if re.search(r'\d', item)]
    if not multi_items:
        multi_items = [text]

    matched_quantitative = False
    for item in multi_items:
        lab_match = re.search(
            r'([A-Za-z0-9\s\-/]+?)(?::|\s+is|\s+was|\s+measured)?\s*[:=]?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?(?:/[0-9]{1,3}(?:,[0-9]{3})*)?)\s*([a-zA-Z/%μuµLgdmgkgh\^]+(?:\s*\(.*?\))?)',
            item
        )
        if lab_match:
            raw_name = lab_match.group(1).strip()
            val = lab_match.group(2).strip()
            unit = lab_match.group(3).strip() if lab_match.group(3) else None
            if unit:
                unit = re.sub(r'\s*\([^)]*\)', '', unit).strip()

            # Filter out non-medical units
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

    # Step 7: Symptom descriptions & clusters
    # (e.g., 'High fever 39.1 C for 4 days with retro-orbital headache and joint muscle pain')
    # (e.g., 'Fever for approximately 4 days with headache, generalized body aches and fatigue')
    if any(s in text.lower() for s in ["fever", "headache", "pain", "fatigue", "aches", "cough", "nausea", "vomiting", "rash"]):
        s_clean = re.sub(r'(?i)^chief complaint:\s*', '', text)
        s_parts = re.split(r'(?i)\s+with\s+|,|\band\b', s_clean)
        for sp in s_parts:
            sp = sp.strip()
            if not sp or sp.lower() in ["the", "a", "in", "for", "approximately"]:
                continue
            # Check fever with temperature
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

    # Step 8: General fallback
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


if __name__ == "__main__":
    test_strings = [
        ("Only hemoglobin value is present; RBC indices (MCV, RDW), reticulocyte count, and iron studies are not documented", {"name": "Unclassified Anemia", "confidence": 90, "likelihood": "High"}),
        ("Hemoglobin 9.2 g/dL", {"name": "Anemia (low hemoglobin)", "confidence": 95, "likelihood": "High"}),
        ("Laboratory report dated 29 Aug 2026 (CBC_Report.pdf)", {"name": "Anemia", "confidence": 95, "likelihood": "High"}),
        ("Hemoglobin 9.2 g/dL (below usual normal ranges)", {"name": "Clinically significant anemia", "confidence": 85, "likelihood": "High"}),
        ("No documented assessment, symptoms, or management plan in the record", {"name": "Anemia", "confidence": 85, "likelihood": "High"}),
        ("No documented history of bleeding, chronic disease, medications, or prior hemoglobin trend", {"name": "Anemia", "confidence": 90, "likelihood": "High"}),
        ("Dengue NS1 Antigen: POSITIVE", {"name": "Acute dengue infection", "confidence": 95, "likelihood": "High"}),
        ("High fever 39.1 C for 4 days with retro-orbital headache and joint muscle pain", {"name": "Acute dengue infection", "confidence": 95, "likelihood": "High"}),
        ("Platelet Count: 80,000 /uL (LOW)", {"name": "Dengue-associated thrombocytopenia", "confidence": 90, "likelihood": "High"}),
        ("Impression: Acute Dengue Fever with Thrombocytopenia", {"name": "Dengue-associated thrombocytopenia", "confidence": 90, "likelihood": "High"}),
        ("Thrombocytopenia with platelet count 80,000 /uL", {"name": "Severe Dengue Risk", "confidence": 60, "likelihood": "Medium"}),
        ("Ongoing high fever for 4 days in the setting of confirmed acute dengue", {"name": "Severe Dengue Risk", "confidence": 60, "likelihood": "Medium"}),
        ("Fever for approximately 4 days with headache, generalized body aches and fatigue", {"name": "Acute dengue infection", "confidence": 95, "likelihood": "High"}),
        ("WBC Count: 3,200 /μL", {"name": "Cytopenias", "confidence": 90, "likelihood": "High"}),
        ("Platelet Count: 95,000 /μL", {"name": "Cytopenias", "confidence": 90, "likelihood": "High"}),
        ("Blood Pressure: 110/70 mmHg and SpO2: 98%", {"name": "Hemodynamic Stability", "confidence": 80, "likelihood": "High"}),
        ("No chest pain reported and no shortness of breath reported", {"name": "Hemodynamic Stability", "confidence": 80, "likelihood": "High"}),
        ("Malaria Blood Smear TESTED_NEGATIVE - No parasites seen.", {"name": "Malaria Ruleout", "confidence": 90, "likelihood": "High"})
    ]

    for s, pat in test_strings:
        safe_s = s.encode('ascii', 'replace').decode('ascii')
        print(f"\nRAW: {safe_s}")
        res = parse_atomic_findings(s, pat)
        for r in res:
            safe_name = r['name'].encode('ascii', 'replace').decode('ascii')
            safe_unit = (r['unit'] or '').encode('ascii', 'replace').decode('ascii')
            word_count = len(r['name'].split())
            print(f"  -> Name: {safe_name!r} ({word_count} w) | Val: {r['value']!r} | Unit: {safe_unit!r} | Type: {r['evidence_type']} | State: {r['evidence_state']} | Conf: {r['confidence']}")
