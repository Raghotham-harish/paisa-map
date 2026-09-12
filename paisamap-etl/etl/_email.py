"""
_email.py — AWS SES-backed transactional email (Phase D1: org invites).

Same graceful-degradation shape as blueprints/billing.py's Razorpay _client():
if AWS credentials/SES_FROM_EMAIL aren't set, or boto3 isn't installed, every
send function returns False and logs a warning instead of raising — an invite
still gets created (with its accept_url returned directly in the API
response) even before a real AWS account exists. Once
AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_REGION/SES_FROM_EMAIL are added to
/etc/paisamap/db.env and the service restarts, emails start actually sending
with zero code changes.
"""

import os
import logging

logger = logging.getLogger(__name__)

try:
    import boto3
except ImportError:
    boto3 = None


def _client():
    if boto3 is None:
        return None
    if not os.environ.get("AWS_ACCESS_KEY_ID") or not os.environ.get("SES_FROM_EMAIL"):
        return None
    return boto3.client("ses", region_name=os.environ.get("AWS_REGION", "ap-south-1"))


def send_org_invite_email(to_email, org_name, inviter_name, role, accept_url):
    """Returns True if SES accepted the send, False otherwise (not
    configured, or the SES call itself failed) — callers must never let a
    False here block invite creation, since the accept_url is always
    returned separately in the API response as a fallback."""
    client = _client()
    if client is None:
        logger.warning(
            "SES not configured — invite to %s for org %r not emailed (accept_url=%s)",
            to_email, org_name, accept_url,
        )
        return False

    subject = f"{inviter_name} invited you to join {org_name} on PaisaMap"
    text_body = (
        f"{inviter_name} has invited you to join {org_name} on PaisaMap as a {role}.\n\n"
        f"Accept the invite: {accept_url}\n\n"
        f"This link expires in 7 days. If you weren't expecting this, you can ignore this email."
    )
    html_body = (
        f"<p>{inviter_name} has invited you to join <strong>{org_name}</strong> "
        f"on PaisaMap as a <strong>{role}</strong>.</p>"
        f'<p><a href="{accept_url}">Accept the invite</a></p>'
        f"<p style=\"color:#666;font-size:13px\">This link expires in 7 days. "
        f"If you weren't expecting this, you can ignore this email.</p>"
    )

    try:
        client.send_email(
            Source=os.environ["SES_FROM_EMAIL"],
            Destination={"ToAddresses": [to_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": text_body, "Charset": "UTF-8"},
                    "Html": {"Data": html_body, "Charset": "UTF-8"},
                },
            },
        )
        return True
    except Exception:
        logger.exception("SES send_email failed for invite to %s", to_email)
        return False
