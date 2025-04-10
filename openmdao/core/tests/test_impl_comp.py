"""Simple example demonstrating how to implement an implicit component."""
import unittest

from io import StringIO

import numpy as np

import openmdao.api as om
from openmdao.utils.assert_utils import assert_near_equal, assert_check_totals
from openmdao.utils.general_utils import remove_whitespace
from openmdao.test_suite.components.sellar import SellarImplicitDis1, SellarImplicitDis2


# Note: The following class definitions are used in feature docs

class QuadraticComp(om.ImplicitComponent):
    """
    A Simple Implicit Component representing a Quadratic Equation.

    R(a, b, c, x) = ax^2 + bx + c

    Solution via Quadratic Formula:
    x = (-b + sqrt(b^2 - 4ac)) / 2a
    """

    def setup(self):
        self.add_input('a', val=1., tags=['tag_a'])
        self.add_input('b', val=1.)
        self.add_input('c', val=1.)
        self.add_output('x', val=0., tags=['tag_x'])

    def setup_partials(self):
        self.declare_partials(of='*', wrt='*')

    def apply_nonlinear(self, inputs, outputs, residuals):
        a = inputs['a']
        b = inputs['b']
        c = inputs['c']
        x = outputs['x']
        residuals['x'] = a * x ** 2 + b * x + c

    def solve_nonlinear(self, inputs, outputs):
        a = inputs['a']
        b = inputs['b']
        c = inputs['c']
        outputs['x'] = (-b + (b ** 2 - 4 * a * c) ** 0.5) / (2 * a)


class QuadraticLinearize(QuadraticComp):

    def linearize(self, inputs, outputs, partials):
        a = inputs['a']
        b = inputs['b']
        x = outputs['x']

        partials['x', 'a'] = x ** 2
        partials['x', 'b'] = x
        partials['x', 'c'] = 1.0
        partials['x', 'x'] = 2 * a * x + b

        self.inv_jac = 1.0 / (2 * a * x + b)

    def solve_linear(self, d_outputs, d_residuals, mode):
        if mode == 'fwd':
            d_outputs['x'] = self.inv_jac * d_residuals['x']
        elif mode == 'rev':
            d_residuals['x'] = self.inv_jac * d_outputs['x']


class QuadraticJacVec(QuadraticComp):

    def setup_partials(self):
        pass  # prevent declaration of partials from base class

    def linearize(self, inputs, outputs, partials):
        a = inputs['a']
        b = inputs['b']
        x = outputs['x']
        self.inv_jac = 1.0 / (2 * a * x + b)

    def apply_linear(self, inputs, outputs,
                     d_inputs, d_outputs, d_residuals, mode):
        a = inputs['a']
        b = inputs['b']
        x = outputs['x']
        if mode == 'fwd':
            if 'x' in d_residuals:
                if 'x' in d_outputs:
                    d_residuals['x'] += (2 * a * x + b) * d_outputs['x']
                if 'a' in d_inputs:
                    d_residuals['x'] += x ** 2 * d_inputs['a']
                if 'b' in d_inputs:
                    d_residuals['x'] += x * d_inputs['b']
                if 'c' in d_inputs:
                    d_residuals['x'] += d_inputs['c']
        elif mode == 'rev':
            if 'x' in d_residuals:
                if 'x' in d_outputs:
                    d_outputs['x'] += (2 * a * x + b) * d_residuals['x']
                if 'a' in d_inputs:
                    d_inputs['a'] += x ** 2 * d_residuals['x']
                if 'b' in d_inputs:
                    d_inputs['b'] += x * d_residuals['x']
                if 'c' in d_inputs:
                    d_inputs['c'] += d_residuals['x']

    def solve_linear(self, d_outputs, d_residuals, mode):
        if mode == 'fwd':
            d_outputs['x'] = self.inv_jac * d_residuals['x']
        elif mode == 'rev':
            d_residuals['x'] = self.inv_jac * d_outputs['x']


class ImplCompTestCase(unittest.TestCase):

    def test_add_input_output_retval(self):
        # check basic metadata expected in return value
        expected_ivp_input = {
            'val': 3,
            'shape': (1,),
            'size': 1,
            'units': 'ft',
            'desc': '',
            'tags': set(),
        }
        expected_ivp_output = {
            'val': 3,
            'shape': (1,),
            'size': 1,
            'units': 'ft',
            'desc': '',
            'tags': {'openmdao:allow_desvar'},
        }
        expected_discrete = {
            'val': 3,
            'type': int,
            'desc': '',
            'tags': set(),
        }

        class ImplComp(om.ImplicitComponent):
            def setup(self):
                meta = self.add_input('x', val=3.0, units='ft')
                for key, val in expected_ivp_input.items():
                    assert meta[key] == val, f'Expected {key}: {val} but got {key}: {meta[key]}'

                meta = self.add_discrete_input('x_disc', val=3)
                for key, val in expected_discrete.items():
                    assert meta[key] == val, f'Expected {key}: {val} but got {key}: {meta[key]}'

                meta = self.add_output('y', val=3.0, units='ft')
                for key, val in expected_ivp_output.items():
                    assert meta[key] == val, f'Expected {key}: {val} but got {key}: {meta[key]}'

                meta = self.add_discrete_output('y_disc', val=3)
                for key, val in expected_discrete.items():
                    assert meta[key] == val, f'Expected {key}: {val} but got {key}: {meta[key]}'

        prob = om.Problem()
        prob.model.add_subsystem('comp', ImplComp())
        prob.setup()


