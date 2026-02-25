"""Billing endpoints for Stripe subscription management."""

from __future__ import annotations

import logging

import stripe
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from orpheus.api.deps import CurrentUser
from orpheus.config import settings
from orpheus.db.models.user import get_user_by_stripe_customer
from orpheus.db.session import get_session


router = APIRouter(prefix="/billing", tags=["billing"])
logger = logging.getLogger(__name__)


def _require_stripe() -> None:
    """Raise 501 if Stripe is not configured."""
    if not settings.stripe_api_key:
        raise HTTPException(501, "Billing is not configured")


def _stripe_client() -> stripe.StripeClient:
    return stripe.StripeClient(settings.stripe_api_key.get_secret_value())


@router.post("/checkout")
async def create_checkout_session(user: CurrentUser):
    """Create a Stripe Checkout Session for a new subscription."""
    _require_stripe()
    client = _stripe_client()

    async with get_session() as db:
        # Create Stripe customer if needed
        if not user.stripe_customer_id:
            customer = client.v1.customers.create(
                params={
                    "email": f"{user.github_login}@users.noreply.github.com" if user.github_login else None,
                    "name": user.github_login,
                    "metadata": {"github_id": str(user.github_id)},
                }
            )
            user.stripe_customer_id = customer.id
            db.add(user)

        session = client.v1.checkout.sessions.create(
            params={
                "mode": "subscription",
                "customer": user.stripe_customer_id,
                "line_items": [{"price": settings.stripe_price_id, "quantity": 1}],
                "success_url": f"{settings.base_url}/#/billing?success=1",
                "cancel_url": f"{settings.base_url}/#/billing",
            }
        )

    return {"url": session.url}


@router.get("/portal")
async def billing_portal(user: CurrentUser):
    """Redirect to the Stripe Customer Portal."""
    _require_stripe()

    if not user.stripe_customer_id:
        raise HTTPException(400, "No billing account")

    client = _stripe_client()
    portal_session = client.v1.billing_portal.sessions.create(
        params={
            "customer": user.stripe_customer_id,
            "return_url": f"{settings.base_url}/#/billing",
        }
    )
    return RedirectResponse(portal_session.url)


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events."""
    if not settings.stripe_api_key or not settings.stripe_webhook_secret:
        raise HTTPException(501, "Billing is not configured")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    event = stripe.Webhook.construct_event(
        payload=payload,
        sig_header=sig_header,
        secret=settings.stripe_webhook_secret.get_secret_value(),
    )

    event_type = event.type
    data = event.data.object

    async with get_session() as db:
        if event_type == "checkout.session.completed":
            customer_id = data.get("customer") if isinstance(data, dict) else getattr(data, "customer", None)
            if customer_id:
                user = await get_user_by_stripe_customer(db, customer_id)
                if user:
                    user.subscription_status = "active"
                    db.add(user)
                    logger.info("Subscription activated for user %s", user.github_login)

        elif event_type == "customer.subscription.updated":
            customer_id = data.get("customer") if isinstance(data, dict) else getattr(data, "customer", None)
            status = data.get("status") if isinstance(data, dict) else getattr(data, "status", None)
            if customer_id and status:
                user = await get_user_by_stripe_customer(db, customer_id)
                if user:
                    user.subscription_status = status
                    db.add(user)
                    logger.info("Subscription status updated to %s for user %s", status, user.github_login)

        elif event_type == "customer.subscription.deleted":
            customer_id = data.get("customer") if isinstance(data, dict) else getattr(data, "customer", None)
            if customer_id:
                user = await get_user_by_stripe_customer(db, customer_id)
                if user:
                    user.subscription_status = "canceled"
                    db.add(user)
                    logger.info("Subscription canceled for user %s", user.github_login)

    return {"status": "ok"}
