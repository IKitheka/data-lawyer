"""Train the Data Lawyer clause classifier on every dataset in ml_data/annotations/*.jsonl.

Row format: {"text": "...", "labels": [...], "scope": [...optional...], "source": "...",
             "license": "MIT|own|CC-BY-NC|...", "split": "train|test"}
  scope  = labels for which this row's NEGATIVES are trustworthy (partial-annotation datasets).
           Missing scope means the row is fully annotated for every label.

Usage (from backend/):
    PYTHONPATH="$(pwd)" python training/train_clause_model.py                      # all data
    PYTHONPATH="$(pwd)" python training/train_clause_model.py --licenses MIT,own   # commercial-safe model
    PYTHONPATH="$(pwd)" python training/train_clause_model.py --final              # also fit on the test split
"""
import argparse
import glob
import json
import random
import time
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import precision_recall_fscore_support

from backend.app.ml.taxonomy import LABELS
from backend.app.ml.clause_model import ARTIFACT, Vec

MIN_RELIABLE_POS = 30  # a label is only trusted by the API with this many real positive examples
THRESHOLDS = [0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.7]


def load_rows(allowed):
    rows = []
    for f in sorted(glob.glob("ml_data/annotations/*.jsonl")):
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            r.setdefault("license", "own")
            r.setdefault("source", Path(f).stem)
            r.setdefault("split", "train")
            r["labels"] = [l for l in r.get("labels", []) if l in LABELS]
            if allowed and r["license"] not in allowed:
                continue
            rows.append(r)
    return rows


def usable(row, label):
    """A row teaches `label` if it is positive, or if its negatives are trustworthy for that label."""
    return label in row["labels"] or "scope" not in row or label in row["scope"]


def fit(rows):
    vec = Vec()
    X = vec.fit_transform([r["text"] for r in rows])
    clfs = {}
    for label in LABELS:
        idx = [i for i, r in enumerate(rows) if usable(r, label)]
        y = np.array([1 if label in rows[i]["labels"] else 0 for i in idx])
        if y.sum() < 3 or (len(y) - y.sum()) < 3:
            print(f"  [skip] {label:24s} positives={int(y.sum())} (need >=3 pos and >=3 neg)")
            continue
        clf = LogisticRegression(C=10, class_weight="balanced", max_iter=3000)
        clf.fit(X[idx], y)
        clfs[label] = clf
    return vec, clfs


def scores(vec, clfs, rows, label):
    idx = [i for i, r in enumerate(rows) if usable(r, label)]
    if not idx:
        return None, None
    X = vec.transform([rows[i]["text"] for i in idx])
    y = np.array([1 if label in rows[i]["labels"] else 0 for i in idx])
    return clfs[label].predict_proba(X)[:, 1], y


def prf(y, p, thr):
    a, b, c, _ = precision_recall_fscore_support(y, (p >= thr).astype(int), average="binary", zero_division=0)
    return a, b, c


STATUS_FILE = Path("ml_data/label_status.json")