class ImplicitCompTestCase(unittest.TestCase):

    def setUp(self):
        group = om.Group()

        group.add_subsystem('comp1', QuadraticLinearize(), promotes_inputs=['a', 'b', 'c'])
        group.add_subsystem('comp2', QuadraticJacVec(), promotes_inputs=['a', 'b', 'c'])

        prob = om.Problem(model=group)
        prob.setup()

        prob.set_val('a', 1.0)
        prob.set_val('b', -4.0)
        prob.set_val('c', 3.0)

        self.prob = prob

    def test_compute_and_derivs(self):
        prob = self.prob
        prob.run_model()

        assert_near_equal(prob['comp1.x'], 3.)
        assert_near_equal(prob['comp2.x'], 3.)

        total_derivs = prob.compute_totals(
            wrt=['a', 'b', 'c'],
            of=['comp1.x', 'comp2.x']
        )
        assert_near_equal(total_derivs['comp1.x', 'a'], [[-4.5]])
        assert_near_equal(total_derivs['comp1.x', 'b'], [[-1.5]])
        assert_near_equal(total_derivs['comp1.x', 'c'], [[-0.5]])

        assert_near_equal(total_derivs['comp2.x', 'a'], [[-4.5]])
        assert_near_equal(total_derivs['comp2.x', 'b'], [[-1.5]])
        assert_near_equal(total_derivs['comp2.x', 'c'], [[-0.5]])

    def test_list_inputs_before_run(self):
        # cannot list_inputs on a Group before running
        model_inputs = self.prob.model.list_inputs(desc=True, prom_name=False, out_stream=None)
        expected = {
            'comp1.a': {'val': [1.], 'desc': ''},
            'comp1.b': {'val': [1.], 'desc': ''},
            'comp1.c': {'val': [1.], 'desc': ''},
            'comp2.a': {'val': [1.], 'desc': ''},
            'comp2.b': {'val': [1.], 'desc': ''},
            'comp2.c': {'val': [1.], 'desc': ''},
        }
        self.assertEqual(dict(model_inputs), expected)

        # list_inputs on a component before running is okay
        c2_inputs = self.prob.model.comp2.list_inputs(desc=True, prom_name=False, out_stream=None)
        expected = {
            'a': {'val': [1.], 'desc': ''},
            'b': {'val': [1.], 'desc': ''},
            'c': {'val': [1.], 'desc': ''}
        }
        self.assertEqual(dict(c2_inputs), expected)

        # listing component inputs based on tags should work
        c2_inputs = self.prob.model.comp2.list_inputs(tags='tag_a', prom_name=False, out_stream=None)
        self.assertEqual(dict(c2_inputs), {'a': {'val': [1.]}})

        # includes and excludes based on relative names should work
        c2_inputs = self.prob.model.comp2.list_inputs(includes='a', prom_name=False, out_stream=None)
        self.assertEqual(dict(c2_inputs), {'a': {'val': [1.]}})

        c2_inputs = self.prob.model.comp2.list_inputs(excludes='c', prom_name=False, out_stream=None)
        expected = {
            'a': {'val': [1.]},
            'b': {'val': [1.]},
        }
        self.assertEqual(dict(c2_inputs), expected)

        # specifying prom_name should not cause an error
        c2_inputs = self.prob.model.comp2.list_inputs(prom_name=True, out_stream=None)
        self.assertEqual(dict(c2_inputs), {
            'a': {'val': [1.], 'prom_name': 'a'},
            'b': {'val': [1.], 'prom_name': 'b'},
            'c': {'val': [1.], 'prom_name': 'c'}
        })

    def test_list_outputs_before_run(self):
        # cannot list_outputs on a Group before running
        model_outputs = self.prob.model.list_outputs(out_stream=None, prom_name=False)
        expected = {
            'comp1.x': {'val': [0.]},
            'comp2.x': {'val': [0.]},
        }
        self.assertEqual(dict(model_outputs), expected)

        # list_outputs on a component before running is okay
        c2_outputs = self.prob.model.comp2.list_outputs(out_stream=None, prom_name=False)
        expected = {
            'x': {'val': np.array([0.])}
        }
        self.assertEqual(dict(c2_outputs), expected)

        # listing component outputs based on tags should work
        c2_outputs = self.prob.model.comp2.list_outputs(tags='tag_x', prom_name=False, out_stream=None)
        self.assertEqual(dict(c2_outputs), expected)

        # includes and excludes based on relative names should work
        c2_outputs = self.prob.model.comp2.list_outputs(includes='x', prom_name=False, out_stream=None)
        self.assertEqual(dict(c2_outputs), expected)

        c2_outputs = self.prob.model.comp2.list_outputs(excludes='x', prom_name=False, out_stream=None)
        self.assertEqual(dict(c2_outputs), {})

        # specifying residuals_tol should not cause an error
        # there are no residuals yet, so nothing should be filtered
        c2_outputs = self.prob.model.comp2.list_outputs(residuals_tol=.01, prom_name=False, out_stream=None)
        self.assertEqual(dict(c2_outputs), {
            'x': {'val': 0.}
        })

        # specifying prom_name should not cause an error
        c2_outputs = self.prob.model.comp2.list_outputs(prom_name=True, out_stream=None)
        self.assertEqual(dict(c2_outputs), {
            'x': {'val': 0., 'prom_name': 'x'}
        })

    def test_list_inputs(self):
        self.prob.run_model()

        stream = StringIO()
        inputs = self.prob.model.list_inputs(hierarchical=False, desc=True, prom_name=False, out_stream=stream)
        self.assertEqual(sorted(inputs), [
            ('comp1.a', {'val':  [1.], 'desc': ''}),
            ('comp1.b', {'val': [-4.], 'desc': ''}),
            ('comp1.c', {'val':  [3.], 'desc': ''}),
            ('comp2.a', {'val':  [1.], 'desc': ''}),
            ('comp2.b', {'val': [-4.], 'desc': ''}),
            ('comp2.c', {'val':  [3.], 'desc': ''})
        ])
        text = stream.getvalue()
        self.assertEqual(text.count('comp1.'), 3)
        self.assertEqual(text.count('comp2.'), 3)
        self.assertEqual(text.count('val'), 1)

    def test_list_inputs_with_tags(self):
        self.prob.run_model()

        # No tags
        inputs = self.prob.model.list_inputs(val=False, prom_name=False, hierarchical=False, out_stream=None)
        self.assertEqual(sorted(inputs), [
            ('comp1.a', {}),
            ('comp1.b', {}),
            ('comp1.c', {}),
            ('comp2.a', {}),
            ('comp2.b', {}),
            ('comp2.c', {})
        ])

        # With tag
        inputs = self.prob.model.list_inputs(val=False, prom_name=False, hierarchical=False, out_stream=None, tags='tag_a')
        self.assertEqual(sorted(inputs), [
            ('comp1.a', {}),
            ('comp2.a', {}),
        ])

        # Wrong tag
        inputs = self.prob.model.list_inputs(val=False, prom_name=False, hierarchical=False, out_stream=None, tags='tag_wrong')
        self.assertEqual(sorted(inputs), [])

    def test_list_inputs_prom_name(self):
        self.prob.run_model()

        stream = StringIO()
        self.prob.model.list_inputs(shape=True, hierarchical=True, out_stream=stream)

        text = stream.getvalue()
        self.assertEqual(text.count('  a  '), 4)
        self.assertEqual(text.count('  b  '), 4)
        self.assertEqual(text.count('  c  '), 4)

        num_non_empty_lines = sum([1 for s in text.splitlines() if s.strip()])
        self.assertEqual(num_non_empty_lines, 11)

    def test_list_explicit_outputs(self):
        self.prob.run_model()

        stream = StringIO()
        outputs = self.prob.model.list_outputs(implicit=False, hierarchical=False, out_stream=stream)
        self.assertEqual([], sorted(outputs))
        text = stream.getvalue()
        self.assertIn('0 Explicit Output(s) in \'model\'', text)

    def test_list_explicit_outputs_with_tags(self):
        self.prob.run_model()

        # No tags
        outputs = self.prob.model.list_outputs(explicit=False, prom_name=False, hierarchical=False, out_stream=None)
        self.assertEqual(sorted(outputs), [
            ('comp1.x', {'val': [3.]}),
            ('comp2.x', {'val': [3.]}),
        ])

        # With tag
        outputs = self.prob.model.list_outputs(explicit=False, prom_name=False, hierarchical=False, out_stream=None,
                                               tags="tag_x")
        self.assertEqual(sorted(outputs), [
            ('comp1.x', {'val': [3.]}),
            ('comp2.x', {'val': [3.]}),
        ])

        # Wrong tag
        outputs = self.prob.model.list_outputs(explicit=False, prom_name=False, hierarchical=False, out_stream=None,
                                               tags="tag_wrong")
        self.assertEqual(sorted(outputs), [])

    def test_list_implicit_outputs(self):
        self.prob.run_model()

        stream = StringIO()
        states = self.prob.model.list_outputs(explicit=False, prom_name=False, residuals=True,
                                              hierarchical=False, out_stream=stream)
        self.assertEqual([('comp1.x', {'val': [3.], 'resids': [0.]}),
                          ('comp2.x', {'val': [3.], 'resids': [0.]})], sorted(states))
        text = stream.getvalue()
        self.assertEqual(1, text.count('comp1.x'))
        self.assertEqual(1, text.count('comp2.x'))
        self.assertEqual(1, text.count('val'))
        self.assertEqual(1, text.count('resids'))

    def test_list_outputs_prom_name(self):
        self.prob.run_model()

        stream = StringIO()
        self.prob.model.list_outputs(explicit=False, residuals=True,
                                     prom_name=True, hierarchical=True,
                                     out_stream=stream)

        text = stream.getvalue()
        self.assertEqual(text.count('comp1.x'), 1)
        self.assertEqual(text.count('comp2.x'), 1)
        num_non_empty_lines = sum([1 for s in text.splitlines() if s.strip()])
        self.assertEqual(num_non_empty_lines, 7)

    def test_list_residuals(self):
        self.prob.run_model()

        stream = StringIO()
        resids = self.prob.model.list_outputs(val=False, residuals=True, hierarchical=False,
                                              out_stream=stream, prom_name=False)
        self.assertEqual(sorted(resids), [
            ('comp1.x', {'resids': [0.]}),
            ('comp2.x', {'resids': [0.]})
        ])
        text = stream.getvalue()
        self.assertEqual(text.count('comp1.'), 1)
        self.assertEqual(text.count('comp1.x'), 1)
        self.assertEqual(text.count('comp2.x'), 1)
        self.assertEqual(text.count('varname'), 1)
        self.assertEqual(text.count('val'), 0)
        self.assertEqual(text.count('resids'), 1)

    def test_list_residuals_with_tol(self):
        prob = om.Problem()
        model = prob.model

        model.add_subsystem('p1', om.IndepVarComp('x', 1.0))
        model.add_subsystem('d1', SellarImplicitDis1())
        model.add_subsystem('d2', SellarImplicitDis2())
        model.connect('d1.y1', 'd2.y1')
        model.connect('d2.y2', 'd1.y2')

        model.nonlinear_solver = om.NewtonSolver(solve_subsystems=False)
        model.nonlinear_solver.options['maxiter'] = 5
        model.linear_solver = om.ScipyKrylov()
        model.linear_solver.precon = om.LinearBlockGS()

        prob.setup()
        prob.set_solver_print(level=-1)

        prob.run_model()

        # list outputs with residuals, p1 and d1 should not appear
        stream = StringIO()
        model.list_outputs(residuals_tol=0.01, residuals=True, prom_name=False, out_stream=stream)

        expected_text = [
            "0 Explicit Output(s) in 'model'",
            "",
            "",
            "1 Implicit Output(s) in 'model'",
            "",
            "varname  val           resids     ",
            "-------  ------------  -----------",
            "d2",
            "  y2",  # values removed from comparison
            "",
            "",
        ]

        captured_output = stream.getvalue()
        for i, line in enumerate(captured_output.split('\n')):
            if line and not line.startswith('-'):
                self.assertEqual(remove_whitespace(line.split('[')[0]),
                                 remove_whitespace(expected_text[i]))


