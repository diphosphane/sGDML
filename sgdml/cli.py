#!/usr/bin/python

# MIT License
#
# Copyright (c) 2018-2021 Stefan Chmiela
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from __future__ import print_function

import logging
import multiprocessing as mp
import argparse
import os
import shutil
import sys
import traceback
import time

import numpy as np
import scipy as sp

try:
    import torch
except ImportError:
    _has_torch = False
else:
    _has_torch = True

try:
    import ase
except ImportError:
    _has_ase = False
else:
    _has_ase = True

from . import __version__, DONE, NOT_DONE, MAX_PRINT_WIDTH
from .predict import GDMLPredict
from .train import GDMLTrain
from .utils import io, ui

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_NAME = 'sgdml'

log = logging.getLogger(__name__)


class AssistantError(Exception):
    pass


def _print_splash(max_processes=None, use_torch=False):

    logo_str = r"""         __________  __  _____
   _____/ ____/ __ \/  |/  / /
  / ___/ / __/ / / / /|_/ / /
 (__  ) /_/ / /_/ / /  / / /___
/____/\____/_____/_/  /_/_____/"""

    can_update, latest_version = _check_update()

    version_str = __version__
    version_str += (
        ' ' + ui.yellow_back_str(' Latest: ' + latest_version + ' ')
        if can_update
        else ''
    )

    # TODO: does this import test work in python3?
    max_processes_str = (
        ''
        if max_processes is None or max_processes >= mp.cpu_count()
        else ' [using {}]'.format(max_processes)
    )
    hardware_str = 'found {:d} CPU(s){}'.format(mp.cpu_count(), max_processes_str)

    if use_torch and _has_torch and torch.cuda.is_available():
        num_gpu = torch.cuda.device_count()
        if num_gpu > 0:
            hardware_str += ' / {:d} GPU(s)'.format(num_gpu)

    logo_str_split = logo_str.splitlines()
    print('\n'.join(logo_str_split[:-1]))
    ui.print_two_column_str(logo_str_split[-1] + '  ' + version_str, hardware_str)

    # Print update notice.
    if can_update:
        print(
            '\n'
            + ui.yellow_back_str(' UPDATE AVAILABLE ')
            + '\n'
            + '-' * MAX_PRINT_WIDTH
        )
        print(
            'A new stable release version {} of this software is available.'.format(
                latest_version
            )
        )
        print(
            'You can update your installation by running \'pip install sgdml --upgrade\'.'
        )


def _check_update():

    try:
        from urllib.request import urlopen
    except ImportError:
        from urllib2 import urlopen

    base_url = 'http://www.quantum-machine.org/gdml/'
    url = '%supdate.php?v=%s' % (base_url, __version__)

    can_update, must_update = '0', '0'
    latest_version = ''
    try:
        response = urlopen(url, timeout=1)
        can_update, must_update, latest_version = response.read().decode().split(',')
        response.close()
    except:
        pass

    return can_update == '1', latest_version


def _print_dataset_properties(dataset, title_str='Dataset properties'):

    print(ui.white_bold_str(title_str))

    n_mols, n_atoms, _ = dataset['R'].shape
    print('  {:<18} \'{}\''.format('Name:', ui.unicode_str(dataset['name'])))
    print('  {:<18} \'{}\''.format('Theory level:', ui.unicode_str(dataset['theory'])))
    print('  {:<18} {:<d}'.format('Atoms:', n_atoms))

    print('  {:<18} {:,} data points'.format('Size:', n_mols))

    ui.print_lattice(dataset['lattice'] if 'lattice' in dataset else None)

    if 'E' in dataset:

        e_unit = 'unknown unit'
        if 'e_unit' in dataset:
            e_unit = ui.unicode_str(dataset['e_unit'])

        print('  Energies [{}]'.format(e_unit))
        if 'E_min' in dataset and 'E_max' in dataset:
            E_min, E_max = dataset['E_min'], dataset['E_max']
        else:
            E_min, E_max = np.min(dataset['E']), np.max(dataset['E'])
        E_range_str = ui.gen_range_str(E_min, E_max)
        ui.print_two_column_str('    {:<16} {}'.format('Range:', E_range_str), 'min |-- range --| max')

        E_mean = dataset['E_mean'] if 'E_mean' in dataset else np.mean(dataset['E'])
        print('    {:<16} {:<.3f}'.format('Mean:', E_mean))

        E_var = dataset['E_var'] if 'E_var' in dataset else np.var(dataset['E'])
        print('    {:<16} {:<.3f}'.format('Variance:', E_var))
    else:
        print('  {:<18} {}'.format('Energies:', 'n/a'))

    f_unit = 'unknown unit'
    if 'r_unit' in dataset and 'e_unit' in dataset:
        f_unit = (
            ui.unicode_str(dataset['e_unit']) + '/' + ui.unicode_str(dataset['r_unit'])
        )

    print('  Forces [{}]'.format(f_unit))

    if 'F_min' in dataset and 'F_max' in dataset:
        F_min, F_max = dataset['F_min'], dataset['F_max']
    else:
        F_min, F_max = np.min(dataset['F'].ravel()), np.max(dataset['F'].ravel())
    F_range_str = ui.gen_range_str(F_min, F_max)
    ui.print_two_column_str('    {:<16} {}'.format('Range:', F_range_str), 'min |-- range --| max')

    F_mean = dataset['F_mean'] if 'F_mean' in dataset else np.mean(dataset['F'].ravel())
    print('    {:<16} {:<.3f}'.format('Mean:', F_mean))

    F_var = dataset['F_var'] if 'F_var' in dataset else np.var(dataset['F'].ravel())
    print('    {:<16} {:<.3f}'.format('Variance:', F_var))

    print('  {:<18} {}'.format('Fingerprint:', ui.unicode_str(dataset['md5'])))

    # if 'code_version' in dataset:
    #    print('  {:<18} sGDML {}'.format('Created with:', ui.unicode_str(dataset['code_version'])))

    idx = np.random.choice(n_mols, 1)[0]
    r = dataset['R'][idx, :, :]
    e = np.squeeze(dataset['E'][idx]) if 'E' in dataset else None
    f = dataset['F'][idx, :, :]
    lattice = dataset['lattice'] if 'lattice' in dataset else None

    print(
        '\n'
        + ui.white_bold_str('Example geometry')
        + ' (no. {:,}, chosen randomly)'.format(idx + 1)
    )
    xyz_info_str = 'Copy & paste the string below into Jmol (www.jmol.org), Avogadro (www.avogadro.cc), etc. to visualize one of the geometries from this dataset. A new example will be drawn on each run.'
    xyz_info_str = ui.wrap_str(xyz_info_str, width=MAX_PRINT_WIDTH - 2)
    xyz_info_str = ui.indent_str(xyz_info_str, 2)
    print(xyz_info_str + '\n')

    xyz_str = io.generate_xyz_str(r, dataset['z'], e=e, f=f, lattice=lattice)
    xyz_str = ui.indent_str(xyz_str, 2)

    cut_str = '---- COPY HERE '
    cut_str_reps = int(np.floor((MAX_PRINT_WIDTH - 6) / len(cut_str)))
    cutline_str = ui.gray_str('  -' + cut_str * cut_str_reps + '-----')

    print(cutline_str)
    print(xyz_str)
    print(cutline_str)


