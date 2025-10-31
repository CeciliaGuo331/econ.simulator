import math

from econ_sim.logic_modules.utility import _compute_instant_utility


def test_shifted_log_utility_positive_for_positive_consumption():
    gamma = 1.0
    eps = 1e-8
    u0 = _compute_instant_utility(0.0, gamma, eps)
    u1 = _compute_instant_utility(1.0, gamma, eps)
    assert math.isclose(u0, math.log(1.0 + 0.0))
    assert u1 > u0


def test_shifted_crra_positive_for_positive_consumption():
    gamma = 2.0
    eps = 1e-8
    u0 = _compute_instant_utility(0.0, gamma, eps)
    u1 = _compute_instant_utility(1.0, gamma, eps)
    # for gamma != 1, u(0) should be 0 with our shifted formulation
    assert math.isclose(u0, 0.0)
    assert u1 > u0
