"""The bundled JSON-Schema subset validator.

`validate` is the install's health check, so this validator has to be right on a bare Python with
no third-party packages. The tests below pin the three things that make its findings useful: the
right instances pass, the wrong ones fail, and every failure carries a JSON pointer that leads to
the exact value.
"""

from __future__ import annotations

import json

import pytest

import schema as schema_module
from common import SCHEMA_VERSION, utc_now_iso
from memory import store

EXPECTED_SCHEMAS = {
    "learning_input",
    "backup_manifest",
    "checkpoint",
    "concept_catalog",
    "concept_graph",
    "concept_mastery",
    "concept_record",
    "graph_artifact",
    "learning_preferences",
    "misconceptions",
    "revision_queue",
    "session_event",
    "source_index",
    "subject_config",
    "subject_state",
    "workspace_state",
}


@pytest.fixture
def registry(workspace):
    return store.registry(workspace)


def pointers(findings):
    return [finding.pointer for finding in findings]


# ----------------------------------------------------------------------------- loading schemas


def test_all_fifteen_schemas_load_and_are_addressable_three_ways(registry):
    assert set(registry.names) == EXPECTED_SCHEMAS
    assert registry.has("concept_record")
    assert registry.has("concept_record.schema.json")
    assert registry.has("https://learningos.local/schemas/concept_record.schema.json")


def test_every_shipped_schema_stays_inside_the_supported_keyword_subset(registry):
    """An ignored keyword is an unenforced rule, so this must stay empty."""
    unsupported = {name: sorted(registry.unsupported_keywords(name)) for name in registry.names}
    assert not any(unsupported.values()), unsupported


def test_unknown_schema_name_raises_and_lists_the_known_names(registry):
    with pytest.raises(Exception) as excinfo:
        registry.get("not_a_schema")
    assert "not_a_schema" in str(excinfo.value)
    assert "concept_record" in excinfo.value.format_human()


# ------------------------------------------------------------------------------ concept records


def valid_record(concept_id: str = "algo:sorting") -> dict:
    return store.new_concept_record(concept_id, "Sorting", subject_id="algo")


def test_a_default_concept_record_is_valid(registry):
    assert registry.validate("concept_record", valid_record()) == []


def test_missing_required_field_points_at_that_field(registry):
    record = valid_record()
    del record["name"]
    findings = registry.validate("concept_record", record)
    assert pointers(findings) == ["/name"]
    assert "required" in findings[0].message


def test_bad_status_is_reported_with_the_allowed_values(registry):
    record = valid_record()
    record["status"] = "mastered"
    findings = registry.validate("concept_record", record)
    assert pointers(findings) == ["/status"]
    assert "provisionally_mastered" in findings[0].message


def test_concept_id_pattern_is_enforced(registry):
    record = valid_record()
    record["concept_id"] = "Algo:Sorting"
    findings = registry.validate("concept_record", record)
    assert "/concept_id" in pointers(findings)


def test_unknown_property_is_rejected_and_named(registry):
    record = valid_record()
    record["confidence"] = 0.5
    findings = registry.validate("concept_record", record)
    assert pointers(findings) == ["/confidence"]
    assert "not allowed" in findings[0].message


def test_nested_pointer_reaches_into_arrays(registry):
    record = valid_record()
    record["retention_evidence"] = [
        {"at": utc_now_iso(), "outcome": "independent_success"},
        {"at": utc_now_iso(), "outcome": "nearly"},
    ]
    findings = registry.validate("concept_record", record)
    assert pointers(findings) == ["/retention_evidence/1/outcome"]


def test_null_is_allowed_where_the_schema_says_so_and_bounds_still_apply(registry):
    record = valid_record()
    record["mastery_estimate"] = None
    assert registry.validate("concept_record", record) == []
    record["mastery_estimate"] = 1.5
    findings = registry.validate("concept_record", record)
    assert pointers(findings) == ["/mastery_estimate"]
    assert "maximum" in findings[0].message


def test_integer_and_boolean_are_not_interchangeable(registry):
    record = valid_record()
    record["independent_successes"] = True
    findings = registry.validate("concept_record", record)
    assert pointers(findings) == ["/independent_successes"]