def _print_task_properties(
    use_sym, use_cprsn, use_E, use_E_cstr, title_str='Task properties'
):

    print(ui.white_bold_str(title_str))

    # print('  {:<18} {}'.format('Solver:', ui.unicode_str('[solver name]')))
    # print('    {:<16} {}'.format('Tolerance:', '[tol]'))

    energy_fix_str = (
        (
            'kernel constraints (+E)'
            if use_E_cstr
            else 'global integration constant recovery'
        )
        if use_E
        else 'none'
    )
    print('  {:<16} {}'.format('Energy handling:', energy_fix_str))

    print(
        '  {:<16} {}'.format(
            'Symmetries:', 'include (sGDML)' if use_sym else 'ignore (GDML)'
        )
    )
    print(
        '  {:<16} {}'.format(
            'Compression:', 'requested' if use_cprsn else 'not requested'
        )
    )


def _print_model_properties(model, title_str='Model properties'):

    print(ui.white_bold_str(title_str))

    print('  {:<18}'.format('Dataset'))
    print('    {:<16} \'{}\''.format('Name:', ui.unicode_str(model['dataset_name'])))
    print('    {:<16} \'{}\''.format('Theory level:', ui.unicode_str(model['dataset_theory'])))

    n_atoms = len(model['z'])
    print('    {:<16} {:<d}'.format('Atoms:', n_atoms))

    ui.print_lattice(model['lattice'] if 'lattice' in model else None, inset=True)

    print('  {:<18} {:<d}'.format('Symmetries:', len(model['perms'])))

    _, cprsn_keep_idxs = np.unique(
        np.sort(model['perms'], axis=0), axis=1, return_index=True
    )
    n_atoms_kept = cprsn_keep_idxs.shape[0]
    print(
        '  {:<18} {}'.format(
            'Compression:',
            '{:<d} effective atoms'.format(n_atoms_kept)
            if 'use_cprsn' in model and model['use_cprsn']
            else 'n/a',
        )
    )

    print('  {:<18}'.format('Hyper-parameters'))
    print('    {:<16} {:<d}'.format('Length scale:', model['sig']))

    if 'lam' in model:
        print('    {:<16} {:<.0e}'.format('Regularization:', model['lam']))

    if 'solver_name' in model:
        print('  {:<18}'.format('Solver'))
        print('    {:<16} \'{}\''.format('Type:', model['solver_name']))

        if model['solver_name'] == 'cg':

            if 'solver_tol' in model:
                ui.print_two_column_str('    {:<16} {:<.0e}'.format('Tolerance:', model['solver_tol']), 'iterate until: norm(K*alpha - y) <= tol*norm(y) = {:<.0e}'.format(model['solver_tol']*model['norm_y_train']))

            if 'solver_resid' in model:
                is_conv = model['solver_resid'] <= model['solver_tol']*model['norm_y_train']
                print('    {:<16} {:<.0e}{}'.format('Converged to:', model['solver_resid'], '' if is_conv else ' (NOT CONVERGED)'))

            if 'solver_iters' in model:
                print('    {:<16} {:<d}'.format('Iterations:', model['solver_iters']))

            if 'n_inducing_pts_init' in model and 'inducing_pts_idxs' in model:
                n_inducing_pts_final = len(model['inducing_pts_idxs']) // (3*n_atoms)
                increased_str = ' (increased to {:<d})'.format(n_inducing_pts_final) if model['n_inducing_pts_init'] < n_inducing_pts_final else ''
                print('    {:<16} {:<d}{}'.format('Inducing points:', model['n_inducing_pts_init'], increased_str))
    else:
        print('  {:<18} {}'.format('Solver:', 'unknown'))


    n_train = len(model['idxs_train'])
    ui.print_two_column_str(
        '  {:<18} {:,} points'.format('Trained on:', n_train),
        'from \'' + ui.unicode_str(model['md5_train']) + '\'',
    )

    if model['use_E']:
        e_err = model['e_err'].item()
    f_err = model['f_err'].item()

    n_valid = len(model['idxs_valid'])
    is_valid = not np.isnan(f_err['mae']) and not np.isnan(f_err['rmse'])
    ui.print_two_column_str(
        '  {:<18} {}{:,} points'.format(
            'Validated on:', '' if is_valid else '[pending] ', n_valid
        ),
        'from \'' + ui.unicode_str(model['md5_valid']) + '\'',
    )

    n_test = int(model['n_test'])
    is_test = n_test > 0
    if is_test:
        ui.print_two_column_str(
            '  {:<18} {:,} points'.format('Tested on:', n_test),
            'from \'' + ui.unicode_str(model['md5_test']) + '\'',
        )
    else:
        print('  {:<18} {}'.format('Test:', '[pending]'))

    e_unit = 'unknown unit'
    f_unit = 'unknown unit'
    if 'r_unit' in model and 'e_unit' in model:
        e_unit = model['e_unit']
        f_unit = ui.unicode_str(model['e_unit']) + '/' + ui.unicode_str(model['r_unit'])

    if is_valid:
        action_str = 'Validation' if not is_valid else 'Expected test'
        print('  {:<18}'.format('{} errors (MAE/RMSE)'.format(action_str)))
        if model['use_E']:
            print(
                '    {:<16} {:>.4f}/{:>.4f} [{}]'.format(
                    'Energy:', e_err['mae'], e_err['rmse'], e_unit
                )
            )
        print(
            '    {:<16} {:>.4f}/{:>.4f} [{}]'.format(
                'Forces:', f_err['mae'], f_err['rmse'], f_unit
            )
        )

def _print_next_step(prev_step, task_dir=None, model_dir=None, model_files=None, dataset_path=None):

    if prev_step == 'create':

        assert task_dir is not None

        ui.print_step_title(
            'NEXT STEP', '{} train {} <valid_dataset_file>'.format(PACKAGE_NAME, task_dir), underscore=False
        )

    elif prev_step == 'train' or prev_step == 'validate' or prev_step == 'resume':

        assert model_dir is not None and model_files is not None

        if dataset_path is None:
            dataset_path = '<test_dataset_file>'

        n_models = len(model_files)
        if n_models == 1:
            model_file_path = os.path.join(model_dir, model_files[0])
            ui.print_step_title(
                'NEXT STEP',
                '{} test {} {} [<n_test>]'.format(
                    PACKAGE_NAME, model_file_path, dataset_path
                ),
                underscore=False,
            )
        else:
            ui.print_step_title(
                'NEXT STEP',
                '{} select {}'.format(PACKAGE_NAME, model_dir),
                underscore=False,
            )

    elif prev_step == 'select':

        assert model_files is not None 

        ui.print_step_title(
            'NEXT STEP',
            '{} test {} <test_dataset_file> [<n_test>]'.format(PACKAGE_NAME, model_files[0]),
            underscore=False,
        )

    else:
        raise AssistantError(
            'Unexpected previous step string.'
        )


