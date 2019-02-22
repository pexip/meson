# Copyright 2019 The Meson development team

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# This class contains the basic functionality needed to run any interpreter
# or an interpreter-based tool.

from .common import CMakeException
from .client import CMakeClient, RequestCMakeInputs, RequestConfigure, RequestCompute, RequestCodeModel, CMakeTarget
from .. import mlog
from ..build import Build
from ..environment import Environment
from ..mparser import Token, CodeBlockNode, FunctionNode, ArrayNode, ArgumentNode, StringNode, IdNode
from ..backend.backends import Backend
from ..dependencies.base import CMakeDependency, ExternalProgram
from subprocess import Popen, PIPE, STDOUT
from typing import Dict
import os, re

CMAKE_BACKEND_GENERATOR_MAP = {
    'ninja': 'Ninja',
    'xcode': 'Xcode',
    'vs2010': 'Visual Studio 10 2010',
    'vs2015': 'Visual Studio 15 2017',
    'vs2017': 'Visual Studio 15 2017',
}

CMAKE_LANGUAGE_MAP = {
    'c': 'C',
    'cpp': 'CXX',
    'cuda': 'CUDA',
    'cs': 'CSharp',
    'java': 'Java',
    'fortran': 'Fortran',
    'swift': 'Swift',
}

class ConverterTarget:
    lang_cmake_to_meson = {val.lower(): key for key, val in CMAKE_LANGUAGE_MAP.items()}

    def __init__(self, target: CMakeTarget):
        self.artifacts = target.artifacts
        self.src_dir = target.src_dir
        self.name = target.name
        self.full_name = target.full_name
        self.type = target.type
        self.install = target.install
        self.install_dir = ''
        self.link_libraries = target.link_libraries
        self.link_flags = target.link_flags + target.link_lang_flags

        if target.install_paths:
            self.install_dir = target.install_paths[0]

        self.languages = []
        self.sources = []
        self.generated = []
        self.includes = []
        self.link_with = []
        self.compile_opts = {}

        # Project default override options (c_std, cpp_std, etc.)
        self.override_options = []

        for i in target.files:
            # Determine the meson language
            lang = ConverterTarget.lang_cmake_to_meson.get(i.language.lower(), 'c')
            if lang not in self.languages:
                self.languages += [lang]
            if lang not in self.compile_opts:
                self.compile_opts[lang] = []

            # Add arguments, but avoid duplicates
            args = i.flags
            args += ['-D{}'.format(x) for x in i.defines]
            self.compile_opts[lang] += [x for x in args if x not in self.compile_opts[lang]]

            # Handle include directories
            self.includes += [x for x in i.includes if x not in self.includes]

            # Add sources to the right array
            if i.is_generated:
                self.generated += i.sources
            else:
                self.sources += i.sources

    def __repr__(self) -> str:
        return '<{}: {}>'.format(self.__class__.__name__, self.name)

    std_regex = re.compile(r'([-]{1,2}std=|/std:v?)(.*)')

    def postprocess(self, output_target_map) -> None:
        # Detect setting the C and C++ standard
        for i in ['c', 'cpp']:
            if not i in self.compile_opts:
                continue

            temp = []
            for j in self.compile_opts[i]:
                m = ConverterTarget.std_regex.match(j)
                if m:
                    self.override_options += ['{}_std={}'.format(i, m.group(2))]
                else:
                    temp += [j]

            self.compile_opts[i] = temp

        # Fix link libraries
        temp = []
        for i in self.link_libraries:
            # Let meson handle this arcane magic
            if ',-rpath,' in i:
                continue
            if i in output_target_map:
                self.link_with += [output_target_map[i]]
                continue

            temp += [i]
        self.link_libraries = temp

        # Make paths relative
        def rel_path(x: str) -> str:
            if os.path.isabs(x) and os.path.commonpath([x, self.src_dir]) == self.src_dir:
                return os.path.relpath(x, self.src_dir)
            return x

        self.includes = [rel_path(x) for x in self.includes]
        self.sources = [rel_path(x) for x in self.sources]
        self.generated = [rel_path(x) for x in self.generated]

    def log(self) -> None:
        mlog.log('Target', mlog.bold(self.name))
        mlog.log('  -- full_name:      ', mlog.bold(self.full_name))
        mlog.log('  -- type:           ', mlog.bold(self.type))
        mlog.log('  -- install:        ', mlog.bold('true' if self.install else 'false'))
        mlog.log('  -- install_dir:    ', mlog.bold(self.install_dir))
        mlog.log('  -- link_libraries: ', mlog.bold(str(self.link_libraries)))
        mlog.log('  -- link_with:      ', mlog.bold(str(self.link_with)))
        mlog.log('  -- link_flags:     ', mlog.bold(str(self.link_flags)))
        mlog.log('  -- languages:      ', mlog.bold(str(self.languages)))
        mlog.log('  -- includes:       ', mlog.bold(str(self.includes)))
        mlog.log('  -- sources:        ', mlog.bold(str(self.sources)))
        mlog.log('  -- generated:      ', mlog.bold(str(self.generated)))
        mlog.log('  -- override_opts:  ', mlog.bold(str(self.override_options)))
        mlog.log('  -- options:')
        for key, val in self.compile_opts.items():
            mlog.log('    -', key, '=', mlog.bold(str(val)))