def test_pointer_prefix_keeps_fragment_findings_locatable(registry):
    record = valid_record()
    record["status"] = "nope"
    findings = registry.validate(
        "concept_record", record, pointer_prefix="/concepts/algo:sorting"
    )
    assert pointers(findings) == ["/concepts/algo:sorting/status"]


def test_minlength_one_rejects_an_empty_string(registry):
    record = valid_record()
    record["name"] = ""
    findings = registry.validate("concept_record", record)
    assert pointers(findings) == ["/name"]
    assert "empty string" in findings[0].message


# ------------------------------------------------------------------- session events and oneOf


def valid_event(**overrides) -> dict:
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": "9f1c2d3e4f5061728394a5b6c7d8e9f0",
        "at": utc_now_iso(),
        "session_id": "2026-02-11-algo-01",
        "subject_id": "algo",
        "type": "answer_evaluated",
        "actor": "agent",
        "tool_version": "learningos 0.1.0",
        "concept_ids": ["algo:sorting"],
        "payload": {"outcome": "hinted_success", "independent": False, "hints_used": 1},
    }
    event.update(overrides)
    return event


def test_a_well_formed_event_matches_exactly_one_branch(registry):
    assert registry.validate("session_event", valid_event()) == []


def test_every_event_type_matches_exactly_one_branch(registry):
    minimal_payloads = {
        "checkpoint_written": {"checkpoint_id": "cp-1"},
        "diagnostic_probe": {"outcome": "partial"},
        "answer_evaluated": {"outcome": "fail", "independent": False, "hints_used": 0},
        "hint_given": {"level": 2},
        "misconception_detected": {"misconception_id": "algo:mc-001"},
        "misconception_resolved": {"misconception_id": "algo:mc-001"},
        "mastery_updated": {"concept_id": "algo:sorting", "status_after": "developing"},
        "retention_check": {"outcome": "independent_success"},
        "revision_scheduled": {"concept_id": "algo:sorting", "due_at": utc_now_iso()},
        "source_indexed": {
            "source_id": "notes-1a2b3c4d",
            "relative_path": "02_KNOWLEDGE/subjects/algo/source_materials/notes.md",
        },
        "graph_generated": {"kind": "learner_mastery"},
        "note_saved": {"relative_path": "03_MEMORY/session_logs/note.md"},
        "open_question_logged": {"question": "Why does merge sort need extra space?"},
        "memory_backup": {"backup_id": "memory-20260211T090000Z"},
        "memory_restore": {"backup_id": "memory-20260211T090000Z"},
    }
    from memory.events import EVENT_TYPES

    q = {"question_id":"q-1","exam_id":"entry","subject_id":"algo","concept_ids":["algo:sorting"],
         "prompt":"A source-backed synthetic question","format":"single","choices":[{"id":"A","text":"First"},{"id":"B","text":"Second"}],
         "answer":"A","solution":"Synthetic test explanation","marks":1,"negative_marks":0,"tolerance":0,
         "source_id":"notes-1a2b3c4d","locator":{"line":1},"origin":"generated","verification":"verified","difficulty":1,"family":"sorting","fingerprint":"abc"}
    minimal_payloads.update({
        "exam_configured":{"exam_id":"entry","name":"Entry","subject_ids":["algo"],"exam_date":None,"daily_minutes":45,"duration_minutes":60,"target":None,"rules_note":"Verify official rules."},
        "question_registered":q,
        "practice_started":{"run_id":"test-1","exam_id":"entry","mode":"mock","duration_minutes":60,"question_ids":["q-1"],"questions":[{**q,"version":"v1","previous_exposures":0}]},
        "practice_attempted":{"run_id":"test-1","question_id":"q-1","response":"A","seconds":30,"timing":"timer","skipped":False,"hints":0,"error":"unknown","confidence":None,"awarded":1,"question_version":"v1","marking":"verified_key"},
        "practice_paused":{"run_id":"test-1"},"practice_resumed":{"run_id":"test-1"},"practice_ended":{"run_id":"test-1"},
        "practice_error_classified":{"run_id":"test-1","question_id":"q-1","error":"reading","note":"Missed a condition.","basis":"confirmed"},
        "study_plan_created":{"exam_id":None,"budget_minutes":45,"reflection_minutes":5,"tasks":[],"unscheduled_tasks":0,"policy":"transparent-v1"}
    })

    learning_input = {"cycle_id":"cycle-1","concept_id":"algo:sorting","task_id":"task-1","prompt":"Explain and apply.","response":"Actual synthetic response.","feedback":"Specific feedback.","dimensions":{"explanation":{"outcome":"pass","rationale":"Correct mechanism."}},"hints":0,"solution_seen":False,"novelty":"variant","difficulty":2,"next_action":"Try a delayed application.","evaluation":"checked"}
    minimal_payloads.update({
        "learning_goal_set":{"purpose":"Understand and apply sorting","mode":"learn","minutes":45},
        "learning_cycle_saved":{"input":learning_input,"submitted_hash":"abc","outcome":"partial","evidence_event_id":"e1","mastery_event_id":"e2","revision_event_id":"e3","record_before":{},"record_after":{},"review_before":None,"review_after":{},"open_misconceptions":0,"blocks_promotion":False},
        "learning_cycle_applied":{"cycle_id":"cycle-1"},
        "learning_note_saved":{"note_id":"note-1","title":"A question","content":"Actual question text.","kind":"question"},
        "session_close_requested":{"outcome":"Practised sorting.","next_action":"Retrieve a variant.","duration_minutes":None},
        "session_close_completed":{"next_action":"Retrieve a variant."}
    })

    for event_type in EVENT_TYPES:
        event = valid_event(type=event_type, payload=minimal_payloads.get(event_type, {}))
        assert registry.validate("session_event", event) == [], event_type


