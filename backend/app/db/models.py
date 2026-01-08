from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Float, DateTime
from app.db.session import Base


class PolicyAnalysis(Base):
    __tablename__ = "policy_analyses"

    id = Column(Integer, primary_key=True, index=True)

    url = Column(String, index=True)
    domain = Column(String, index=True)
    service_name = Column(String, index=True)

    policy_hash = Column(String, index=True)
    raw_text = Column(Text)

    summary = Column(Text)
    risk_level = Column(String, index=True)  # LOW/MEDIUM/HIGH
    risk_score = Column(Float)

    jurisdiction = Column(String)
    red_flags_json = Column(Text)   # JSON string
    reasoning_json = Column(Text)   # JSON string
    extracted_json = Column(Text)   # JSON string

    created_at = Column(DateTime, default=datetime.utcnow)

    # ---- LLM cache fields ----
    llm_summary = Column(Text, nullable=True)
    llm_red_flags_json = Column(Text, nullable=True)
    llm_risk_level = Column(String(20), nullable=True)
    llm_model = Column(String(100), nullable=True)