def reliable_labels(clfs, rows):
    """A label is trusted by the API with >= MIN_RELIABLE_POS real examples, or when label_status.json
    records measured evidence that the model beats the old keywords for it (use_model)."""
    status = json.loads(STATUS_FILE.read_text(encoding="utf-8")) if STATUS_FILE.exists() else {}
    force_model, force_kw = set(status.get("use_model", [])), set(status.get("use_keywords", []))
    out = []
    for l in clfs:
        n = sum(1 for r in rows if l in r["labels"] and r["source"] != "seed_v0")
        if l in force_kw:
            continue
        if n >= MIN_RELIABLE_POS or l in force_model:
            out.append(l)
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--licenses", default="", help="comma list of allowed licenses (default: all)")
    ap.add_argument("--final", action="store_true", help="also train on the test split (no honest metrics after)")
    a = ap.parse_args()
    allowed = {x.strip() for x in a.licenses.split(",") if x.strip()}

    rows = load_rows(allowed)
    if len(rows) < 20:
        raise SystemExit("Need at least 20 labelled clauses.")
    by_src = {}
    for r in rows:
        by_src.setdefault((r["source"], r["license"]), 0)
        by_src[(r["source"], r["license"])] += 1
    print(f"Loaded {len(rows)} rows" + (f" (licenses: {sorted(allowed)})" if allowed else ""))
    for (s, l), n in sorted(by_src.items()):
        print(f"  {s:16s} license={l:10s} rows={n}")

    test = [r for r in rows if r["split"] == "test"]
    pool = [r for r in rows if r["split"] != "test"]
    thresholds = {}

    if len(test) >= 50 and not a.final:
        rnd = random.Random(42)
        rnd.shuffle(pool)
        k = max(20, int(0.15 * len(pool)))
        val, tr = pool[:k], pool[k:]
        vec, clfs = fit(tr)
        for label in clfs:  # tune each threshold on validation only
            p, y = scores(vec, clfs, val, label)
            best = max(THRESHOLDS, key=lambda t: prf(y, p, t)[2]) if p is not None and y.sum() else 0.5
            thresholds[label] = best
        print(f"\nHeld-out TEST evaluation ({len(test)} clauses; thresholds tuned on validation only):")
        print(f"{'label':26s} {'thr':>4s} {'prec':>5s} {'rec':>5s} {'f1':>5s} {'test_pos':>8s}")
        f1s = []
        for label, clf in clfs.items():
            p, y = scores(vec, clfs, test, label)
            if p is None or y.sum() == 0 or y.sum() == len(y):
                print(f"{label:26s}  (cannot evaluate: test set lacks positives or negatives for this label)")
                continue
            pr, rc, f1 = prf(y, p, thresholds[label])
            f1s.append(f1)
            weak = "*" if any(label in r.get("weak_scope", []) for r in tr) else " "
            print(f"{label + weak:26s} {thresholds[label]:4.2f} {pr:5.2f} {rc:5.2f} {f1:5.2f} {int(y.sum()):8d}")
        if f1s:
            print("(* = weak negatives: treat score as optimistic)")
            print(f"\nMacro-F1 over {len(f1s)} evaluable labels: {np.mean(f1s):.3f}")
        final_rows = pool  # train + validation, test stays untouched
    else:
        print("\nNo usable held-out test split: metrics skipped (add a dataset with a 'test' split).")
        final_rows = pool + test if a.final else rows

    vec, clfs = fit(final_rows)
    for label in clfs:
        thresholds.setdefault(label, 0.4)
    th_file = Path("ml_data/label_thresholds.json")
    if th_file.exists():  # thresholds measured on unseen policies beat ones tuned on a small validation split
        override = {l: t for l, t in json.loads(th_file.read_text(encoding="utf-8")).items() if l in clfs}
        thresholds.update(override)
        print(f"Applied measured thresholds from {th_file} to {len(override)} labels")
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "version": f"clause-tfidf-lr-{time.strftime('%Y%m%d')}{'-' + '_'.join(sorted(allowed)) if allowed else ''}",
            "vectorizer": vec,
            "classifiers": clfs,
            "thresholds": thresholds,
            "trained_on": len(final_rows),
            "reliable": reliable_labels(clfs, final_rows),
            "licenses": sorted(allowed) or "all",
        },
        ARTIFACT,
    )
    rel = joblib.load(ARTIFACT)["reliable"]
    print(f"Reliable labels used by the API ({len(rel)}): {', '.join(rel)}")
    print(f"Falling back to legacy keywords for: {', '.join(l for l in LABELS if l not in rel)}")
    print(f"\nSaved model -> {ARTIFACT}  ({len(clfs)}/{len(LABELS)} labels trained, {len(final_rows)} rows)")


if __name__ == "__main__":
    main()
