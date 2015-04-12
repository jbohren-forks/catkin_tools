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
INSTALL_MANIFEST_FILE = 'install_manifest.txt'

class CMakeCleanJob(Job):

    """Job class for cleaning plain cmake packages"""

    def __init__(self, package, package_path, context, force_cmake):
        Job.__init__(self, package, package_path, context, force_cmake)
        self.commands = self.get_commands()

    def get_commands(self):
        commands = []
        # Setup build variables
        pkg_dir = os.path.join(self.context.source_space_abs, self.package_path)
        build_space = create_build_space(self.context.build_space_abs, self.package.name)

        # Read install manifest
        install_manifest_path = os.path.join(build_space, INSTALL_MANIFEST_FILE)
        installed_files = set()
        if os.path.exists(install_manifest_path):
            with open(install_manifest_path) as f:
                installed_files = set([line.strip() for line in f.readlines()])

        dirs_to_check = set()

        for installed_file in installed_files:
            # Make sure the file is given by an absolute path and it exists
            if not os.path.isabs(installed_file) or not os.path.exists(installed_file):
                continue

            # Add commands to remove the file or directory
            if os.path.isdir(installed_file):
                commands.append(CMakeCommand(
                    None,
                    [CMAKE_EXEC, '-E', 'remove_directory', installed_file],
                    build_space))
            else:
                commands.append(CMakeCommand(
                    None,
                    [CMAKE_EXEC, '-E', 'remove', installed_file],
                    build_space))

            # Check if directories that contain this file will be empty once it's removed
            path = installed_file
            # Only look in the devel space
            while path != self.context.devel_space_abs:
                # Pop up a directory
                path, dirname = os.path.split(path)

                # Skip if this path isn't a directory
                if not os.path.isdir(path):
                    continue

                dirs_to_check.add(path)

        # For each directory which may be empty after cleaning, visit them depth-first and count their descendants
        dir_descendants = dict()
        dirs_to_remove = set()
        for path in sorted(dirs_to_check, key=lambda k: -len(k.split(os.path.sep))):
            # Get the absolute path to all the files currently in this directory
            files = [os.path.join(path,f) for f in os.listdir(path)]
            # Filter out the files which we intend to remove
            files = [f for f in files if f not in installed_files]
            # Compute the minimum number of files potentially contained in this path
            dir_descendants[path] = sum([(dir_descendants.get(f, 1) if os.path.isdir(f) else 1) for f in files])

            # Schedule the directory for removal if removal of the given files will make it empty
            if dir_descendants[path] == 0:
                dirs_to_remove.add(path)

        for generated_dir in dirs_to_remove:
            commands.append(CMakeCommand(
                None,
                [CMAKE_EXEC, '-E', 'remove_directory', generated_dir],
                build_space))

        return commands
