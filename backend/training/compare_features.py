"""Compare feature sets for the clause classifier on (a) PolicyIE's held-out test split and
(b) the hand-written probe set in ml_data/probe/ (never used for training).

Usage (from backend/):
    PYTHONPATH="$(pwd)" python training/compare_features.py --encoder none     # TF-IDF only
    PYTHONPATH="$(pwd)" python training/compare_features.py --encoder spacy    # + spaCy word vectors
    PYTHONPATH="$(pwd)" python training/compare_features.py --encoder minilm   # + MiniLM sentence embeddings
"""
import argparse
import glob
import json
import random

import numpy as np
from scipy.sparse import csr_matrix, hstack
from sklearn.linear_model import LogisticRegression

import training.train_clause_model as T
from backend.app.ml.clause_model import Vec

LAB = ["collection", "purpose", "share_third_party", "advertising", "tracking",
       "retention_period", "retention_vague", "security"]


def get_encoder(name):
    if name == "spacy":
        import spacy
        nlp = spacy.load("en_core_web_md", disable=["tagger", "parser", "ner", "attribute_ruler", "lemmatizer"])
        return lambda texts: np.vstack([nlp.make_doc(t).vector for t in texts])
    if name == "minilm":
        from sentence_transformers import SentenceTransformer
        m = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        return lambda texts: m.encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
    return None


class Feat:
    def __init__(self, enc, alpha):
        self.enc, self.alpha, self.v = enc, alpha, Vec()

    def fit(self, rows):
        t = [r["text"] for r in rows]
        parts = [self.v.fit_transform(t)]
        if self.enc:
            W = self.enc(t)
            self.mu, self.sd = W.mean(0), W.std(0) + 1e-6
            parts.append(csr_matrix((W - self.mu) / self.sd * self.alpha * 0.1))
        return hstack(parts).tocsr()

    def tf(self, rows):
        t = [r["text"] for r in rows]
        parts = [self.v.transform(t)]
        if self.enc:
            parts.append(csr_matrix((self.enc(t) - self.mu) / self.sd * self.alpha * 0.1))
        return hstack(parts).tocsr()


def run(enc, alpha, tr, val, evalsets):
    f = Feat(enc, alpha)
    X = f.fit(tr)
    clfs = {}
    for l in LAB:
        idx = [i for i, r in enumerate(tr) if T.usable(r, l)]
        y = np.array([l in tr[i]["labels"] for i in idx]).astype(int)
        clfs[l] = LogisticRegression(C=10, class_weight="balanced", max_iter=3000).fit(X[idx], y)

    def sc(rs, l):
        idx = [i for i, r in enumerate(rs) if T.usable(r, l)]
        if not idx:
            return None, None
        p = clfs[l].predict_proba(f.tf([rs[i] for i in idx]))[:, 1]
        return p, np.array([l in rs[i]["labels"] for i in idx]).astype(int)

    thr = {}
    for l in LAB:  # thresholds tuned on validation only
        p, y = sc(val, l)
        thr[l] = max(T.THRESHOLDS, key=lambda t: T.prf(y, p, t)[2])
    out = {}
    for name, rs in evalsets.items():
        f1s, TP, FP, FN = [], 0, 0, 0
        for l in LAB:
            p, y = sc(rs, l)
            if p is None or y.sum() == 0 or y.sum() == len(y):
                continue
            pred = (p >= thr[l]).astype(int)
            f1s.append(T.prf(y, p, thr[l])[2])
            TP += int(((pred == 1) & (y == 1)).sum())
            FP += int(((pred == 1) & (y == 0)).sum())
            FN += int(((pred == 0) & (y == 1)).sum())
        out[name] = (float(np.mean(f1s)), 2 * TP / (2 * TP + FP + FN) if TP else 0.0)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--encoder", default="none", choices=["none", "spacy", "minilm"])
    a = ap.parse_args()
    rows = T.load_rows(set())
    test = [r for r in rows if r["split"] == "test"]
    pool = [r for r in rows if r["split"] != "test"]
    random.Random(42).shuffle(pool)
    k = max(20, int(0.15 * len(pool)))
    val, tr = pool[:k], pool[k:]
    probe = []
    for f in glob.glob("ml_data/probe/*.jsonl"):
        probe += [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]
    evalsets = {"policyie_test": test, "probe": probe}
    enc = get_encoder(a.encoder)
    print(f"{'config':22s} {'PolicyIE macroF1':>17s} {'probe macroF1':>14s} {'probe microF1':>14s}")
    for alpha in ([1] if not enc else [1, 2, 4]):
        o = run(enc, alpha, tr, val, evalsets)
        name = "tfidf only" if not enc else f"tfidf + {a.encoder} x{alpha}"
        print(f"{name:22s} {o['policyie_test'][0]:17.3f} {o['probe'][0]:14.3f} {o['probe'][1]:14.3f}")


if __name__ == "__main__":
    main()
