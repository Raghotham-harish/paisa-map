"""
llm_extract.py — Core LLM extraction engine for Phase 2.

Handles three document tiers:
  Tier 1 — Digital PDFs (pdfplumber)        : clean text, no LLM needed
  Tier 2 — Semi-structured text (LLM only)  : pdfplumber text → Ollama
  Tier 3 — Scanned PDFs (OCR + LLM)        : marker-pdf → Ollama

Stub mode: when Ollama is not running, returns synthetic data marked as
  source="stub". Safe to run in CI / before Ollama is installed.

Supported models (in preference order):
  llama3.2   — fast, good structured output, 2B or 3B
  qwen2.5    — better multilingual (Hindi land records), 7B
  mistral    — strong instruction following, 7B

Usage (from other ETL scripts):
  from etl.llm_extract import extract, DocumentType, STUB_MODE

  result = extract(DocumentType.LAND_RECORD, text=my_text)
  result = extract(DocumentType.RTO_CERT,    pdf_path=Path("cert.pdf"))
  result = extract(DocumentType.UPI_REPORT,  pdf_path=Path("npci.pdf"))
"""

import json
import logging
import re
import os
from enum import Enum
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)

# ── Model config ─────────────────────────────────────────────────────────────
OLLAMA_URL   = os.getenv("OLLAMA_URL", "http://localhost:11434")
PREFERRED_MODELS = ["llama3.2", "qwen2.5", "mistral", "llama2"]

# ── Document types ────────────────────────────────────────────────────────────
class DocumentType(str, Enum):
    LAND_RECORD = "land_record"   # ROR, sale deed, mutation, EC
    RTO_CERT    = "rto_cert"      # Registration certificate, fitness cert
    UPI_REPORT  = "upi_report"    # NPCI monthly UPI statistics
    NABARD_RRB  = "nabard_rrb"    # NABARD district RRB data
    MUNICIPAL   = "municipal"     # Property tax assessment document


# ── Pydantic-style schema definitions (plain dicts — no pydantic dep needed) ──
SCHEMAS: dict[str, dict] = {
    DocumentType.LAND_RECORD: {
        "description": "Extracted fields from an Indian land/property document",
        "fields": {
            "survey_number"     : ("str",   "Survey / khasra / plot number"),
            "district"          : ("str",   "District name"),
            "state"             : ("str",   "State name"),
            "pincode"           : ("str",   "6-digit pincode if mentioned, else null"),
            "area_sqft"         : ("float", "Total area in square feet (convert from sq m / cents / bigha if needed)"),
            "transaction_value" : ("float", "Sale / market value in INR (full number, not lakhs)"),
            "rate_per_sqft"     : ("float", "Transaction value / area (compute if not explicit)"),
            "property_type"     : ("str",   "residential / commercial / agricultural / mixed"),
            "transaction_date"  : ("str",   "DD-MM-YYYY or YYYY"),
            "confidence"        : ("str",   "high / medium / low — how confident are you in the extracted values"),
        },
    },
    DocumentType.RTO_CERT: {
        "description": "Extracted fields from an Indian vehicle registration certificate",
        "fields": {
            "registration_number": ("str",   "Vehicle registration number"),
            "owner_pincode"       : ("str",   "6-digit pincode of registered owner"),
            "vehicle_class"       : ("str",   "LMV / HMV / MCWG / 2W / 3W / other"),
            "fuel_type"           : ("str",   "Petrol / Diesel / CNG / Electric / Hybrid"),
            "make_model"          : ("str",   "Manufacturer and model"),
            "engine_cc"           : ("float", "Engine displacement in cc; null for electric"),
            "price_inr"           : ("float", "Ex-showroom or purchase price in INR if stated"),
            "registration_date"   : ("str",   "DD-MM-YYYY"),
            "is_luxury"           : ("bool",  "True if price > 20L or vehicle is premium brand"),
            "confidence"          : ("str",   "high / medium / low"),
        },
    },
    DocumentType.UPI_REPORT: {
        "description": "NPCI monthly UPI transaction statistics by state",
        "fields": {
            "month_year"       : ("str",   "Month and year e.g. March 2024"),
            "state_data"       : ("list",  "Array of {state, txn_count_cr, txn_value_cr} objects"),
            "total_txn_cr"     : ("float", "Total UPI transactions in crore"),
            "total_value_cr"   : ("float", "Total transaction value in crore INR"),
            "top_3_states"     : ("list",  "Top 3 states by volume"),
        },
    },
    DocumentType.NABARD_RRB: {
        "description": "NABARD district/state RRB (Regional Rural Bank) data",
        "fields": {
            "year"              : ("str",   "Financial year e.g. 2022-23"),
            "state_data"        : ("list",  "Array of {state, district, rrb_name, branches, deposits_cr, advances_cr}"),
            "total_rrb_branches": ("int",   "Total RRB branches nationally"),
            "cd_ratio"          : ("float", "National credit-deposit ratio for RRBs"),
        },
    },
    DocumentType.MUNICIPAL: {
        "description": "Municipal property tax assessment document",
        "fields": {
            "ward"              : ("str",   "Ward / zone name or number"),
            "pincode"           : ("str",   "6-digit pincode"),
            "property_type"     : ("str",   "residential / commercial"),
            "built_up_area_sqft": ("float", "Built-up area in sq ft"),
            "annual_value_inr"  : ("float", "Annual value / ARV assessed"),
            "tax_amount_inr"    : ("float", "Annual tax amount"),
            "assessment_year"   : ("str",   "Assessment year"),
            "confidence"        : ("str",   "high / medium / low"),
        },
    },
}