def test_a_bad_payload_names_the_matching_branch_not_all_eighteen(registry):
    event = valid_event(payload={"outcome": "hinted_success"})
    findings = registry.validate("session_event", event)
    assert len(findings) == 1
    message = findings[0].message
    assert "answer evaluated" in message, message
    assert "independent" in message and "hints_used" in message, message


def test_an_unknown_event_type_is_reported_at_the_type_pointer(registry):
    event = valid_event(type="learner_sighed")
    findings = registry.validate("session_event", event)
    assert "/type" in pointers(findings)


def test_a_payload_key_outside_the_branch_is_rejected(registry):
    event = valid_event()
    event["payload"]["mood"] = "cheerful"
    findings = registry.validate("session_event", event)
    assert len(findings) == 1
    assert "mood" in findings[0].message


def test_event_id_must_be_uuid4_hex_without_dashes(registry):
    event = valid_event(event_id="9f1c2d3e-4f50-6172-8394-a5b6c7d8e9f0")
    findings = registry.validate("session_event", event)
    assert "/event_id" in pointers(findings)


def test_relative_path_pattern_blocks_a_stored_absolute_path(registry):
    event = valid_event(
        type="note_saved",
        payload={"relative_path": "e:/AI-KIRO-CODEX/LearningOS/03_MEMORY/note.md"},
    )
    findings = registry.validate("session_event", event)
    assert len(findings) == 1, findings
    assert "relative_path" in findings[0].message or "/payload" in str(findings[0].pointer)


# ------------------------------------------------------------- keywords exercised via a fixture


CUSTOM_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "$id": "https://learningos.local/schemas/custom.schema.json",
    "title": "Custom",
    "type": "object",
    "required": ["when"],
    "properties": {
        "when": {"type": "string", "format": "date-time"},
        "either": {"anyOf": [{"type": "integer"}, {"type": "string", "minLength": 2}]},
        "tags": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string"}},
        "extras": {"type": "object", "additionalProperties": {"type": "integer"}},
        "ref": {"$ref": "#/$defs/positive"},
    },
    "additionalProperties": False,
    "$defs": {"positive": {"type": "number", "exclusiveMinimum": 0}},
}


@pytest.fixture
def custom_registry(tmp_path):
    directory = tmp_path / "schemas"
    directory.mkdir()
    (directory / "custom.schema.json").write_text(
        json.dumps(CUSTOM_SCHEMA), encoding="utf-8"
    )
    return schema_module.SchemaRegistry(directory).load()


