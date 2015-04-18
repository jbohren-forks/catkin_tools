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

import csv
import glob
import os
import stat
import sys
import threading

from multiprocessing import cpu_count

from catkin_tools.argument_parsing import handle_make_arguments

from catkin_tools.runner import run_command

from catkin_tools.utils import which

from .commands.cmake import CMakeCommand
from .commands.cmake import CMAKE_EXEC
from .commands.make import MakeCommand
from .commands.make import MAKE_EXEC
from .commands.python_command import PythonCommand

from .job import create_build_space
from .job import create_env_file
from .job import Job

from .catkin_templates import *

DEVEL_MANIFEST_FILENAME = 'devel_manifest.txt'
DOT_CATKIN_FILENAME = '.catkin'

devel_product_blacklist = [
    DOT_CATKIN_FILENAME,
    DOT_ROSINSTALL_FILNAME,
    ENV_SH_FILENAME,
    SETUP_BASH_FILENAME,
    SETUP_ZSH_FILENAME,
    SETUP_SH_FILENAME,
    SETUP_UTIL_PY_FILENAME]

CATKIN_TOOLS_COLLISIONS_FILENAME = '.catkin_tools_collisions'

# Synchronize access to the .catkin file
dot_catkin_file_lock = threading.Lock()
dest_collisions_file_lock = threading.Lock()


def append_dot_catkin_file(devel_space_abs, package_source_abs):
    """
    Append the package source path to the .catkin file in the merged devel space

    This is normally done by catkin.
    """

    with dot_catkin_file_lock:
        if not os.path.exists(devel_space_abs):
            os.mkdir(devel_space_abs)
        dot_catkin_filename_abs = os.path.join(devel_space_abs, DOT_CATKIN_FILENAME)
        if os.path.exists(dot_catkin_filename_abs):
            with open(dot_catkin_filename_abs, 'r') as dot_catkin_file:
                dot_catkin_paths = dot_catkin_file.read().split(';')
            if package_source_abs not in dot_catkin_paths:
                with open(dot_catkin_filename_abs, 'ab') as dot_catkin_file:
                    dot_catkin_file.write(';%s' % package_source_abs)
        else:
            with open(dot_catkin_filename_abs, 'w+') as dot_catkin_file:
                dot_catkin_file.write(package_source_abs)
    return 0


def clear_dot_catkin_file(devel_space_abs, package_source_abs):
    """
    Remove a package source path from the .catkin file in the merged devel space
    """
    with dot_catkin_file_lock:
        dot_catkin_filename_abs = os.path.join(devel_space_abs, DOT_CATKIN_FILENAME)
        if os.path.exists(dot_catkin_filename_abs):
            dot_catkin_paths = []
            with open(dot_catkin_filename_abs, 'r') as dot_catkin_file:
                dot_catkin_paths = dot_catkin_file.read().split(';')
            if package_source_abs in dot_catkin_paths:
                dot_catkin_paths = [p for p in dot_catkin_paths if p != package_source_abs]
                with open(dot_catkin_filename_abs, 'wb') as dot_catkin_file:
                    dot_catkin_file.write(';'.join(dot_catkin_paths))
    return 0


def generate_setup_files(devel_space_abs):
    """
    Generate catkin setup files if they don't exist.

    This is normally done by catkin.
    """

    dot_rosinstall_file_path = os.path.join(devel_space_abs, DOT_ROSINSTALL_FILNAME)
    env_sh_file_path = os.path.join(devel_space_abs, ENV_SH_FILENAME)
    setup_bash_file_path = os.path.join(devel_space_abs, SETUP_BASH_FILENAME)
    setup_sh_file_path = os.path.join(devel_space_abs, SETUP_SH_FILENAME)
    setup_zsh_file_path = os.path.join(devel_space_abs, SETUP_ZSH_FILENAME)

    if not os.path.exists(dot_rosinstall_file_path):
        with open(dot_rosinstall_file_path, 'wb') as dot_rosinstall_file:
            dot_rosinstall_file.write(
                DOT_ROSINSTALL_FILE_TEMPLATE.replace(
                    '@SETUP_DIR@', devel_space_abs))

    if not os.path.exists(env_sh_file_path):
        with open(env_sh_file_path, 'wb') as env_sh_file:
            env_sh_file.write(
                ENV_SH_FILE_TEMPLATE.replace(
                    '@SETUP_FILENAME@', SETUP_SH_FILENAME_STEM))

    if not os.path.exists(setup_bash_file_path):
        with open(setup_bash_file_path, 'wb') as setup_bash_file:
            setup_bash_file.write(SETUP_BASH_FILE_TEMPLATE)

    if not os.path.exists(setup_sh_file_path):
        with open(setup_sh_file_path, 'wb') as setup_sh_file:
            setup_sh_file.write(
                SETUP_SH_FILE_TEMPLATE.replace(
                    '@SETUP_DIR@', devel_space_abs))

    if not os.path.exists(setup_zsh_file_path):
        with open(setup_zsh_file_path, 'wb') as setup_zsh_file:
            setup_zsh_file.write(SETUP_ZSH_FILE_TEMPLATE)

    return 0


