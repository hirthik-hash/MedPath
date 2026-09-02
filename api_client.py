import requests

# ============================================================
# MEDPATH API CLIENT
# ============================================================

API_BASE_URL = "http://127.0.0.1:8000"


# ============================================================
# ERROR RESPONSE
# ============================================================

class _ErrorResponse:
    def __init__(self, message: str, status_code: int = 503):
        self.status_code = status_code
        self._message = message

    def json(self):
        return {"detail": self._message}

    @property
    def text(self):
        return self._message


def _error_response(message: str, status_code: int = 503):
    return _ErrorResponse(message, status_code)


# ============================================================
# HTTP HELPERS
# ============================================================

def _post(endpoint: str, data: dict):
    try:
        return requests.post(
            f"{API_BASE_URL}{endpoint}",
            json=data,
            timeout=10
        )

    except requests.exceptions.ConnectionError:
        return _error_response(
            "Cannot connect to MedPath backend. "
            "Make sure FastAPI/Uvicorn is running on port 8000."
        )

    except requests.exceptions.Timeout:
        return _error_response(
            "The MedPath backend took too long to respond."
        )

    except requests.exceptions.RequestException as e:
        return _error_response(str(e))


def _get(endpoint: str, params=None):
    try:
        return requests.get(
            f"{API_BASE_URL}{endpoint}",
            params=params,
            timeout=10
        )

    except requests.exceptions.ConnectionError:
        return _error_response(
            "Cannot connect to MedPath backend. "
            "Make sure FastAPI/Uvicorn is running on port 8000."
        )

    except requests.exceptions.Timeout:
        return _error_response(
            "The MedPath backend took too long to respond."
        )

    except requests.exceptions.RequestException as e:
        return _error_response(str(e))


# ============================================================
# REGISTER
# ============================================================

def register_user(user_data: dict):
    """
    Register a new Patient, Doctor, or CHW.
    """

    return _post(
        "/register",
        user_data
    )


# ============================================================
# LOGIN
# ============================================================

def login_user(login_data: dict):
    """
    Login using email, password and role.

    Example:
        {
            "email": "test@example.com",
            "password": "Test@12345",
            "role": "patient"
        }
    """

    return _post(
        "/login",
        login_data
    )


# ============================================================
# CURRENT USER
# ============================================================

def get_current_user(user_id):
    """
    Get the currently logged-in user's information
    from FastAPI.

    The user ID is sent as a query parameter.
    """

    return _get(
        "/me",
        params={"user_id": user_id}
    )


# ============================================================
# GET PATIENT
# ============================================================

def get_patient(patient_id):
    """
    Get a patient's complete record from FastAPI.
    """

    if not patient_id:
        return _error_response(
            "Patient ID is required.",
            status_code=400
        )

    return _get(
        f"/patients/{patient_id}"
    )


# ============================================================
# GET PATIENT TIMELINE
# ============================================================

def get_patient_timeline(patient_id):
    """
    Get the healthcare timeline for a patient.

    This will be used later by:
        Patient dashboard
        Doctor dashboard
        CHW dashboard
        AI analysis
        AI chatbot
    """

    if not patient_id:
        return _error_response(
            "Patient ID is required.",
            status_code=400
        )

    return _get(
        f"/patients/{patient_id}/timeline"
    )


# ============================================================
# GET PATIENT MEDICAL DATA
# ============================================================

def get_patient_medical_data(patient_id):
    """
    Get medical information belonging to a patient.

    This will eventually provide the common data source for:
        - Analysis
        - Insights
        - Chatbot
        - Doctor view
        - CHW coordination
    """

    if not patient_id:
        return _error_response(
            "Patient ID is required.",
            status_code=400
        )

    return _get(
        f"/patients/{patient_id}/medical-data"
    )


# ============================================================
# HEALTH CHECK
# ============================================================

def check_backend():
    """
    Check whether the FastAPI backend is running.
    """

    return _get("/")


# ============================================================
# DEBUG USERS
# ============================================================

def get_debug_users():
    """
    Development-only helper.

    Used to verify that users are being stored
    correctly in SQLite.

    Remove this later before the final production version.
    """

    return _get("/debug/users")


# ============================================================
# CLINICAL EVIDENCE GRAPH
# ============================================================

def get_patient_evidence(patient_id: str):
    if not patient_id:
        return _error_response("Patient ID is required.", status_code=400)
    return _get(f"/patients/{patient_id}/evidence")


def get_patient_evidence_graph(patient_id: str):
    if not patient_id:
        return _error_response("Patient ID is required.", status_code=400)
    return _get(f"/patients/{patient_id}/evidence/graph")


def get_evidence_details(patient_id: str, evidence_id: int):
    if not patient_id or not evidence_id:
        return _error_response("Patient ID and Evidence ID are required.", status_code=400)
    return _get(f"/patients/{patient_id}/evidence/{evidence_id}")


def ingest_patient_evidence(patient_id: str, payload: dict):
    if not patient_id:
        return _error_response("Patient ID is required.", status_code=400)
    return _post(f"/patients/{patient_id}/evidence/ingest", payload)


