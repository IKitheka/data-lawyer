import os
import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.app.db import get_db
from backend.app.models import Account, ApiKey
from backend.app.core.auth import require_api_key

router = APIRouter()

stripe.api_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()

PRICE_PRO = (os.getenv("STRIPE_PRICE_PRO") or "").strip()
PRICE_BUSINESS = (os.getenv("STRIPE_PRICE_BUSINESS") or "").strip()
WEBHOOK_SECRET = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()

PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or "").strip()  # e.g. https://your-api.onrender.com

def _plan_from_price(price_id: str) -> tuple[str, int]:
    # plan name, daily quota
    if price_id == PRICE_BUSINESS:
        return ("business", 3000)
    if price_id == PRICE_PRO:
        return ("pro", 300)
    return ("free", 20)

@router.post("/checkout")
def create_checkout_session(
    request: Request,
    payload: dict,
    api_key: ApiKey = Depends(require_api_key),
    db: Session = Depends(get_db),
):
    if not stripe.api_key:
        raise HTTPException(500, "Stripe is not configured")

    price = (payload.get("price_id") or "").strip()
    if price not in {PRICE_PRO, PRICE_BUSINESS}:
        raise HTTPException(400, "Invalid price_id")

    # Ensure account exists
    acct = db.query(Account).filter(Account.id == api_key.account_id).first()
    if not acct:
        raise HTTPException(500, "Account missing for API key")

    if not acct.stripe_customer_id:
        customer = stripe.Customer.create(email=acct.email)
        acct.stripe_customer_id = customer["id"]
        db.commit()

    success = f"{PUBLIC_BASE_URL}/billing/success"
    cancel = f"{PUBLIC_BASE_URL}/billing/cancel"

    sess = stripe.checkout.Session.create(
        mode="subscription",
        customer=acct.stripe_customer_id,
        line_items=[{"price": price, "quantity": 1}],
        success_url=success,
        cancel_url=cancel,
        metadata={
            "account_id": str(acct.id),
            "api_key_id": str(api_key.id),
            "price_id": price,
        },
    )

    return {"checkout_url": sess["url"]}

@router.post("/portal")
def create_portal_session(
    api_key: ApiKey = Depends(require_api_key),
    db: Session = Depends(get_db),
):
    if not stripe.api_key:
        raise HTTPException(500, "Stripe is not configured")

    acct = db.query(Account).filter(Account.id == api_key.account_id).first()
    if not acct or not acct.stripe_customer_id:
        raise HTTPException(400, "No Stripe customer found")

    sess = stripe.billing_portal.Session.create(
        customer=acct.stripe_customer_id,
        return_url=f"{PUBLIC_BASE_URL}/billing/return",
    )
    return {"portal_url": sess["url"]}

@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    if not WEBHOOK_SECRET:
        raise HTTPException(500, "Stripe webhook secret not configured")

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig, WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(400, "Invalid webhook signature")

    etype = event["type"]
    data = event["data"]["object"]

    # We care about subscription lifecycle
    if etype in ("checkout.session.completed",):
        # subscription created
        meta = data.get("metadata") or {}
        api_key_id = int(meta.get("api_key_id") or "0")
        price_id = (meta.get("price_id") or "").strip()

        plan, quota = _plan_from_price(price_id)

        key = db.query(ApiKey).filter(ApiKey.id == api_key_id).first()
        if key:
            key.plan = plan
            key.daily_quota = quota
            key.is_active = True
            key.stripe_price_id = price_id
            db.commit()

    if etype in ("customer.subscription.updated", "customer.subscription.created"):
        sub = data
        customer_id = sub.get("customer")
        items = sub.get("items", {}).get("data", [])
        price_id = items[0]["price"]["id"] if items else ""

        plan, quota = _plan_from_price(price_id)

        # Find account by stripe customer id, update all keys or a default key
        acct = db.query(Account).filter(Account.stripe_customer_id == customer_id).first()
        if acct:
            # simple approach: upgrade all keys for that account
            keys = db.query(ApiKey).filter(ApiKey.account_id == acct.id).all()
            for k in keys:
                k.plan = plan
                k.daily_quota = quota
                k.is_active = True
                k.stripe_price_id = price_id
                k.stripe_subscription_id = sub.get("id")
            db.commit()

    if etype in ("customer.subscription.deleted",):
        sub = data
        customer_id = sub.get("customer")
        acct = db.query(Account).filter(Account.stripe_customer_id == customer_id).first()
        if acct:
            keys = db.query(ApiKey).filter(ApiKey.account_id == acct.id).all()
            for k in keys:
                k.plan = "free"
                k.daily_quota = 20
                k.stripe_subscription_id = None
                k.stripe_price_id = None
            db.commit()

    return {"ok": True}
