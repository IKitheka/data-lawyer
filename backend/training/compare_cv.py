"""Model vs the old keyword checks on ALL your own labelled rows, using 5-fold cross-validation.

Every row of yours gets predicted by a model that never saw it (trained on PolicyIE + the other 4/5 of
your rows), so far more sentences count than in the small fixed test split. The keywords need no training,
so they are scored on the same rows. The model uses a fixed 0.40 threshold here.

Usage (from backend/):
    PYTHONPATH="$(pwd)" python training/compare_cv.py
"""
import contextlib
import io
import random
import sys

import numpy as np

import training.train_clause_model as T
from backend.app.api.policies import _extract_contacts, _heuristic_analyze
from backend.app.ml.analyzer import _legacy_lookup

LABELS = ["right_access", "right_delete", "right_correct", "right_object", "right_portability",
          "withdraw_consent", "opt_out", "service_provider", "sale", "intl_transfer",
          "transfer_safeguards", "supervisory_authority", "contact",
          "arbitration_waiver", "policy_change_no_notice", "retention_period", "retention_vague"]
THR = 0.40
MIN_N = 15


def prf(y, pred):
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def main():
    rows = T.load_rows(set())
    own = [r for r in rows if r["source"] != "policyie" and "scope" not in r]
    other = [r for r in rows if r["source"] == "policyie"]
    by_policy = "--by-policy" in sys.argv
    mode = "leave-one-policy-out (each policy predicted by a model that never saw ANY of its sentences)" if by_policy \
        else "5-fold cross-validation (sentences from the same policy can appear in both train and test)"
    print(f"{len(own)} own rows, scored by {mode}...\n")

    order = list(range(len(own)))
    random.Random(0).shuffle(order)
    probs = {}
    if by_policy:
        srcs = sorted({r["source"] for r in own})
        folds = [[i for i, r in enumerate(own) if r["source"] == sr] for sr in srcs]
    else:
        folds = [order[i::5] for i in range(5)]
    for f in folds:
        held = set(f)
        with contextlib.redirect_stdout(io.StringIO()):
            vec, clfs = T.fit(other + [own[i] for i in order if i not in held])
        X = vec.transform([own[i]["text"] for i in f])
        for l, c in clfs.items():
            p = c.predict_proba(X)[:, 1]
            for k, i in enumerate(f):
                probs[(l, i)] = p[k]

    legacy = []
    for r in own:
        st = _heuristic_analyze("https://example.com/p", r["text"])["structured"]
        legacy.append((st, _extract_contacts(r["text"]), r["text"].lower()))

    print(f"{'label':26s} {'n_pos':>5s} | {'keywords':>8s} | {'model':>5s} {'prec':>5s} {'rec':>5s} | verdict")
    for label in LABELS:
        y = np.array([1 if label in r["labels"] else 0 for r in own])
        n = int(y.sum())
        if n == 0:
            print(f"{label:26s} {n:5d} | {'-':>8s} | {'-':>5s} {'-':>5s} {'-':>5s} | no examples yet")
            continue
        kw = np.array([int(_legacy_lookup(label, *legacy[i])) for i in range(len(own))])
        f_kw = prf(y, kw)[2]
        covered = sum((label, i) in probs for i in range(len(own)))
        if covered == 0:
            print(f"{label:26s} {n:5d} | {f_kw:8.2f} | {'-':>5s} {'-':>5s} {'-':>5s} | model not trained (too few examples)")
            continue
        # a fold with too few examples for this label trains no classifier: count it as "predict negative"
        pm = np.array([1 if probs.get((label, i), 0.0) >= THR else 0 for i in range(len(own))])
        p, r, f_ml = prf(y, pm)
        if n < MIN_N:
            verdict = f"too few (n<{MIN_N})"
        elif f_ml > f_kw + 0.10:
            verdict = "model better"
        elif f_kw > f_ml + 0.10:
            verdict = "KEYWORDS better"
        else:
            verdict = "about equal"
        if covered < len(own):
            verdict += " (some folds had too few examples to train)"
        print(f"{label:26s} {n:5d} | {f_kw:8.2f} | {f_ml:5.2f} {p:5.2f} {r:5.2f} | {verdict}")


if __name__ == "__main__":
    main()
