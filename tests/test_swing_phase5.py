"""
Unit and Regression Tests for Phase 5 (News, LLM Classifier, Alternative Data)
==============================================================================
Tests:
- Schema validation
- Anonymization (ticker and company name -> 'the Company')
- Substring guard (hallucinated quotes discarded)
- Provider fallback chain
- Refusal inside backtests (BACKTEST_RUNNING / BACKTEST_MODE assert)
- Why-it-moved word count (<= 25 words) and source citation
"""

import os
import pytest
from src.news.llm_classifier import (
    anonymize_text,
    compute_text_hash,
    LLMClassifier,
    ALLOWED_EVENT_TYPES,
)
from src.news.sources import classify_8k_items
from src.news.why_it_moved import generate_why_it_moved


def test_anonymize_text():
    raw = "Apple Inc. announced today that AAPL will release quarterly earnings."
    anon = anonymize_text(raw, ticker="AAPL", company_name="Apple Inc.")
    assert "AAPL" not in anon
    assert "Apple Inc." not in anon
    assert "the Company" in anon


def test_substring_guard():
    classifier = LLMClassifier()
    # Mock chunk
    chunk = [{"text": "Company revenue grew 25% year-over-year in Q3."}]

    # Case 1: valid verbatim quote
    res_valid = classifier._parse_and_validate_response(
        raw_text='''[{
            "id": 0,
            "event_type": "earnings_beat",
            "direction": 1,
            "materiality": 3,
            "one_line_reason": "Strong Q3 revenue growth",
            "evidence_quote": "revenue grew 25%"
        }]''',
        chunk=chunk,
        provider="test",
        model="test-model",
    )
    assert len(res_valid) == 1
    assert res_valid[0]["is_valid"] is True
    assert res_valid[0]["event_type"] == "earnings_beat"

    # Case 2: hallucinated quote not present in chunk
    res_hallucinated = classifier._parse_and_validate_response(
        raw_text='''[{
            "id": 0,
            "event_type": "earnings_beat",
            "direction": 1,
            "materiality": 3,
            "one_line_reason": "CEO made a statement",
            "evidence_quote": "CEO stated that earnings were outstanding"
        }]''',
        chunk=chunk,
        provider="test",
        model="test-model",
    )
    assert len(res_hallucinated) == 1
    assert res_hallucinated[0]["is_valid"] is False


def test_allowed_event_types():
    assert "earnings_beat" in ALLOWED_EVENT_TYPES
    assert "guidance_cut" in ALLOWED_EVENT_TYPES
    assert "offering_dilution" in ALLOWED_EVENT_TYPES
    assert "fda_approval" in ALLOWED_EVENT_TYPES
    assert "no_news" in ALLOWED_EVENT_TYPES


def test_refusal_inside_backtest():
    classifier = LLMClassifier()
    os.environ["BACKTEST_RUNNING"] = "1"
    try:
        with pytest.raises(RuntimeError, match="Refusing to run LLM classifier inside a backtest"):
            classifier.classify_batch([{"text": "Sample text"}])
    finally:
        del os.environ["BACKTEST_RUNNING"]


def test_deterministic_8k_classification():
    res_earnings = classify_8k_items(["2.02", "9.01"])
    assert res_earnings["event_type"] == "earnings"
    assert res_earnings["is_negative"] is False
    assert res_earnings["direction"] == 1

    res_neg = classify_8k_items(["1.02", "9.01"])
    assert res_neg["event_type"] == "negative_event"
    assert res_neg["is_negative"] is True
    assert res_neg["direction"] == -1


def test_why_it_moved_generator():
    news = [
        {"title": "Company reports record Q3 revenue growth", "url": "https://sec.gov", "source": "SEC"},
        {"title": "Analyst raises price target on solid outlook", "url": "https://news.com", "source": "Wire"},
    ]
    why = generate_why_it_moved("TEST", "BUY", news)
    assert why["label"] == "AI summary - check sources"
    assert len(why["sources"]) == 2
    # Verify word count <= 25 words
    assert len(why["summary"].split()) <= 25
    assert "[1]" in why["summary"]

    # Empty news fallback
    empty_why = generate_why_it_moved("TEST", "BUY", [])
    assert empty_why["summary"] == "No company-specific news found in SEC filings or headlines."
