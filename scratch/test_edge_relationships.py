def determine_relationship_type(finding, raw_pattern_name, atomic_pattern_name):
    state = finding.get('evidence_state', 'PRESENT')
    if state == 'NOT_YET_TESTED':
        return 'INDICATES'

    p_lower = (raw_pattern_name + ' ' + atomic_pattern_name).lower()
    f_name_lower = finding.get('name', '').lower()
    f_val_lower = str(finding.get('value', '')).lower()

    is_benign_or_ruleout_pattern = any(w in p_lower for w in [
        'stable', 'hemodynamically stable', 'normal', 'unremarkable',
        'without', 'absence', 'cleared', 'ruled out', 'less likely', 'unlikely',
        'negative', 'non-severe', 'low risk', 'intact', 'no compromise'
    ])

    is_negative_finding = (state == 'TESTED_NEGATIVE') or any(w in f_val_lower for w in ['absent', 'denied', 'negative', 'clear', 'normal'])

    is_abnormal_finding = any(w in f_name_lower for w in [
        'fever', 'pain', 'headache', 'bleeding', 'dyspnea', 'tachycardia', 'hypotension', 'ns1', 'thrombocytopenia'
    ]) and not is_negative_finding

    if is_benign_or_ruleout_pattern:
        if is_negative_finding or 'normal' in f_val_lower:
            return 'SUPPORTS'
        elif is_abnormal_finding:
            return 'CONTRADICTS'
        else:
            return 'SUPPORTS'
    else:
        if is_negative_finding:
            p_terms = [t for t in p_lower.split() if len(t) > 3]
            f_terms = [t for t in f_name_lower.split() if len(t) > 3]
            direct_refutation = any(t in f_terms for t in p_terms) and any(w in f_name_lower for w in ['smear', 'antigen', 'test', 'culture', 'pcr'])
            if direct_refutation:
                return 'CONTRADICTS'
            return 'SUPPORTS'
        else:
            return 'SUPPORTS'

test_cases = [
    ({'name': 'Chest Pain', 'value': 'Absent', 'evidence_state': 'TESTED_NEGATIVE'}, 'Currently hemodynamically stable without documented respiratory compromise', 'Currently Hemodynamically Stable'),
    ({'name': 'Shortness Of Breath', 'value': 'Absent', 'evidence_state': 'TESTED_NEGATIVE'}, 'Currently hemodynamically stable without documented respiratory compromise', 'Currently Hemodynamically Stable'),
    ({'name': 'Blood Pressure', 'value': '110/70', 'evidence_state': 'PRESENT'}, 'Currently hemodynamically stable without documented respiratory compromise', 'Currently Hemodynamically Stable'),
    ({'name': 'Malaria Blood Smear', 'value': 'Negative', 'evidence_state': 'TESTED_NEGATIVE'}, 'Malaria less likely', 'Malaria Less Likely'),
    ({'name': 'Malaria Blood Smear', 'value': 'Negative', 'evidence_state': 'TESTED_NEGATIVE'}, 'Active Malaria Infection', 'Active Malaria Infection'),
    ({'name': 'Dengue Ns1 Antigen', 'value': 'Positive', 'evidence_state': 'PRESENT'}, 'Acute dengue infection', 'Acute Dengue Infection'),
    ({'name': 'Rbc Indices', 'value': 'Not Documented', 'evidence_state': 'NOT_YET_TESTED'}, 'Unclassified Anemia', 'Unclassified Anemia')
]

for f, rp, ap in test_cases:
    rel = determine_relationship_type(f, rp, ap)
    print(f"{f['name']} ({f['evidence_state']}, {f['value']}) -> {ap} : {rel}")