def unlink_devel_products(build_space_abs, dest_devel):
    """
    Remove all files from the `products` dest list, as well as any empty
    directories containing those files.
    """

    # List of files to clean
    files_to_clean = []

    # Read in devel_manifest.txt
    devel_manifest_path = os.path.join(build_space_abs, DEVEL_MANIFEST_FILENAME)
    with open(devel_manifest_path, 'rb') as devel_manifest:
        manifest_reader = csv.reader(devel_manifest, delimiter=' ', quotechar='"')

        # Remove all listed symlinks and empty directories
        for source_file, dest_file in manifest_reader:
            if not os.path.exists(dest_file):
                print("WARNING: Dest file doesn't exist, so it can't be removed: " + dest_file)
            elif not os.path.islink(dest_file):
                print("ERROR: Dest file isn't a symbolic link: " + dest_file)
                return -1
            elif False and os.path.realpath(dest_file) != source_file:
                print("ERROR: Dest file isn't a symbolic link to the expected file: " + dest_file)
                return -1
            else:
                # Clean the file or decrement the collision count
                files_to_clean.append(dest_file)

    # Remove all listed symlinks and empty directories which have been removed
    # after this build, and update the collision file
    clean_files(dest_devel, [], files_to_clean)

    return 0


def clean_files(dest_devel, files_that_collide, files_to_clean):
    """
    Removes a list of files or decrements collison counts for colliding files.

    Synchronized.
    """

    with dest_collisions_file_lock:
        # Map from dest files to number of collisions
        dest_collisions = dict()

        # Load destination collisions file
        collisions_file_path = os.path.join(dest_devel, CATKIN_TOOLS_COLLISIONS_FILENAME)
        if os.path.exists(collisions_file_path):
            with open(collisions_file_path, 'rb') as collisions_file:
                collisions_reader = csv.reader(collisions_file, delimiter=' ', quotechar='"')
                dest_collisions = dict([(path, int(count)) for path, count in collisions_reader])

        # Add collisions
        for dest_file in files_that_collide:
            if dest_file in dest_collisions:
                dest_collisions[dest_file] += 1
            else:
                dest_collisions[dest_file] = 1

        # Remove files that no longer collide
        for dest_file in files_to_clean:
            # Get the collisions
            n_collisions = dest_collisions.get(dest_file, 0)

            # Check collisions
            if n_collisions == 0:
                print('Unlinking %s' % (dest_file))
                # Remove this link
                os.unlink(dest_file)
                # Remove any non-empty directories containing this file
                try:
                    os.removedirs(os.path.split(dest_file)[0])
                except OSError:
                    pass

            # Update collisions
            if n_collisions > 1:
                # Decrement the dest collisions dict
                dest_collisions[dest_file] -= 1
            elif n_collisions == 1:
                # Remove it from the dest collisions dict
                del dest_collisions[dest_file]

        # Load destination collisions file
        collisions_file_path = os.path.join(dest_devel, CATKIN_TOOLS_COLLISIONS_FILENAME)
        with open(collisions_file_path, 'wb') as collisions_file:
            collisions_writer = csv.writer(collisions_file, delimiter=' ', quotechar='"')
            for dest_file, count in dest_collisions.items():
                collisions_writer.writerow([dest_file, count])