class ImplicitCompGuessTestCase(unittest.TestCase):

    def test_guess_nonlinear(self):

        class ImpWithInitial(QuadraticLinearize):

            def solve_nonlinear(self, inputs, outputs):
                """ Do nothing. """
                pass

            def guess_nonlinear(self, inputs, outputs, resids):
                # Solution at x=1 and x=3. Default value takes us to the x=1 solution. Here
                # we set it to a value that will take us to the x=3 solution.
                outputs['x'] = 5.0

        group = om.Group()

        group.add_subsystem('pa', om.IndepVarComp('a', 1.0))
        group.add_subsystem('pb', om.IndepVarComp('b', 1.0))
        group.add_subsystem('pc', om.IndepVarComp('c', 1.0))
        group.add_subsystem('comp2', ImpWithInitial())
        group.connect('pa.a', 'comp2.a')
        group.connect('pb.b', 'comp2.b')
        group.connect('pc.c', 'comp2.c')

        prob = om.Problem(model=group)
        group.nonlinear_solver = om.NewtonSolver()
        group.nonlinear_solver.options['solve_subsystems'] = True
        group.nonlinear_solver.options['max_sub_solves'] = 1
        group.linear_solver = om.ScipyKrylov()

        prob.setup()

        prob['pa.a'] = 1.
        prob['pb.b'] = -4.
        prob['pc.c'] = 3.

        # Making sure that guess_nonlinear is called early enough to eradicate this.
        prob['comp2.x'] = np.nan

        prob.run_model()
        assert_near_equal(prob['comp2.x'], 3.)

    def test_guess_nonlinear_complex_step(self):

        class ImpWithInitial(om.ImplicitComponent):
            """
            An implicit component to solve the quadratic equation: x^2 - 4x + 3
            (solutions at x=1 and x=3)
            """
            def setup(self):
                self.add_input('a', val=1.)
                self.add_input('b', val=-4.)
                self.add_input('c', val=3.)

                self.add_output('x', val=0.)

                self.declare_partials(of='*', wrt='*')

            def apply_nonlinear(self, inputs, outputs, residuals):
                a = inputs['a']
                b = inputs['b']
                c = inputs['c']
                x = outputs['x']
                residuals['x'] = a * x ** 2 + b * x + c

            def linearize(self, inputs, outputs, partials):
                a = inputs['a']
                b = inputs['b']

                x = outputs['x']

                partials['x', 'a'] = x ** 2
                partials['x', 'b'] = x
                partials['x', 'c'] = 1.0
                partials['x', 'x'] = 2 * a * x + b

            def guess_nonlinear(self, inputs, outputs, resids):

                if outputs.asarray().dtype == complex:
                    raise RuntimeError('Vector should not be complex when guess_nonlinear is called.')

                # Default initial state of zero for x takes us to x=1 solution.
                # Here we set it to a value that will take us to the x=3 solution.
                outputs['x'] = 5.0

        prob = om.Problem()
        model = prob.model

        indep = om.IndepVarComp()
        indep.add_output('a', 1.0)
        indep.add_output('b', -4.0)
        indep.add_output('c', 3.0)
        model.add_subsystem('p', indep)
        model.add_subsystem('comp', ImpWithInitial())
        fn = model.add_subsystem('fn', om.ExecComp(['y = .03*a*x*x - .04*a*a*b*x - c']))
        fn.declare_partials(of='*', wrt='*', method='cs')

        model.connect('p.a', 'comp.a')
        model.connect('p.a', 'fn.a')
        model.connect('p.b', 'fn.b')
        model.connect('p.c', 'fn.c')
        model.connect('comp.x', 'fn.x')

        model.nonlinear_solver = om.NewtonSolver(solve_subsystems=False)
        model.nonlinear_solver.options['rtol'] = 1e-12
        model.nonlinear_solver.options['atol'] = 1e-12
        model.nonlinear_solver.options['maxiter'] = 15
        model.linear_solver = om.ScipyKrylov()

        prob.setup(force_alloc_complex=True)
        prob.run_model()

        assert_near_equal(prob['comp.x'], 3.)

        totals = prob.check_totals(of=['fn.y'], wrt=['p.a'], method='cs', out_stream=None)
        assert_check_totals(totals)

    def test_guess_nonlinear_residuals(self):

        class ImpWithInitial(om.ImplicitComponent):
            """
            An implicit component to solve the quadratic equation: x^2 - 4x + 3
            (solutions at x=1 and x=3)
            """
            def setup(self):
                self.add_input('a', val=1.)
                self.add_input('b', val=-4.)
                self.add_input('c', val=3.)

                self.add_output('x', val=0.)

                self.declare_partials(of='*', wrt='*')

            def apply_nonlinear(self, inputs, outputs, residuals):
                a = inputs['a']
                b = inputs['b']
                c = inputs['c']
                x = outputs['x']
                residuals['x'] = a * x ** 2 + b * x + c

            def linearize(self, inputs, outputs, partials):
                a = inputs['a']
                b = inputs['b']

                x = outputs['x']

                partials['x', 'a'] = x ** 2
                partials['x', 'b'] = x
                partials['x', 'c'] = 1.0
                partials['x', 'x'] = 2 * a * x + b

            def guess_nonlinear(self, inputs, outputs, resids):
                # Default initial state of zero for x takes us to x=1 solution.
                # Here we set it to a value that will take us to the x=3 solution.
                outputs['x'] = 5.0
                assert(resids['x'] != 0.)

        prob = om.Problem()
        model = prob.model

        model.add_subsystem('comp', ImpWithInitial())

        model.nonlinear_solver = om.NewtonSolver(solve_subsystems=False)
        model.linear_solver = om.ScipyKrylov()

        prob.setup()
        prob['comp.x'] = 10
        prob.run_model()

    def test_guess_nonlinear_group_residual(self):
        # Test that data is transfered to a component before calling guess_nonlinear.

        class ImpWithInitial(om.ImplicitComponent):
            """
            An implicit component to solve the quadratic equation: x^2 - 4x + 3
            (solutions at x=1 and x=3)
            """
            def setup(self):
                self.add_input('a', val=1.)
                self.add_input('b', val=-4.)
                self.add_input('c', val=3.)

                self.add_output('x', val=0.)

                self.declare_partials(of='*', wrt='*')

            def apply_nonlinear(self, inputs, outputs, residuals):
                a = inputs['a']
                b = inputs['b']
                c = inputs['c']
                x = outputs['x']
                residuals['x'] = a * x ** 2 + b * x + c

            def linearize(self, inputs, outputs, partials):
                a = inputs['a']
                b = inputs['b']

                x = outputs['x']

                partials['x', 'a'] = x ** 2
                partials['x', 'b'] = x
                partials['x', 'c'] = 1.0
                partials['x', 'x'] = 2 * a * x + b

            def guess_nonlinear(self, inputs, outputs, resids):
                # Default initial state of zero for x takes us to x=1 solution.
                # Here we set it to a value that will take us to the x=3 solution.
                outputs['x'] = 5.0
                assert(resids['x'] != 0.)

        group = om.Group()

        group.add_subsystem('px', om.IndepVarComp('x', -1.0))
        group.add_subsystem('comp1', ImpWithInitial())
        group.add_subsystem('comp2', ImpWithInitial())
        group.connect('px.x', 'comp1.a')
        group.connect('comp1.x', 'comp2.a')

        group.nonlinear_solver = om.NewtonSolver(solve_subsystems=False)
        group.linear_solver = om.ScipyKrylov()

        prob = om.Problem(model=group)
        prob.set_solver_print(level=0)
        prob.setup()

        prob.run_model()

    def test_guess_nonlinear_transfer(self):
        # Test that data is transfered to a component before calling guess_nonlinear.

        class ImpWithInitial(om.ImplicitComponent):

            def setup(self):
                self.add_input('x', 3.0)
                self.add_output('y', 4.0)

            def solve_nonlinear(self, inputs, outputs):
                """ Do nothing. """
                pass

            def apply_nonlinear(self, inputs, outputs, resids):
                """ Do nothing. """
                pass

            def guess_nonlinear(self, inputs, outputs, resids):
                # Passthrough
                outputs['y'] = inputs['x']

        group = om.Group()

        group.add_subsystem('px', om.IndepVarComp('x', 77.0))
        group.add_subsystem('comp1', ImpWithInitial())
        group.add_subsystem('comp2', ImpWithInitial())
        group.connect('px.x', 'comp1.x')
        group.connect('comp1.y', 'comp2.x')

        group.nonlinear_solver = om.NewtonSolver(solve_subsystems=False)
        group.nonlinear_solver.options['maxiter'] = 1

        prob = om.Problem(model=group)
        prob.set_solver_print(level=0)
        prob.setup()

        prob.run_model()
        assert_near_equal(prob['comp2.y'], 77., 1e-5)

    def test_guess_nonlinear_transfer_subbed(self):
        # Test that data is transfered to a component before calling guess_nonlinear.

        class ImpWithInitial(om.ImplicitComponent):

            def setup(self):
                self.add_input('x', 3.0)
                self.add_output('y', 4.0)

            def solve_nonlinear(self, inputs, outputs):
                """ Do nothing. """
                pass

            def apply_nonlinear(self, inputs, outputs, resids):
                """ Do nothing. """
                resids['y'] = 1.0e-6
                pass

            def guess_nonlinear(self, inputs, outputs, resids):
                # Passthrough
                outputs['y'] = inputs['x']

        group = om.Group()
        sub = om.Group()

        group.add_subsystem('px', om.IndepVarComp('x', 77.0))
        sub.add_subsystem('comp1', ImpWithInitial())
        sub.add_subsystem('comp2', ImpWithInitial())
        group.connect('px.x', 'sub.comp1.x')
        group.connect('sub.comp1.y', 'sub.comp2.x')

        group.add_subsystem('sub', sub)

        group.nonlinear_solver = om.NewtonSolver(solve_subsystems=False)
        group.nonlinear_solver.options['maxiter'] = 1

        prob = om.Problem(model=group)
        prob.set_solver_print(level=0)
        prob.setup()

        prob.run_model()
        assert_near_equal(prob['sub.comp2.y'], 77., 1e-5)

    def test_guess_nonlinear_transfer_subbed2(self):
        # Test that data is transfered to a component before calling guess_nonlinear.

        class ImpWithInitial(om.ImplicitComponent):

            def setup(self):
                self.add_input('x', 3.0)
                self.add_output('y', 4.0)

            def solve_nonlinear(self, inputs, outputs):
                """ Do nothing. """
                pass

            def apply_nonlinear(self, inputs, outputs, resids):
                """ Do nothing. """
                resids['y'] = 1.0e-6
                pass

            def guess_nonlinear(self, inputs, outputs, resids):
                # Passthrough
                outputs['y'] = inputs['x']

        group = om.Group()
        sub = om.Group()

        group.add_subsystem('px', om.IndepVarComp('x', 77.0))
        sub.add_subsystem('comp1', ImpWithInitial())
        sub.add_subsystem('comp2', ImpWithInitial())
        group.connect('px.x', 'sub.comp1.x')
        group.connect('sub.comp1.y', 'sub.comp2.x')

        group.add_subsystem('sub', sub)

        sub.nonlinear_solver = om.NewtonSolver(solve_subsystems=False)
        sub.nonlinear_solver.options['maxiter'] = 1

        prob = om.Problem(model=group)
        prob.set_solver_print(level=0)
        prob.setup()

        prob.run_model()
        assert_near_equal(prob['sub.comp2.y'], 77., 1e-5)

    def test_guess_nonlinear_feature(self):

        class ImpWithInitial(om.ImplicitComponent):
            """
            An implicit component to solve the quadratic equation: x^2 - 4x + 3
            (solutions at x=1 and x=3)
            """
            def setup(self):
                self.add_input('a', val=1.)
                self.add_input('b', val=-4.)
                self.add_input('c', val=3.)

                self.add_output('x', val=0.)

                self.declare_partials(of='*', wrt='*')

            def apply_nonlinear(self, inputs, outputs, residuals):
                a = inputs['a']
                b = inputs['b']
                c = inputs['c']
                x = outputs['x']
                residuals['x'] = a * x ** 2 + b * x + c

            def linearize(self, inputs, outputs, partials):
                a = inputs['a']
                b = inputs['b']

                x = outputs['x']

                partials['x', 'a'] = x ** 2
                partials['x', 'b'] = x
                partials['x', 'c'] = 1.0
                partials['x', 'x'] = 2 * a * x + b

            def guess_nonlinear(self, inputs, outputs, resids):
                # Check residuals
                if np.abs(resids['x']) > 1.0E-2:
                    # Default initial state of zero for x takes us to x=1 solution.
                    # Here we set it to a value that will take us to the x=3 solution.
                    outputs['x'] = 5.0

        prob = om.Problem()
        model = prob.model

        model.add_subsystem('comp', ImpWithInitial())

        model.nonlinear_solver = om.NewtonSolver(solve_subsystems=False)
        model.linear_solver = om.ScipyKrylov()

        prob.setup()
        prob.run_model()

        assert_near_equal(prob['comp.x'], 3.)

    def test_guess_nonlinear_inputs_read_only(self):
        class ImpWithInitial(om.ImplicitComponent):

            def setup(self):
                self.add_input('x', 3.0)
                self.add_output('y', 4.0)

            def guess_nonlinear(self, inputs, outputs, resids):
                # inputs is read_only, should not be allowed
                inputs['x'] = 0.

            def apply_nonlinear(self, inputs, outputs, residuals, discrete_inputs=None,
                                discrete_outputs=None):
                pass

        group = om.Group()

        group.add_subsystem('px', om.IndepVarComp('x', 77.0))
        group.add_subsystem('comp1', ImpWithInitial())
        group.add_subsystem('comp2', ImpWithInitial())
        group.connect('px.x', 'comp1.x')
        group.connect('comp1.y', 'comp2.x')

        group.nonlinear_solver = om.NewtonSolver(solve_subsystems=False)
        group.nonlinear_solver.options['maxiter'] = 1

        prob = om.Problem(model=group)
        prob.set_solver_print(level=0)
        prob.setup()

        with self.assertRaises(ValueError) as cm:
            prob.run_model()

        self.assertEqual(str(cm.exception),
                         "'comp1' <class ImpWithInitial>: Attempt to set value of 'x' in input vector "
                         "when it is read only.")

    def test_guess_nonlinear_inputs_read_only_reset(self):
        class ImpWithInitial(om.ImplicitComponent):

            def setup(self):
                self.add_input('x', 3.0)
                self.add_output('y', 4.0)

            def guess_nonlinear(self, inputs, outputs, resids):
                raise om.AnalysisError("It's just a scratch.")

            def apply_nonlinear(self, inputs, outputs, residuals, discrete_inputs=None,
                discrete_outputs=None):
                pass

        group = om.Group()

        group.add_subsystem('px', om.IndepVarComp('x', 77.0))
        group.add_subsystem('comp1', ImpWithInitial())
        group.add_subsystem('comp2', ImpWithInitial())
        group.connect('px.x', 'comp1.x')
        group.connect('comp1.y', 'comp2.x')

        group.nonlinear_solver = om.NewtonSolver(solve_subsystems=False)
        group.nonlinear_solver.options['maxiter'] = 1

        prob = om.Problem(model=group)
        prob.set_solver_print(level=0)
        prob.setup()

        with self.assertRaises(om.AnalysisError):
            prob.run_model()

        # verify read_only status is reset after AnalysisError
        prob['comp1.x'] = 111.

    def test_guess_nonlinear_resids_read_only(self):
        class ImpWithInitial(om.ImplicitComponent):

            def setup(self):
                self.add_input('x', 3.0)
                self.add_output('y', 4.0)

            def guess_nonlinear(self, inputs, outputs, resids):
                # inputs is read_only, should not be allowed
                resids['y'] = 0.

            def apply_nonlinear(self, inputs, outputs, residuals, discrete_inputs=None,
                discrete_outputs=None):
                pass

        group = om.Group()

        group.add_subsystem('px', om.IndepVarComp('x', 77.0))
        group.add_subsystem('comp1', ImpWithInitial())
        group.add_subsystem('comp2', ImpWithInitial())
        group.connect('px.x', 'comp1.x')
        group.connect('comp1.y', 'comp2.x')

        group.nonlinear_solver = om.NewtonSolver(solve_subsystems=False)
        group.nonlinear_solver.options['maxiter'] = 1

        prob = om.Problem(model=group)
        prob.set_solver_print(level=0)
        prob.setup()

        with self.assertRaises(ValueError) as cm:
            prob.run_model()

        self.assertEqual(str(cm.exception),
                         "'comp1' <class ImpWithInitial>: Attempt to set value of 'y' in residual vector "
                         "when it is read only.")

    def test_apply_nonlinear_missing_override(self):
        class ImpWithInitial(om.ImplicitComponent):

            def setup(self):
                self.add_input('x', 3.0)
                self.add_output('y', 4.0)

            def guess_nonlinear(self, inputs, outputs, resids):
                # inputs is read_only, should not be allowed
                inputs['x'] = 0.

        group = om.Group()

        group.add_subsystem('px', om.IndepVarComp('x', 77.0))
        group.add_subsystem('comp1', ImpWithInitial())
        group.add_subsystem('comp2', ImpWithInitial())
        group.connect('px.x', 'comp1.x')
        group.connect('comp1.y', 'comp2.x')

        group.nonlinear_solver = om.NewtonSolver(solve_subsystems=False)
        group.nonlinear_solver.options['maxiter'] = 1

        prob = om.Problem(model=group)
        prob.set_solver_print(level=0)
        prob.setup()

        with self.assertRaises(ValueError) as cm:
            prob.run_model()

        self.assertEqual(str(cm.exception),
                         "'comp1' <class ImpWithInitial>: Attempt to set value of 'x' in input vector when it is read only.")