# ── PDF text extraction (Tier 1 & 2) ─────────────────────────────────────────

def extract_text_pdfplumber(pdf_path: Path) -> str:
    """Extract text from a digital PDF using pdfplumber (fast, lightweight)."""
    try:
        import pdfplumber
        pages = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                tables = page.extract_tables() or []
                for table in tables:
                    for row in table:
                        text += "\n" + "  |  ".join(str(c or "") for c in row)
                pages.append(text)
        return "\n\n--- PAGE BREAK ---\n\n".join(pages)
    except ImportError:
        log.warning("pdfplumber not installed — pip install pdfplumber")
        return ""
    except Exception as exc:
        log.warning("pdfplumber failed for %s: %s", pdf_path, exc)
        return ""


def extract_text_marker(pdf_path: Path) -> str:
    """
    Extract text from a scanned PDF using marker-pdf (OCR-capable, heavier).
    Falls back to pdfplumber if marker-pdf is not installed.
    """
    try:
        from marker.convert import convert_single_pdf
        from marker.models import load_all_models
        models = load_all_models()
        full_text, _, _ = convert_single_pdf(str(pdf_path), models)
        return full_text
    except ImportError:
        log.info("marker-pdf not installed — falling back to pdfplumber")
        return extract_text_pdfplumber(pdf_path)
    except Exception as exc:
        log.warning("marker-pdf failed for %s: %s — falling back", pdf_path, exc)
        return extract_text_pdfplumber(pdf_path)


def pdf_to_text(pdf_path: Path, force_ocr: bool = False) -> str:
    """Auto-detect whether PDF needs OCR; use appropriate extractor."""
    text = extract_text_pdfplumber(pdf_path)
    # If we got very little text (< 100 chars per page), assume scanned
    page_count = max(1, text.count("PAGE BREAK") + 1)
    if force_ocr or len(text) / page_count < 100:
        log.info("Low text density — switching to OCR for %s", pdf_path.name)
        text = extract_text_marker(pdf_path)
    return text


# ── Ollama client ─────────────────────────────────────────────────────────────

def _active_model():
    """Return first available Ollama model from preference list, or None."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if not resp.ok:
            return None
        installed = {m["name"].split(":")[0] for m in resp.json().get("models", [])}
        for m in PREFERRED_MODELS:
            if m in installed:
                return m
        if installed:
            return next(iter(installed))
        return None
    except Exception:
        return None


STUB_MODE: bool = False   # set to True by tests or when Ollama unavailable


def _build_prompt(doc_type: DocumentType, text: str) -> str:
    schema = SCHEMAS[doc_type]
    field_desc = "\n".join(
        f'  "{k}": ({v[0]}) {v[1]}'
        for k, v in schema["fields"].items()
    )
    return f"""You are a data extraction assistant specialising in Indian government documents.

Document type: {doc_type.value}
Task: {schema["description"]}

Extract the following fields and return ONLY valid JSON (no markdown, no explanation):
{{
{field_desc}
}}

Rules:
- Return null for any field you cannot find with confidence.
- For monetary values, always return the full number in INR (not lakhs/crores notation).
- Convert units to the target unit specified in each field description.
- Set confidence = "low" if you had to infer or estimate any key field.

Document text:
---
{text[:6000]}
---

