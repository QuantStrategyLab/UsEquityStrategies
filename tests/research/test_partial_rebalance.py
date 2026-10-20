"""Synthetic B5 oracles; no market fixtures, network, orders, or ledger imports."""

import dataclasses
import inspect
import math
import unittest
from copy import deepcopy
from fractions import Fraction as F

from us_equity_strategies.research.partial_rebalance import (
    HardPolicyContext,
    SecurityPolicy,
    decide_partial_rebalance,
)


def policy(**changes):
    values = dict(
        securities=(SecurityPolicy('RISK', True, 1.0, 'TEST', 0.8),
                    SecurityPolicy('BOXX', False, 1.0, 'BILL', 1.0)),
        max_effective_risk_exposure=0.8,
        max_underlying_exposure=(('TEST', 0.8),),
        vetoed_symbols=(),
        source='synthetic-original-gate-v1',
        classification_source='synthetic-classification-v1',
        synthetic_only=True,
        cap_basis='DECISION_TIME_TARGET',
        max_one_way_risk_turnover=None,
        turnover_basis='RISK_BUY_WEIGHT',
        weight_unit='NAV_FRACTION',
    )
    values.update(changes)
    return HardPolicyContext(**values)


def decide(**changes):
    values = dict(
        security_universe=('RISK', 'BOXX'),
        current_weights={'RISK': 0.4, 'BOXX': 0.58},
        full_target={'RISK': 0.6, 'BOXX': 0.38},
        mode='PROPORTIONAL',
        decision_session='2030-01-02',
        cutoff='2030-01-02T21:05:00Z',
        hard_policy_context=policy(),
    )
    values.update(changes)
    return decide_partial_rebalance(**values)


