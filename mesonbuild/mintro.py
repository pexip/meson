# Copyright 2014-2016 The Meson development team

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""This is a helper script for IDE developers. It allows you to
extract information such as list of targets, files, compiler flags,
tests and so on. All output is in JSON for simple parsing.

Currently only works for the Ninja backend. Others use generated
project files and don't need this info."""

import json
from . import build, mtest, coredata as cdata
from . import environment
from . import mesonlib
from . import astinterpreter
from . import mparser
from . import mlog
from . import compilers
from . import optinterpreter
from .interpreterbase import InvalidArguments
from .backend import ninjabackend, backends
import sys, os
import pathlib

def add_arguments(parser):
    parser.add_argument('--targets', action='store_true', dest='list_targets', default=False,
                        help='List top level targets.')
    parser.add_argument('--installed', action='store_true', dest='list_installed', default=False,
                        help='List all installed files and directories.')
    parser.add_argument('--target-files', action='store', dest='target_files', default=None,
                        help='List source files for a given target.')
    parser.add_argument('--buildsystem-files', action='store_true', dest='buildsystem_files', default=False,
                        help='List files that make up the build system.')
    parser.add_argument('--buildoptions', action='store_true', dest='buildoptions', default=False,
                        help='List all build options.')
    parser.add_argument('--tests', action='store_true', dest='tests', default=False,
                        help='List all unit tests.')
    parser.add_argument('--benchmarks', action='store_true', dest='benchmarks', default=False,
                        help='List all benchmarks.')
    parser.add_argument('--dependencies', action='store_true', dest='dependencies', default=False,
                        help='List external dependencies.')
    parser.add_argument('--projectinfo', action='store_true', dest='projectinfo', default=False,
                        help='Information about projects.')
    parser.add_argument('--backend', choices=cdata.backendlist, dest='backend', default='ninja',
                        help='The backend to use for the --buildoptions introspection.')
    parser.add_argument('builddir', nargs='?', default='.', help='The build directory')

def determine_installed_path(target, installdata):
    install_targets = []
    for i in target.outputs:
        for j in installdata.targets:
            if os.path.basename(j.fname) == i: # FIXME, might clash due to subprojects.
                install_targets += [j]
                break
    if len(install_targets) == 0:
        raise RuntimeError('Something weird happened. File a bug.')

    # Normalize the path by using os.path.sep consistently, etc.
    # Does not change the effective path.
    install_targets = list(map(lambda x: os.path.join(installdata.prefix, x.outdir, os.path.basename(x.fname)), install_targets))
    install_targets = list(map(lambda x: str(pathlib.PurePath(x)), install_targets))

    return install_targets


def list_installed(installdata):
    res = {}
    if installdata is not None:
        for t in installdata.targets:
            res[os.path.join(installdata.build_dir, t.fname)] = \
                os.path.join(installdata.prefix, t.outdir, os.path.basename(t.fname))
        for path, installpath, unused_prefix in installdata.data:
            res[path] = os.path.join(installdata.prefix, installpath)
        for path, installdir, unused_custom_install_mode in installdata.headers:
            res[path] = os.path.join(installdata.prefix, installdir, os.path.basename(path))
        for path, installpath, unused_custom_install_mode in installdata.man:
            res[path] = os.path.join(installdata.prefix, installpath)
    print(json.dumps(res))


def list_targets(coredata, builddata, installdata):
    tlist = []
    for (idname, target) in builddata.get_targets().items():
        t = {'name': target.get_basename(), 'id': idname}
        fname = target.get_filename()
        if isinstance(fname, list):
            fname = [os.path.join(target.subdir, x) for x in fname]
        else:
            fname = os.path.join(target.subdir, fname)
        t['filename'] = fname
        if isinstance(target, build.Executable):
            typename = 'executable'
        elif isinstance(target, build.SharedLibrary):
            typename = 'shared library'
        elif isinstance(target, build.StaticLibrary):
            typename = 'static library'
        elif isinstance(target, build.CustomTarget):
            typename = 'custom'
        elif isinstance(target, build.RunTarget):
            typename = 'run'
        else:
            typename = 'unknown'
        t['type'] = typename
        if installdata and target.should_install():
            t['installed'] = True
            t['install_filename'] = determine_installed_path(target, installdata)
        else:
            t['installed'] = False
        t['build_by_default'] = target.build_by_default
        tlist.append(t)
    print(json.dumps(tlist))