def link_devel_products(build_space_abs, source_devel, dest_devel):
    """
    Create directories and symlinks to files
    """

    # Pair of source/dest files or directories
    products = list()
    # List of files to clean
    files_to_clean = []
    # List of files that collide
    files_that_collide = []

    # Gather all of the files in the devel space
    for source_path, dirs, files in os.walk(source_devel):
        # compute destination path
        dest_path = os.path.join(dest_devel, os.path.relpath(source_path, source_devel))

        # create directories in the destination develspace
        for dirname in dirs:
            source_dir = os.path.join(source_path, dirname)
            dest_dir = os.path.join(dest_path, dirname)

            if not os.path.exists(dest_dir):
                # Create the dest directory if it doesn't exist
                os.mkdir(dest_dir)
            elif not os.path.isdir(dest_dir):
                print('ERROR: cannot create directory: ' + dest_dir)
                return -1

        # create symbolic links from the source to the dest
        for filename in files:

            if source_path == source_devel and filename in devel_product_blacklist:
                continue

            source_file = os.path.join(source_path, filename)
            dest_file = os.path.join(dest_path, filename)

            # Store the source/dest pair
            products.append((source_file, dest_file))

            # Check if the symlink exists
            if os.path.exists(dest_file):
                if os.path.realpath(dest_file) != os.path.realpath(source_file):
                    # If the link links to a different file, report a warning and increment
                    # the collision counter for this path
                    print('WARNING: Cannot symlink from %s to existing file %s' % (source_file, dest_file))
                    # Increment link collision counter
                    files_that_collide.append(dest_file)
            else:
                # Create the symlink
                print('Symlinking from %s to %s' % (source_file, dest_file))
                os.symlink(source_file, dest_file)

    devel_manifest_path = os.path.join(build_space_abs, DEVEL_MANIFEST_FILENAME)

    # Load the old list of symlinked files for this package
    if os.path.exists(devel_manifest_path):
        with open(devel_manifest_path, 'rb') as devel_manifest:
            manifest_reader = csv.reader(devel_manifest, delimiter=' ', quotechar='"')

            for source_file, dest_file in manifest_reader:
                print('Checking (%s, %s)' % (source_file, dest_file))
                if (source_file, dest_file) not in products:
                    # Clean the file or decrement the collision count
                    print('Cleaning (%s, %s)' % (source_file, dest_file))
                    files_to_clean.append(dest_file)

    # Remove all listed symlinks and empty directories which have been removed
    # after this build, and update the collision file
    clean_files(dest_devel, files_that_collide, files_to_clean)

    # Save the list of symlinked files
    with open(devel_manifest_path, 'wb') as devel_manifest:
        manifest_writer = csv.writer(devel_manifest, delimiter=' ', quotechar='"')
        for source_file, dest_file in products:
            manifest_writer.writerow([source_file, dest_file])

    return 0


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

        # Symlink command if linked devel
        if self.context.link_devel:
            commands.extend([
                PythonCommand(
                    append_dot_catkin_file,
                    {'devel_space_abs': self.context.devel_space_abs,
                     'package_source_abs': os.path.join(self.context.source_space_abs, self.package_path)},
                    build_space),
                PythonCommand(
                    generate_setup_files,
                    {'devel_space_abs': self.context.devel_space_abs},
                    build_space),
                PythonCommand(
                    link_devel_products,
                    {'build_space_abs': os.path.join(self.context.build_space_abs, self.package.name),
                     'source_devel': devel_space,
                     'dest_devel': self.context.devel_space_abs},
                    build_space),
            ])

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
            commands.append(CMakeCommand(None, [CMAKE_EXEC, '-E', 'remove_directory', devel_space], build_space))
            return commands
        elif self.context.link_devel:
            devel_space = os.path.join(build_space, 'devel')
        else:
            devel_space = self.context.devel_space_abs

        # For isolated install space, remove it entirely
        if self.context.isolate_install:
            install_space = os.path.join(self.context.install_space_abs, self.package.name)
            commands.append(CMakeCommand(None, [CMAKE_EXEC, '-E', 'remove_directory', install_space], build_space))
            return commands
        else:
            install_space = self.context.install_space_abs

        # Symlink command if linked devel
        if self.context.link_devel:
            commands.extend([
                PythonCommand(
                    unlink_devel_products,
                    {'build_space_abs': os.path.join(self.context.build_space_abs, self.package.name),
                     'dest_devel': self.context.devel_space_abs},
                    build_space),
                PythonCommand(
                    clear_dot_catkin_file,
                    {'devel_space_abs': self.context.devel_space_abs,
                     'package_source_abs': os.path.join(self.context.source_space_abs, self.package_path)},
                    build_space),
            ])

        return commands