def all(
    dataset,
    valid_dataset,
    test_dataset,
    n_train,
    n_valid,
    n_test,
    sigs,
    gdml,
    use_E,
    use_E_cstr,
    use_cprsn,
    overwrite,
    max_processes,
    use_torch,
    solver,
    n_inducing_pts_init,
    interact_cut_off,
    task_dir=None,
    model_file=None,
    **kwargs
):

    print(
        '\n' + ui.white_back_str(' STEP 0 ') + ' Dataset(s)\n' + '-' * MAX_PRINT_WIDTH
    )

    _, dataset_extracted = dataset
    _print_dataset_properties(dataset_extracted, title_str='Properties')

    if valid_dataset is None:
        valid_dataset = dataset
    else:
        _, valid_dataset_extracted = valid_dataset
        print()
        _print_dataset_properties(
            valid_dataset_extracted, title_str='Properties (validation)'
        )

        if not np.array_equal(dataset_extracted['z'], valid_dataset_extracted['z']):
            raise AssistantError(
                'Atom composition or order in validation dataset does not match the one in bulk dataset.'
            )

    if test_dataset is None:
        test_dataset = dataset
    else:
        _, test_dataset_extracted = test_dataset
        _print_dataset_properties(test_dataset_extracted, title_str='Properties (test)')

        if not np.array_equal(dataset_extracted['z'], test_dataset_extracted['z']):
            raise AssistantError(
                'Atom composition or order in test dataset does not match the one in bulk dataset.'
            )

    ui.print_step_title('STEP 1', 'Cross-validation task creation')
    task_dir = create(
        dataset,
        valid_dataset,
        n_train,
        n_valid,
        sigs,
        gdml,
        use_E,
        use_E_cstr,
        use_cprsn,
        overwrite,
        max_processes,
        task_dir,
        solver=solver,
        n_inducing_pts_init=n_inducing_pts_init,
        interact_cut_off=interact_cut_off,
        **kwargs
    )

    ui.print_step_title('STEP 2', 'Training and validation')
    task_dir_arg = io.is_dir_with_file_type(task_dir, 'task')
    model_dir_or_file_path = train(
        task_dir_arg, valid_dataset, overwrite, max_processes, use_torch, **kwargs
    )

    model_dir_arg = io.is_dir_with_file_type(
        model_dir_or_file_path, 'model', or_file=True
    )

    ui.print_step_title('STEP 3', 'Hyper-parameter selection')
    model_file_name = select(
        model_dir_arg, overwrite, max_processes, model_file, **kwargs
    )

    ui.print_step_title('STEP 4', 'Testing')
    model_dir_arg = io.is_dir_with_file_type(model_file_name, 'model', or_file=True)
    test(
        model_dir_arg,
        test_dataset,
        n_test,
        overwrite=False,
        max_processes=max_processes,
        use_torch=use_torch,
        **kwargs
    )

    print(
        '\n'
        + ui.color_str('  DONE  ', fore_color=ui.BLACK, back_color=ui.GREEN, bold=True)
        + ' Training assistant finished sucessfully.'
    )
    print('         This is your model file: \'{}\''.format(model_file_name))


# if training job exists and is a subset of the requested cv range, add new tasks
# otherwise, if new range is different or smaller, fail
def create(  # noqa: C901
    dataset,
    valid_dataset,
    n_train,
    n_valid,
    sigs,
    gdml,
    use_E,
    use_E_cstr,
    use_cprsn,
    overwrite,
    max_processes,
    task_dir=None,
    solver='analytic',
    n_inducing_pts_init=None,
    interact_cut_off=None,
    command=None,
    **kwargs
):

    has_valid_dataset = not (valid_dataset is None or valid_dataset == dataset)

    dataset_path, dataset = dataset
    n_data = dataset['F'].shape[0]

    func_called_directly = (
        command == 'create'
    )  # has this function been called from command line or from 'all'?
    if func_called_directly:
        ui.print_step_title('TASK CREATION')
        _print_dataset_properties(dataset)
        print()

    _print_task_properties(
        use_sym=not gdml, use_cprsn=use_cprsn, use_E=use_E, use_E_cstr=use_E_cstr
    )
    print()

    if n_data < n_train:
        raise AssistantError(
            'Dataset only contains {} points, can not train on {}.'.format(
                n_data, n_train
            )
        )

    if not has_valid_dataset:
        valid_dataset_path, valid_dataset = dataset_path, dataset
        if n_data - n_train < n_valid:
            raise AssistantError(
                'Dataset only contains {} points, can not train on {} and validate on {}.'.format(
                    n_data, n_train, n_valid
                )
            )
    else:
        valid_dataset_path, valid_dataset = valid_dataset
        n_valid_data = valid_dataset['R'].shape[0]
        if n_valid_data < n_valid:
            raise AssistantError(
                'Validation dataset only contains {} points, can not validate on {}.'.format(
                    n_data, n_valid
                )
            )

    if sigs is None:
        log.info(
            'Kernel hyper-parameter sigma was automatically set to range \'10:10:100\'.'
        )
        sigs = list(range(10, 100, 10))  # default range

    if task_dir is None:
        task_dir = io.train_dir_name(
            dataset,
            n_train,
            use_sym=not gdml,
            use_cprsn=use_cprsn,
            use_E=use_E,
            use_E_cstr=use_E_cstr,
        )

    task_file_names = []
    if os.path.exists(task_dir):
        if overwrite:
            log.info('Overwriting existing training directory.')
            shutil.rmtree(task_dir, ignore_errors=True)
            os.makedirs(task_dir)
        else:
            if io.is_task_dir_resumeable(
                task_dir, dataset, valid_dataset, n_train, n_valid, sigs, gdml
            ):
                log.info(
                    'Resuming existing hyper-parameter search in \'{}\'.'.format(
                        task_dir
                    )
                )

                # Get all task file names.
                try:
                    _, task_file_names = io.is_dir_with_file_type(task_dir, 'task')
                except Exception:
                    pass
            else:
                raise AssistantError(
                    'Unfinished hyper-parameter search found in \'{}\'.\n'.format(
                        task_dir
                    )
                    + 'Run \'%s %s -o %s %d %d -s %s\' to overwrite.'
                    % (
                        PACKAGE_NAME,
                        command,
                        dataset_path,
                        n_train,
                        n_valid,
                        ' '.join(str(s) for s in sigs),
                    )
                )
    else:
        os.makedirs(task_dir)

    if task_file_names:

        with np.load(
            os.path.join(task_dir, task_file_names[0]), allow_pickle=True
        ) as task:
            tmpl_task = dict(task)
    else:
        if not use_E:
            log.info(
                'Energy labels will be ignored for training.\n'
                + 'Note: If available in the dataset file, the energy labels will however still be used to generate stratified training, test and validation datasets. Otherwise a random sampling is used.'
            )

        if 'E' not in dataset:
            log.warning(
                'Training dataset will be sampled with no guidance from energy labels (i.e. randomly)!'
            )

        if 'E' not in valid_dataset:
            log.warning(
                'Validation dataset will be sampled with no guidance from energy labels (i.e. randomly)!\n'
                + 'Note: Larger validation datasets are recommended due to slower convergence of the error.'
            )

        if ('lattice' in dataset) ^ ('lattice' in valid_dataset):
            log.error('One of the datasets specifies lattice vectors and one does not!')
            # TODO: stop program?

        if 'lattice' in dataset or 'lattice' in valid_dataset:
            log.info(
                'Lattice vectors found in dataset: applying periodic boundary conditions.'
            )

        gdml_train = GDMLTrain(max_processes=max_processes)
        try:
            tmpl_task = gdml_train.create_task(
                dataset,
                n_train,
                valid_dataset,
                n_valid,
                sig=1,
                use_sym=not gdml,
                use_E=use_E,
                use_E_cstr=use_E_cstr,
                use_cprsn=use_cprsn,
                solver=solver,
                n_inducing_pts_init=n_inducing_pts_init,
                interact_cut_off=interact_cut_off,
                callback=ui.callback,
            )  # template task
        except:
            print()
            log.critical(traceback.format_exc())
            sys.exit()

    n_written = 0
    for sig in sigs:
        tmpl_task['sig'] = sig
        task_file_name = io.task_file_name(tmpl_task)
        task_path = os.path.join(task_dir, task_file_name)

        if os.path.isfile(task_path):
            log.warning('Skipping existing task \'{}\'.'.format(task_file_name))
        else:
            np.savez_compressed(task_path, **tmpl_task)
            n_written += 1
    if n_written > 0:
        log.done(
            'Writing {:d}/{:d} task(s) with {} training points each.'.format(
                n_written, len(sigs), tmpl_task['R_train'].shape[0]
            )
        )

    if func_called_directly:
        _print_next_step('create', task_dir=task_dir)

    return task_dir