class ImplicitCompReadOnlyTestCase(unittest.TestCase):

    def test_apply_nonlinear_inputs_read_only(self):
        class BadComp(QuadraticComp):
            def apply_nonlinear(self, inputs, outputs, residuals):
                super().apply_nonlinear(inputs, outputs, residuals)
                inputs['a'] = 0.  # should not be allowed

        prob = om.Problem()
        prob.model.add_subsystem('bad', BadComp())
        prob.setup()
        prob.run_model()

        # check input vector
        with self.assertRaises(ValueError) as cm:
            prob.model.run_apply_nonlinear()

        self.assertEqual(str(cm.exception),
                         "'bad' <class BadComp>: Attempt to set value of 'a' in input vector "
                         "when it is read only.")

    def test_apply_nonlinear_outputs_read_only(self):
        class BadComp(QuadraticComp):
            def apply_nonlinear(self, inputs, outputs, residuals):
                super().apply_nonlinear(inputs, outputs, residuals)
                outputs['x'] = 0.  # should not be allowed

        prob = om.Problem()
        prob.model.add_subsystem('bad', BadComp())
        prob.setup()
        prob.run_model()

        # check output vector
        with self.assertRaises(ValueError) as cm:
            prob.model.run_apply_nonlinear()

        self.assertEqual(str(cm.exception),
                         "'bad' <class BadComp>: Attempt to set value of 'x' in output vector "
                         "when it is read only.")

    def test_apply_nonlinear_read_only_reset(self):
        class BadComp(QuadraticComp):
            def apply_nonlinear(self, inputs, outputs, residuals):
                super().apply_nonlinear(inputs, outputs, residuals)
                raise om.AnalysisError("It's just a scratch.")

        prob = om.Problem()
        prob.model.add_subsystem('bad', BadComp())
        prob.setup()
        prob.run_model()

        with self.assertRaises(om.AnalysisError):
            prob.model.run_apply_nonlinear()

        # verify read_only status is reset after AnalysisError
        prob['bad.a'] = 111.
        prob['bad.x'] = 111.

    def test_solve_nonlinear_inputs_read_only(self):
        class BadComp(QuadraticComp):
            def solve_nonlinear(self, inputs, outputs):
                super().solve_nonlinear(inputs, outputs)
                inputs['a'] = 0.  # should not be allowed

        prob = om.Problem()
        prob.model.add_subsystem('bad', BadComp())
        prob.setup()

        # check input vector
        with self.assertRaises(ValueError) as cm:
            prob.run_model()

        self.assertEqual(str(cm.exception),
                         "'bad' <class BadComp>: Attempt to set value of 'a' in input vector "
                         "when it is read only.")

    def test_solve_nonlinear_inputs_read_only_reset(self):
        class BadComp(QuadraticComp):
            def solve_nonlinear(self, inputs, outputs):
                super().solve_nonlinear(inputs, outputs)
                raise om.AnalysisError("It's just a scratch.")

        prob = om.Problem()
        prob.model.add_subsystem('bad', BadComp())
        prob.setup()

        with self.assertRaises(om.AnalysisError):
            prob.run_model()

        # verify read_only status is reset after AnalysisError
        prob['bad.a'] = 111.

    def test_linearize_inputs_read_only(self):
        class BadComp(QuadraticLinearize):
            def linearize(self, inputs, outputs, partials):
                super().linearize(inputs, outputs, partials)
                inputs['a'] = 0.  # should not be allowed

        prob = om.Problem()
        prob.model.add_subsystem('bad', BadComp())
        prob.setup()
        prob.run_model()

        # check input vector
        with self.assertRaises(ValueError) as cm:
            prob.model.run_linearize()

        self.assertEqual(str(cm.exception),
                         "'bad' <class BadComp>: Attempt to set value of 'a' in input vector "
                         "when it is read only.")

    def test_linearize_outputs_read_only(self):
        class BadComp(QuadraticLinearize):
            def linearize(self, inputs, outputs, partials):
                super().linearize(inputs, outputs, partials)
                outputs['x'] = 0.  # should not be allowed

        prob = om.Problem()
        prob.model.add_subsystem('bad', BadComp())
        prob.setup()
        prob.run_model()

        # check input vector
        with self.assertRaises(ValueError) as cm:
            prob.model.run_linearize()

        self.assertEqual(str(cm.exception),
                         "'bad' <class BadComp>: Attempt to set value of 'x' in output vector "
                         "when it is read only.")

    def test_linearize_read_only_reset(self):
        class BadComp(QuadraticLinearize):
            def linearize(self, inputs, outputs, partials):
                super().linearize(inputs, outputs, partials)
                raise om.AnalysisError("It's just a scratch.")

        prob = om.Problem()
        prob.model.add_subsystem('bad', BadComp())
        prob.setup()
        prob.run_model()

        with self.assertRaises(om.AnalysisError):
            prob.model.run_linearize()

        # verify read_only status is reset after AnalysisError
        prob['bad.a'] = 111.
        prob['bad.x'] = 111.

    def test_apply_linear_inputs_read_only(self):
        class BadComp(QuadraticJacVec):
            def apply_linear(self, inputs, outputs, d_inputs, d_outputs, d_residuals, mode):
                super().apply_linear(inputs, outputs,
                                                  d_inputs, d_outputs, d_residuals, mode)
                inputs['a'] = 0.  # should not be allowed

        prob = om.Problem()
        prob.model.add_subsystem('bad', BadComp())
        prob.setup()
        prob.run_model()

        # check input vector
        with self.assertRaises(ValueError) as cm:
            prob.model.run_apply_linear('fwd')

        self.assertEqual(str(cm.exception),
                         "'bad' <class BadComp>: Attempt to set value of 'a' in input vector "
                         "when it is read only.")

    def test_apply_linear_outputs_read_only(self):
        class BadComp(QuadraticJacVec):
            def apply_linear(self, inputs, outputs, d_inputs, d_outputs, d_residuals, mode):
                super().apply_linear(inputs, outputs,
                                                  d_inputs, d_outputs, d_residuals, mode)
                outputs['x'] = 0.  # should not be allowed

        prob = om.Problem()
        prob.model.add_subsystem('bad', BadComp())
        prob.setup()
        prob.run_model()

        # check input vector
        with self.assertRaises(ValueError) as cm:
            prob.model.run_apply_linear('fwd')

        self.assertEqual(str(cm.exception),
                         "'bad' <class BadComp>: Attempt to set value of 'x' in output vector "
                         "when it is read only.")

    def test_apply_linear_dinputs_read_only(self):
        class BadComp(QuadraticJacVec):
            def apply_linear(self, inputs, outputs, d_inputs, d_outputs, d_residuals, mode):
                super().apply_linear(inputs, outputs,
                                                  d_inputs, d_outputs, d_residuals, mode)
                d_inputs['a'] = 0.  # should not be allowed

        prob = om.Problem()
        prob.model.add_subsystem('bad', BadComp())
        prob.setup()
        prob.run_model()

        # check input vector
        with self.assertRaises(ValueError) as cm:
            prob.model.run_apply_linear('fwd')

        self.assertEqual(str(cm.exception),
                         "'bad' <class BadComp>: Attempt to set value of 'a' in input vector "
                         "when it is read only.")

    def test_apply_linear_doutputs_read_only(self):
        class BadComp(QuadraticJacVec):
            def apply_linear(self, inputs, outputs, d_inputs, d_outputs, d_residuals, mode):
                super().apply_linear(inputs, outputs,
                                                  d_inputs, d_outputs, d_residuals, mode)
                d_outputs['x'] = 0.  # should not be allowed

        prob = om.Problem()
        prob.model.add_subsystem('bad', BadComp())
        prob.setup()
        prob.run_model()

        # check input vector
        with self.assertRaises(ValueError) as cm:
            prob.model.run_apply_linear('fwd')

        self.assertEqual(str(cm.exception),
                         "'bad' <class BadComp>: Attempt to set value of 'x' in output vector "
                         "when it is read only.")

    def test_apply_linear_dresids_read_only(self):
        class BadComp(QuadraticJacVec):
            def apply_linear(self, inputs, outputs, d_inputs, d_outputs, d_residuals, mode):
                super().apply_linear(inputs, outputs,
                                                  d_inputs, d_outputs, d_residuals, mode)
                d_residuals['x'] = 0.  # should not be allowed

        prob = om.Problem()
        prob.model.add_subsystem('bad', BadComp())
        prob.setup()
        prob.run_model()

        # check input vector
        with self.assertRaises(ValueError) as cm:
            prob.model.run_apply_linear('rev')

        self.assertEqual(str(cm.exception),
                         "'bad' <class BadComp>: Attempt to set value of 'x' in residual vector "
                         "when it is read only.")

    def test_apply_linear_read_only_reset(self):
        class BadComp(QuadraticJacVec):
            def apply_linear(self, inputs, outputs, d_inputs, d_outputs, d_residuals, mode):
                super().apply_linear(inputs, outputs,
                                                  d_inputs, d_outputs, d_residuals, mode)
                raise om.AnalysisError("It's just a scratch.")

        prob = om.Problem()
        prob.model.add_subsystem('bad', BadComp())
        prob.setup()
        prob.run_model()

        with self.assertRaises(om.AnalysisError):
            prob.model.run_apply_linear('rev')

        # verify read_only status is reset after AnalysisError
        prob['bad.a'] = 111.
        prob['bad.x'] = 111.
        prob.model.bad._vectors['residual']['linear']['x'] = 111.

    def test_solve_linear_doutputs_read_only(self):
        class BadComp(QuadraticJacVec):
            def solve_linear(self, d_outputs, d_residuals, mode):
                super().solve_linear(d_outputs, d_residuals, mode)
                d_outputs['x'] = 0.  # should not be allowed

        prob = om.Problem()
        prob.model.add_subsystem('bad', BadComp())
        prob.setup()
        prob.run_model()
        prob.model.run_linearize()

        # check input vector
        with self.assertRaises(ValueError) as cm:
            prob.model.run_solve_linear('rev')

        self.assertEqual(str(cm.exception),
                         "'bad' <class BadComp>: Attempt to set value of 'x' in output vector "
                         "when it is read only.")

    def test_solve_linear_dresids_read_only(self):
        class BadComp(QuadraticJacVec):
            def solve_linear(self, d_outputs, d_residuals, mode):
                super().solve_linear(d_outputs, d_residuals, mode)
                d_residuals['x'] = 0.  # should not be allowed

        prob = om.Problem()
        prob.model.add_subsystem('bad', BadComp())
        prob.setup()
        prob.run_model()
        prob.model.run_linearize()

        # check input vector
        with self.assertRaises(ValueError) as cm:
            prob.model.run_solve_linear('fwd')

        self.assertEqual(str(cm.exception),
                         "'bad' <class BadComp>: Attempt to set value of 'x' in residual vector "
                         "when it is read only.")

    def test_solve_linear_read_only_reset(self):
        class BadComp(QuadraticJacVec):
            def solve_linear(self, d_outputs, d_residuals, mode):
                super().solve_linear(d_outputs, d_residuals, mode)
                raise om.AnalysisError("It's just a scratch.")

        prob = om.Problem()
        prob.model.add_subsystem('bad', BadComp())
        prob.setup()
        prob.run_model()
        prob.model.run_linearize()

        with self.assertRaises(om.AnalysisError):
            prob.model.run_solve_linear('fwd')

        # verify read_only status is reset after AnalysisError
        prob.model.bad._vectors['residual']['linear']['x'] = 111.


