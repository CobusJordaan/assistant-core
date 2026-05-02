"""HTTP client for the billing assistant API."""

import os
import httpx


class BillingClient:
    """Client for the CRM billing assistant API."""

    def __init__(self):
        self.base_url = os.getenv("BILLING_ASSISTANT_BASE_URL", "").rstrip("/")
        self.token = os.getenv("BILLING_ASSISTANT_API_TOKEN", "")
        self.timeout = float(os.getenv("BILLING_ASSISTANT_TIMEOUT", "15"))

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def _check_configured(self):
        if not self.configured:
            raise RuntimeError("Billing API not configured. Set BILLING_ASSISTANT_BASE_URL and BILLING_ASSISTANT_API_TOKEN.")

    def client_lookup(self, query: str, limit: int = 10) -> dict:
        """Search clients by name, email, phone, or client number."""
        self._check_configured()
        resp = httpx.get(
            f"{self.base_url}/api/assistant/client-lookup",
            params={"q": query, "limit": limit},
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def client_balance(self, client_id: int) -> dict:
        """Get account balance and outstanding invoice total."""
        self._check_configured()
        resp = httpx.get(
            f"{self.base_url}/api/assistant/client-balance",
            params={"client_id": client_id},
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def client_unpaid_invoices(self, client_id: int, limit: int = 20) -> dict:
        """List unpaid/partially paid invoices for a client."""
        self._check_configured()
        resp = httpx.get(
            f"{self.base_url}/api/assistant/client-unpaid-invoices",
            params={"client_id": client_id, "limit": limit},
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def client_by_phone(self, phone: str) -> dict:
        """Look up a client by phone number."""
        self._check_configured()
        resp = httpx.get(
            f"{self.base_url}/api/assistant/client-by-phone",
            params={"phone": phone},
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def client_summary(self, client_id: int) -> dict:
        """Full client overview: info, billing, services."""
        self._check_configured()
        resp = httpx.get(
            f"{self.base_url}/api/assistant/client-summary",
            params={"client_id": client_id},
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def create_support_ticket(self, client_id: int | None, category: str,
                              subject: str, message: str,
                              source: str = "whatsapp",
                              source_phone: str = "") -> dict:
        """Create a support ticket via the billing API."""
        self._check_configured()
        payload = {
            "client_id": client_id,
            "category": category,
            "subject": subject,
            "message": message,
            "source": source,
            "source_phone": source_phone,
        }
        resp = httpx.post(
            f"{self.base_url}/api/assistant/create-support-ticket",
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def send_invoice_whatsapp(self, client_id: int, phone_number: str = "",
                              language: str = "") -> dict:
        """Send the latest unpaid invoice to a client via WhatsApp."""
        self._check_configured()
        payload = {"client_id": client_id}
        if phone_number:
            payload["phone_number"] = phone_number
        if language:
            payload["language"] = language
        resp = httpx.post(
            f"{self.base_url}/api/assistant/send-invoice-whatsapp",
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
        )
        return resp.json()

    def client_radius_status(self, client_id: int) -> dict:
        """Get RADIUS online status, IP, MAC, and session info for a client."""
        self._check_configured()
        resp = httpx.get(
            f"{self.base_url}/api/assistant/client-radius-status",
            params={"client_id": client_id},
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def verify_client_identity(self, client_number: str, email: str,
                                contract_id: str = "") -> dict:
        """Identity check used by the WhatsApp linking flow.

        `client_number` is the user-supplied account reference and is matched
        against EITHER `clients.client_number` OR `clients.contract_id`.
        `email` must match the row's `email` column. If `contract_id` is also
        supplied (legacy mid-flight sessions), both ID columns must align —
        the billing endpoint preserves that strict mode for back-compat.
        Returns the API JSON; matched=false replies include a reason code.
        """
        self._check_configured()
        payload = {"client_number": client_number, "email": email}
        if contract_id:
            payload["contract_id"] = contract_id
        resp = httpx.post(
            f"{self.base_url}/api/assistant/verify-client-identity",
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def link_whatsapp(self, client_id: int, phone: str, profile_name: str = "") -> dict:
        """Persist a verified WhatsApp number -> client mapping."""
        self._check_configured()
        payload = {"client_id": client_id, "phone": phone}
        if profile_name:
            payload["profile_name"] = profile_name
        resp = httpx.post(
            f"{self.base_url}/api/assistant/link-whatsapp",
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def send_statement_whatsapp(self, client_id: int, phone_number: str = "",
                                language: str = "") -> dict:
        """Send a statement to a client via WhatsApp."""
        self._check_configured()
        payload = {"client_id": client_id}
        if phone_number:
            payload["phone_number"] = phone_number
        if language:
            payload["language"] = language
        resp = httpx.post(
            f"{self.base_url}/api/assistant/send-statement-whatsapp",
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
        )
        return resp.json()


# Module-level singleton
billing_client = BillingClient()
