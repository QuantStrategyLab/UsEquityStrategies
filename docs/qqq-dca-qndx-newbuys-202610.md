# QQQ DCA: QNDX new buys, October 2026

`qqq-dca-qndx-newbuys-202610` is a descriptive lookup alias and an explicitly selected configuration for `nasdaq_sp500_smart_dca`. Only new contributions allocated to QQQ or QQQM are routed to QNDX. Existing QQQ, QQQM, and QNDX positions retain their snapshot market values as the starting targets; no conversion, liquidation, or rebalancing is requested. Signals remain QQQ and SPY.

## Configuration and risk accounting

`new_buy_execution_symbol` defaults to `null` in both catalog and manifest, preserving the current QQQM/SPLG allocations and managed symbols. Its only non-null accepted value is `"QNDX"`. Selecting the alias alone uses the unchanged default; it does not apply the preset.

The bundled [preset](../src/us_equity_strategies/configs/qqq-dca-qndx-newbuys-202610.json) contains only:

```json
{
  "new_buy_execution_symbol": "QNDX",
  "managed_symbols": ["QQQ", "QQQM", "QNDX", "SPLG"]
}
```

The DCA runtime adapter loads this preset only when its `config_path` is explicitly set to `package://us_equity_strategies/configs/qqq-dca-qndx-newbuys-202610.json`. With no path it returns no overrides. Other paths are rejected rather than silently interpreted as this preset. Programmatic integrations can supply the option through `StrategyContext.runtime_config` and must supply all four managed symbols to their portfolio/quote input builders.

The planner always retains QQQ and QQQM and adds QNDX when the option is on, even if a caller omits them from `managed_symbols`. It adds the new contribution to QNDX's current value, leaving the other leg's allocation unchanged. Existing cadence, cash reserve, minimum contribution, and maximum contribution settings still apply. A skipped contribution produces no execution targets.

The enabled entrypoint checks the actual target positions through the existing shared risk gate with value-target exposure enforcement. It requires the platform's valid `capital_base` and `capital_base_binding`; missing, stale, or invalid evidence rejects execution. It then submits the sum of QQQ + QQQM + QNDX as a risk-only target under each equivalent ticker in turn, together with the other targets. Each existing ticker cap must cover the combined exposure, and existing total nominal/effective exposure, position-count, loss, and circuit-breaker checks remain in force. No cap value is changed. Only the original separate execution targets can be returned on approval; a risk rejection returns no positions. `nasdaq_combined_target_value` exposes the combined amount for inspection.

TQQQ growth/income and all rebalancing combos are excluded. Their allocations, exits, and configuration are unchanged. The core combos and TQQQ reject this unsupported option; the leveraged combo ignores it and retains its original targets. Do not apply this preset to them.

## IBKR read-only verification: 2026-10-15

