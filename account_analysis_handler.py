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
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))

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
You must check:
1. Are payments applied to the correct invoices? (oldest debt first is standard practice)
2. Are any invoices receiving more allocation than their total? (over-allocation)
3. Are any payments split across many invoices without clearing any? (inefficient allocation)
4. Do payment dates make sense relative to the invoices they are allocated to?
5. Are there any invoices that are overdue yet a payment was allocated to a newer invoice instead?
6. Do the deterministic_findings flag any specific allocation problems you should explain in plain English?

YOU MUST respond with ONLY a valid JSON object — no markdown, no preamble, no text outside the JSON.

Required JSON structure:
{
  "overall_status": "Healthy" or "Needs Attention" or "High Risk",
  "payer_profile": "Good payer" or "Mostly reliable" or "Slow payer" or "Problematic payer",
  "summary": "2-4 sentence plain-English account overview including payment allocation quality",
  "key_findings": [{"severity": "info" or "warning" or "critical", "title": "...", "detail": "..."}],
  "allocation_issues": [{"code": "...", "title": "...", "detail": "...", "reference": "..."}],
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
            "options": {"temperature": 0.1, "num_predict": 2048},
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

    # Build context payload — allocation_ledger is the key new addition
    # Limit ledger rows to keep prompt within model context window
    context = {
        "analysis_period_months": req.analysis_period_months,
        "client": req.client,
        "financial_summary": req.financial_summary,
        "risk_score": req.risk_score,
        "deterministic_findings": req.deterministic_findings,
        "recent_invoices": req.invoices[:15],
        "recent_payments": req.payments[:15],
        "recent_credit_notes": req.credit_notes[:8],
        "allocation_ledger": req.allocation_ledger[:40],  # each payment→invoice link
    }

    user_message = (
        "Analyze this billing account — pay special attention to the allocation_ledger "
        "which shows exactly how each payment was applied to invoices. "
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
