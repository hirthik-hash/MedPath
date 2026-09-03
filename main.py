from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

try:
    from .database import Base, engine, get_db
    from .models import User, Patient, EvidenceNode, EvidenceEdge
    from .schemas import (
        UserCreate, UserResponse, UserLogin,
        EvidenceNodeCreate, EvidenceEdgeCreate,
        PatientChatRequest, PatientChatResponse
    )
except ImportError:
    from database import Base, engine, get_db
    from models import User, Patient, EvidenceNode, EvidenceEdge
    from schemas import (
        UserCreate, UserResponse, UserLogin,
        EvidenceNodeCreate, EvidenceEdgeCreate,
        PatientChatRequest, PatientChatResponse
    )



# ============================================================
# DATABASE INITIALIZATION
# ============================================================

Base.metadata.create_all(bind=engine)


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title="MedPath API"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():
    return {
        "message": "MedPath backend is running"
    }


# ============================================================
# CONVERT USER DATABASE OBJECT TO RESPONSE
# ============================================================

def user_to_response(user, patient_id=None):

    return {
        "user_id": str(user.id),

        "role": user.role,

        "full_name": user.full_name,

        "email": user.email,

        "mobile": user.mobile,

        "date_of_birth": user.date_of_birth,

        "gender": user.gender,

        "patient_id": patient_id,

        "specialization": user.specialization,

        "license_number": user.license_number,

        "hospital": user.hospital,

        "employee_id": user.employee_id,

        "organization": user.organization,

        "region": user.region
    }


# ============================================================
# REGISTER USER
# ============================================================

@app.post(
    "/register",
    response_model=UserResponse
)
def register_user(
    user: UserCreate,
    db: Session = Depends(get_db)
):

    # --------------------------------------------------------
    # Clean input
    # --------------------------------------------------------

    email = user.email.strip().lower()
    role = user.role.strip().lower()

    # --------------------------------------------------------
    # Validate role
    # --------------------------------------------------------

    if role not in ["patient", "doctor", "chw"]:

        raise HTTPException(
            status_code=400,
            detail="Invalid role. Choose Patient, Doctor, or CHW."
        )

    # --------------------------------------------------------
    # Check whether email already exists
    # --------------------------------------------------------

    existing_user = (
        db.query(User)
        .filter(User.email == email)
        .first()
    )

    if existing_user:

        raise HTTPException(
            status_code=400,
            detail="Email already registered"
        )

    # --------------------------------------------------------
    # Create User
    # --------------------------------------------------------

    new_user = User(

        role=role,

        full_name=user.full_name.strip(),

        email=email,

        mobile=user.mobile.strip(),

        date_of_birth=user.date_of_birth,

        gender=user.gender,

        password=user.password,

        # Doctor fields
        specialization=user.specialization,

        license_number=user.license_number,

        hospital=user.hospital,

        # CHW fields
        employee_id=user.employee_id,

        organization=user.organization,

        region=user.region
    )

    # --------------------------------------------------------
    # Save User
    # --------------------------------------------------------

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # --------------------------------------------------------
    # Patient ID
    # --------------------------------------------------------

    patient_id = None

    if role == "patient":

        patient_id = f"MP{new_user.id:05d}"

        new_patient = Patient(

            patient_id=patient_id,

            linked_user_id=new_user.id,

            name=user.full_name.strip(),

            date_of_birth=user.date_of_birth,

            gender=user.gender,

            phone=user.mobile.strip()
        )

        db.add(new_patient)
        db.commit()
        db.refresh(new_patient)

    # --------------------------------------------------------
    # Return registered user
    # --------------------------------------------------------

    return user_to_response(
        new_user,
        patient_id
    )


# ============================================================
# LOGIN USER
# ============================================================

@app.post(
    "/login",
    response_model=UserResponse
)
def login_user(
    user: UserLogin,
    db: Session = Depends(get_db)
):

    # --------------------------------------------------------
    # Clean input
    # --------------------------------------------------------

    email = user.email.strip().lower()
    role = user.role.strip().lower()

    # --------------------------------------------------------
    # Find user by email
    # --------------------------------------------------------

    existing_user = (
        db.query(User)
        .filter(User.email == email)
        .first()
    )

    # --------------------------------------------------------
    # User not found
    # --------------------------------------------------------

    if not existing_user:

        raise HTTPException(
            status_code=401,
            detail="Invalid email or password"
        )

    # --------------------------------------------------------
    # Check password
    # --------------------------------------------------------

    if existing_user.password != user.password:

        raise HTTPException(
            status_code=401,
            detail="Invalid email or password"
        )

    # --------------------------------------------------------
    # Check role
    # --------------------------------------------------------

    if existing_user.role.strip().lower() != role:

        raise HTTPException(
            status_code=401,
            detail="Incorrect role selected"
        )

    # --------------------------------------------------------
    # Get Patient ID
    # --------------------------------------------------------

    patient_id = None

    if existing_user.role.strip().lower() == "patient":

        patient = (
            db.query(Patient)
            .filter(
                Patient.linked_user_id == existing_user.id
            )
            .first()
        )

        if patient:

            patient_id = patient.patient_id

    # --------------------------------------------------------
    # Return logged-in user
    # --------------------------------------------------------

    return user_to_response(
        existing_user,
        patient_id
    )


# ============================================================
# CURRENT USER
# ============================================================

@app.get(
    "/me",
    response_model=UserResponse
)
def get_current_user(
    user_id: int,
    db: Session = Depends(get_db)
):

    # --------------------------------------------------------
    # Find user
    # --------------------------------------------------------

    user = (
        db.query(User)
        .filter(User.id == user_id)
        .first()
    )

    if not user:

        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    # --------------------------------------------------------
    # Get Patient ID
    # --------------------------------------------------------

    patient_id = None

    if user.role.strip().lower() == "patient":

        patient = (
            db.query(Patient)
            .filter(
                Patient.linked_user_id == user.id
            )
            .first()
        )

        if patient:

            patient_id = patient.patient_id

    # --------------------------------------------------------
    # Return current user
    # --------------------------------------------------------

    return user_to_response(
        user,
        patient_id
    )


# ============================================================
# DEBUG USERS
# ============================================================

@app.get("/debug/users")
def get_debug_users(db: Session = Depends(get_db)):
    users = db.query(User).all()
    result = []
    for u in users:
        patient_id = None
        if u.role.strip().lower() == "patient":
            patient = db.query(Patient).filter(Patient.linked_user_id == u.id).first()
            if patient:
                patient_id = patient.patient_id
        result.append(user_to_response(u, patient_id))
    return result


# ============================================================
# GET PATIENT BY PATIENT_ID
# ============================================================

