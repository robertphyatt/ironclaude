# tests/test_worker_registry.py
import json
import pytest
from ironclaude.db import init_db
from ironclaude.worker_registry import WorkerRegistry


@pytest.fixture
def registry(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    return WorkerRegistry(conn)


class TestObjectives:
    def test_create_objective(self, registry):
        obj_id = registry.create_objective("Process D&D chapters")
        assert obj_id == 1

    def test_get_active_objective(self, registry):
        registry.create_objective("Process D&D chapters")
        obj = registry.get_active_objective()
        assert obj is not None
        assert obj["text"] == "Process D&D chapters"

    def test_complete_objective(self, registry):
        obj_id = registry.create_objective("Test")
        registry.complete_objective(obj_id)
        obj = registry.get_active_objective()
        assert obj is None


class TestTasks:
    def test_create_task(self, registry):
        obj_id = registry.create_objective("Test")
        task_id = registry.create_task(obj_id, "Do the thing", order_index=0)
        assert task_id == 1

    def test_get_pending_tasks(self, registry):
        obj_id = registry.create_objective("Test")
        registry.create_task(obj_id, "Task 1", order_index=0)
        registry.create_task(obj_id, "Task 2", order_index=1)
        tasks = registry.get_pending_tasks(obj_id)
        assert len(tasks) == 2

    def test_assign_task(self, registry):
        obj_id = registry.create_objective("Test")
        task_id = registry.create_task(obj_id, "Task 1", order_index=0)
        registry.update_task_status(task_id, "assigned", worker_id="worker-1")
        tasks = registry.get_pending_tasks(obj_id)
        assert len(tasks) == 0


class TestGetTaskDescription:
    def test_returns_description_for_valid_task(self, registry):
        obj_id = registry.create_objective("Test")
        task_id = registry.create_task(obj_id, "Implement auth flow", order_index=0)
        assert registry.get_task_description(task_id) == "Implement auth flow"

    def test_returns_none_for_missing_task(self, registry):
        assert registry.get_task_description(999) is None

    def test_returns_none_for_none_task_id(self, registry):
        assert registry.get_task_description(None) is None


class TestWorkers:
    def test_register_worker(self, registry):
        registry.register_worker("worker-1", "claude-max", "worker-1", repo="/tmp/test")
        w = registry.get_worker("worker-1")
        assert w is not None
        assert w["type"] == "claude-max"
        assert w["status"] == "running"

    def test_update_worker_status(self, registry):
        registry.register_worker("worker-1", "claude-max", "worker-1")
        registry.update_worker_status("worker-1", "completed")
        w = registry.get_worker("worker-1")
        assert w["status"] == "completed"

    def test_get_running_workers(self, registry):
        registry.register_worker("w-1", "claude-max", "w-1")
        registry.register_worker("w-2", "claude-max", "w-2")
        registry.update_worker_status("w-2", "completed")
        running = registry.get_running_workers()
        assert len(running) == 1
        assert running[0]["id"] == "w-1"


    def test_register_worker_stores_description(self, registry):
        registry.register_worker("w-1", "claude-max", "ic-w-1", description="Fix auth bug")
        w = registry.get_worker("w-1")
        assert w["description"] == "Fix auth bug"

    def test_register_worker_description_defaults_empty(self, registry):
        registry.register_worker("w-1", "claude-max", "ic-w-1")
        w = registry.get_worker("w-1")
        assert w["description"] == ""

    def test_get_running_workers_includes_description(self, registry):
        registry.register_worker("w-1", "claude-max", "ic-w-1", description="Deploy service")
        running = registry.get_running_workers()
        assert len(running) == 1
        assert running[0]["description"] == "Deploy service"


class TestWorkersByType:
    def test_get_running_workers_by_type_returns_matching(self, registry):
        """Returns only running workers of the specified type."""
        registry.register_worker("w-1", "ollama", "ic-w-1", repo="/tmp/test")
        registry.register_worker("w-2", "claude-opus", "ic-w-2", repo="/tmp/test")
        ollama_workers = registry.get_running_workers_by_type("ollama")
        assert len(ollama_workers) == 1
        assert ollama_workers[0]["id"] == "w-1"

    def test_get_running_workers_by_type_excludes_completed(self, registry):
        """Excludes completed workers of the specified type."""
        registry.register_worker("w-1", "ollama", "ic-w-1")
        registry.update_worker_status("w-1", "completed")
        ollama_workers = registry.get_running_workers_by_type("ollama")
        assert len(ollama_workers) == 0

    def test_get_running_workers_by_type_empty_when_none(self, registry):
        """Returns empty list when no workers of that type exist."""
        registry.register_worker("w-1", "claude-opus", "ic-w-1")
        ollama_workers = registry.get_running_workers_by_type("ollama")
        assert len(ollama_workers) == 0


class TestEvents:
    def test_log_event(self, registry):
        registry.log_event("worker_spawned", worker_id="w-1", details={"repo": "/tmp"})
        events = registry.get_recent_events(limit=5)
        assert len(events) == 1
        assert events[0]["event_type"] == "worker_spawned"
        assert json.loads(events[0]["details"])["repo"] == "/tmp"
