"""Contract tests for private PM continuity across native roots."""

import json
import sqlite3
import uuid

import pytest

from ironclaude.worker_pm_handoff import snapshot_pm_state, prepare_pm_handoff, readback_pm_handoff


SCHEMA = """
CREATE TABLE sessions (terminal_session TEXT PRIMARY KEY, professional_mode TEXT NOT NULL DEFAULT 'undecided', workflow_stage TEXT NOT NULL DEFAULT 'idle', active_skill TEXT, brainstorming_active INTEGER NOT NULL DEFAULT 0, plan_name TEXT, plan_json TEXT, current_wave INTEGER NOT NULL DEFAULT 0, review_pending INTEGER NOT NULL DEFAULT 0, review_block_count INTEGER NOT NULL DEFAULT 0, plan_lineage INTEGER NOT NULL DEFAULT 0, inherit_review INTEGER NOT NULL DEFAULT 0, circuit_breaker INTEGER NOT NULL DEFAULT 0, memory_search_required INTEGER NOT NULL DEFAULT 0, testing_theatre_checked INTEGER NOT NULL DEFAULT 0, project_hash TEXT, updated_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE wave_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, terminal_session TEXT NOT NULL, task_id INTEGER NOT NULL, wave_number INTEGER NOT NULL, task_name TEXT NOT NULL, description TEXT, allowed_files TEXT, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE plan_history (id INTEGER PRIMARY KEY AUTOINCREMENT, terminal_session TEXT NOT NULL, plan_name TEXT NOT NULL, design_file TEXT NOT NULL, completed_tasks TEXT, total_tasks INTEGER NOT NULL, retreat_reason TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, terminal_session TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, old_value TEXT, new_value TEXT, context TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE registered_designs (design_file TEXT PRIMARY KEY, registered_at TEXT NOT NULL DEFAULT (datetime('now')), terminal_session TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE subagent_sessions (child_session TEXT PRIMARY KEY, parent_session TEXT NOT NULL, task_number INTEGER, created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE pending_subagent_parents (id INTEGER PRIMARY KEY AUTOINCREMENT, parent_session TEXT NOT NULL, task_number INTEGER, created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE review_grades (id INTEGER PRIMARY KEY AUTOINCREMENT, terminal_session TEXT NOT NULL, wave_number INTEGER NOT NULL, task_ids TEXT NOT NULL, grade TEXT NOT NULL, task_boundary INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE tier_up_reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, terminal_session TEXT NOT NULL, plan_lineage INTEGER NOT NULL DEFAULT 0, plan_hash TEXT NOT NULL, reviewer_model TEXT NOT NULL, verdict TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TRIGGER prevent_duplicate_blind_tier_up_review BEFORE INSERT ON tier_up_reviews WHEN NEW.verdict IN ('SOLID','HAS-ISSUES','top-tier-self') AND EXISTS (SELECT 1 FROM tier_up_reviews WHERE terminal_session=NEW.terminal_session AND plan_lineage=NEW.plan_lineage AND verdict IN ('SOLID','HAS-ISSUES','top-tier-self')) BEGIN SELECT RAISE(ABORT,'one blind review per lineage'); END;
"""


