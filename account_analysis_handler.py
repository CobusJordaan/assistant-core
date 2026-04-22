"""
Account Analysis Handler
========================
FastAPI endpoint that accepts structured billing data and generates
an AI-powered account analysis using the local Ollama model.

All data stays on-network — no external API calls.
Called internally by the billing service (bearer-token protected).
"""

import os
import re
import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger("account-analysis")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
ANALYSIS_MODEL = os.getenv("DEFAULT_MODEL", "llama3.1:8b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "300"))

router = APIRouter()


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class AccountAnalysisRequest(BaseModel):
    client: dict[str, Any]
    financial_summary: dict[str, Any]
    invoices: list[dict[str, Any]] = []
    payments: list[dict[str, Any]] = []
    credit_notes: list[dict[str, Any]] = []
    allocation_ledger: list[dict[str, Any]] = []
    deterministic_findings: list[dict[str, Any]] = []
    risk_score: int = 0
    analysis_period_months: int = 12


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an expert accounting assistant reviewing a client's billing account. Produce a clear, professional, actionable analysis for accounting staff.

RULES:
- Only analyze data provided. Do not invent or assume transactions.
- Reference actual figures, invoice numbers, payment references, and dates from the data.
- Pay special attention to the allocation_ledger — this shows exactly which payment was applied to which invoice.
- Be concise but thorough. This is for accounting staff, not the client.
- Where data is incomplete, say so in confidence_notes.

ALLOCATION ANALYSIS FOCUS:
The allocation_ledger contains rows where each row = one payment applied to one invoice.

IMPORTANT — DO NOT flag these as problems:
- A payment allocated to a recent invoice that was issued around the same time as the payment. This is NORMAL — a customer paying their current month's invoice is correct behaviour.
- A debit order or EFT applied to the invoice it was raised for, even if that invoice is "newer". Monthly recurring billing will always look like this.

DO flag these as genuine problems:
1. A payment was allocated to a newer invoice while an OLDER invoice remains UNPAID and OVERDUE (e.g. 30+ days past due). The older debt should have been settled first.
2. Invoices receiving more total allocation than their invoice total (over-allocation).
3. A payment split across 3+ invoices without fully clearing any single invoice (inefficient — suggests manual error).
4. The same payment allocated to the same invoice more than once (duplicate allocation).
5. Payments that pre-date the invoice they are allocated to by more than a few days (possibly a data entry error).
6. Do the deterministic_findings flag any specific allocation problems you should explain in plain English?

When assessing allocation order, always check: are there older unpaid/overdue invoices that were skipped? If the account has NO older unpaid invoices, then allocating to the current invoice is perfectly correct — do not flag this.

PROPOSED ALLOCATION PLAN:
Use ONLY the allocation_focus block (not the full invoices/payments lists) to produce proposed_allocations.
allocation_focus.unpaid_invoices_oldest_first = invoices that still have a balance, sorted oldest due date first.
allocation_focus.payments_with_unallocated_amount = payments that have money not yet applied to an invoice.
Rules:
- Work through payments_with_unallocated_amount oldest payment first.
- For each payment, allocate its unallocated_amount against unpaid_invoices_oldest_first, clearing the oldest invoice first before moving to the next.
- If a payment's unallocated_amount exactly or more than covers an invoice balance, mark that invoice fully paid and carry the remainder to the next oldest.
- If there are no unpaid invoices, set suggested_invoices to [] and set note to "No outstanding invoices — payment is a credit or overpayment".
- Round all amounts to 2 decimal places.
- If allocation_focus has no payments or no unpaid invoices, return proposed_allocations as [].

YOU MUST respond with ONLY a valid JSON object — no markdown, no preamble, no text outside the JSON.

Required JSON structure:
{
  "overall_status": "Healthy" or "Needs Attention" or "High Risk",
  "payer_profile": "Good payer" or "Mostly reliable" or "Slow payer" or "Problematic payer",
  "summary": "2-4 sentence plain-English account overview including payment allocation quality",
  "key_findings": [{"severity": "info" or "warning" or "critical", "title": "...", "detail": "..."}],
  "allocation_issues": [{"code": "...", "title": "...", "detail": "...", "reference": "..."}],
  "proposed_allocations": [
    {
      "payment_ref": "...",
      "payment_date": "YYYY-MM-DD",
      "payment_amount": number,
      "currently_correct": true or false,
      "suggested_invoices": [
        {"invoice_number": "...", "amount": number, "reason": "..."}
      ],
      "note": "optional plain-English note about this payment"
    }
  ],
  "debit_order_risk": "Low" or "Medium" or "High" or "N/A",
  "payment_behavior": {"avg_days_to_pay": number or null, "late_payment_count": number, "failed_debit_count": number, "consistency": "Consistent" or "Irregular" or "Deteriorating" or "Improving"},
  "recommendations": [{"priority": "high" or "medium" or "low", "action": "..."}],
  "confidence_notes": "..."
}"""


# ---------------------------------------------------------------------------
# Ollama call
# ---------------------------------------------------------------------------

async def _list_ollama_models() -> list[str]:
    """Return list of model names available in Ollama."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                return [m["name"] for m in data.get("models", [])]
    except Exception:
        pass
    return []


async def _call_ollama(system: str, user_message: str) -> str:
    """Call local Ollama /api/chat and return the assistant response text.

    Falls back to /api/generate if /api/chat returns 404 (older Ollama builds).
    """
    combined_prompt = f"{system}\n\n{user_message}"

    async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
        # Try /api/chat first (Ollama ≥ 0.1.14)
        chat_payload = {
            "model": ANALYSIS_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 4096},
        }
        resp = await client.post(f"{OLLAMA_URL}/api/chat", json=chat_payload)

        if resp.status_code == 404:
            # Model not found — log available models to help diagnose
            available = await _list_ollama_models()
            hint = f" Available models: {', '.join(available)}" if available else " No models found in Ollama."
            raise httpx.HTTPStatusError(
                f"Model '{ANALYSIS_MODEL}' not found in Ollama (404).{hint} "
                f"Set DEFAULT_MODEL in .env to a valid model name.",
                request=resp.request, response=resp,
            )

        resp.raise_for_status()
        data = resp.json()
        return data["message"]["content"].strip()