The [IBKR public US securities directory](https://www.interactivebrokers.co.uk/en/index.php?asset=&b=QMN&cntry=usa&e=QUS&f=40092&ib_entity=uk&ln=&tag=United+States) lists QNDX in USD as the State Street SPDR Portfolio Nasdaq 100 ETF. This establishes broker-directory coverage, not an account-level permission check.

The [IBKR fractional-share instrument list](https://www.interactivebrokers.com/download/fracshare_stk.csv), read in memory as instrument metadata only, contained:

```text
SYMBOL: QNDX
MAIN_EXCHANGE: NASDAQ
DESCRIPTION: SS SPDR PRTFO NASDAQ 100 ETF
IB_CONTRACT_ID: 895491809
SCHEDULED_INELIGIBILTY_DATE: empty
```

IBKR's [fractional-share guide](https://www.interactivebrokers.com/campus/trading-lessons/fractional-shares/) identifies this list as the eligibility source. The [issuer's listing information](https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-portfolio-nasdaq-100-etf-qndx) independently identifies NASDAQ, USD, ISIN US78468R3903, and CUSIP 78468R390. Select the equity/ETF contract (`STK`, USD, primary exchange NASDAQ), never the same-symbol futures product.

Instrument-level fractional eligibility is confirmed by the public list. Account-specific buying permissions and API fractional execution remain unverified. No authenticated broker probe, orders, paper orders, account changes, or market-data downloads were used. The public contract-details website was unavailable. Keep the preset off until the existing account and API path are verified; if fractional eligibility cannot be confirmed at activation, keep it off or use a separately verified existing whole-share path without increasing any budget.

## Merge impact and later activation

Merging this PR does not change live orders. It changes library source and adds an unselected preset; no deployed image, live configuration, scheduler, account, or permission is changed.

The IBKR source was checked at immutable revision `92a342e47a645b936b39a1d563c0158c8fcfcb66`:

- [`pyproject.toml:25`](https://github.com/QuantStrategyLab/InteractiveBrokersPlatform/blob/92a342e47a645b936b39a1d563c0158c8fcfcb66/pyproject.toml#L25) pins UES to `9d1544d1476dd19b1da61b16f37cee7f9126caf4`; [`uv.lock:1494-1496`](https://github.com/QuantStrategyLab/InteractiveBrokersPlatform/blob/92a342e47a645b936b39a1d563c0158c8fcfcb66/uv.lock#L1494) locks that exact source. A UES merge does not move this pin.
- [`Dockerfile:17-20`](https://github.com/QuantStrategyLab/InteractiveBrokersPlatform/blob/92a342e47a645b936b39a1d563c0158c8fcfcb66/Dockerfile#L17) installs with `uv sync --frozen`, records `UES_REVISION`, and removes git later in the build. The container imports its installed package; it does not pull UES main for each cycle.
- [`strategy_runtime.py:1733-1770`](https://github.com/QuantStrategyLab/InteractiveBrokersPlatform/blob/92a342e47a645b936b39a1d563c0158c8fcfcb66/strategy_runtime.py#L1733) invokes the selected adapter's parameter loader and merges its output over the installed manifest defaults. [`main.py:381-385`](https://github.com/QuantStrategyLab/InteractiveBrokersPlatform/blob/92a342e47a645b936b39a1d563c0158c8fcfcb66/main.py#L381) creates that strategy runtime at application startup. A running instance does not see a later UES source merge or a local preset edit.
- [`sync-cloud-run-env.yml:3-4,71-77`](https://github.com/QuantStrategyLab/InteractiveBrokersPlatform/blob/92a342e47a645b936b39a1d563c0158c8fcfcb66/.github/workflows/sync-cloud-run-env.yml#L3) exposes deployment through `workflow_dispatch`; its job requires that event. [`ci.yml:3-6`](https://github.com/QuantStrategyLab/InteractiveBrokersPlatform/blob/92a342e47a645b936b39a1d563c0158c8fcfcb66/.github/workflows/ci.yml#L3) runs tests on push/PR without deploying. UES's own CI runs tests and has no live deployment step.

Later activation requires a separately authorized platform PR to update the UES dependency and lockfile to the reviewed merge commit, verify the existing account/API path for the specified ETF, and explicitly select the preset with `IBKR_STRATEGY_CONFIG_PATH=package://us_equity_strategies/configs/qqq-dca-qndx-newbuys-202610.json` for the intended standalone DCA runtime. Existing runtime allowlists/caps and valid capital evidence must admit the full managed set without relaxing limits; otherwise leave it off. Build and deploy via the normal protected CI workflow and its existing traffic/scheduler approvals. At the next normal DCA window, only newly contributed Nasdaq-leg cash can buy QNDX. This PR performs none of these production steps.

The other documented consumers also pin UES and install from their frozen lockfiles: [CharlesSchwabPlatform dependency](https://github.com/QuantStrategyLab/CharlesSchwabPlatform/blob/6d1c810152adfbcd3dbbb5d74ce79b9d944da02b/pyproject.toml#L23) and [Dockerfile](https://github.com/QuantStrategyLab/CharlesSchwabPlatform/blob/6d1c810152adfbcd3dbbb5d74ce79b9d944da02b/Dockerfile#L17), [LongBridgePlatform dependency](https://github.com/QuantStrategyLab/LongBridgePlatform/blob/93f73736d0698f3d8d285563f510ae0dcbbf661d/pyproject.toml#L22) and [Dockerfile](https://github.com/QuantStrategyLab/LongBridgePlatform/blob/93f73736d0698f3d8d285563f510ae0dcbbf661d/Dockerfile#L24), and [FirstradePlatform dependency](https://github.com/QuantStrategyLab/FirstradePlatform/blob/537ab80bb76dfc6b29591a5951152b905ffbf813/pyproject.toml#L22) and [Dockerfile](https://github.com/QuantStrategyLab/FirstradePlatform/blob/537ab80bb76dfc6b29591a5951152b905ffbf813/Dockerfile#L17). Their deployment workflows expose manual dispatch; no downstream deployment was invoked.

## Synthetic validation

`tests/test_qqq_dca_qndx_newbuys.py` covers routing from both legacy symbols, preserving old holdings, combined caps under all three tickers, the unchanged total cap, missing capital evidence, skipped contributions, unchanged defaults, preset loading through the IBKR adapter, unchanged QQQ signals, and excluded TQQQ/rebalancing combinations. Existing DCA, catalog, entrypoint, combo, and risk-gate tests provide regression coverage. All test inputs are synthetic.
