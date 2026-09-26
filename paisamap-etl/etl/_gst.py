"""
_gst.py — the GST rules a tax invoice needs (P12, PRICING_MODEL §9).

Confirmed with the CA (2026-09-26):
  - one SAC for everything PaisaMap sells (subscriptions, credits, PDF reports,
    API access): 998439, "other online contents";
  - 18% GST;
  - place of supply = the buyer's state: Karnataka buyers pay CGST 9% + SGST 9%
    (intra-state), every other state pays IGST 18% (inter-state);
  - GST on prepaid credits is due when they are bought (an advance received for
    a service is taxed on receipt), which is how checkout already charges it.

Place of supply for a domestic service (IGST Act s.12(2)): the buyer's location
when they are registered or gave an address; with nothing on record, the
supplier's own location — so an anonymous consumer is taxed as intra-state.

Pure functions only (no DB, no Flask), so every rule is unit-testable.
"""

from datetime import datetime, timedelta, timezone

import _pricing

SAC_CODE = "998439"
SAC_DESCRIPTION = "Other online contents n.e.c."

# Who issues the invoice. Already public on /contact; the GSTIN itself comes
# from PAISAMAP_GSTIN at runtime (blueprints/billing.py:_seller_gstin), never
# from here, so an unregistered deployment can't print one.
SELLER = {
    "trade_name": "Cooter Studio",
    "legal_name": "Raghotham Harisha",
    "address_lines": [
        "3rd Floor, 16, 7th Cross Road, Sri Banasankari Provision Stores",
        "Chikkasandra, Bengaluru, Karnataka 560057",
    ],
    "state_code": "29",
    "email": "ragho@cooterlabs.com",
    "website": "paisamaps.com",
}

# GST state / UT codes (the first two digits of every GSTIN).
STATES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram",
    "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra", "29": "Karnataka", "30": "Goa", "31": "Lakshadweep",
    "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh",
    "38": "Ladakh",
}

_GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
IST = timezone(timedelta(hours=5, minutes=30))

MAX_NAME_LEN = 200
MAX_ADDRESS_LEN = 500


def gstin_checksum_ok(gstin: str) -> bool:
    """The 15th character of a GSTIN is a mod-36 check digit over the first 14.
    Catches most typos that the shape check alone lets through."""
    if not _pricing.valid_gstin(gstin):
        return False
    total = 0
    for i, ch in enumerate(gstin[:14]):
        product = _GSTIN_CHARS.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _GSTIN_CHARS[(36 - total % 36) % 36] == gstin[14]


def normalize_buyer(raw):
    """Validate the optional billing details a buyer gives at checkout.
    Returns (buyer, error_code). `buyer` is None when nothing was given.

    buyer = {name, gstin, state_code, address} (each may be None). A GSTIN fixes
    the state (its first two digits); a state picked as well must agree with it.
    A GSTIN needs the business's legal name, since that is what the invoice is
    issued to."""
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        return None, "invalid_billing"

    def text(key, limit):
        v = raw.get(key)
        if v is None:
            return None, None
        if not isinstance(v, str):
            return None, f"invalid_billing_{key}"
        v = " ".join(v.split())
        if len(v) > limit:
            return None, f"invalid_billing_{key}"
        return v or None, None

    name, err = text("name", MAX_NAME_LEN)
    if err:
        return None, err
    address, err = text("address", MAX_ADDRESS_LEN)
    if err:
        return None, err
    gstin, err = text("gstin", 15)
    if err:
        return None, "invalid_gstin"
    state, err = text("state_code", 2)
    if err:
        return None, "invalid_state"

    if gstin is not None:
        gstin = gstin.upper()
        if not gstin_checksum_ok(gstin) or gstin[:2] not in STATES:
            return None, "invalid_gstin"
        if state is not None and state != gstin[:2]:
            return None, "gstin_state_mismatch"
        state = gstin[:2]
        if name is None:
            return None, "gstin_needs_name"
    if state is not None and state not in STATES:
        return None, "invalid_state"

    if name is None and gstin is None and state is None and address is None:
        return None, None
    return {"name": name, "gstin": gstin, "state_code": state, "address": address}, None


def place_of_supply(buyer) -> str:
    """The state code GST is due in: the buyer's, else the seller's."""
    if buyer and buyer.get("state_code") in STATES:
        return buyer["state_code"]
    return SELLER["state_code"]


def split_gst(gst_paise: int, pos_code: str, seller_state: str = SELLER["state_code"]) -> dict:
    """Split the GST already charged into its heads. The total never changes
    (18% either way); intra-state halves it into CGST + SGST, the odd paisa (if
    any) going to CGST so the heads always add back to exactly what was paid."""
    if gst_paise and pos_code == seller_state:
        cgst = (gst_paise + 1) // 2
        return {"supply": "intra", "cgst_paise": cgst, "sgst_paise": gst_paise - cgst, "igst_paise": 0}
    return {"supply": "inter" if gst_paise else "none",
            "cgst_paise": 0, "sgst_paise": 0, "igst_paise": gst_paise}


def financial_year(when: datetime) -> str:
    """Indian financial year (April-March) of an instant, in IST: '26-27'."""
    d = when.astimezone(IST) if when.tzinfo else when.replace(tzinfo=timezone.utc).astimezone(IST)
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


def invoice_number(seq: int, when: datetime) -> str:
    """'PM-26-27-000123' — GST wants a serial unique within the financial year,
    at most 16 characters of letters, digits, '-' and '/'. The sequence itself
    never resets, which keeps it unique across years too."""
    return f"PM-{financial_year(when)}-{seq:06d}"


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
         "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
         "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_thousand(n: int) -> str:
    words = []
    if n >= 100:
        words += [_ONES[n // 100], "Hundred"]
        n %= 100
    if n >= 20:
        words.append(_TENS[n // 10])
        n %= 10
    if n:
        words.append(_ONES[n])
    return " ".join(words)


def _indian_words(n: int) -> str:
    if n == 0:
        return "Zero"
    parts = []
    for size, label in ((10**7, "Crore"), (10**5, "Lakh"), (10**3, "Thousand")):
        if n >= size:
            parts.append(f"{_indian_words(n // size) if size == 10**7 else _below_thousand(n // size)} {label}")
            n %= size
    if n:
        parts.append(_below_thousand(n))
    return " ".join(parts)


def amount_in_words(paise: int) -> str:
    """'Rupees Fourteen Thousand One Hundred Sixty and Fifty Paise Only' (Indian
    lakh/crore grouping, as Indian invoices write it)."""
    rupees, p = divmod(int(paise), 100)
    out = f"Rupees {_indian_words(rupees)}"
    if p:
        out += f" and {_indian_words(p)} Paise"
    return out + " Only"
