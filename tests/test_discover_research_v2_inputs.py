from scripts.discover_research_v2_inputs import MAX_OBJECTS, summarize


def test_inventory_filters_and_preserves_scope():
    result = summarize([
        {"name": "research/v2/input/set/qqqm/prices.csv", "generation": "7", "size": "123"},
        {"name": "research/v2/input/set/unrelated.bin", "size": "2"},
    ])
    assert result["status"] == "LIST_COMPLETE"
    assert result["objects_seen"] == 2
    assert result["matched_objects"] == [{
        "uri": "gs://qsl-research-evidence-831478360303/research/v2/input/set/qqqm/prices.csv",
        "generation": "7", "size": "123", "md5Hash": None,
        "crc32c": None, "updated": None,
    }]


def test_budget_boundary_is_incomplete():
    result = summarize([{"name": "research/v2/input/x"}] * MAX_OBJECTS)
    assert result["status"] == "RANGE_INCOMPLETE"