class ListFeatureTestCase(unittest.TestCase):

    def setUp(self):

        group = om.Group()

        sub = group.add_subsystem('sub', om.Group(), promotes_inputs=['a', 'b', 'c'])

        sub.add_subsystem('comp1', QuadraticComp(), promotes_inputs=['a', 'b', 'c'])
        sub.add_subsystem('comp2', QuadraticComp(), promotes_inputs=['a', 'b', 'c'])

        global prob
        prob = om.Problem(model=group)
        prob.setup()

        prob.set_val('a', 1.)
        prob.set_val('b', -4.)
        prob.set_val('c', 3.)
        prob.run_model()

    def test_list_return_value(self):
        # list inputs
        inputs = prob.model.list_inputs(out_stream=None, prom_name=False, return_format='list')
        self.assertEqual(sorted(inputs), [
            ('sub.comp1.a', {'val': [1.]}),
            ('sub.comp1.b', {'val': [-4.]}),
            ('sub.comp1.c', {'val': [3.]}),
            ('sub.comp2.a', {'val': [1.]}),
            ('sub.comp2.b', {'val': [-4.]}),
            ('sub.comp2.c', {'val': [3.]})
        ])

        inputs = prob.model.list_inputs(out_stream=None, prom_name=False, return_format='dict')
        self.assertEqual(inputs, {
            'sub.comp1.a': {'val': [1.]},
            'sub.comp1.b': {'val': [-4.]},
            'sub.comp1.c': {'val': [3.]},
            'sub.comp2.a': {'val': [1.]},
            'sub.comp2.b': {'val': [-4.]},
            'sub.comp2.c': {'val': [3.]}
        })

        # list outputs
        outputs = prob.model.list_outputs(out_stream=None, prom_name=False, return_format='list')
        self.assertEqual(sorted(outputs), [
            ('sub.comp1.x', {'val': [3.]}),
            ('sub.comp2.x', {'val': [3.]})
        ])

        outputs = prob.model.list_outputs(out_stream=None, prom_name=False, return_format='dict')
        self.assertEqual(outputs, {
            'sub.comp1.x': {'val': [3.]},
            'sub.comp2.x': {'val': [3.]}
        })

    def test_list_no_values(self):
        # list inputs
        inputs = prob.model.list_inputs(val=False, prom_name=False, out_stream=None)
        self.assertEqual([n[0] for n in sorted(inputs)], [
            'sub.comp1.a',
            'sub.comp1.b',
            'sub.comp1.c',
            'sub.comp2.a',
            'sub.comp2.b',
            'sub.comp2.c'
        ])

    def test_simple_list_vars_options(self):

        group = om.Group()

        comp1 = group.add_subsystem('comp1', om.IndepVarComp())
        comp1.add_output('a', 1.0, units='ft')
        comp1.add_output('b', 1.0, units='inch')
        comp1.add_output('c', 1.0, units='ft')

        sub = group.add_subsystem('sub', om.Group())
        sub.add_subsystem('comp2', QuadraticComp())
        sub.add_subsystem('comp3', QuadraticComp())

        group.connect('comp1.a', 'sub.comp2.a')
        group.connect('comp1.b', 'sub.comp2.b')
        group.connect('comp1.c', 'sub.comp2.c')

        group.connect('comp1.a', 'sub.comp3.a')
        group.connect('comp1.b', 'sub.comp3.b')
        group.connect('comp1.c', 'sub.comp3.c')

        prob = om.Problem(model=group)
        prob.setup()

        prob['comp1.a'] = 1.
        prob['comp1.b'] = -4.
        prob['comp1.c'] = 3.
        prob.run_model()

        # list_inputs test
        stream = StringIO()
        inputs = prob.model.list_inputs(val=False, prom_name=False, out_stream=stream)
        text = stream.getvalue()
        self.assertEqual(sorted(inputs), [
            ('sub.comp2.a', {}),
            ('sub.comp2.b', {}),
            ('sub.comp2.c', {}),
            ('sub.comp3.a', {}),
            ('sub.comp3.b', {}),
            ('sub.comp3.c', {}),
        ])
        self.assertEqual(1, text.count("6 Input(s) in 'model'"))
        self.assertEqual(1, text.count("\nsub"))
        self.assertEqual(1, text.count("\n  comp2"))
        self.assertEqual(2, text.count("\n    a"))
        num_non_empty_lines = sum([1 for s in text.splitlines() if s.strip()])
        self.assertEqual(num_non_empty_lines, 12)

        # list_outputs tests
        # list implicit outputs
        outputs = prob.model.list_outputs(explicit=False, prom_name=False, out_stream=None)
        text = stream.getvalue()
        self.assertEqual(sorted(outputs), [
            ('sub.comp2.x', {'val': [3.]}),
            ('sub.comp3.x', {'val': [3.]})
        ])
        # list explicit outputs
        stream = StringIO()
        outputs = prob.model.list_outputs(implicit=False, prom_name=False, out_stream=None)
        self.assertEqual(sorted(outputs), [
            ('comp1.a', {'val': [1.]}),
            ('comp1.b', {'val': [-4.]}),
            ('comp1.c', {'val': [3.]}),
        ])


