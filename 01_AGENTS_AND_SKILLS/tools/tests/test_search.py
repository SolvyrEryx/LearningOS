"""Searching the learner's own history.

`search-memory` has to find the same thing whether it reads the log directly or reads the optional
SQLite cache — otherwise the cache is a bug generator rather than a speed-up. These tests pin that
equality down, along with the filters, the zero-hit contract (exit 0, not an error) and the
"local only" promise: a search that needed a network call or an embedding model would fail on a
train, which is exactly where revision happens.
"""

from __future__ import annotations

import json

import pytest

from common import EXIT_OK, EXIT_USAGE, Workspace
from memory import events as events_mod
from memory import mastery
from memory import search as search_mod
from memory import store

SESSION = "2026-02-11-math2-01"
CONCEPT = "math2:fourier-series"


@pytest.fixture
def history(ws, cli, seed_subject) -> Workspace:
    """Two subjects with real evidence: events, concept records and a misconception."""
    seed_subject("math2", name="Mathematics 2", slugs=("fourier-series", "periodic-functions"))
    seed_subject("thermo", name="Thermodynamics", slugs=("entropy",))
    workspace = Workspace.at(ws)

    events_mod.append_event(
        workspace,
        type="session_started",
        session_id=SESSION,
        subject_id="math2",
        at="2026-02-01T09:00:00Z",
        payload={"plan": "diagnose the a0 term, then one unaided derivation"},
    )
    events_mod.append_event(
        workspace,
        type="answer_evaluated",
        session_id=SESSION,
        subject_id="math2",
        concept_ids=[CONCEPT],
        at="2026-02-02T09:00:00Z",
        payload={
            "outcome": "hinted_success",
            "independent": False,
            "hints_used": 1,
            "notes": "needed the orthogonality reminder before the a0 integral",
        },
    )
    events_mod.append_event(
        workspace,
        type="answer_evaluated",
        session_id=SESSION,
        subject_id="math2",
        concept_ids=[CONCEPT],
        at="2026-02-09T09:00:00Z",
        payload={
            "outcome": "independent_success",
            "independent": True,
            "hints_used": 0,
            "notes": "derived the a0 normalising factor unaided",
        },
    )
    events_mod.append_event(
        workspace,
        type="retention_check",
        session_id="2026-02-11-thermo-01",
        subject_id="thermo",
        concept_ids=["thermo:entropy"],
        at="2026-02-11T09:00:00Z",
        payload={"outcome": "partial", "closed_book": True, "notes": "entropy sign confusion"},
    )

    record = store.get_concept_record(workspace, "math2", CONCEPT)
    record["next_action"] = "Re-derive a0 for a square wave with no notes."
    record["recurring_mistakes"] = [
        {
            "pattern": "drops the 1/2 on the a0 term",
            "count": 2,
            "first_seen_at": "2026-02-02T09:00:00Z",
            "last_seen_at": "2026-02-09T09:00:00Z",
        }
    ]
    record["helpful_interventions"] = ["worked-example-fading"]
    store.put_concept_record(workspace, "math2", record)

    misconceptions, entry = mastery.apply_misconception(
        store.load_misconceptions(workspace, "math2"),
        subject_id="math2",
        description="believes the mean value of the signal is a0 rather than a0/2",
        concept_ids=[CONCEPT],
        detection_signals=["writes a0 straight into the series"],
        status="open",
        at="2026-02-02T09:00:00Z",
    )
    store.save_misconceptions(workspace, "math2", misconceptions)
    record = store.get_concept_record(workspace, "math2", CONCEPT)
    record["misconception_ids"] = [entry["misconception_id"]]
    store.put_concept_record(workspace, "math2", record)
    return workspace


def hits(result_json: str) -> list[dict]:
    return json.loads(result_json)["hits"]


# ----------------------------------------------------------------------------- what it can find


def test_it_matches_an_event_payload_note(history, cli):
    result = cli("search-memory", "orthogonality", "--json")
    assert result.code == EXIT_OK, result.text
    found = hits(result.out)
    assert len(found) == 1
    assert found[0]["type"] == "answer_evaluated"
    assert found[0]["concept_ids"] == [CONCEPT]
    assert "orthogonality" in found[0]["snippet"]
    assert found[0]["path"].startswith("03_MEMORY/events/session_events.jsonl:")


def test_it_matches_a_concept_record(history, cli):
    result = cli("search-memory", "square wave", "--json")
    found = hits(result.out)
    assert [hit["kind"] for hit in found] == ["concept"]
    assert found[0]["type"] == "concept_record"
    assert found[0]["concept_ids"] == [CONCEPT]
    assert found[0]["path"] == "03_MEMORY/subjects/math2/concept_mastery.json"


def test_it_matches_a_recurring_mistake_pattern(history, cli):
    result = cli("search-memory", "drops the 1/2", "--json")
    found = hits(result.out)
    assert found and found[0]["kind"] == "concept"


def test_it_matches_misconception_text(history, cli):
    result = cli("search-memory", "mean value of the signal", "--json")
    found = hits(result.out)
    assert found and found[0]["kind"] == "misconception"
    assert found[0]["type"] == "math2:mc-001"
    assert found[0]["path"] == "03_MEMORY/subjects/math2/misconceptions.json"


def test_it_matches_across_all_three_layers_at_once(history, cli):
    result = cli("search-memory", "a0", "--json")
    found = hits(result.out)
    kinds = {hit["kind"] for hit in found}
    assert kinds == {"event", "concept", "misconception"}


def test_all_tokens_must_appear_for_a_multi_word_query(history, cli):
    assert hits(cli("search-memory", "entropy sign", "--json").out), "both tokens present"
    assert not hits(cli("search-memory", "entropy orthogonality", "--json").out)