@app.get("/patients/{patient_id}")
def get_patient_by_id(patient_id: str, db: Session = Depends(get_db)):
    patient = db.query(Patient).filter(Patient.patient_id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    user = db.query(User).filter(User.id == patient.linked_user_id).first()
    return {
        "patient_id": patient.patient_id,
        "name": patient.name,
        "date_of_birth": patient.date_of_birth,
        "gender": patient.gender,
        "phone": patient.phone,
        "user": user_to_response(user, patient.patient_id) if user else None
    }


# ============================================================
# GET PATIENT TIMELINE
# ============================================================

@app.get("/patients/{patient_id}/timeline")
def get_patient_timeline(patient_id: str, db: Session = Depends(get_db)):
    patient = db.query(Patient).filter(Patient.patient_id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return {
        "patient_id": patient_id,
        "timeline": []
    }


# ============================================================
# GET PATIENT MEDICAL DATA
# ============================================================

@app.get("/patients/{patient_id}/medical-data")
def get_patient_medical_data(patient_id: str, db: Session = Depends(get_db)):
    patient = db.query(Patient).filter(Patient.patient_id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return {
        "patient_id": patient_id,
        "symptoms": [],
        "labs": [],
        "medications": [],
        "documents": []
    }


# ============================================================
# CLINICAL EVIDENCE GRAPH ENDPOINTS
# ============================================================

from datetime import datetime
from typing import Dict, Any

@app.get("/patients/{patient_id}/evidence")
def get_patient_evidence(patient_id: str, db: Session = Depends(get_db)):
    nodes = db.query(EvidenceNode).filter(EvidenceNode.patient_id == patient_id).all()
    return nodes


@app.get("/patients/{patient_id}/evidence/graph")
def get_patient_evidence_graph(patient_id: str, db: Session = Depends(get_db)):
    nodes = db.query(EvidenceNode).filter(EvidenceNode.patient_id == patient_id).all()
    edges = db.query(EvidenceEdge).filter(EvidenceEdge.patient_id == patient_id).all()
    return {
        "nodes": [
            {
                "id": n.id,
                "patient_id": n.patient_id,
                "evidence_type": n.evidence_type,
                "name": n.name,
                "value": n.value,
                "unit": n.unit,
                "date": n.date,
                "source_document_id": n.source_document_id,
                "source_document_name": n.source_document_name,
                "source_type": n.source_type,
                "confidence": n.confidence,
                "verification_status": n.verification_status,
                "evidence_state": n.evidence_state,
                "created_at": n.created_at
            }
            for n in nodes
        ],
        "edges": [
            {
                "id": e.id,
                "patient_id": e.patient_id,
                "source_node_id": e.source_node_id,
                "target_node_id": e.target_node_id,
                "relationship_type": e.relationship_type,
                "confidence": e.confidence,
                "created_at": e.created_at
            }
            for e in edges
        ]
    }


@app.get("/patients/{patient_id}/evidence/{evidence_id}")
def get_evidence_details(patient_id: str, evidence_id: int, db: Session = Depends(get_db)):
    node = db.query(EvidenceNode).filter(
        EvidenceNode.patient_id == patient_id,
        EvidenceNode.id == evidence_id
    ).first()
    
    if not node:
        raise HTTPException(status_code=404, detail="Evidence node not found")
        
    outgoing = db.query(EvidenceEdge).filter(
        EvidenceEdge.patient_id == patient_id,
        EvidenceEdge.source_node_id == evidence_id
    ).all()
    
    incoming = db.query(EvidenceEdge).filter(
        EvidenceEdge.patient_id == patient_id,
        EvidenceEdge.target_node_id == evidence_id
    ).all()
    
    related_node_ids = set([e.target_node_id for e in outgoing] + [e.source_node_id for e in incoming])
    related_nodes = db.query(EvidenceNode).filter(
        EvidenceNode.patient_id == patient_id,
        EvidenceNode.id.in_(related_node_ids)
    ).all() if related_node_ids else []
    
    return {
        "node": {
            "id": node.id,
            "patient_id": node.patient_id,
            "evidence_type": node.evidence_type,
            "name": node.name,
            "value": node.value,
            "unit": node.unit,
            "date": node.date,
            "source_document_id": node.source_document_id,
            "source_document_name": node.source_document_name,
            "source_type": node.source_type,
            "confidence": node.confidence,
            "verification_status": node.verification_status,
            "evidence_state": node.evidence_state,
            "created_at": node.created_at
        },
        "outgoing_edges": [
            {
                "id": e.id,
                "patient_id": e.patient_id,
                "source_node_id": e.source_node_id,
                "target_node_id": e.target_node_id,
                "relationship_type": e.relationship_type,
                "confidence": e.confidence,
                "created_at": e.created_at
            } for e in outgoing
        ],
        "incoming_edges": [
            {
                "id": e.id,
                "patient_id": e.patient_id,
                "source_node_id": e.source_node_id,
                "target_node_id": e.target_node_id,
                "relationship_type": e.relationship_type,
                "confidence": e.confidence,
                "created_at": e.created_at
            } for e in incoming
        ],
        "related_nodes": [
            {
                "id": r.id,
                "name": r.name,
                "evidence_type": r.evidence_type,
                "value": r.value,
                "unit": r.unit
            } for r in related_nodes
        ]
    }


@app.post("/patients/{patient_id}/evidence/ingest")
def ingest_patient_evidence(
    patient_id: str,
    payload: Dict[str, Any],
    db: Session = Depends(get_db)
):
    nodes_data = payload.get("nodes", [])
    edges_data = payload.get("edges", [])
    
    node_id_map = {}
    created_nodes = []
    
    for n in nodes_data:
        etype = str(n.get("evidence_type", "CLINICAL_NOTE")).upper()
        name = str(n.get("name", "Unnamed Evidence")).strip()
        val = str(n.get("value")) if n.get("value") is not None else None
        unit = str(n.get("unit")) if n.get("unit") is not None else None
        date = str(n.get("date")) if n.get("date") is not None else None
        src_doc_id = str(n.get("source_document_id")) if n.get("source_document_id") else None
        src_doc_name = str(n.get("source_document_name")) if n.get("source_document_name") else None
        src_type = str(n.get("source_type", "N8N_AI_ANALYSIS"))
        conf = str(n.get("confidence", "High"))
        verif = str(n.get("verification_status", "Confirmed"))
        estate = str(n.get("evidence_state", "PRESENT")).upper().strip()
        if estate not in {"PRESENT", "TESTED_NEGATIVE", "NOT_YET_TESTED"}:
            estate = "PRESENT"

        temp_key = str(n.get("id") or name)
        
        existing = db.query(EvidenceNode).filter(
            EvidenceNode.patient_id == patient_id,
            EvidenceNode.evidence_type == etype,
            EvidenceNode.name == name,
            EvidenceNode.value == val
        ).first()
        
        if existing:
            updated = False
            if existing.evidence_state != estate:
                existing.evidence_state = estate
                updated = True
            if existing.confidence != conf:
                existing.confidence = conf
                updated = True
            if unit is not None and existing.unit != unit:
                existing.unit = unit
                updated = True
            if updated:
                db.commit()
                db.refresh(existing)
            node_id_map[temp_key] = existing.id
            created_nodes.append(existing)
        else:
            new_node = EvidenceNode(
                patient_id=patient_id,
                evidence_type=etype,
                name=name,
                value=val,
                unit=unit,
                date=date,
                source_document_id=src_doc_id,
                source_document_name=src_doc_name,
                source_type=src_type,
                confidence=conf,
                verification_status=verif,
                evidence_state=estate,
                created_at=datetime.now().isoformat()
            )
            db.add(new_node)
            db.commit()
            db.refresh(new_node)
            node_id_map[temp_key] = new_node.id
            created_nodes.append(new_node)
            
    created_edges = []
    valid_rel_types = {
        "SUPPORTS", "CONTRADICTS", "TEMPORAL", "ASSOCIATED_WITH",
        "DERIVED_FROM", "CORROBORATES", "INDICATES", "CAUSES",
        "TREATED_BY", "CONFIRMS"
    }

    for e in edges_data:
        src_ref = str(e.get("source_node_id"))
        tgt_ref = str(e.get("target_node_id"))
        rel_type = str(e.get("relationship_type", "ASSOCIATED_WITH")).upper().strip()
        if rel_type not in valid_rel_types:
            rel_type = "ASSOCIATED_WITH"

        conf = str(e.get("confidence", "High"))
        
        src_id = node_id_map.get(src_ref) or (int(src_ref) if src_ref.isdigit() else None)
        tgt_id = node_id_map.get(tgt_ref) or (int(tgt_ref) if tgt_ref.isdigit() else None)
        
        if src_id and tgt_id:
            existing_edge = db.query(EvidenceEdge).filter(
                EvidenceEdge.patient_id == patient_id,
                EvidenceEdge.source_node_id == src_id,
                EvidenceEdge.target_node_id == tgt_id
            ).first()
            
            if not existing_edge:
                new_edge = EvidenceEdge(
                    patient_id=patient_id,
                    source_node_id=src_id,
                    target_node_id=tgt_id,
                    relationship_type=rel_type,
                    confidence=conf,
                    created_at=datetime.now().isoformat()
                )
                db.add(new_edge)
                db.commit()
                db.refresh(new_edge)
                created_edges.append(new_edge)
            else:
                updated_edge = False
                if existing_edge.relationship_type != rel_type:
                    existing_edge.relationship_type = rel_type
                    updated_edge = True
                if existing_edge.confidence != conf:
                    existing_edge.confidence = conf
                    updated_edge = True
                if updated_edge:
                    db.commit()
                    db.refresh(existing_edge)
                created_edges.append(existing_edge)

                
    return {
        "message": f"Successfully processed {len(created_nodes)} evidence nodes and {len(created_edges)} edges",
        "patient_id": patient_id
    }


# ============================================================
# PATIENT-AWARE AI CHAT ASSISTANT
# ============================================================

import os
import json
import requests as http_requests

def assemble_patient_context(patient_id: str, db: Session) -> str:
    """
    Assembles a clean, structured text representation of the patient's clinical record:
    - Evidence nodes and edges from SQLite
    - Saved doctor & clinical notes
    - Timeline events and documents
    - Stored AI analysis (hypotheses, alerts, care gaps, next best test)
    """
    nodes = db.query(EvidenceNode).filter(EvidenceNode.patient_id == patient_id).all()
    edges = db.query(EvidenceEdge).filter(EvidenceEdge.patient_id == patient_id).all()

    patient_record = {}
    patients_file = os.path.join(os.path.dirname(__file__), "data", "patients.json")
    if os.path.exists(patients_file):
        try:
            with open(patients_file, "r", encoding="utf-8") as f:
                patients_data = json.load(f)
                patient_record = patients_data.get(patient_id, {})
        except Exception as e:
            print(f"Error loading patients.json for context: {e}")

    pname = patient_record.get("name", "Unknown Patient")
    dob = patient_record.get("date_of_birth", "Not documented")
    gender = patient_record.get("gender", "Not documented")

    diagnoses = [n for n in nodes if n.evidence_type == "DIAGNOSIS"]
    labs = [n for n in nodes if n.evidence_type == "LAB_RESULT"]
    symptoms = [n for n in nodes if n.evidence_type == "SYMPTOM"]
    meds = [n for n in nodes if n.evidence_type == "MEDICATION"]

    # Also check patient_record fields in case nodes haven't been synced
    if not symptoms and patient_record.get("symptoms"):
        symptoms_data = patient_record["symptoms"]
        if isinstance(symptoms_data, list):
            symptoms = [{"name": str(s), "evidence_state": "PRESENT"} for s in symptoms_data]

    if not labs and patient_record.get("labs"):
        labs_data = patient_record["labs"]
        if isinstance(labs_data, list):
            labs = [{"name": str(l), "evidence_state": "PRESENT"} for l in labs_data]

    lines = []
    lines.append(f"=== PATIENT CLINICAL RECORD: {pname} (Patient ID: {patient_id}) ===")
    lines.append(f"Demographics: DOB: {dob} | Gender: {gender}")
    lines.append("")

    lines.append("--- EVIDENCE-SUPPORTED DIAGNOSTIC HYPOTHESES (AI Patterns) ---")
    if diagnoses:
        for d in diagnoses:
            dname = d.name if hasattr(d, "name") else d.get("name", "Hypothesis")
            conf = (d.confidence if hasattr(d, "confidence") else d.get("confidence")) or "Medium"
            verif = (d.verification_status if hasattr(d, "verification_status") else d.get("verification_status")) or "Pending"
            lines.append(f"• Hypothesis: {dname} (Confidence: {conf}, Status: {verif})")
    elif patient_record.get("patterns"):
        for p in patient_record["patterns"]:
            p_name = p.get("name") if isinstance(p, dict) else str(p)
            lines.append(f"• Hypothesis: {p_name}")
    else:
        lines.append("• No diagnostic hypotheses recorded.")
    lines.append("")

    lines.append("--- DOCUMENTED CLINICAL EVIDENCE (Facts) ---")
    lines.append("[Laboratory & Diagnostic Findings]:")
    tested_labs = [l for l in labs if (l.evidence_state if hasattr(l, "evidence_state") else l.get("evidence_state")) != "NOT_YET_TESTED"]
    if tested_labs:
        for l in tested_labs:
            lname = l.name if hasattr(l, "name") else l.get("name", "Test")
            lval = l.value if hasattr(l, "value") else l.get("value")
            lunit = l.unit if hasattr(l, "unit") else l.get("unit")
            lstate = l.evidence_state if hasattr(l, "evidence_state") else l.get("evidence_state", "PRESENT")
            lsrc = (l.source_document_name if hasattr(l, "source_document_name") else l.get("source_document_name")) or "Clinical Record"
            val_str = f": {lval} {lunit or ''}".strip() if lval else ""
            lines.append(f"  - {lname}{val_str} (State: {lstate}, Source: {lsrc})")
    else:
        lines.append("  - None recorded.")

    lines.append("[Documented Symptoms]:")
    tested_symps = [s for s in symptoms if (s.evidence_state if hasattr(s, "evidence_state") else s.get("evidence_state")) != "NOT_YET_TESTED"]
    if tested_symps:
        for s in tested_symps:
            sname = s.name if hasattr(s, "name") else s.get("name", "Symptom")
            sval = s.value if hasattr(s, "value") else s.get("value")
            sstate = s.evidence_state if hasattr(s, "evidence_state") else s.get("evidence_state", "PRESENT")
            val_str = f" ({sval})" if sval else ""
            lines.append(f"  - {sname}{val_str} (State: {sstate})")
    else:
        lines.append("  - None recorded.")

    lines.append("[Documented Medications]:")
    if meds:
        for m in meds:
            mname = m.name if hasattr(m, "name") else m.get("name", "Medication")
            mstate = m.evidence_state if hasattr(m, "evidence_state") else m.get("evidence_state", "PRESENT")
            lines.append(f"  - {mname} (State: {mstate})")
    elif patient_record.get("medications"):
        for m in patient_record["medications"]:
            m_name = m.get("name") if isinstance(m, dict) else str(m)
            lines.append(f"  - {m_name}")
    else:
        lines.append("  - None recorded.")
    lines.append("")

    lines.append("--- MISSING TESTS & CARE GAPS (Not Yet Tested) ---")
    missing_nodes = [n for n in nodes if (n.evidence_state if hasattr(n, "evidence_state") else n.get("evidence_state")) == "NOT_YET_TESTED"]
    if missing_nodes:
        for m in missing_nodes:
            mname = m.name if hasattr(m, "name") else m.get("name")
            mtype = m.evidence_type if hasattr(m, "evidence_type") else m.get("evidence_type")
            lines.append(f"• Missing/Needed: {mname} (Type: {mtype})")
    elif patient_record.get("missing_data"):
        for md in patient_record["missing_data"]:
            lines.append(f"• Missing/Needed: {md}")
    else:
        lines.append("• No missing tests explicitly flagged.")

    if patient_record.get("care_gaps"):
        for cg in patient_record["care_gaps"]:
            lines.append(f"• Care Gap: {cg}")

    if patient_record.get("next_best_test"):
        lines.append(f"• Recommended Next Best Test: {patient_record['next_best_test']}")
    lines.append("")

    lines.append("--- EVIDENCE RELATIONSHIPS (Graph Provenance) ---")
    node_dict = {n.id: n for n in nodes}
    if edges:
        for e in edges[:20]:
            s_node = node_dict.get(e.source_node_id)
            t_node = node_dict.get(e.target_node_id)
            if s_node and t_node:
                lines.append(f"• [{s_node.name}] --{e.relationship_type}--> [{t_node.name}]")
    lines.append("")

    lines.append("--- DOCTOR & CLINICAL NOTES ---")
    doc_notes = patient_record.get("doctor_notes_history", [])
    if doc_notes:
        for dn in doc_notes:
            lines.append(f"• [{dn.get('created_at', '')}] {dn.get('author', 'Doctor')}: \"{dn.get('text', '')}\"")
    elif patient_record.get("doctor_notes"):
        lines.append(f"• Doctor Note: \"{patient_record.get('doctor_notes')}\"")
    else:
        lines.append("• No doctor notes recorded.")

    if patient_record.get("clinical_notes"):
        lines.append(f"• AI Clinical Note Summary: {patient_record.get('clinical_notes')}")
    lines.append("")

    lines.append("--- DOCUMENTS & TIMELINE ---")
    docs = patient_record.get("documents", [])
    if docs:
        for doc in docs:
            lines.append(f"• Document: {doc.get('name')} (Status: {doc.get('status', 'Uploaded')}, Type: {doc.get('type')}, Uploaded: {doc.get('uploadedAt')})")
    else:
        lines.append("• No documents uploaded.")

    for evt in patient_record.get("timeline", []):
        lines.append(f"• Event: {evt.get('date')} - {evt.get('description')}")

    return "\n".join(lines)


# ============================================================
# SYNONYM MAPS FOR FACTUAL LOOKUP
# ============================================================

# Maps user query keywords → canonical node names (lowercase) in evidence_nodes
LAB_SYNONYMS = {
    "hemoglobin":  ["hemoglobin", "haemoglobin"],
    "haemoglobin": ["hemoglobin", "haemoglobin"],
    "hgb":         ["hemoglobin"],
    "hb":          ["hemoglobin"],
    "platelet":    ["platelet count", "thrombocytopenia"],
    "platelets":   ["platelet count", "thrombocytopenia"],
    "plt":         ["platelet count"],
    "wbc":         ["wbc count", "white blood cell"],
    "white blood cell": ["wbc count"],
    "white blood count": ["wbc count"],
    "leukocyte":   ["wbc count"],
    "rbc":         ["rbc", "rbc indices"],
    "red blood cell": ["rbc"],
    "red cell":    ["rbc"],
    "mcv":         ["mcv", "mean corpuscular volume"],
    "mchc":        ["mchc"],
    "rdw":         ["rdw"],
    "blood pressure": ["blood pressure"],
    "bp":          ["blood pressure"],
    "spo2":        ["spo2"],
    "oxygen saturation": ["spo2"],
    "o2":          ["spo2"],
    "temperature": ["temperature", "fever", "temp"],
    "dengue":      ["dengue ns1 antigen", "and dengue ns1 antigen"],
    "ns1":         ["dengue ns1 antigen", "and dengue ns1 antigen"],
    "malaria":     ["malaria"],
}

SYMPTOM_SYNONYMS = {
    "fever":    ["fever", "ongoing high fever"],
    "headache": ["headache"],
    "fatigue":  ["fatigue"],
    "body ache": ["body aches", "generalized body aches"],
    "myalgia":  ["body aches", "generalized body aches"],
    "chest pain": ["chest pain"],
    "dyspnea":  ["shortness of breath"],
    "shortness of breath": ["shortness of breath"],
    "sob":      ["shortness of breath"],
}

# CBC-related node name fragments
CBC_KEYWORDS = {"hemoglobin", "platelet count", "wbc count", "rbc", "mcv", "mchc", "rdw",
                "thrombocytopenia", "reticulocyte", "iron", "haemoglobin"}

MEDICAL_KNOWLEDGE_TRIGGERS = [
    "what is ", "what are ", "explain ", "define ", "what does ", "what do ", "meaning of ",
    "how does ", "how do ", "what causes ", "why is ", "why does ",
    "what is anemia", "what is thrombocytopenia", "what is dengue", "what is leukopenia",
    "what does low ", "what does high ", "what is cbc",
]

INTERPRETATION_TRIGGERS = [
    "does this patient", "does the patient", "does she", "does he",
    "is this patient", "is the patient",
    "are the results", "are these results", "is this normal", "is this abnormal",
    "are the platelets", "is the hemoglobin", "is the wbc",
    "is this concerning", "are these concerning", "should i be worried",
    "evidence of ", "consistent with ", "indicate ", "suggests ",
    "why is the", "why is this", "what does this mean",
    "could this be", "might this be",
]


def _get_all_nodes(patient_id: str, db: Session):
    """Return all evidence_nodes for patient_id, grouped by type."""
    nodes = db.query(EvidenceNode).filter(EvidenceNode.patient_id == patient_id).all()
    return nodes


def _factual_lab_lookup(nodes, query_lower: str):
    """
    Direct structured lookup from evidence_nodes.
    Returns list of matching (name, value, unit, source) tuples from LAB_RESULT nodes.
    """
    lab_nodes = [n for n in nodes if n.evidence_type == "LAB_RESULT" and n.evidence_state == "PRESENT"]

    # Find which synonyms match
    matched_canonical = set()
    for kw, canonicals in LAB_SYNONYMS.items():
        if kw in query_lower:
            matched_canonical.update(canonicals)

    if not matched_canonical:
        return []

    results = []
    for n in lab_nodes:
        n_name_lower = n.name.lower()
        for canon in matched_canonical:
            if canon in n_name_lower or n_name_lower in canon:
                val_str = n.value or ""
                unit_str = n.unit or ""
                src_str = n.source_document_name or "Clinical Record"
                results.append((n.name, val_str, unit_str, src_str))
                break
    return results


def _factual_symptom_lookup(nodes, query_lower: str):
    """Direct lookup of SYMPTOM nodes."""
    symp_nodes = [n for n in nodes if n.evidence_type == "SYMPTOM"]
    present = [n for n in symp_nodes if n.evidence_state == "PRESENT"]
    absent  = [n for n in symp_nodes if n.evidence_state == "TESTED_NEGATIVE"]

    matched = []
    for kw, canonicals in SYMPTOM_SYNONYMS.items():
        if kw in query_lower:
            for n in symp_nodes:
                n_lower = n.name.lower()
                if any(c in n_lower for c in canonicals):
                    val = f" ({n.value})" if n.value else ""
                    status = "Present" if n.evidence_state == "PRESENT" else ("Absent" if n.evidence_state == "TESTED_NEGATIVE" else n.evidence_state)
                    matched.append(f"{n.name}{val} — **{status}**")
    return matched


def _cbc_lookup(nodes):
    """Return all CBC-related lab results."""
    lab_nodes = [n for n in nodes if n.evidence_type == "LAB_RESULT" and n.evidence_state == "PRESENT"]
    cbc_results = []
    for n in lab_nodes:
        n_lower = n.name.lower()
        if any(kw in n_lower for kw in CBC_KEYWORDS):
            val_str = f"{n.value} {n.unit or ''}".strip()
            cbc_results.append((n.name, val_str, n.source_document_name or "Clinical Record"))
    return cbc_results


def _all_labs_lookup(nodes):
    """Return all present LAB_RESULT nodes."""
    return [n for n in nodes if n.evidence_type == "LAB_RESULT" and n.evidence_state == "PRESENT"]


def _all_symptoms_lookup(nodes):
    """Return all SYMPTOM nodes (present and negative)."""
    return [n for n in nodes if n.evidence_type == "SYMPTOM"]


def _medications_lookup(nodes):
    """Return all MEDICATION nodes."""
    return [n for n in nodes if n.evidence_type == "MEDICATION" and n.evidence_state not in ("NOT_YET_TESTED",)]


def _diagnoses_lookup(nodes):
    """Return all DIAGNOSIS nodes."""
    return [n for n in nodes if n.evidence_type == "DIAGNOSIS" and n.evidence_state == "PRESENT"]


def _missing_lookup(nodes):
    """Return all NOT_YET_TESTED nodes."""
    return [n for n in nodes if n.evidence_state == "NOT_YET_TESTED"]


def _classify_question(q: str) -> str:
    """
    Returns:
      'factual_lab'     - asking for a specific lab/vital value
      'factual_symptom' - asking about a specific symptom
      'factual_cbc'     - asking for CBC panel
      'factual_all_labs' - asking for all lab results
      'factual_symptoms_all' - listing all symptoms
      'factual_meds'    - asking about medications
      'factual_missing' - asking what's missing/not done
      'factual_diagnoses' - asking about diagnoses
      'factual_summary' - general summary
      'medical_knowledge' - general medical question
      'interpretation'  - patient-specific clinical reasoning
      'general'         - fallback
    """
    q_low = q.lower().strip()

    # Medical knowledge first
    for trigger in MEDICAL_KNOWLEDGE_TRIGGERS:
        if q_low.startswith(trigger) or f" {trigger}" in f" {q_low}":
            # Make sure it's NOT a patient-specific question
            if not any(pt in q_low for pt in ("this patient", "the patient", "she", "he", "her", "his", "they")):
                return "medical_knowledge"

    # Interpretation
    for trigger in INTERPRETATION_TRIGGERS:
        if trigger in q_low:
            return "interpretation"

    # CBC panel
    if any(k in q_low for k in ("cbc", "complete blood count", "blood count panel", "show cbc", "all cbc")):
        return "factual_cbc"

    # All labs
    if any(k in q_low for k in ("all lab", "all results", "lab results", "laboratory results", "all tests", "all findings")):
        return "factual_all_labs"

    # All symptoms
    if q_low.strip() in ("symptoms", "complaints", "symptom list") or q_low.startswith("what symptoms") or q_low.startswith("list symptoms") or q_low.startswith("what are the symptoms"):
        return "factual_symptoms_all"

    # Specific symptom lookup
    for kw in SYMPTOM_SYNONYMS:
        if kw in q_low:
            return "factual_symptom"

    # Specific lab/vital lookup
    for kw in LAB_SYNONYMS:
        if kw in q_low:
            return "factual_lab"

    # Missing info
    if any(k in q_low for k in ("missing", "not done", "not performed", "not yet", "care gap", "next test", "what's needed", "what is needed", "pending test")):
        return "factual_missing"

    # Medications
    if any(k in q_low for k in ("medication", "medications", "medicine", "medicines", "drug", "drugs", "prescription")):
        return "factual_meds"

    # Diagnoses / patterns
    if any(k in q_low for k in ("diagnosis", "diagnoses", "diagnose", "pattern", "hypothesis", "assessment", "impression")):
        return "factual_diagnoses"

    # Summary
    if any(k in q_low for k in ("summary", "overview", "summarize", "summarise", "tell me about", "brief", "profile")):
        return "factual_summary"

    # Documents
    if any(k in q_low for k in ("document", "uploaded", "file", "report")):
        return "factual_summary"

    return "general"


def route_patient_question(
    patient_id: str,
    question: str,
    nodes,
    patient_record: dict,
    patient_context: str,
    conversation_history: list,
) -> str:
    """
    Hybrid question router for the patient-aware AI chatbot.
    Dispatches to direct lookup, medical knowledge AI, or interpretation AI.
    """
    q_low = question.lower().strip()
    qtype = _classify_question(question)
    pname = patient_record.get("name", f"Patient {patient_id}")

    # ── FACTUAL LAB LOOKUP ───────────────────────────────────────────
    if qtype == "factual_lab":
        results = _factual_lab_lookup(nodes, q_low)
        if results:
            if len(results) == 1:
                name, val, unit, src = results[0]
                val_display = f"{val} {unit}".strip() if val else "documented but value not recorded"
                return f"**{name}:** {val_display}\n*(Source: {src})*"
            else:
                lines = [f"**Documented values for the requested test:**"]
                # Group by test name
                seen_names = {}
                for name, val, unit, src in results:
                    val_display = f"{val} {unit}".strip() if val else "value not recorded"
                    name_key = name.lower()
                    if name_key not in seen_names:
                        seen_names[name_key] = []
                    seen_names[name_key].append(f"{val_display} *(Source: {src})*")
                for test_name, entries in seen_names.items():
                    if len(entries) == 1:
                        lines.append(f"• **{test_name.title()}:** {entries[0]}")
                    else:
                        lines.append(f"• **{test_name.title()}:** multiple results:")
                        for e in entries:
                            lines.append(f"  — {e}")
                return "\n".join(lines)
        else:
            # Check if it's in the NOT_YET_TESTED list
            missing = _missing_lookup(nodes)
            for m in missing:
                if any(kw in m.name.lower() for kw in q_low.split()):
                    return f"That test is not yet documented for {pname}. It is listed as a pending/required test: **{m.name}**."
            return f"That result is not documented in {pname}'s record."

    # ── FACTUAL SYMPTOM LOOKUP ───────────────────────────────────────
    if qtype == "factual_symptom":
        matched = _factual_symptom_lookup(nodes, q_low)
        if matched:
            return "**Documented symptom status:**\n" + "\n".join(f"• {s}" for s in matched)
        return f"That symptom is not documented in {pname}'s record."

    # ── CBC PANEL ────────────────────────────────────────────────────
    if qtype == "factual_cbc":
        results = _cbc_lookup(nodes)
        if results:
            lines = [f"**CBC Results for {pname}:**\n"]
            # Deduplicate by test name (show all if multiple)
            seen = {}
            for name, val, src in results:
                seen.setdefault(name.lower(), []).append((name, val, src))
            for entries in seen.values():
                if len(entries) == 1:
                    name, val, src = entries[0]
                    lines.append(f"• **{name}:** {val} *(Source: {src})*")
                else:
                    name = entries[0][0]
                    lines.append(f"• **{name}:** multiple results:")
                    for _, val, src in entries:
                        lines.append(f"  — {val} *(Source: {src})*")
            # List what's NOT documented
            missing = _missing_lookup(nodes)
            cbc_missing = [m.name for m in missing if any(k in m.name.lower() for k in CBC_KEYWORDS)]
            if cbc_missing:
                lines.append(f"\n**Not yet documented:** {', '.join(cbc_missing)}")
            return "\n".join(lines)
        else:
            return f"No CBC results are documented in {pname}'s record."

    # ── ALL LABS ─────────────────────────────────────────────────────
    if qtype == "factual_all_labs":
        lab_nodes = _all_labs_lookup(nodes)
        if lab_nodes:
            lines = [f"**All Documented Lab & Vital Results for {pname}:**\n"]
            for n in lab_nodes:
                val_str = f"{n.value} {n.unit or ''}".strip()
                src_str = n.source_document_name or "Clinical Record"
                lines.append(f"• **{n.name}:** {val_str} *(Source: {src_str})*")
            missing = _missing_lookup(nodes)
            if missing:
                lines.append("\n**Not Yet Tested / Missing:**")
                for m in missing:
                    if m.evidence_type == "LAB_RESULT":
                        lines.append(f"• {m.name}")
            return "\n".join(lines)
        return f"No laboratory results are documented in {pname}'s record."

    # ── ALL SYMPTOMS ─────────────────────────────────────────────────
    if qtype == "factual_symptoms_all":
        symp_nodes = _all_symptoms_lookup(nodes)
        present = [n for n in symp_nodes if n.evidence_state == "PRESENT"]
        absent  = [n for n in symp_nodes if n.evidence_state == "TESTED_NEGATIVE"]
        unknown = [n for n in symp_nodes if n.evidence_state == "NOT_YET_TESTED"]

        if not symp_nodes:
            return f"No symptoms are documented in {pname}'s record."

        lines = [f"**Documented Symptoms for {pname}:**\n"]
        if present:
            lines.append("**Present:**")
            for n in present:
                val_str = f" ({n.value})" if n.value else ""
                lines.append(f"• {n.name}{val_str}")
        if absent:
            lines.append("\n**Absent (Tested Negative):**")
            for n in absent:
                lines.append(f"• {n.name}")
        if unknown:
            lines.append("\n**Not Yet Assessed:**")
            for n in unknown:
                lines.append(f"• {n.name}")
        return "\n".join(lines)

    # ── MEDICATIONS ──────────────────────────────────────────────────
    if qtype == "factual_meds":
        med_nodes = _medications_lookup(nodes)
        # Also check patient_record
        json_meds = patient_record.get("medications", [])
        if med_nodes:
            lines = [f"**Documented Medications for {pname}:**"]
            for n in med_nodes:
                val_str = f": {n.value}" if n.value else ""
                lines.append(f"• {n.name}{val_str} (State: {n.evidence_state})")
            return "\n".join(lines)
        elif json_meds:
            names = ", ".join(m.get("name", str(m)) for m in json_meds)
            return f"**Medications on record:** {names}"
        # Check if explicitly flagged as not documented
        med_missing = [n for n in nodes if n.evidence_type == "MEDICATION" and n.evidence_state == "NOT_YET_TESTED"]
        if med_missing:
            return f"Medications have not been documented for {pname}. This is flagged as a care gap."
        return f"No medication information is documented in {pname}'s record."

    # ── MISSING / CARE GAPS ──────────────────────────────────────────
    if qtype == "factual_missing":
        missing = _missing_lookup(nodes)
        if not missing:
            return f"No pending tests or care gaps are explicitly flagged in {pname}'s record."

        lines = [f"**Pending Tests & Missing Information for {pname}:**\n"]
        by_type = {}
        for m in missing:
            by_type.setdefault(m.evidence_type, []).append(m.name)

        for etype in ["LAB_RESULT", "SYMPTOM", "MEDICATION", "CLINICAL_NOTE"]:
            if etype in by_type:
                label = {"LAB_RESULT": "Labs Not Yet Done", "SYMPTOM": "Symptoms Not Assessed",
                         "MEDICATION": "Medication Info Missing", "CLINICAL_NOTE": "Clinical Documentation Gaps"}.get(etype, etype)
                lines.append(f"**{label}:**")
                for name in by_type[etype]:
                    lines.append(f"• {name}")
                lines.append("")

        # Next best test from patient_record documents
        for doc in patient_record.get("documents", []):
            nbt = doc.get("next_best_test")
            if nbt and isinstance(nbt, dict):
                lines.append(f"**Recommended Next Best Test:** {nbt.get('name', '')}")
                lines.append(f"*{nbt.get('reason', '')}*")
                break

        return "\n".join(lines)

    # ── DIAGNOSES / PATTERNS ─────────────────────────────────────────
    if qtype == "factual_diagnoses":
        diag_nodes = _diagnoses_lookup(nodes)
        if diag_nodes:
            lines = [f"**Evidence-Supported Diagnostic Hypotheses for {pname}:**\n"]
            for n in diag_nodes:
                conf = n.confidence or "Medium"
                verif = n.verification_status or "Pending"
                src = n.source_document_name or "AI Pattern Analysis"
                lines.append(f"• **{n.name}** (Confidence: {conf}, Status: {verif}) — *{src}*")
            lines.append("\n*These are evidence-supported hypotheses, not confirmed diagnoses. Clinical correlation is required.*")
            return "\n".join(lines)
        return f"No diagnostic hypotheses are currently documented for {pname}."

    # ── SUMMARY ──────────────────────────────────────────────────────
    if qtype == "factual_summary":
        lab_nodes = _all_labs_lookup(nodes)
        symp_nodes = [n for n in nodes if n.evidence_type == "SYMPTOM" and n.evidence_state == "PRESENT"]
        diag_nodes = _diagnoses_lookup(nodes)
        missing_nodes = _missing_lookup(nodes)
        docs = patient_record.get("documents", [])
        doc_notes_hist = patient_record.get("doctor_notes_history", [])

        dob = patient_record.get("date_of_birth", "Not documented")
        gender = patient_record.get("gender", "Not documented")

        lines = [f"## 📋 Clinical Summary — {pname} ({patient_id})"]
        lines.append(f"**Demographics:** DOB: {dob} | Gender: {gender}\n")

        if docs:
            lines.append(f"**Uploaded Documents ({len(docs)}):**")
            for d in docs:
                status = d.get("analysis_status", d.get("status", "Uploaded"))
                lines.append(f"• {d.get('name')} *(Analysis: {status})*")
            lines.append("")

        if diag_nodes:
            lines.append("**Evidence-Supported Hypotheses:**")
            for n in diag_nodes:
                lines.append(f"• {n.name} *(Confidence: {n.confidence or 'Medium'}, Status: {n.verification_status or 'Pending'})*")
            lines.append("")

        if lab_nodes:
            lines.append("**Documented Lab & Vital Results:**")
            for n in lab_nodes:
                val_str = f"{n.value} {n.unit or ''}".strip()
                lines.append(f"• {n.name}: {val_str} *(Source: {n.source_document_name or 'Clinical Record'})*")
            lines.append("")

        if symp_nodes:
            lines.append("**Documented Symptoms:**")
            for n in symp_nodes:
                val_str = f" ({n.value})" if n.value else ""
                lines.append(f"• {n.name}{val_str}")
            lines.append("")

        if missing_nodes:
            lines.append(f"**Pending / Missing ({len(missing_nodes)} items):**")
            for m in missing_nodes[:6]:
                lines.append(f"• {m.name} [{m.evidence_type}]")
            lines.append("")

        if doc_notes_hist:
            latest = doc_notes_hist[0]
            lines.append(f"**Latest Doctor Note** ({latest.get('created_at', '')[:10]}, {latest.get('author', 'Doctor')}):")
            lines.append(f"> {latest.get('text', '')}")

        lines.append("\n*Hypotheses are evidence-supported patterns requiring clinical correlation. Not confirmed diagnoses.*")
        return "\n".join(lines)

    # ── MEDICAL KNOWLEDGE / INTERPRETATION → use patient_context + hint ──
    if qtype in ("medical_knowledge", "interpretation", "general"):
        return None  # Signal to use AI/context path


def assemble_structured_context(patient_id: str, nodes, patient_record: dict) -> str:
    """
    Build a clean, compact, structured context block for AI prompts.
    Uses the live node objects directly instead of string parsing.
    """
    pname = patient_record.get("name", f"Patient {patient_id}")
    dob   = patient_record.get("date_of_birth", "Unknown")
    gender= patient_record.get("gender", "Unknown")

    lab_nodes  = [n for n in nodes if n.evidence_type == "LAB_RESULT" and n.evidence_state == "PRESENT"]
    symp_nodes = [n for n in nodes if n.evidence_type == "SYMPTOM"]
    diag_nodes = [n for n in nodes if n.evidence_type == "DIAGNOSIS" and n.evidence_state == "PRESENT"]
    med_nodes  = [n for n in nodes if n.evidence_type == "MEDICATION"]
    miss_nodes = [n for n in nodes if n.evidence_state == "NOT_YET_TESTED"]

    sections = []
    sections.append(f"=== PATIENT: {pname} (ID: {patient_id}) | DOB: {dob} | Gender: {gender} ===\n")

    if lab_nodes:
        sections.append("LABORATORY & VITAL RESULTS (documented, from uploaded records):")
        for n in lab_nodes:
            val_str = f"{n.value} {n.unit or ''}".strip()
            sections.append(f"  • {n.name}: {val_str}  [Source: {n.source_document_name or 'Clinical Record'}]")
        sections.append("")

    symp_present = [n for n in symp_nodes if n.evidence_state == "PRESENT"]
    symp_absent  = [n for n in symp_nodes if n.evidence_state == "TESTED_NEGATIVE"]
    if symp_present:
        sections.append("PRESENT SYMPTOMS:")
        for n in symp_present:
            val_str = f" ({n.value})" if n.value else ""
            sections.append(f"  • {n.name}{val_str}")
        sections.append("")
    if symp_absent:
        sections.append("ABSENT SYMPTOMS (explicitly tested negative):")
        for n in symp_absent:
            sections.append(f"  • {n.name}: Absent")
        sections.append("")

    if diag_nodes:
        sections.append("EVIDENCE-SUPPORTED HYPOTHESES (AI Pattern Analysis, not confirmed diagnoses):")
        for n in diag_nodes:
            sections.append(f"  • {n.name} (Confidence: {n.confidence or 'Medium'}, Status: {n.verification_status or 'Pending'}) [Source: {n.source_document_name or 'AI'}]")
        sections.append("")

    if med_nodes:
        sections.append("MEDICATIONS:")
        for n in med_nodes:
            val_str = f": {n.value}" if n.value else ""
            sections.append(f"  • {n.name}{val_str} [{n.evidence_state}]")
        sections.append("")

    if miss_nodes:
        sections.append("MISSING / NOT YET TESTED:")
        for n in miss_nodes:
            sections.append(f"  • {n.name} [{n.evidence_type}]")
        sections.append("")

    # Doctor notes
    doc_notes = patient_record.get("doctor_notes_history", [])
    if doc_notes:
        sections.append("DOCTOR NOTES:")
        for dn in doc_notes[:3]:
            sections.append(f"  • [{dn.get('created_at','')[:10]}] {dn.get('author','Doctor')}: {dn.get('text','')}")
        sections.append("")

    # Document-level AI analysis summaries
    for doc in patient_record.get("documents", []):
        if doc.get("analysis_status") == "Completed" and doc.get("overall_summary"):
            sections.append(f"DOCUMENT AI ANALYSIS — {doc.get('name')}:")
            sections.append(f"  Summary: {doc.get('overall_summary','')[:300]}")
            alerts = doc.get("alerts", [])
            if alerts:
                sections.append(f"  Alert: {alerts[0].get('message','')}" if isinstance(alerts[0], dict) else f"  Alert: {alerts[0]}")
            nbt = doc.get("next_best_test")
            if nbt and isinstance(nbt, dict):
                sections.append(f"  Next Best Test: {nbt.get('name','')}")
            sections.append("")

    return "\n".join(sections)


@app.post("/patients/{patient_id}/chat", response_model=PatientChatResponse)
def patient_chat_endpoint(
    patient_id: str,
    payload: PatientChatRequest,
    db: Session = Depends(get_db)
):
    """
    Patient-aware AI chat assistant — hybrid question router.
    1. Factual questions → direct structured lookup from evidence_nodes (no AI, no hallucination).
    2. Medical knowledge questions → AI with no patient context needed.
    3. Interpretation questions → AI grounded strictly on this patient's evidence nodes.
    """
    target_patient_id = (payload.patient_id or patient_id or "").strip()
    if not target_patient_id:
        raise HTTPException(status_code=400, detail="Patient ID is required.")

    question = (payload.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question is required.")

    # 1. Load structured data
    nodes = _get_all_nodes(target_patient_id, db)
    patient_record = {}
    patients_file = os.path.join(os.path.dirname(__file__), "data", "patients.json")
    if os.path.exists(patients_file):
        try:
            with open(patients_file, "r", encoding="utf-8") as f:
                all_patients = json.load(f)
                patient_record = all_patients.get(target_patient_id, {})
        except Exception as e:
            print(f"[CHAT] patients.json load error: {e}")

    pname = patient_record.get("name", f"Patient {target_patient_id}")
    print(f"[CHAT] Patient: {target_patient_id} ({pname}) | Q: '{question}' | Nodes: {len(nodes)}")

    # 2. Try factual lookup first (no AI needed)
    patient_context = assemble_patient_context(target_patient_id, db)  # for legacy fallback
    structured_ctx  = assemble_structured_context(target_patient_id, nodes, patient_record)

    factual_answer = route_patient_question(
        target_patient_id, question, nodes, patient_record, patient_context,
        payload.conversation_history or []
    )
    if factual_answer is not None:
        print(f"[CHAT] Factual answer ({len(factual_answer)} chars)")
        return PatientChatResponse(answer=factual_answer)

    # 3. Determine question type for AI routing
    qtype = _classify_question(question)
    q_low = question.lower()

    # 4. AI path
    api_key = os.environ.get("OPENAI_API_KEY")

    if qtype == "medical_knowledge":
        # Pure medical knowledge — no patient data needed, just medical context
        system_prompt = (
            "You are MedPath AI, a knowledgeable clinical assistant for doctors. "
            "Answer the following medical knowledge question clearly and concisely. "
            "Do not invent patient-specific data. "
            "If relevant, mention how the answer may apply to a selected patient's documented findings."
        )
        # Provide minimal patient context as a hint
        lab_nodes = [n for n in nodes if n.evidence_type == "LAB_RESULT" and n.evidence_state == "PRESENT"]
        if lab_nodes:
            patient_hint = f"\nFor reference, this patient ({pname}) has: " + ", ".join(
                f"{n.name}: {n.value} {n.unit or ''}".strip() for n in lab_nodes[:5]
            )
            system_prompt += patient_hint
    else:
        # Interpretation — ground strictly in this patient's actual evidence
        system_prompt = f"""You are MedPath AI, a clinical assistant helping doctors reason about patient records.

CRITICAL RULES:
1. ONLY use the patient data provided below. Do NOT invent, estimate, or infer values not documented.
2. For documented values, state them exactly as recorded. Example: "Hemoglobin is documented at 9.2 g/dL."
3. For interpretations, clearly label what is a documented fact vs clinical interpretation.
4. If information is not in the record, say: "That is not documented in {pname}'s record."
5. NEVER hallucinate lab values, medications, symptoms, or diagnoses.

{structured_ctx}"""

    if api_key:
        messages = [{"role": "system", "content": system_prompt}]
        if payload.conversation_history:
            for msg in (payload.conversation_history or [])[-6:]:
                role_str = "assistant" if msg.get("role") in ("assistant", "ai") else "user"
                content = msg.get("content") or ""
                if content:
                    messages.append({"role": role_str, "content": content})
        messages.append({"role": "user", "content": question})

        try:
            resp = http_requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": "gpt-4o-mini", "messages": messages, "temperature": 0.2, "max_tokens": 700},
                timeout=15
            )
            if resp.status_code == 200:
                ai_answer = resp.json()["choices"][0]["message"]["content"].strip()
                if ai_answer:
                    print(f"[CHAT] OpenAI ({qtype}) answer ({len(ai_answer)} chars)")
                    return PatientChatResponse(answer=ai_answer)
        except Exception as e:
            print(f"[CHAT] OpenAI error: {e}")

    # 5. Fallback: text-based grounded answer from structured_ctx
    q_tokens = [t for t in q_low.replace("?","").replace(".","").split()
                if len(t) > 3 and t not in ("what", "when", "where", "which", "about", "this", "that", "patient", "tell", "show", "does", "have", "with")]

    ctx_lines = structured_ctx.split("\n")
    matched = [l.strip() for l in ctx_lines if l.strip() and any(tok in l.lower() for tok in q_tokens)]
    if matched:
        answer = f"**From {pname}'s record (relevant findings):**\n" + "\n".join(f"• {l.lstrip('•  ')}" for l in matched[:8])
    else:
        answer = f"That information is not documented in {pname}'s record."

    print(f"[CHAT] Text-fallback answer ({len(answer)} chars)")
    return PatientChatResponse(answer=answer)

