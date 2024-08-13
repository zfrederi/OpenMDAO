import unittest
import sys
import itertools
from collections.abc import Iterable

import numpy as np
from openmdao.utils.assert_utils import assert_near_equal, assert_check_partials, assert_check_totals
import openmdao.api as om

from openmdao.utils.jax_utils import jax, jnp, ExplicitCompJaxify

try:
    from parameterized import parameterized
except ImportError:
    from openmdao.utils.assert_utils import SkipParameterized as parameterized

if jax is None:
    def jjit(f, *args, **kwargs):
        return f
else:
    from openmdao.jax import act_tanh, smooth_abs, smooth_max, smooth_min, ks_max, ks_min
    def jjit(f, *args, **kwargs):
        if om.env_truthy('JAX_CPU') and 'backend' not in kwargs:
            return jax.jit(f, *args, backend='cpu', **kwargs)
        else:
            return jax.jit(f, *args, **kwargs)


@unittest.skipIf(jax is None, 'jax is not available.')
class TestJax(unittest.TestCase):

    def test_tanh_act(self):
        f = act_tanh(6, mu=1.0E-5, z=6, a=-10, b=10)
        assert_near_equal(np.asarray(f), 0.0)

        f = act_tanh(6, mu=1.0E-5, z=6, a=-10, b=0)
        assert_near_equal(np.asarray(f), -5.0)

        f = act_tanh(-10, mu=1.0E-5, z=6, a=-10, b=0)
        assert_near_equal(np.asarray(f), -10)

        f = act_tanh(10, mu=1.0E-5, z=6, a=-10, b=20)
        assert_near_equal(np.asarray(f), 20)

    def test_smooth_max(self):
        x = np.linspace(0, 1, 1000)
        sin = np.sin(x)
        cos = np.cos(x)

        smax = smooth_max(sin, cos, mu=1.0E-6)

        idxs_sgt = np.where(sin > cos)
        idxs_cgt = np.where(sin < cos)

        assert_near_equal(smax[idxs_sgt], sin[idxs_sgt])
        assert_near_equal(smax[idxs_cgt], cos[idxs_cgt])

    def test_smooth_min(self):
        x = np.linspace(0, 1, 1000)
        sin = np.sin(x)
        cos = np.cos(x)

        smin = smooth_min(sin, cos, mu=1.0E-6)

        idxs_sgt = np.where(sin > cos)
        idxs_cgt = np.where(sin < cos)

        assert_near_equal(smin[idxs_sgt], cos[idxs_sgt])
        assert_near_equal(smin[idxs_cgt], sin[idxs_cgt])

    def test_smooth_abs(self):
        x = np.linspace(-0.5, 0.5, 1000)

        sabs = smooth_abs(x)
        abs = np.abs(x)

        idxs_compare = np.where(abs > 0.1)
        assert_near_equal(sabs[idxs_compare], abs[idxs_compare], tolerance=1.0E-9)

    def test_ks_max(self):
        x = np.random.random(1000)

        ksmax = ks_max(x, rho=1.E6)
        npmax = np.max(x)

        assert_near_equal(ksmax, npmax, tolerance=1.0E-6)

    def test_ks_min(self):
        x = np.random.random(1000)

        ksmin = ks_min(x, rho=1.E6)
        npmin = np.min(x)

        assert_near_equal(ksmin, npmin, tolerance=1.0E-6)


if __name__ == '__main__':
    unittest.main()