class CacheUsingComp(om.ImplicitComponent):
    def setup(self):
        self.cache = {}
        self.lin_sol_count = 0
        self.add_input('x', val=np.ones(10))
        self.add_output('y', val=np.zeros(10))

        self.declare_partials(of='*', wrt='*')

    def apply_nonlinear(self, inputs, outputs, residuals):
        x = inputs['x']
        y = outputs['y']
        residuals['y'] = x * y ** 2

    def solve_nonlinear(self, inputs, outputs):
        x = inputs['x']
        outputs['y'] = x ** 2 + 1.0

    def linearize(self, inputs, outputs, partials):
        subjac = np.zeros((inputs['x'].size, inputs['x'].size))
        for row, val in enumerate(inputs['x']):
            subjac[row, :] = inputs['x'] * 2.0
        partials['y', 'x'] = subjac
        self.lin_sol_count = 0

    def solve_linear(self, d_outputs, d_residuals, mode):
        # print('                    doutputs', d_outputs['y'])
        # print('dresids', d_residuals['y'])
        # if self.lin_sol_count in self.cache:
        #    print('cache  ', self.cache[self.lin_sol_count])

        fwd = mode == 'fwd'

        if self.lin_sol_count in self.cache:
            if fwd:
                assert(np.all(d_outputs['y'] == self.cache[self.lin_sol_count]))
            else:
                assert(np.all(d_residuals['y'] == self.cache[self.lin_sol_count]))

        if fwd:
            d_outputs['y'] = d_residuals['y'] + 2.
            self.cache[self.lin_sol_count] = d_outputs['y'].copy()
        else:  # rev
            d_residuals['y'] = d_outputs['y'] + 2.
            self.cache[self.lin_sol_count] = d_residuals['y'].copy()

        self.lin_sol_count += 1


