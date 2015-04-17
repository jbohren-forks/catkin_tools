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

import glob
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
DEVEL_MANIFEST_FILE = 'devel_manifest.txt'

def unlink_devel_products(build_space_abs):
    """
    Remove all files from the `products` dest list, as well as any empty
    directories containing those files.
    """

    # Read in devel_manifest.txt
    devel_manifest_path = os.path.join(build_space_abs, DEVEL_MANIFEST_FILE)
    with open(devel_manifest_path, 'rb') as devel_manifest:
        manifest_reader = csv.reader(devel_manifest, delimiter=' ', quotechar='"')

        # Remove all listed symlinks and empty directories
        for source_file, dest_file in manifest_reader:
            if not os.path.exists(dest_file):
                print("WARNING: Dest file doesn't exist, so it can't be removed: "+dest_file)
            elif not os.islink(dest_file):
                print("ERROR: Dest file isn't a symbolic link: "+dest_file)
                return False
            elif os.path.realpath(dest_file) != source_file:
                print("ERROR: Dest file isn't a symbolic link to the expected file: "+dest_file)
                return False
            else:
                # Remove this link
                os.unlink(dest_file)
                # Remove any non-empty directories containing this file
                os.removedirs(os.path.split(dest_file)[0])

    return True

def link_devel_products(build_space_abs, source_devel, dest_devel):
    """
    Create directories and symlinks to files
    """

    # Pair of source/dest files or directories
    products = []

    for source_path, dirs, files in os.walk(source_devel):
        # compute destination path
        dest_path = os.path.join(dest_devel, os.path.relpath(source_path, source_devel))

        # create directories in the destination develspace
        for dirname in dirs:
            source_dir = os.path.join(source_path, dirname)
            dest_dir = os.path.join(source_path, dirname)

            if not os.path.exists(dest_dir):
                # Create the dest directory if it doesn't exist
                os.path.mkdir(dest_dir)
            elif not os.path.isdir(dest_dir):
                print('ERROR: cannot create directory: '+dest_dir)
                return False

        # create symbolic links from the source to the dest
        for filename in files:
            source_file = os.path.join(source_path,filename)
            dest_file = os.path.join(dest_path,filename)

            # Store the source/dest pair
            products.append((source_file,dest_file))

            # Check if the symlink exists
            if os.path.exists(dest_file):
                if os.path.realpath(dest_file) != os.path.realpath(source_file):
                    # If the link links to a different file, update it
                    os.unlink(dest_file)
                    os.symlink(source_file, dest_file)
                else:
                    print('ERROR: cannot create file: '+dest_file)
                    return False
            else:
                # Create the symlink
                os.symlink(source_file, dest_file)

    # Write out devel_manifest.txt
    devel_manifest_path = os.path.join(build_space_abs, DEVEL_MANIFEST_FILE)

    # Save the list of symlinked files
    with open(devel_manifest_path, 'wb') as devel_manifest:
        manifest_writer = csv.writer(devel_manifest, delimiter=' ', quotechar='"')
        for source_file, dest_file in products:
            manifest_writer.writerow([source_file, dest_file])

    return True


class CatkinBuildJob(Job):

    """Job class for building catkin packages"""

    def __init__(self, package, package_path, context, force_cmake):
        Job.__init__(self, package, package_path, context, force_cmake)
        self.commands = self.get_commands()

    def get_commands(self):
        commands = []
        # Setup build variables
        pkg_dir = os.path.join(self.context.source_space_abs, self.package_path)
        build_space = create_build_space(self.context.build_space_abs, self.package.name)
        # Devel space path
        if self.context.isolate_devel:
            devel_space = os.path.join(self.context.devel_space_abs, self.package.name)
        elif self.context.link_devel:
            devel_space = os.path.join(build_space, 'devel')
        else:
            devel_space = self.context.devel_space_abs
        # Install space path
        if self.context.isolate_install:
            install_space = os.path.join(self.context.install_space_abs, self.package.name)
        else:
            install_space = self.context.install_space_abs
        # Create an environment file
        env_cmd = create_env_file(self.package, self.context)
        # CMake command
        makefile_path = os.path.join(build_space, 'Makefile')
        if not os.path.isfile(makefile_path) or self.force_cmake:
            commands.append(CMakeCommand(
                env_cmd,
                #[INSTALLWATCH_EXEC, '-o', os.path.join(self.context.build_space_abs, 'build_logs', '%s_cmake_products.log' % self.package.name)] +
                [
                    CMAKE_EXEC,
                    pkg_dir,
                    '-DCATKIN_DEVEL_PREFIX=' + devel_space,
                    '-DCMAKE_INSTALL_PREFIX=' + install_space
                ] + self.context.cmake_args,
                build_space
            ))
        else:
            commands.append(MakeCommand(env_cmd, [MAKE_EXEC, 'cmake_check_build_system'], build_space))
        # Make command
        commands.append(MakeCommand(
            env_cmd,
            [MAKE_EXEC] +
            handle_make_arguments(self.context.make_args + self.context.catkin_make_args),
            build_space
        ))
        # Make install command, if installing
        if self.context.install:
            commands.append(InstallCommand(env_cmd, [MAKE_EXEC, 'install'], build_space))
        return commands


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

        # Make command
        commands.append(MakeCommand(
            None,
            [MAKE_EXEC] + ['clean'],
            #handle_make_arguments(self.context.make_args + self.context.catkin_make_args),
            build_space
        ))

        # Catkin Config dirs
        # FIXME: Hacks away!
        config_products = [
            os.path.join(devel_space, 'lib', 'pkgconfig', '%s.pc' % self.package.name)]

        commands.append(CMakeCommand(None,[CMAKE_EXEC, '-E', 'remove', '-f'] + config_products, build_space))

        config_product_dirs = [
            os.path.join(devel_space, 'include', self.package.name),
            os.path.join(devel_space, 'lib', self.package.name),
            os.path.join(devel_space, 'share', self.package.name),
            os.path.join(devel_space, 'share', 'common-lisp', 'ros', self.package.name)]

        config_product_dirs.extend(glob.glob(os.path.join(devel_space, 'lib', 'python*', 'dist-packages', self.package.name)))

        for config_product_dir in config_product_dirs:
            commands.append(CMakeCommand(None,[CMAKE_EXEC, '-E', 'remove_directory', config_product_dir], build_space))

        return commands
