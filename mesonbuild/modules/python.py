# Copyright 2018 The Meson development team

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os

from pathlib import Path
from .. import mesonlib
from . import ExtensionModule
from mesonbuild.modules import ModuleReturnValue
from . import permittedSnippetKwargs
from ..interpreterbase import (
    noPosargs, noKwargs, permittedKwargs,
    InterpreterObject, InvalidArguments
)
from ..interpreter import shlib_kwargs, ExternalProgramHolder
from .. import mlog
from ..environment import detect_cpu_family
from ..dependencies.base import (
    DependencyMethods, ExternalDependency,
    ExternalProgram, PkgConfigDependency,
    NonExistingExternalProgram
)

mod_kwargs = set(['subdir'])
mod_kwargs.update(shlib_kwargs)
mod_kwargs -= set(['install_rpath'])


def run_command(python, command):
    _, stdout, _ = mesonlib.Popen_safe(python.get_command() + [
        '-c',
        command])

    return stdout.strip()


class PythonDependency(ExternalDependency):
    def __init__(self, python_holder, environment, kwargs):
        super().__init__('python', environment, None, kwargs)
        self.name = 'python'
        self.static = kwargs.get('static', False)
        self.version = python_holder.version
        self.platform = python_holder.platform
        self.pkgdep = None
        self.config_vars = python_holder.config_vars
        self.paths = python_holder.paths

        if DependencyMethods.PKGCONFIG in self.methods:
            try:
                if mesonlib.version_compare(self.version, '>= 3.0'):
                    self.pkgdep = PkgConfigDependency('python3', environment, kwargs)
                else:
                    self.pkgdep = PkgConfigDependency('python2', environment, kwargs)
                if self.pkgdep.found():
                    self.compile_args = self.pkgdep.get_compile_args()
                    self.link_args = self.pkgdep.get_link_args()
                    self.version = self.pkgdep.get_version()
                    self.is_found = True
                    self.pcdep = self.pkgdep
                    return
                else:
                    self.pkgdep = None
            except Exception:
                pass

        if not self.is_found:
            if mesonlib.is_windows() and DependencyMethods.SYSCONFIG in self.methods:
                self._find_libpy_windows(environment)

        if self.is_found:
            mlog.log('Dependency', mlog.bold(self.name), 'found:', mlog.green('YES'))
        else:
            mlog.log('Dependency', mlog.bold(self.name), 'found:', mlog.red('NO'))

    def get_windows_python_arch(self):
        if self.platform == 'mingw':
            pycc = self.config_vars.get('CC')
            if pycc.startswith('x86_64'):
                return '64'
            elif pycc.startswith(('i686', 'i386')):
                return '32'
            else:
                mlog.log('MinGW Python built with unknown CC {!r}, please file'
                         'a bug'.format(pycc))
                return None
        elif self.platform == 'win32':
            return '32'
        elif self.platform in ('win64', 'win-amd64'):
            return '64'
        mlog.log('Unknown Windows Python platform {!r}'.format(self.platform))
        return None

    def get_windows_link_args(self):
        if self.platform.startswith('win'):
            vernum = self.config_vars.get('py_version_nodot')
            if self.static:
                libname = 'libpython{}.a'.format(vernum)
            else:
                libname = 'python{}.lib'.format(vernum)
            lib = Path(self.config_vars.get('base')) / 'libs' / libname
        elif self.platform == 'mingw':
            if self.static:
                libname = self.config_vars.get('LIBRARY')
            else:
                libname = self.config_vars.get('LDLIBRARY')
            lib = Path(self.config_vars.get('LIBDIR')) / libname
        if not lib.exists():
            mlog.log('Could not find Python3 library {!r}'.format(str(lib)))
            return None
        return [str(lib)]

    def _find_libpy_windows(self, env):
        '''
        Find python3 libraries on Windows and also verify that the arch matches
        what we are building for.
        '''
        pyarch = self.get_windows_python_arch()
        if pyarch is None:
            self.is_found = False
            return
        arch = detect_cpu_family(env.coredata.compilers)
        if arch == 'x86':
            arch = '32'
        elif arch == 'x86_64':
            arch = '64'
        else:
            # We can't cross-compile Python 3 dependencies on Windows yet
            mlog.log('Unknown architecture {!r} for'.format(arch),
                     mlog.bold(self.name))
            self.is_found = False
            return
        # Pyarch ends in '32' or '64'
        if arch != pyarch:
            mlog.log('Need', mlog.bold(self.name), 'for {}-bit, but '
                     'found {}-bit'.format(arch, pyarch))
            self.is_found = False
            return
        # This can fail if the library is not found
        largs = self.get_windows_link_args()
        if largs is None:
            self.is_found = False
            return
        self.link_args = largs
        # Compile args
        inc = self.paths.get('include')
        self.compile_args = ['-I' + inc]
        platinc = self.paths.get('platinclude')
        if platinc and inc != platinc:
            self.compile_args.append('-I' + platinc)
        self.is_found = True

    @staticmethod
    def get_methods():
        if mesonlib.is_windows():
            return [DependencyMethods.PKGCONFIG, DependencyMethods.SYSCONFIG]
        elif mesonlib.is_osx():
            return [DependencyMethods.PKGCONFIG, DependencyMethods.EXTRAFRAMEWORK]
        else:
            return [DependencyMethods.PKGCONFIG]

    def get_pkgconfig_variable(self, variable_name, kwargs):
        if self.pkgdep:
            return self.pkgdep.get_pkgconfig_variable(variable_name, kwargs)
        else:
            return super().get_pkgconfig_variable(variable_name, kwargs)