def list_target_files(target_name, coredata, builddata):
    try:
        t = builddata.targets[target_name]
        sources = t.sources + t.extra_files
    except KeyError:
        print("Unknown target %s." % target_name)
        sys.exit(1)
    out = []
    for i in sources:
        if isinstance(i, mesonlib.File):
            i = os.path.join(i.subdir, i.fname)
        out.append(i)
    print(json.dumps(out))

class BuildoptionsOptionHelper:
    # mimic an argparse namespace
    def __init__(self, cross_file):
        self.cross_file = cross_file
        self.native_file = None
        self.cmd_line_options = {}

class BuildoptionsInterperter(astinterpreter.AstInterpreter):
    # Interpreter to detect the options without a build directory
    # Most of the code is stolen from interperter.Interpreter
    def __init__(self, source_root, subdir, backend, cross_file=None, subproject='', subproject_dir='subprojects', env=None):
        super().__init__(source_root, subdir)

        options = BuildoptionsOptionHelper(cross_file)
        self.cross_file = cross_file
        if env is None:
            self.environment = environment.Environment(source_root, None, options)
        else:
            self.environment = env
        self.subproject = subproject
        self.subproject_dir = subproject_dir
        self.coredata = self.environment.get_coredata()
        self.option_file = os.path.join(self.source_root, self.subdir, 'meson_options.txt')
        self.backend = backend
        self.default_options = {'backend': self.backend}

        self.funcs.update({
            'project': self.func_project,
            'add_languages': self.func_add_languages
        })

    def detect_compilers(self, lang, need_cross_compiler):
        comp, cross_comp = self.environment.detect_compilers(lang, need_cross_compiler)
        if comp is None:
            return None, None

        self.coredata.compilers[lang] = comp
        # Native compiler always exist so always add its options.
        new_options = comp.get_options()
        if cross_comp is not None:
            self.coredata.cross_compilers[lang] = cross_comp
            new_options.update(cross_comp.get_options())

        optprefix = lang + '_'
        for k, o in new_options.items():
            if not k.startswith(optprefix):
                raise RuntimeError('Internal error, %s has incorrect prefix.' % k)
            if k in self.environment.cmd_line_options:
                o.set_value(self.environment.cmd_line_options[k])
            self.coredata.compiler_options.setdefault(k, o)

        return comp, cross_comp

    def flatten_args(self, args):
        # Resolve mparser.ArrayNode if needed
        flattend_args = []
        for i in args:
            if isinstance(i, mparser.ArrayNode):
                flattend_args += [x.value for x in i.args.arguments]
            elif isinstance(i, str):
                flattend_args += [i]
            else:
                pass
        return flattend_args

    def add_languages(self, args):
        need_cross_compiler = self.environment.is_cross_build() and self.environment.cross_info.need_cross_compiler()
        for lang in sorted(args, key=compilers.sort_clink):
            lang = lang.lower()
            if lang not in self.coredata.compilers:
                (comp, _) = self.detect_compilers(lang, need_cross_compiler)
                if comp is None:
                    return
                for optname in comp.base_options:
                    if optname in self.coredata.base_options:
                        continue
                    oobj = compilers.base_options[optname]
                    self.coredata.base_options[optname] = oobj

    def func_project(self, node, args, kwargs):
        if len(args) < 1:
            raise InvalidArguments('Not enough arguments to project(). Needs at least the project name.')

        proj_langs = self.flatten_args(args[1:])

        if os.path.exists(self.option_file):
            oi = optinterpreter.OptionInterpreter(self.subproject)
            oi.process(self.option_file)
            self.coredata.merge_user_options(oi.options)

        def_opts = kwargs.get('default_options', [])
        if isinstance(def_opts, mparser.ArrayNode):
            def_opts = [x.value for x in def_opts.args.arguments]

        self.project_default_options = mesonlib.stringlistify(def_opts)
        self.project_default_options = cdata.create_options_dict(self.project_default_options)
        self.default_options.update(self.project_default_options)
        self.coredata.set_default_options(self.default_options, self.subproject, self.environment.cmd_line_options)

        if not self.is_subproject() and 'subproject_dir' in kwargs:
            spdirname = kwargs['subproject_dir']
            if isinstance(spdirname, str):
                self.subproject_dir = spdirname
        if not self.is_subproject():
            subprojects_dir = os.path.join(self.source_root, self.subproject_dir)
            if os.path.isdir(subprojects_dir):
                for i in os.listdir(subprojects_dir):
                    if os.path.isdir(os.path.join(subprojects_dir, i)):
                        self.do_subproject(i)

        self.coredata.init_backend_options(self.backend)
        options = {k: v for k, v in self.environment.cmd_line_options.items() if k.startswith('backend_')}

        self.coredata.set_options(options)
        self.add_languages(proj_langs)

    def do_subproject(self, dirname):
        subproject_dir_abs = os.path.join(self.environment.get_source_dir(), self.subproject_dir)
        subpr = os.path.join(subproject_dir_abs, dirname)
        try:
            subi = BuildoptionsInterperter(subpr, '', self.backend, cross_file=self.cross_file, subproject=dirname, subproject_dir=self.subproject_dir, env=self.environment)
            subi.analyze()
        except:
            return

    def func_add_languages(self, node, args, kwargs):
        return self.add_languages(self.flatten_args(args))

    def is_subproject(self):
        return self.subproject != ''

    def analyze(self):
        self.load_root_meson_file()
        self.sanity_check_ast()
        self.parse_project()
        self.run()

