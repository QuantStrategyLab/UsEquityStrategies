# Production option-overlay retirement

The production option overlay has been retired from the strategy profiles and
US combo entrypoints. The core equity strategies, their risk controls and DCA
rules continue to use their own contracts. This change does not close or settle
any option position and does not authorize an order.

New production defaults contain no option-overlay recipe or budget. An explicit
legacy enabled switch, nonempty recipe, old option budget, unknown `option_*`
setting, or supplied option order intent or position is rejected with
`OptionOverlayRetiredError` before strategy decisions. An explicitly disabled
boolean switch can remain as a compatibility marker. Remove legacy fields only
after their account effects have been reviewed and the configuration has been
migrated deliberately; do not silently substitute a different strategy.

The entrypoint checks the fields supplied in runtime configuration, context
state, market data, portfolio metadata and supplied base diagnostics. An absent `option_positions` field
is not evidence that a brokerage account has no option obligations. In
particular, the generic portfolio position contract does not classify options.
Existing broker positions, open orders, assignment, expiry, settlement and cash
obligations require independent broker-specific reconciliation before any
migration or execution decision.

Historical option recipes and the separately bounded research replay remain for
reproducibility. Their presence does not restore production order capability,
qualify a historical result, or prove real option-chain or account evidence.