class CacheLinSolutionTestCase(unittest.TestCase):
    def test_caching_fwd(self):
        p = om.Problem()
        p.model.add_subsystem('indeps', om.IndepVarComp('x', val=np.arange(10, dtype=float)))
        p.model.add_subsystem('C1', CacheUsingComp())
        p.model.connect('indeps.x', 'C1.x')
        p.model.add_design_var('indeps.x', cache_linear_solution=True)
        p.model.add_objective('C1.y')
        p.setup(mode='fwd')
        p.run_model()

        for i in range(10):
            p['indeps.x'] += np.arange(10, dtype=float)
            # run_model always runs setup_driver which resets the cached total jacobian object,
            # so save it here and restore after the run_model.  This is a contrived test.  In
            # real life, we only care about caching linear solutions when we're under run_driver.
            old_tot_jac = p.driver._total_jac
            p.run_model()
            p.driver._total_jac = old_tot_jac
            p.driver._compute_totals(of=['C1.y'], wrt=['indeps.x'])

    def test_caching_rev(self):
        p = om.Problem()
        p.model.add_subsystem('indeps', om.IndepVarComp('x', val=np.arange(10, dtype=float)))
        p.model.add_subsystem('C1', CacheUsingComp())
        p.model.connect('indeps.x', 'C1.x')
        p.model.add_design_var('indeps.x')
        p.model.add_objective('C1.y', cache_linear_solution=True)
        p.setup(mode='rev')
        p.run_model()

        for i in range(10):
            p['indeps.x'] += np.arange(10, dtype=float)
            # run_model always runs setup_driver which resets the cached total jacobian object,
            # so save it here and restore after the run_model.  This is a contrived test.  In
            # real life, we only care about caching linear solutions when we're under run_driver.
            old_tot_jac = p.driver._total_jac
            p.run_model()
            p.driver._total_jac = old_tot_jac
            p.driver._compute_totals(of=['C1.y'], wrt=['indeps.x'])