class PartialRebalanceTests(unittest.TestCase):
    def rejected(self, result, reason=None, status='REJECTED'):
        self.assertEqual(result.status, status)
        self.assertIsNone(result.kind)
        self.assertIsNone(result.target_weights)
        self.assertIsNone(result.residual_cash)
        if reason:
            self.assertEqual(result.reason, reason)

    def test_proportional_default_independent_constants(self):
        result = decide()
        self.assertEqual(result.status, 'ACCEPTED')
        self.assertEqual(result.kind, 'TARGET_WEIGHTS')
        weights = dict(result.target_weights)
        self.assertEqual(weights['RISK'], float(F(1, 2)))
        self.assertEqual(weights['BOXX'], float(F(12, 25)))
        self.assertAlmostEqual(result.residual_cash, float(F(1, 50)))
        self.assertAlmostEqual(sum(weights.values()) + result.residual_cash, 1.0)

    def test_full_and_lambda_one_identical(self):
        # Mode/parameter diagnostics differ; the action and exact target do not.
        full = decide(mode='FULL')
        endpoint = decide(lambda_value=1)
        self.assertEqual(full.target_weights, endpoint.target_weights)
        self.assertEqual(full.residual_cash, endpoint.residual_cash)
        self.assertEqual(full.kind, endpoint.kind)
        self.assertEqual(dict(full.target_weights), {'RISK': 0.6, 'BOXX': 0.38})

    def test_lambda_zero_has_no_target_or_target_cash(self):
        result = decide(lambda_value=0)
        self.assertEqual(result.kind, 'HOLD_POSITIONS')
        self.assertIsNone(result.target_weights)
        self.assertIsNone(result.residual_cash)

    def test_band_default_inside(self):
        result = decide(mode='BAND_HOLD', full_target={'RISK': 0.405, 'BOXX': 0.575})
        self.assertEqual(result.kind, 'HOLD_POSITIONS')
        self.assertIsNone(result.target_weights)

    def test_band_binary_equality(self):
        # .41-.4 rounds away from .01. Only the band comparison has ulp tolerance.
        result = decide(mode='BAND_HOLD', full_target={'RISK': 0.41, 'BOXX': 0.57})
        self.assertEqual(result.kind, 'HOLD_POSITIONS')

    def test_band_outside_returns_full_not_edge(self):
        result = decide(mode='BAND_HOLD', full_target={'RISK': 0.411, 'BOXX': 0.569})
        self.assertEqual(dict(result.target_weights), {'RISK': 0.411, 'BOXX': 0.569})

    def test_band_includes_boxx_not_only_risk(self):
        result = decide(mode='BAND_HOLD', full_target={'RISK': 0.4, 'BOXX': 0.5})
        self.assertEqual(result.kind, 'TARGET_WEIGHTS')

    def test_zero_band_requires_exact_equal_weights(self):
        self.assertEqual(decide(mode='BAND_HOLD', band=0,
                                full_target={'RISK': 0.4, 'BOXX': 0.58}).kind,
                         'HOLD_POSITIONS')
        self.assertEqual(decide(mode='BAND_HOLD', band=0,
                                full_target={'RISK': math.nextafter(0.4, 1),
                                             'BOXX': 0.58}).kind, 'TARGET_WEIGHTS')

    def test_residual_cash_not_normalized_or_boxx(self):
        result = decide(mode='FULL', full_target={'RISK': 0.2, 'BOXX': 0.3})
        self.assertEqual(dict(result.target_weights), {'RISK': 0.2, 'BOXX': 0.3})
        self.assertEqual(result.residual_cash, 0.5)

    def test_all_cash_and_fully_invested_boundaries(self):
        for weights in ({'RISK': 0, 'BOXX': 0}, {'RISK': 0, 'BOXX': 1}):
            with self.subTest(weights=weights):
                result = decide(mode='FULL', full_target=weights)
                self.assertEqual(result.status, 'ACCEPTED')
                self.assertEqual(result.residual_cash, 1 - weights['BOXX'])

    def test_invalid_weights(self):
        for field in ('current_weights', 'full_target', 'forced_target'):
            for value in (True, '0.4', None, float('nan'), float('inf'),
                          -float('inf'), -0.01, 1.01, 10**400):
                with self.subTest(field=field, value=value):
                    self.rejected(decide(**{field: {'RISK': value, 'BOXX': 0}}))

    def test_no_borrowing_even_tiny_overfunding(self):
        for field in ('current_weights', 'full_target'):
            self.rejected(decide(**{field: {'RISK': math.nextafter(0.5, 1),
                                            'BOXX': 0.5}}))

    def test_missing_extra_and_sequence_weights(self):
        for field in ('current_weights', 'full_target', 'forced_target'):
            for weights in ({'RISK': 0.4}, {'RISK': 0.4, 'BOXX': 0.5, 'X': 0},
                            [('RISK', 0.2), ('RISK', 0.2), ('BOXX', 0.5)], None):
                if field == 'forced_target' and weights is None:
                    continue
                with self.subTest(field=field, weights=weights):
                    self.rejected(decide(**{field: weights}))

    def test_invalid_universe(self):
        for universe in (('RISK',), ('RISK', 'BOXX', 'BOXX'), ('RISK', 'BOXX', 'X'),
                         ('RISK', 'boxx'), (), 'RISK', ['RISK', 'BOXX'], (True, 'BOXX')):
            with self.subTest(universe=universe):
                self.rejected(decide(security_universe=universe))

    def test_invalid_parameters_modes_and_units(self):
        for field in ('lambda_value', 'band'):
            for value in (True, None, '1%', '0.5', -0.1, 1.1, float('nan'),
                          float('inf'), 10**400):
                with self.subTest(field=field, value=value):
                    self.rejected(decide(**{field: value}))
        for mode in ('PARTIAL', 'full', None, True, []):
            self.rejected(decide(mode=mode))
        self.rejected(decide(hard_policy_context=policy(weight_unit='PERCENT')))

    def test_session_and_utc_cutoff(self):
        for session, cutoff in [('2030-02-30', '2030-01-02T21:05:00Z'),
                                ('2030-01-03', '2030-01-02T21:05:00Z'),
                                ('2030-01-02', '2030-01-02T21:05:00'),
                                ('2030-01-02', '2030-01-02T21:05:00+08:00'),
                                (None, True)]:
            self.rejected(decide(decision_session=session, cutoff=cutoff))

    def test_metadata_summary_and_no_financial_authorization(self):
        result = decide()
        self.assertEqual(result.scope, 'SYNTHETIC_ONLY')
        self.assertFalse(result.live_ready)
        self.assertFalse(result.order_authorized)
        self.assertFalse(result.policy_authenticated)
        self.assertFalse(result.pit_authenticated)
        self.assertEqual(result.decision_session, '2030-01-02')
        self.assertEqual(result.cutoff, '2030-01-02T21:05:00Z')
        self.assertEqual(dict(result.parameter_units)['band'], 'ABSOLUTE_NAV_FRACTION')
        self.assertEqual(dict(result.parameter_units)['lambda_value'], 'DECISION_GAP_FRACTION')
        self.assertEqual(len(result.input_summary.sha256), 64)
        self.assertAlmostEqual(result.input_summary.current_residual_cash, 0.02)
        self.assertIn('ACTUAL_DRIFT_FEES_NET_CASH_AND_HARD_POLICY',
                      result.requires_downstream_verification)

    def test_determinism_order_independence_and_immutability(self):
        current = {'RISK': 0.4, 'BOXX': 0.58}
        target = {'RISK': 0.6, 'BOXX': 0.38}
        ctx = policy()
        before = deepcopy((current, target, ctx))
        result = decide(current_weights=current, full_target=target, hard_policy_context=ctx)
        self.assertEqual((current, target, ctx), before)
        self.assertEqual(result, decide(current_weights=dict(reversed(list(current.items()))),
                                        full_target=target, hard_policy_context=ctx))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.kind = 'HOLD_POSITIONS'
        with self.assertRaises(TypeError):
            result.target_weights[0] = ('RISK', 1)
        current['RISK'] = 0
        self.assertEqual(dict(result.target_weights)['RISK'], 0.5)
        self.assertNotEqual(result.input_summary.sha256,
                            decide(full_target={'RISK': 0.61, 'BOXX': 0.37}).input_summary.sha256)

    def test_no_future_returns_parameter(self):
        self.assertNotIn('future_returns', inspect.signature(decide_partial_rebalance).parameters)
        with self.assertRaises(TypeError):
            decide(future_returns={'RISK': 100})

    def test_missing_policy_or_sources(self):
        for ctx in (None, {}, policy(source=''), policy(classification_source=''),
                    policy(synthetic_only=False), policy(synthetic_only=1)):
            self.rejected(decide(hard_policy_context=ctx))

    def test_invalid_classification_or_incomplete_caps(self):
        base = policy()
        for securities in ((), base.securities[:1], base.securities * 2,
                           (dataclasses.replace(base.securities[0], is_risk=1),
                            base.securities[1])):
            self.rejected(decide(hard_policy_context=policy(securities=securities)))
        for field in ('max_weight', 'effective_exposure_factor'):
            for value in (None, True, -1, float('inf'), float('nan')):
                spec = dataclasses.replace(base.securities[0], **{field: value})
                self.rejected(decide(hard_policy_context=policy(
                    securities=(spec, base.securities[1]))))
        for limits in ((), (('UNKNOWN', 1),), (('TEST', 1), ('TEST', 1)),
                       (('TEST', None),), (('TEST', True),)):
            self.rejected(decide(hard_policy_context=policy(max_underlying_exposure=limits)))

    def test_missing_illegal_effective_cap_and_turnover(self):
        for field in ('max_effective_risk_exposure', 'max_one_way_risk_turnover'):
            for value in (True, '1', -1, float('nan'), float('inf')):
                self.rejected(decide(hard_policy_context=policy(**{field: value})))
        self.rejected(decide(hard_policy_context=policy(max_effective_risk_exposure=None)))

    def test_full_target_is_gated_input_even_if_half_would_pass(self):
        self.rejected(decide(hard_policy_context=policy(max_effective_risk_exposure=0.55)),
                      'HARD_CAP_EFFECTIVE_EXPOSURE')

    def test_hold_cannot_keep_current_over_cap(self):
        ctx = policy(max_effective_risk_exposure=0.5)
        for mode, params in [('PROPORTIONAL', {'lambda_value': 0}),
                             ('BAND_HOLD', {'band': 1})]:
            self.rejected(decide(mode=mode, current_weights={'RISK': 0.6, 'BOXX': 0.38},
                                 full_target={'RISK': 0.4, 'BOXX': 0.58},
                                 hard_policy_context=ctx, **params), 'HARD_CAP_EFFECTIVE_EXPOSURE')

    def test_hard_cap_no_epsilon_permission(self):
        ctx = policy(max_effective_risk_exposure=0.5)
        self.assertEqual(decide(mode='FULL', full_target={'RISK': 0.5, 'BOXX': 0.48},
                                hard_policy_context=ctx).status, 'ACCEPTED')
        self.rejected(decide(mode='FULL', full_target={'RISK': math.nextafter(0.5, 1),
                                                      'BOXX': 0.48},
                             hard_policy_context=ctx), 'HARD_CAP_EFFECTIVE_EXPOSURE')

    def test_symbol_and_underlying_cap_and_veto(self):
        base = policy()
        spec = dataclasses.replace(base.securities[0], max_weight=0.59)
        self.rejected(decide(hard_policy_context=policy(securities=(spec, base.securities[1]))),
                      'HARD_CAP_SYMBOL_WEIGHT')
        self.rejected(decide(hard_policy_context=policy(max_underlying_exposure=(('TEST', 0.59),))),
                      'HARD_CAP_UNDERLYING_EXPOSURE')
        self.rejected(decide(hard_policy_context=policy(vetoed_symbols=('RISK',))), 'HARD_VETO')
        self.rejected(decide(hard_policy_context=policy(vetoed_symbols=('UNKNOWN',))))

    def test_boxx_risk_classification_is_explicit(self):
        ctx = policy(securities=(SecurityPolicy('RISK', False, 1, 'TEST', 0.8),
                                 SecurityPolicy('BOXX', True, 2, 'BILL', 1)),
                     max_underlying_exposure=(('BILL', 0.7),))
        self.rejected(decide(hard_policy_context=ctx), 'HARD_CAP_UNDERLYING_EXPOSURE')

    def test_mandatory_same_target_all_modes_and_extreme_throttles(self):
        for mode in ('FULL', 'PROPORTIONAL', 'BAND_HOLD'):
            for lam in (0, 0.5, 1):
                result = decide(mode=mode, lambda_value=lam, band=1,
                                current_weights={'RISK': 0.6, 'BOXX': 0.38},
                                full_target={'RISK': 0.4, 'BOXX': 0.58},
                                mandatory_reduce=True,
                                forced_target={'RISK': 0.4, 'BOXX': 0.58},
                                forced_source='synthetic-original-gate-v1',
                                forced_reason='SYNTHETIC_CAP_REDUCTION')
                self.assertEqual(result.kind, 'TARGET_WEIGHTS')
                self.assertEqual(dict(result.target_weights)['RISK'], 0.4)
                self.assertEqual(result.reason, 'MANDATORY_REDUCTION')
                self.assertEqual(result.forced_source, 'synthetic-original-gate-v1')
                self.assertEqual(result.forced_reason, 'SYNTHETIC_CAP_REDUCTION')

    def forced(self, **changes):
        values = dict(mandatory_reduce=True,
                      current_weights={'RISK': 0.6, 'BOXX': 0.38},
                      full_target={'RISK': 0.4, 'BOXX': 0.58},
                      forced_target={'RISK': 0.4, 'BOXX': 0.58},
                      forced_source='synthetic-original-gate-v1', forced_reason='SYNTHETIC_EXIT')
        values.update(changes)
        return decide(**values)

    def test_forced_requires_matching_source_and_reason(self):
        for changes in ({'forced_target': None}, {'forced_source': ''},
                        {'forced_source': 'other'}, {'forced_reason': ''},
                        {'mandatory_reduce': 1}):
            self.rejected(self.forced(**changes))

    def test_forced_cannot_increase_any_risk_security(self):
        self.rejected(self.forced(forced_target={'RISK': 0.61, 'BOXX': 0.37}),
                      'FORCED_RISK_INCREASE')
        # BOXX explicitly classified as risk must also not increase.
        ctx = policy(securities=(SecurityPolicy('RISK', True, 1, 'TEST', 0.8),
                                 SecurityPolicy('BOXX', True, 1, 'BILL', 1)),
                     max_effective_risk_exposure=1,
                     max_underlying_exposure=(('TEST', 0.8), ('BILL', 1)))
        self.rejected(self.forced(hard_policy_context=ctx), 'FORCED_RISK_INCREASE')

    def test_forced_still_subject_to_cap_and_veto(self):
        self.rejected(self.forced(hard_policy_context=policy(max_effective_risk_exposure=0.3)),
                      'HARD_CAP_EFFECTIVE_EXPOSURE')
        self.rejected(self.forced(hard_policy_context=policy(vetoed_symbols=('RISK',))),
                      'HARD_VETO')

    def test_risk_buy_turnover_not_l1(self):
        # Sell .2 and buy BOXX .2: risk BUY turnover is zero, even at zero limit.
        ctx = policy(max_one_way_risk_turnover=0)
        self.assertEqual(self.forced(hard_policy_context=ctx).status, 'ACCEPTED')
        self.rejected(decide(mode='FULL', hard_policy_context=ctx), 'HARD_CAP_RISK_BUY_TURNOVER')
        self.assertEqual(decide(mode='FULL', current_weights={'RISK': 0.6, 'BOXX': 0.38},
                                full_target={'RISK': 0.4, 'BOXX': 0.58},
                                hard_policy_context=ctx).status, 'ACCEPTED')

    def test_turnover_checks_selected_partial_not_unexecuted_full(self):
        self.assertEqual(decide(hard_policy_context=policy(
            max_one_way_risk_turnover=0.15)).status, 'ACCEPTED')
        self.rejected(decide(mode='FULL', hard_policy_context=policy(
            max_one_way_risk_turnover=0.15)), 'HARD_CAP_RISK_BUY_TURNOVER')

    def test_turnover_boundary_has_no_epsilon(self):
        ctx = policy(max_one_way_risk_turnover=0.125)
        self.assertEqual(decide(mode='FULL', current_weights={'RISK': 0.375, 'BOXX': 0.5},
                                full_target={'RISK': 0.5, 'BOXX': 0.375},
                                hard_policy_context=ctx).status, 'ACCEPTED')
        self.rejected(decide(mode='FULL', current_weights={'RISK': 0.375, 'BOXX': 0.5},
                             full_target={'RISK': math.nextafter(0.5, 1), 'BOXX': 0.375},
                             hard_policy_context=ctx), 'HARD_CAP_RISK_BUY_TURNOVER')

    def test_exact_multisecurity_hard_exposure_sum(self):
        ctx = policy(securities=(SecurityPolicy('RISK', True, 1, 'TEST', 0.8),
                                 SecurityPolicy('OTHER', True, 1, 'TEST', 0.8),
                                 SecurityPolicy('BOXX', False, 1, 'BILL', 1)),
                     max_effective_risk_exposure=0.3,
                     max_underlying_exposure=(('TEST', 0.8),))
        # Exact binary .1+.2 exceeds binary .3; rounded sums cannot grant risk.
        self.rejected(decide(mode='FULL', security_universe=('RISK', 'OTHER', 'BOXX'),
                             current_weights={'RISK': 0, 'OTHER': 0, 'BOXX': 0.5},
                             full_target={'RISK': 0.1, 'OTHER': 0.2, 'BOXX': 0.5},
                             hard_policy_context=ctx), 'HARD_CAP_EFFECTIVE_EXPOSURE')

    def test_forced_per_security_not_aggregate_decrease(self):
        ctx = policy(securities=(SecurityPolicy('RISK', True, 1, 'TEST', 0.8),
                                 SecurityPolicy('OTHER', True, 1, 'TEST', 0.8),
                                 SecurityPolicy('BOXX', False, 1, 'BILL', 1)),
                     max_effective_risk_exposure=0.8)
        self.rejected(self.forced(security_universe=('RISK', 'OTHER', 'BOXX'),
                                 current_weights={'RISK': 0.5, 'OTHER': 0.1, 'BOXX': 0.38},
                                 full_target={'RISK': 0.2, 'OTHER': 0.2, 'BOXX': 0.58},
                                 forced_target={'RISK': 0.2, 'OTHER': 0.2, 'BOXX': 0.58},
                                 hard_policy_context=ctx), 'FORCED_RISK_INCREASE')

    def test_forced_can_exit_current_veto_without_revalidating_old_book(self):
        result = self.forced(full_target={'RISK': 0, 'BOXX': 0.98},
                             forced_target={'RISK': 0, 'BOXX': 0.98},
                             hard_policy_context=policy(vetoed_symbols=('RISK',),
                                                        max_effective_risk_exposure=0))
        self.assertEqual(result.status, 'ACCEPTED')
        self.assertEqual(dict(result.target_weights)['RISK'], 0)

    def test_boxx_cap_and_veto_apply_even_when_nonrisk(self):
        base = policy()
        ctx = policy(securities=(base.securities[0],
                                 dataclasses.replace(base.securities[1], max_weight=0.3)))
        self.rejected(decide(hard_policy_context=ctx), 'HARD_CAP_SYMBOL_WEIGHT')
        self.rejected(decide(hard_policy_context=policy(vetoed_symbols=('BOXX',))), 'HARD_VETO')

    def test_unknown_turnover_basis_and_mandatory_conflict(self):
        for basis in ('L1', 'HALF_L1', 'UNKNOWN', None):
            ctx = policy(max_one_way_risk_turnover=0.1, turnover_basis=basis)
            self.rejected(decide(hard_policy_context=ctx), 'UNSUPPORTED_TURNOVER_BASIS',
                          status='UNSUPPORTED')
            self.rejected(self.forced(hard_policy_context=ctx),
                          'UNSUPPORTED_RISK_THROTTLE_CONFLICT', status='UNSUPPORTED')

    def test_post_fee_and_fill_time_caps_are_unsupported_for_all_actions(self):
        for basis in ('POST_FEE', 'FILL_TIME', 'UNKNOWN', None):
            ctx = policy(cap_basis=basis)
            for result in (decide(hard_policy_context=ctx),
                           decide(lambda_value=0, hard_policy_context=ctx),
                           self.forced(hard_policy_context=ctx)):
                self.rejected(result, 'UNSUPPORTED_CAP_BASIS_REQUIRES_DOWNSTREAM_VERIFICATION',
                              status='UNSUPPORTED')

    def test_fee_oracle_does_not_certify_postfee_cap(self):
        # Independent rational net security fees: no implementation-derived oracle.
        fee = (F(1, 10) + F(1, 10)) * F(10, 10000)
        self.assertEqual(fee, F(1, 5000))
        self.assertEqual(F(1, 50) - fee, F(99, 5000))
        risk = F(1, 2) / (1 - fee)
        self.assertEqual(risk, F(2500, 4999))
        self.assertGreater(risk, F(1, 2))
        # After actual net fees, the next supplied current weights must differ.
        post = {'RISK': float(risk), 'BOXX': float(F(12, 25) / (1 - fee))}
        next_decision = decide(current_weights=post)
        self.assertNotEqual(next_decision.input_summary.sha256, decide().input_summary.sha256)
        self.assertAlmostEqual(dict(next_decision.target_weights)['RISK'],
                               float(F(27497, 49990)))
        self.rejected(decide(hard_policy_context=policy(cap_basis='POST_FEE')),
                      status='UNSUPPORTED')

    def test_hold_drift_oracle_and_wrong_fixed_target_trade(self):
        result = decide(mode='BAND_HOLD', full_target={'RISK': 0.405, 'BOXX': 0.575})
        self.assertEqual(result.kind, 'HOLD_POSITIONS')
        self.assertIsNone(result.target_weights)
        old_risk_value = F(2, 5) * F(102, 100)
        nav = F(3, 5) + old_risk_value
        self.assertEqual(old_risk_value / nav, F(17, 42))
        self.assertEqual(old_risk_value - F(2, 5) * nav, F(3, 625))
        hold_trade, hold_fee = F(0), F(0)  # Same quantities: nothing to net or charge.
        self.assertEqual((hold_trade, hold_fee), (0, 0))

    def test_fill_drift_proportional_gap_not_half_actual_trade(self):
        full = decide(mode='FULL')
        half = decide()
        nav = F(27, 25)
        old_risk_value = F(12, 25)
        full_buy = F(3, 5) * nav - old_risk_value
        half_buy = F(1, 2) * nav - old_risk_value
        self.assertEqual(full_buy, F(21, 125))
        self.assertEqual(half_buy, F(3, 50))
        self.assertNotEqual(half_buy, full_buy / 2)
        self.assertEqual(dict(full.target_weights)['RISK'], 0.6)
        self.assertEqual(dict(half.target_weights)['RISK'], 0.5)


