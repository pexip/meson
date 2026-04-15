# SPDX-License-Identifier: Apache-2.0
# Copyright The Meson development team

from __future__ import annotations

import argparse
import copy
import os
import pickle
import re
import shutil
import typing as T

from . import build
from .backend.backends import TestSerialisation
from .mesonlib import RealPathAction, setup_vsenv
from .options import OptionKey
from .scripts import destdir_join

if T.TYPE_CHECKING:
    pass

# Placeholder tokens embedded in the installed-tests pickle so that the
# test directory can be moved to a different location and still work.
# ``@@INSTALLEDTESTSDIR@@`` is replaced at run-time with the directory
# passed via ``meson test -C <dir>``.
# ``@@INSTALLPREFIX@@`` is replaced at run-time with the sibling install
# prefix (i.e. <dir>/../../<prefix-tail> derived from the tests_subdir).
INSTALLED_TESTS_DIR_PLACEHOLDER = '@@INSTALLEDTESTSDIR@@'
INSTALL_PREFIX_PLACEHOLDER = '@@INSTALLPREFIX@@'


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


def _build_installed_files_map(
    wd: str,
) -> T.Dict[str, str]:
    """Build a mapping from normalized build-dir file paths to their
    installed locations **relative to the install prefix**.

    For example, a target installed to ``{prefix}/lib/libfoo.so`` will
    map to ``lib/libfoo.so``.  The caller can then prepend the
    ``@@INSTALLPREFIX@@`` placeholder so that the path is resolved at
    test-run time.
    """
    install_dat = os.path.join(wd, 'meson-private', 'install.dat')
    if not os.path.isfile(install_dat):
        return {}

    from .minstall import load_install_data
    d = load_install_data(install_dat)

    mapping: T.Dict[str, str] = {}
    for t in d.targets:
        src = os.path.normpath(os.path.realpath(t.fname))
        # outdir is relative to prefix (e.g. "lib" or "bin")
        rel_installed = os.path.join(t.outdir, os.path.basename(t.fname))
        mapping[src] = rel_installed
    return mapping


