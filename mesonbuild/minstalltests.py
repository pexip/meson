# SPDX-License-Identifier: Apache-2.0
# Copyright The Meson development team

from __future__ import annotations

import argparse
import copy
import os
import pickle
import re
import shutil
import sys
import typing as T

from . import build
from .backend.backends import TestSerialisation
from .mesonlib import RealPathAction, setup_vsenv
from .options import OptionKey

if T.TYPE_CHECKING:
    pass


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('-C', dest='wd', action=RealPathAction,
                        help='directory to cd into before running')
    parser.add_argument('--destdir', default=None,
                        help='Sets or overrides DESTDIR environment. (Since 0.57.0)')
    parser.add_argument('--tests-prefix', default=None, dest='tests_prefix',
                        help='Directory under prefix to install tests into (default: tests)')
    parser.add_argument('--no-rebuild', default=False, action='store_true',
                        help='Do not rebuild before installing tests.')
    parser.add_argument('-q', '--quiet', default=False, action='store_true',
                        help='Do not print every file that was installed.')


def run(options: argparse.Namespace) -> int:
    from . import tooldetect

    if options.wd:
        os.chdir(options.wd)
    else:
        options.wd = os.getcwd()

    b = build.load(options.wd)
    need_vsenv = T.cast('bool', b.environment.coredata.optstore.get_value_for(OptionKey('vsenv')))
    setup_vsenv(need_vsenv)

    if not options.no_rebuild:
        backend = T.cast('str', b.environment.coredata.optstore.get_value_for(OptionKey('backend')))
        if backend == 'ninja':
            ninja = tooldetect.detect_ninja()
            if ninja:
                ret = __import__('subprocess').run(ninja + ['-C', options.wd]).returncode
                if ret != 0:
                    print(f'Could not rebuild {options.wd}')
                    return 1
            else:
                print("Can't find ninja, can't rebuild before installing tests.")
                # Exit code 127 matches shell 'command not found', consistent
                # with meson test (mtest.py) when ninja is missing.
                return 127

    # Load the test data from the build directory
    test_data_file = os.path.join(options.wd, 'meson-private', 'meson_test_setup.dat')
    if not os.path.isfile(test_data_file):
        print(f'Test data not found at {test_data_file}. Is this a Meson build directory?')
        return 1

    with open(test_data_file, 'rb') as f:
        tests: T.List[TestSerialisation] = pickle.load(f)

    if not tests:
        print('No tests found.')
        return 0

    # Determine the install prefix
    prefix = T.cast('str', b.environment.coredata.optstore.get_value_for(OptionKey('prefix')))
    tests_subdir = options.tests_prefix or 'tests'

    # Determine destdir
    destdir = options.destdir
    if destdir is None:
        destdir = os.environ.get('DESTDIR', '')
    if destdir and not os.path.isabs(destdir):
        destdir = os.path.join(options.wd, destdir)

    # The install root for tests: DESTDIR + prefix + tests_subdir
    install_root = os.path.join(destdir, prefix.lstrip(os.sep), tests_subdir) if destdir else os.path.join(prefix, tests_subdir)

    build_dir = b.environment.get_build_dir()
    source_dir = b.environment.get_source_dir()

    if not options.quiet:
        print(f'Installing tests to {install_root}')

    os.makedirs(install_root, exist_ok=True)

    # Create meson-private dir in the install location
    installed_private_dir = os.path.join(install_root, 'meson-private')
    os.makedirs(installed_private_dir, exist_ok=True)

    # Create meson-logs dir in the install location (for test log output)
    installed_logs_dir = os.path.join(install_root, 'meson-logs')
    os.makedirs(installed_logs_dir, exist_ok=True)

    # Rewrite test data with installed paths
    installed_tests: T.List[TestSerialisation] = []
    copied_files: T.Set[str] = set()

    for test in tests:
        new_test = copy.deepcopy(test)

        # Install test executable(s)
        new_fname: T.List[str] = []
        for fname in test.fname:
            if _is_under_dir(fname, build_dir):
                # This is a built executable - copy it to install location
                rel_path = os.path.relpath(fname, build_dir)
                dest_path = os.path.join(install_root, rel_path)
                _copy_file(fname, dest_path, copied_files, options.quiet)
                new_fname.append(dest_path)
            elif _is_under_dir(fname, source_dir):
                # Source file (e.g., a test script from source tree)
                rel_path = os.path.relpath(fname, source_dir)
                dest_path = os.path.join(install_root, rel_path)
                _copy_file(fname, dest_path, copied_files, options.quiet)
                new_fname.append(dest_path)
            else:
                # External program (e.g., /usr/bin/python3) - keep as-is
                new_fname.append(fname)
        new_test.fname = new_fname

        # Install files referenced in cmd_args
        new_cmd_args: T.List[str] = []
        for arg in test.cmd_args:
            if os.path.isabs(arg) and _is_under_dir(arg, build_dir):
                rel_path = os.path.relpath(arg, build_dir)
                dest_path = os.path.join(install_root, rel_path)
                _copy_file(arg, dest_path, copied_files, options.quiet)
                new_cmd_args.append(dest_path)
            elif os.path.isabs(arg) and _is_under_dir(arg, source_dir):
                rel_path = os.path.relpath(arg, source_dir)
                dest_path = os.path.join(install_root, rel_path)
                _copy_file(arg, dest_path, copied_files, options.quiet)
                new_cmd_args.append(dest_path)
            elif not os.path.isabs(arg):
                # Relative path - may reference a built file (e.g. custom target output)
                # Check if it exists relative to build dir and copy it
                abs_in_build = os.path.join(build_dir, arg)
                if os.path.isfile(abs_in_build):
                    dest_path = os.path.join(install_root, arg)
                    _copy_file(abs_in_build, dest_path, copied_files, options.quiet)
                # Keep the relative path as-is (it will be relative to the
                # install root where meson test runs from)
                new_cmd_args.append(arg)
            else:
                new_cmd_args.append(arg)
        new_test.cmd_args = new_cmd_args

        # Rewrite workdir if it points into the build or source dir
        if test.workdir:
            if _is_under_dir(test.workdir, build_dir):
                rel_path = os.path.relpath(test.workdir, build_dir)
                new_test.workdir = os.path.join(install_root, rel_path)
            elif _is_under_dir(test.workdir, source_dir):
                rel_path = os.path.relpath(test.workdir, source_dir)
                new_test.workdir = os.path.join(install_root, rel_path)

        # Rewrite extra_paths (shared library paths)
        new_extra_paths: T.List[str] = []
        for p in test.extra_paths:
            if _is_under_dir(p, build_dir):
                rel_path = os.path.relpath(p, build_dir)
                dest_path = os.path.join(install_root, rel_path)
                # Copy all shared libraries from this directory
                if os.path.isdir(p):
                    _copy_directory_contents(p, dest_path, copied_files, options.quiet)
                new_extra_paths.append(dest_path)
            else:
                new_extra_paths.append(p)
        new_test.extra_paths = new_extra_paths

        # Rewrite LD_LIBRARY_PATH / DYLD_LIBRARY_PATH entries in env, and
        # copy shared libraries from those directories
        new_env = copy.deepcopy(test.env)
        _rewrite_env_paths(new_env, build_dir, source_dir, install_root,
                           copied_files, options.quiet)
        new_test.env = new_env

        installed_tests.append(new_test)

    # Write the rewritten test data
    installed_test_data = os.path.join(installed_private_dir, 'meson_test_setup.dat')
    with open(installed_test_data, 'wb') as f:
        pickle.dump(installed_tests, f)

    # Copy build.dat and coredata.dat (needed by `meson test`)
    for dat_file in ('build.dat', 'coredata.dat'):
        src = os.path.join(options.wd, 'meson-private', dat_file)
        dst = os.path.join(installed_private_dir, dat_file)
        if os.path.isfile(src):
            shutil.copy2(src, dst)
            if not options.quiet:
                print(f'Installing {dat_file} to {dst}')

    # Write a marker file so that `meson test -C <dir>` can detect this is
    # an installed-tests directory and automatically skip the rebuild step.
    marker_path = os.path.join(installed_private_dir, 'installed-tests.marker')
    with open(marker_path, 'w', encoding='utf-8') as f:
        f.write('This directory contains installed Meson tests.\n')

    if not options.quiet:
        print(f'\nInstalled {len(installed_tests)} tests to {install_root}')
        print(f'Run tests with: meson test -C {install_root}')

    return 0