class PythonHolder(ExternalProgramHolder, InterpreterObject):
    def __init__(self, interpreter, python):
        InterpreterObject.__init__(self)
        ExternalProgramHolder.__init__(self, python)
        self.interpreter = interpreter
        prefix = self.interpreter.environment.coredata.get_builtin_option('prefix')

        self.config_vars = eval(run_command(python, "import sysconfig; print (repr(sysconfig.get_config_vars()))"))
        self.paths = eval(run_command(python, "import sysconfig; print (repr(sysconfig.get_paths()))"))
        install_paths = eval(run_command(python, "import sysconfig; print (repr(sysconfig.get_paths("
            "scheme='posix_prefix', vars={'base': '', 'platbase': '', 'installed_base': ''})))"))
        self.platlib_install_path = os.path.join(prefix, install_paths['platlib'][1:])
        self.purelib_install_path = os.path.join(prefix, install_paths['purelib'][1:])
        self.version = run_command(python, "import sysconfig; print (sysconfig.get_python_version())")
        self.platform = run_command(python, "import sysconfig; print (sysconfig.get_platform())")

    @permittedSnippetKwargs(mod_kwargs)
    def extension_module(self, interpreter, state, args, kwargs):
        if 'name_prefix' in kwargs:
            raise mesonlib.MesonException('Name_prefix is set automatically, specifying it is forbidden.')
        if 'name_suffix' in kwargs:
            raise mesonlib.MesonException('Name_suffix is set automatically, specifying it is forbidden.')

        if not 'install_dir' in kwargs:
            subdir = kwargs.pop('subdir', '')
            if not isinstance(subdir, str):
                raise InvalidArguments('"subdir" argument must be a string.')
            kwargs['install_dir'] = os.path.join(self.platlib_install_path, subdir)

        suffix = self.config_vars.get('EXT_SUFFIX') or self.config_vars.get('SO') or self.config_vars.get('.so')

        kwargs['name_prefix'] = ''
        # Strip the leading dot
        kwargs['name_suffix'] = suffix[1:]

        return interpreter.func_shared_module(None, args, kwargs)

    def dependency(self, interpreter, state, args, kwargs):
        dep = PythonDependency(self, interpreter.environment, kwargs)
        return interpreter.holderify(dep)

    @permittedSnippetKwargs(['pure', 'subdir'])
    def install_sources(self, interpreter, state, args, kwargs):
        pure = kwargs.pop('pure', False)
        if not isinstance(pure, bool):
            raise InvalidArguments('"pure" argument must be a boolean.')

        subdir = kwargs.pop('subdir', '')
        if not isinstance(subdir, str):
            raise InvalidArguments('"subdir" argument must be a string.')

        if pure:
            kwargs['install_dir'] = os.path.join(self.purelib_install_path, subdir)
        else:
            kwargs['install_dir'] = os.path.join(self.platlib_install_path, subdir)

        return interpreter.func_install_data(None, args, kwargs)

    @noPosargs
    @permittedKwargs(['pure', 'subdir'])
    def get_install_dir(self, node, args, kwargs):
        pure = kwargs.pop('pure', False)
        if not isinstance(pure, bool):
            raise InvalidArguments('"pure" argument must be a boolean.')

        subdir = kwargs.pop('subdir', '')
        if not isinstance(subdir, str):
            raise InvalidArguments('"subdir" argument must be a string.')

        if pure:
            res = os.path.join(self.purelib_install_path, subdir)
        else:
            res = os.path.join(self.platlib_install_path, subdir)

        return ModuleReturnValue(res, [])

    @noPosargs
    @noKwargs
    def language_version(self, node, args, kwargs):
        return ModuleReturnValue(self.version, [])

    @noPosargs
    @noKwargs
    def found(self, node, args, kwargs):
        return ModuleReturnValue(True, [])

    @noKwargs
    def get_config_var(self, node, args, kwargs):
        var_name = args[0]
        if not var_name:
            raise InvalidArguments('must specify a key')
        return ModuleReturnValue(self.config_vars.get(var_name, ''), [])

    def method_call(self, method_name, args, kwargs):
        try:
            fn = getattr(self, method_name)
        except AttributeError:
            raise InvalidArguments('Python object does not have method %s.' % method_name)

        if method_name in ['extension_module', 'dependency', 'install_sources']:
            value = fn(self.interpreter, None, args, kwargs)
            return self.interpreter.holderify(value)
        else:
            value = fn(None, args, kwargs)
            return self.interpreter.module_method_callback(value)