@pytest.fixture
def state(tmp_path):
    path = tmp_path / "pm.db"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    source, target = str(uuid.uuid4()), str(uuid.uuid4())
    plan = json.dumps({"name":"GM Core","goal":"Continue GM Core","design_file":"docs/plans/2026-10-04-gm-core-r3-r2-fix-loop3-design.md","tasks":[{"id":number,"allowed_files":["file.py"]} for number in range(1,8)]}, separators=(",",":"))
    db.execute("INSERT INTO sessions (terminal_session,professional_mode,workflow_stage,active_skill,plan_name,plan_json,current_wave,review_pending,review_block_count,plan_lineage,inherit_review,circuit_breaker,memory_search_required,testing_theatre_checked,project_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (source,"on","reviewing","code-review","GM Core",plan,3,1,4,2,1,1,1,1,"project"))
    db.execute("INSERT INTO sessions (terminal_session,professional_mode,project_hash) VALUES (?,?,?)", (target,"on","project"))
    for number, status in enumerate(["review_passed"] * 5 + ["submitted", "pending"], 1):
        db.execute("INSERT INTO wave_tasks (terminal_session,task_id,wave_number,task_name,status) VALUES (?,?,?,?,?)", (source,number,1 if number<4 else (2 if number<6 else 3),f"Task {number}",status))
    db.execute("INSERT INTO review_grades (terminal_session,wave_number,task_ids,grade,task_boundary,created_at) VALUES (?,?,?,?,?,?)", (source,1,"[1,2,3]","A",1,"2026-10-04 01:00:00"))
    db.execute("INSERT INTO review_grades (terminal_session,wave_number,task_ids,grade,task_boundary,created_at) VALUES (?,?,?,?,?,?)", (source,3,"[6]","A",1,"2026-10-04 02:00:00"))
    db.execute("INSERT INTO tier_up_reviews (terminal_session,plan_lineage,plan_hash,reviewer_model,verdict,created_at) VALUES (?,?,?,?,?,?)", (source,2,"oldhash","gpt-6-astra","HAS-ISSUES","2026-10-04 01:00:00"))
    db.execute("INSERT INTO tier_up_reviews (terminal_session,plan_lineage,plan_hash,reviewer_model,verdict,created_at) VALUES (?,?,?,?,?,?)", (source,2,"currenthash","gpt-6-astra","advisor-remediated","2026-10-04 02:00:00"))
    db.execute("INSERT INTO registered_designs (design_file,registered_at,terminal_session,consumed) VALUES (?,?,?,?)", (json.loads(plan)["design_file"],"2026-10-04 00:00:00",source,1))
    db.commit()
    db.close()
    return path, source, target


def test_transfer_preserves_evidence_and_readback(state):
    path, source, target = state
    before = snapshot_pm_state(path, source, "project")
    result = prepare_pm_handoff(path, before, target, 7)
    assert result == readback_pm_handoff(path, source, target, 7)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    src = dict(db.execute("SELECT * FROM sessions WHERE terminal_session=?",(source,)).fetchone())
    dst = dict(db.execute("SELECT * FROM sessions WHERE terminal_session=?",(target,)).fetchone())
    assert src["active_skill"] == "code-review" and src["review_pending"] == 1
    assert dst["workflow_stage"] == "executing" and dst["active_skill"] is None
    assert dst["review_pending"] == 0 and dst["testing_theatre_checked"] == 0
    for field in ("plan_json","plan_lineage","review_block_count","circuit_breaker","memory_search_required","inherit_review"):
        assert dst[field] == src[field]
    assert [r[0] for r in db.execute("SELECT status FROM wave_tasks WHERE terminal_session=? ORDER BY task_id",(target,))] == ["review_passed"]*5+["pending"]*2
    assert [r[0] for r in db.execute("SELECT task_ids FROM review_grades WHERE terminal_session=?",(target,))] == ["[1,2,3]"]
    assert [(r[0],r[1],r[2]) for r in db.execute("SELECT plan_hash,reviewer_model,verdict FROM tier_up_reviews WHERE terminal_session=? ORDER BY id",(target,))] == [("oldhash","gpt-6-astra","HAS-ISSUES"),("currenthash","gpt-6-astra","advisor-remediated")]
    for table, key in (("wave_tasks","tasks"),("plan_history","history"),("review_grades","grades"),("tier_up_reviews","reviews")):
        assert [dict(row) for row in db.execute(f"SELECT * FROM {table} WHERE terminal_session=? ORDER BY id",(source,))] == before[key]
    assert src == before["session"]
    original_reviews = before["reviews"]
    copied_reviews = [dict(row) for row in db.execute("SELECT * FROM tier_up_reviews WHERE terminal_session=? ORDER BY id",(target,))]
    assert [(r["plan_lineage"],r["plan_hash"],r["reviewer_model"],r["verdict"],r["created_at"]) for r in copied_reviews] == [(r["plan_lineage"],r["plan_hash"],r["reviewer_model"],r["verdict"],r["created_at"]) for r in original_reviews]
    assert result["copied_ids"]["reviews"] == [[old["id"],new["id"]] for old,new in zip(original_reviews,copied_reviews)]
    audit = json.loads(db.execute("SELECT context FROM audit_log WHERE terminal_session=? AND action='worker_pm_handoff_prepared'",(target,)).fetchone()[0])
    assert audit["snapshot_digest"] == before["digest"] and audit["result"]["copied_ids"] == result["copied_ids"]
    with pytest.raises(sqlite3.IntegrityError, match="one blind"):
        db.execute("INSERT INTO tier_up_reviews (terminal_session,plan_lineage,plan_hash,reviewer_model,verdict) VALUES (?,?,?,?,?)",(target,2,"other","x","SOLID"))
    assert db.execute("SELECT design_file FROM registered_designs WHERE terminal_session=? AND consumed=1",(target,)).fetchone()[0] == json.loads(src["plan_json"])["design_file"]
    assert db.execute("SELECT registered_at FROM registered_designs WHERE terminal_session=?",(target,)).fetchone()[0] == "2026-10-04 00:00:00"
    db.close()
    assert prepare_pm_handoff(path,before,target,7) == result


def test_changed_source_and_dirty_target_refuse(state):
    path, source, target = state
    snap = snapshot_pm_state(path,source,"project")
    db = sqlite3.connect(path)
    db.execute("UPDATE sessions SET review_block_count=5 WHERE terminal_session=?",(source,)); db.commit()
    with pytest.raises(ValueError,match="source"):
        prepare_pm_handoff(path,snap,target,1)
    db.execute("UPDATE sessions SET review_block_count=4 WHERE terminal_session=?",(source,))
    db.execute("INSERT INTO wave_tasks (terminal_session,task_id,wave_number,task_name) VALUES (?,?,?,?)",(target,99,1,"foreign")); db.commit(); db.close()
    with pytest.raises(ValueError,match="target"):
        prepare_pm_handoff(path,snap,target,1)


def test_unsupported_schema_and_foreign_project_refuse(state, tmp_path):
    path, source, target = state
    with pytest.raises(ValueError,match="project"):
        snapshot_pm_state(path,source,"other")
    with pytest.raises(ValueError,match="schema"):
        snapshot_pm_state(tmp_path / "missing.db",source,"project")
    db=sqlite3.connect(path); db.execute("DROP TABLE plan_history"); db.commit(); db.close()
    with pytest.raises(ValueError,match="schema"):
        snapshot_pm_state(path,source,"project")


def test_real_in_progress_case_and_planning_stage_preserved(state):
    path, source, target = state
    db = sqlite3.connect(path)
    db.execute("UPDATE sessions SET workflow_stage='executing',review_pending=0 WHERE terminal_session=?", (source,))
    db.execute("UPDATE wave_tasks SET status='in_progress' WHERE terminal_session=? AND task_id=6", (source,))
    db.commit(); db.close()
    snap = snapshot_pm_state(path,source,"project")
    prepare_pm_handoff(path,snap,target,1)
    db = sqlite3.connect(path)
    assert db.execute("SELECT workflow_stage FROM sessions WHERE terminal_session=?",(target,)).fetchone()[0] == "executing"
    assert db.execute("SELECT status FROM wave_tasks WHERE terminal_session=? AND task_id=6",(target,)).fetchone()[0] == "pending"
    db.close()


def test_executing_submitted_review_reopens_without_deadlock(state):
    path, source, target = state
    db = sqlite3.connect(path)
    db.execute("UPDATE sessions SET workflow_stage='executing' WHERE terminal_session=?", (source,))
    db.commit(); db.close()
    snap = snapshot_pm_state(path,source,"project")
    prepare_pm_handoff(path,snap,target,1)
    db=sqlite3.connect(path)
    assert db.execute("SELECT workflow_stage,review_pending FROM sessions WHERE terminal_session=?",(target,)).fetchone() == ("executing",0)
    assert db.execute("SELECT status FROM wave_tasks WHERE terminal_session=? AND task_id=6",(target,)).fetchone()[0] == "pending"
    db.close()


def test_planning_consumed_registration_and_stage_preserved(state):
    path, source, target = state
    db=sqlite3.connect(path)
    db.execute("UPDATE sessions SET workflow_stage='final_plan_prep',review_pending=0 WHERE terminal_session=?",(source,))
    db.execute("DELETE FROM wave_tasks WHERE terminal_session=?",(source,))
    db.commit(); db.close()
    snap=snapshot_pm_state(path,source,"project")
    prepare_pm_handoff(path,snap,target,1)
    db=sqlite3.connect(path)
    assert db.execute("SELECT workflow_stage FROM sessions WHERE terminal_session=?",(target,)).fetchone()[0] == "final_plan_prep"
    design=json.loads(snap["session"]["plan_json"])["design_file"]
    assert db.execute("SELECT consumed FROM registered_designs WHERE design_file=? AND terminal_session=?",(design,target)).fetchone()[0] == 1
    db.close()


def test_later_failure_rolls_back_session_tasks_reviews_and_registration(state):
    path,source,target=state
    db=sqlite3.connect(path)
    db.execute("UPDATE review_grades SET task_ids='bad json' WHERE terminal_session=?",(source,))
    db.commit();db.close()
    snap=snapshot_pm_state(path,source,"project")
    with pytest.raises(ValueError,match="task_ids"):
        prepare_pm_handoff(path,snap,target,1)
    db=sqlite3.connect(path)
    assert db.execute("SELECT workflow_stage FROM sessions WHERE terminal_session=?",(target,)).fetchone()[0] == "idle"
    for table in ("wave_tasks","review_grades","tier_up_reviews"):
        assert db.execute(f"SELECT count(*) FROM {table} WHERE terminal_session=?",(target,)).fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM registered_designs WHERE terminal_session=?",(source,)).fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM audit_log WHERE terminal_session=?",(target,)).fetchone()[0] == 0
    db.close()


