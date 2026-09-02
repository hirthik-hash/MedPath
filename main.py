from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

try:
    from .database import Base, engine, get_db
    from .models import User, Patient, EvidenceNode, EvidenceEdge
    from .schemas import UserCreate, UserResponse, UserLogin, EvidenceNodeCreate, EvidenceEdgeCreate
except ImportError:
    from database import Base, engine, get_db
    from models import User, Patient, EvidenceNode, EvidenceEdge
    from schemas import UserCreate, UserResponse, UserLogin, EvidenceNodeCreate, EvidenceEdgeCreate



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
            if existing.evidence_state != estate:
                existing.evidence_state = estate
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
                EvidenceEdge.target_node_id == tgt_id,
                EvidenceEdge.relationship_type == rel_type
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
                created_edges.append(existing_edge)

                
    return {
        "message": f"Successfully processed {len(created_nodes)} evidence nodes and {len(created_edges)} edges",
        "patient_id": patient_id
    }
