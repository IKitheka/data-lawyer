"""Batch labelling: export a numbered batch of sentences to paste into a chat, then import the answers.

Usage (from backend/):
    export:  python training/batch_label.py export --url URL --source "Name Privacy Policy 2026-10" --limit 25
             python training/batch_label.py export --file policy.txt --source "Name 2026-10" --limit 25
             (optional: --focus gaps|rights|transfers|complaints|notices|sale   --no-auto-skip)
    import:  python training/batch_label.py import --answers batch_answers.txt

Answers file: one line per sentence, "number: labels", for example
    3: 10 11        (label numbers or names)
    4: 0            (no label applies)
    5: s            (skip, not saved and not asked again)
Lines you leave out are not saved and may be offered again later.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from backend.app.ml.clause_model import ModelNotReady, predict_clauses
from backend.app.ml.taxonomy import LABELS
from training.label_tool import FOCUS, OUT, html_to_text, looks_like_fragment

STATE = Path("ml_data/batch_state.json")
BATCH_DIR = Path("ml_data/batches")
SKIPPED = Path("ml_data/skipped_texts.txt")


def source_slug(source):
    return re.sub(r"[^a-z0-9]+", "-", source.lower()).strip("-")


def batch_path(source):
    return BATCH_DIR / f"{source_slug(source)}.json"


def read_lines(path: Path):
    return {l.strip().lower() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()} if path.exists() else set()


def done_texts():
    out = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.add(json.loads(line)["text"].lower())
    return out | read_lines(SKIPPED)


def do_export(a):
    if a.url:
        import requests
        try:
            r = requests.get(a.url, timeout=20, headers={"User-Agent": "DataLawyer-labeller/1.0"})
            r.raise_for_status()
        except requests.RequestException as e:
            sys.exit(f"Could not fetch {a.url}\n  {e}\nCheck the URL, or save the policy text to a file and use --file.")
        text = html_to_text(r.text)
        if len(text) < 500:
            sys.exit(f"Only {len(text)} characters found (page may be JavaScript-rendered). Use --file instead.")
    else:
        text = Path(a.file).read_text(encoding="utf-8", errors="replace")
    try:
        preds = predict_clauses(text)
    except ModelNotReady:
        sys.exit("Train a model first: python training/train_clause_model.py")

    seen, items = done_texts(), []
    rx = re.compile(FOCUS.get(a.focus, a.focus), re.I) if a.focus else None
    for p in preds:
        t, k = p["clause"], p["clause"].lower()
        if k in seen:
            continue
        seen.add(k)
        if rx and not rx.search(t):
            continue
        if not a.no_auto_skip and looks_like_fragment(t):
            continue
        items.append({"n": len(items) + 1, "text": t, "model": p["labels"]})
        if len(items) >= a.limit:
            break
    if not items:
        sys.exit("Nothing new to label from this policy with these filters.")
    state = {"source": a.source, "items": items}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2)
    STATE.write_text(payload, encoding="utf-8")
    batch_path(a.source).write_text(payload, encoding="utf-8")
    print(f"=== {len(items)} sentences from: {a.source} (copy everything from here to the end line) ===")
    for it in items:
        print(f"{it['n']}. {it['text']}   [model: {', '.join(it['model']) or 'none'}]")
    print("=== end of batch ===")


def parse_labels(s: str):
    s = s.strip().lower()
    if s in ("s", "skip"):
        return "SKIP"
    if s == "0":
        return []
    out = []
    for tok in re.split(r"[,\s]+", s):
        if tok.isdigit() and 1 <= int(tok) <= len(LABELS):
            out.append(LABELS[int(tok) - 1])
        elif tok in LABELS:
            out.append(tok)
        else:
            return None
    return sorted(set(out)) if out else None


def do_import(a):
    saved_batch = batch_path(a.source)
    if not saved_batch.exists():
        sys.exit(
            f"No saved batch found for source: {a.source!r}. "
            "Export that policy first, then verify its numbered sentences."
        )
    st = json.loads(saved_batch.read_text(encoding="utf-8"))
    if st.get("source") != a.source:
        sys.exit(
            f"Source mismatch: requested {a.source!r}, "
            f"but saved batch is for {st.get('source')!r}."
        )
    by_n = {it["n"]: it["text"] for it in st["items"]}
    have = done_texts()
    saved = skipped = 0
    errors = []
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for raw in Path(a.answers).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*(\d+)\s*[:.)\-]\s*(.+?)\s*$", raw)
        if not m:
            continue
        n, ans = int(m.group(1)), m.group(2)
        if n not in by_n:
            errors.append(f"line '{raw.strip()}': no sentence number {n} in this batch")
            continue
        labels = parse_labels(ans)
        if labels is None:
            errors.append(f"line '{raw.strip()}': labels not understood")
            continue
        text = by_n[n]
        if labels == "SKIP":
            with SKIPPED.open("a", encoding="utf-8") as f:
                f.write(text.lower() + "\n")
            skipped += 1
            continue
        if text.lower() in have:
            continue
        split = "test" if int(hashlib.md5(text.lower().encode()).hexdigest(), 16) % 5 == 0 else "train"
        row = {"text": text, "labels": labels, "source": st["source"], "license": "own", "split": split}
        with OUT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        have.add(text.lower())
        saved += 1
    print(f"saved {saved} rows, skipped {skipped}, {len(by_n) - saved - skipped} sentences left unanswered")
    for e in errors:
        print("  problem:", e)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--url")
    e.add_argument("--file")
    e.add_argument("--source", required=True)
    e.add_argument("--limit", type=int, default=25)
    e.add_argument("--focus", default="gaps")
    e.add_argument("--no-auto-skip", action="store_true")
    i = sub.add_parser("import")
    i.add_argument("--answers", required=True)
    i.add_argument("--source", required=True)
    a = ap.parse_args()
    if a.cmd == "export":
        if not (a.url or a.file):
            sys.exit("Give --url or --file")
        do_export(a)
    else:
        do_import(a)


if __name__ == "__main__":
    main()
