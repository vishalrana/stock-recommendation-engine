"""
LLM News and Event Classifier (Shadow Layer)
============================================
Strict structured classification for news headlines, SEC filings, and wire releases.

Pre-registered constraints:
- Provider fallback chain: Groq -> Gemini -> local/none.
- Strict JSON schema: event_type, direction (-1|0|1), materiality (1|2|3),
  one_line_reason (<=140 chars), evidence_quote (<=200 chars verbatim substring).
- Anonymization: replaces company name and ticker with "the Company".
- Verification: discards labels whose evidence_quote is not an exact substring of the raw input.
- Refuses to run in backtest mode (asserts on env flag BACKTEST_RUNNING).
- Cacheable by text_hash.
"""

import os
import re
import json
import hashlib
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1.0"

ALLOWED_EVENT_TYPES = {
    "earnings_beat",
    "earnings_miss",
    "guidance_raise",
    "guidance_cut",
    "offering_dilution",
    "buyback",
    "mna_target",
    "mna_acquirer",
    "fda_approval",
    "fda_rejection_crl",
    "litigation_regulatory",
    "exec_departure",
    "restatement_auditor",
    "going_concern_delisting",
    "contract_win",
    "analyst_upgrade",
    "analyst_downgrade",
    "other",
    "no_news",
}

DEFAULT_GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
DEFAULT_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")


