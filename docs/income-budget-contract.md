# Income allocation budget

The income basket is an asset allocation, not a dividend, deposit, cash or
accounting ledger. Its budget must have one owner for a given portfolio decision.
A combined portfolio may apply its shared budget after building raw member
signals; a standalone strategy retains its own existing amount plan. The
current combo uses raw signals rather than nesting full member entrypoints.
Other integration paths must establish ownership before they can be considered
free of duplicate allocation.

`build_income_layer_plan` keeps the existing holding lock: the target income
value is at least the current value of the configured income symbols. Disabling
the layer sets the new desired allocation to zero; it does not sell or release
existing income holdings. TQQQ and SOXL standalone plans continue to subtract
this locked value before applying their existing core, cash reserve and risk
rules. They must not receive a second income allocation on top of that plan.

For a weight-based consumer, `allocate_income_budget` treats each income target
as a minimum. A matching existing target first satisfies that minimum. The
remaining shortfall is funded proportionally from non-income core targets, then
only if needed from income targets above their own minima. The total invested
weight and its original implicit cash remainder are preserved; equal symbols
are merged. For example, core SPY 0.6 with income SGOV 0.1 becomes SPY 0.5 and
SGOV 0.1, retaining 0.4 cash. If SPY and SGOV are already 0.3 each and SGOV's
minimum is 0.3, both stay unchanged and cash remains 0.4.

Invalid inputs or insufficient existing target weight raise `IncomeBudgetError`.
A consumer must treat that result as non-actionable; it must not synthesize
zero targets, discard locked holdings or substitute an unchecked risk target.
The budget helper does not itself authorize trading, certify account cash, or
allocate between independent strategies that share an account.

Cash balances, settled and available cash, dividends, reinvestment, fees and
external contributions retain their own accounting contracts. The budgeting
change does not supply income evidence or change strategy selection, caps,
position ownership or risk gates. The combo now reads supplied income holdings
as minimum targets rather than assuming no holdings; this is a versioned
behavior change. Qualification
of a particular income basket remains a separate evidence decision.

If the declared core budget cannot fund locked holdings while preserving its
implicit cash, public consumers currently abort with `IncomeBudgetError` before
returning a decision. No executable empty target or fallback is returned. This
also delays that cycle's otherwise valid reduction targets; independent broker
risk handling must remain active. Automated resolution of conflicting holding
locks, risk-off targets and cash policy is not introduced by this patch.
