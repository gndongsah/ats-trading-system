"""In-memory event comparisons; no broker-time or completeness inference."""

from src.webull_sandbox_cash_activities import (
    CashActivityHistory,
    _same_event,
    _validate_query_window,
)


def comparison_windows(start, end, overlap_start, enclosing_end):
    """Validate the four-window protocol before credentials/network access."""
    if overlap_start is None and enclosing_end is None:
        return None
    if None in (start, end, overlap_start, enclosing_end):
        raise ValueError("comparison requires all four window bounds")
    _validate_query_window(start, enclosing_end)
    _validate_query_window(overlap_start, end)
    if not start < overlap_start < end < enclosing_end:
        raise ValueError("comparison bounds must be strictly increasing")
    return (
        (start, end),
        (start, end),
        (overlap_start, enclosing_end),
        (start, enclosing_end),
    )


def compare_histories(left: CashActivityHistory, right: CashActivityHistory) -> dict:
    """Compare parsed values, ignoring observation time and page provenance.

    Caller must fetch both histories for the same selected API account. No IDs,
    hashes, amounts, symbols, raw timestamps or cursors leave this function.
    """
    if not left.pagination_complete or not right.pagination_complete:
        raise ValueError("comparison requires terminal request sequences")
    a = {event.event_id: event for event in left.events}
    b = {event.event_id: event for event in right.events}
    if len(a) != len(left.events) or len(b) != len(right.events):
        raise ValueError("comparison requires unique parsed event identities")
    common = a.keys() & b.keys()
    changed = sum(not _same_event(a[key], b[key]) for key in common)
    return {
        "left_event_count": len(a),
        "right_event_count": len(b),
        "left_only_count": len(a.keys() - b.keys()),
        "right_only_count": len(b.keys() - a.keys()),
        "shared_id_count": len(common),
        "changed_parsed_payload_count": changed,
        "unchanged_parsed_payload_count": len(common) - changed,
    }


def summarize_protocol(histories):
    a, repeat, overlap, enclosing = histories
    repeat_result = compare_histories(a, repeat)
    repeat_result["equal_parsed_event_sets"] = not any(
        repeat_result[key]
        for key in (
            "left_only_count",
            "right_only_count",
            "changed_parsed_payload_count",
        )
    )
    return {
        "repeat": repeat_result,
        "base_to_overlap": compare_histories(a, overlap),
        "base_to_enclosing": compare_histories(a, enclosing),
        "overlap_to_enclosing": compare_histories(overlap, enclosing),
        "history_coverage": "UNVERIFIED",
        "broker_time_semantics": "UNVERIFIED",
        "enforcement_eligible": False,
    }