def compute_text_hash(text: str) -> str:
    """Deterministic MD5 / SHA256 text hash for caching."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def anonymize_text(text: str, ticker: Optional[str] = None, company_name: Optional[str] = None) -> str:
    """Replace company name and ticker symbol with 'the Company'."""
    anonymized = text
    if ticker:
        anonymized = re.sub(rf"(?<!\w){re.escape(ticker)}(?!\w)", "the Company", anonymized, flags=re.IGNORECASE)
    if company_name:
        anonymized = re.sub(rf"(?<!\w){re.escape(company_name)}(?!\w)", "the Company", anonymized, flags=re.IGNORECASE)
    return anonymized


def build_system_prompt() -> str:
    return (
        "You are an objective financial event classifier. "
        "Analyze the provided financial text batch and categorize each item into strict JSON. "
        "Return ONLY a JSON array of objects, one per item, matching the schema exactly:\n"
        "[\n"
        "  {\n"
        "    \"id\": int,\n"
        "    \"event_type\": one of [earnings_beat, earnings_miss, guidance_raise, guidance_cut, "
        "offering_dilution, buyback, mna_target, mna_acquirer, fda_approval, fda_rejection_crl, "
        "litigation_regulatory, exec_departure, restatement_auditor, going_concern_delisting, "
        "contract_win, analyst_upgrade, analyst_downgrade, other, no_news],\n"
        "    \"direction\": -1 (negative), 0 (neutral), or 1 (positive),\n"
        "    \"materiality\": 1 (low/routine), 2 (moderate), or 3 (high/catalyst),\n"
        "    \"one_line_reason\": \"brief explanation <= 140 chars\",\n"
        "    \"evidence_quote\": \"verbatim substring copied from input <= 200 chars\"\n"
        "  }\n"
        "]\n"
        "CRITICAL: evidence_quote MUST be an exact verbatim substring from the input text. Do not paraphrase."
    )


class LLMClassifier:
    """
    LLM News Classifier with fallback chain: Groq -> Gemini -> None.
    Caches by text_hash and verifies evidence_quote substring.
    """

    def __init__(self, groq_api_key: Optional[str] = None, gemini_api_key: Optional[str] = None):
        self.groq_api_key = groq_api_key or os.environ.get("GROQ_API_KEY")
        self.gemini_api_key = gemini_api_key or os.environ.get("GEMINI_API_KEY")
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.request_count = 0
        self.max_requests_per_run = 800

    def assert_not_in_backtest(self) -> None:
        """The classifier must refuse to run inside any backtest."""
        if (
            os.environ.get("BACKTEST_RUNNING") == "1"
            or os.environ.get("SWING_BACKTEST_RUNNING") == "1"
            or os.environ.get("BACKTEST_MODE") == "1"
        ):
            raise RuntimeError(
                "Refusing to run LLM classifier inside a backtest! "
                "LLM labels on historical data before model training cutoffs create severe look-ahead bias."
            )

    def classify_batch(
        self, items: List[Dict[str, Any]]
    ) -> List[Optional[Dict[str, Any]]]:
        """
        Classifies a batch of items: [{'id': ..., 'ticker': ..., 'text': ...}].
        Returns list of structured label dicts (or None if invalid/unavailable).
        """
        self.assert_not_in_backtest()

        if not items:
            return []

        results: List[Optional[Dict[str, Any]]] = [None] * len(items)
        to_classify = []
        to_classify_indices = []

        for idx, itm in enumerate(items):
            text = itm.get("text", "")
            thash = compute_text_hash(text)
            if thash in self.cache:
                results[idx] = self.cache[thash]
            else:
                to_classify.append(itm)
                to_classify_indices.append(idx)

        if not to_classify:
            return results

        # Process in batches of 10 to 20
        batch_size = 15
        for i in range(0, len(to_classify), batch_size):
            if self.request_count >= self.max_requests_per_run:
                logger.warning("LLM daily request limit (800) reached. Degraded to unavailable.")
                break

            chunk = to_classify[i : i + batch_size]
            chunk_indices = to_classify_indices[i : i + batch_size]

            chunk_results = self._call_provider_chain(chunk)
            for sub_idx, res in enumerate(chunk_results):
                orig_idx = chunk_indices[sub_idx]
                results[orig_idx] = res
                if res and res.get("is_valid"):
                    thash = compute_text_hash(chunk[sub_idx].get("text", ""))
                    self.cache[thash] = res

        return results

    def _call_provider_chain(self, chunk: List[Dict[str, Any]]) -> List[Optional[Dict[str, Any]]]:
        """Try Groq -> Gemini -> local fallback."""
        # 1. Groq
        if self.groq_api_key:
            try:
                res = self._call_groq(chunk)
                if res:
                    return res
            except Exception as e:
                logger.warning(f"Groq classification failed: {e}. Falling back to Gemini.")

        # 2. Gemini
        if self.gemini_api_key:
            try:
                res = self._call_gemini(chunk)
                if res:
                    return res
            except Exception as e:
                logger.warning(f"Gemini classification failed: {e}. Falling back.")

        # 3. Degrade gracefully to unavailable
        return [None] * len(chunk)

    def _call_groq(self, chunk: List[Dict[str, Any]]) -> Optional[List[Optional[Dict[str, Any]]]]:
        import requests

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.groq_api_key}",
            "Content-Type": "application/json",
        }

        # Anonymize each item
        anonymized_payload = []
        for idx, itm in enumerate(chunk):
            text = itm.get("text", "")
            ticker = itm.get("ticker", "")
            company = itm.get("company_name", "")
            anon = anonymize_text(text, ticker=ticker, company_name=company)
            anonymized_payload.append({"id": idx, "text": anon})

        user_content = json.dumps(anonymized_payload)
        data = {
            "model": DEFAULT_GROQ_MODEL,
            "messages": [
                {"role": "system", "content": build_system_prompt()},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }

        self.request_count += 1
        resp = requests.post(url, headers=headers, json=data, timeout=20)
        if resp.status_code != 200:
            logger.warning(f"Groq API error {resp.status_code}: {resp.text}")
            return None

        body = resp.json()
        raw_text = body["choices"][0]["message"]["content"]
        return self._parse_and_validate_response(
            raw_text, chunk, provider="groq", model=DEFAULT_GROQ_MODEL
        )

    def _call_gemini(self, chunk: List[Dict[str, Any]]) -> Optional[List[Optional[Dict[str, Any]]]]:
        import requests

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{DEFAULT_GEMINI_MODEL}:generateContent?key={self.gemini_api_key}"
        headers = {"Content-Type": "application/json"}

        anonymized_payload = []
        for idx, itm in enumerate(chunk):
            text = itm.get("text", "")
            ticker = itm.get("ticker", "")
            company = itm.get("company_name", "")
            anon = anonymize_text(text, ticker=ticker, company_name=company)
            anonymized_payload.append({"id": idx, "text": anon})

        prompt = (
            build_system_prompt()
            + "\n\nAnalyze the following items and respond with strict JSON array:\n"
            + json.dumps(anonymized_payload)
        )

        data = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
            },
        }

        self.request_count += 1
        resp = requests.post(url, headers=headers, json=data, timeout=20)
        if resp.status_code != 200:
            logger.warning(f"Gemini API error {resp.status_code}: {resp.text}")
            return None

        body = resp.json()
        candidates = body.get("candidates", [])
        if not candidates:
            return None
        raw_text = candidates[0]["content"]["parts"][0]["text"]
        return self._parse_and_validate_response(
            raw_text, chunk, provider="gemini", model=DEFAULT_GEMINI_MODEL
        )

    def _parse_and_validate_response(
        self,
        raw_text: str,
        chunk: List[Dict[str, Any]],
        provider: str,
        model: str,
    ) -> List[Optional[Dict[str, Any]]]:
        """
        Parses JSON array and enforces:
        1. event_type in ALLOWED_EVENT_TYPES
        2. direction in [-1, 0, 1]
        3. materiality in [1, 2, 3]
        4. one_line_reason <= 140 chars
        5. evidence_quote is a verbatim substring of the raw input.
        """
        parsed_list = []
        try:
            val = json.loads(raw_text)
            if isinstance(val, list):
                parsed_list = val
            elif isinstance(val, dict):
                # Check for root wrapper
                for v in val.values():
                    if isinstance(v, list):
                        parsed_list = v
                        break
                if not parsed_list and "event_type" in val:
                    parsed_list = [val]
        except Exception as e:
            logger.warning(f"Failed to parse LLM JSON: {e}")
            return [None] * len(chunk)

        out: List[Optional[Dict[str, Any]]] = [None] * len(chunk)
        for entry in parsed_list:
            if not isinstance(entry, dict):
                continue
            item_id = entry.get("id", 0)
            if not (0 <= item_id < len(chunk)):
                continue

            orig_item = chunk[item_id]
            orig_text = orig_item.get("text", "")
            raw_quote = str(entry.get("evidence_quote", "")).strip()

            # Hallucination guard: evidence_quote MUST be a substring of the raw input
            # (or anonymized input)
            is_valid = True
            if not raw_quote:
                is_valid = False
            elif raw_quote.lower() not in orig_text.lower():
                anon_text = anonymize_text(orig_text, orig_item.get("ticker"))
                if raw_quote.lower() not in anon_text.lower():
                    logger.info(
                        f"Discarding hallucinated label: quote '{raw_quote[:40]}' not in input"
                    )
                    is_valid = False

            event_type = entry.get("event_type", "other")
            if event_type not in ALLOWED_EVENT_TYPES:
                event_type = "other"

            try:
                direction = int(entry.get("direction", 0))
                if direction not in (-1, 0, 1):
                    direction = 0
            except (ValueError, TypeError):
                direction = 0

            try:
                materiality = int(entry.get("materiality", 1))
                if materiality not in (1, 2, 3):
                    materiality = 1
            except (ValueError, TypeError):
                materiality = 1

            reason = str(entry.get("one_line_reason", ""))[:140]

            out[item_id] = {
                "provider": provider,
                "model": model,
                "prompt_version": PROMPT_VERSION,
                "event_type": event_type,
                "direction": direction,
                "materiality": materiality,
                "one_line_reason": reason,
                "evidence_quote": raw_quote[:200],
                "raw_output": entry,
                "is_valid": is_valid,
            }

        return out
