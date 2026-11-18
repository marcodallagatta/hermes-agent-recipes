import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from free_model_watch import WatchError, classify_models, is_free_model, prompt_block, resolve_baseline, run_watch, snapshot_decision


class FreeModelWatchTests(unittest.TestCase):
    def test_free_detection_uses_each_catalogs_convention(self):
        self.assertTrue(is_free_model("OpenRouter", {"id": "vendor/model:free"}))
        self.assertTrue(is_free_model("OpenCode Zen", {"id": "vendor/model-free"}))
        self.assertTrue(is_free_model("Vercel AI Gateway", {"id": "vendor/model", "type": "language", "tags": ["free"]}))
        self.assertFalse(is_free_model("Vercel AI Gateway", {"id": "vendor/model", "type": "embedding", "tags": ["free"]}))
        self.assertFalse(is_free_model("Nous Portal", {"id": "vendor/model:paid"}))

    def test_baseline_matches_the_requested_effort_variant(self):
        records = [{
            "name": "Example Model (Low)",
            "slug": "example-model",
            "evaluations": {"artificial_analysis_intelligence_index": 73},
        }]
        self.assertEqual(resolve_baseline(records, {"model": "Example Model", "effort": "Low"}), 73)

    def test_prompt_block_includes_score_and_installer_selected_reference(self):
        decision = {
            "added_offers": ["OpenRouter|vendor/model:free|82"],
            "removed_offers": [],
            "added_unbenchmarked": [],
            "removed_unbenchmarked": [],
        }
        rows = {"OpenRouter|vendor/model:free|82": {"name": "Example Free Model"}}
        config = {"gate": {"model": "Configured Gate", "effort": "High"}, "timezone": "UTC"}
        prompt = prompt_block(decision, rows, config, Decimal("70"), [("Reference", "Medium", Decimal("65"))])
        self.assertIn("AA Index 82", prompt)
        self.assertIn("Reference (Medium): AA Index 65", prompt)

    def test_classification_separates_qualifying_and_unbenchmarked(self):
        models = [
            {"source": "OpenRouter", "id": "vendor/fast:free", "name": "Fast"},
            {"source": "Nous Portal", "id": "vendor/new:free", "name": "New"},
            {"source": "Vercel AI Gateway", "id": "vendor/low:free", "name": "Low"},
        ]
        scores = {"OpenRouter|vendor/fast:free": 82, "Vercel AI Gateway|vendor/low:free": 59}
        qualifying, unbenchmarked = classify_models(models, scores, gate_score=70)
        self.assertEqual([x["id"] for x in qualifying], ["vendor/fast:free"])
        self.assertEqual([x["id"] for x in unbenchmarked], ["vendor/new:free"])

    def test_synthetic_watch_seeds_quietly_then_reports_new_qualifier(self):
        config = {
            "sources": ["OpenRouter"],
            "timezone": "UTC",
            "gate": {"model": "Gate Model", "effort": "Low"},
            "references": [{"label": "Reference", "model": "Reference Model", "effort": "Medium"}],
        }
        catalog = [{"id": "vendor/alpha:free", "name": "Alpha"}]
        aa_records = [
            {"id": "gate", "name": "Gate Model (Low)", "slug": "gate-model", "evaluations": {"artificial_analysis_intelligence_index": 70}},
            {"id": "reference", "name": "Reference Model (Medium)", "slug": "reference-model", "evaluations": {"artificial_analysis_intelligence_index": 65}},
            {"id": "alpha", "name": "Alpha (Low)", "slug": "alpha", "evaluations": {"artificial_analysis_intelligence_index": 75}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "state.json"
            with patch("free_model_watch.state_path", return_value=state_file):
                first, state = run_watch(config, catalog_fetcher=lambda _source: list(catalog), aa_fetcher=lambda: (1, list(aa_records)), state=None)
                self.assertEqual(first, '{"wakeAgent": false}')
                catalog.append({"id": "vendor/beta:free", "name": "Beta"})
                aa_records.append({"id": "beta", "name": "Beta (Low)", "slug": "beta", "evaluations": {"artificial_analysis_intelligence_index": 80}})
                second, _ = run_watch(config, catalog_fetcher=lambda _source: list(catalog), aa_fetcher=lambda: (1, list(aa_records)), state=state)
                self.assertIn("New qualifying offers", second)
                self.assertIn("Beta", second)
                self.assertIn("AA Index 80", second)

    def test_source_failure_preserves_baseline_and_deduplicates_same_error(self):
        prior = {"schema": 1, "aa_version": 4, "config_hash": "same", "offers": ["OpenRouter|old:free|80"], "unbenchmarked": [], "error_signature": ""}
        config = {"sources": ["OpenRouter"], "gate": {"model": "Example", "effort": "Low"}, "references": [], "timezone": "UTC"}

        def fail_source(_source):
            raise WatchError("openrouter.ai returned HTTP 503.")

        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "state.json"
            with patch("free_model_watch.state_path", return_value=state_file):
                first_output, first_state = run_watch(config, catalog_fetcher=fail_source, aa_fetcher=lambda: (4, []), state=prior)
                self.assertIn("Watch incomplete", first_output)
                self.assertEqual(first_state["offers"], prior["offers"])
                self.assertEqual(json.loads(state_file.read_text())["offers"], prior["offers"])
                second_output, _ = run_watch(config, catalog_fetcher=fail_source, aa_fetcher=lambda: (4, []), state=first_state)
                self.assertEqual(second_output, '{"wakeAgent": false}')

    def test_snapshot_first_run_and_index_change_rebaseline_silently(self):
        current = {"offers": ["source|vendor/model:free|82"], "unbenchmarked": []}
        self.assertFalse(snapshot_decision(None, current, aa_version=4)["wake_agent"])
        prior = {"schema": 1, "aa_version": 3, **current}
        self.assertFalse(snapshot_decision(prior, current, aa_version=4)["wake_agent"])

    def test_snapshot_reports_only_real_additions_or_removals(self):
        previous = {"offers": ["source|old:free|80"], "unbenchmarked": []}
        current = {"offers": ["source|new:free|81"], "unbenchmarked": ["source|unknown:free"]}
        prior = {"schema": 1, "aa_version": 4, **previous}
        decision = snapshot_decision(prior, current, aa_version=4)
        self.assertTrue(decision["wake_agent"])
        self.assertEqual(decision["added_offers"], ["source|new:free|81"])
        self.assertEqual(decision["removed_offers"], ["source|old:free|80"])
        self.assertEqual(decision["added_unbenchmarked"], ["source|unknown:free"])

    def test_quiet_snapshot_suppresses_agent(self):
        current = {"offers": ["source|model:free|82"], "unbenchmarked": []}
        prior = {**current, "aa_version": 4}
        decision = snapshot_decision(prior, current, aa_version=4)
        self.assertFalse(decision["wake_agent"])


if __name__ == "__main__":
    unittest.main()