if __name__ == '__main__':
    unittest.main()


class RetrospectivePolicyAdmissionTests(unittest.TestCase):
    def context(self, **changes):
        from us_equity_strategies.research.partial_rebalance import RetrospectivePolicyContext
        values = dataclasses.asdict(policy())
        values['securities'] = policy().securities
        values.update(scope='RS04:TQQQ', exact_source=values['source'],
            code_sha256='a'*64, config_sha256='b'*64, source_identity='fixture-identity',
            dataset_class='REAL_ARCHIVED_RAW_ACTIONS', synthetic_only=False)
        values.update(changes)
        return RetrospectivePolicyContext(**values)

    def test_explicit_context_preserves_real_scope_and_all_original_limits(self):
        context = self.context()
        result = decide(hard_policy_context=context)
        self.assertEqual(result.status, 'ACCEPTED')
        self.assertEqual(result.scope, 'RETROSPECTIVE_RESEARCH_ONLY')
        self.assertFalse(context.synthetic_only)
        self.assertFalse(result.pit_authenticated)
        self.assertEqual(dict(result.target_weights), {'RISK': .5, 'BOXX': .48})
        veto = decide(hard_policy_context=dataclasses.replace(context, vetoed_symbols=('RISK',)))
        self.assertEqual(veto.reason, 'HARD_VETO')
        forced = decide(hard_policy_context=context, mandatory_reduce=True,
                        forced_target={'RISK': .1, 'BOXX': .5},
                        forced_source=context.source, forced_reason='fixture-original-reduction')
        self.assertEqual(forced.reason, 'MANDATORY_REDUCTION')
        cap = decide(hard_policy_context=dataclasses.replace(context, max_effective_risk_exposure=.2))
        self.assertEqual(cap.reason, 'HARD_CAP_EFFECTIVE_EXPOSURE')

    def test_no_implicit_real_relabel_or_postfee_permission(self):
        for changes in ({'synthetic_only': True}, {'scope':'RS04:R4'}, {'config_sha256':''},
                        {'exact_source':'different'}, {'clock':'PIT_AVAILABLE_AT'}):
            with self.subTest(changes=changes):
                self.assertEqual(decide(hard_policy_context=self.context(**changes)).status, 'REJECTED')
        result = decide(hard_policy_context=self.context(cap_basis='ACTUAL_POST_FEE'))
        self.assertEqual(result.status, 'UNSUPPORTED')
        self.assertIsNone(result.target_weights)
        # An old unadmitted context cannot turn real merely by flipping its flag.
        result = decide(hard_policy_context=policy(synthetic_only=False))
        self.assertEqual(result.reason, 'INVALID_POLICY_SOURCE_SCOPE_OR_UNITS')