class LinearSystemCompPrimal(om.ImplicitComponent):
    def __init__(self, input_prefix, output_prefix, domap, **kwargs):
        super().__init__(**kwargs)
        self.Aname = input_prefix + 'A'
        self.bname = input_prefix + 'b'
        self.xname = output_prefix + 'x'
        self.domap = domap

    def initialize(self):
        self.options.declare('size', default=1, types=int)

    def setup(self):
        size = self.options['size']

        shape = (size, )

        if ':' in self.Aname and self.domap:
            self.add_input(self.Aname, primal_name=self.Aname.rpartition(':')[-1], val=np.eye(size))
        else:
            self.add_input(self.Aname, val=np.eye(size))
        if ':' in self.bname and self.domap:
            self.add_input(self.bname, primal_name=self.bname.rpartition(':')[-1], val=np.ones(shape))
        else:
            self.add_input(self.bname, val=np.ones(shape))
        if ':' in self.xname and self.domap:
            self.add_output(self.xname, primal_name=self.xname.rpartition(':')[-1], shape=shape)
        else:
            self.add_output(self.xname, shape=shape)

    def setup_partials(self):
        size = self.options['size']
        mat_size = size * size
        full_size = size

        row_col = np.arange(full_size, dtype="int")

        self.declare_partials(self.xname, self.bname, val=np.full(full_size, -1.0), rows=row_col, cols=row_col)

        rows = np.repeat(np.arange(full_size), size)

        cols = np.arange(mat_size)

        self.declare_partials(self.xname, self.Aname, rows=rows, cols=cols)

        cols = np.tile(np.arange(size), size)
        cols += np.repeat(np.arange(1), mat_size) * size

        self.declare_partials(of=self.xname, wrt=self.xname, rows=rows, cols=cols)

        if self.matrix_free:
            self.linear_solver = om.ScipyKrylov()
        else:
            self.linear_solver = om.DirectSolver()
        self.nonlinear_solver = om.NewtonSolver(solve_subsystems=False)

    def compute_primal(self, A, b, x):
        return A.dot(x) - b


class TestMappedNames(unittest.TestCase):
    def test_implicit_comp_primal_mapped_names(self):
        p = om.Problem()
        p.model.add_subsystem('comp', LinearSystemCompPrimal('my:', 'my:', domap=True, size=3,
                                                             derivs_method='cs'))

        p.setup()

        x = np.array([1, 2, -3])
        A = np.array([[5.0, -3.0, 2.0], [1.0, 7.0, -4.0], [1.0, 0.0, 8.0]])
        b = A.dot(x)

        p.set_val('comp.my:A', A)
        p.set_val('comp.my:b', b)
        p.set_val('comp.my:x', x)
        p.final_setup()
        p.run_model()

        assert_near_equal(p['comp.my:x'], x, .0001)

    def test_implicit_comp_primal_bad_inputs_no_mapping(self):
        p = om.Problem()
        p.model.add_subsystem('comp', LinearSystemCompPrimal('my:', '', domap=False))

        with self.assertRaises(RuntimeError) as ctx:
            p.setup()

        self.assertEqual(str(ctx.exception),
                         "'comp' <class LinearSystemCompPrimal>: compute_primal method args ['A', 'b', 'x'] "
                         "don't match the args ['my:A', 'my:b', 'x'] mapped from this component's inputs. To "
                         "map inputs to the compute_primal method, set the name used in compute_primal to the "
                         "'primal_name' arg when calling add_input/add_discrete_input. This is only necessary "
                         "if the declared component input name is not a valid Python name.")



    def test_implicit_comp_primal_bad_outputs_no_mapping(self):
        p = om.Problem()
        p.model.add_subsystem('comp', LinearSystemCompPrimal('', 'my:', domap=False))

        with self.assertRaises(RuntimeError) as ctx:
            p.setup()

        self.assertEqual(str(ctx.exception),
                         "'comp' <class LinearSystemCompPrimal>: compute_primal method args ['A', 'b', 'x'] "
                         "don't match the args ['A', 'b', 'my:x'] mapped from this component's inputs. To map "
                         "inputs to the compute_primal method, set the name used in compute_primal to the 'primal_name' "
                         "arg when calling add_input/add_discrete_input. This is only necessary if the declared "
                         "component input name is not a valid Python name.")



if __name__ == '__main__':
    unittest.main()
