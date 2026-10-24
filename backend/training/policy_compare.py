"""Whole-policy comparison: new ML analyzer vs the old keyword analyzer, on complete policy texts.

Part 1  risk score and level per policy (production model vs old keyword analyzer).
Part 2  per label: does each detector find the practice anywhere in the policy? Scored against your own
        labels. The model is evaluated leave-one-policy-out (never trained on the policy it is scored on).
        Your labels cover only the sentences you labelled, so "extra" detections are UNVERIFIED, not
        necessarily wrong: use --show-extras LABEL to read them.

Setup: ml_data/policy_sources.txt, one policy per line:   Source name | URL or file path
       (the name must match the --source you used when labelling).
Usage (from backend/):
    PYTHONPATH="$(pwd)" python training/policy_compare.py
    PYTHONPATH="$(pwd)" python training/policy_compare.py --show-extras right_access
"""
import contextlib
import io
import json
import os
import re
import sys
from pathlib import Path

import numpy as np

import training.train_clause_model as T
from backend.app.api.policies import _detect_jurisdiction, _domain_from_url, _extract_contacts, _heuristic_analyze
from backend.app.ml.analyzer import _legacy_lookup, ml_analyze
from backend.app.ml.clause_model import segment
from training.label_tool import html_to_text

SOURCES = Path("ml_data/policy_sources.txt")
CACHE = Path("ml_data/policy_cache")
TH_FILE = Path("ml_data/label_thresholds.json")
LABELS = ["right_access", "right_delete", "right_correct", "right_object", "right_portability",
          "withdraw_consent", "opt_out", "service_provider", "sale", "intl_transfer",
          "transfer_safeguards", "supervisory_authority", "contact", "arbitration_waiver",
          "policy_change_no_notice", "retention_period", "retention_vague"]


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def load_text(name, loc):
    CACHE.mkdir(parents=True, exist_ok=True)
    cp = CACHE / (slug(name) + ".txt")
    if cp.exists():
        return cp.read_text(encoding="utf-8")
    if loc.startswith("http"):
        import requests
        r = requests.get(loc, timeout=25, headers={"User-Agent": "DataLawyer-labeller/1.0"})
        r.raise_for_status()
        text = html_to_text(r.text)
    else:
        text = Path(os.path.expanduser(loc)).read_text(encoding="utf-8", errors="replace")
    cp.write_text(text, encoding="utf-8")
    return text


def main():
    show = sys.argv[sys.argv.index("--show-extras") + 1].split(",") if "--show-extras" in sys.argv else []
    pols = []
    for line in SOURCES.read_text(encoding="utf-8").splitlines():
        if "|" in line and not line.strip().startswith("#"):
            n, loc = [x.strip() for x in line.split("|", 1)]
            try:
                pols.append((n, loc, load_text(n, loc)))
            except Exception as e:  # keep going if one page fails
                print(f"  [skipped] {n}: {e}")
    rows = T.load_rows(set())
    own = [r for r in rows if r["source"] != "policyie" and "scope" not in r]
    other = [r for r in rows if r["source"] == "policyie"]
    thr = json.loads(TH_FILE.read_text()) if TH_FILE.exists() else {}

    print(f"\n=== Part 1: risk score, old keyword analyzer vs new model ({len(pols)} policies) ===")
    print(f"{'policy':38s} {'clauses':>7s} | {'old':>9s} | {'new':>9s} | change")
    old_res = {}
    flagdiff = []
    for n, loc, text in pols:
        url = loc if loc.startswith("http") else "https://example.com/policy"
        old = _heuristic_analyze(url, text)
        new = ml_analyze(url=url, text=text, domain=_domain_from_url(url), jurisdiction=_detect_jurisdiction(text),
                         contacts=_extract_contacts(text), legacy=old["structured"])
        old_res[n] = (old, _extract_contacts(text))
        norm = lambda fl: {f.split(" Evidence:")[0][:75] for f in fl}  # noqa: E731
        flagdiff.append((n, new["risk_score"] - old["risk_score"],
                         sorted(norm(old["red_flags"]) - norm(new["red_flags"])),
                         sorted(norm(new["red_flags"]) - norm(old["red_flags"]))))
        d = new["risk_score"] - old["risk_score"]
        print(f"{n[:38]:38s} {new['structured']['clauses_analyzed']:7d} | {old['risk_score']:3d} {old['risk_level']:>5s} | "
              f"{new['risk_score']:3d} {new['risk_level']:>5s} | {d:+d}")

    from collections import Counter
    oc, nc = Counter(), Counter()
    for _, _, oo, nn in flagdiff:
        oc.update(oo)
        nc.update(nn)
    print("\nRed flags raised ONLY by the old analyzer (number of policies):")
    for f, c in oc.most_common():
        print(f"  {c:2d}  {f}")
    print("Red flags raised ONLY by the new analyzer:")
    for f, c in nc.most_common():
        print(f"  {c:2d}  {f}")
    for n, d, oo, nn in flagdiff:
        if abs(d) >= 15:
            print(f"\nLarge change, {n[:40]} ({d:+d}):")
            for f in oo:
                print(f"    only OLD: {f}")
            for f in nn:
                print(f"    only NEW: {f}")

    print("\n=== Part 2: does each detector find the practice anywhere in the policy? (model: leave-one-policy-out) ===")
    stats = {l: dict(gold=0, mk=0, kk=0, mx=0, kx=0) for l in LABELS}
    extras = {l: [] for l in LABELS}
    for n, loc, text in pols:
        mine = [r for r in own if r["source"] == n]
        if not mine:
            continue
        gold = {l for r in mine for l in r["labels"]}
        with contextlib.redirect_stdout(io.StringIO()):
            vec, clfs = T.fit(other + [r for r in own if r["source"] != n])
        sents = segment(text)
        X = vec.transform(sents)
        old, contacts = old_res[n]
        tl = text.lower()
        for l in LABELS:
            fired, best = False, (0.0, "")
            if l in clfs:
                p = clfs[l].predict_proba(X)[:, 1]
                t = thr.get(l, 0.40)
                fired = bool((p >= t).any())
                i = int(p.argmax())
                best = (float(p[i]), sents[i])
            kw = bool(_legacy_lookup(l, old["structured"], contacts, tl))
            s = stats[l]
            if l in gold:
                s["gold"] += 1
                s["mk"] += fired
                s["kk"] += kw
            else:
                s["mx"] += fired
                s["kx"] += kw
                if fired:
                    extras[l].append((n, best[0], best[1]))
    print(f"{'label':26s} {'policies with it':>16s} | {'model found':>11s} {'keywords found':>14s} | {'model extra':>11s} {'kw extra':>8s}")
    for l in LABELS:
        s = stats[l]
        if s["gold"] == 0 and s["mx"] == 0 and s["kx"] == 0:
            continue
        print(f"{l:26s} {s['gold']:16d} | {s['mk']:11d} {s['kk']:14d} | {s['mx']:11d} {s['kx']:8d}")
    print("(extra = detected in a policy where you did not label it: unverified, see --show-extras LABEL)")
    for lab in show:
        print(f"\n=== model detections of '{lab}' not backed by your labels ===")
        for n, p, sent in sorted(extras.get(lab, []), key=lambda x: -x[1])[:6]:
            print(f"- {n[:34]} ({p:.2f}): {sent[:200]}")


if __name__ == "__main__":
    main()
