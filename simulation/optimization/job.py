import os
import tempfile

import simulation.constants
import simulation.model.constants
import simulation.model.options
import simulation.optimization.constants

import measurements.constants

import util.batch.universal.system
import util.io.env

import util.logging


class CostFunctionJob(util.batch.universal.system.Job):

    def __init__(self, cf_kind, model_options, output_dir=None, model_job_options=None, min_standard_deviations=None, min_measurements_correlations=None, max_box_distance_to_water=None, eval_f=True, eval_df=True, cost_function_job_options=None, include_initial_concentrations_factor_by_default=False):
        util.logging.debug('Initiating cost function job with cf_kind {}, eval_f {} and eval_df {}.'.format(cf_kind, eval_f, eval_df))

        # if no output dir, use tmp output dir
        remove_output_dir_on_close = output_dir is None
        if output_dir is None:
            output_dir = simulation.model.constants.DATABASE_TMP_DIR
            os.makedirs(output_dir, exist_ok=True)
            output_dir = tempfile.mkdtemp(dir=output_dir, prefix='cost_function_tmp_')

        # init job object
        super().__init__(output_dir, remove_output_dir_on_close=remove_output_dir_on_close)

        # convert model options
        model_options = simulation.model.options.as_model_options(model_options)

        # save CF options
        self.options['/cf/kind'] = cf_kind
        self.options['/cf/model_options'] = repr(model_options)
        self.options['/cf/model_job_options'] = repr(model_job_options)
        self.options['/cf/max_box_distance_to_water'] = max_box_distance_to_water
        self.options['/cf/min_standard_deviations'] = min_standard_deviations
        self.options['/cf/min_measurements_correlations'] = min_measurements_correlations
        self.options['/cf/include_initial_concentrations_factor_by_default'] = include_initial_concentrations_factor_by_default

        # prepare job options
        if cost_function_job_options is None:
            cost_function_job_options = {}

        # prepare job name
        try:
            job_name = cost_function_job_options['name']
        except KeyError:
            job_name = cf_kind
            if cf_kind == 'GLS':
                job_name = job_name + '_{min_measurements_correlations}'.format(min_measurements_correlations=min_measurements_correlations)
            job_name = job_name + '_' + model_options.model_name
            if max_box_distance_to_water is not None and max_box_distance_to_water != float('inf'):
                job_name = job_name + '_N{max_box_distance_to_water:d}'.format(max_box_distance_to_water=max_box_distance_to_water)

        # prepare node setup
        try:
            nodes_setup = cost_function_job_options['nodes_setup']
        except KeyError:
            nodes_setup = simulation.optimization.constants.COST_FUNCTION_NODES_SETUP_JOB.copy()
            if eval_df:
                nodes_setup['memory'] = nodes_setup['memory'] + 5
            if cf_kind == 'GLS':
                nodes_setup['memory'] = nodes_setup['memory'] + 20

        # init job file
        queue = None
        self.set_job_options(job_name, nodes_setup, queue=queue)

        # write python script
        commands = ['import numpy as np']
        commands += ['import simulation.model.options']
        commands += ['import simulation.optimization.cost_function']
        commands += ['import measurements.all.data']
        commands += ['import util.batch.universal.system']
        commands += ['import util.logging']

        if max_box_distance_to_water == float('inf'):
            max_box_distance_to_water = None
        if min_measurements_correlations == float('inf'):
            min_measurements_correlations = None

        commands += ['with util.logging.Logger():']
        commands += ['    model_options = {model_options!r}'.format(model_options=model_options)]
        commands += ['    measurements_object = measurements.all.data.all_measurements(tracers=model_options.tracers, min_standard_deviations={min_standard_deviations}, min_measurements_correlations={min_measurements_correlations}, max_box_distance_to_water={max_box_distance_to_water})'.format(
            min_standard_deviations=min_standard_deviations,
            min_measurements_correlations=min_measurements_correlations,
            max_box_distance_to_water=max_box_distance_to_water)]

        if model_job_options is not None:
            commands += ['    model_job_options = {model_job_options!r}'.format(model_job_options=model_job_options)]
        else:
            commands += ['    model_job_options = None']
        commands += ['    cf = simulation.optimization.cost_function.{cf_kind}(measurements_object=measurements_object, model_options=model_options, model_job_options=model_job_options, include_initial_concentrations_factor_by_default={include_initial_concentrations_factor_by_default})'.format(
            cf_kind=cf_kind,
            include_initial_concentrations_factor_by_default=include_initial_concentrations_factor_by_default)]

        if eval_f:
            commands += ['    cf.f()']
        if eval_df:
            commands += ['    cf.df()']
        commands += ['']

        script_str = os.linesep.join(commands)
        script_str = script_str.replace('array', 'np.array')

        python_script_file = os.path.join(output_dir, 'run.py')
        with open(python_script_file, mode='w') as f:
            f.write(script_str)
            f.flush()

        # prepare run command and write job file
        def export_env_command(env_name):
            try:
                env_value = util.io.env.load(env_name)
            except util.io.env.EnvironmentLookupError:
                return ''
            else:
                return 'export {env_name}={env_value}'.format(env_name=env_name, env_value=env_value)
        env_names = [simulation.constants.BASE_DIR_ENV_NAME, simulation.constants.SIMULATION_OUTPUT_DIR_ENV_NAME, simulation.constants.METOS3D_DIR_ENV_NAME, measurements.constants.BASE_DIR_ENV_NAME, util.batch.universal.system.BATCH_SYSTEM_ENV_NAME, util.io.env.PYTHONPATH_ENV_NAME]
        pre_commands = [export_env_command(env_name) for env_name in env_names]

        batch_system = util.batch.universal.system.BATCH_SYSTEM
        pre_commands.append(batch_system.pre_command('python'))

        pre_commands = [pre_command for pre_command in pre_commands if len(pre_command) > 0]
        pre_command = os.linesep.join(pre_commands)

        python_command = batch_system.command('python')
        command = '{python_command} {python_script_file}'.format(python_command=python_command, python_script_file=python_script_file)

        super().write_job_file(command, pre_command=pre_command)
