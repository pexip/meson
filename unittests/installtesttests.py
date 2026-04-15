# SPDX-License-Identifier: Apache-2.0
# Copyright The Meson development team

import os
import pickle
import shutil
import stat
import tempfile
import typing as T

from unittest import SkipTest

from mesonbuild.minstalltests import (
    _is_under_dir,
    _is_shared_library,
    _copy_file,
    _copy_directory_contents,
    _rewrite_env_paths,
    INSTALLED_TESTS_DIR_PLACEHOLDER,
    INSTALL_PREFIX_PLACEHOLDER,
)
from mesonbuild.mesonlib import is_windows
from mesonbuild.utils.core import EnvironmentVariables

from run_tests import Backend

from .baseplatformtests import BasePlatformTests


class InternalInstallTestsTests(BasePlatformTests):
    """Tests for internal helper functions in minstalltests.py."""

    def test_is_under_dir_basic(self):
        """Test basic _is_under_dir behavior."""
        self.assertTrue(_is_under_dir('/foo/bar', '/foo'))
        self.assertTrue(_is_under_dir('/foo/bar/baz', '/foo'))
        self.assertTrue(_is_under_dir('/foo/bar/baz', '/foo/bar'))

    def test_is_under_dir_same_dir(self):
        """Test _is_under_dir with same directory."""
        self.assertTrue(_is_under_dir('/foo', '/foo'))
        self.assertTrue(_is_under_dir('/foo/', '/foo'))

    def test_is_under_dir_not_under(self):
        """Test _is_under_dir with paths that are not under the directory."""
        self.assertFalse(_is_under_dir('/bar/baz', '/foo'))
        self.assertFalse(_is_under_dir('/foobar', '/foo'))

    def test_is_under_dir_relative_path(self):
        """Test _is_under_dir with relative paths always returns False."""
        self.assertFalse(_is_under_dir('bar/baz', '/foo'))
        self.assertFalse(_is_under_dir('relative/path', '/foo'))

    def test_is_shared_library_so(self):
        """Test _is_shared_library with .so files."""
        self.assertTrue(_is_shared_library('libfoo.so'))
        self.assertTrue(_is_shared_library('libfoo.so.1'))
        self.assertTrue(_is_shared_library('libfoo.so.1.2'))
        self.assertTrue(_is_shared_library('libfoo.so.1.2.3'))

    def test_is_shared_library_dylib(self):
        """Test _is_shared_library with .dylib files."""
        self.assertTrue(_is_shared_library('libfoo.dylib'))

    def test_is_shared_library_dll(self):
        """Test _is_shared_library with .dll and .dll.a files."""
        self.assertTrue(_is_shared_library('libfoo.dll'))
        self.assertTrue(_is_shared_library('libfoo.dll.a'))

    def test_is_shared_library_not_shared(self):
        """Test _is_shared_library with non-shared library files."""
        self.assertFalse(_is_shared_library('libfoo.a'))
        self.assertFalse(_is_shared_library('foo.o'))
        self.assertFalse(_is_shared_library('foo.c'))
        self.assertFalse(_is_shared_library('foo.h'))
        self.assertFalse(_is_shared_library('libfoo.so.bak'))
        self.assertFalse(_is_shared_library('README'))

    def test_copy_file(self):
        """Test _copy_file creates directories and copies files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, 'source.txt')
            dst = os.path.join(tmpdir, 'subdir', 'dest.txt')
            with open(src, 'w') as f:
                f.write('hello')
            if not is_windows():
                # Make it executable
                os.chmod(src, os.stat(src).st_mode | stat.S_IXUSR)

            copied: T.Set[str] = set()
            _copy_file(src, dst, copied, quiet=True)

            self.assertTrue(os.path.isfile(dst))
            self.assertIn(dst, copied)
            with open(dst) as f:
                self.assertEqual(f.read(), 'hello')
            if not is_windows():
                # Check executable permission is preserved
                self.assertTrue(os.stat(dst).st_mode & stat.S_IXUSR)

    def test_copy_file_skips_already_copied(self):
        """Test _copy_file does not re-copy files already in the copied set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, 'source.txt')
            dst = os.path.join(tmpdir, 'dest.txt')
            with open(src, 'w') as f:
                f.write('original')

            copied: T.Set[str] = {dst}  # Already marked as copied
            _copy_file(src, dst, copied, quiet=True)
            # Should not create the file since it's already in copied set
            self.assertFalse(os.path.isfile(dst))

    def test_copy_file_skips_nonexistent_source(self):
        """Test _copy_file silently skips non-existent source files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dst = os.path.join(tmpdir, 'dest.txt')
            copied: T.Set[str] = set()
            _copy_file('/nonexistent/file.txt', dst, copied, quiet=True)
            self.assertFalse(os.path.isfile(dst))
            self.assertNotIn(dst, copied)

    def test_copy_directory_contents(self):
        """Test _copy_directory_contents copies all files recursively."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = os.path.join(tmpdir, 'src')
            dst_dir = os.path.join(tmpdir, 'dst')
            os.makedirs(os.path.join(src_dir, 'sub'))
            with open(os.path.join(src_dir, 'a.txt'), 'w') as f:
                f.write('file_a')
            with open(os.path.join(src_dir, 'sub', 'b.txt'), 'w') as f:
                f.write('file_b')

            copied: T.Set[str] = set()
            _copy_directory_contents(src_dir, dst_dir, copied, quiet=True)

            self.assertTrue(os.path.isfile(os.path.join(dst_dir, 'a.txt')))
            self.assertTrue(os.path.isfile(os.path.join(dst_dir, 'sub', 'b.txt')))
            with open(os.path.join(dst_dir, 'a.txt')) as f:
                self.assertEqual(f.read(), 'file_a')
            with open(os.path.join(dst_dir, 'sub', 'b.txt')) as f:
                self.assertEqual(f.read(), 'file_b')

    def test_copy_directory_contents_nonexistent(self):
        """Test _copy_directory_contents is no-op for non-existent directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dst_dir = os.path.join(tmpdir, 'dst')
            copied: T.Set[str] = set()
            _copy_directory_contents('/nonexistent', dst_dir, copied, quiet=True)
            self.assertFalse(os.path.exists(dst_dir))

    def test_rewrite_env_paths(self):
        """Test _rewrite_env_paths rewrites build dir paths to placeholders."""
        build_dir = '/build'
        source_dir = '/source'
        install_root = '/install'

        env = EnvironmentVariables()
        env.set('MY_VAR', [os.path.join(build_dir, 'subdir', 'file')])

        copied: T.Set[str] = set()
        _rewrite_env_paths(env, build_dir, source_dir, install_root, copied, quiet=True)

        # Check that the path was rewritten with the placeholder
        self.assertEqual(len(env.envvars), 1)
        method, name, values, sep = env.envvars[0]
        self.assertEqual(name, 'MY_VAR')
        self.assertEqual(values, [os.path.join(INSTALLED_TESTS_DIR_PLACEHOLDER, 'subdir', 'file')])

    def test_rewrite_env_paths_source_dir(self):
        """Test _rewrite_env_paths rewrites source dir paths to placeholders."""
        build_dir = '/build'
        source_dir = '/source'
        install_root = '/install'

        env = EnvironmentVariables()
        env.set('MY_VAR', [os.path.join(source_dir, 'data', 'file')])

        copied: T.Set[str] = set()
        _rewrite_env_paths(env, build_dir, source_dir, install_root, copied, quiet=True)

        self.assertEqual(len(env.envvars), 1)
        method, name, values, sep = env.envvars[0]
        self.assertEqual(name, 'MY_VAR')
        self.assertEqual(values, [os.path.join(INSTALLED_TESTS_DIR_PLACEHOLDER, 'data', 'file')])

    def test_rewrite_env_paths_external_path_unchanged(self):
        """Test _rewrite_env_paths does not modify external paths."""
        build_dir = '/build'
        source_dir = '/source'
        install_root = '/install'

        env = EnvironmentVariables()
        env.set('MY_VAR', ['/usr/bin/python3'])

        copied: T.Set[str] = set()
        _rewrite_env_paths(env, build_dir, source_dir, install_root, copied, quiet=True)

        self.assertEqual(len(env.envvars), 1)
        method, name, values, sep = env.envvars[0]
        self.assertEqual(name, 'MY_VAR')
        self.assertEqual(values, ['/usr/bin/python3'])


class InstallTestsCommandTests(BasePlatformTests):
    """Integration tests for 'meson install --install-tests'."""

    def _install_tests(self, *, destdir: T.Optional[str] = None,
                       tests_prefix: T.Optional[str] = None,
                       extra_args: T.Optional[T.List[str]] = None,
                       quiet: bool = False) -> str:
        """Run 'meson install --install-tests' and return stdout."""
        if self.backend is not Backend.ninja:
            raise SkipTest(f'{self.backend.name!r} backend can\'t install tests')
        cmd = self.meson_command + ['install', '-C', self.builddir, '--no-rebuild', '--install-tests']
        if tests_prefix:
            cmd += ['--tests-prefix', tests_prefix]
        if quiet:
            cmd += ['-q']
        if extra_args:
            cmd += extra_args
        env = None
        if destdir:
            env = {'DESTDIR': destdir}
        return self._run(cmd, override_envvars=env)

    def _get_installed_test_dir(self, destdir: str,
                                tests_prefix: str = 'tests') -> str:
        """Return the path where tests are installed."""
        from mesonbuild.scripts import destdir_join
        return os.path.join(destdir_join(destdir, self.prefix), tests_prefix)

    def _load_installed_tests(self, install_root: str) -> T.List:
        """Load the installed test data and resolve placeholders."""
        from mesonbuild.minstalltests import resolve_installed_test_placeholders
        test_data = os.path.join(install_root, 'meson-private', 'meson_test_setup.dat')
        marker = os.path.join(install_root, 'meson-private', 'installed-tests.marker')
        with open(test_data, 'rb') as f:
            tests = pickle.load(f)
        resolve_installed_test_placeholders(tests, install_root, marker)
        return tests

    def test_install_tests_basic(self):
        """Test basic install-tests installs test executable and data files."""
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        self.init(testdir)
        self.build()

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        self._install_tests(destdir=destdir)

        install_root = self._get_installed_test_dir(destdir)

        # Check the meson-private directory was created
        private_dir = os.path.join(install_root, 'meson-private')
        self.assertPathExists(private_dir)

        # Check test data file was created
        test_data = os.path.join(private_dir, 'meson_test_setup.dat')
        self.assertPathExists(test_data)

        # Check build.dat was copied
        build_dat = os.path.join(private_dir, 'build.dat')
        self.assertPathExists(build_dat)

        # Check coredata.dat was copied
        coredata_dat = os.path.join(private_dir, 'coredata.dat')
        self.assertPathExists(coredata_dat)

        # Check meson-logs directory was created
        logs_dir = os.path.join(install_root, 'meson-logs')
        self.assertPathExists(logs_dir)

        # Load the installed test data and verify
        tests = self._load_installed_tests(install_root)
        self.assertGreater(len(tests), 0)

        # The test executable should be in the install root
        for test in tests:
            for fname in test.fname:
                if os.path.isabs(fname):
                    self.assertTrue(
                        fname.startswith(install_root),
                        f'Test executable {fname} should be under install root {install_root}'
                    )
                    self.assertPathExists(fname)

    def test_install_tests_run_installed(self):
        """Test that installed tests can be run with 'meson test -C <dir>'.

        The installed test directory is auto-detected (no --no-rebuild needed).
        """
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        self.init(testdir)
        self.build()

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        self._install_tests(destdir=destdir)

        install_root = self._get_installed_test_dir(destdir)

        # Run 'meson test -C <install_root>' WITHOUT --no-rebuild.
        # The installed-tests marker should make meson auto-skip rebuild.
        cmd = self.meson_command + ['test', '-C', install_root]
        result = self._run(cmd)
        self.assertIn('OK', result)

    def test_install_tests_no_tests(self):
        """Test install-tests with a project that has no tests."""
        testdir = os.path.join(self.common_test_dir, '3 static')
        self.init(testdir)
        self.build()

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        result = self._install_tests(destdir=destdir)
        self.assertIn('No tests found', result)

    def test_install_tests_custom_prefix(self):
        """Test install-tests with custom tests-prefix."""
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        self.init(testdir)
        self.build()

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        self._install_tests(destdir=destdir, tests_prefix='my-tests')

        install_root = self._get_installed_test_dir(destdir, 'my-tests')
        self.assertPathExists(os.path.join(install_root, 'meson-private', 'meson_test_setup.dat'))

        # The default 'tests' directory should NOT exist
        default_root = self._get_installed_test_dir(destdir, 'tests')
        self.assertPathDoesNotExist(default_root)

    def test_install_tests_destdir(self):
        """Test install-tests with DESTDIR support."""
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        self.init(testdir)
        self.build()

        destdir = os.path.join(self.builddir, 'custom-destdir')
        self._install_tests(destdir=destdir)

        install_root = self._get_installed_test_dir(destdir)
        self.assertPathExists(install_root)
        self.assertPathExists(os.path.join(install_root, 'meson-private', 'meson_test_setup.dat'))

    def test_install_tests_quiet(self):
        """Test install-tests with quiet mode produces minimal output."""
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        self.init(testdir)
        self.build()

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        result = self._install_tests(destdir=destdir, quiet=True)
        # Quiet mode should not mention individual file installations
        self.assertNotIn('Installing', result)

    def test_install_tests_verbose_output(self):
        """Test install-tests without quiet mode lists installed files."""
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        self.init(testdir)
        self.build()

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        result = self._install_tests(destdir=destdir, quiet=False)
        # Should see "Installing" messages
        self.assertIn('Installing', result)
        self.assertIn('Installed', result)

    def test_install_tests_with_args(self):
        """Test install-tests for project with test arguments."""
        testdir = os.path.join(self.common_test_dir, '41 test args')
        self.init(testdir)
        self.build()

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        self._install_tests(destdir=destdir)

        install_root = self._get_installed_test_dir(destdir)
        test_data = os.path.join(install_root, 'meson-private', 'meson_test_setup.dat')
        self.assertPathExists(test_data)

        tests = self._load_installed_tests(install_root)

        # Should have multiple tests
        self.assertGreater(len(tests), 1)

        # Find the 'command line arguments' test and check it has args
        cmd_args_test = None
        for t in tests:
            if t.name == 'command line arguments':
                cmd_args_test = t
                break
        self.assertIsNotNone(cmd_args_test, 'Should find "command line arguments" test')
        self.assertEqual(cmd_args_test.cmd_args, ['first', 'second'])

    def test_install_tests_with_args_run(self):
        """Test that installed tests with args can be run successfully."""
        testdir = os.path.join(self.common_test_dir, '41 test args')
        self.init(testdir)
        self.build()

        # First verify tests pass normally
        self.run_tests()

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        self._install_tests(destdir=destdir)
        install_root = self._get_installed_test_dir(destdir)

        # Run installed tests WITHOUT --no-rebuild (auto-detected)
        cmd = self.meson_command + ['test', '-C', install_root]
        result = self._run(cmd)
        self.assertIn('OK', result)

    def test_install_tests_with_shared_library(self):
        """Test install-tests copies shared libraries for linked tests."""
        testdir = os.path.join(self.common_test_dir, '6 linkshared')
        self.init(testdir)
        self.build()

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        self._install_tests(destdir=destdir)

        install_root = self._get_installed_test_dir(destdir)
        test_data = os.path.join(install_root, 'meson-private', 'meson_test_setup.dat')
        self.assertPathExists(test_data)

        tests = self._load_installed_tests(install_root)

        self.assertGreater(len(tests), 0)

        # All test executables should exist in installed location
        # After resolution, some fnames may point to prefix paths (already-installed
        # binaries) that don't exist under install_root with DESTDIR; only check
        # existence for paths under install_root.
        for test in tests:
            for fname in test.fname:
                if os.path.isabs(fname) and fname.startswith(install_root):
                    self.assertPathExists(fname)

    def test_install_tests_shared_library_run(self):
        """Test that installed tests with shared library deps can actually run."""
        testdir = os.path.join(self.common_test_dir, '6 linkshared')
        self.init(testdir)
        self.build()

        # First verify tests pass normally
        self.run_tests()

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        self._install_tests(destdir=destdir)
        install_root = self._get_installed_test_dir(destdir)

        # Run installed tests WITHOUT --no-rebuild (auto-detected)
        cmd = self.meson_command + ['test', '-C', install_root]
        result = self._run(cmd)
        self.assertIn('OK', result)

    def test_install_tests_paths_rewritten(self):
        """Test that paths in test data are rewritten to point into install root."""
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        self.init(testdir)
        self.build()

        # Use an external destdir to ensure paths don't accidentally
        # overlap with build dir
        destdir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(destdir, ignore_errors=True))
        self._install_tests(destdir=destdir)
        install_root = self._get_installed_test_dir(destdir)

        tests = self._load_installed_tests(install_root)

        # Load original test data to compare
        orig_test_data = os.path.join(self.builddir, 'meson-private', 'meson_test_setup.dat')
        with open(orig_test_data, 'rb') as f:
            orig_tests = pickle.load(f)

        # Compute the prefix directory (install_root stripped of tests_subdir)
        prefix_dir = os.path.dirname(install_root)

        # All absolute paths in fname should be under install_root or
        # the prefix (for already-installed files), and NOT the build dir
        for test in tests:
            for fname in test.fname:
                if os.path.isabs(fname):
                    self.assertFalse(
                        fname.startswith(self.builddir),
                        f'Test path {fname} should NOT reference the build dir'
                    )
                    self.assertTrue(
                        fname.startswith(install_root) or fname.startswith(prefix_dir),
                        f'Test path {fname} should be under install root {install_root} or prefix {prefix_dir}'
                    )

        # Verify the original tests reference the build dir
        for test in orig_tests:
            for fname in test.fname:
                if os.path.isabs(fname):
                    self.assertTrue(
                        fname.startswith(self.builddir),
                        f'Original test path {fname} should reference the build dir'
                    )

    def test_install_tests_executable_permissions(self):
        """Test that installed test executables retain executable permissions."""
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        self.init(testdir)
        self.build()

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        self._install_tests(destdir=destdir)
        install_root = self._get_installed_test_dir(destdir)

        tests = self._load_installed_tests(install_root)

        for test in tests:
            for fname in test.fname:
                if os.path.isabs(fname) and os.path.isfile(fname):
                    if not is_windows():
                        mode = os.stat(fname).st_mode
                        self.assertTrue(
                            mode & stat.S_IXUSR,
                            f'Installed test executable {fname} should be executable'
                        )
                    else:
                        self.assertTrue(
                            os.access(fname, os.X_OK),
                            f'Installed test executable {fname} should be executable'
                        )

    def test_install_tests_multiple_runs(self):
        """Test that running install-tests twice works (overwrites cleanly)."""
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        self.init(testdir)
        self.build()

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        self._install_tests(destdir=destdir)
        # Run again — should not fail
        self._install_tests(destdir=destdir)

        install_root = self._get_installed_test_dir(destdir)
        self.assertPathExists(os.path.join(install_root, 'meson-private', 'meson_test_setup.dat'))

    def test_install_tests_with_env_vars(self):
        """Test that environment variables in test data are properly handled."""
        testdir = os.path.join(self.common_test_dir, '41 test args')
        self.init(testdir)
        self.build()

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        self._install_tests(destdir=destdir)
        install_root = self._get_installed_test_dir(destdir)

        tests = self._load_installed_tests(install_root)

        # Find the 'environment variables' test
        env_test = None
        for t in tests:
            if t.name == 'environment variables':
                env_test = t
                break
        self.assertIsNotNone(env_test, 'Should find "environment variables" test')
        # The test should have environment variables set
        self.assertTrue(len(env_test.env.envvars) > 0,
                        'Environment variables test should have env vars')

    def test_install_tests_test_count_preserved(self):
        """Test that the number of tests is preserved in installed data."""
        testdir = os.path.join(self.common_test_dir, '41 test args')
        self.init(testdir)
        self.build()

        # Load original test data
        orig_test_data = os.path.join(self.builddir, 'meson-private', 'meson_test_setup.dat')
        with open(orig_test_data, 'rb') as f:
            orig_tests = pickle.load(f)

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        self._install_tests(destdir=destdir)
        install_root = self._get_installed_test_dir(destdir)

        # Load installed test data
        installed_tests = self._load_installed_tests(install_root)

        # Same number of tests
        self.assertEqual(len(orig_tests), len(installed_tests))

        # Same test names
        orig_names = sorted(t.name for t in orig_tests)
        installed_names = sorted(t.name for t in installed_tests)
        self.assertEqual(orig_names, installed_names)

    def test_install_tests_workdir_rewritten(self):
        """Test that workdir paths are properly rewritten."""
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        self.init(testdir)
        self.build()

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        self._install_tests(destdir=destdir)
        install_root = self._get_installed_test_dir(destdir)

        tests = self._load_installed_tests(install_root)

        for test in tests:
            if test.workdir:
                self.assertFalse(
                    test.workdir.startswith(self.builddir),
                    f'Test workdir {test.workdir} should NOT reference the build dir'
                )

    def test_install_tests_marker_file_created(self):
        """Test that install-tests creates the installed-tests marker file."""
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        self.init(testdir)
        self.build()

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        self._install_tests(destdir=destdir)
        install_root = self._get_installed_test_dir(destdir)

        marker = os.path.join(install_root, 'meson-private', 'installed-tests.marker')
        self.assertPathExists(marker)

    def test_install_tests_auto_skip_rebuild(self):
        """Test that 'meson test -C <installed-dir>' auto-skips rebuild.

        When running tests from an installed test directory, 'meson test'
        should detect the marker file and automatically skip the rebuild
        step, so the user does NOT need to pass --no-rebuild.
        """
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        self.init(testdir)
        self.build()

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        self._install_tests(destdir=destdir)
        install_root = self._get_installed_test_dir(destdir)

        # Run tests without --no-rebuild; should succeed because of auto-detection
        cmd = self.meson_command + ['test', '-C', install_root]
        result = self._run(cmd)
        self.assertIn('OK', result)

    def test_install_tests_verbose_hint(self):
        """Test that verbose install-tests output shows how to run tests."""
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        self.init(testdir)
        self.build()

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        result = self._install_tests(destdir=destdir, quiet=False)
        # The usage hint should NOT mention --no-rebuild (auto-detected)
        self.assertIn('meson test -C', result)
        self.assertNotIn('--no-rebuild', result)

    def test_install_tests_destdir_via_env(self):
        """Test that DESTDIR can be set via environment variable."""
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        self.init(testdir)
        self.build()

        destdir = os.path.join(self.builddir, 'env-destdir')
        self._install_tests(destdir=destdir)
        install_root = self._get_installed_test_dir(destdir)

        self.assertPathExists(install_root)
        self.assertPathExists(os.path.join(install_root, 'meson-private', 'meson_test_setup.dat'))
        self.assertPathExists(os.path.join(install_root, 'meson-private', 'installed-tests.marker'))

    def test_install_tests_destdir_via_flag(self):
        """Test that --destdir flag overrides DESTDIR environment variable."""
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        self.init(testdir)
        self.build()

        env_destdir = os.path.join(self.builddir, 'env-destdir')
        flag_destdir = os.path.join(self.builddir, 'flag-destdir')

        # Set DESTDIR env but also pass --destdir flag
        cmd = self.meson_command + ['install', '-C', self.builddir, '--no-rebuild',
                                    '--install-tests', '--destdir', flag_destdir]
        self._run(cmd, override_envvars={'DESTDIR': env_destdir})

        # Flag should win over env
        flag_root = self._get_installed_test_dir(flag_destdir)
        self.assertPathExists(flag_root)
        self.assertPathExists(os.path.join(flag_root, 'meson-private', 'meson_test_setup.dat'))

        # Env destdir should NOT have been used
        self.assertPathDoesNotExist(env_destdir)

    def test_install_tests_run_specific_test(self):
        """Test running a specific installed test by name."""
        testdir = os.path.join(self.common_test_dir, '41 test args')
        self.init(testdir)
        self.build()

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        self._install_tests(destdir=destdir)
        install_root = self._get_installed_test_dir(destdir)

        # Run only the 'command line arguments' test
        cmd = self.meson_command + ['test', '-C', install_root,
                                    'command line arguments']
        result = self._run(cmd)
        self.assertIn('OK', result)

    def test_install_tests_list(self):
        """Test listing installed tests."""
        testdir = os.path.join(self.common_test_dir, '41 test args')
        self.init(testdir)
        self.build()

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        self._install_tests(destdir=destdir)
        install_root = self._get_installed_test_dir(destdir)

        cmd = self.meson_command + ['test', '-C', install_root, '--list']
        result = self._run(cmd)
        self.assertIn('command line arguments', result)

    def test_install_tests_extra_paths_rewritten(self):
        """Test that extra_paths entries pointing to build dir are rewritten."""
        testdir = os.path.join(self.common_test_dir, '6 linkshared')
        self.init(testdir)
        self.build()

        destdir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(destdir, ignore_errors=True))
        self._install_tests(destdir=destdir)
        install_root = self._get_installed_test_dir(destdir)

        tests = self._load_installed_tests(install_root)

        for test in tests:
            for p in test.extra_paths:
                if os.path.isabs(p):
                    self.assertFalse(
                        p.startswith(self.builddir),
                        f'extra_path {p} should NOT reference the build dir'
                    )

    def test_install_tests_env_paths_rewritten(self):
        """Test that environment variable paths are rewritten."""
        testdir = os.path.join(self.common_test_dir, '6 linkshared')
        self.init(testdir)
        self.build()

        destdir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(destdir, ignore_errors=True))
        self._install_tests(destdir=destdir)
        install_root = self._get_installed_test_dir(destdir)

        tests = self._load_installed_tests(install_root)

        for test in tests:
            for method, name, values, sep in test.env.envvars:
                for val in values:
                    if isinstance(val, str) and os.path.isabs(val):
                        self.assertFalse(
                            val.startswith(self.builddir),
                            f'Env var {name} value {val} should NOT reference the build dir'
                        )

    def test_meson_install_without_install_tests(self):
        """Test that normal 'meson install' does NOT install tests."""
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        self.init(testdir)
        self.build()

        destdir = os.path.join(self.builddir, 'install-dest')
        cmd = self.meson_command + ['install', '-C', self.builddir,
                                    '--no-rebuild']
        self._run(cmd, override_envvars={'DESTDIR': destdir})

        # Tests dir should NOT exist for a normal install
        tests_root = self._get_installed_test_dir(destdir)
        self.assertPathDoesNotExist(tests_root)

    def test_install_tests_no_lib_duplication(self):
        """Test that libraries already installed by 'meson install' are NOT
        duplicated into the test install directory.

        Given a project with an installed shared library (install: true)
        and a test that links to it, 'meson install --install-tests' should
        install the library under [prefix]/lib via the normal install, and
        only install the test executable under [prefix]/tests.  The library
        must NOT appear under [prefix]/tests.
        """
        testdir = os.path.join(self.common_test_dir, '287 install tests shared lib')
        self.init(testdir)
        self.build()

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        self._install_tests(destdir=destdir)

        install_root = self._get_installed_test_dir(destdir)
        from mesonbuild.scripts import destdir_join
        prefix_root = destdir_join(destdir, self.prefix)

        def _is_mylib(fn: str) -> bool:
            """Check if a file is a shared library for 'mylib'."""
            return fn.startswith(('libmylib', 'mylib')) and _is_shared_library(fn)

        # The shared library should be installed under [prefix]/lib (or bin on
        # Windows), NOT anywhere under [prefix]/tests.
        lib_in_prefix = False
        for dirpath, dirnames, filenames in os.walk(prefix_root):
            # Skip the tests subtree
            rel = os.path.relpath(dirpath, prefix_root)
            if rel == 'tests' or rel.startswith('tests' + os.sep):
                continue
            for fn in filenames:
                if _is_mylib(fn):
                    lib_in_prefix = True
        self.assertTrue(lib_in_prefix,
                        'mylib should be installed under the prefix (e.g. lib/ or bin/)')

        # The shared library must NOT appear under the test install dir
        for dirpath, dirnames, filenames in os.walk(install_root):
            for fn in filenames:
                if _is_mylib(fn):
                    self.fail(f'Library {fn} should NOT be duplicated under '
                              f'tests dir {install_root}, found at {dirpath}')

        # The test executable should still be present in the test tree
        tests = self._load_installed_tests(install_root)
        self.assertGreater(len(tests), 0)

    def test_install_tests_no_lib_duplication_run(self):
        """Test that installed tests referencing non-duplicated libs can run."""
        testdir = os.path.join(self.common_test_dir, '287 install tests shared lib')
        self.init(testdir)
        self.build()

        # First verify tests pass in the build directory
        self.run_tests()

        destdir = os.path.join(self.builddir, 'install-tests-dest')
        self._install_tests(destdir=destdir)
        install_root = self._get_installed_test_dir(destdir)

        # The installed tests should still run — they find the library via
        # LD_LIBRARY_PATH / extra_paths pointing at [prefix]/lib.
        cmd = self.meson_command + ['test', '-C', install_root]
        result = self._run(cmd)
        self.assertIn('OK', result)

    def test_install_tests_relocatable(self):
        """Test that the entire prefix tree can be moved to a different path
        and installed tests still run successfully.

        This verifies that the installed-tests pickle uses relocatable
        placeholders instead of hard-coded absolute paths.
        """
        testdir = os.path.join(self.common_test_dir, '287 install tests shared lib')
        self.init(testdir)
        self.build()

        # First verify tests pass normally
        self.run_tests()

        # Install to a destdir
        destdir = os.path.join(self.builddir, 'install-tests-dest')
        self._install_tests(destdir=destdir)
        install_root = self._get_installed_test_dir(destdir)

        # Sanity check: tests run from the original location
        cmd = self.meson_command + ['test', '-C', install_root]
        result = self._run(cmd)
        self.assertIn('OK', result)

        # Now move the entire prefix tree to a different location
        from mesonbuild.scripts import destdir_join
        prefix_root = destdir_join(destdir, self.prefix)
        relocated = os.path.join(self.builddir, 'relocated-tree')
        shutil.move(prefix_root, relocated)

        # Compute the new test dir inside the relocated tree
        relocated_tests = os.path.join(relocated, 'tests')

        # The tests should STILL pass from the new location
        cmd = self.meson_command + ['test', '-C', relocated_tests]
        result = self._run(cmd)
        self.assertIn('OK', result)
