"""Versioned moment-surrogate contract; no fitting, solver, I/O or authority.

With explicitly zero-return cash, wealth factor is 1+w'U. Its second-order
log Taylor term uses E[UU']=Cov(U)+E[U]E[U]', not Cov(U) alone. Both methods
below are surrogates, not exact or cost-net Kelly optimization.
"""
from collections.abc import Mapping
import math

from .auto_portfolio_account import digest, utc

METHODS = ('SECOND_RAW_MOMENT_TAYLOR', 'MEAN_COVARIANCE_FURTHER_APPROX')


def _finite(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError('finite nonboolean numeric moment required')
    return float(value)


def _positive_definite(matrix):
    n = len(matrix)
    lower = [[0.]*n for _ in range(n)]
    floor = 1e-14 * max(abs(x) for row in matrix for x in row)
    for i in range(n):
        for j in range(i+1):
            if matrix[i][j] != matrix[j][i]:
                raise ValueError('asymmetric moment matrix')
            residual = matrix[i][j]-math.fsum(lower[i][k]*lower[j][k] for k in range(j))
            if i == j:
                if not math.isfinite(residual) or residual <= floor:
                    raise ValueError('moment matrix not numerically positive definite')
                lower[i][j] = math.sqrt(residual)
            else:
                lower[i][j] = residual/lower[j][j]


def prepare_kelly_quadratic(mu, covariance, *, math_contract, moments, scope_ref, currency, cutoff_utc):
    """Bind same-model plug-in moments and construct the declared QP matrix.

    The existing solver maximizes mu'w-w'Hw/(2c). Here c is applied once as
    curvature scaling. Under active constraints that is generally different
    from post-multiplying the full constrained Kelly solution by c.
    """
    out = dict(status='KELLY_MATH_GAPS',gaps=[],contract_version='qsl.kelly-math.v1',
        exact_log_optimal=False,cost_net_optimal=False,log_wealth_domain_certified=False,
        financial_risk_certified=False,execution_authorized=False,c_applications=1)
    gaps = out['gaps']
    if not isinstance(moments,Mapping) or moments.get('synthetic') is not True:
        gaps.append('SYNTHETIC_MOMENTS_REQUIRED')
        return out
    if moments.get('unit') != 'SHRUNKEN_EXCESS_SIMPLE_RETURN_PER_SESSION':
        gaps.append('MOMENT_UNIT_UNSUPPORTED')
        return out
    if not isinstance(math_contract,Mapping):
        gaps.append('KELLY_MATH_CONTRACT_UNBOUND')
        return out
    try:
        if math_contract.get('schema_version') != 'qsl.kelly-math.v1':
            raise ValueError('unknown math contract')
        if math_contract.get('scope_ref') != scope_ref or math_contract.get('currency') != currency:
            raise ValueError('scope mismatch')
        for key in ('source','source_version'):
            if not isinstance(math_contract.get(key),str) or not math_contract[key].strip():
                raise ValueError('source/version required')
        cutoff = utc(cutoff_utc)
        if not utc(moments.get('available_at')) <= utc(math_contract.get('available_at')) <= cutoff:
            raise ValueError('math/source availability')
        if not utc(math_contract.get('valid_from')) <= cutoff < utc(math_contract.get('valid_until')):
            raise ValueError('math contract not effective')
        out['math_contract_sha256'] = digest(math_contract)
    except (ValueError,TypeError,OverflowError,AttributeError):
        gaps.append('KELLY_MATH_CONTRACT_UNBOUND')
    method = math_contract.get('method')
    if method not in METHODS:
        gaps.append('KELLY_MATH_METHOD_UNSUPPORTED')
    try:
        if (math_contract.get('cash_numeraire') != 'ZERO_RETURN_CASH'
                or _finite(math_contract.get('cash_simple_return')) != 0.):
            raise ValueError('nonzero or unknown cash return')
    except (ValueError,TypeError,OverflowError):
        gaps.append('KELLY_CASH_NUMERAIRE_UNSUPPORTED')
    model = math_contract.get('joint_moment_model_id')
    if (not isinstance(model,str) or not model.strip() or moments.get('joint_moment_model_id') != model
            or not math_contract.get('moment_estimator_version')
            or math_contract['moment_estimator_version'] != moments.get('estimator_version')):
        gaps.append('KELLY_MOMENT_MODEL_UNBOUND')
    if math_contract.get('covariance_basis') != 'PREDICTIVE_CENTERED_COVARIANCE':
        gaps.append('KELLY_COVARIANCE_BASIS_UNSUPPORTED')
    if math_contract.get('return_cost_basis') != 'BEFORE_ACCOUNT_TRANSACTION_COSTS':
        gaps.append('KELLY_RETURN_COST_BASIS_UNSUPPORTED')
    if math_contract.get('c_semantics') != 'QP_CURVATURE_PENALTY_ONCE':
        gaps.append('KELLY_C_SEMANTICS_UNSUPPORTED')
    if gaps:
        return out
    try:
        mu = [_finite(x) for x in mu]
        cov = [[_finite(x) for x in row] for row in covariance]
        n = len(mu)
        if n == 0 or len(cov) != n or any(len(row) != n for row in cov):
            raise ValueError('moment dimensions')
        _positive_definite(cov)
        raw = [[_finite(math.fsum((cov[i][j],mu[i]*mu[j]))) for j in range(n)] for i in range(n)]
        matrix = raw if method == 'SECOND_RAW_MOMENT_TAYLOR' else cov
        _positive_definite(matrix)
        out.update(status='MATH_CONTRACT_BOUND_SYNTHETIC',method=method,
            candidate_math_version='qsl.kelly-qp.'+('raw-second-moment.v1' if method == METHODS[0] else 'covariance-further-approx.v1'),
            quadratic_matrix_kind='SECOND_RAW_MOMENT' if method == METHODS[0] else 'CENTERED_COVARIANCE',
            second_raw_moment=raw,quadratic_matrix=matrix,mean_outer_product_omitted=method != METHODS[0],
            c_semantics='QP_CURVATURE_PENALTY_ONCE',objective='mu.T @ w - (w.T @ H @ w) / (2*c)',
            constraints='w >= 0; sum(w) <= 1; remaining weight is explicit zero-return cash',
            moment_model_id=model,return_cost_basis='BEFORE_ACCOUNT_TRANSACTION_COSTS',
            qualification='QUADRATIC_MOMENT_SURROGATE_ONLY')
    except (ValueError,TypeError,OverflowError,ArithmeticError):
        gaps.append('KELLY_MOMENT_MATRIX_UNSUPPORTED')
    return out
