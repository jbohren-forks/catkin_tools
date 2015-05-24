
from __future__ import print_function

from multiprocessing import cpu_count
from tempfile import mkstemp
from termios import FIONREAD

import array
import errno
import fcntl
import os
import re
import subprocess
import time

from catkin_tools.common import log
from catkin_tools.common import version_tuple


class Job(object):

    """A Job is a series of operations, each of which is considered a "stage" of the job."""

    def __init__(self, jid, deps, stages, continue_on_failure=True):
        self.jid = jid
        self.deps = deps
        self.stages = stages
        self.continue_on_failure = continue_on_failure

    def all_deps_completed(self, completed_jobs):
        """Return True if all dependencies have been completed."""
        return all([dep_id in completed_jobs for dep_id in self.deps])

    def all_deps_succeeded(self, completed_jobs):
        """Return True if all dependencies have been completed and succeeded."""
        return all([completed_jobs.get(dep_id, False) for dep_id in self.deps])

    def any_deps_failed(self, completed_jobs):
        """Return True if any dependencies which have been completed have failed."""
        return any([not completed_jobs.get(dep_id, True) for dep_id in self.deps])


def memory_usage():
    """
    Get used and total memory usage.

    :returns: Used and total memory in bytes
    :rtype: tuple
    """

    # Handle optional psutil support
    try:
        import psutil

        psutil_version = version_tuple(psutil.__version__)
        if psutil_version < (0, 6, 0):
            usage = psutil.phymem_usage()
            used = usage.used
        else:
            usage = psutil.virtual_memory()
            used = usage.total - usage.available

        return used, usage.total

    except ImportError:
        pass

    return None, None


JOBSERVER_SUPPORT_MAKEFILE = b'''
all:
\techo $(MAKEFLAGS) | grep -- '--jobserver-fds'
'''


