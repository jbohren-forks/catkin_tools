# Copyright 2014 Open Source Robotics Foundation, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import stat
import sys

from multiprocessing import cpu_count

from catkin_tools.argument_parsing import handle_make_arguments

from catkin_tools.runner import run_command

from catkin_tools.utils import which

from .commands.cmake import CMakeCommand
from .commands.cmake import CMAKE_EXEC
from .commands.make import MakeCommand
from .commands.make import MAKE_EXEC

from .job import create_build_space
from .job import create_env_file
from .job import Job


INSTALLWATCH_EXEC = which('installwatch')


def get_python_install_dir():
    """Returns the same value as the CMake variable PYTHON_INSTALL_DIR

    The PYTHON_INSTALL_DIR variable is normally set from the CMake file:

        catkin/cmake/python.cmake

    :returns: Python install directory for the system Python
    :rtype: str
    """
    python_install_dir = 'lib'
    if os.name != 'nt':
        python_version_xdoty = str(sys.version_info[0]) + '.' + str(sys.version_info[1])
        python_install_dir = os.path.join(python_install_dir, 'python' + python_version_xdoty)

    python_use_debian_layout = os.path.exists('/etc/debian_version')
    python_packages_dir = 'dist-packages' if python_use_debian_layout else 'site-packages'
    python_install_dir = os.path.join(python_install_dir, python_packages_dir)
    return python_install_dir

class CMakeJob(Job):

    """Job class for building plain cmake packages"""

    def __init__(self, package, package_path, context, force_cmake):
        Job.__init__(self, package, package_path, context, force_cmake)
        self.commands = self.get_commands()

    def get_multiarch(self):
        if not sys.platform.lower().startswith('linux'):
            return ''
        # this function returns the suffix for lib directories on supported systems or an empty string
        # it uses two step approach to look for multiarch: first run gcc -print-multiarch and if
        # failed try to run dpkg-architecture
        error_thrown = False
        try:
            p = subprocess.Popen(
                ['gcc', '-print-multiarch'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = p.communicate()
        except (OSError, FileNotFoundError):
            error_thrown = True
        if error_thrown or p.returncode != 0:
            try:
                out, err = subprocess.Popen(
                    ['dpkg-architecture', '-qDEB_HOST_MULTIARCH'],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
            except (OSError, FileNotFoundError):
                return ''
        # be sure to return empty string or a valid multiarch tuple
        decoded = out.decode().strip()
        assert(not decoded or decoded.count('-') == 2)
        return decoded

    def get_commands(self):
        commands = []
        # Setup build variables
        pkg_dir = os.path.join(self.context.source_space_abs, self.package_path)
        build_space = create_build_space(self.context.build_space_abs, self.package.name)
        if self.context.isolate_devel:
            devel_space = os.path.join(self.context.devel_space_abs, self.package.name)
        else:
            devel_space = self.context.devel_space_abs
        if self.context.isolate_install:
            install_space = os.path.join(self.context.install_space_abs, self.package.name)
        else:
            install_space = self.context.install_space_abs
        install_target = install_space if self.context.install else devel_space
        # Create an environment file
        env_cmd = create_env_file(self.package, self.context)
        # CMake command
        makefile_path = os.path.join(build_space, 'Makefile')
        if not os.path.isfile(makefile_path) or self.force_cmake:
            commands.append(CMakeCommand(
                env_cmd,
                [
                    CMAKE_EXEC,
                    pkg_dir,
                    '-DCMAKE_INSTALL_PREFIX=' + install_target
                ] + self.context.cmake_args,
                build_space
            ))
            commands[-1].cmd.extend(self.context.cmake_args)
        else:
            commands.append(MakeCommand(env_cmd, [MAKE_EXEC, 'cmake_check_build_system'], build_space))
        # Make command
        commands.append(MakeCommand(
            env_cmd,
            [MAKE_EXEC] + handle_make_arguments(self.context.make_args),
            build_space
        ))
        # Make install command (always run on plain cmake)
        commands.append(InstallCommand(env_cmd, [MAKE_EXEC, 'install'], build_space))
        # Determine the location of where the setup.sh file should be created
        if self.context.install:
            setup_file_path = os.path.join(install_space, 'setup.sh')
            if not self.context.isolate_install and os.path.exists(setup_file_path):
                return commands
        else:  # Create it in the devel space
            setup_file_path = os.path.join(devel_space, 'setup.sh')
            if not self.context.isolate_devel and os.path.exists(setup_file_path):
                # Do not replace existing setup.sh if devel space is merged
                return commands
        # Create the setup file other packages will source when depending on this package
        arch = self.get_multiarch()
        subs = {}
        subs['cmake_prefix_path'] = install_target + ":"
        subs['ld_path'] = os.path.join(install_target, 'lib') + ":"
        pythonpath = os.path.join(install_target, get_python_install_dir())
        subs['pythonpath'] = pythonpath + ':'
        subs['pkgcfg_path'] = os.path.join(install_target, 'lib', 'pkgconfig') + ":"
        subs['path'] = os.path.join(install_target, 'bin') + ":"
        if arch:
            subs['ld_path'] += os.path.join(install_target, 'lib', arch) + ":"
            subs['pkgcfg_path'] += os.path.join(install_target, 'lib', arch, 'pkgconfig') + ":"
        setup_file_directory = os.path.dirname(setup_file_path)
        if not os.path.exists(setup_file_directory):
            os.makedirs(setup_file_directory)
        # Create a temporary file in the setup_file_directory, so os.rename cannot fail
        tmp_dst_handle, tmp_dst_path = tempfile.mkstemp(
            dir=setup_file_directory,
            prefix=os.path.basename(setup_file_path) + '.')
        # Write the fulfilled template to the file
        data = """\
#!/usr/bin/env sh
# generated from catkin_tools.verbs.catkin_build.job python module

# remember type of shell if not already set
if [ -z "$CATKIN_SHELL" ]; then
  CATKIN_SHELL=sh
fi

# detect if running on Darwin platform
_UNAME=`uname -s`
IS_DARWIN=0
if [ "$_UNAME" = "Darwin" ]; then
  IS_DARWIN=1
fi

# Prepend to the environment
export CMAKE_PREFIX_PATH="{cmake_prefix_path}$CMAKE_PREFIX_PATH"
if [ $IS_DARWIN -eq 0 ]; then
  export LD_LIBRARY_PATH="{ld_path}$LD_LIBRARY_PATH"
else
  export DYLD_LIBRARY_PATH="{ld_path}$DYLD_LIBRARY_PATH"
fi
export PATH="{path}$PATH"
export PKG_CONFIG_PATH="{pkgcfg_path}$PKG_CONFIG_PATH"
export PYTHONPATH="{pythonpath}$PYTHONPATH"
""".format(**subs)
        os.write(tmp_dst_handle, data.encode('utf-8'))
        os.close(tmp_dst_handle)
        # Do an atomic rename with os.rename
        os.rename(tmp_dst_path, setup_file_path)
        return commands