def train(
    task_dir, valid_dataset, overwrite, max_processes, use_torch, command=None, **kwargs
):

    task_dir, task_file_names = task_dir
    n_tasks = len(task_file_names)

    func_called_directly = (
        command == 'train'
    )  # has this function been called from command line or from 'all'?
    if func_called_directly:
        ui.print_step_title('MODEL TRAINING')

    def cprsn_callback(n_atoms, n_atoms_kept):
        log.info(
            '{:d} out of {:d} atoms remain after compression.\n'.format(
                n_atoms_kept, n_atoms
            )
            + 'Note: Compression reduces the size of the optimization problem the cost of prediction accuracy!'
        )

    unconv_model_file = '_unconv_model.npz'
    unconv_model_path = os.path.join(task_dir, unconv_model_file)

    def save_progr_callback(
        unconv_model,
    ):  # saves current (unconverged) model during iterative training

        np.savez_compressed(unconv_model_path, **unconv_model)

    try:
        gdml_train = GDMLTrain(max_processes=max_processes, use_torch=use_torch)
    except:
        print()
        log.critical(traceback.format_exc())
        sys.exit()

    prev_valid_err = -1

    for i, task_file_name in enumerate(task_file_names):
        if n_tasks > 1:
            if i > 0:
                print()
            print(ui.white_bold_str('Task {:d} of {:d}'.format(i + 1, n_tasks)))

        task_file_path = os.path.join(task_dir, task_file_name)
        with np.load(task_file_path, allow_pickle=True) as task:

            model_file_name = io.model_file_name(task, is_extended=False)
            model_file_path = os.path.join(task_dir, model_file_name)

            if not overwrite and os.path.isfile(model_file_path):
                log.warning(
                    'Skipping exising model \'{}\'.'.format(model_file_name)
                    + (
                        '\nRun \'{} train -o {}\' to overwrite.'.format(
                            PACKAGE_NAME, task_file_path
                        )
                        if func_called_directly
                        else ''
                    )
                )
                continue

            try:
                model = gdml_train.train(
                    task, cprsn_callback, save_progr_callback, ui.callback
                )
            except:
                print()
                log.critical(traceback.format_exc())
                sys.exit()
            else:
                if func_called_directly:
                    log.done('Writing model to file \'{}\''.format(model_file_path))
                np.savez_compressed(model_file_path, **model)

                # Delete temporary model, if one exists.
                unconv_model_exists = os.path.isfile(unconv_model_path)
                if unconv_model_exists:
                    os.remove(unconv_model_path)

                # Delete template model, if one exists.
                templ_model_path = os.path.join(task_dir, 'm0.npz')
                templ_model_exists = os.path.isfile(templ_model_path)
                if templ_model_exists:
                    os.remove(templ_model_path)

            # Validate model.
            model_dir = (task_dir, [model_file_name])
            valid_errs = test(
                model_dir,
                valid_dataset,
                -1,  # n_test = -1 -> validation mode
                overwrite,
                max_processes,
                use_torch,
                command,
                **kwargs
            )

            if prev_valid_err != -1 and prev_valid_err < valid_errs[0]:
                print()
                log.warning(
                    'Skipping remaining training tasks, as validation error is rising again.'
                )
                break

            prev_valid_err = valid_errs[0]

    model_dir_or_file_path = model_file_path if n_tasks == 1 else task_dir
    if func_called_directly:

        model_dir_arg = io.is_dir_with_file_type(model_dir_or_file_path, 'model', or_file=True)
        model_dir, model_files = model_dir_arg
        _print_next_step('train', model_dir=model_dir, model_files=model_files)

    return model_dir_or_file_path  # model directory or file


def _batch(iterable, n=1):
    l = len(iterable)
    for ndx in range(0, l, n):
        yield iterable[ndx : min(ndx + n, l)]


def _online_err(err, size, n, mae_n_sum, rmse_n_sum):

    err = np.abs(err)

    mae_n_sum += np.sum(err) / size
    mae = mae_n_sum / n

    rmse_n_sum += np.sum(err ** 2) / size
    rmse = np.sqrt(rmse_n_sum / n)

    return mae, mae_n_sum, rmse, rmse_n_sum


def resume(model, dataset, valid_dataset, overwrite, max_processes, use_torch, command=None, **kwargs):

    model_path, model = model
    dataset_path, dataset = dataset

    valid_dataset_arg = valid_dataset
    valid_dataset_path, valid_dataset = valid_dataset

    ui.print_step_title('RESUME TRAINING')
    _print_model_properties(model, title_str='Model properties (before)')
    print()

    if dataset['md5'] != model['md5_train']:
        raise AssistantError(
            'Fingerprint of provided training dataset does not match the one specified in model file.'
        )
    if valid_dataset['md5'] != model['md5_valid']:
        raise AssistantError(
            'Fingerprint of provided validation dataset does not match the one specified in model file.'
        )

    if model['solver_name'] == 'analytic':
        raise AssistantError(
            'This model was trained using the analytic solver and is thus converged to a higher accuracy than the iterative solver can provide!'
        )
    elif 'solver_resid' in model and 'solver_tol' in model:
        if model['solver_resid'] <= model['solver_tol']*model['norm_y_train']:
            log.warning('Model is already converged to the specified tolerance.')

    gdml_train = GDMLTrain(max_processes=max_processes, use_torch=use_torch)
    try:
        task = gdml_train.create_task_from_model(
            model,
            dataset,
        )
    except:
        print()
        log.critical(traceback.format_exc())
        sys.exit()
    del gdml_train

    n_train = len(model['idxs_train'])

    use_sym = model['perms'].shape[0] > 1
    use_E = 'e_err' in model
    use_E_cstr = 'alphas_E' in model

    task_dir = 'resume_' + io.train_dir_name(
        dataset,
        n_train,
        use_sym=use_sym,
        use_cprsn=False, # fix me
        use_E=use_E,
        use_E_cstr=use_E_cstr,
    )

    if os.path.exists(task_dir):
        if overwrite:
            log.info('Overwriting existing training directory.')
            shutil.rmtree(task_dir, ignore_errors=True)
            os.makedirs(task_dir)
        else:
            raise AssistantError(
                'Task directory already exists: \'{}\'.\n'.format(
                    task_dir
                )
                + 'Run \'%s %s %s %s %s -o\' to overwrite.'
                % (
                    PACKAGE_NAME,
                    command,
                    model_path,
                    dataset_path,
                    valid_dataset_path,
                )
            )
    else:
        os.makedirs(task_dir)

    # try to safe task file
    task_file_name = io.task_file_name(task)
    task_path = os.path.join(task_dir, task_file_name)
    if os.path.isfile(task_path):
        raise AssistantError(
            'Task file with same name already exists!'
        )
    else:
        np.savez_compressed(task_path, **task)

    task_dir_arg = io.is_dir_with_file_type(task_path, 'task', or_file=True)
    model_dir_or_file_path = train(
        task_dir_arg, valid_dataset_arg, overwrite, max_processes, use_torch, **kwargs
    )

    model_dir, model_files = io.is_dir_with_file_type(model_path, 'model', or_file=True)
    _print_next_step('resume', model_dir=model_dir, model_files=model_files)

