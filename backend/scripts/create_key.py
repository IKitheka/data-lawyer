import os

from dotenv import load_dotenv
load_dotenv()

from backend.app.db import engine  # noqa: E402

def main():
    email = (os.getenv("BOOTSTRAP_EMAIL") or "").strip()
    if not email:
        raise SystemExit("BOOTSTRAP_EMAIL is required")

    # TODO: your existing key creation logic goes here
    print("✅ Env OK. DATABASE_URL set:", bool(os.getenv("DATABASE_URL")))
    print("✅ Creating key for:", email)

if __name__ == "__main__":
    main()
