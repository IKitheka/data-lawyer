"""Does the trained model beat the old hard-coded keyword checks? Per label, on held-out TEST rows only.

Usage (from backend/):
    PYTHONPATH="$(pwd)" python training/compare_with_legacy.py
"""
import joblib
import numpy as np

import training.train_clause_model as T
from backend.app.api.policies import _extract_contacts, _heuristic_analyze
from backend.app.ml.analyzer import _legacy_lookup
from backend.app.ml.clause_model import ARTIFACT

LEGACY_LABELS = ["right_access", "right_delete", "right_correct", "right_object", "right_portability",
                 "withdraw_consent", "opt_out", "service_provider", "sale", "intl_transfer",
                 "transfer_safeguards", "supervisory_authority", "contact",
                 "arbitration_waiver", "policy_change_no_notice"]


def main():
    b = joblib.load(ARTIFACT)
    rows = [r for r in T.load_rows(set()) if r["split"] == "test"]
    X = b["vectorizer"].transform([r["text"] for r in rows])
    legacy = []
    for r in rows:
        st = _heuristic_analyze("https://example.com/p", r["text"])["structured"]
        legacy.append((st, _extract_contacts(r["text"]), r["text"].lower()))

    print(f"{len(rows)} held-out test rows\n")
    print(f"{'label':26s} {'n_pos':>5s} | {'keywords F1':>11s} | {'model F1':>8s} | verdict")
    for label in LEGACY_LABELS:
        idx = [i for i, r in enumerate(rows) if T.usable(r, label)]
        y = np.array([1 if label in rows[i]["labels"] else 0 for i in idx])
        n = int(y.sum())
        if not idx or n == 0:
            print(f"{label:26s} {n:5d} | {'-':>11s} | {'-':>8s} | no test examples yet")
            continue
        kw = np.array([int(_legacy_lookup(label, *[legacy[i][0], legacy[i][1], legacy[i][2]])) for i in idx])
        f_kw = T.prf(y, kw.astype(float), 0.5)[2]
        if label in b["classifiers"]:
            p = b["classifiers"][label].predict_proba(X[idx])[:, 1]
            f_ml = T.prf(y, p, b["thresholds"][label])[2]
            ml_txt = f"{f_ml:8.2f}"
        else:
            f_ml, ml_txt = -1, f"{'(none)':>8s}"
        if n < 5:
            verdict = "too few examples to judge"
        elif f_ml < 0:
            verdict = "model not trained for this label"
        elif f_ml > f_kw + 0.05:
            verdict = "model better"
        elif f_kw > f_ml + 0.05:
            verdict = "KEYWORDS better"
        else:
            verdict = "about equal"
        print(f"{label:26s} {n:5d} | {f_kw:11.2f} | {ml_txt} | {verdict}")


if __name__ == "__main__":
    main()
