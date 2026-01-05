import re

def detect_jurisdiction(text_lower: str) -> str:
    if "gdpr" in text_lower or "european union" in text_lower:
        return "GDPR"
    if "ccpa" in text_lower or "cpra" in text_lower:
        return "CCPA/CPRA"
    if "data protection act" in text_lower or "odpc" in text_lower or "kenya" in text_lower:
        return "Kenya DPA"
    return "UNKNOWN"

def analyze_with_rules(text: str) -> dict:
    t = text.lower()

    red_flags = []
    # selling data signals
    if re.search(r"\b(sell|sold|selling)\b.*\b(data|information)\b", t):
        red_flags.append("Policy suggests data may be sold.")
    if "third parties" in t or "third-party" in t:
        red_flags.append("Mentions sharing with third parties.")
    if "advertising" in t or "ad partners" in t or "ad network" in t:
        red_flags.append("Mentions advertising/marketing partners.")
    if "location" in t:
        red_flags.append("Mentions location data.")
    if "health" in t or "medical" in t:
        red_flags.append("Mentions health/medical data.")
    if "retain" in t and ("indefinitely" in t or "as long as necessary" in t):
        red_flags.append("Retention period is vague or potentially long.")

    # risk score
    score = 10
    if any("sold" in rf or "sell" in rf for rf in red_flags):
        score += 40
    if any("third parties" in rf for rf in red_flags):
        score += 20
    if any("advertising" in rf for rf in red_flags):
        score += 15
    if any("health" in rf for rf in red_flags):
        score += 15
    if any("location" in rf for rf in red_flags):
        score += 10
    if any("Retention" in rf or "Retention" in rf for rf in red_flags):
        score += 10

    score = min(100, float(score))

    if score >= 60:
        level = "HIGH"
    elif score >= 30:
        level = "MEDIUM"
    else:
        level = "LOW"

    jurisdiction = detect_jurisdiction(t)

    summary = (
        "This policy was analyzed using rule-based checks. "
        "Add an LLM key later to enable richer summaries and extraction."
    )

    extracted = {
        "data_collected": {
            "mentions_location": "location" in t,
            "mentions_health": ("health" in t or "medical" in t),
            "mentions_advertising": ("advertising" in t or "ad network" in t),
        },
        "data_sharing": {
            "mentions_third_parties": ("third parties" in t or "third-party" in t),
            "mentions_selling": bool(re.search(r"\b(sell|sold|selling)\b", t)),
        },
        "user_rights": {
            "mentions_deletion": ("delete" in t or "erasure" in t),
            "mentions_access": ("access" in t),
            "mentions_opt_out": ("opt out" in t or "opt-out" in t),
        }
    }

    reasoning = {
        "sentences_triggering_risk": [],
        "explanations": red_flags[:]
    }

    return {
        "summary": summary,
        "risk_level": level,
        "risk_score": score,
        "jurisdiction": jurisdiction,
        "red_flags": red_flags,
        "reasoning": reasoning,
        "extracted": extracted
    }