Return only the JSON object:"""


def _stub_result(doc_type: DocumentType) -> dict:
    """Return clearly-labelled synthetic data when Ollama is not running."""
    stubs = {
        DocumentType.LAND_RECORD: {
            "survey_number": "STUB-123", "district": "South Delhi", "state": "Delhi",
            "pincode": "110016", "area_sqft": 1200.0, "transaction_value": 14400000.0,
            "rate_per_sqft": 12000.0, "property_type": "residential",
            "transaction_date": "2023-01-15", "confidence": "stub",
        },
        DocumentType.RTO_CERT: {
            "registration_number": "DL-STUB-0001", "owner_pincode": "110016",
            "vehicle_class": "LMV", "fuel_type": "Petrol", "make_model": "STUB Sedan",
            "engine_cc": 1200.0, "price_inr": 800000.0, "registration_date": "2023-06-01",
            "is_luxury": False, "confidence": "stub",
        },
        DocumentType.UPI_REPORT: {
            "month_year": "STUB March 2024", "total_txn_cr": 1300.0,
            "total_value_cr": 19000.0, "top_3_states": ["Maharashtra","Delhi","Karnataka"],
            "state_data": [{"state":"Delhi","txn_count_cr":85,"txn_value_cr":1400}],
        },
        DocumentType.NABARD_RRB: {
            "year": "2022-23", "total_rrb_branches": 21856, "cd_ratio": 0.72,
            "state_data": [{"state":"UP","district":"STUB","rrb_name":"STUB RRB",
                            "branches":12,"deposits_cr":450,"advances_cr":320}],
        },
        DocumentType.MUNICIPAL: {
            "ward": "STUB-W1", "pincode": "110016", "property_type": "residential",
            "built_up_area_sqft": 900.0, "annual_value_inr": 180000.0,
            "tax_amount_inr": 9000.0, "assessment_year": "2023-24", "confidence": "stub",
        },
    }
    result = stubs.get(doc_type, {})
    result["_source"] = "stub"
    return result


def extract(
    doc_type: DocumentType,
    text: str = "",
    pdf_path: Path = None,
    force_ocr: bool = False,
    model: str = None,
) -> dict:
    """
    Main extraction entrypoint.

    Args:
        doc_type   : what kind of document this is
        text       : pre-extracted text (skip PDF step if provided)
        pdf_path   : path to PDF file (text will be extracted automatically)
        force_ocr  : force marker-pdf OCR even if pdfplumber finds text
        model      : override Ollama model (default: first available)

    Returns:
        dict with extracted fields + "_source": "ollama" | "stub"
    """
    # Step 1: get text
    if not text and pdf_path:
        text = pdf_to_text(pdf_path, force_ocr=force_ocr)

    if not text.strip():
        log.warning("No text to extract from — returning stub")
        return _stub_result(doc_type)

    # Step 2: check if Ollama available
    global STUB_MODE
    active = model or _active_model()
    if STUB_MODE or not active:
        if not STUB_MODE:
            log.warning("Ollama not running at %s — returning stub result", OLLAMA_URL)
            log.warning("Install: brew install ollama && ollama pull llama3.2")
            STUB_MODE = True
        return _stub_result(doc_type)

    # Step 3: build prompt and call Ollama
    prompt = _build_prompt(doc_type, text)
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": active, "prompt": prompt, "format": "json", "stream": False},
            timeout=120,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "{}")
        # Strip any accidental markdown fences
        raw = re.sub(r"```(?:json)?\s*", "", raw).strip()
        result = json.loads(raw)
        result["_source"] = f"ollama:{active}"
        result["_model"]  = active
        return result
    except json.JSONDecodeError as exc:
        log.warning("LLM returned invalid JSON (%s) — retrying with stricter prompt", exc)
        return _retry_extract(doc_type, text, active)
    except Exception as exc:
        log.error("Ollama call failed: %s — returning stub", exc)
        return _stub_result(doc_type)


def _retry_extract(doc_type: DocumentType, text: str, model: str) -> dict:
    """Second attempt: shorter context + more explicit JSON instruction."""
    short_text = text[:2000]
    prompt = (
        f"Extract data from this Indian {doc_type.value} document. "
        f"Return ONLY a JSON object with these keys: "
        f"{list(SCHEMAS[doc_type]['fields'].keys())}. "
        f"Use null for missing values. No explanation.\n\n"
        f"Text: {short_text}"
    )
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model, "prompt": prompt, "format": "json", "stream": False},
            timeout=90,
        )
        result = json.loads(resp.json().get("response", "{}"))
        result["_source"] = f"ollama:{model}:retry"
        return result
    except Exception as exc:
        log.error("Retry also failed: %s", exc)
        return _stub_result(doc_type)


# ── Batch extraction ──────────────────────────────────────────────────────────

def batch_extract(
    doc_type: DocumentType,
    pdf_paths: list[Path],
    force_ocr: bool = False,
) -> list[dict]:
    """Extract from multiple PDFs; logs progress."""
    results = []
    for i, path in enumerate(pdf_paths, 1):
        log.info("[%d/%d] Extracting %s from %s", i, len(pdf_paths), doc_type.value, path.name)
        r = extract(doc_type, pdf_path=path, force_ocr=force_ocr)
        r["_file"] = str(path)
        results.append(r)
    return results


# ── Quick sanity test ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    model = _active_model()
    if model:
        print(f"Ollama active model: {model}")
    else:
        print("Ollama not running — will use stub mode")

    # Test each doc type with stub text
    for dt in DocumentType:
        r = extract(dt, text=f"Sample {dt.value} document for testing.")
        src = r.get("_source", "?")
        conf = r.get("confidence", r.get("cd_ratio", "n/a"))
        print(f"  {dt.value:20s}: source={src}, confidence={conf}")

    print("\nllm_extract.py ready.")
