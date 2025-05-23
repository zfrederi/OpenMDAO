"""
Class definition for SqliteRecorder, which provides dictionary backed by SQLite.
"""

from io import BytesIO

import os.path
import gc
import sqlite3

import json
import numpy as np

import pickle
import zlib

from openmdao import __version__ as openmdao_version
from openmdao.recorders.case_recorder import CaseRecorder, PICKLE_VER
from openmdao.utils.mpi import MPI
from openmdao.utils.record_util import dict_to_structured_array
from openmdao.utils.options_dictionary import OptionsDictionary
from openmdao.utils.general_utils import make_serializable, default_noraise
from openmdao.core.driver import Driver
from openmdao.core.system import System
from openmdao.core.problem import Problem
from openmdao.solvers.solver import Solver
from openmdao.utils.om_warnings import issue_warning, CaseRecorderWarning


"""
SQL case database version history.
----------------------------------
14-- OpenMDAO 3.8.1
     Metadata pickle and JSON blobs are compressed.
     Save metadata separately for parallel runs.
13-- OpenMDAO 3.8.1
     Added OpenMDAO version number to recorder file
12-- OpenMDAO 3.6.1
     Change key for system metadata to use non-ambiguous separator
11-- OpenMDAO 3.2
     IndepVarComps are created automatically, so this changes some bookkeeping.
10-- OpenMDAO 3.0
     Added abs_err and rel_err recording to Problem recording
9 -- OpenMDAO 3.0
     Changed the character to split the derivatives from 'of,wrt' to 'of!wrt' to allow for commas
     in variable names
8 -- OpenMDAO 3.0
     Added inputs, outputs, and residuals fields to problem_cases table. Added
     outputs and residuals fields to driver_iterations table
7 -- OpenMDAO 3.0
     Added derivatives field to table for recording problems.
6 -- OpenMDAO 3.X
     Removed abs2prom from the driver_metadata table.
5 -- OpenMDAO 2.5
     Added source column (driver name, system/solver pathname) to global iterations table.
4 -- OpenMDAO 2.4
     Added variable settings metadata that contains scaling info.
3 -- OpenMDAO 2.4
     Storing most data as JSON rather than binary numpy arrays.
2 -- OpenMDAO 2.4, merged 20 July 2018.
     Added support for recording derivatives from driver, resulting in a new table.
1 -- Through OpenMDAO 2.3
     Original implementation.
"""
format_version = 14

# separator, cannot be a legal char for names
META_KEY_SEP = '!'


def array_to_blob(array):
    """
    Make numpy array into a BLOB.

    Convert a numpy array to something that can be written
    to a BLOB field in sqlite.

    TODO : move this to a util file?

    Parameters
    ----------
    array : array
        The array that will be converted to a blob.

    Returns
    -------
    blob
        The blob created from the array.
    """
    out = BytesIO()
    np.save(out, array)
    out.seek(0)
    return sqlite3.Binary(out.read())


def blob_to_array(blob):
    """
    Convert sqlite BLOB to numpy array.

    TODO : move this to a util file?

    Parameters
    ----------
    blob : blob
        The blob that will be converted to an array.

    Returns
    -------
    array
        The array created from the blob.
    """
    out = BytesIO(blob)
    out.seek(0)
    return np.load(out, allow_pickle=True)


