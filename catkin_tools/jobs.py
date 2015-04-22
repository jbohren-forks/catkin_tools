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

from catkin_tools.utils import which

MAKE_EXEC = which('make')
CMAKE_EXEC = which('cmake')

class Command(object):

    """Single command which is part of a job"""
    lock_install_space = False
    stage_name = ''

    def __init__(self, env_loader, cmd, location):
        self.cmd = [env_loader] + cmd
        self.cmd_str = ' '.join(self.cmd)
        self.executable = os.path.basename(cmd[0])
        self.pretty = ' '.join([self.executable] + cmd[1:])
        self.plain_cmd = cmd
        self.plain_cmd_str = ' '.join(self.plain_cmd)
        self.env_loader = env_loader
        self.location = location

    def 

class SystemCommand(Command):

    def execute(self):
        """Execute the command in the pre-determined location."""
        return run_command(self.cmd, cwd=self.location)


class MakeCommand(Command):
    stage_name = 'make'

    def __init__(self, env_loader, cmd, location):
        super(MakeCommand, self).__init__(env_loader, cmd, location)

        if MAKE_EXEC is None:
            raise RuntimeError("Executable 'make' could not be found in PATH.")


class CMakeCommand(Command):
    stage_name = 'cmake'

    def __init__(self, env_loader, cmd, location):
        super(CMakeCommand, self).__init__(env_loader, cmd, location)

        if CMAKE_EXEC is None:
            raise RuntimeError("Executable 'cmake' could not be found in PATH.")


class InstallCommand(MakeCommand):

    """Command which touches the install space"""
    lock_install_space = True
    stage_name = 'make install'

    def __init__(self, env_loader, cmd, location):
        super(InstallCommand, self).__init__(env_loader, cmd, location)


class Job(object):

    """Encapsulates a job which builds a package"""

    def __init__(self, package, package_path, context, force_cmake):
        self.package = package
        self.package_path = package_path
        self.context = context
        self.force_cmake = force_cmake
        self.commands = []
        self.__command_index = 0

    def get_commands(self):
        raise NotImplementedError('get_commands')

    def __iter__(self):
        return self

    def __next__(self):
        return self.next()

    def next(self):
        if self.__command_index >= len(self.commands):
            raise StopIteration()
        self.__command_index += 1
        return self.commands[self.__command_index - 1]


