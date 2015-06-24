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

from __future__ import print_function

import os

from catkin_tools.execution.io import IOBufferProtocol

from catkin_tools.terminal_color import ansi
from catkin_tools.terminal_color import fmt
from catkin_tools.terminal_color import sanitize

from catkin_tools.utils import which

CMAKE_EXEC = which('cmake')


class CMakeIOBufferProtocol(IOBufferProtocol):

    """An asyncio protocol that collects stdout and stderr.

    This class also generates `stdout` and `stderr` events.

    Since the underlying asyncio API constructs the actual protocols, this
    class provides a factory method to inject the job and stage information
    into the created protocol.
    """

    def __init__(self, label, job_id, stage_label, event_queue, log_path, source_path, *args, **kwargs):
        super(CMakeIOBufferProtocol, self).__init__(label, job_id, stage_label, event_queue, log_path, *args, **kwargs)
        self.source_path = source_path

    def on_stdout_received(self, data):
        colored = self.color_lines(data)
        super(CMakeIOBufferProtocol, self).on_stdout_received(colored)

    def on_stderr_received(self, data):
        colored = self.color_lines(data)
        super(CMakeIOBufferProtocol, self).on_stderr_received(colored)

    def color_lines(self, data):
        """Apply colorization rules to each line in data"""
        decoded_data = data.decode('utf-8')
        # TODO: This will only work if all lines are received at once. Instead
        # of direclty splitting lines, we should buffer the data lines until
        # the last character is a line break
        lines = decoded_data.splitlines(True) # Keep line breaks
        colored_lines = [self.colorize_cmake(l) for l in lines]
        colored_data = ''.join(colored_lines)
        encoded_data = colored_data.encode('utf-8')
        return encoded_data

    @classmethod
    def factory_factory(cls, source_path):
        """Factory factory for constructing protocols that know the source path for this CMake package."""
        def factory(label, job_id, stage_label, event_queue, log_path):
            # factory is called by caktin_tools executor
            def init_proxy(*args, **kwargs):
                # init_proxy is called by asyncio
                return cls(label, job_id, stage_label, event_queue, log_path, source_path, *args, **kwargs)
            return init_proxy
        return factory

    def colorize_cmake(self, line):
        """Colorizes output from CMake

        This also prepends the source path to the locations of warnings and errors.

        :param line: one, new line terminated, line from `cmake` which needs coloring.
        :type line: str
        """
        #return line
        cline = sanitize(line)

        if len(cline.strip()) == 0:
            return cline

        if line.startswith('-- '):
            cline = '@{cf}--@| ' + cline[len('-- '):]
            if ':' in cline:
                split_cline = cline.rstrip().split(':', 1)
                cline = cline.replace(split_cline[1], '@{yf}%s@|' % split_cline[1])
        elif line.lower().startswith('warning'):
            # WARNING
            cline = fmt('@{yf}', reset=False) + cline
        elif line.startswith('CMake Warning at '):
            # CMake Warning at...
            cline = cline.replace('CMake Warning at ', '@{yf}@!CMake Warning@| at ' + self.source_path + os.path.sep)
        elif line.startswith('CMake Warning (dev) at '):
            # CMake Warning at...
            cline = cline.replace(
                'CMake Warning (dev) at ', '@{yf}@!CMake Warning (dev)@| at ' + self.source_path + os.path.sep)
        elif line.startswith('CMake Warning'):
            # CMake Warning...
            cline = cline.replace('CMake Warning', '@{yf}@!CMake Warning@|')
        elif line.startswith('ERROR:'):
            # ERROR:
            cline = cline.replace('ERROR:', '@!@{rf}ERROR:@|')
        elif line.startswith('CMake Error at '):
            # CMake Error...
            cline = cline.replace('CMake Error at ', '@{rf}@!CMake Error@| at ' + self.source_path + os.path.sep)
        elif line.startswith('CMake Error'):
            # CMake Error...
            cline = cline.replace('CMake Error', '@{rf}@!CMake Error@|')
        elif line.startswith('Call Stack (most recent call first):'):
            # CMake Call Stack
            cline = cline.replace('Call Stack (most recent call first):',
                                  '@{cf}@_Call Stack (most recent call first):@|')

        return fmt(cline, reset=False)