def test_stale_registration_owner_and_target_child_binding_refuse(state):
    path,source,target=state
    snap=snapshot_pm_state(path,source,"project")
    db=sqlite3.connect(path)
    db.execute("INSERT INTO subagent_sessions (child_session,parent_session) VALUES (?,?)",(str(uuid.uuid4()),target))
    db.commit();db.close()
    with pytest.raises(ValueError,match="child bindings"):
        prepare_pm_handoff(path,snap,target,1)
    db=sqlite3.connect(path)
    db.execute("DELETE FROM subagent_sessions WHERE parent_session=?",(target,))
    db.execute("UPDATE registered_designs SET terminal_session=? WHERE terminal_session=?",(str(uuid.uuid4()),source))
    db.commit();db.close()
    with pytest.raises(ValueError,match="registration owner"):
        prepare_pm_handoff(path,snap,target,1)


def test_replay_rejects_changed_source_and_target(state):
    path,source,target=state
    snap=snapshot_pm_state(path,source,"project")
    prepare_pm_handoff(path,snap,target,1)
    db=sqlite3.connect(path)
    db.execute("UPDATE sessions SET review_block_count=9 WHERE terminal_session=?",(source,))
    db.commit();db.close()
    with pytest.raises(ValueError,match="source historical"):
        prepare_pm_handoff(path,snap,target,1)
    db=sqlite3.connect(path)
    db.execute("UPDATE sessions SET review_block_count=4 WHERE terminal_session=?",(source,))
    db.execute("UPDATE sessions SET circuit_breaker=0 WHERE terminal_session=?",(target,))
    db.commit();db.close()
    with pytest.raises(ValueError,match="target PM state"):
        prepare_pm_handoff(path,snap,target,1)


