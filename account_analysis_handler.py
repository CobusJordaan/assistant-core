"""
Account Analysis Handler
========================
FastAPI endpoint that accepts structured billing data and generates
an AI-powered account analysis using the Anthropic Claude API.

Called internally by the billing service (bearer-token protected).
"""

import os
import json
import logging
from typing import Any

import anthropic
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger("account-analysis")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANALYSIS_MODEL = "claude-haiku-4-5-20251001"

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
    deterministic_findings: list[dict[str, Any]] = []
    risk_score: int = 0
    analysis_period_months: int = 12


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an expert accounting assistant. Your role is to analyze a client's billing account data and produce a clear, professional, actionable analysis for accounting staff.

IMPORTANT RULES:
- Only analyze data you are given. Do not invent or assume transactions.
- Be specific and reference actual figures, invoice numbers, or dates from the data.
- Be concise but thorough. This is for accounting staff, not the client.
- Where data is incomplete or ambiguous, say so clearly (confidence_notes).
- Your tone should be professional and direct — like an experienced accountant reviewing a file.

RESPONSE FORMAT:
You must respond with a valid JSON object with exactly these fields:
{
  "overall_status": "Healthy" | "Needs Attention" | "High Risk",
  "payer_profile": "Good payer" | "Mostly reliable" | "Slow payer" | "Problematic payer",
  "summary": "2-4 sentence plain-English account overview",
  "key_findings": [
    {"severity": "info"|"warning"|"critical", "title": "...", "detail": "..."}
  ],
  "allocation_issues": [
    {"code": "...", "title": "...", "detail": "...", "reference": "..."}
  ],
  "debit_order_risk": "Low" | "Medium" | "High" | "N/A",
  "payment_behavior": {
    "avg_days_to_pay": number | null,
    "late_payment_count": number,
    "failed_debit_count": number,
    "consistency": "Consistent" | "Irregular" | "Deteriorating" | "Improving"
  },
  "recommendations": [
    {"priority": "high"|"medium"|"low", "action": "..."}
  ],
  "confidence_notes": "Any caveats about data completeness or uncertainty"
}

Return ONLY the JSON object. No markdown, no explanation outside the JSON."""


# ---------------------------------------------------------------------------
# Analysis endpoint
# ---------------------------------------------------------------------------

@router.post("/internal/account-analysis")
async def account_analysis(
    req: AccountAnalysisRequest,
    request: Request,
    authorization: str | None = Header(None),
):
    """Generate AI account analysis for a billing client."""
    # Auth check (reuse the same token check from app.py)
    from app import _check_internal_token
    if not _check_internal_token(authorization):
        return JSONResponse(status_code=401, content={"success": False, "error": "Unauthorized"})

    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not configured")
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "AI service not configured (missing API key)"},
        )

    # Build the analysis context payload
    context = {
        "analysis_period_months": req.analysis_period_months,
        "client": req.client,
        "financial_summary": req.financial_summary,
        "risk_score": req.risk_score,
        "deterministic_findings": req.deterministic_findings,
        "recent_invoices": req.invoices[:20],
        "recent_payments": req.payments[:20],
        "recent_credit_notes": req.credit_notes[:10],
    }

    user_message = (
        f"Please analyze this billing account and return your analysis as JSON.\n\n"
        f"Account data:\n{json.dumps(context, indent=2, default=str)}"
    )

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=ANALYSIS_MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        raw_text = message.content[0].text.strip()

        # Parse the JSON response
        try:
            analysis = json.loads(raw_text)
        except json.JSONDecodeError:
            # Try to extract JSON if wrapped in markdown code block
            import re
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
            if match:
                analysis = json.loads(match.group(1))
            else:
                logger.error("Claude returned non-JSON: %s", raw_text[:500])
                return JSONResponse(
                    status_code=502,
                    content={"success": False, "error": "AI returned invalid response format"},
                )

        logger.info(
            "Account analysis generated for client %s — status: %s",
            req.client.get("client_number", "?"),
            analysis.get("overall_status", "?"),
        )
        return {"success": True, "analysis": analysis}

    except anthropic.APIError as e:
        logger.error("Anthropic API error: %s", e)
        return JSONResponse(
            status_code=502,
            content={"success": False, "error": f"AI API error: {str(e)}"},
        )
    except Exception as e:
        logger.error("Unexpected error in account analysis: %s", e, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Internal error generating analysis"},
        )