def _is_under_dir(path: str, directory: str) -> bool:
    """Check if path is under the given directory.

    Only works with absolute paths - relative paths are never considered
    to be under any directory."""
    if not os.path.isabs(path):
        return False
    try:
        path = os.path.normpath(os.path.realpath(path))
        directory = os.path.normpath(os.path.realpath(directory))
        return path.startswith(directory + os.sep) or path == directory
    except (ValueError, OSError):
        return False


def _copy_file(src: str, dst: str, copied: T.Set[str], quiet: bool) -> None:
    """Copy a file, creating parent directories as needed."""
    if dst in copied:
        return
    if not os.path.isfile(src):
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    # Preserve executable permissions
    src_mode = os.stat(src).st_mode
    os.chmod(dst, src_mode)
    copied.add(dst)
    if not quiet:
        print(f'Installing {os.path.basename(src)} to {os.path.dirname(dst)}')


def _copy_directory_contents(src_dir: str, dst_dir: str, copied: T.Set[str], quiet: bool) -> None:
    """Copy all files from src_dir to dst_dir."""
    if not os.path.isdir(src_dir):
        return
    os.makedirs(dst_dir, exist_ok=True)
    for entry in os.listdir(src_dir):
        src_path = os.path.join(src_dir, entry)
        dst_path = os.path.join(dst_dir, entry)
        if os.path.isfile(src_path):
            _copy_file(src_path, dst_path, copied, quiet)
        elif os.path.isdir(src_path):
            _copy_directory_contents(src_path, dst_path, copied, quiet)