def _resolve_lib_dir_installed_relpath(
    lib_dir: str, installed_files: T.Dict[str, str]
) -> T.Optional[str]:
    """If every shared library in *lib_dir* is already covered by the
    normal install (present in *installed_files*) AND they all go to the
    same install directory (relative to prefix), return that directory.
    Otherwise return ``None``, meaning some libraries need to be copied
    into the test install tree.
    """
    if not os.path.isdir(lib_dir):
        return None

    install_dirs: T.Set[str] = set()
    has_libs = False

    for entry in os.listdir(lib_dir):
        src_path = os.path.join(lib_dir, entry)
        if (os.path.isfile(src_path) or os.path.islink(src_path)) and _is_shared_library(entry):
            has_libs = True
            norm = os.path.normpath(os.path.realpath(src_path))
            rel_installed = installed_files.get(norm)
            if rel_installed is None:
                # This lib is NOT installed by `meson install` → must copy
                return None
            install_dirs.add(os.path.dirname(rel_installed))

    if not has_libs:
        return None

    if len(install_dirs) == 1:
        return install_dirs.pop()

    # Libs go to different directories – caller should copy them
    return None


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
    if os.path.isabs(tests_subdir) or '..' in os.path.normpath(tests_subdir).split(os.sep):
        print(f'Invalid tests install subdir {tests_subdir!r}: must be a relative path under the install prefix and must not contain "..".')
        return 1

    # Determine destdir
    destdir = options.destdir
    if destdir is None:
        destdir = os.environ.get('DESTDIR', '')
    if destdir and not os.path.isabs(destdir):
        destdir = os.path.join(options.wd, destdir)

    # The install root for tests: DESTDIR + prefix + tests_subdir
    if destdir:
        install_root = os.path.join(destdir_join(destdir, prefix), tests_subdir)
    else:
        install_root = os.path.join(prefix, tests_subdir)

    build_dir = os.path.normpath(os.path.realpath(b.environment.get_build_dir()))
    source_dir = os.path.normpath(os.path.realpath(b.environment.get_source_dir()))

    # Build a mapping of files already installed by `meson install` so that
    # we can reference them in-place and avoid duplicating shared libraries
    # into the test install directory.  Values are paths *relative to the
    # install prefix* (e.g. ``lib/libfoo.so``).
    installed_files = _build_installed_files_map(options.wd)

    # Helper: produce a relocatable placeholder path for a file that was
    # copied into the test install tree.
    def _tests_placeholder(rel: str) -> str:
        return os.path.join(INSTALLED_TESTS_DIR_PLACEHOLDER, rel)

    # Helper: produce a relocatable placeholder path for a file that is
    # already installed by `meson install` under the main prefix.
    def _prefix_placeholder(rel: str) -> str:
        return os.path.join(INSTALL_PREFIX_PLACEHOLDER, rel)

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
            norm_fname = os.path.normpath(os.path.realpath(fname)) if os.path.isabs(fname) else fname
            if _is_under_dir(fname, build_dir):
                # Check if this file is already installed by meson install
                rel_installed = installed_files.get(norm_fname)
                if rel_installed:
                    # Reference the main install prefix (relocatable)
                    new_fname.append(_prefix_placeholder(rel_installed))
                else:
                    # This is a test-only executable - copy it to install location
                    rel_path = os.path.relpath(norm_fname, build_dir)
                    dest_path = os.path.join(install_root, rel_path)
                    _copy_file(fname, dest_path, copied_files, options.quiet)
                    new_fname.append(_tests_placeholder(rel_path))
            elif _is_under_dir(fname, source_dir):
                # Source file (e.g., a test script from source tree)
                rel_path = os.path.relpath(norm_fname, source_dir)
                dest_path = os.path.join(install_root, rel_path)
                _copy_file(fname, dest_path, copied_files, options.quiet)
                # Copy sibling .py files so implicit imports work
                _copy_sibling_python_files(norm_fname, dest_path, copied_files, options.quiet)
                new_fname.append(_tests_placeholder(rel_path))
            else:
                # External program (e.g., /usr/bin/python3) - keep as-is
                new_fname.append(fname)
        new_test.fname = new_fname

        # Install files referenced in cmd_args
        new_cmd_args: T.List[str] = []
        for arg in test.cmd_args:
            norm_arg = os.path.normpath(os.path.realpath(arg)) if os.path.isabs(arg) else arg
            if os.path.isabs(arg) and _is_under_dir(arg, build_dir):
                rel_installed = installed_files.get(norm_arg)
                if rel_installed:
                    new_cmd_args.append(_prefix_placeholder(rel_installed))
                else:
                    rel_path = os.path.relpath(norm_arg, build_dir)
                    dest_path = os.path.join(install_root, rel_path)
                    _copy_file(arg, dest_path, copied_files, options.quiet)
                    new_cmd_args.append(_tests_placeholder(rel_path))
            elif os.path.isabs(arg) and _is_under_dir(arg, source_dir):
                rel_path = os.path.relpath(norm_arg, source_dir)
                dest_path = os.path.join(install_root, rel_path)
                _copy_file(arg, dest_path, copied_files, options.quiet)
                # Copy sibling .py files so implicit imports work
                _copy_sibling_python_files(norm_arg, dest_path, copied_files, options.quiet)
                new_cmd_args.append(_tests_placeholder(rel_path))
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
            norm_workdir = os.path.normpath(os.path.realpath(test.workdir))
            if _is_under_dir(test.workdir, build_dir):
                rel_path = os.path.relpath(norm_workdir, build_dir)
                new_test.workdir = _tests_placeholder(rel_path)
            elif _is_under_dir(test.workdir, source_dir):
                rel_path = os.path.relpath(norm_workdir, source_dir)
                new_test.workdir = _tests_placeholder(rel_path)

        # Rewrite extra_paths (shared library paths).
        # If every library in a directory is already installed by
        # `meson install`, point to the installed location instead of
        # copying.  Otherwise fall back to copying into the test tree.
        new_extra_paths: T.List[str] = []
        for p in test.extra_paths:
            if _is_under_dir(p, build_dir):
                redirect = _resolve_lib_dir_installed_relpath(p, installed_files)
                if redirect is not None:
                    new_extra_paths.append(_prefix_placeholder(redirect))
                else:
                    norm_p = os.path.normpath(os.path.realpath(p))
                    rel_path = os.path.relpath(norm_p, build_dir)
                    dest_path = os.path.join(install_root, rel_path)
                    if os.path.isdir(p):
                        _copy_directory_contents(p, dest_path, copied_files,
                                                 options.quiet, installed_files)
                    new_extra_paths.append(_tests_placeholder(rel_path))
            else:
                new_extra_paths.append(p)
        new_test.extra_paths = new_extra_paths

        # Rewrite LD_LIBRARY_PATH / DYLD_LIBRARY_PATH entries in env, and
        # copy shared libraries from those directories (only test-only ones)
        new_env = copy.deepcopy(test.env)
        _rewrite_env_paths(new_env, build_dir, source_dir, install_root,
                           copied_files, options.quiet, installed_files)
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
    # The marker also records the tests_subdir so mtest.py can compute the
    # sibling install-prefix directory for @@INSTALLPREFIX@@ resolution.
    marker_path = os.path.join(installed_private_dir, 'installed-tests.marker')
    with open(marker_path, 'w', encoding='utf-8') as f:
        f.write(f'tests_subdir={tests_subdir}\n')

    if not options.quiet:
        print(f'\nInstalled {len(installed_tests)} tests to {install_root}')
        print(f'Run tests with: meson test -C {install_root}')

    return 0