class CMakeInterpreter:
    def __init__(self, build: Build, subdir: str, src_dir: str, build_dir: str, env: Environment, backend: Backend):
        assert(hasattr(backend, 'name'))
        self.build = build
        self.subdir = subdir
        self.src_dir = src_dir
        self.build_dir = build_dir
        self.env = env
        self.backend_name = backend.name
        self.client = CMakeClient(self.env)

        # Raw CMake results
        self.bs_files = []
        self.codemodel = None

        # Analysed data
        self.project_name = ''
        self.languages = []

    def configure(self) -> None:
        # Find CMake
        cmake_exe, cmake_vers, _ = CMakeDependency.find_cmake_binary(self.env)
        if cmake_exe is None or cmake_exe is False:
            raise CMakeException('Unable to find CMake')
        assert(isinstance(cmake_exe, ExternalProgram))
        if not cmake_exe.found():
            raise CMakeException('Unable to find CMake')

        generator = CMAKE_BACKEND_GENERATOR_MAP[self.backend_name]
        cmake_args = cmake_exe.get_command()

        # Map meson compiler to CMake variables
        for lang, comp in self.build.compilers.items():
            if lang not in CMAKE_LANGUAGE_MAP:
                continue
            cmake_lang = CMAKE_LANGUAGE_MAP[lang]
            exelist = comp.get_exelist()
            if len(exelist) == 1:
                cmake_args += ['-DCMAKE_{}_COMPILER={}'.format(cmake_lang, exelist[0])]
            elif len(exelist) == 2:
                cmake_args += ['-DCMAKE_{}_COMPILER_LAUNCHER={}'.format(cmake_lang, exelist[0]),
                               '-DCMAKE_{}_COMPILER={}'.format(cmake_lang, exelist[1])]
        cmake_args += ['-G', generator]

        # Run CMake
        mlog.log()
        with mlog.nested():
            mlog.log('Configuring the build directory with', mlog.bold('CMake'), 'version', mlog.cyan(cmake_vers))
            mlog.log(mlog.bold('Running:'), ' '.join(cmake_args))
            mlog.log()
            os.makedirs(self.build_dir, exist_ok=True)
            proc = Popen(cmake_args + [self.src_dir], stdout=PIPE, stderr=STDOUT, cwd=self.build_dir)

            # Print CMake log in realtime
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                mlog.log(line.decode('utf-8').strip('\n'))

            # Wait for CMake to finish
            proc.communicate()

        mlog.log()
        h = mlog.green('SUCCEEDED') if proc.returncode == 0 else mlog.red('FAILED')
        mlog.log('CMake configuration:', h)
        if proc.returncode != 0:
            raise CMakeException('Failed to configure the CMake subproject')

    def initialise(self) -> None:
        # Run configure the old way becuse doing it
        # with the server doesn't work for some reason
        self.configure()

        with self.client.connect():
            generator = CMAKE_BACKEND_GENERATOR_MAP[self.backend_name]
            self.client.do_handshake(self.src_dir, self.build_dir, generator, 1)

            # Do a second configure to initialise the server
            self.client.query_checked(RequestConfigure(), 'CMake server configure')

            # Generate the build system files
            self.client.query_checked(RequestCompute(), 'Generating build system files')

            # Get CMake build system files
            bs_reply = self.client.query_checked(RequestCMakeInputs(), 'Querying build system files')

            # Now get the CMake code model
            cm_reply = self.client.query_checked(RequestCodeModel(), 'Querying the CMake code model')

        src_dir = bs_reply.src_dir
        self.bs_files = [x.file for x in bs_reply.build_files if  not x.is_cmake and not x.is_temp]
        self.bs_files = [os.path.relpath(os.path.join(src_dir, x), self.env.get_source_dir()) for x in self.bs_files]
        self.codemodel = cm_reply

    def analyse(self) -> None:
        if self.codemodel is None:
            raise CMakeException('CMakeInterpreter was not initialized')

        # Clear analyser data
        self.project_name = ''
        self.languages = []

        # Find all targets
        targets = []
        for i in self.codemodel.configs:
            for j in i.projects:
                if not self.project_name:
                    self.project_name = j.name
                for k in j.targets:
                    targets += [ConverterTarget(k)]

        output_target_map = {x.full_name: x for x in targets}

        for i in targets:
            i.postprocess(output_target_map)
            self.languages += [x for x in i.languages if x not in self.languages]

        mlog.log('CMake project', mlog.bold(self.project_name), 'has', mlog.bold(str(len(targets))), 'build targets.')

    def pretend_to_be_meson(self) -> CodeBlockNode:
        if not self.project_name:
            raise CMakeException('CMakeInterpreter was not analysed')

        def token(tid: str = 'string', val='') -> Token:
            return Token(tid, self.subdir, 0, 0, 0, None, val)

        def string(value: str) -> StringNode:
            return StringNode(token(val=value))

        def id(value: str) -> IdNode:
            return IdNode(token(val=value))

        def array(elements: list) -> ArrayNode:
            args = ArgumentNode(token())
            for i in elements:
                if isinstance(i, str):
                    i = string(i)
                args.arguments += [i]
            return ArrayNode(args, 0, 0)

        def function(name: str, args=[]) -> FunctionNode:
            args_n = ArgumentNode(token())
            for i in args:
                if isinstance(i, str):
                    i = string(i)
                args_n.arguments += [i]
            func_n = FunctionNode(self.subdir, 0, 0, name, args_n)
            return func_n

        root_cb = CodeBlockNode(token())
        root_cb.lines += [function('project', [self.project_name, array(self.languages)])]

        return root_cb