def _rewrite_env_paths(env: 'build.EnvironmentVariables', build_dir: str, source_dir: str,
                       install_root: str, copied_files: T.Set[str], quiet: bool) -> None:
    """Rewrite paths in environment variables that point to build/source dirs.

    EnvironmentVariables stores operations as a list of tuples:
        (method, name, values, separator)
    where values is a list of strings. We rewrite any paths in those values.
    For library path variables, we also copy the library files.
    """
    lib_path_vars = {'LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH'}
    new_envvars = []
    for method, name, values, separator in env.envvars:
        new_values = []
        for val in values:
            if isinstance(val, str):
                if _is_under_dir(val, build_dir):
                    rel = os.path.relpath(val, build_dir)
                    dest = os.path.join(install_root, rel)
                    # Copy shared libraries if this is a library path variable
                    if name in lib_path_vars and os.path.isdir(val):
                        _copy_shared_libraries(val, dest, copied_files, quiet)
                    val = dest
                elif _is_under_dir(val, source_dir):
                    rel = os.path.relpath(val, source_dir)
                    dest = os.path.join(install_root, rel)
                    if name in lib_path_vars and os.path.isdir(val):
                        _copy_shared_libraries(val, dest, copied_files, quiet)
                    val = dest
            new_values.append(val)
        new_envvars.append((method, name, new_values, separator))
    env.envvars = new_envvars


def _copy_shared_libraries(src_dir: str, dst_dir: str, copied: T.Set[str], quiet: bool) -> None:
    """Copy shared library files from src_dir to dst_dir."""
    if not os.path.isdir(src_dir):
        return
    os.makedirs(dst_dir, exist_ok=True)
    for entry in os.listdir(src_dir):
        src_path = os.path.join(src_dir, entry)
        if os.path.isfile(src_path) and _is_shared_library(entry):
            dst_path = os.path.join(dst_dir, entry)
            _copy_file(src_path, dst_path, copied, quiet)
        elif os.path.islink(src_path) and _is_shared_library(entry):
            # Preserve symlinks for versioned shared libraries (e.g., libfoo.so -> libfoo.so.1)
            dst_path = os.path.join(dst_dir, entry)
            if dst_path not in copied:
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                if os.path.exists(dst_path) or os.path.islink(dst_path):
                    os.unlink(dst_path)
                link_target = os.readlink(src_path)
                os.symlink(link_target, dst_path)
                copied.add(dst_path)
                if not quiet:
                    print(f'Installing symlink {entry} to {dst_dir}')


def _is_shared_library(filename: str) -> bool:
    """Check if a filename looks like a shared library."""
    # Match .so, .so.X, .so.X.Y, .so.X.Y.Z, .dylib, .dll
    if re.search(r'\.so(\.[0-9]+)*$', filename):
        return True
    if filename.endswith('.dylib') or filename.endswith('.dll'):
        return True
    return False
