"""Independent synthetic arithmetic and admission checks; no strategy or data I/O."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
import json
import math
import unittest

from us_equity_strategies.research.bayesian_kelly import (
    B4Error, B4Observation, B4RebuildRequired, B4Spec, LOG_RETURN_UNIT,
    batch_b4, b4_fractional_target, b4_predictive_moments, initialise_b4,
    state_from_dict, state_to_dict, update_b4,
)

ANCHOR = datetime(2026, 1, 1, 21, tzinfo=timezone.utc)
SPEC = B4Spec(("TQQQ",), ANCHOR)
SIGMA = ((.0004,),)
TRAINING = "synthetic-fixed-covariance-training"


def observation(i, values=(.002,), *, spec=SPEC, revision=1, identity=None):
    close = ANCHOR + timedelta(days=i)
    return B4Observation(identity or f"session-{i}", revision, close,
                         close + timedelta(seconds=1), spec.core_ids, values)


def admit(state, obs):
    cutoff = obs.available_at + timedelta(seconds=1)
    if state.last_cutoff is not None:
        cutoff = max(cutoff, state.last_cutoff)
    return update_b4(state, obs, decision_cutoff=cutoff)


def example():
    s = initialise_b4(SPEC, SIGMA, TRAINING)
    s = admit(s, observation(1)).state
    return admit(s, observation(2, (-.001,))).state


def resign(payload):
    body = {k: v for k, v in payload.items() if k != "content_sha256"}
    payload["content_sha256"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return payload


class BayesianKellyTests(unittest.TestCase):
    def close(self, actual, expected):
        self.assertLessEqual(abs(actual - expected), 1e-12 + 1e-10 * abs(expected))

    def test_prediction_scaling_zero_available_is_not_unknown_or_prior(self):
        """Only an admitted negative mean produces a numerical zero target."""
        prior = b4_predictive_moments(initialise_b4(SPEC, SIGMA, TRAINING))
        state = admit(initialise_b4(SPEC, SIGMA, TRAINING),
                      observation(1, (-.002,))).state
        available = b4_predictive_moments(state)
        zero = b4_fractional_target(available, scope_cap={"TQQQ": 1.}, cap_source=TRAINING)
        unknown = b4_fractional_target(available, scope_cap=None, cap_source=TRAINING)
        opening = b4_fractional_target(prior, scope_cap={"TQQQ": 1.}, cap_source=TRAINING)
        self.assertEqual((available.status, zero.status, zero.targets),
                         ("AVAILABLE", "AVAILABLE", (0.,)))
        self.assertLess(available.mean_simple[0], 0.)
        self.assertEqual((unknown.status, unknown.targets, unknown.fractional_applied_once),
                         ("UNAVAILABLE_SCOPE_CAP", None, False))
        self.assertEqual((opening.status, opening.targets, opening.fractional_applied_once),
                         ("PRIOR_ONLY", None, False))
        for result in (zero, unknown, opening):
            self.assertTrue(result.no_order)
            self.assertFalse(result.live_ready)
            self.assertEqual(result.budget_effect, "NONE")

    def test_prediction_scaling_moment_units_and_invalid_numbers_do_not_make_targets(self):
        """Moment admission is distinct from observation admission and missing caps."""
        moments = b4_predictive_moments(example())
        with self.assertRaisesRegex(B4Error, "SIMPLE_RETURN_MOMENTS_REQUIRED"):
            b4_fractional_target(replace(moments, unit=LOG_RETURN_UNIT),
                                 scope_cap={"TQQQ": 1.}, cap_source=TRAINING)
        for value in (None, float('nan'), float('inf'), float('-inf')):
            with self.subTest(value=value), self.assertRaises(B4Error):
                b4_fractional_target(replace(moments, mean_simple=(value,)),
                                     scope_cap={"TQQQ": 1.}, cap_source=TRAINING)
        with self.assertRaises(B4Error):
            b4_fractional_target(moments, scope_cap={"TQQQ": 1.}, cap_source="")

    def test_hand_posterior_sequence(self):
        state = initialise_b4(SPEC, SIGMA, TRAINING)
        first = admit(state, observation(1))
        self.assertEqual(first.status, "UPDATED")
        self.close(b4_predictive_moments(first.state).mean_log[0], .0008)
        second = admit(first.state, observation(2, (-.001,))).state
        m = b4_predictive_moments(second)
        self.assertEqual(second.n, 2)
        self.close(m.mean_log[0], .0002)
        self.close(m.parameter_cov_log[0][0], .0004 / 3)
        self.close(m.predictive_cov_log[0][0], .0004 * 4 / 3)
        self.assertEqual(m.innovation_cov_log, SIGMA)

    def test_predictive_prior_is_centered_in_simple_return(self):
        m = b4_predictive_moments(initialise_b4(SPEC, SIGMA, TRAINING))
        self.close(m.mean_simple[0], 0.)
        self.assertEqual(m.status, "PRIOR_ONLY")
        self.assertEqual(m.mean_log, (-.0004,))
        self.assertGreater(math.expm1(.0008 / 2), 0.)  # zero log mean is not neutral

    def test_prior_has_no_target_even_when_veto_or_caps_present(self):
        m = b4_predictive_moments(initialise_b4(SPEC, SIGMA, TRAINING))
        for veto in (False, True):
            out = b4_fractional_target(m, scope_cap={"TQQQ": .4}, cap_source="toy", veto=veto)
            self.assertEqual(out.status, "PRIOR_ONLY")
            self.assertIsNone(out.targets)
            self.assertFalse(out.fractional_applied_once)

    def test_independent_decimal_moments_and_full_second_moment(self):
        m = b4_predictive_moments(example())
        with localcontext() as ctx:
            ctx.prec = 70
            v = Decimal('.0004') / 3
            p = Decimal('.0004') + v
            a = Decimal('.0002') + p / 2
            mean = a.exp() - 1
            variance = (2 * a).exp() * (p.exp() - 1)
            q = variance + mean * mean
            self.close(m.mean_simple[0], float(mean))
            self.close(m.predictive_cov_simple[0][0], float(variance))
            self.close(m.second_raw_moment_simple[0], float(q))
            out = b4_fractional_target(m, scope_cap={"TQQQ": 1.}, cap_source="synthetic")
            self.close(out.targets[0], float(mean / q / 4))
            self.close(out.targets[0], .2184495084045708)
            self.assertNotEqual(out.targets[0], float(mean / variance / 4))

    def test_correlated_two_dimensional_covariance_decomposition(self):
        spec = B4Spec(("TQQQ", "SOXL"), ANCHOR)
        sigma = ((.0004, .00012), (.00012, .0009))
        obs = [observation(1, (.002, .001), spec=spec),
               observation(2, (-.001, .002), spec=spec)]
        state = batch_b4(spec, sigma, TRAINING, obs, decision_cutoff=obs[-1].available_at)
        m = b4_predictive_moments(state)
        self.close(m.mean_log[0], .0002)
        self.close(m.mean_log[1], .0007)
        self.close(m.predictive_cov_simple[0][1], .0001602957398198809)
        for i in range(2):
            for j in range(2):
                self.close(m.predictive_cov_simple[i][j],
                           m.parameter_cov_simple[i][j] + m.innovation_cov_simple[i][j])
        for matrix in (m.predictive_cov_simple, m.parameter_cov_simple, m.innovation_cov_simple):
            self.assertGreaterEqual(matrix[0][0], 0.)
            self.assertGreaterEqual(matrix[1][1], 0.)
            self.assertGreaterEqual(matrix[0][0] * matrix[1][1] - matrix[0][1] ** 2, -1e-18)

    def test_negative_cross_covariance_is_not_clipped(self):
        spec = B4Spec(("TQQQ", "SOXL"), ANCHOR)
        state = initialise_b4(spec, ((.0004, -.00012), (-.00012, .0009)), TRAINING)
        m = b4_predictive_moments(state)
        self.assertLess(m.predictive_cov_simple[0][1], 0.)

    def test_sequential_batch_and_checkpoint_restart_match(self):
        obs = [observation(i, ((-1) ** i * (i % 5) * .0001,)) for i in range(1, 41)]
        state = initialise_b4(SPEC, SIGMA, TRAINING)
        for x in obs[:17]:
            state = admit(state, x).state
        restored = state_from_dict(json.loads(json.dumps(state_to_dict(state))),
            expected_spec=SPEC, expected_sigma=SIGMA, expected_training_identity=TRAINING)
        for x in obs[17:]:
            restored = admit(restored, x).state
        batch = batch_b4(SPEC, SIGMA, TRAINING, obs, decision_cutoff=obs[-1].available_at + timedelta(seconds=1))
        a, b = b4_predictive_moments(restored), b4_predictive_moments(batch)
        self.close(a.mean_log[0], b.mean_log[0])
        self.close(a.mean_log[0], (-.0004 + math.fsum(x.values[0] for x in obs)) / 41)
        self.assertEqual(a.predictive_cov_log, b.predictive_cov_log)
        self.assertEqual(restored.n, batch.n)

    def test_checkpoint_roundtrip_restores_exact_compensation(self):
        state = initialise_b4(SPEC, SIGMA, TRAINING)
        for i, y in enumerate((1e16, 1., -1e16), 1):
            state = admit(state, observation(i, (y,))).state
        self.assertEqual(state.sum_y[0] + state.compensation[0], 1.)
        self.assertNotEqual(state.compensation[0], 0.)
        restored = state_from_dict(json.loads(json.dumps(state_to_dict(state))),
            expected_spec=SPEC, expected_sigma=SIGMA, expected_training_identity=TRAINING)
        self.assertEqual(restored, state)
        self.assertEqual(state_to_dict(restored), state_to_dict(state))

    def test_old_exact_duplicate_precedes_watermark_check(self):
        state = example()
        packet = state_to_dict(state)
        out = update_b4(state, observation(1), decision_cutoff=ANCHOR + timedelta(days=4))
        self.assertEqual(out.status, "DUPLICATE_NO_UPDATE")
        self.assertIs(out.state, state)
        self.assertEqual(state_to_dict(out.state), packet)

    def test_revision_requires_rebuild_and_does_not_mutate_old_state(self):
        state = example()
        packet = state_to_dict(state)
        with self.assertRaisesRegex(B4RebuildRequired, "REVISION_REQUIRES_REBUILD"):
            admit(state, observation(1, (.003,), revision=2))
        self.assertEqual(state_to_dict(state), packet)
        revised = [observation(1, (.003,), revision=2), observation(2, (-.001,))]
        rebuilt = batch_b4(SPEC, SIGMA, TRAINING, revised,
                           decision_cutoff=ANCHOR + timedelta(days=4))
        self.close(b4_predictive_moments(rebuilt).mean_log[0], (.003 - .001 - .0004) / 3)
        self.assertNotEqual(state_to_dict(rebuilt)["prefix_sha256"], packet["prefix_sha256"])

    def test_same_revision_conflict_requires_rebuild(self):
        with self.assertRaisesRegex(B4RebuildRequired, "CONTENT_CONFLICT"):
            admit(example(), observation(1, (.009,)))

    def test_out_of_order_new_identity_requires_rebuild(self):
        with self.assertRaisesRegex(B4RebuildRequired, "OUT_OF_ORDER"):
            admit(example(), observation(1, identity="other-session"))

    def test_future_available_observation_is_rejected(self):
        obs = observation(1)
        with self.assertRaisesRegex(B4Error, "FUTURE"):
            update_b4(initialise_b4(SPEC, SIGMA, TRAINING), obs,
                      decision_cutoff=obs.observed_at)

    def test_future_append_does_not_change_existing_prefix(self):
        state = example()
        before = b4_predictive_moments(state)
        admit(state, observation(3, (.9,)))
        self.assertEqual(b4_predictive_moments(state), before)

    def test_observation_before_training_end_is_not_reused(self):
        with self.assertRaisesRegex(B4Error, "TRAINING_OVERLAP"):
            admit(initialise_b4(SPEC, SIGMA, TRAINING), observation(0))

    def test_availability_cannot_precede_observation(self):
        obs = observation(1)
        with self.assertRaises(B4Error):
            admit(initialise_b4(SPEC, SIGMA, TRAINING), replace(obs, available_at=ANCHOR))

    def test_decision_cutoff_cannot_move_backwards(self):
        with self.assertRaisesRegex(B4Error, "CUTOFF"):
            update_b4(example(), observation(3), decision_cutoff=ANCHOR + timedelta(days=1))

    def test_unit_and_core_order_are_binding(self):
        base = initialise_b4(SPEC, SIGMA, TRAINING)
        for change in ({"unit": "ANNUALISED_PERCENT"}, {"core_ids": ("SOXL",)}, {"values": (.1, .2)}):
            with self.subTest(change=change), self.assertRaises(B4Error):
                admit(base, replace(observation(1), **change))

    def test_naive_timestamps_are_rejected(self):
        for change in ({"observed_at": datetime(2026, 1, 2)},
                       {"available_at": datetime(2026, 1, 2)}):
            with self.subTest(change=change), self.assertRaises(B4Error):
                admit(initialise_b4(SPEC, SIGMA, TRAINING), replace(observation(1), **change))

    def test_nonfinite_boolean_or_non_numeric_observations_rejected(self):
        for y in (True, float('nan'), float('inf'), float('-inf'), "0.1", None):
            with self.subTest(y=y), self.assertRaises(B4Error):
                admit(initialise_b4(SPEC, SIGMA, TRAINING), observation(1, (y,)))

    def test_invalid_covariance_is_rejected_without_jitter(self):
        bad = [((0.,),), ((-.1,),), ((True,),), ((float('nan'),),), ((float('inf'),),),
               ((1., 1.), (1., 1.)), ((1., 2.), (2., 1.)),
               ((1., .1), (.2, 1.)), ((1.,), (1.,))]
        for sigma in bad:
            spec = SPEC if len(sigma) == 1 else B4Spec(("TQQQ", "SOXL"), ANCHOR)
            with self.subTest(sigma=sigma), self.assertRaises(B4Error):
                initialise_b4(spec, sigma, TRAINING)

    def test_invalid_identity_revision_or_spec_is_rejected(self):
        for rev in (0, -1, True, 1.2):
            with self.subTest(revision=rev), self.assertRaises(B4Error):
                admit(initialise_b4(SPEC, SIGMA, TRAINING), replace(observation(1), revision=rev))
        for spec in (B4Spec(("TQQQ", "TQQQ"), ANCHOR), B4Spec(("BAD",), ANCHOR),
                     B4Spec(("TQQQ",), ANCHOR, unit="SIMPLE_PERCENT")):
            with self.subTest(spec=spec), self.assertRaises(B4Error):
                initialise_b4(spec, SIGMA, TRAINING)
        with self.assertRaises(B4Error):
            initialise_b4(SPEC, SIGMA, "")

    def test_moment_overflow_is_uncomputable_not_infinity(self):
        state = admit(initialise_b4(SPEC, SIGMA, TRAINING), observation(1, (10000.,))).state
        with self.assertRaisesRegex(B4Error, "UNCOMPUTABLE"):
            b4_predictive_moments(state)

    def test_compensated_sum_overflow_does_not_mutate(self):
        state = admit(initialise_b4(SPEC, SIGMA, TRAINING), observation(1, (1e308,))).state
        with self.assertRaises(B4Error):
            admit(state, observation(2, (1e308,)))
        self.assertEqual(state.n, 1)

    def test_checkpoint_expected_identity_order_and_sigma_required(self):
        packet = state_to_dict(example())
        cases = [(replace(SPEC, core_ids=("SOXL",)), SIGMA, TRAINING),
                 (SPEC, ((.0005,),), TRAINING), (SPEC, SIGMA, "other-training")]
        for spec, sigma, training in cases:
            with self.subTest(training=training, sigma=sigma), self.assertRaises(B4Error):
                state_from_dict(packet, expected_spec=spec, expected_sigma=sigma,
                                expected_training_identity=training)

    def test_checkpoint_integrity_and_unknown_fields(self):
        packet = state_to_dict(example())
        for key, value in (("n", 99), ("watermark", ANCHOR.isoformat()),
                           ("sum_y", [1.]), ("compensation", [1.]),
                           ("prefix_sha256", "0" * 64), ("unknown", True)):
            for fresh_digest in (False, True):
                altered = deepcopy(packet)
                altered[key] = value
                if fresh_digest:
                    resign(altered)
                with self.subTest(key=key, fresh_digest=fresh_digest), self.assertRaises(B4Error):
                    state_from_dict(altered, expected_spec=SPEC, expected_sigma=SIGMA,
                                    expected_training_identity=TRAINING)

    def test_checkpoint_record_tampering_is_detected_even_with_new_outer_digest(self):
        packet = state_to_dict(example())
        packet["records"][0]["observation"]["values"][0] = .009
        with self.assertRaises(B4Error):
            state_from_dict(resign(packet), expected_spec=SPEC, expected_sigma=SIGMA,
                            expected_training_identity=TRAINING)

    def test_cap_source_is_required_and_never_defaulted(self):
        m = b4_predictive_moments(example())
        for caps, source in ((None, None), ({"TQQQ": .4}, None), (None, "toy")):
            out = b4_fractional_target(m, scope_cap=caps, cap_source=source)
            self.assertEqual(out.status, "UNAVAILABLE_SCOPE_CAP")
            self.assertIsNone(out.targets)

    def test_fractional_once_cap_and_veto(self):
        m = b4_predictive_moments(example())
        a = b4_fractional_target(m, c=.25, scope_cap={"TQQQ": 1.}, cap_source="toy")
        b = b4_fractional_target(m, c=.4, scope_cap={"TQQQ": 1.}, cap_source="toy")
        self.close(b.targets[0], 1.6 * a.targets[0])
        capped = b4_fractional_target(m, scope_cap={"TQQQ": .1}, cap_source="toy")
        self.assertEqual(capped.targets, (.1,))
        self.assertEqual(b4_fractional_target(m, scope_cap={"TQQQ": 0.}, cap_source="toy").targets, (0.,))
        veto = b4_fractional_target(m, scope_cap={"TQQQ": 1.}, cap_source="toy", veto=True)
        self.assertEqual(veto.targets, (0.,))
        self.assertEqual(veto.status, "VETOED")
        self.assertTrue(a.no_order)
        self.assertFalse(a.live_ready)
        self.assertEqual(a.budget_effect, "NONE")
        self.assertEqual(a.approximation, "SECOND_ORDER_FRICTIONLESS_PROXY")
        self.assertTrue(a.fractional_applied_once)

    def test_approved_fractions_apply_before_cap_without_changing_requested_c(self):
        m = b4_predictive_moments(example())
        for c in (.10, .25, .40):
            for cap, veto in ((1., False), (.1, False), (0., False), (1., True)):
                with self.subTest(c=c, cap=cap, veto=veto):
                    out = b4_fractional_target(m, c=c, scope_cap={"TQQQ": cap},
                                               cap_source="toy", veto=veto)
                    self.assertEqual(out.c, c)
                    self.assertEqual(out.targets, (0. if veto else min(cap, c * .8737980336182832),))
                    self.assertEqual(out.raw_second_order, (.8737980336182832,))
                    self.assertEqual(out.status, "VETOED" if veto else "AVAILABLE")
                    self.assertTrue(out.fractional_applied_once)
                    self.assertTrue(out.no_order)
                    self.assertFalse(out.live_ready)
                    self.assertEqual(out.budget_effect, "NONE")

    def test_quarter_fraction_matches_frozen_pre_extension_result(self):
        # Captured on b018372b before changing the domain; exact equality.
        m = b4_predictive_moments(example())
        explicit = b4_fractional_target(m, c=.25, scope_cap={"TQQQ": 1.}, cap_source="toy")
        default = b4_fractional_target(m, scope_cap={"TQQQ": 1.}, cap_source="toy")
        self.assertEqual(default, explicit)
        self.assertEqual(explicit.targets, (.2184495084045708,))
        self.assertEqual(explicit.raw_second_order, (.8737980336182832,))
        self.assertEqual(explicit.c, .25)
        self.assertEqual(explicit.unit, "CAPITAL_FRACTION")

    def test_invalid_fraction_cap_or_veto_is_rejected(self):
        m = b4_predictive_moments(example())
        prior = b4_predictive_moments(initialise_b4(SPEC, SIGMA, TRAINING))
        for c in (0, -0., -.1, .05, .2, .3, .5, .75, 1.,
                  math.nextafter(.1, 0.), math.nextafter(.25, 1.), math.nextafter(.4, 1.),
                  float('nan'), float('inf'), float('-inf'), '0.10', '0.25', '0.40',
                  True, False, None):
            # Invalid c cannot become a harmless prior/unavailable result.
            for moments, caps in ((m, {"TQQQ": .4}), (m, None), (prior, {"TQQQ": .4})):
                with self.subTest(c=c, n=moments.n, caps=caps), self.assertRaises(B4Error):
                    b4_fractional_target(moments, c=c, scope_cap=caps, cap_source="toy")
        for cap in (True, -1., 1.1, float('nan')):
            with self.subTest(cap=cap), self.assertRaises(B4Error):
                b4_fractional_target(m, scope_cap={"TQQQ": cap}, cap_source="toy")
        with self.assertRaises(B4Error):
            b4_fractional_target(m, scope_cap={"SOXL": .4}, cap_source="toy")
        with self.assertRaises(B4Error):
            b4_fractional_target(m, scope_cap={"TQQQ": .4}, cap_source="toy", veto=1)

    def test_negative_predictive_mean_is_known_zero_long_only(self):
        state = admit(initialise_b4(SPEC, SIGMA, TRAINING), observation(1, (-.1,))).state
        out = b4_fractional_target(b4_predictive_moments(state), scope_cap={"TQQQ": .4}, cap_source="toy")
        self.assertEqual(out.status, "AVAILABLE")
        self.assertEqual(out.targets, (0.,))


class ExpectedLogProxyBoundaryTests(unittest.TestCase):
    """Conditional one-core log utility; mathematical caps give no authority."""

    def moments_at(self, mu):
        # Sigma=1/32, n=1 gives v=3/64 exactly; no covariance fitting.
        state = initialise_b4(SPEC, ((1 / 32,),), TRAINING)
        return b4_predictive_moments(admit(
            state, observation(1, (2 * mu + 1 / 32,))).state)

    def test_expected_log_endpoint_conditions_bound_proxy_scope(self):
        # U(0)=0, U(1)=mu; strict concavity and the endpoint derivatives
        # determine the full optimum's location, without integration.
        cases = ((-3 / 64, "ZERO"), (-3 / 128, "ZERO"),
                 (0., "INTERIOR"), (3 / 128, "ONE"), (3 / 64, "ONE"))
        for mu, expected in cases:
            with self.subTest(mu=mu, optimum=expected), localcontext() as ctx:
                ctx.prec = 70
                m = self.moments_at(mu)
                self.assertEqual(m.mean_log, (mu,))
                self.assertEqual(m.predictive_cov_log, ((3 / 64,),))
                md, v = Decimal(mu), Decimal(3) / 64
                d0 = (md + v / 2).exp() - 1
                d1 = 1 - (-md + v / 2).exp()
                location = "ZERO" if d0 <= 0 else "ONE" if d1 >= 0 else "INTERIOR"
                self.assertEqual(location, expected)
                out = b4_fractional_target(m, c=.4, scope_cap={"TQQQ": 1.},
                                           cap_source="synthetic-math-domain-only")
                self.assertEqual(out.approximation, "SECOND_ORDER_FRICTIONLESS_PROXY")
                if expected == "ZERO":
                    self.assertEqual(out.targets, (0.,))
                else:
                    self.assertGreater(out.targets[0], 0.)
                    self.assertLess(out.targets[0], 1.)
                if mu == 3 / 128:
                    # The full log optimum is 1, yet even the unfractional
                    # raw proxy is below 1. This is approximation, not a bug.
                    self.assertLess(out.raw_second_order[0], .95)

    def test_symmetric_log_oracle_reverses_quadratic_proxy_preference(self):
        # With mu=0, U'(.5)=2 E[tanh(Y/2)]=0 by symmetry. Strict
        # concavity gives the unique full log optimum .5. No general
        # closed form or numerical utility gap is asserted.
        m = self.moments_at(0.)
        self.assertEqual(m.mean_log, (0.,))
        out = b4_fractional_target(m, c=.4, scope_cap={"TQQQ": 1.},
                                   cap_source="synthetic-math-domain-only")
        raw = out.raw_second_order[0]
        mean, second = m.mean_simple[0], m.second_raw_moment_simple[0]
        quadratic_raw = mean * raw - second * raw * raw / 2
        quadratic_log_optimum = mean / 2 - second / 8
        self.assertGreater(quadratic_raw, quadratic_log_optimum)
        self.assertGreater(.5 - raw, .03)
        self.assertLess(.5 - raw, .04)
        # Compare to c times the exact full optimum, preserving the
        # intentional fractional rule instead of calling it a sizing bug.
        self.assertGreater(.4 * .5 - out.targets[0], .4 * .03)
        self.assertLess(.4 * .5 - out.targets[0], .4 * .04)


if __name__ == "__main__":
    unittest.main()


class JointMomentFundingContractTests(unittest.TestCase):
    """Fixed hypothetical joint-account arithmetic, never pooled strategy books."""

    def joint_moments(self, sign):
        spec = B4Spec(("TQQQ", "SOXL"), ANCHOR)
        sigma = ((1/32, sign/64), (sign/64, 1/32))
        state = admit(initialise_b4(spec, sigma, TRAINING),
                      observation(1, (1/32, 1/32), spec=spec)).state
        m = b4_predictive_moments(state)
        self.assertEqual(m.mean_log, (0., 0.))
        self.assertEqual(m.predictive_cov_log, ((3/64, sign*3/128), (sign*3/128, 3/64)))
        with localcontext() as ctx:
            ctx.prec = 70
            v, cross = Decimal(3)/64, Decimal(sign)*3/128
            mean = (v/2).exp()-1
            qdiag = (2*v).exp()-2*(v/2).exp()+1
            qcross = (v+cross).exp()-2*(v/2).exp()+1
            for i in range(2):
                self.assertAlmostEqual(m.mean_simple[i], float(mean), delta=1e-14)
                self.assertAlmostEqual(m.second_raw_moment_simple[i], float(qdiag), delta=1e-14)
            actual_cross = m.predictive_cov_simple[0][1]+m.mean_simple[0]*m.mean_simple[1]
            self.assertAlmostEqual(actual_cross, float(qcross), delta=1e-14)
            self.assertAlmostEqual(actual_cross-m.predictive_cov_simple[0][1], float(mean*mean), delta=1e-14)
            self.assertGreater(qdiag*qdiag-qcross*qcross, 0)
        return m, float(qdiag), actual_cross

    def test_JM1_positive_cross_raw_moment(self):
        m, diagonal, cross = self.joint_moments(1)
        weight, cash = .25, .5
        full = 2*weight*weight*(diagonal+cross)
        diagonal_only = 2*weight*weight*diagonal
        self.assertEqual(2*weight+cash, 1.)
        self.assertGreater(cross, 0.)
        self.assertAlmostEqual(full, .009533908291658953, delta=1e-14)
        self.assertAlmostEqual(full-diagonal_only, .0031768449037702013, delta=1e-14)
        self.assertGreater(full, diagonal_only)

    def test_JM2_negative_cross_raw_moment(self):
        m, diagonal, cross = self.joint_moments(-1)
        weight, cash = .5, 0.
        full = 2*weight*weight*(diagonal+cross)
        diagonal_only = 2*weight*weight*diagonal
        self.assertEqual(2*weight+cash, 1.)
        self.assertLess(cross, 0.)  # Signed cross moments must not be clipped.
        self.assertAlmostEqual(full, .013571095250376049, delta=1e-14)
        self.assertAlmostEqual(full-diagonal_only, -.011857158301178958, delta=1e-14)
        self.assertLess(full, diagonal_only)

    def test_JM3_funding_wealth_positive_domain(self):
        m, diagonal, cross = self.joint_moments(1)
        self.assertTrue(all(math.isfinite(x) for x in (diagonal, cross, *m.mean_simple)))
        # Positive definite log covariance gives positive density near this
        # positive-gross witness, so finite moments cannot define real log wealth.
        v, c = m.predictive_cov_log[0]
        self.assertGreater(v*v-c*c, 0.)
        wealth = -.5+.75*.25+.75*.25
        self.assertEqual(wealth, -.125)
        with self.assertRaises(ValueError):
            math.log(wealth)
        # Even the upper edge of the open gross box (0.2, 0.3)^2 is negative.
        self.assertLess(-.5+.75*.3+.75*.3, 0.)
        # Funded nonnegative weights and residual zero-return cash imply a
        # strictly positive combination for every positive lognormal gross.
        weights, cash = (.25, .25), .5
        self.assertTrue(all(w >= 0 for w in weights))
        self.assertEqual(sum(weights)+cash, 1.)
        self.assertGreater(cash+sum(w*.25 for w in weights), 0.)
