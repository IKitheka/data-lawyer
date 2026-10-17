"""Terminal labelling tool: the model pre-labels a real privacy policy, you confirm or correct.

Every row you save is FULLY annotated (you decide all labels), so it teaches the model every label,
including the ones it cannot detect yet (rights, transfers, complaints...). Source/provenance is stored.

Usage (from backend/):
    PYTHONPATH="$(pwd)" python training/label_tool.py --file policy.txt --source "Example Co privacy policy"
    PYTHONPATH="$(pwd)" python training/label_tool.py --url https://example.com/privacy --source "Example Co"
Keys:  Enter = accept suggestion | numbers (e.g. 8 15) = set labels | 0 = no labels
       s = skip | ? = label menu | u = undo last | q = quit (progress is saved after every row)
"""
import argparse
import hashlib
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

from backend.app.ml.clause_model import ModelNotReady, predict_clauses, segment
from backend.app.ml.taxonomy import LABELS

OUT = Path("ml_data/annotations/own_labeled.jsonl")
BLOCK = {"p", "div", "li", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "ul", "ol"}

# --focus only chooses WHICH sentences to show you (a sampling aid). It never decides the label; you do.
FOCUS = {
    "rights": r"\bright\b|\brights\b|\baccess\b|rectif|\bcorrect|\berase|\bdelet|\bobject|portab|restrict|withdraw|opt[- ]?out|unsubscribe|\brequest",
    "transfers": r"transfer|outside|abroad|overseas|cross-border|international|third countr|standard contractual|adequacy|safeguard",
    "complaints": r"complain|commissioner|supervisory|regulator|authorit|data protection officer|\bDPO\b|contact us|e-?mail us|write to us",
    "notices": r"without notice|change this|update this|amend|arbitration|class action|jury|waive",
    "sale": r"\bsell|\bsold\b|\bsale\b|for value|valuable consideration",
}
FOCUS["gaps"] = "|".join(FOCUS.values())


_NUM = re.compile(r"^\s*(?:\(?[A-Za-z0-9]{1,3}[.)]\s+|\d+(?:\.\d+)*\.?\s+)+")


def looks_like_fragment(t: str) -> bool:
    """Mechanical skip rules: question headings, very short lines, and lines that start mid-sentence."""
    t = t.strip()
    if t.endswith("?") or len(t.split()) < 4:
        return True
    body = _NUM.sub("", t)
    return bool(body) and body[0].isalpha() and body[0].islower()


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1
        if tag in BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self.skip:
            self.skip -= 1
        if tag in BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    return re.sub(r"\n\s*\n+", "\n", "".join(p.out))


def menu():
    cols = [f"{i + 1:2d} {l}" for i, l in enumerate(LABELS)]
    for i in range(0, len(cols), 3):
        print("   " + "".join(f"{c:30s}" for c in cols[i:i + 3]))


def parse_choice(s: str):
    ids = []
    for tok in re.split(r"[,\s]+", s.strip()):
        if tok.isdigit() and 1 <= int(tok) <= len(LABELS):
            ids.append(LABELS[int(tok) - 1])
        elif tok in LABELS:
            ids.append(tok)
        else:
            return None
    return sorted(set(ids))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file")
    ap.add_argument("--url")
    ap.add_argument("--source", required=True, help="e.g. 'Safaricom privacy policy 2026-10'")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many labelled rows")
    ap.add_argument("--focus", default="", help="show only sentences matching: rights|transfers|complaints|notices|sale|gaps or your own regex")
    ap.add_argument("--auto-skip", action="store_true", help="hide question headings, tiny lines and mid-sentence fragments")
    a = ap.parse_args()
    if not (a.file or a.url):
        sys.exit("Give --file or --url")

    if a.url:
        import requests
        try:
            r = requests.get(a.url, timeout=20, headers={"User-Agent": "DataLawyer-labeller/1.0"})
            r.raise_for_status()
        except requests.RequestException as e:
            sys.exit(f"Could not fetch {a.url}\n  {e}\nCheck the URL is a real page, or save the policy text to a file and use --file.")
        text = html_to_text(r.text)
        if len(text) < 500:
            sys.exit(f"Only {len(text)} characters of text found at {a.url} (page may be JavaScript-rendered).\nCopy the policy text into a file and use --file instead.")
    else:
        text = Path(a.file).read_text(encoding="utf-8", errors="replace")

    try:
        preds = predict_clauses(text)
    except ModelNotReady:
        sys.exit("Train a model first: python training/train_clause_model.py")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["text"].lower())
    todo = [p for p in preds if p["clause"].lower() not in done]
    seen_here, unique = set(), []
    for p in todo:  # the same sentence often appears several times in one policy: show it once
        k = p["clause"].lower()
        if k not in seen_here:
            seen_here.add(k)
            unique.append(p)
    todo = unique
    seen_here, unique = set(), []
    for p in todo:  # the same sentence often appears several times in one policy: show it once
        k = p["clause"].lower()
        if k not in seen_here:
            seen_here.add(k)
            unique.append(p)
    todo = unique
    print(f"{len(preds)} clauses found, {len(preds) - len(todo)} already labelled, {len(todo)} to go.\n")
    if a.focus:
        rx = re.compile(FOCUS.get(a.focus, a.focus), re.I)
        before = len(todo)
        todo = [p for p in todo if rx.search(p["clause"])]
        print(f"focus '{a.focus}': {len(todo)} of {before} unlabelled clauses match.\n")
    if a.auto_skip:
        before = len(todo)
        todo = [p for p in todo if not looks_like_fragment(p["clause"])]
        print(f"auto-skip: hid {before - len(todo)} headings/fragments, {len(todo)} left to label.\n")
    menu()
    print()

    saved = []
    last_labels = None
    for n, p in enumerate(todo, 1):
        if a.limit and len(saved) >= a.limit:
            break
        print(f"\n[{n}/{len(todo)}]  {p['clause']}")
        print(f"   suggested: {', '.join(p['labels']) or '(none)'}")
        while True:
            ans = input("   > ").strip().lower()
            if ans == "?":
                menu()
            elif ans == "q":
                print(f"Saved {len(saved)} new rows to {OUT}")
                return
            elif ans == "s":
                break
            elif ans == "u":
                if saved:
                    saved.pop()
                    lines = OUT.read_text(encoding="utf-8").splitlines()[:-1]
                    OUT.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
                    print("   (undid last row)")
                continue
            else:
                if ans == "=" and last_labels is not None:
                    labels = last_labels
                else:
                    labels = p["labels"] if ans == "" else ([] if ans == "0" else parse_choice(ans))
                if labels is None:
                    print("   ? not understood: use numbers from the menu, 0, Enter, s, u or q")
                    continue
                split = "test" if int(hashlib.md5(p["clause"].lower().encode()).hexdigest(), 16) % 5 == 0 else "train"
                row = {"text": p["clause"], "labels": labels, "source": a.source, "license": "own", "split": split}
                with OUT.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                saved.append(row)
                last_labels = labels
                break
    print(f"\nDone. Saved {len(saved)} new rows to {OUT}")


if __name__ == "__main__":
    main()
