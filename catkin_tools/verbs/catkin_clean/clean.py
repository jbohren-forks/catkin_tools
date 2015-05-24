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

"""This modules implements the engine for cleaning packages in parallel"""

import operator
import os
import shutil
import stat
import sys
import time
import yaml

try:
    # Python3
    from queue import Queue
except ImportError:
    # Python2
    from Queue import Queue

try:
    from catkin_pkg.packages import find_packages
    from catkin_pkg.topological_order import topological_order_packages
except ImportError as e:
    sys.exit(
        'ImportError: "from catkin_pkg.topological_order import '
        'topological_order" failed: %s\nMake sure that you have installed '
        '"catkin_pkg", and that it is up to date and on the PYTHONPATH.' % e
    )

from catkin_tools.execution.controllers import ConsoleStatusController
from catkin_tools.execution.executor import execute_jobs
from catkin_tools.execution.executor import run_until_complete
from catkin_tools.execution.jobs import Job
from catkin_tools.execution.jobs import JobServer
from catkin_tools.execution.stages import CmdStage

from catkin_tools.common import format_time_delta
from catkin_tools.common import get_cached_recursive_build_depends_in_workspace
from catkin_tools.common import get_recursive_run_depends_in_workspace
from catkin_tools.common import get_recursive_build_dependants_in_workspace
from catkin_tools.common import log
from catkin_tools.common import wide_log

from catkin_tools.jobs.catkin import catkin_clean_job
from catkin_tools.jobs.cmake import cmake_clean_job
from catkin_tools.jobs.job import get_build_type

from catkin_tools.execution.jobs import JobServer

from .color import clr


def determine_packages_to_be_cleaned(packages, context):
    """Returns list of packages which should be cleaned, and those package's deps.

    :param packages: list of packages to be built, if None all packages are built
    :type packages: list
    :param context: Workspace context
    :type context: :py:class:`catkin_tools.verbs.catkin_build.context.Context`
    :returns: tuple of packages to be built and those package's deps
    :rtype: tuple
    """
    start = time.time()

    # Get all the packages in the context source space
    # Suppress warnings since this is a utility function
    workspace_packages = find_packages(context.source_space_abs, exclude_subspaces=True, warnings=[])

    # If there are no packages raise
    if not workspace_packages:
        sys.exit("No packages were found in the source space '{0}'".format(context.source_space_abs))
    log("Found '{0}' packages in {1}."
        .format(len(workspace_packages), format_time_delta(time.time() - start)))

    # Order the packages by topology
    ordered_packages = topological_order_packages(workspace_packages)
    # Set the packages in the workspace for the context
    context.packages = ordered_packages
    # Determine the packages which should be cleaned
    packages_to_be_cleaned = []
    packages_to_be_cleaned_deps = []

    # Determine the packages to be cleaned
    # First assert all of the packages given are in the workspace
    workspace_package_names = dict([(pkg.name, (path, pkg)) for path, pkg in ordered_packages])
    for package in packages:
        # This is ok if it's orphaned
        if package not in workspace_package_names:
            continue
        # If metapackage, include run depends which are in the workspace
        package_obj = workspace_package_names[package][1]
        if 'metapackage' in [e.tagname for e in package_obj.exports]:
            for rdep in package_obj.run_depends:
                if rdep.name in workspace_package_names:
                    packages.append(rdep.name)
    # Limit the packages to be cleaned to just the provided packages
    for pkg_path, package in ordered_packages:
        if package.name in packages:
            packages_to_be_cleaned.append((pkg_path, package))
            # Get the packages that depend on the packages to be cleaned
            pkg_deps = get_recursive_build_dependants_in_workspace(package.name, ordered_packages)
            packages_to_be_cleaned_deps.extend(pkg_deps)

    return packages_to_be_cleaned, packages_to_be_cleaned_deps, ordered_packages


def clean_job_factory(context, path, package, force_cmake):
    job = None
    build_type = get_build_type(package)
    if build_type == 'catkin':
        job = CatkinCleanJob(context, package.name)
    elif build_type == 'cmake':
        job = CMakeCleanJob(context, package.name)
    return job


def clean_packages(
        context,
        packages_to_be_cleaned,
        build=True,
        devel=False,
        install=False):

    # print(packages_to_be_cleaned)

    # for path, pkg in packages_to_be_cleaned:
        # if os.path.exists(os.path.join(context.build_space_abs, pkg.name)):
            # print("[clean] Cleaning package: %s" % pkg.name)

    # Use install_manifests to remove files from installspace
    if install:
        # TODO: Create jobs for this
        pass

    if devel:
        # Construct jobs
        jobs = []
        for pkg_path, pkg in packages_to_be_cleaned:
            # Ignore metapackages
            if 'metapackage' in [e.tagname for e in pkg.exports]:
                continue

            # Get actual execution deps
            all_deps = pkg.buildtool_depends + pkg.build_depends + pkg.build_export_depends
            deps = [d for d in all_deps if d in [p.name for _, p in packages_to_be_cleaned]]

            # Create the job depends on the build type
            build_type = get_build_type(pkg)
            if build_type == 'catkin':
                jobs.append(catkin_clean_job(context, pkg.name, deps))
            elif 0 and build_type == 'cmake':
                jobs.append(cmake_build_job(context, pkg.name, deps))
            else:
                wide_log("[build] Skipping package '{}' because it has an unknown package build type: \"{}\"".format(pkg.name, build_type))

        # Print jobs TODO: remove this / make it a debug option
        if 0:
            for job in jobs:
                print('{}'.format(job.jid))
                for stage in job.stages:
                    print('  - {} {}'.format(stage.label, type(stage)))

        # Initialize jobserver
        JobServer.initialize()

        # Queue for communicating status
        event_queue = Queue()

        try:
            # Spin up status output thread
            status_thread = ConsoleStatusController(
                'build',
                ['package', 'packages'],
                jobs,
                event_queue)
            status_thread.start()

            # Block while running N jobs asynchronously
            run_until_complete(execute_jobs(
                jobs,
                event_queue,
                continue_on_failure=True,
                continue_without_deps=False))

        except KeyboardInterrupt:
            wide_log("[build] User interrupted!")
            event_queue.put(None)

    # Remove build directories
    # TODO: Create jobs for this
    if build:
        for path, pkg in packages_to_be_cleaned:
            build_space = os.path.join(context.build_space_abs, pkg.name)
            if os.path.exists(build_space):
                log("[%s] Removing package buildspace: %s" % (pkg.name, build_space))
                shutil.rmtree(build_space)
            else:
                log("[%s] Package buildspace is empty: %s" % (pkg.name, build_space))