def validate(
    model_dir,
    valid_dataset,
    overwrite,
    max_processes,
    use_torch,
    command=None,
    **kwargs
):

    dataset_path_extracted, dataset_extracted = valid_dataset

    func_called_directly = (
        command == 'validate'
    )  # has this function been called from command line or from 'all'?
    if func_called_directly:
        ui.print_step_title('MODEL VALIDATION')
        _print_dataset_properties(dataset_extracted)

    test(
        model_dir,
        valid_dataset,
        -1,  # n_test = -1 -> validation mode
        overwrite,
        max_processes,
        use_torch,
        command,
        **kwargs
    )

    if func_called_directly:

        model_dir, model_files = model_dir
        n_models = len(model_files)
        _print_next_step('validate', model_dir=model_dir, model_files=model_files)


def test(
    model_dir,
    test_dataset,
    n_test,
    overwrite,
    max_processes,
    use_torch,
    command=None,
    **kwargs
):  # noqa: C901

    # NOTE: this function runs a validation if n_test < 0 and test with all points if n_test == 0

    model_dir, model_file_names = model_dir
    n_models = len(model_file_names)

    n_test = 0 if n_test is None else n_test
    is_validation = n_test < 0
    is_test = n_test >= 0

    # NEW: turn me back on!
    # if (
    #    is_validation and n_models == 1
    # ):  # validation mode with only one model to validate
    #    log.warning('Skipping validation step as there is only one model to validate.')
    #    return

    dataset_path, dataset = test_dataset

    func_called_directly = (
        command == 'test'
    )  # has this function been called from command line or from 'all'?
    if func_called_directly:
        ui.print_step_title('MODEL TEST')
        _print_dataset_properties(dataset)

    F_rmse = []  # NEW

    # NEW

    DEBUG_WRITE = False

    if DEBUG_WRITE:
        if os.path.exists('test_pred.xyz'):
            os.remove('test_pred.xyz')
        if os.path.exists('test_ref.xyz'):
            os.remove('test_ref.xyz')
        if os.path.exists('test_diff.xyz'):
            os.remove('test_diff.xyz')

    # NEW

    num_workers, batch_size = 0, 0
    gdml_train = None
    for i, model_file_name in enumerate(model_file_names):

        model_path = os.path.join(model_dir, model_file_name)
        _, model = io.is_file_type(model_path, 'model')

        if i == 0 and command != 'all':
            print()
            _print_model_properties(model)
            print()

        if not np.array_equal(model['z'], dataset['z']):
            raise AssistantError(
                'Atom composition or order in dataset does not match the one in model.'
            )

        if ('lattice' in model) is not ('lattice' in dataset):
            if 'lattice' in model:
                raise AssistantError(
                    'Model contains lattice vectors, but dataset does not.'
                )
            elif 'lattice' in dataset:
                raise AssistantError(
                    'Dataset contains lattice vectors, but model does not.'
                )

        if model['use_E']:
            e_err = model['e_err'].item()
        f_err = model['f_err'].item()

        is_model_validated = not (np.isnan(f_err['mae']) or np.isnan(f_err['rmse']))

        if n_models > 1:
            if i > 0:
                print()
            print(
                ui.white_bold_str(
                    '%s model %d of %d'
                    % ('Testing' if is_test else 'Validating', i + 1, n_models)
                )
            )

        if is_validation:
            if is_model_validated and not overwrite:
                log.warning(
                    'Skipping already validated model \'{}\'.'.format(model_file_name)
                    + (
                        '\nRun \'{} validate -o {} {}\' to overwrite.'.format(
                            PACKAGE_NAME, model_path, dataset_path
                        )
                        if command == 'test'
                        else ''
                    )
                )
                continue

            if dataset['md5'] != model['md5_valid']:
                raise AssistantError(
                    'Fingerprint of provided validation dataset does not match the one specified in model file.'
                )

        test_idxs = model['idxs_valid']
        if is_test:

            # exclude training and/or test sets from validation set if necessary
            excl_idxs = np.empty((0,), dtype=np.uint)
            if dataset['md5'] == model['md5_train']:
                excl_idxs = np.concatenate([excl_idxs, model['idxs_train']]).astype(
                    np.uint
                )
            if dataset['md5'] == model['md5_valid']:
                excl_idxs = np.concatenate([excl_idxs, model['idxs_valid']]).astype(
                    np.uint
                )

            n_data = dataset['F'].shape[0]
            n_data_eff = n_data - len(excl_idxs)

            if (
                n_test == 0 and n_data_eff != 0
            ):  # test on all data points that have not been used for training or testing
                n_test = n_data_eff
                log.info(
                    'Test set size was automatically set to {:,} points.'.format(n_test)
                )

            if n_test == 0 or n_data_eff == 0:
                log.warning('Skipping! No unused points for test in provided dataset.')
                return
            elif n_data_eff < n_test:
                n_test = n_data_eff
                log.warning(
                    'Test size reduced to {:d}. Not enough unused points in provided dataset.'.format(
                        n_test
                    )
                )

            if 'E' in dataset:
                if gdml_train is None:
                    gdml_train = GDMLTrain(max_processes=max_processes)
                test_idxs = gdml_train.draw_strat_sample(
                    dataset['E'], n_test, excl_idxs=excl_idxs
                )
            else:
                test_idxs = np.delete(np.arange(n_data), excl_idxs)

                log.warning(
                    'Test dataset will be sampled with no guidance from energy labels (randomly)!\n'
                    + 'Note: Larger test datasets are recommended due to slower convergence of the error.'
                )
        # shuffle to improve convergence of online error
        np.random.shuffle(test_idxs)

        # NEW
        if DEBUG_WRITE:
            test_idxs = np.sort(test_idxs)

        z = dataset['z']
        R = dataset['R'][test_idxs, :, :]
        F = dataset['F'][test_idxs, :, :]

        if model['use_E']:
            E = dataset['E'][test_idxs]

        try:
            gdml_predict = GDMLPredict(
                model, max_processes=max_processes, use_torch=use_torch
            )
        except:
            print()
            log.critical(traceback.format_exc())
            sys.exit()

        b_size = min(1000, len(test_idxs))

        if not use_torch:
            if num_workers == 0 or batch_size == 0:
                ui.callback(NOT_DONE, disp_str='Optimizing parallelism')

                gps, is_from_cache = gdml_predict.prepare_parallel(
                    n_bulk=b_size, return_is_from_cache=True
                )
                num_workers, batch_size, bulk_mp = (
                    gdml_predict.num_workers,
                    gdml_predict.chunk_size,
                    gdml_predict.bulk_mp,
                )

                ui.callback(
                    DONE,
                    disp_str='Optimizing parallelism'
                    + (' (from cache)' if is_from_cache else ''),
                    sec_disp_str='%d workers %s/ chunks of %d'
                    % (num_workers, '[MP] ' if bulk_mp else '', batch_size),
                )
            else:
                gdml_predict._set_num_workers(num_workers)
                gdml_predict._set_batch_size(batch_size)
                gdml_predict._set_bulk_mp(bulk_mp)

        n_atoms = z.shape[0]

        if model['use_E']:
            e_mae_sum, e_rmse_sum = 0, 0
        f_mae_sum, f_rmse_sum = 0, 0
        cos_mae_sum, cos_rmse_sum = 0, 0
        mag_mae_sum, mag_rmse_sum = 0, 0

        n_done = 0
        t = time.time()
        for b_range in _batch(list(range(len(test_idxs))), b_size):

            n_done_step = len(b_range)
            n_done += n_done_step

            r = R[b_range].reshape(n_done_step, -1)
            e_pred, f_pred = gdml_predict.predict(r)

            # energy error
            if model['use_E']:
                e = E[b_range]
                e_mae, e_mae_sum, e_rmse, e_rmse_sum = _online_err(
                    np.squeeze(e) - e_pred, 1, n_done, e_mae_sum, e_rmse_sum
                )

            # force component error
            f = F[b_range].reshape(n_done_step, -1)
            f_mae, f_mae_sum, f_rmse, f_rmse_sum = _online_err(
                f - f_pred, 3 * n_atoms, n_done, f_mae_sum, f_rmse_sum
            )

            # magnitude error
            f_pred_mags = np.linalg.norm(f_pred.reshape(-1, 3), axis=1)
            f_mags = np.linalg.norm(f.reshape(-1, 3), axis=1)
            mag_mae, mag_mae_sum, mag_rmse, mag_rmse_sum = _online_err(
                f_pred_mags - f_mags, n_atoms, n_done, mag_mae_sum, mag_rmse_sum
            )

            # normalized cosine error
            f_pred_norm = f_pred.reshape(-1, 3) / f_pred_mags[:, None]
            f_norm = f.reshape(-1, 3) / f_mags[:, None]
            cos_err = np.arccos(np.clip(np.einsum('ij,ij->i', f_pred_norm, f_norm), -1, 1)) / np.pi
            cos_mae, cos_mae_sum, cos_rmse, cos_rmse_sum = _online_err(
                cos_err, n_atoms, n_done, cos_mae_sum, cos_rmse_sum
            )

            # NEW

            if is_test and DEBUG_WRITE:

                try:
                    with open('test_pred.xyz', 'a') as file:

                        n = r.shape[0]
                        for i, ri in enumerate(r):

                            r_out = ri.reshape(-1, 3)
                            e_out = e_pred[i]
                            f_out = f_pred[i].reshape(-1, 3)

                            ext_xyz_str = (
                                io.generate_xyz_str(r_out, model['z'], e=e_out, f=f_out)
                                + '\n'
                            )

                            file.write(ext_xyz_str)

                except IOError:
                    sys.exit("ERROR: Writing xyz file failed.")

                try:
                    with open('test_ref.xyz', 'a') as file:

                        n = r.shape[0]
                        for i, ri in enumerate(r):

                            r_out = ri.reshape(-1, 3)
                            e_out = (
                                None
                                if not model['use_E']
                                else np.squeeze(E[b_range][i])
                            )
                            f_out = f[i].reshape(-1, 3)

                            ext_xyz_str = (
                                io.generate_xyz_str(r_out, model['z'], e=e_out, f=f_out)
                                + '\n'
                            )
                            file.write(ext_xyz_str)

                except IOError:
                    sys.exit("ERROR: Writing xyz file failed.")

                try:
                    with open('test_diff.xyz', 'a') as file:

                        n = r.shape[0]
                        for i, ri in enumerate(r):

                            r_out = ri.reshape(-1, 3)
                            e_out = (
                                None
                                if not model['use_E']
                                else (np.squeeze(E[b_range][i]) - e_pred[i])
                            )
                            f_out = (f[i] - f_pred[i]).reshape(-1, 3)

                            ext_xyz_str = (
                                io.generate_xyz_str(r_out, model['z'], e=e_out, f=f_out)
                                + '\n'
                            )
                            file.write(ext_xyz_str)

                except IOError:
                    sys.exit("ERROR: Writing xyz file failed.")

            # NEW

            sps = n_done / (time.time() - t)  # examples per second
            disp_str = 'energy %.3f/%.3f, ' % (e_mae, e_rmse) if model['use_E'] else ''
            disp_str += 'forces %.3f/%.3f' % (f_mae, f_rmse)
            disp_str = (
                '{} errors (MAE/RMSE): '.format('Test' if is_test else 'Validation')
                + disp_str
            )
            sec_disp_str = '@ %.1f geo/s' % sps if b_range is not None else ''

            ui.callback(
                n_done,
                len(test_idxs),
                disp_str=disp_str,
                sec_disp_str=sec_disp_str,
                newline_when_done=False,
            )

        if is_test:
            ui.callback(
                DONE,
                disp_str='Testing on {:,} points'.format(n_test),
                sec_disp_str=sec_disp_str,
            )
        else:
            ui.callback(DONE, disp_str=disp_str, sec_disp_str=sec_disp_str)

        if model['use_E']:
            e_rmse_pct = (e_rmse / e_err['rmse'] - 1.0) * 100
        f_rmse_pct = (f_rmse / f_err['rmse'] - 1.0) * 100

        # if func_called_directly and n_models == 1:
        if is_test and n_models == 1:
            print(ui.white_bold_str('\nTest errors (MAE/RMSE)'))

            r_unit = 'unknown unit'
            e_unit = 'unknown unit'
            f_unit = 'unknown unit'
            if 'r_unit' in dataset and 'e_unit' in dataset:
                r_unit = dataset['r_unit']
                e_unit = dataset['e_unit']
                f_unit = str(dataset['e_unit']) + '/' + str(dataset['r_unit'])

            format_str = '  {:<18} {:>.4f}/{:>.4f} [{}]'
            if model['use_E']:
                ui.print_two_column_str(
                    format_str.format('Energy:', e_mae, e_rmse, e_unit),
                    'relative to expected: {:+.1f}%'.format(e_rmse_pct),
                )

            ui.print_two_column_str(
                format_str.format('Forces:', f_mae, f_rmse, f_unit),
                'relative to expected: {:+.1f}%'.format(f_rmse_pct),
            )

            print(format_str.format('  Magnitude:', mag_mae, mag_rmse, r_unit))
            ui.print_two_column_str(
                format_str.format('  Angle:', cos_mae, cos_rmse, '0-1'),
                'lower is better',
            )
            print()

        model_mutable = dict(model)
        model.close()
        model = model_mutable

        model_needs_update = (
            overwrite
            or (is_test and model['n_test'] < len(test_idxs))
            or (is_validation and not is_model_validated)
        )
        if model_needs_update:

            if is_validation and overwrite:
                model['n_test'] = 0  # flag the model as not tested

            if is_test:
                model['n_test'] = len(test_idxs)
                model['md5_test'] = dataset['md5']

            if model['use_E']:
                model['e_err'] = {
                    'mae': np.asscalar(e_mae),
                    'rmse': np.asscalar(e_rmse),
                }

            model['f_err'] = {'mae': np.asscalar(f_mae), 'rmse': np.asscalar(f_rmse)}
            np.savez_compressed(model_path, **model)

            if is_test and model['n_test'] > 0:
                log.info('Expected errors were updated in model file.')

        else:
            add_info_str = (
                'the same number of'
                if model['n_test'] == len(test_idxs)
                else 'only {:,}'.format(len(test_idxs))
            )
            log.warning(
                'This model has previously been tested on {:,} points, which is why the errors for the current test run with {} points have NOT been used to update the model file.\n'.format(
                    model['n_test'], add_info_str
                )
                + 'Run \'{} test -o {} {} {}\' to overwrite.'.format(
                    PACKAGE_NAME, os.path.relpath(model_path), dataset_path, n_test
                )
            )

        F_rmse.append(f_rmse)

    return F_rmse


