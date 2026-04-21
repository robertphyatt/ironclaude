"""Tests for ironclaude.plugins.scan.pipeline.orphan_remediation."""
from __future__ import annotations

import json
from pathlib import Path


def _touch_jpg(path: Path, content: bytes = b"img") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


class TestRemediateRegularBatch:
    """Path A: regular batches — rename old-format orphan JPGs + create blank stubs."""

    def test_rename_and_create_stub(self, tmp_path):
        """Old-format orphan JPG: renamed to new format, blank stub JSON created."""
        from ironclaude.plugins.scan.pipeline.orphan_remediation import remediate_regular_batch

        batch = tmp_path / "batch_005_dow_bonus"
        images = batch / "images"
        images.mkdir(parents=True)
        _touch_jpg(images / "dow_region_003_b.jpg")

        stats = remediate_regular_batch(batch, apply=True)

        assert not (images / "dow_region_003_b.jpg").exists()
        assert (images / "dow_region_b005_003_b.jpg").exists()
        json_path = batch / "dow_region_b005_003_b.json"
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert data["card_id"] == "dow_region_b005_003_b"
        assert data["card_type"] == "REGION"
        assert data["side"] == "b"
        assert data["cues"] == []
        assert data["crop_cues"] == []
        assert stats["renamed"] == 1
        assert stats["stubs_created"] == 1

    def test_idempotent(self, tmp_path):
        """Second run makes no changes; file state is unchanged."""
        from ironclaude.plugins.scan.pipeline.orphan_remediation import remediate_regular_batch

        batch = tmp_path / "batch_005_dow_bonus"
        images = batch / "images"
        images.mkdir(parents=True)
        _touch_jpg(images / "dow_region_003_b.jpg")

        remediate_regular_batch(batch, apply=True)

        before_files = sorted(
            p.relative_to(batch).as_posix() for p in batch.rglob("*") if p.is_file()
        )
        before_json = (batch / "dow_region_b005_003_b.json").read_text()

        stats = remediate_regular_batch(batch, apply=True)

        after_files = sorted(
            p.relative_to(batch).as_posix() for p in batch.rglob("*") if p.is_file()
        )
        after_json = (batch / "dow_region_b005_003_b.json").read_text()

        assert before_files == after_files
        assert before_json == after_json
        assert stats["renamed"] == 0
        assert stats["stubs_created"] == 0


class TestRemediateConflictBatch:
    """Path B: CONFLICT batches — create per-card JSON stubs for new-format orphans."""

    def _make_master(self, batch_dir: Path, entries: list) -> None:
        master = batch_dir / "batch_009_story_engine_CONFLICT.json"
        master.parent.mkdir(parents=True, exist_ok=True)
        master.write_text(json.dumps(entries))

    def test_stub_from_master_entry(self, tmp_path):
        """NNN present in master JSON: stub gets cues from master entry."""
        from ironclaude.plugins.scan.pipeline.orphan_remediation import remediate_conflict_batch

        batch = tmp_path / "batch_009_story_engine_CONFLICT"
        images = batch / "images"
        images.mkdir(parents=True)
        _touch_jpg(images / "se_conflict_b009_002_a.jpg")
        self._make_master(batch, [
            {
                "card_id": "se_conflict_002",
                "image_path": "/some/path/images/se_conflict_002_a.jpg",
                "card_type": "CONFLICT",
                "side": "a",
                "cues": ["CUE ONE", "CONFLICT"],
                "crop_cues": [
                    {"text": "CUE ONE", "crop_index": 0},
                    {"text": "CONFLICT", "crop_index": 1},
                ],
            }
        ])

        stats = remediate_conflict_batch(batch, apply=True)

        json_path = batch / "se_conflict_b009_002_a.json"
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert data["card_id"] == "se_conflict_b009_002_a"
        assert data["card_type"] == "CONFLICT"
        assert data["cues"] == ["CUE ONE", "CONFLICT"]
        assert data["crop_cues"] == [
            {"text": "CUE ONE", "crop_index": 0},
            {"text": "CONFLICT", "crop_index": 1},
        ]
        assert stats["stubs_from_master"] == 1
        assert stats["stubs_blank"] == 0

    def test_blank_stub_when_nnn_not_in_master(self, tmp_path):
        """NNN absent from master JSON: blank stub created."""
        from ironclaude.plugins.scan.pipeline.orphan_remediation import remediate_conflict_batch

        batch = tmp_path / "batch_009_story_engine_CONFLICT"
        images = batch / "images"
        images.mkdir(parents=True)
        _touch_jpg(images / "se_conflict_b009_001_a.jpg")
        self._make_master(batch, [])  # empty master — NNN=1 not present

        stats = remediate_conflict_batch(batch, apply=True)

        json_path = batch / "se_conflict_b009_001_a.json"
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert data["cues"] == []
        assert data["crop_cues"] == []
        assert stats["stubs_blank"] == 1
        assert stats["stubs_from_master"] == 0

    def test_idempotent(self, tmp_path):
        """Second run skips cards that already have JSON."""
        from ironclaude.plugins.scan.pipeline.orphan_remediation import remediate_conflict_batch

        batch = tmp_path / "batch_009_story_engine_CONFLICT"
        images = batch / "images"
        images.mkdir(parents=True)
        _touch_jpg(images / "se_conflict_b009_002_a.jpg")
        self._make_master(batch, [
            {
                "card_id": "se_conflict_002",
                "image_path": "/path/se_conflict_002_a.jpg",
                "card_type": "CONFLICT",
                "side": "a",
                "cues": ["X"],
                "crop_cues": [],
            }
        ])

        remediate_conflict_batch(batch, apply=True)
        stats = remediate_conflict_batch(batch, apply=True)

        assert stats["stubs_created"] == 0
        assert stats["skipped_has_json"] == 1


class TestDryRun:
    """Dry-run writes no files but returns accurate stats."""

    def test_dry_run_writes_nothing(self, tmp_path):
        """apply=False: no files created, no files renamed; stats reflect planned actions."""
        from ironclaude.plugins.scan.pipeline.orphan_remediation import remediate_regular_batch

        batch = tmp_path / "batch_005_dow_bonus"
        images = batch / "images"
        images.mkdir(parents=True)
        _touch_jpg(images / "dow_region_003_b.jpg")

        stats = remediate_regular_batch(batch, apply=False)

        assert (images / "dow_region_003_b.jpg").exists()
        assert not (images / "dow_region_b005_003_b.jpg").exists()
        assert not (batch / "dow_region_b005_003_b.json").exists()
        assert stats["renamed"] == 1
        assert stats["stubs_created"] == 1
