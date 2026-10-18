"""Convert PolicyIE (MIT licensed) brat annotations into Data Lawyer clause rows.

Usage (from backend/):
    python training/import_policyie.py --src /path/to/PolicyIE/data/sanitized_split
Writes ml_data/annotations/policyie.jsonl
"""
import argparse
import glob
import json
import os
import re
from collections import Counter

EVENT_LABEL = {
    "first-party-collection-use": "collection",
    "third-party-sharing-disclosure": "share_third_party",
    "data-security-protection": "security",
}
PURPOSE_ENTITIES = {
    "Basic-service-feature", "Service-operation-and-security", "Analytics-research",
    "Personalization-customization", "Communications", "Legal-requirement",
    "Adverstising-Marketing", "Merge-acquisition", "Other",
}
# Labels whose NEGATIVES we trust because PolicyIE annotates these practices sentence by sentence.
SCOPE = ["collection", "share_third_party", "retention_period", "retention_vague", "security"]
# Labels derived from event arguments (purpose/ads/cookies): positives are good, negatives are noisier
# (e.g. "you can disable cookies" has no event, so it is labelled negative). Treated as weak.
WEAK_SCOPE = ["purpose", "advertising", "tracking"]

# PolicyIE tags ANY retention span as a "period", but ~70% are open-ended ("as long as needed", "a reasonable
# period of time", "indefinitely"). Our definition: retention_period = a definite duration or a clear criterion
# the user controls; everything else is retention_vague. We decide from the annotated span text itself.
CONCRETE = re.compile(
    r"\d|\b(one|two|three|four|five|six|seven|eight|nine|ten|twelve|thirty|sixty|ninety|a|an)\b[\s-]+(day|week|month|year)s?"
    r"|\buntil\b|\bimmediately\b|\bas long as (you|your)\b|account is active|\bdeletes? (their|your|the) (own )?account"
)


def parse_ann(path):
    ents, events = {}, []
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if line.startswith("T"):
            tid, meta, *tail = line.split("\t")
            typ, rest = meta.split(" ", 1)
            start = int(re.split(r"[ ;]", rest)[0])
            ents[tid] = (typ, start, tail[0].strip().lower() if tail else "")
        elif line.startswith("E"):
            _, body = line.split("\t", 1)
            parts = body.split()
            etype, trig = parts[0].split(":")
            args = [(a.split(":")[0], a.split(":")[1]) for a in parts[1:]]
            events.append((etype, trig, args))
    return ents, events


def parse_sentences(conll_path):
    sents, cur = [], None
    for line in open(conll_path, encoding="utf-8"):
        line = line.rstrip("\n")
        if line.startswith("# text = "):
            cur = {"text": line[9:], "start": None, "end": None}
            sents.append(cur)
        elif cur and line and not line.startswith("#"):
            m = re.search(r"TokenRange=(\d+):(\d+)", line)
            if m:
                s, e = int(m.group(1)), int(m.group(2))
                cur["start"] = s if cur["start"] is None else min(cur["start"], s)
                cur["end"] = e if cur["end"] is None else max(cur["end"], e)
    return [s for s in sents if s["start"] is not None]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", default="ml_data/annotations/policyie.jsonl")
    a = ap.parse_args()

    n_ann = len(glob.glob(os.path.join(a.src, "**", "*.ann"), recursive=True))
    if n_ann == 0:
        raise SystemExit(f"No .ann files found under {a.src}. Nothing was written (your existing data is untouched).\n"
                         "Check the path: it should contain the 'train' and 'test' folders.")

    stats, rows, seen = Counter(), [], set()
    for split in ("train", "test"):
        for ann in sorted(glob.glob(os.path.join(a.src, split, "**", "*.ann"), recursive=True)):
            conll = ann[:-4] + ".conll"
            if not os.path.exists(conll):
                continue
            ents, events = parse_ann(ann)
            for s in parse_sentences(conll):
                inside = lambda off: s["start"] <= off < s["end"]  # noqa: E731
                labels, negated = set(), False
                for etype, trig, args in events:
                    if trig not in ents or not inside(ents[trig][1]):
                        continue
                    argnames = [n for n, _ in args]
                    if "Polarity" in argnames:
                        negated = True
                        continue
                    if etype in EVENT_LABEL:
                        labels.add(EVENT_LABEL[etype])
                    elif etype == "data-storage-retention-deletion":
                        spans = [ents[t][2] for n, t in args if n.startswith("Retention-Period") and t in ents]
                        labels.add("retention_period" if any(CONCRETE.search(x) for x in spans) else "retention_vague")
                    if any(n.startswith("Purpose-Argument") for n in argnames):
                        labels.add("purpose")
                    for n, t in args:
                        if t in ents and ents[t][0] == "Adverstising-Marketing":
                            labels.add("advertising")
                        if t in ents and ents[t][0] == "Data-cookies-web-beacons-and-other-technologies":
                            labels.add("tracking")
                if negated and not labels:
                    stats["skipped_negated_only"] += 1
                    continue
                text = " ".join(s["text"].split())
                if len(text) < 25 or text.lower() in seen:
                    stats["skipped_short_or_dup"] += 1
                    continue
                seen.add(text.lower())
                rows.append({
                    "text": text, "labels": sorted(labels), "scope": SCOPE + WEAK_SCOPE, "weak_scope": WEAK_SCOPE,
                    "source": "policyie", "license": "MIT", "split": split,
                })
                stats[f"{split}_rows"] += 1
                for l in labels:
                    stats[f"label:{l}"] += 1

    if not rows:
        raise SystemExit("Parsed 0 rows, so nothing was written (your existing data is untouched).")
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Wrote {len(rows)} rows -> {a.out}")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
