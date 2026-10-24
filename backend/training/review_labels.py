"""Find and fix wrong labels in your own labelled rows.

It trains on everything EXCEPT a fold of your rows, then asks the model about the rows it left out.
Rows where the model strongly disagrees with your label are shown first. You decide; the model never edits.

Usage (from backend/):
    PYTHONPATH="$(pwd)" python training/review_labels.py                    # worst 40 disagreements
    PYTHONPATH="$(pwd)" python training/review_labels.py --top 80
    PYTHONPATH="$(pwd)" python training/review_labels.py --label right_delete   # every row with that label
Keys: Enter = keep as is | numbers = set labels | 0 = no labels | d = delete row | q = save and quit
"""
import argparse
import contextlib
import io
import random
import shutil
import json
from pathlib import Path

import training.train_clause_model as T
from training.label_tool import menu, parse_choice

OWN = Path("ml_data/annotations/own_labeled.jsonl")


def suspicion(own, other):
    order = list(range(len(own)))
    random.Random(0).shuffle(order)
    out = [(0.0, "") for _ in own]
    for f in [order[i::5] for i in range(5)]:
        held = set(f)
        train_rows = other + [own[i] for i in order if i not in held]
        with contextlib.redirect_stdout(io.StringIO()):
            vec, clfs = T.fit(train_rows)
        X = vec.transform([own[i]["text"] for i in f])
        probs = {l: c.predict_proba(X)[:, 1] for l, c in clfs.items()}
        for k, i in enumerate(f):
            best = (0.0, "")
            for l, p in probs.items():
                has = l in own[i]["labels"]
                s = (1 - p[k]) if has else p[k]
                if s > best[0]:
                    best = (s, f"you have '{l}', model says {p[k]:.2f}" if has else f"you lack '{l}', model says {p[k]:.2f}")
            out[i] = best
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    own = [json.loads(l) for l in OWN.read_text(encoding="utf-8").splitlines() if l.strip()]
    other = [r for r in T.load_rows(set()) if r["source"] == "policyie"]
    print(f"{len(own)} own rows; scoring disagreements (a minute or so)...")
    sus = suspicion(own, other)
    idx = [i for i in range(len(own)) if not a.label or a.label in own[i]["labels"]]
    idx.sort(key=lambda i: -sus[i][0])
    if not a.label:
        idx = idx[: a.top]
    shutil.copy(OWN, str(OWN) + ".bak")
    print(f"backup saved to {OWN}.bak\n")
    menu()
    deleted, changed = set(), 0
    for n, i in enumerate(idx, 1):
        r = own[i]
        print(f"\n[{n}/{len(idx)}]  ({r['source']})  {r['text']}")
        print(f"   labels now: {', '.join(r['labels']) or '(none)'}   | {sus[i][1] or 'model agrees'}")
        while True:
            ans = input("   > ").strip().lower()
            if ans == "":
                break
            if ans == "q":
                idx = []
                break
            if ans == "d":
                deleted.add(i)
                break
            labels = [] if ans == "0" else parse_choice(ans)
            if labels is None:
                print("   ? use numbers from the menu, 0, Enter, d or q")
                continue
            if labels != r["labels"]:
                r["labels"] = labels
                changed += 1
            break
        if not idx:
            break
    keep = [r for j, r in enumerate(own) if j not in deleted]
    OWN.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in keep) + "\n", encoding="utf-8")
    print(f"\nSaved. changed {changed} rows, deleted {len(deleted)}, {len(keep)} rows remain.")


if __name__ == "__main__":
    main()
