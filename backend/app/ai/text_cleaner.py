import re

def normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def chunk_text(text: str, chunk_size: int = 8000) -> list[str]:
    return [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
