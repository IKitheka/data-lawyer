import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    PROJECT_NAME: str = "Data Lawyer"
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./data_lawyer.db")
    OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")  # optional

settings = Settings()
