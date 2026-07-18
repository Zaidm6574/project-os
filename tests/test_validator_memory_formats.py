import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATE_RUN = ROOT / "addons" / "full-engine" / "memory" / "validate_run.py"


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_run_memory_formats", VALIDATE_RUN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ValidatorMemoryFormatTests(unittest.TestCase):
    def setUp(self):
        self.validator = load_validator()
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "project"
        self.run = self.project / "runs" / "demo"
        self.run.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rejects_arbitrary_nonempty_store_files(self):
        store = self.project / "memory" / "store"
        store.mkdir(parents=True)
        (store / "junk.txt").write_text("not machine-readable memory\n", encoding="utf-8")
        (store / "notes.md").write_text("# Review notes\n\nDone.\n", encoding="utf-8")

        self.assertFalse(self.validator._has_graph_or_memory(str(self.run)))

    def test_accepts_graph_json_with_nodes(self):
        graph = self.project / "graphify-out" / "graph.json"
        graph.parent.mkdir(parents=True)
        graph.write_text('{"nodes":[{"id":"goal"}],"edges":[]}\n', encoding="utf-8")

        self.assertTrue(self.validator._has_graph_or_memory(str(self.run)))

    def test_rejects_graph_nodes_that_are_not_nonempty_mappings_with_ids(self):
        graph = self.project / "graphify-out" / "graph.json"
        graph.parent.mkdir(parents=True)

        for nodes in ("goal", [], [{}], [{"id": ""}], ["goal"]):
            with self.subTest(nodes=nodes):
                graph.write_text(
                    __import__("json").dumps({"nodes": nodes}), encoding="utf-8"
                )
                self.assertFalse(self.validator._has_graph_or_memory(str(self.run)))

    def test_accepts_valid_jsonl_memory_record(self):
        brain = self.project / "brain" / "shared-brain.jsonl"
        brain.parent.mkdir(parents=True)
        brain.write_text(
            '{"id":"lesson-1","type":"lesson","text":"Verified lesson"}\n',
            encoding="utf-8",
        )

        self.assertTrue(self.validator._has_graph_or_memory(str(self.run)))

    def test_jsonl_ignores_blank_lines_when_every_record_is_valid(self):
        brain = self.project / "brain" / "shared-brain.jsonl"
        brain.parent.mkdir(parents=True)
        brain.write_text(
            "\n"
            '{"id":"lesson-1","type":"lesson","text":"First"}\n'
            "   \n"
            '{"id":"lesson-2","type":"lesson","text":"Second"}\n',
            encoding="utf-8",
        )

        self.assertTrue(self.validator._has_graph_or_memory(str(self.run)))

    def test_rejects_jsonl_without_nonblank_records(self):
        brain = self.project / "brain" / "shared-brain.jsonl"
        brain.parent.mkdir(parents=True)
        brain.write_text("\n  \n\t\n", encoding="utf-8")

        self.assertFalse(self.validator._has_graph_or_memory(str(self.run)))

    def test_rejects_jsonl_when_any_nonblank_record_is_invalid(self):
        brain = self.project / "brain" / "shared-brain.jsonl"
        brain.parent.mkdir(parents=True)
        valid = '{"id":"lesson-1","type":"lesson","text":"Verified"}'
        invalid_records = (
            "not json",
            "[]",
            '{"id":"","type":"lesson","text":"Verified"}',
            '{"id":"lesson-1","type":"","text":"Verified"}',
            '{"id":"lesson-1","type":"lesson","text":""}',
        )

        for invalid in invalid_records:
            for lines in ((invalid, valid), (valid, invalid)):
                with self.subTest(invalid=invalid, order=lines):
                    brain.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    self.assertFalse(self.validator._has_graph_or_memory(str(self.run)))

    def test_rejects_structurally_empty_jsonl_memory_record(self):
        brain = self.project / "brain" / "shared-brain.jsonl"
        brain.parent.mkdir(parents=True)
        brain.write_text("{}\n", encoding="utf-8")

        self.assertFalse(self.validator._has_graph_or_memory(str(self.run)))

    def test_accepts_osvec_sidecar_with_records(self):
        sidecar = self.project / "memory" / "store" / "project.sidecar.json"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text(
            """
{
  "backend": "numpy-bruteforce",
  "dim": 384,
  "embedder": "hashing",
  "records": {
    "42": {
      "memory_id": "lesson-1",
      "u64_id": 42,
      "text": "Verified lesson",
      "memory_type": "lesson",
      "source_file": "runs/demo/13-delivery-report.md",
      "tags": [],
      "created_at": "2026-07-16T12:00:00",
      "run_slug": "demo"
    }
  }
}
""".strip()
            + "\n",
            encoding="utf-8",
        )

        self.assertTrue(self.validator._has_graph_or_memory(str(self.run)))

    def test_rejects_osvec_records_with_invalid_required_fields(self):
        sidecar = self.project / "memory" / "store" / "project.sidecar.json"
        sidecar.parent.mkdir(parents=True)
        valid = {
            "memory_id": "lesson-1",
            "u64_id": 42,
            "text": "Verified lesson",
            "memory_type": "lesson",
        }
        invalid_fields = {
            "memory_id": "",
            "text": "",
            "memory_type": "",
            "u64_id": True,
        }

        for field, invalid_value in invalid_fields.items():
            with self.subTest(field=field):
                record = dict(valid)
                record[field] = invalid_value
                sidecar.write_text(
                    __import__("json").dumps({"records": {"42": record}}),
                    encoding="utf-8",
                )
                self.assertFalse(self.validator._has_graph_or_memory(str(self.run)))

    def test_rejects_empty_or_malformed_known_artifacts(self):
        store = self.project / "memory" / "store"
        store.mkdir(parents=True)
        (store / "project.sidecar.json").write_text('{"records":{}}\n', encoding="utf-8")
        (store / "project.tvim").write_bytes(b"not-a-real-vector-index")
        (store / "project.tvim.npz").write_bytes(b"not-a-real-npz-index")

        self.assertFalse(self.validator._has_graph_or_memory(str(self.run)))


if __name__ == "__main__":
    unittest.main()