def test_invalid_uuid_counter_and_missing_target_refuse(state):
    path,source,target=state
    with pytest.raises(ValueError,match="UUID"):
        snapshot_pm_state(path,"not-a-uuid","project")
    db=sqlite3.connect(path)
    db.execute("UPDATE sessions SET review_block_count=-1 WHERE terminal_session=?",(source,))
    db.commit();db.close()
    with pytest.raises(ValueError,match="review_block_count"):
        snapshot_pm_state(path,source,"project")


def test_plan_ready_without_plan_keeps_consumed_design_and_stage(state):
    path,source,target=state
    db=sqlite3.connect(path)
    db.execute("UPDATE sessions SET workflow_stage='plan_ready',plan_json=NULL,plan_name=NULL,current_wave=0,review_pending=0 WHERE terminal_session=?",(source,))
    db.execute("DELETE FROM wave_tasks WHERE terminal_session=?",(source,))
    db.execute("DELETE FROM review_grades WHERE terminal_session=?",(source,))
    db.commit();db.close()
    snap=snapshot_pm_state(path,source,"project")
    prepare_pm_handoff(path,snap,target,1)
    db=sqlite3.connect(path)
    assert db.execute("SELECT workflow_stage,plan_json FROM sessions WHERE terminal_session=?",(target,)).fetchone() == ("plan_ready",None)
    assert db.execute("SELECT consumed FROM registered_designs WHERE terminal_session=?",(target,)).fetchone()[0] == 1
    db.close()
    assert readback_pm_handoff(path,source,target,1)["stage"] == "plan_ready"


