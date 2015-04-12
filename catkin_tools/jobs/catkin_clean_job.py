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

class CatkinCleanJob(Job):

    """Job class for building catkin packages"""

    def __init__(self, package, package_path, context, force_cmake):
        Job.__init__(self, package, package_path, context, force_cmake)
        self.commands = self.get_commands()

    def get_commands(self):
        commands = []
        # Setup build variables
        pkg_dir = os.path.join(self.context.source_space_abs, self.package_path)
        # Check if the build space exists
        build_space = os.path.join(self.context.build_space_abs, self.package.name)
        if not os.path.exists(build_space):
            return commands

        # For isolated devel space, remove it entirely
        if self.context.isolate_devel:
            devel_space = os.path.join(self.context.devel_space_abs, self.package.name)
            commands.append(CMakeCommand(None,[CMAKE_EXEC, '-E', 'remove_directory', devel_space], build_space))
            return commands
        else:
            devel_space = self.context.devel_space_abs

        # For isolated install space, remove it entirely
        if self.context.isolate_install:
            install_space = os.path.join(self.context.install_space_abs, self.package.name)
            commands.append(CMakeCommand(None,[CMAKE_EXEC, '-E', 'remove_directory', install_space], build_space))
            return commands
        else:
            install_space = self.context.install_space_abs

        # CMake command
        makefile_path = os.path.join(build_space, 'Makefile')
        # Make command
        commands.append(MakeCommand(
            None,
            [MAKE_EXEC] + ['clean'],
            #handle_make_arguments(self.context.make_args + self.context.catkin_make_args),
            build_space
        ))
        return commands