class NoPluginCoreSourcePolicyTests(unittest.TestCase):
    def context(self):
        from us_equity_strategies.research.partial_rebalance import (
            NoPluginCorePolicyContext, CORE_ONLY_MAPPING_SHA256, CORE_ONLY_CONFIG_SHA256, CORE_ONLY_SOURCE_SHA256)
        return NoPluginCorePolicyContext('TQQQ','artificial-source','REAL_ARCHIVED_RAW_ACTIONS','a'*64,
            CORE_ONLY_MAPPING_SHA256, CORE_ONLY_CONFIG_SHA256,tuple(sorted(CORE_ONLY_SOURCE_SHA256.items())))

    def test_source_only_has_no_fabricated_caps_or_classification(self):
        context = self.context()
        result = decide_partial_rebalance(security_universe=('TQQQ','QQQM','BOXX'),
            current_weights={'TQQQ':0.,'QQQM':0.,'BOXX':0.},
            full_target={'TQQQ':.6,'QQQM':.1,'BOXX':.27},mode='FULL',
            decision_session='2030-01-02',cutoff='2030-01-02T23:00:00+00:00',hard_policy_context=context)
        self.assertEqual(result.status,'ACCEPTED')
        self.assertEqual(result.scope,'RETROSPECTIVE_CORE_ONLY_SOURCE_RULES')
        self.assertFalse(context.synthetic_only)
        self.assertFalse(hasattr(context,'securities'))
        self.assertFalse(hasattr(context,'max_effective_risk_exposure'))

    def test_domain_and_enabled_external_rule_still_refuse(self):
        import dataclasses
        args = dict(security_universe=('TQQQ','QQQM','BOXX'),current_weights={'TQQQ':0.,'QQQM':0.,'BOXX':0.},
            full_target={'TQQQ':.6,'QQQM':.1,'BOXX':.27},mode='FULL',decision_session='2030-01-02',
            cutoff='2030-01-02T23:00:00+00:00',hard_policy_context=self.context())
        for changes in ({'full_target':{'TQQQ':1.,'QQQM':0.,'BOXX':.1}},
                {'full_target':{'TQQQ':float('nan'),'QQQM':0.,'BOXX':0.}},
                {'mandatory_reduce':True,'forced_target':{'TQQQ':0.,'QQQM':0.,'BOXX':0.},
                 'forced_source':self.context().source,'forced_reason':'not-in-source'},
                {'hard_policy_context':dataclasses.replace(self.context(),config_sha256='0'*64)},
                {'security_universe':('OTHER','QQQM','BOXX')}):
            with self.subTest(changes=changes):
                self.assertNotEqual(decide_partial_rebalance(**(args | changes)).status,'ACCEPTED')