class JobServer:

    """
    This class implements a GNU make-compatible job server.
    """

    # Singleton jobserver
    _singleton = None

    # Flag designating whether the `make` program supports the GNU Make
    # jobserver interface
    _gnu_make_supported = False

    def __init__(self, max_jobs=None, max_load=None, max_mem=None, gnu_make_enabled=False):
        """
        :param max_jobs: the maximum number of jobs available
        :param max_load: do not dispatch additional jobs if this system load
        value is exceeded
        :param max_mem: do not dispatch additional jobs if system physical
        memory usage exceeds this value (see _set_max_mem for additional
        documentation)
        """

        assert(JobServer._singleton is None)

        self._gnu_make_enabled = False

        if not max_jobs:
            try:
                max_jobs = cpu_count()
            except NotImplementedError:
                log('@{yf}WARNING: Failed to determine the cpu_count, falling back to 1 jobs as the default.@|')
                max_jobs = 1
        else:
            max_jobs = int(max_jobs)

        self.max_jobs = max_jobs
        self.max_load = max_load
        self._set_max_mem(max_mem)

        self.job_pipe = os.pipe()

        # Initialize the pipe with max_jobs tokens
        for i in range(max_jobs):
            os.write(self.job_pipe[1], b'+')

    @staticmethod
    def _test_gnu_make_support():
        """
        Test if the system 'make' supports the job server implementation.
        """

        fd, makefile = mkstemp()
        os.write(fd, JOBSERVER_SUPPORT_MAKEFILE)
        os.close(fd)

        ret = subprocess.call(['make', '-f', makefile, '-j2'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        os.unlink(makefile)
        return (ret == 0)

    def _set_max_mem(self, max_mem):
        """
        Set the maximum memory to keep instantiating jobs.

        :param max_mem: String describing the maximum memory that can be used
        on the system. It can either describe memory percentage or absolute
        amount.  Use 'P%' for percentage or 'N' for absolute value in bytes,
        'Nk' for kilobytes, 'Nm' for megabytes, and 'Ng' for gigabytes.
        :type max_mem: str
        """

        if max_mem is None:
            self.max_mem = None
            return
        elif type(max_mem) is float or type(max_mem) is int:
            mem_percent = max_mem
        elif type(max_mem) is str:
            m_percent = re.search('([0-9]+)\%', max_mem)
            m_abs = re.search('([0-9]+)([kKmMgG]{0,1})', max_mem)

            if m_percent is None and m_abs is None:
                self.max_mem = None
                return

            if m_percent:
                mem_percent = m_abs.group(1)
            elif m_abs:
                val = float(m_abs.group(1))
                mag_symbol = m_abs.group(2)

                _, total_mem = memory_usage()

                if mag_symbol == '':
                    mag = 1.0
                elif mag_symbol.lower() == 'k':
                    mag = 1024.0
                elif mag_symbol.lower() == 'm':
                    mag = pow(1024.0, 2)
                elif mag_symbol.lower() == 'g':
                    mag = pow(1024.0, 3)

                mem_percent = 100.0 * val * mag / total_mem

        self.max_mem = max(0.0, min(100.0, float(mem_percent)))

    def _load_ok(self):
        if self.max_load is not None:
            try:
                load = os.getloadavg()
                if self._running_jobs() > 0 and load[1] > self.max_load:
                    return False
            except NotImplementedError:
                return True

        return True

    def _mem_ok(self):
        if self.max_mem is not None:
            mem_used, mem_total = memory_usage()
            mem_percent_used = 100.0 * float(mem_used) / float(mem_total)
            if self._running_jobs() > 0 and mem_percent_used > self.max_mem:
                return False

        return True

    def _conditions_ok(self):
        return self._load_ok() and self._mem_ok()

    def _acquire(self):
        """
        Obtain a job server token. Be sure to call _release() to avoid
        deadlocks.
        """
        try:
            # read a token from the job pipe
            token = os.read(self.job_pipe[0], 1)
            return token
        except OSError as e:
            if e.errno != errno.EINTR:
                raise

        return None

    def _release(self):
        """
        Write a token to the job pipe.
        """
        os.write(self.job_pipe[1], b'+')

    def _running_jobs(self):

        try:
            buf = array.array('i', [0])
            if fcntl.ioctl(self.job_pipe[0], FIONREAD, buf) == 0:
                return self.max_jobs - buf[0]
        except NotImplementedError:
            pass
        except OSError:
            pass

        return cls._singleton.max_jobs

    @classmethod
    def initialize(cls, *args, **kwargs):
        """
        Initialize the global GNU Make jobserver.

        :param max_jobs: the maximum number of jobs available
        :param max_load: do not dispatch additional jobs if this system load
        value is exceeded
        :param max_mem: do not dispatch additional jobs if system physical
        memory usage exceeds this value
        """

        # Only initialize once
        assert(cls._singleton is None)

        # Check if the jobserver is supported
        cls._gnu_make_supported = cls._test_gnu_make_support()

        if not cls._gnu_make_supported:
            log('@{yf}WARNING: Make job server not supported. The number of Make '
                'jobs may exceed the number of CPU cores.@|')
            return

        # Create the jobserver singleton
        cls._singleton = JobServer(*args, **kwargs)

    @classmethod
    def set_max_mem(cls, max_mem):
        """
        Set the maximum memory to keep instantiating jobs.

        :param max_mem: String describing the maximum memory that can be used on
        the system. It can either describe memory percentage or absolute amount.
        Use 'P%' for percentage or 'N' for absolute value in bytes, 'Nk' for
        kilobytes, 'Nm' for megabytes, and 'Ng' for gigabytes.
        :type max_mem: str
        """

        cls._singleton._set_max_mem(max_mem)

    @classmethod
    def wait_acquire(cls):
        """
        Block until a job server token is acquired, then return it.
        """

        token = None

        while token is None:
            # make sure we're observing load and memory maximums
            if not self._conditions_ok():
                time.sleep(0.01)
                continue

            # try to get a job token
            token = self._acquire()

        return token

    @classmethod
    def try_acquire(cls):
        """
        Yield None until a job server token is acquired, then yield it.
        """
        while True:
            # make sure we're observing load and memory maximums
            if cls._singleton._conditions_ok() and cls.running_jobs() < cls.max_jobs():
                # try to get a job token
                token = cls._singleton._acquire()
                yield token
            else:
                yield None

    @classmethod
    def release(cls):
        """
        Release a job server token.
        """
        cls._singleton._release()

    @classmethod
    def gnu_make_enabled(cls):
        return cls._gnu_make_supported and cls._singleton._gnu_make_supported

    @classmethod
    def gnu_make_args(cls):
        """
        Get required arguments for spawning child gnu Make processes.
        """

        if cls.gnu_make_enabled():
            return ["--jobserver-fds=%d,%d" % cls._singleton.job_pipe, "-j"]
        else:
            return []

    @classmethod
    def max_jobs(cls):
        """
        Get the maximum number of jobs.
        """

        return cls._singleton.max_jobs

    @classmethod
    def running_jobs(cls):
        """
        Try to estimate the number of currently running jobs.
        """

        if not cls._gnu_make_supported:
            return '?'

        return cls._singleton._running_jobs()


class JobGuard:

    """
    Context manager representing a jobserver job.
    """

    def __enter__(self):
        JobServer.wait_acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        JobServer.release()
        return False