def _extract_json(text: str) -> dict:
    """Parse JSON from model output, handling markdown code blocks."""
    # Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown code block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))

    # Find first { ... } block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))

    raise ValueError(f"No JSON found in model response. First 300 chars: {text[:300]}")


# ---------------------------------------------------------------------------
# Analysis endpoint
# ---------------------------------------------------------------------------

@router.post("/internal/account-analysis")
async def account_analysis(
    req: AccountAnalysisRequest,
    request: Request,
    authorization: str | None = Header(None),
):
    """Generate AI account analysis for a billing client using local Ollama."""
    from app import _check_internal_token
    if not _check_internal_token(authorization):
        return JSONResponse(status_code=401, content={"success": False, "error": "Unauthorized"})

    # Build context payload.
    # Keep general slices small so the prompt stays within the model's context window.
    # Add a focused allocation_focus block for the proposed_allocations section so
    # the model only needs to reason about unpaid invoices and unallocated payments.
    unpaid_invoices = [
        i for i in req.invoices
        if i.get("payment_status") in ("unpaid", "partially_paid") and (i.get("balance") or 0) > 0
    ]
    payments_needing_allocation = [
        p for p in req.payments
        if (p.get("unallocated_amount") or 0) > 0
    ]

    context = {
        "analysis_period_months": req.analysis_period_months,
        "client": req.client,
        "financial_summary": req.financial_summary,
        "risk_score": req.risk_score,
        "deterministic_findings": req.deterministic_findings,
        "recent_invoices": req.invoices[:10],
        "recent_payments": req.payments[:10],
        "recent_credit_notes": req.credit_notes[:5],
        "allocation_ledger": req.allocation_ledger[:20],
        # Focused subset for proposed_allocations — only what matters
        "allocation_focus": {
            "unpaid_invoices_oldest_first": sorted(
                unpaid_invoices, key=lambda i: i.get("due_date") or i.get("invoice_date") or ""
            )[:15],
            "payments_with_unallocated_amount": sorted(
                payments_needing_allocation, key=lambda p: p.get("payment_date") or ""
            )[:15],
        },
    }

    user_message = (
        "Analyze this billing account. "
        "Use the allocation_ledger to review how payments were applied. "
        "Use the allocation_focus block to build the proposed_allocations plan. "
        "Return your analysis as a JSON object only.\n\n"
        f"Account data:\n{json.dumps(context, indent=2, default=str)}"
    )

    try:
        raw_text = await _call_ollama(SYSTEM_PROMPT, user_message)
        analysis = _extract_json(raw_text)

        logger.info(
            "Account analysis generated for client %s — status: %s (model: %s)",
            req.client.get("client_number", "?"),
            analysis.get("overall_status", "?"),
            ANALYSIS_MODEL,
        )
        return {"success": True, "analysis": analysis}

    except httpx.ConnectError:
        logger.error("Cannot connect to Ollama at %s", OLLAMA_URL)
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": f"Cannot connect to local AI model at {OLLAMA_URL}. Is Ollama running?"},
        )
    except httpx.HTTPStatusError as e:
        logger.error("Ollama HTTP error: %s", e)
        return JSONResponse(
            status_code=502,
            content={"success": False, "error": str(e)},
        )
    except httpx.TimeoutException:
        logger.error("Ollama timed out after %ds", OLLAMA_TIMEOUT)
        return JSONResponse(
            status_code=504,
            content={"success": False, "error": "Local AI model timed out. The account may have too much data, or the model is busy."},
        )
    except (ValueError, json.JSONDecodeError) as e:
        logger.error("Failed to parse model JSON response: %s", e)
        return JSONResponse(
            status_code=502,
            content={"success": False, "error": "AI model returned an unstructured response. Try refreshing — smaller models may need retries."},
        )
    except Exception as e:
        logger.error("Unexpected error in account analysis: %s", e, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Internal error generating analysis"},
        )
