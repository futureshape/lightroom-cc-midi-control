import json
import tempfile
import unittest
from pathlib import Path

from config_store import clear_mappings, load_config, mappings_for, save_config


class ConfigStoreTests(unittest.TestCase):
    def test_migrates_legacy_mappings_to_selected_controller(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mappings.json"
            path.write_text(json.dumps({
                "midi_port": "Controller A",
                "client_guid": "guid",
                "mappings": [{"midi_key": "cc:1:1"}],
            }))
            config = load_config(path)

            self.assertEqual(mappings_for(config), [{"midi_key": "cc:1:1"}])
            self.assertNotIn("mappings", config)

    def test_controllers_have_independent_mapping_lists(self):
        config = {"midi_port": "Controller A", "controllers": {}}
        mappings_for(config).append({"midi_key": "cc:1:1"})
        config["midi_port"] = "Controller B"

        self.assertEqual(mappings_for(config), [])
        mappings_for(config).append({"midi_key": "cc:1:2"})
        self.assertEqual(mappings_for(config, "Controller A"), [{"midi_key": "cc:1:1"}])

    def test_round_trip_preserves_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mappings.json"
            config = {"midi_port": "B", "controllers": {
                "A": {"mappings": [{"midi_key": "note:1:1"}]},
                "B": {"mappings": []},
            }}
            save_config(config, path)

            self.assertEqual(load_config(path), config)

    def test_clear_mappings_only_resets_selected_controller(self):
        config = {"midi_port": "A", "controllers": {
            "A": {"mappings": [{"midi_key": "cc:1:1"}]},
            "B": {"mappings": [{"midi_key": "cc:1:2"}]},
        }}

        self.assertEqual(clear_mappings(config), 1)
        self.assertEqual(mappings_for(config, "A"), [])
        self.assertEqual(mappings_for(config, "B"), [{"midi_key": "cc:1:2"}])


if __name__ == "__main__":
    unittest.main()