# ----------------------------------------------------------------------------------- filtering


def test_subject_filter_keeps_the_subjects_apart(history, cli):
    found = hits(cli("search-memory", "notes", "--subject", "math2", "--json").out)
    assert found
    assert {hit["subject_id"] for hit in found} == {"math2"}
    assert not hits(cli("search-memory", "orthogonality", "--subject", "thermo", "--json").out)


def test_type_filter_restricts_to_events_of_that_type(history, cli):
    found = hits(cli("search-memory", "a0", "--type", "answer_evaluated", "--json").out)
    assert found
    assert {hit["type"] for hit in found} == {"answer_evaluated"}
    assert all(hit["kind"] == "event" for hit in found), (
        "concept and misconception records have no event type, so a type filter excludes them"
    )


def test_since_filter_drops_older_evidence(history, cli):
    all_events = hits(cli("search-memory", "a0", "--type", "answer_evaluated", "--json").out)
    recent = hits(
        cli(
            "search-memory",
            "a0",
            "--type",
            "answer_evaluated",
            "--since",
            "2026-02-05",
            "--json",
        ).out
    )
    assert len(all_events) == 2
    assert len(recent) == 1
    assert recent[0]["at"] == "2026-02-09T09:00:00Z"


def test_limit_caps_the_hits(history, cli):
    assert len(hits(cli("search-memory", "a0", "--limit", "1", "--json").out)) == 1


def test_results_are_newest_first(history, cli):
    found = hits(cli("search-memory", "a0", "--type", "answer_evaluated", "--json").out)
    stamps = [hit["at"] for hit in found]
    assert stamps == sorted(stamps, reverse=True)


# -------------------------------------------------------------------------- contracts and errors


def test_zero_hits_is_not_an_error(history, cli):
    result = cli("search-memory", "quantum chromodynamics")
    assert result.code == EXIT_OK
    assert "no matches" in result.out


def test_a_malformed_since_is_a_usage_error(history, cli):
    result = cli("search-memory", "a0", "--since", "last Tuesday")
    assert result.code == EXIT_USAGE
    assert "--since" in result.err
    assert "YYYY-MM-DD" in result.err


def test_an_unknown_event_type_is_a_usage_error(history, cli):
    result = cli("search-memory", "a0", "--type", "learner_was_tired")
    assert result.code == EXIT_USAGE
    assert "answer_evaluated" in result.err, "the message lists the valid types"


def test_a_zero_limit_is_a_usage_error(history, cli):
    assert cli("search-memory", "a0", "--limit", "0").code == EXIT_USAGE


def test_the_human_report_prints_the_documented_columns(history, cli):
    result = cli("search-memory", "orthogonality")
    assert result.code == EXIT_OK
    assert "at" in result.out and "subject" in result.out and "snippet" in result.out
    assert "answer_evaluated" in result.out
    assert CONCEPT in result.out


# ------------------------------------------------------------------------------- the FTS5 cache


def test_reindex_then_query_returns_exactly_the_scan_results(history, cli):
    for query in ("a0", "orthogonality", "square wave", "mean value of the signal"):
        scanned = hits(cli("search-memory", query, "--json").out)
        result = cli("search-memory", query, "--reindex", "--json")
        assert result.code == EXIT_OK, result.text
        assert hits(result.out) == scanned, f"the cache changed the answer for {query!r}"


def test_the_index_is_used_when_fresh_and_bypassed_when_stale(history, cli):
    assert cli("search-memory", "a0", "--reindex", "--json").code == EXIT_OK
    if not search_mod.fts5_available():  # pragma: no cover - FTS5 is present in this environment
        pytest.skip("SQLite has no FTS5 here; the scan path is the only path")
    cache = search_mod.index_path(history)
    assert cache.is_file()
    assert search_mod.index_is_fresh(history)
    assert json.loads(cli("search-memory", "a0", "--json").out)["engine"] == "index"

    events_mod.append_event(
        history,
        type="note_saved",
        session_id=SESSION,
        subject_id="math2",
        payload={"relative_path": "03_MEMORY/session_logs/scratch.md", "title": "a0 revisited"},
    )
    assert not search_mod.index_is_fresh(history), "new evidence makes the cache stale"
    payload = json.loads(cli("search-memory", "a0", "--json").out)
    assert payload["engine"] == "scan"
    assert any(hit["type"] == "note_saved" for hit in payload["hits"]), (
        "a stale cache is bypassed, so the newest evidence is still found"
    )


def test_deleting_the_cache_loses_nothing(history, cli):
    assert cli("search-memory", "a0", "--reindex").code == EXIT_OK
    cache = search_mod.index_path(history)
    if cache.is_file():
        cache.unlink()
    result = cli("search-memory", "orthogonality", "--json")
    assert result.code == EXIT_OK
    assert len(hits(result.out)) == 1


def test_searching_never_writes_to_memory(history, cli, hash_tree):
    before = hash_tree(history.root / "03_MEMORY")
    assert cli("search-memory", "a0").code == EXIT_OK
    assert hash_tree(history.root / "03_MEMORY") == before


def test_a_corrupt_log_line_is_skipped_and_counted(history, cli):
    log = history.root / "03_MEMORY/events/session_events.jsonl"
    with open(log, "a", encoding="utf-8", newline="\n") as handle:
        handle.write("{this is not json}\n")
    payload = json.loads(cli("search-memory", "orthogonality", "--json").out)
    assert payload["unreadable_log_lines"] == 1
    assert len(payload["hits"]) == 1, "one bad line does not stop the search"