def list_buildoptions_from_source(sourcedir, backend):
    # Make sure that log entries in other parts of meson don't interfere with the JSON output
    mlog.disable()
    backend = backends.get_backend_from_name(backend, None)
    intr = BuildoptionsInterperter(sourcedir, '', backend.name)
    intr.analyze()
    # Reenable logging just in case
    mlog.enable()
    list_buildoptions(intr.coredata)

def list_buildoptions(coredata):
    optlist = []

    dir_option_names = ['bindir',
                        'datadir',
                        'includedir',
                        'infodir',
                        'libdir',
                        'libexecdir',
                        'localedir',
                        'localstatedir',
                        'mandir',
                        'prefix',
                        'sbindir',
                        'sharedstatedir',
                        'sysconfdir']
    test_option_names = ['errorlogs',
                         'stdsplit']
    core_option_names = [k for k in coredata.builtins if k not in dir_option_names + test_option_names]

    dir_options = {k: o for k, o in coredata.builtins.items() if k in dir_option_names}
    test_options = {k: o for k, o in coredata.builtins.items() if k in test_option_names}
    core_options = {k: o for k, o in coredata.builtins.items() if k in core_option_names}

    add_keys(optlist, core_options, 'core')
    add_keys(optlist, coredata.backend_options, 'backend')
    add_keys(optlist, coredata.base_options, 'base')
    add_keys(optlist, coredata.compiler_options, 'compiler')
    add_keys(optlist, dir_options, 'directory')
    add_keys(optlist, coredata.user_options, 'user')
    add_keys(optlist, test_options, 'test')
    print(json.dumps(optlist))

def add_keys(optlist, options, section):
    keys = list(options.keys())
    keys.sort()
    for key in keys:
        opt = options[key]
        optdict = {'name': key, 'value': opt.value, 'section': section}
        if isinstance(opt, cdata.UserStringOption):
            typestr = 'string'
        elif isinstance(opt, cdata.UserBooleanOption):
            typestr = 'boolean'
        elif isinstance(opt, cdata.UserComboOption):
            optdict['choices'] = opt.choices
            typestr = 'combo'
        elif isinstance(opt, cdata.UserIntegerOption):
            typestr = 'integer'
        elif isinstance(opt, cdata.UserArrayOption):
            typestr = 'array'
        else:
            raise RuntimeError("Unknown option type")
        optdict['type'] = typestr
        optdict['description'] = opt.description
        optlist.append(optdict)

def find_buildsystem_files_list(src_dir):
    # I feel dirty about this. But only slightly.
    filelist = []
    for root, _, files in os.walk(src_dir):
        for f in files:
            if f == 'meson.build' or f == 'meson_options.txt':
                filelist.append(os.path.relpath(os.path.join(root, f), src_dir))
    return filelist

def list_buildsystem_files(builddata):
    src_dir = builddata.environment.get_source_dir()
    filelist = find_buildsystem_files_list(src_dir)
    print(json.dumps(filelist))

def list_deps(coredata):
    result = []
    for d in coredata.deps.values():
        if d.found():
            result += [{'name': d.name,
                        'compile_args': d.get_compile_args(),
                        'link_args': d.get_link_args()}]
    print(json.dumps(result))