def test_date_time_format_is_checked_strictly(custom_registry):
    assert custom_registry.validate("custom", {"when": "2026-02-11T09:14:00Z"}) == []
    findings = custom_registry.validate("custom", {"when": "2026-02-11 09:14"})
    assert pointers(findings) == ["/when"]


def test_anyof_reports_the_closest_branch(custom_registry):
    instance = {"when": "2026-02-11T09:14:00Z", "either": "x"}
    findings = custom_registry.validate("custom", instance)
    assert pointers(findings) == ["/either"]
    assert "anyOf" in findings[0].message


def test_array_constraints_and_additional_properties_as_a_schema(custom_registry):
    base = {"when": "2026-02-11T09:14:00Z"}
    assert custom_registry.validate("custom", {**base, "tags": ["a"]}) == []
    assert pointers(custom_registry.validate("custom", {**base, "tags": []})) == ["/tags"]
    assert pointers(custom_registry.validate("custom", {**base, "tags": ["a", "a"]})) == ["/tags"]
    assert custom_registry.validate("custom", {**base, "extras": {"n": 1}}) == []
    assert pointers(custom_registry.validate("custom", {**base, "extras": {"n": "1"}})) == [
        "/extras/n"
    ]


def test_local_ref_and_exclusive_minimum(custom_registry):
    base = {"when": "2026-02-11T09:14:00Z"}
    assert custom_registry.validate("custom", {**base, "ref": 0.5}) == []
    assert pointers(custom_registry.validate("custom", {**base, "ref": 0})) == ["/ref"]


def test_unsupported_keyword_is_reported_rather_than_silently_ignored(tmp_path):
    directory = tmp_path / "schemas"
    directory.mkdir()
    (directory / "odd.schema.json").write_text(
        json.dumps({"type": "object", "patternProperties": {"^a": {"type": "string"}}}),
        encoding="utf-8",
    )
    registry = schema_module.SchemaRegistry(directory).load()
    assert registry.unsupported_keywords("odd") == {"patternProperties"}


# --------------------------------------------------------------------- optional strict crosscheck


def test_strict_mode_agrees_with_the_bundled_validator_on_valid_data(registry):
    assert registry.validate("session_event", valid_event(), strict=True) == []
    assert registry.validate("concept_record", valid_record(), strict=True) == []


@pytest.mark.skipif(
    not schema_module.jsonschema_available(), reason="jsonschema is an optional dependency"
)
def test_strict_mode_finds_the_same_errors_as_jsonschema(registry):
    record = valid_record()
    record["status"] = "nope"
    findings = registry.validate("concept_record", record, strict=True)
    assert any(finding.pointer == "/status" for finding in findings)


def test_missing_jsonschema_never_raises(registry, monkeypatch):
    """The absence of an optional dependency must be invisible, not fatal."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "jsonschema":
            raise ImportError("simulated missing dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert registry.validate("concept_record", valid_record(), strict=True) == []


# --------------------------------------------------------------------- the shipped templates


@pytest.mark.parametrize(
    "relative,schema_name",
    [
        ("03_MEMORY/global/workspace_state.json", "workspace_state"),
        ("03_MEMORY/global/learning_preferences.json", "learning_preferences"),
        ("03_MEMORY/subjects/_template/subject_state.json", "subject_state"),
        ("03_MEMORY/subjects/_template/concept_mastery.json", "concept_mastery"),
        ("03_MEMORY/subjects/_template/concept_graph.json", "concept_graph"),
        ("03_MEMORY/subjects/_template/misconceptions.json", "misconceptions"),
        ("03_MEMORY/subjects/_template/revision_queue.json", "revision_queue"),
        ("02_KNOWLEDGE/subjects/_template/subject_config.json", "subject_config"),
        ("02_KNOWLEDGE/subjects/_template/concept_catalog.json", "concept_catalog"),
        ("02_KNOWLEDGE/subjects/_template/source_index.json", "source_index"),
        ("01_AGENTS_AND_SKILLS/templates/memory_templates/checkpoint.json", "checkpoint"),
    ],
)
def test_shipped_templates_validate_as_written(ws, registry, relative, schema_name):
    data = json.loads((ws / relative).read_text(encoding="utf-8"))
    assert registry.validate(schema_name, data, path=relative) == []