class PythonModule(ExtensionModule):
    def __init__(self):
        super().__init__()
        self.snippets.add('find')

    def _get_win_pythonpath(self, name_or_path):
        if not name_or_path in ['python2', 'python3']:
            return None
        ver = {'python2': '-2', 'python3': '-3'}[name_or_path]
        cmd = ['py', ver, '-c', "import sysconfig; print(sysconfig.get_config_var('BINDIR'))"]
        _, stdout, _ = mesonlib.Popen_safe(cmd)
        dir = stdout.strip()
        if os.path.exists(dir):
            return os.path.join(dir, 'python')
        else:
            return None

    @permittedSnippetKwargs(['required'])
    def find(self, interpreter, state, args, kwargs):
        required = kwargs.get('required', True)
        if not isinstance(required, bool):
            raise InvalidArguments('"required" argument must be a boolean.')

        name_or_path = args[0]
        if not name_or_path:
            python = ExternalProgram('python3', mesonlib.python_command, silent=True)
        else:
            if mesonlib.is_windows():
                pythonpath = self._get_win_pythonpath(name_or_path)
                if pythonpath is not None:
                    name_or_path = pythonpath

            python = ExternalProgram(name_or_path, silent = True)
            # Last ditch effort, python2 or python3 can be named python
            # on various platforms, let's not give up just yet, if an executable
            # named python is available and has a compatible version, let's use
            # it
            if not python.found() and name_or_path in ['python2', 'python3']:
                python = ExternalProgram('python', silent = True)
                if python.found():
                    version = run_command(python, "import sysconfig; print (sysconfig.get_python_version())")
                    if not version or \
                            name_or_path == 'python2' and mesonlib.version_compare(version, '>= 3.0') or \
                            name_or_path == 'python3' and not mesonlib.version_compare(version, '>= 3.0'):
                        res = NonExistingExternalProgram()

        if not python.found():
            if required:
                raise mesonlib.MesonException('{} not found'.format(name_or_path))
            res = ExternalProgramHolder(NonExistingExternalProgram())
        else:
            # Sanity check, we expect to have something that at least quacks in tune
            version = run_command(python, "import sysconfig; print (sysconfig.get_python_version())")
            if not version:
                res = ExternalProgramHolder(NonExistingExternalProgram())
            else:
                res = PythonHolder(interpreter, python)

        return res


def initialize():
    return PythonModule()