def select(
    model_dir, overwrite, max_processes, model_file=None, command=None, **kwargs
):  # noqa: C901

    func_called_directly = (
        command == 'select'
    )  # has this function been called from command line or from 'all'?
    if func_called_directly:
        ui.print_step_title('MODEL SELECTION')

    any_model_not_validated = False
    any_model_is_tested = False

    model_dir, model_file_names = model_dir
    if len(model_file_names) > 1:

        use_E = True

        rows = []
        data_names = ['sig', 'MAE', 'RMSE', 'MAE', 'RMSE']
        for i, model_file_name in enumerate(model_file_names):
            model_path = os.path.join(model_dir, model_file_name)
            _, model = io.is_file_type(model_path, 'model')

            use_E = model['use_E']

            if i == 0:
                idxs_train = set(model['idxs_train'])
                md5_train = model['md5_train']
                idxs_valid = set(model['idxs_valid'])
                md5_valid = model['md5_valid']
            else:
                if (
                    md5_train != model['md5_train']
                    or md5_valid != model['md5_valid']
                    or idxs_train != set(model['idxs_train'])
                    or idxs_valid != set(model['idxs_valid'])
                ):
                    raise AssistantError(
                        '{} contains models trained or validated on different datasets.'.format(
                            model_dir
                        )
                    )

            e_err = {'mae': 0.0, 'rmse': 0.0}
            if model['use_E']:
                e_err = model['e_err'].item()
            f_err = model['f_err'].item()

            is_model_validated = not (np.isnan(f_err['mae']) or np.isnan(f_err['rmse']))
            if not is_model_validated:
                any_model_not_validated = True

            is_model_tested = model['n_test'] > 0
            if is_model_tested:
                any_model_is_tested = True

            rows.append(
                [model['sig'], e_err['mae'], e_err['rmse'], f_err['mae'], f_err['rmse']]
            )

            model.close()

        if any_model_not_validated:
            log.error(
                'One or more models in the given directory have not been validated yet.\n'
                + 'This is required before selecting the best performer.'
            )
            print()
            sys.exit()

        if any_model_is_tested:
            log.error(
                'One or more models in the given directory have already been tested. This means that their recorded expected errors are test errors, not validation errors. However, one should never perform model selection based on the test error!\n'
                + 'Please run the validation command (again) with the overwrite option \'-o\', then this selection command.'
            )
            return

        f_rmse_col = [row[4] for row in rows]
        best_idx = f_rmse_col.index(min(f_rmse_col))  # idx of row with lowest f_rmse
        best_sig = rows[best_idx][0]

        rows = sorted(rows, key=lambda col: col[0])  # sort according to sigma
        print(ui.white_bold_str('Cross-validation errors'))
        print(' ' * 7 + 'Energy' + ' ' * 6 + 'Forces')
        print((' {:>3} ' + '{:>5} ' * 4).format(*data_names))
        print(' ' + '-' * 27)
        format_str = ' {:>3} ' + '{:5.2f} ' * 4
        format_str_no_E = ' {:>3}     -     - ' + '{:5.2f} ' * 2
        for row in rows:
            if use_E:
                row_str = format_str.format(*row)
            else:
                row_str = format_str_no_E.format(*[row[0], row[3], row[4]])

            if row[0] != best_sig:
                row_str = ui.gray_str(row_str)
            print(row_str)
        print()

        sig_col = [row[0] for row in rows]
        if best_sig == min(sig_col) or best_sig == max(sig_col):
            log.warning(
                'The optimal sigma lies on the boundary of the search grid.\n'
                + 'Model performance might improve if the search grid is extended in direction sigma {} {:d}.'.format(
                    '<' if best_idx == 0 else '>', best_sig
                )
            )

    else:  # only one model available
        log.warning(
            'Skipping model selection step as there is only one model to select.'
        )

        best_idx = 0

    best_model_path = os.path.join(model_dir, model_file_names[best_idx])

    if model_file is None:

        # generate model file name based on model properties
        best_model = np.load(best_model_path, allow_pickle=True)
        model_file = io.model_file_name(best_model, is_extended=True)
        best_model.close()

    model_exists = os.path.isfile(model_file)
    if model_exists and overwrite:
        log.info('Overwriting existing model file.')

    if not model_exists or overwrite:
        if func_called_directly:
            log.done('Writing model file \'{}\''.format(model_file))

        shutil.copy(best_model_path, model_file)
        shutil.rmtree(model_dir, ignore_errors=True)
    else:
        log.warning(
            'Model \'{}\' already exists.\n'.format(model_file)
            + 'Run \'{} select -o {}\' to overwrite.'.format(
                PACKAGE_NAME, os.path.relpath(model_dir)
            )
        )

    if func_called_directly:
        _print_next_step('select', model_files=[model_file])

    return model_file


