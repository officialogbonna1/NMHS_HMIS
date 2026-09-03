"""
AI agent integration points. Each function is a thin wrapper around the
Anthropic API — kept isolated here so agents can be swapped, mocked in
tests, or rate-limited independently of the rest of the app.

Suggested first agents for this HMIS:
  1. Note summarizer   — condenses a doctor's free-text note into a
                          structured summary for the patient's timeline.
  2. Interaction check — cross-references a new prescription against the
                          patient's active medication list and allergies,
                          flagging concerns before dispensing (advisory
                          only — never auto-blocks; a human decides).
  3. Reorder assistant — given low-stock items, drafts a supplier
                          reorder list with suggested quantities based on
                          recent consumption rate.

Wire your ANTHROPIC_API_KEY via environment variable, not in code.
"""
import os
import anthropic

_client = None


def get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def summarize_note(note_text: str) -> str:
    client = get_client()
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": (
                "Summarize this clinical note into 2-3 bullet points for a "
                "patient timeline. Be factual, no added interpretation.\n\n"
                f"{note_text}"
            ),
        }],
    )
    return resp.content[0].text


def check_interactions(new_drug: str, active_medications: list[str], allergies: list[str]) -> str:
    client = get_client()
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": (
                f"A doctor is about to prescribe: {new_drug}.\n"
                f"Patient's active medications: {', '.join(active_medications) or 'none'}.\n"
                f"Patient's known allergies: {', '.join(allergies) or 'none'}.\n"
                "Flag any notable interaction or allergy concern in 2-3 sentences. "
                "This is advisory only for a licensed clinician — do not give a "
                "final go/no-go, just the relevant concern(s) or 'No notable concerns flagged.'"
            ),
        }],
    )
    return resp.content[0].text


def draft_reorder_list(low_stock_items: list[dict]) -> str:
    client = get_client()
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": (
                "Draft a supplier reorder list from this low-stock data "
                f"(item, current qty, threshold): {low_stock_items}. "
                "Suggest a reorder quantity per item (roughly 2x threshold "
                "unless the data suggests otherwise). Output as a short table."
            ),
        }],
    )
    return resp.content[0].text