def test_target_nondefault_safety_flag_refuses(state):
    path,source,target=state
    snap=snapshot_pm_state(path,source,"project")
    db=sqlite3.connect(path)
    db.execute("UPDATE sessions SET memory_search_required=1 WHERE terminal_session=?",(target,))
    db.commit();db.close()
    with pytest.raises(ValueError,match="target PM root already has work"):
        prepare_pm_handoff(path,snap,target,1)


def test_target_owned_design_and_boolean_generation_refuse(state):
    path,source,target=state
    snap=snapshot_pm_state(path,source,"project")
    with pytest.raises(ValueError,match="generation"):
        prepare_pm_handoff(path,snap,target,True)
    with pytest.raises(ValueError,match="generation"):
        readback_pm_handoff(path,source,target,True)
    db=sqlite3.connect(path)
    db.execute("INSERT INTO registered_designs (design_file,terminal_session,consumed) VALUES (?,?,?)",("docs/designs/foreign.md",target,1))
    db.commit();db.close()
    with pytest.raises(ValueError,match="already owns a design"):
        prepare_pm_handoff(path,snap,target,1)


def test_audit_failure_after_registration_cas_rolls_back_everything(state):
    path,source,target=state
    snap=snapshot_pm_state(path,source,"project")
    db=sqlite3.connect(path)
    db.execute("CREATE TRIGGER fail_handoff_audit BEFORE INSERT ON audit_log WHEN NEW.action='worker_pm_handoff_prepared' BEGIN SELECT RAISE(ABORT,'audit fail'); END")
    db.commit();db.close()
    with pytest.raises(sqlite3.IntegrityError,match="audit fail"):
        prepare_pm_handoff(path,snap,target,1)
    db=sqlite3.connect(path)
    assert db.execute("SELECT workflow_stage FROM sessions WHERE terminal_session=?",(target,)).fetchone()[0] == "idle"
    assert db.execute("SELECT count(*) FROM registered_designs WHERE terminal_session=?",(source,)).fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM registered_designs WHERE terminal_session=?",(target,)).fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM tier_up_reviews WHERE terminal_session=?",(target,)).fetchone()[0] == 0
    db.close()


def test_known_plan_history_metadata_variant_survives_transfer(state):
    path,source,target=state
    db=sqlite3.connect(path)
    db.execute("ALTER TABLE plan_history ADD COLUMN plan_lineage INTEGER")
    db.execute("ALTER TABLE plan_history ADD COLUMN repository_identity TEXT")
    design=db.execute("SELECT design_file FROM registered_designs WHERE terminal_session=?",(source,)).fetchone()[0]
    db.execute("INSERT INTO plan_history (terminal_session,plan_name,design_file,total_tasks,plan_lineage,repository_identity) VALUES (?,?,?,?,?,?)",(source,"GM Core",design,7,2,"repo-identity"))
    db.commit();db.close()
    snap=snapshot_pm_state(path,source,"project")
    proof=prepare_pm_handoff(path,snap,target,1)
    assert readback_pm_handoff(path,source,target,1) == proof
    db=sqlite3.connect(path)
    assert db.execute("SELECT plan_lineage,repository_identity FROM plan_history WHERE terminal_session=?",(source,)).fetchone() == (2,"repo-identity")
    assert db.execute("SELECT plan_lineage,repository_identity FROM plan_history WHERE terminal_session=?",(target,)).fetchone() == (2,"repo-identity")
    db.close()


def test_unknown_plan_history_column_still_refuses(state):
    path,source,_=state
    db=sqlite3.connect(path)
    db.execute("ALTER TABLE plan_history ADD COLUMN unexpected TEXT")
    db.commit();db.close()
    with pytest.raises(ValueError,match="unsupported PM schema: plan_history"):
        snapshot_pm_state(path,source,"project")
