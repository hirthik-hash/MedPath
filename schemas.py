from pydantic import BaseModel
from typing import Optional


# ============================================================
# USER CREATE / REGISTRATION
# ============================================================

class UserCreate(BaseModel):

    # Common fields
    role: str
    full_name: str
    email: str
    mobile: str
    date_of_birth: str
    password: str

    # --------------------------------------------------------
    # Patient fields
    # --------------------------------------------------------

    gender: Optional[str] = None

    # --------------------------------------------------------
    # Doctor fields
    # --------------------------------------------------------

    specialization: Optional[str] = None
    license_number: Optional[str] = None
    hospital: Optional[str] = None

    # --------------------------------------------------------
    # CHW fields
    # --------------------------------------------------------

    employee_id: Optional[str] = None
    organization: Optional[str] = None
    region: Optional[str] = None


# ============================================================
# USER RESPONSE
# ============================================================

class UserResponse(BaseModel):

    # User ID
    user_id: str

    # Common user information
    role: str
    full_name: str
    email: str
    mobile: str
    date_of_birth: str

    # --------------------------------------------------------
    # Patient
    # --------------------------------------------------------

    gender: Optional[str] = None
    patient_id: Optional[str] = None

    # --------------------------------------------------------
    # Doctor
    # --------------------------------------------------------

    specialization: Optional[str] = None
    license_number: Optional[str] = None
    hospital: Optional[str] = None

    # --------------------------------------------------------
    # CHW
    # --------------------------------------------------------

    employee_id: Optional[str] = None
    organization: Optional[str] = None
    region: Optional[str] = None


# ============================================================
# USER LOGIN
# ============================================================

class UserLogin(BaseModel):

    email: str
    password: str
    role: str


# ============================================================
# EVIDENCE SCHEMAS
# ============================================================

VALID_EVIDENCE_STATES = {"PRESENT", "TESTED_NEGATIVE", "NOT_YET_TESTED"}

VALID_RELATIONSHIP_TYPES = {
    "SUPPORTS",
    "CONTRADICTS",
    "TEMPORAL",
    "ASSOCIATED_WITH",
    "DERIVED_FROM",
    "CORROBORATES",
    # Legacy relationship types preserved:
    "INDICATES",
    "CAUSES",
    "TREATED_BY",
    "CONFIRMS",
}


class EvidenceNodeCreate(BaseModel):
    evidence_type: str
    name: str
    value: Optional[str] = None
    unit: Optional[str] = None
    date: Optional[str] = None
    source_document_id: Optional[str] = None
    source_document_name: Optional[str] = None
    source_type: Optional[str] = "N8N_AI_ANALYSIS"
    confidence: Optional[str] = "High"
    verification_status: Optional[str] = "Confirmed"
    evidence_state: Optional[str] = "PRESENT"


class EvidenceNodeResponse(BaseModel):
    id: int
    patient_id: str
    evidence_type: str
    name: str
    value: Optional[str] = None
    unit: Optional[str] = None
    date: Optional[str] = None
    source_document_id: Optional[str] = None
    source_document_name: Optional[str] = None
    source_type: str
    confidence: str
    verification_status: str
    evidence_state: str
    created_at: str


class EvidenceEdgeCreate(BaseModel):
    source_node_id: int
    target_node_id: int
    relationship_type: str
    confidence: Optional[str] = "High"


class EvidenceEdgeResponse(BaseModel):
    id: int
    patient_id: str
    source_node_id: int
    target_node_id: int
    relationship_type: str
    confidence: Optional[str] = "High"
    created_at: str


class EvidenceGraphResponse(BaseModel):
    nodes: list[EvidenceNodeResponse]
    edges: list[EvidenceEdgeResponse]


# ============================================================
# CHAT SCHEMAS
# ============================================================

class PatientChatRequest(BaseModel):
    patient_id: Optional[str] = None
    question: str
    conversation_history: Optional[list[dict]] = []



class PatientChatResponse(BaseModel):
    answer: str

