# TQQQ baseline SMA/arithmetic reslice v1

Research-only, offline, controls-explicitly-disabled baseline. `SMA200_INCLUSIVE_CLOSE_V1` uses the latest 200 unique QQQ closes with dates `<= d` after session close `d`; next-open execution uses only the next bar's open. The first observed date requires 199 prior QQQ observations; 198 fails. Costs are fixed at zero.

Every price, quantity, holding value, cash, equity, return and metric arithmetic step is finite/range checked. Overflow, underflow-to-zero, NaN/Inf or invalid intermediate state raises sanitized `TqqqBaselineError` before a result is constructed. Result wire is deterministic and run identity includes exact window/session/as-of and cost/timing semantics.

No production controls, metadata, QPK/store, network, live/order/account/funds/leverage behavior is used.
