"""How many sentences describe a data practice? A real privacy policy has many; most other pages have almost none.

Usage (from backend/):
    PYTHONPATH="$(pwd)" python training/policy_likeness.py                       # your cached policies
    PYTHONPATH="$(pwd)" python training/policy_likeness.py URL_OR_FILE ...      # plus any pages you want to test
"""
import sys
from pathlib import Path

from backend.app.ml.clause_model import predict_clauses
from training.label_tool import html_to_text

PRACTICE = {"collection", "purpose", "share_third_party", "service_provider", "sale", "advertising", "tracking",
            "retention_period", "retention_vague", "security", "right_access", "right_delete", "right_correct",
            "right_object", "right_portability", "withdraw_consent", "opt_out", "intl_transfer",
            "transfer_safeguards", "supervisory_authority", "contact", "children"}


def load(arg):
    if arg.startswith("http"):
        import requests
        r = requests.get(arg, timeout=25, headers={"User-Agent": "DataLawyer-labeller/1.0"})
        r.raise_for_status()
        return html_to_text(r.text)
    return Path(arg).expanduser().read_text(encoding="utf-8", errors="replace")


def main():
    items = [(p.stem, p.read_text(encoding="utf-8")) for p in sorted(Path("ml_data/policy_cache").glob("*.txt"))]
    for a in sys.argv[1:]:
        try:
            items.append(("(test) " + a[-40:], load(a)))
        except Exception as e:
            print(f"  [skipped] {a}: {e}")
    print(f"{'page':44s} {'sentences':>9s} {'practice':>9s} {'share':>7s}")
    for name, text in items:
        cl = predict_clauses(text[:20000])  # the API analyses at most this much text
        n = sum(1 for c in cl if PRACTICE & set(c["labels"]))
        print(f"{name[:44]:44s} {len(cl):9d} {n:9d} {100 * n / max(1, len(cl)):6.1f}%")


if __name__ == "__main__":
    main()
