"""Clause segmentation + multi-label clause classifier (TF-IDF + logistic regression)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

import joblib
from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer

ARTIFACT = Path(__file__).parent / "artifacts" / "clause_model.joblib"
_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n{1,}|\s*[•\u2022]\s*")


# Numbers carry meaning as a CLASS ("12 months", "seven years", "30 days" are all "a stated duration"), not as
# individual tokens. Mapping them to one token lets the classifier learn that class from few examples.
_DURATION = re.compile(
    r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirty|sixty|ninety)\b(?=[\s-]+(?:day|week|month|year)s?\b)"
)


def normalize_text(t: str) -> str:
    t = t.lower()
    t = _DURATION.sub(" durnum ", t)
    return re.sub(r"\d+", " anynum ", t)


class Vec:
    """Word (1-2gram) + char (3-5gram) TF-IDF, so wording variants still match."""
    def __init__(self):
        self.w = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=1, preprocessor=normalize_text)
        self.c = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, min_df=2, preprocessor=normalize_text)

    def fit_transform(self, X):
        return hstack([self.w.fit_transform(X), self.c.fit_transform(X)]).tocsr()

    def transform(self, X):
        return hstack([self.w.transform(X), self.c.transform(X)]).tocsr()


class ModelNotReady(RuntimeError):
    pass


def segment(text: str, min_len: int = 25, max_len: int = 700) -> List[str]:
    """Split a policy into clause-sized chunks."""
    out: List[str] = []
    for part in _SPLIT.split(text or ""):
        part = " ".join(part.split())
        if len(part) < min_len:
            continue
        out.append(part[:max_len])
    return out


_bundle = None


def _load():
    global _bundle
    if _bundle is None:
        if not ARTIFACT.exists():
            raise ModelNotReady(f"No trained model at {ARTIFACT}. Run training first.")
        _bundle = joblib.load(ARTIFACT)
    return _bundle


def model_version() -> str:
    return str(_load().get("version", "unknown"))


def covered_labels() -> set:
    """Labels the model has enough real training data for (others use legacy fallback)."""
    return set(_load().get("reliable", []))


def predict_clauses(text: str) -> List[Dict]:
    """Return [{clause, scores:{label:prob}, labels:[...]}] for every clause."""
    b = _load()
    clauses = segment(text)
    if not clauses:
        return []
    X = b["vectorizer"].transform(clauses)
    results = [{"clause": c, "scores": {}, "labels": []} for c in clauses]
    rel = set(b.get("reliable", []))
    for label, clf in b["classifiers"].items():
        if label not in rel:
            continue
        probs = clf.predict_proba(X)[:, 1]
        thr = b["thresholds"].get(label, 0.5)
        for r, p in zip(results, probs):
            r["scores"][label] = float(p)
            if p >= thr:
                r["labels"].append(label)
    return results