def list_tests(testdata):
    result = []
    for t in testdata:
        to = {}
        if isinstance(t.fname, str):
            fname = [t.fname]
        else:
            fname = t.fname
        to['cmd'] = fname + t.cmd_args
        if isinstance(t.env, build.EnvironmentVariables):
            to['env'] = t.env.get_env(os.environ)
        else:
            to['env'] = t.env
        to['name'] = t.name
        to['workdir'] = t.workdir
        to['timeout'] = t.timeout
        to['suite'] = t.suite
        to['is_parallel'] = t.is_parallel
        result.append(to)
    print(json.dumps(result))

def list_projinfo(builddata):
    result = {'version': builddata.project_version,
              'descriptive_name': builddata.project_name}
    subprojects = []
    for k, v in builddata.subprojects.items():
        c = {'name': k,
             'version': v,
             'descriptive_name': builddata.projects.get(k)}
        subprojects.append(c)
    result['subprojects'] = subprojects
    print(json.dumps(result))

class ProjectInfoInterperter(astinterpreter.AstInterpreter):
    def __init__(self, source_root, subdir):
        super().__init__(source_root, subdir)
        self.funcs.update({'project': self.func_project})
        self.project_name = None
        self.project_version = None

    def func_project(self, node, args, kwargs):
        if len(args) < 1:
            raise InvalidArguments('Not enough arguments to project(). Needs at least the project name.')
        self.project_name = args[0]
        self.project_version = kwargs.get('version', 'undefined')
        if isinstance(self.project_version, mparser.ElementaryNode):
            self.project_version = self.project_version.value

    def set_variable(self, varname, variable):
        pass

    def analyze(self):
        self.load_root_meson_file()
        self.sanity_check_ast()
        self.parse_project()
        self.run()

def list_projinfo_from_source(sourcedir):
    files = find_buildsystem_files_list(sourcedir)

    result = {'buildsystem_files': []}
    subprojects = {}

    for f in files:
        f = f.replace('\\', '/')
        if f == 'meson.build':
            interpreter = ProjectInfoInterperter(sourcedir, '')
            interpreter.analyze()
            version = None
            if interpreter.project_version is str:
                version = interpreter.project_version
            result.update({'version': version, 'descriptive_name': interpreter.project_name})
            result['buildsystem_files'].append(f)
        elif f.startswith('subprojects/'):
            subproject_id = f.split('/')[1]
            subproject = subprojects.setdefault(subproject_id, {'buildsystem_files': []})
            subproject['buildsystem_files'].append(f)
            if f.count('/') == 2 and f.endswith('meson.build'):
                interpreter = ProjectInfoInterperter(os.path.join(sourcedir, 'subprojects', subproject_id), '')
                interpreter.analyze()
                subproject.update({'name': subproject_id, 'version': interpreter.project_version, 'descriptive_name': interpreter.project_name})
        else:
            result['buildsystem_files'].append(f)

    subprojects = [obj for name, obj in subprojects.items()]
    result['subprojects'] = subprojects
    print(json.dumps(result))

def run(options):
    datadir = 'meson-private'
    if options.builddir is not None:
        datadir = os.path.join(options.builddir, datadir)
    if options.builddir.endswith('/meson.build') or options.builddir.endswith('\\meson.build') or options.builddir == 'meson.build':
        sourcedir = '.' if options.builddir == 'meson.build' else options.builddir[:-11]
        if options.projectinfo:
            list_projinfo_from_source(sourcedir)
            return 0
        if options.buildoptions:
            list_buildoptions_from_source(sourcedir, options.backend)
            return 0
    if not os.path.isdir(datadir):
        print('Current directory is not a build dir. Please specify it or '
              'change the working directory to it.')
        return 1

    coredata = cdata.load(options.builddir)
    builddata = build.load(options.builddir)
    testdata = mtest.load_tests(options.builddir)
    benchmarkdata = mtest.load_benchmarks(options.builddir)

    # Install data is only available with the Ninja backend
    try:
        installdata = ninjabackend.load(options.builddir)
    except FileNotFoundError:
        installdata = None

    if options.list_targets:
        list_targets(coredata, builddata, installdata)
    elif options.list_installed:
        list_installed(installdata)
    elif options.target_files is not None:
        list_target_files(options.target_files, coredata, builddata)
    elif options.buildsystem_files:
        list_buildsystem_files(builddata)
    elif options.buildoptions:
        list_buildoptions(coredata)
    elif options.tests:
        list_tests(testdata)
    elif options.benchmarks:
        list_tests(benchmarkdata)
    elif options.dependencies:
        list_deps(coredata)
    elif options.projectinfo:
        list_projinfo(builddata)
    else:
        print('No command specified')
        return 1
    return 0
