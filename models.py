from sqlalchemy import Column, Integer, String
try:
    from .database import Base
except ImportError:
    from database import Base




# ============================================================
# USER TABLE
# ============================================================

class User(Base):

    __tablename__ = "users"

    # --------------------------------------------------------
    # Primary Key
    # --------------------------------------------------------

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    # --------------------------------------------------------
    # Common User Information
    # --------------------------------------------------------

    role = Column(
        String,
        nullable=False
    )

    full_name = Column(
        String,
        nullable=False
    )

    email = Column(
        String,
        unique=True,
        nullable=False,
        index=True
    )

    mobile = Column(
        String,
        nullable=False
    )

    date_of_birth = Column(
        String,
        nullable=False
    )

    gender = Column(
        String,
        nullable=True
    )

    password = Column(
        String,
        nullable=False
    )

    # --------------------------------------------------------
    # Doctor Information
    # --------------------------------------------------------

    specialization = Column(
        String,
        nullable=True
    )

    license_number = Column(
        String,
        nullable=True
    )

    hospital = Column(
        String,
        nullable=True
    )

    # --------------------------------------------------------
    # CHW Information
    # --------------------------------------------------------

    employee_id = Column(
        String,
        nullable=True
    )

    organization = Column(
        String,
        nullable=True
    )

    region = Column(
        String,
        nullable=True
    )


# ============================================================
# PATIENT TABLE
# ============================================================

class Patient(Base):

    __tablename__ = "patients"

    # --------------------------------------------------------
    # Internal Patient Database ID
    # --------------------------------------------------------

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    # --------------------------------------------------------
    # MedPath Patient ID
    # Example: MP00001
    # --------------------------------------------------------

    patient_id = Column(
        String,
        unique=True,
        nullable=False,
        index=True
    )

    # --------------------------------------------------------
    # Link Patient to User
    # Patient.linked_user_id → User.id
    # --------------------------------------------------------

    linked_user_id = Column(
        Integer,
        nullable=False,
        index=True
    )

    # --------------------------------------------------------
    # Patient Information
    # --------------------------------------------------------

    name = Column(
        String,
        nullable=False
    )

    date_of_birth = Column(
        String,
        nullable=False
    )

    gender = Column(
        String,
        nullable=True
    )

    phone = Column(
        String,
        nullable=True
    )


# ============================================================
# EVIDENCE NODE TABLE
# ============================================================

class EvidenceNode(Base):
    """
    Stores individual evidence nodes in the MedPath Clinical Evidence Graph.
    
    Supported evidence_state values:
    - PRESENT: Clinical finding/symptom present or lab test result abnormal/positive.
    - TESTED_NEGATIVE: Test performed and result was normal/negative or symptom explicitly absent.
    - NOT_YET_TESTED: Test recommended/ordered but not yet performed or no result available.
    """

    __tablename__ = "evidence_nodes"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    patient_id = Column(
        String,
        nullable=False,
        index=True
    )

    evidence_type = Column(
        String,
        nullable=False
    )

    name = Column(
        String,
        nullable=False
    )

    value = Column(
        String,
        nullable=True
    )

    unit = Column(
        String,
        nullable=True
    )

    date = Column(
        String,
        nullable=True
    )

    source_document_id = Column(
        String,
        nullable=True
    )

    source_document_name = Column(
        String,
        nullable=True
    )

    source_type = Column(
        String,
        nullable=False,
        default="N8N_AI_ANALYSIS"
    )

    confidence = Column(
        String,
        nullable=False,
        default="High"
    )

    verification_status = Column(
        String,
        nullable=False,
        default="Confirmed"
    )

    evidence_state = Column(
        String,
        nullable=False,
        default="PRESENT"
    )

    created_at = Column(
        String,
        nullable=False
    )


# ============================================================
# EVIDENCE EDGE TABLE
# ============================================================

class EvidenceEdge(Base):
    """
    Stores directed relationships/edges between evidence nodes and hypotheses.
    
    Supported relationship_type values:
    - SUPPORTS: Evidence supports hypothesis/pattern.
    - CONTRADICTS: Evidence refutes or contradicts hypothesis/pattern.
    - TEMPORAL: Time-sequence relation between events/findings.
    - ASSOCIATED_WITH: Clinical association between findings.
    - DERIVED_FROM: Evidence derived from document/note.
    - CORROBORATES: Finding corroborates another finding.
    - Legacy types: INDICATES, CAUSES, TREATED_BY, CONFIRMS.
    """

    __tablename__ = "evidence_edges"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    patient_id = Column(
        String,
        nullable=False,
        index=True
    )

    source_node_id = Column(
        Integer,
        nullable=False,
        index=True
    )

    target_node_id = Column(
        Integer,
        nullable=False,
        index=True
    )

    relationship_type = Column(
        String,
        nullable=False
    )

    confidence = Column(
        String,
        nullable=True,
        default="High"
    )

    created_at = Column(
        String,
        nullable=False
    )
