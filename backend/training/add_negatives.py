"""Add all-negative rows from Wikipedia pages. Dry run unless --write."""
import argparse, json, re, urllib.parse, urllib.request
from backend.app.ml.clause_model import segment

OUT = "ml_data/annotations/own_labeled.jsonl"

def fetch(page):
    url = ("https://en.wikipedia.org/w/api.php?action=query&prop=extracts&explaintext=1"
           "&format=json&titles=" + urllib.parse.quote(page))
    req = urllib.request.Request(url, headers={"User-Agent": "DataLawyerTraining/1.0 (research)"})
    d = json.load(urllib.request.urlopen(req, timeout=30))
    return next(iter(d["query"]["pages"].values())).get("extract", "")

ap = argparse.ArgumentParser()
ap.add_argument("--page", action="append", required=True)
ap.add_argument("--limit", type=int, default=60)
ap.add_argument("--license", default="own")
ap.add_argument("--write", action="store_true")
a = ap.parse_args()

seen = {json.loads(l)["text"] for l in open(OUT)}
new = []
for page in a.page:
    text = re.sub(r"==+[^=]+==+", " ", fetch(page))
    n = 0
    for s in segment(text):
        s = " ".join(s.split())
        w = len(s.split())
        if w < 6 or w > 60 or s in seen or s[-1] not in ".!?":
            continue
        seen.add(s)
        new.append({"text": s, "labels": [], "source": "Negative: " + page,
                    "license": a.license, "split": "train"})
        n += 1
        if n >= a.limit:
            break
    print(f"{page}: {n} rows")
print("total new:", len(new))
for r in new[:: max(1, len(new) // 6)][:6]:
    print(" -", r["text"][:110])
if a.write:
    with open(OUT, "a") as f:
        for r in new:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("written")
else:
    print("DRY RUN, nothing written")