class SqliteRecorder(CaseRecorder):
    """
    Recorder that saves cases in a sqlite db.

    Parameters
    ----------
    filepath : str or Path
        Path to the recorder file.
    append : bool, optional
        Optional. If True, append to an existing case recorder file.
    pickle_version : int, optional
        The pickle protocol version to use when pickling metadata.
    record_viewer_data : bool, optional
        If True, record data needed for visualization.

    Attributes
    ----------
    _record_viewer_data : bool
        Flag indicating whether to record data needed to generate N2 diagram.
    connection : sqlite connection object
        Connection to the sqlite3 database.
    metadata_connection : sqlite connection object
        Connection to the sqlite3 database, if metadata is recorded separately.
    _record_metadata : Whether this process is recording metadata. Always True
        for serial runs, only True for rank 0 of parallel runs.
    _abs2prom : {'input': dict, 'output': dict}
        Dictionary mapping absolute names to promoted names.
    _prom2abs : {'input': dict, 'output': dict}
        Dictionary mapping promoted names to absolute names.
    _abs2meta : {'name': {}}
        Dictionary mapping absolute variable names to their metadata including units,
        bounds, and scaling.
    _pickle_version : int
        The pickle protocol version to use when pickling metadata.
    _filepath : str
        Path to the recorder file.
    _database_initialized : bool
        Flag indicating whether or not the database has been initialized.
    _started : set
        set of recording requesters for which this recorder has been started.
    _use_outputs_dir : bool
        Flag indicating if the database is being saved in the problem outputs dir.
    """

    def __init__(self, filepath, append=False, pickle_version=PICKLE_VER, record_viewer_data=True):
        """
        Initialize the SqliteRecorder.
        """
        if append:
            raise NotImplementedError("Append feature not implemented for SqliteRecorder")

        self.connection = None
        self.metadata_connection = None
        self._record_metadata = True
        self._record_viewer_data = record_viewer_data

        self._abs2prom = {'input': {}, 'output': {}}
        self._prom2abs = {'input': {}, 'output': {}}
        self._abs2meta = {}
        self._pickle_version = pickle_version
        self._filepath = str(filepath)

        self._use_outputs_dir = not (os.path.sep in self._filepath or '/' in self._filepath)

        self._database_initialized = False
        self._started = set()

        super().__init__(record_viewer_data)

    def _initialize_database(self, comm):
        """
        Initialize the database.

        Parameters
        ----------
        comm : MPI.Comm or <FakeComm> or None
            The communicator for the recorder (should be the comm for the Problem).
        """
        filepath = None
        self.connection = self.metadata_connection = None

        if MPI and comm and comm.size > 1:
            if self._record_on_proc:
                if self._parallel:
                    # recording on multiple procs, so a separate file for each recording proc
                    # plus a file for the common metadata, written by the lowest recording rank
                    rank = comm.rank
                    filepath = f"{self._filepath}_{rank}"
                    print("Note: SqliteRecorder is running on multiple processors. "
                          f"Cases from rank {rank} are being written to {filepath}.")
                    if rank == min(self._recording_ranks):
                        metadata_filepath = f'{self._filepath}_meta'
                        print("Note: Metadata is being recorded separately as "
                              f"{metadata_filepath}.")
                        try:
                            os.remove(metadata_filepath)
                            issue_warning("The existing case recorder metadata file, "
                                          f"{metadata_filepath}, is being overwritten.",
                                          category=UserWarning)
                        except OSError:
                            pass
                        self.metadata_connection = sqlite3.connect(metadata_filepath)
                    else:
                        self._record_metadata = False
                else:
                    # recording only on this proc
                    filepath = self._filepath
        else:
            # no MPI or comm size == 1
            filepath = self._filepath

        if filepath:
            try:
                os.remove(filepath)
                issue_warning(f'The existing case recorder file, {filepath},'
                              ' is being overwritten.', category=UserWarning)
            except OSError:
                pass

            self.connection = sqlite3.connect(filepath)
            if self._record_metadata and self.metadata_connection is None:
                self.metadata_connection = self.connection

            with self.connection as c:
                # used to keep track of the order of the case records across all case tables
                c.execute("CREATE TABLE global_iterations(id INTEGER PRIMARY KEY, "
                          "record_type TEXT, rowid INT, source TEXT)")

                c.execute("CREATE TABLE driver_iterations(id INTEGER PRIMARY KEY, "
                          "counter INT, iteration_coordinate TEXT, timestamp REAL, "
                          "success INT, msg TEXT, inputs TEXT, outputs TEXT, residuals TEXT)")
                c.execute("CREATE TABLE driver_derivatives(id INTEGER PRIMARY KEY, "
                          "counter INT, iteration_coordinate TEXT, timestamp REAL, "
                          "success INT, msg TEXT, derivatives BLOB)")
                c.execute("CREATE INDEX driv_iter_ind on driver_iterations(iteration_coordinate)")

                c.execute("CREATE TABLE problem_cases(id INTEGER PRIMARY KEY, "
                          "counter INT, case_name TEXT, timestamp REAL, "
                          "success INT, msg TEXT, inputs TEXT, outputs TEXT, residuals TEXT, "
                          "jacobian BLOB, abs_err REAL, rel_err REAL)")
                c.execute("CREATE INDEX prob_name_ind on problem_cases(case_name)")

                c.execute("CREATE TABLE system_iterations(id INTEGER PRIMARY KEY, "
                          "counter INT, iteration_coordinate TEXT, timestamp REAL, "
                          "success INT, msg TEXT, inputs TEXT, outputs TEXT, residuals TEXT)")
                c.execute("CREATE INDEX sys_iter_ind on system_iterations(iteration_coordinate)")

                c.execute("CREATE TABLE solver_iterations(id INTEGER PRIMARY KEY, "
                          "counter INT, iteration_coordinate TEXT, timestamp REAL, "
                          "success INT, msg TEXT, abs_err REAL, rel_err REAL, "
                          "solver_inputs TEXT, solver_output TEXT, solver_residuals TEXT)")
                c.execute("CREATE INDEX solv_iter_ind on solver_iterations(iteration_coordinate)")

                if self._record_metadata:
                    with self.metadata_connection as m:
                        m.execute("CREATE TABLE metadata(format_version INT, openmdao_version "
                                  "TEXT, abs2prom BLOB, prom2abs BLOB, abs2meta BLOB, "
                                  "var_settings BLOB,conns BLOB)")
                        m.execute("INSERT INTO metadata(format_version, openmdao_version, "
                                  "abs2prom, prom2abs) VALUES(?,?,?,?)",
                                  (format_version, openmdao_version, None, None))
                        m.execute("CREATE TABLE driver_metadata(id TEXT PRIMARY KEY, "
                                  "model_viewer_data TEXT)")
                        m.execute("CREATE TABLE system_metadata(id TEXT PRIMARY KEY, "
                                  "scaling_factors BLOB, component_metadata BLOB)")
                        m.execute("CREATE TABLE solver_metadata(id TEXT PRIMARY KEY, "
                                  "solver_options BLOB, solver_class TEXT)")

        self._database_initialized = True
        if MPI and comm and comm.size > 1:
            comm.barrier()

    def _make_abs2meta_serializable(self):
        """
        Convert all abs2meta variable properties to a form that can be dumped as JSON.
        """
        for meta in self._abs2meta.values():
            for prop, val in meta.items():
                meta[prop] = make_serializable(val)

    def _make_var_setting_serializable(self, var_settings):
        """
        Convert all var_settings variable properties to a form that can be dumped as JSON.

        Parameters
        ----------
        var_settings : dict
            Dictionary mapping absolute variable names to variable settings.

        Returns
        -------
        var_settings : dict
            Dictionary mapping absolute variable names to var settings that are JSON compatible.
        """
        # var_settings is already a copy at the outer level, so we just have to copy the
        # inner dicts to prevent modifying the original designvars, objectives, and constraints.
        for name, meta in var_settings.items():
            meta = meta.copy()
            for prop, val in meta.items():
                meta[prop] = make_serializable(val)
            var_settings[name] = meta
        return var_settings

    def startup(self, recording_requester, comm=None):
        """
        Prepare for a new run and create/update the abs2prom and prom2abs variables.

        Parameters
        ----------
        recording_requester : object
            Object to which this recorder is attached.
        comm : MPI.Comm or <FakeComm> or None
            The MPI communicator for the recorder (should be the comm for the Problem).
        """
        # we only want to set up recording once for each recording_requester
        if recording_requester in self._started:
            return

        super().startup(recording_requester, comm)

        # grab the system and driver
        if isinstance(recording_requester, Driver):
            system = recording_requester._problem().model
            driver = recording_requester
        elif isinstance(recording_requester, System):
            system = recording_requester
            driver = None
        elif isinstance(recording_requester, Problem):
            system = recording_requester.model
            driver = recording_requester.driver
        elif isinstance(recording_requester, Solver):
            system = recording_requester._system()
            driver = None
        else:
            raise ValueError('Driver encountered a recording_requester it cannot handle'
                             ': {0}'.format(recording_requester))

        if self._use_outputs_dir:
            self._filepath = system.get_outputs_dir(mkdir=True) / self._filepath

        if not self._database_initialized:
            self._initialize_database(comm)

        states = system._list_states_allprocs()

        if driver is None:
            desvars = system.get_design_vars(True, get_sizes=False, use_prom_ivc=False)
            responses = system.get_responses(True, get_sizes=False, use_prom_ivc=False)
            constraints = {}
            objectives = {}
            for name, data in responses.items():
                if data['type'] == 'con':
                    constraints[name] = data
                else:
                    objectives[name] = data

        # _get_vars_exec_order makes a collective MPI call so need to call in all procs
        var_order = system._get_vars_exec_order(inputs=True, outputs=True, local=False)

        if self.connection:

            if driver is not None:
                desvars = driver._designvars
                responses = driver._responses
                constraints = driver._cons
                objectives = driver._objs

            # merge current abs2prom and prom2abs with this system's version
            self._abs2prom['input'].update(system._resolver.abs2prom_iter('input'))
            self._abs2prom['output'].update(system._resolver.abs2prom_iter('output'))
            for v, abs_names in system._resolver.prom2abs_iter('input'):
                if v not in self._prom2abs['input']:
                    self._prom2abs['input'][v] = abs_names.copy()
                else:
                    lst = self._prom2abs['input'][v]
                    old = set(lst)
                    for name in abs_names:
                        if name not in old:
                            lst.append(name)

            # for outputs, there can be only one abs name per promoted name
            for v, abs_names in system._resolver.prom2abs_iter('output'):
                self._prom2abs['output'][v] = abs_names

            for name, meta in system.abs_meta_iter('output', local=False, discrete=True):
                if name not in self._abs2meta:
                    meta = meta.copy()
                    self._abs2meta[name] = meta
                    meta['type'] = ['output']
                    meta['explicit'] = name not in states

            for name, meta in system.abs_meta_iter('input', local=False, discrete=True):
                if name not in self._abs2meta:
                    meta = meta.copy()
                    self._abs2meta[name] = meta
                    meta['type'] = ['input']
                    meta['explicit'] = True

            for varinfo, var_type in [(desvars, 'desvar'), (responses, 'response'),
                                      (objectives, 'objective'), (constraints, 'constraint')]:
                for name, vmeta in varinfo.items():
                    srcname = vmeta['source']
                    self._abs2meta[srcname]['type'].append(var_type)
                    self._abs2meta[srcname]['explicit'] = srcname not in states

            self._make_abs2meta_serializable()

            # store the updated abs2prom and prom2abs
            abs2prom = zlib.compress(json.dumps(self._abs2prom).encode('ascii'))
            prom2abs = zlib.compress(json.dumps(self._prom2abs).encode('ascii'))
            abs2meta = zlib.compress(json.dumps(self._abs2meta).encode('ascii'))
            conns = zlib.compress(json.dumps(
                system._problem_meta['model_ref']()._conn_global_abs_in2out).encode('ascii'))

            # TODO: seems like we could clobber the var_settings for a desvar in cases where a
            # desvar is also a constraint... Make a test case and fix if needed.
            var_settings = {}
            var_settings.update(desvars)
            var_settings.update(objectives)
            var_settings.update(constraints)
            var_settings = self._make_var_setting_serializable(var_settings)
            var_settings['execution_order'] = var_order
            var_settings_json = zlib.compress(
                json.dumps(var_settings, default=default_noraise).encode('ascii'))

            if self._record_metadata:
                with self.metadata_connection as m:
                    m.execute("UPDATE metadata SET " +   # nosec: trusted input
                              "abs2prom=?, prom2abs=?, abs2meta=?, var_settings=?, conns=?",
                              (abs2prom, prom2abs, abs2meta, var_settings_json, conns))

        self._started.add(recording_requester)

    def record_iteration_driver(self, driver, data, metadata):
        """
        Record data and metadata from a Driver.

        Parameters
        ----------
        driver : Driver
            Driver in need of recording.
        data : dict
            Dictionary containing desvars, objectives, constraints, responses, and System vars.
        metadata : dict
            Dictionary containing execution metadata.
        """
        if not self._database_initialized:
            raise RuntimeError(f"{driver.msginfo} attempted to record iteration to "
                               f"'{self._filepath}', but database is not initialized;"
                               " `run_model()`, `run_driver()`, or `final_setup()` "
                               "must be called after adding a recorder.")

        if self.connection:
            outputs = data['output']
            inputs = data['input']
            residuals = data['residual']

            # convert to list so this can be dumped as JSON
            for in_out_resid in (inputs, outputs, residuals):
                if in_out_resid is None:
                    continue
                for var in in_out_resid:
                    in_out_resid[var] = make_serializable(in_out_resid[var])

            outputs_text = json.dumps(outputs)
            inputs_text = json.dumps(inputs)
            residuals_text = json.dumps(residuals)

            with self.connection as c:
                c = c.cursor()  # need a real cursor for lastrowid

                c.execute("INSERT INTO driver_iterations(counter, iteration_coordinate, "
                          "timestamp, success, msg, inputs, outputs, residuals) "
                          "VALUES(?,?,?,?,?,?,?,?)",
                          (self._counter, self._iteration_coordinate,
                           metadata['timestamp'], metadata['success'], metadata['msg'],
                           inputs_text, outputs_text, residuals_text))

                c.execute("INSERT INTO global_iterations(record_type, rowid, source) VALUES(?,?,?)",
                          ('driver', c.lastrowid, driver._get_name()))

    def record_iteration_problem(self, problem, data, metadata):
        """
        Record data and metadata from a Problem.

        Parameters
        ----------
        problem : Problem
            Problem in need of recording.
        data : dict
            Dictionary containing desvars, objectives, and constraints.
        metadata : dict
            Dictionary containing execution metadata.
        """
        if not self._database_initialized:
            raise RuntimeError(f"{problem.msginfo} attempted to record iteration to "
                               f"'{self._filepath}', but database is not initialized;"
                               " `run_model()`, `run_driver()`, or `final_setup()` "
                               "must be called after adding a recorder.")

        if self.connection:
            outputs = data['output']
            inputs = data['input']
            residuals = data['residual']

            driver = problem.driver
            if problem.recording_options['record_derivatives'] and \
               driver._designvars and driver._responses:
                totals = data['totals']
            else:
                totals = {}
            totals_array = dict_to_structured_array(totals)
            totals_blob = array_to_blob(totals_array)

            # convert to list so this can be dumped as JSON
            for in_out_resid in (inputs, outputs, residuals):
                if in_out_resid is None:
                    continue
                for var in in_out_resid:
                    in_out_resid[var] = make_serializable(in_out_resid[var])

            outputs_text = json.dumps(outputs)
            inputs_text = json.dumps(inputs)
            residuals_text = json.dumps(residuals)

            abs_err = data['abs'] if 'abs' in data else None
            rel_err = data['rel'] if 'rel' in data else None

            with self.connection as c:
                c = c.cursor()  # need a real cursor for lastrowid

                c.execute("INSERT INTO problem_cases(counter, case_name, "
                          "timestamp, success, msg, inputs, outputs, residuals, jacobian, "
                          "abs_err, rel_err ) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          (self._counter, metadata['name'],
                           metadata['timestamp'], metadata['success'], metadata['msg'],
                           inputs_text, outputs_text, residuals_text, totals_blob,
                           abs_err, rel_err))

                c.execute("INSERT INTO global_iterations(record_type, rowid, source) VALUES(?,?,?)",
                          ('problem', c.lastrowid, metadata['name']))

    def record_iteration_system(self, system, data, metadata):
        """
        Record data and metadata from a System.

        Parameters
        ----------
        system : System
            System in need of recording.
        data : dict
            Dictionary containing inputs, outputs, and residuals.
        metadata : dict
            Dictionary containing execution metadata.
        """
        if not self._database_initialized:
            raise RuntimeError(f"{system.msginfo} attempted to record iteration to "
                               f"'{self._filepath}', but database is not initialized;"
                               " `run_model()`, `run_driver()`, or `final_setup()` "
                               "must be called after adding a recorder.")

        if self.connection:
            inputs = data['input']
            outputs = data['output']
            residuals = data['residual']

            # convert to list so this can be dumped as JSON
            for i_o_r in (inputs, outputs, residuals):
                for var, dat in i_o_r.items():
                    i_o_r[var] = make_serializable(dat)

            outputs_text = json.dumps(outputs)
            inputs_text = json.dumps(inputs)
            residuals_text = json.dumps(residuals)

            with self.connection as c:
                c = c.cursor()  # need a real cursor for lastrowid

                c.execute("INSERT INTO system_iterations(counter, iteration_coordinate, "
                          "timestamp, success, msg, inputs , outputs , residuals ) "
                          "VALUES(?,?,?,?,?,?,?,?)",
                          (self._counter, self._iteration_coordinate,
                           metadata['timestamp'], metadata['success'], metadata['msg'],
                           inputs_text, outputs_text, residuals_text))

                # get the pathname of the source system
                source_system = system.pathname
                if source_system == '':
                    source_system = 'root'

                c.execute("INSERT INTO global_iterations(record_type, rowid, source) VALUES(?,?,?)",
                          ('system', c.lastrowid, source_system))

    def record_iteration_solver(self, solver, data, metadata):
        """
        Record data and metadata from a Solver.

        Parameters
        ----------
        solver : Solver
            Solver in need of recording.
        data : dict
            Dictionary containing outputs, residuals, and errors.
        metadata : dict
            Dictionary containing execution metadata.
        """
        if not self._database_initialized:
            raise RuntimeError(f"{solver.msginfo} attempted to record iteration to "
                               f"'{self._filepath}', but database is not initialized;"
                               " `run_model()`, `run_driver()`, or `final_setup()` "
                               "must be called after adding a recorder.")

        if self.connection:
            abs = data['abs']
            rel = data['rel']
            inputs = data['input']
            outputs = data['output']
            residuals = data['residual']

            # convert to list so this can be dumped as JSON
            for i_o_r in (inputs, outputs, residuals):
                if i_o_r is None:
                    continue
                for var in i_o_r:
                    i_o_r[var] = make_serializable(i_o_r[var])

            outputs_text = json.dumps(outputs)
            inputs_text = json.dumps(inputs)
            residuals_text = json.dumps(residuals)

            with self.connection as c:
                c = c.cursor()  # need a real cursor for lastrowid

                c.execute("INSERT INTO solver_iterations(counter, iteration_coordinate, "
                          "timestamp, success, msg, abs_err, rel_err, "
                          "solver_inputs, solver_output, solver_residuals) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (self._counter, self._iteration_coordinate,
                           metadata['timestamp'], metadata['success'], metadata['msg'],
                           abs, rel, inputs_text, outputs_text, residuals_text))

                # get the pathname of the source system
                source_system = solver._system().pathname
                if source_system == '':
                    source_system = 'root'

                # get solver type from SOLVER class attribute to determine the solver pathname
                solver_type = solver.SOLVER[0:2]
                if solver_type == 'NL':
                    source_solver = source_system + '.nonlinear_solver'
                elif solver_type == 'LS':
                    source_solver = source_system + '.nonlinear_solver.linesearch'
                else:
                    raise RuntimeError("Solver type '%s' not recognized during recording. "
                                       "Expecting NL or LS" % solver.SOLVER)

                c.execute("INSERT INTO global_iterations(record_type, rowid, source) VALUES(?,?,?)",
                          ('solver', c.lastrowid, source_solver))

    def record_viewer_data(self, model_viewer_data, key='Driver'):
        """
        Record model viewer data.

        Parameters
        ----------
        model_viewer_data : dict
            Data required to visualize the model.
        key : str, optional
            The unique ID to use for this data in the table.
        """
        if self._record_metadata and self.metadata_connection:
            json_data = json.dumps(model_viewer_data, default=default_noraise)

            # Note: recorded to 'driver_metadata' table for legacy/compatibility reasons.
            try:
                with self.metadata_connection as m:
                    m.execute("INSERT INTO driver_metadata(id, model_viewer_data) VALUES(?,?)",
                              (key, json_data))
            except sqlite3.IntegrityError:
                # This recorder already has model data.
                pass

    def record_metadata_system(self, system, run_number=None):
        """
        Record system metadata.

        Parameters
        ----------
        system : System
            The System for which to record metadata.
        run_number : int or None
            Number indicating which run the metadata is associated with.
            None for the first run, 1 for the second, etc.
        """
        if self._record_metadata and self.metadata_connection:

            scaling_vecs, user_options = self._get_metadata_system(system)

            if scaling_vecs is None:
                return

            scaling_factors = pickle.dumps(scaling_vecs, self._pickle_version)

            # try to pickle the metadata, report if it failed
            try:
                pickled_metadata = pickle.dumps(user_options, self._pickle_version)
            except Exception:
                try:
                    for key, values in user_options._dict.items():
                        pickle.dumps(values, self._pickle_version)
                except Exception:
                    pickled_metadata = pickle.dumps(OptionsDictionary(), self._pickle_version)
                    msg = f"Trying to record option '{key}' which cannot be pickled on this " \
                          "system. Set option 'recordable' to False. Skipping recording options " \
                          "for this system."
                    issue_warning(msg, prefix=system.msginfo, category=CaseRecorderWarning)

            path = system.pathname
            if not path:
                path = 'root'

            scaling_factors = sqlite3.Binary(zlib.compress(scaling_factors))
            pickled_metadata = sqlite3.Binary(zlib.compress(pickled_metadata))

            if run_number is None:
                name = path
            else:
                name = META_KEY_SEP.join([path, str(run_number)])

            with self.metadata_connection as m:
                m.execute("INSERT INTO system_metadata"
                          "(id, scaling_factors, component_metadata) "
                          "VALUES(?,?,?)", (name, scaling_factors,
                                            pickled_metadata))

    def record_metadata_solver(self, solver, run_number=None):
        """
        Record solver metadata.

        Parameters
        ----------
        solver : Solver
            The Solver for which to record metadata.
        run_number : int or None
            Number indicating which run the metadata is associated with.
            None for the first run, 1 for the second, etc.
        """
        if self._record_metadata and self.metadata_connection:
            path = solver._system().pathname
            solver_class = type(solver).__name__

            if not path:
                path = 'root'

            id = "{}.{}".format(path, solver_class)

            if run_number is not None:
                id = META_KEY_SEP.join([id, str(run_number)])

            solver_options = zlib.compress(pickle.dumps(solver.options, self._pickle_version))

            with self.metadata_connection as m:
                m.execute("INSERT INTO solver_metadata(id, solver_options, solver_class)"
                          " VALUES(?,?,?)", (id, sqlite3.Binary(solver_options), solver_class))

    def record_derivatives_driver(self, recording_requester, data, metadata):
        """
        Record derivatives data from a Driver.

        Parameters
        ----------
        recording_requester : object
            Driver in need of recording.
        data : dict
            Dictionary containing derivatives keyed by 'of,wrt' to be recorded.
        metadata : dict
            Dictionary containing execution metadata.
        """
        if self.connection:

            data_array = dict_to_structured_array(data)
            data_blob = array_to_blob(data_array)

            with self.connection as c:
                c = c.cursor()  # need a real cursor for lastrowid

                c.execute("INSERT INTO driver_derivatives(counter, iteration_coordinate, "
                          "timestamp, success, msg, derivatives) VALUES(?,?,?,?,?,?)",
                          (self._counter, self._iteration_coordinate,
                           metadata['timestamp'], metadata['success'], metadata['msg'],
                           data_blob))

    def shutdown(self):
        """
        Shut down the recorder.
        """
        # close database connection
        if self._record_metadata and self.metadata_connection and \
                self.metadata_connection != self.connection:
            self.metadata_connection.close()

        if self.connection:
            self.connection.close()

        # sqlite close() does not always write until garbage collection occurs.
        # If collection is not forced like this and a reader is immediately opened on
        # the same file, it may find a 0-length or malformed db.
        # See https://www.sqlite.org/c3ref/close.html for more info
        gc.collect()

    def delete_recordings(self):
        """
        Delete all the recordings.
        """
        if self.connection:
            self.connection.execute("DELETE FROM global_iterations")
            self.connection.execute("DELETE FROM driver_iterations")
            self.connection.execute("DELETE FROM driver_derivatives")
            self.connection.execute("DELETE FROM problem_cases")
            self.connection.execute("DELETE FROM system_iterations")
            self.connection.execute("DELETE FROM solver_iterations")
            self.connection.execute("DELETE FROM driver_metadata")
            self.connection.execute("DELETE FROM system_metadata")
            self.connection.execute("DELETE FROM solver_metadata")
