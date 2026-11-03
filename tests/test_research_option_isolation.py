from copy import deepcopy
from dataclasses import replace

import pytest
from quant_platform_kit.common.strategy_contracts import StrategyContext

from tests.test_optimized_strategy_replay import _soxx_credit_fixture
from us_equity_strategies.entrypoints import evaluate_soxl_soxx_trend_income
from us_equity_strategies.option_overlay_retirement import OptionOverlayRetiredError
from us_equity_strategies.research import optimized_strategy_replay as replay
from us_equity_strategies.research.local_member_replay_input import (
    load_local_member_fixture,
)


@pytest.mark.parametrize("enabled", [False, True])
def test_validated_research_options_stay_in_model_not_production_builder(tmp_path, monkeypatch, enabled):
    path = tmp_path / "synthetic-credit.json"
    _soxx_credit_fixture(path)
    _, request = load_local_member_fixture(path)
    if not enabled:
        config = dict(request.runtime_config)
        config.update(option_overlay_enabled=False, option_income_overlay_enabled=False)
        request = replace(request, runtime_config=config, option_market_inputs=None, research_identity=None)
    original = deepcopy(dict(request.runtime_config))
    manifest, builder = replay._PROFILES["soxl_soxx_trend_income"]
    seen = []

    def actual_core(context, **kwargs):
        seen.append(dict(context.runtime_config))
        assert not any(key.startswith("option_") for key in context.runtime_config)
        return builder(context, **kwargs)

    monkeypatch.setitem(replay._PROFILES, "soxl_soxx_trend_income", (manifest, actual_core))
    result = replay.replay_optimized_strategy(request)
    assert result is not None
    assert seen
    assert dict(request.runtime_config) == original
    assert request.runtime_config["option_income_overlay_recipe"] == "soxx_put_credit_spread_income_v1"
    # No caller-controlled production exemption was introduced.
    with pytest.raises(OptionOverlayRetiredError):
        evaluate_soxl_soxx_trend_income(StrategyContext(as_of="2024-01-03", runtime_config=original))