def show(file, overwrite, max_processes, command=None, **kwargs):

    ui.print_step_title('SHOW DETAILS')
    file_path, file = file

    if file['type'].astype(str) == 'd':
        _print_dataset_properties(file)

    if file['type'].astype(str) == 't':
        _print_task_properties(
            use_sym=file['use_sym'],
            use_cprsn=file['use_cprsn'],
            use_E=file['use_E'],
            use_E_cstr=file['use_E_cstr'],
        )

    if file['type'].astype(str) == 'm':
        _print_model_properties(file)


def reset(command=None, **kwargs):

    if ui.yes_or_no('\nDo you really want to purge all caches and temporary files?'):

        curr_dir = os.getcwd()
        bmark_file = '_bmark_cache.npz'
        bmark_path = os.path.join(curr_dir, bmark_file)

        if os.path.exists(bmark_path):
            try:
                os.remove(bmark_path)
            except OSError:
                print()
                log.critical('Exception: unable to delete benchmark cache.')
                sys.exit()

            log.done('Benchmark cache deleted.')
        else:
            log.info('Benchmark cache was already empty.')
    else:
        print(' Cancelled.')
    print()


def main():
    #def _add_argument_dataset(parser, qualifier_str='', help='path to dataset file'):
    #    parser.add_argument(
    #        'dataset',
    #        metavar='<'
    #        + qualifier_str
    #        + ('_' if qualifier_str != '' else '')
    #        + 'dataset_file>',
    #        type=lambda x: io.is_file_type(x, 'dataset'),
    #        help=help,
    #    )

    def _add_argument_sample_size(parser, subset_str):
        subparser.add_argument(
            'n_%s' % subset_str,
            metavar='<n_%s>' % subset_str,
            type=io.is_strict_pos_int,
            help='%s sample size' % subset_str,
        )

    def _add_argument_dir_with_file_type(parser, type, or_file=False):
        parser.add_argument(
            '%s_dir' % type,
            metavar='<%s_dir%s>' % (type, '_or_file' if or_file else ''),
            type=lambda x: io.is_dir_with_file_type(x, type, or_file=or_file),
            help='path to %s directory%s' % (type, ' or file' if or_file else ''),
        )

    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--version',
        action='version',
        version='%(prog)s '
        + __version__
        + ' [Python {}, NumPy {}, SciPy {}'.format(
            '.'.join(map(str, sys.version_info[:3])), np.__version__, sp.__version__
        )
        + ', PyTorch {}'.format(torch.__version__ if _has_torch else 'N/A')
        + ', ASE {}'.format(ase.__version__ if _has_ase else 'N/A')
        + ']',
    )

    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument(
        '-o',
        '--overwrite',
        dest='overwrite',
        action='store_true',
        help='overwrite existing files',
    )
    parent_parser.add_argument(
        '-p',
        '--max_processes',
        metavar='<max_processes>',
        type=io.is_strict_pos_int,
        help='limit the number of processes for this application',
    )
    parent_parser.add_argument(
        '--torch',
        dest='use_torch',
        action='store_true',
        help='use PyTorch to enable GPU acceleration',
    )

    subparsers = parser.add_subparsers(title='commands', dest='command')
    subparsers.required = True
    parser_all = subparsers.add_parser(
        'all', help='reconstruct force field from beginning to end', parents=[parent_parser]
    )
    parser_create = subparsers.add_parser(
        'create', help='create training task(s)', parents=[parent_parser]
    )
    parser_train = subparsers.add_parser(
        'train', help='train model(s) from task(s)', parents=[parent_parser]
    )
    parser_resume = subparsers.add_parser(
        'resume', help='resume training a model', parents=[parent_parser]
    )
    parser_valid = subparsers.add_parser(
        'validate', help='validate model(s)', parents=[parent_parser]
    )
    parser_select = subparsers.add_parser(
        'select', help='select best performing model', parents=[parent_parser]
    )
    parser_test = subparsers.add_parser(
        'test', help='test a model', parents=[parent_parser]
    )
    parser_show = subparsers.add_parser(
        'show',
        help='print details about dataset, task or model file',
        parents=[parent_parser],
    )
    subparsers.add_parser(
        'reset', help='delete all caches and temporary files', parents=[parent_parser]
    )

    for subparser in [parser_all, parser_create]:

        subparser.add_argument(
            'dataset',
            metavar='<dataset_file>',
            type=lambda x: io.is_file_type(x, 'dataset'),
            help='path to dataset file (train/validation/test subsets are sampled from here if no seperate dataset are specified)',
        )

        _add_argument_sample_size(subparser, 'train')
        _add_argument_sample_size(subparser, 'valid')
        subparser.add_argument(
            '-v',
            '--validation_dataset',
            metavar='<valid_dataset_file>',
            dest='valid_dataset',
            type=lambda x: io.is_file_type(x, 'dataset'),
            help='path to validation dataset file',
        )
        subparser.add_argument(
            '-t',
            '--test_dataset',
            metavar='<test_dataset_file>',
            dest='test_dataset',
            type=lambda x: io.is_file_type(x, 'dataset'),
            help='path to test dataset file',
        )
        subparser.add_argument(
            '-s',
            '--sig',
            metavar=('<s1>', '<s2>'),
            dest='sigs',
            type=io.parse_list_or_range,
            help='integer list and/or range <start>:[<step>:]<stop> for the kernel hyper-parameter sigma',
            nargs='+',
        )
        subparser.add_argument(
            '--task_dir',
            metavar='<task_dir>',
            dest='task_dir',
            help='user-defined task output dir name',
        )

        group = subparser.add_mutually_exclusive_group()
        group.add_argument(
            '--gdml',
            action='store_true',
            help='don\'t include symmetries in the model (GDML)',
        )
        group.add_argument(
            '--cprsn',
            dest='use_cprsn',
            action='store_true',
            help='compress kernel matrix along symmetric degrees of freedom',
        )

        group = subparser.add_mutually_exclusive_group()
        group.add_argument(
            '--no_E',
            dest='use_E',
            action='store_false',
            help='only reconstruct force field w/o potential energy surface',
        )
        group.add_argument(
            '--E_cstr',
            dest='use_E_cstr',
            action='store_true',
            help='include the energy constraints in the kernel',
        )

    for subparser in [parser_valid, parser_test]:
        _add_argument_dir_with_file_type(subparser, 'model', or_file=True)

    parser_valid.add_argument(
        'valid_dataset',
        metavar='<valid_dataset_file>',
        type=lambda x: io.is_file_type(x, 'dataset'),
        help='path to validation dataset file',
    )
    parser_test.add_argument(
        'test_dataset',
        metavar='<test_dataset_file>',
        type=lambda x: io.is_file_type(x, 'dataset'),
        help='path to test dataset file',
    )

    for subparser in [parser_all, parser_test]:
        subparser.add_argument(
            'n_test',
            metavar='<n_test>',
            type=io.is_strict_pos_int,
            help='test sample size',
            nargs='?',
            default=None,
        )

    for subparser in [parser_all, parser_select]:
        subparser.add_argument(
            '--model_file',
            metavar='<model_file>',
            dest='model_file',
            help='user-defined model output file name',
        )

    
    # train
    _add_argument_dir_with_file_type(parser_train, 'task', or_file=True)

    # resume
    parser_resume.add_argument(
        'model',
        metavar='<model_file>',
        type=lambda x: io.is_file_type(x, 'model'),
        help='path to model file to complete training for',
    )
    parser_resume.add_argument(
        'dataset',
        metavar='<train_dataset_file>',
        type=lambda x: io.is_file_type(x, 'dataset'),
        help='path to original training dataset file',
    )

    for subparser in [parser_train, parser_resume]:
        subparser.add_argument(
            'valid_dataset',
            metavar='<valid_dataset_file>',
            type=lambda x: io.is_file_type(x, 'dataset'),
            help='path to validation dataset file',
        )

    # select
    _add_argument_dir_with_file_type(parser_select, 'model')

    # show
    parser_show.add_argument(
        'file',
        metavar='<file>',
        type=lambda x: io.is_valid_file_type(x),
        help='path to dataset, task or model file',
    )

    args = parser.parse_args()

    # post-processing for optional sig argument
    if 'sigs' in args and args.sigs is not None:
        args.sigs = np.hstack(
            args.sigs
        ).tolist()  # flatten list, if (part of it) was generated using the range syntax
        args.sigs = sorted(list(set(args.sigs)))  # remove potential duplicates

    # post-processing for optional model output file argument
    if 'model_file' in args and args.model_file is not None:
        if not args.model_file.endswith('.npz'):
            args.model_file += '.npz'

    _print_splash(args.max_processes, args.use_torch)

    # Check PyTorch GPU support.
    if 'use_torch' in args and args.use_torch:
        if _has_torch:
            if not torch.cuda.is_available():
                print()  # TODO: print only if log level includes warning
                log.warning(
                    'Your PyTorch installation does not see any GPU(s) on your system and will thus run all calculations on the CPU! Unless this is what you want, we recommend running CPU calculations without \'--torch\' for improved performance.'
                )
        else:
            print()
            log.critical(
                'Optional PyTorch dependency not found! Please run \'pip install sgdml[torch]\' to install it or disable the PyTorch option.'
            )
            sys.exit()

    # Replace solver flags with keyword.
    args = vars(args)
    args['solver'] = 'analytic'
    if 'use_cg' in args and args['use_cg']:
        args['solver'] = 'cg'
    args.pop('use_cg', None)


    # TODO: remove dummy variables once iterative solver ships
    missing_keys = ['n_inducing_pts_init', 'interact_cut_off', 'use_cg']
    for missing_key in missing_keys:
        if missing_key not in args: 
            args[missing_key] = None

    try:
        getattr(sys.modules[__name__], args['command'])(**args)
    except AssistantError as err:
        log.error(str(err))
        sys.exit('')
    except:
        log.critical(traceback.format_exc())
        sys.exit()
    print()


if __name__ == "__main__":
    main()
