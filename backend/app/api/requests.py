from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

class DeletionRequestInput(BaseModel):
    service_name: str
    company_email: str | None = None
    jurisdiction: str = "Kenya DPA"  # or GDPR / CCPA

@router.post("/deletion-email")
def deletion_email(payload: DeletionRequestInput):
    # Template-based (works without LLM)
    email = payload.company_email or "privacy@company.com"
    subj = f"Request for Data Deletion - {payload.service_name}"

    body = f"""Subject: {subj}

Hello,

I am writing to formally request deletion of my personal data held by {payload.service_name}, in accordance with {payload.jurisdiction}.

Please confirm:
1) Whether you process my personal data,
2) The categories of data you hold,
3) All third parties with whom my data has been shared,
4) That my personal data has been deleted from your systems and backups where applicable.

If you require additional information to identify my account, please let me know what is strictly necessary.

Regards,
[Your Full Name]
[Your Email / Phone]
"""
    return {"to": email, "subject": subj, "body": body}