def resolve_installed_test_placeholders(
    tests: T.List[TestSerialisation],
    tests_dir: str,
    marker_path: str,
) -> None:
    """Resolve ``@@INSTALLEDTESTSDIR@@`` and ``@@INSTALLPREFIX@@``
    placeholders in *tests* **in-place**.

    *tests_dir* is the directory passed via ``meson test -C``.
    *marker_path* is the path to the ``installed-tests.marker`` file
    (must exist).

    The install prefix is derived from the marker contents::

        tests_subdir=tests          →  prefix = tests_dir/..
        tests_subdir=my/custom/dir  →  prefix = tests_dir/../../..
    """
    tests_dir = os.path.normpath(os.path.realpath(tests_dir))

    # Read tests_subdir from the marker file to compute the prefix
    tests_subdir = 'tests'
    with open(marker_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line.startswith('tests_subdir='):
                tests_subdir = line.split('=', 1)[1]
                break

    # prefix_dir = tests_dir stripped of tests_subdir at the end
    # e.g. /install/usr/tests → /install/usr  (if tests_subdir == "tests")
    from pathlib import PurePath
    prefix_dir = tests_dir
    for _ in PurePath(tests_subdir).parts:
        prefix_dir = os.path.dirname(prefix_dir)

    def _resolve(s: str) -> str:
        if s.startswith(INSTALLED_TESTS_DIR_PLACEHOLDER):
            rest = s[len(INSTALLED_TESTS_DIR_PLACEHOLDER):]
            if rest and rest[0] in ('/', '\\'):
                rest = rest[1:]
            return os.path.join(tests_dir, rest) if rest else tests_dir
        if s.startswith(INSTALL_PREFIX_PLACEHOLDER):
            rest = s[len(INSTALL_PREFIX_PLACEHOLDER):]
            if rest and rest[0] in ('/', '\\'):
                rest = rest[1:]
            return os.path.join(prefix_dir, rest) if rest else prefix_dir
        return s

    for test in tests:
        test.fname = [_resolve(f) for f in test.fname]
        test.cmd_args = [_resolve(a) for a in test.cmd_args]
        test.extra_paths = [_resolve(p) for p in test.extra_paths]
        if test.workdir:
            test.workdir = _resolve(test.workdir)
        # Resolve env var values
        new_envvars = []
        for method, name, values, separator in test.env.envvars:
            new_values = [_resolve(v) if isinstance(v, str) else v
                          for v in values]
            new_envvars.append((method, name, new_values, separator))
        test.env.envvars = new_envvars


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
    dst_dir = os.path.dirname(dst)
    if dst_dir:
        os.makedirs(dst_dir, exist_ok=True)
    shutil.copy2(src, dst)  # Preserves permissions and metadata
    copied.add(dst)
    if not quiet:
        print(f'Installing {os.path.basename(src)} to {os.path.dirname(dst)}')


def _copy_sibling_python_files(src_file: str, dst_file: str, copied: T.Set[str], quiet: bool) -> None:
    """When copying a Python source file, also copy sibling ``.py`` files.

    Python scripts frequently ``import`` modules from the same directory.
    These sibling files are implicit dependencies that Meson does not track,
    so we copy every ``.py`` file that lives next to *src_file* into the
    same destination directory as *dst_file*.
    """
    if not src_file.endswith('.py'):
        return
    src_dir = os.path.dirname(src_file)
    dst_dir = os.path.dirname(dst_file)
    if not src_dir or not os.path.isdir(src_dir):
        return
    for entry in os.listdir(src_dir):
        if entry.endswith('.py'):
            sib_src = os.path.join(src_dir, entry)
            sib_dst = os.path.join(dst_dir, entry)
            if os.path.isfile(sib_src):
                _copy_file(sib_src, sib_dst, copied, quiet)


def _copy_directory_contents(src_dir: str, dst_dir: str, copied: T.Set[str], quiet: bool,
                             installed_files: T.Optional[T.Dict[str, str]] = None) -> None:
    """Copy files from src_dir to dst_dir, skipping files that are already
    installed by ``meson install`` (present in *installed_files*)."""
    if not os.path.isdir(src_dir):
        return
    os.makedirs(dst_dir, exist_ok=True)
    for entry in os.listdir(src_dir):
        src_path = os.path.join(src_dir, entry)
        dst_path = os.path.join(dst_dir, entry)
        if os.path.isfile(src_path):
            # Skip files already installed by meson install
            if installed_files is not None:
                norm = os.path.normpath(os.path.realpath(src_path))
                if norm in installed_files:
                    continue
            _copy_file(src_path, dst_path, copied, quiet)
        elif os.path.isdir(src_path):
            _copy_directory_contents(src_path, dst_path, copied, quiet, installed_files)


def _rewrite_env_paths(env: 'build.EnvironmentVariables', build_dir: str, source_dir: str,
                       install_root: str, copied_files: T.Set[str], quiet: bool,
                       installed_files: T.Optional[T.Dict[str, str]] = None) -> None:
    """Rewrite paths in environment variables that point to build/source dirs.

    EnvironmentVariables stores operations as a list of tuples:
        (method, name, values, separator)
    where values is a list of strings. We rewrite any paths in those values.
    For library path variables, if the libraries are already installed by
    ``meson install``, point to the installed location via a relocatable
    placeholder instead of copying.  Otherwise, copy only test-only libraries.
    """
    # On Windows, DLLs are found via PATH (not LD_LIBRARY_PATH).
    lib_path_vars = {'LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH', 'PATH'}
    new_envvars = []
    for method, name, values, separator in env.envvars:
        new_values = []
        for val in values:
            if isinstance(val, str):
                if _is_under_dir(val, build_dir):
                    # For library path variables, check if the libs are
                    # already installed and redirect there
                    if name in lib_path_vars and installed_files is not None:
                        redirect = _resolve_lib_dir_installed_relpath(val, installed_files)
                        if redirect is not None:
                            val = os.path.join(INSTALL_PREFIX_PLACEHOLDER, redirect)
                            new_values.append(val)
                            continue

                    norm_val = os.path.normpath(os.path.realpath(val))
                    rel = os.path.relpath(norm_val, build_dir)
                    dest = os.path.join(install_root, rel)
                    # Copy shared libraries if this is a library path variable
                    if name in lib_path_vars and os.path.isdir(val):
                        _copy_shared_libraries(val, dest, copied_files, quiet,
                                               installed_files)
                    val = os.path.join(INSTALLED_TESTS_DIR_PLACEHOLDER, rel)
                elif _is_under_dir(val, source_dir):
                    norm_val = os.path.normpath(os.path.realpath(val))
                    rel = os.path.relpath(norm_val, source_dir)
                    dest = os.path.join(install_root, rel)
                    if name in lib_path_vars and os.path.isdir(val):
                        _copy_shared_libraries(val, dest, copied_files, quiet,
                                               installed_files)
                    val = os.path.join(INSTALLED_TESTS_DIR_PLACEHOLDER, rel)
            new_values.append(val)
        new_envvars.append((method, name, new_values, separator))
    env.envvars = new_envvars


def _copy_shared_libraries(src_dir: str, dst_dir: str, copied: T.Set[str], quiet: bool,
                           installed_files: T.Optional[T.Dict[str, str]] = None) -> None:
    """Copy shared library files from src_dir to dst_dir, skipping
    libraries that are already installed by ``meson install``."""
    if not os.path.isdir(src_dir):
        return
    os.makedirs(dst_dir, exist_ok=True)
    for entry in os.listdir(src_dir):
        src_path = os.path.join(src_dir, entry)
        # Skip files already installed by meson install
        if installed_files is not None:
            norm = os.path.normpath(os.path.realpath(src_path))
            if norm in installed_files:
                continue
        if os.path.islink(src_path) and _is_shared_library(entry):
            # Preserve symlinks for versioned shared libraries (e.g., libfoo.so -> libfoo.so.1)
            dst_path = os.path.join(dst_dir, entry)
            if dst_path not in copied:
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                if os.path.exists(dst_path) or os.path.islink(dst_path):
                    os.unlink(dst_path)
                try:
                    link_target = os.readlink(src_path)
                    os.symlink(link_target, dst_path)
                except (NotImplementedError, OSError):
                    # Symlinks may not be available on Windows without
                    # developer mode — fall back to a regular file copy.
                    shutil.copy2(src_path, dst_path)
                copied.add(dst_path)
                if not quiet:
                    print(f'Installing symlink {entry} to {dst_dir}')
        elif os.path.isfile(src_path) and _is_shared_library(entry):
            dst_path = os.path.join(dst_dir, entry)
            _copy_file(src_path, dst_path, copied, quiet)


def _is_shared_library(filename: str) -> bool:
    """Check if a filename looks like a shared library."""
    # Match .so, .so.X, .so.X.Y, .so.X.Y.Z, .dylib, .dll, .dll.a (import lib)
    if re.search(r'\.so(\.[0-9]+)*$', filename):
        return True
    if filename.endswith('.dylib') or filename.endswith('.dll') or filename.endswith('.dll.a'):
        return True
    return False
