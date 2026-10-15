"""Data Lawyer privacy taxonomy: the labels the clause model learns to predict."""

LABELS = [
    "collection",             # what data is collected
    "purpose",                # why data is used
    "share_third_party",      # disclosed to third parties / partners
    "service_provider",       # processors / vendors
    "sale",                   # sale or "sharing" for value
    "advertising",            # ads / marketing use
    "tracking",               # cookies, pixels, analytics, SDKs
    "retention_period",       # specific retention period or clear criteria
    "retention_vague",        # retention without a clear period
    "right_access",
    "right_delete",
    "right_correct",
    "right_object",
    "right_portability",
    "withdraw_consent",
    "opt_out",
    "intl_transfer",
    "transfer_safeguards",
    "security",
    "supervisory_authority",  # complaint route to a regulator
    "contact",                # how to reach the controller / DPO
    "policy_change_no_notice",
    "arbitration_waiver",     # arbitration / class-action waiver
    "children",
]

# Maps model labels onto the existing `structured` response shape used by the extension.
RIGHTS_MAP = {
    "access": "right_access",
    "delete": "right_delete",
    "correct": "right_correct",
    "object": "right_object",
    "portability": "right_portability",
    "withdraw_consent": "withdraw_consent",
    "opt_out_marketing": "opt_out",
}
