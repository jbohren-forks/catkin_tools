
try:
    # Python3
    from queue import Empty
except ImportError:
    # Python2
    from Queue import Empty

import sys
import threading
import time

from catkin_tools.common import format_time_delta
from catkin_tools.common import format_time_delta_short
from catkin_tools.common import log
from catkin_tools.common import terminal_width
from catkin_tools.common import wide_log

from catkin_tools.terminal_color import ansi
from catkin_tools.terminal_color import fmt
from catkin_tools.terminal_color import sanitize
from catkin_tools.terminal_color import ColorMapper

from .jobs import JobServer

# This map translates more human reable format strings into colorized versions
_color_translation_map = {
    # 'output': 'colorized_output'

    '': fmt('@!' + sanitize('') + '@|'),

    # Job starting
    "Starting >>> {:<{}}":
    fmt(       "Starting  @!@{gf}>>>@| @!@{cf}{:<{}}@|"),

    # Job finishing
    "Finished <<< {:<{}} [ {} ]":
    fmt("@!@{kf}Finished@|  @{gf}<<<@| @{cf}{:<{}}@| [ @{yf}{}@| ]"),

    "Failed <<< {:<{}} [ {} ]":
    fmt("@!@{rf}Failed@|    @{rf}<<<@| @{cf}{:<{}}@| [ @{yf}{}@| ]"),

    # Job abandoning
    "Abandoned <<< {:<{}} [ {} ]":
    fmt("@!@{rf}Abandoned@| @{rf}<<<@| @{cf}{:<{}}@| [ @{yf}{}@| ]"),

    "Depends on failed job {}":
    fmt("@{yf}Depends on failed job @!{}@|"),

    "Depends on failed job {} via {}":
    fmt("@{yf}Depends on failed job @!{}@| @{yf}via @!{}@|"),

    # Stage finishing
    "Starting >> {}:{}":
    fmt("Starting  @{gf} >>@| @{cf}{}@|:@{bf}{}@|"),

    "Finished << {}:{}":
    fmt("@!@{kf}Finished@|  @{gf} <<@| @{cf}{}@|:@{bf}{}@|"),

    "Output << {}:{}":
    fmt("@!@{kf}Output@|    @!@{kf} <<@| @{cf}{}@|:@{bf}{}@|"),

    "Failed << {}:{:<{}} [ Exited with code {} ]":
    fmt("@!@{rf}Failed@|    @{rf} <<@| @{cf}{}@|:@{bf}{:<{}}@|[ @{yf}Exited with code @!@{yf}{}@| ]"),

    "Warnings << {}:{}":
    fmt("@!@{yf}Warnings@|  @{yf} <<@| @{cf}{}@|:@{bf}{}@|"),

    "Errors << {}:{}":
    fmt("@!@{rf}Errors@|    @{rf} <<@| @{cf}{}@|:@{bf}{}@|"),

    # Interleaved
    "[{}:{}] ":
    fmt("[@{cf}{}@|:@{bf}{}@|] "),

    # Status line
    "[{} {} s] [{}/{} complete] [{}/{} jobs]":
    fmt("[@{pf}{}@| - @{yf}{}@|] [@!@{gf}{}@|/@{gf}{}@| complete] [@!@{gf}{}@|/@{gf}{}@| jobs]"),

    "[{}:{} - {}]":
    fmt("[@{cf}{}@|:@{bf}{}@| - @{yf}{}@|]"),

    # Summary
    "[{}] Summary: All {} jobs completed successfully!":
    fmt("[{}] @/@!Summary:@| @/All @!{}@| @/jobs completed successfully!@|"),

    "[{}] Summary: {} of {} jobs completed successfully.":
    fmt("[{}] @/@!Summary:@| @/@!{}@| @/of @!{}@| @/jobs completed successfully:@|"),

    "[{}] Warnings: No completed jobs produced warnings.":
    fmt("[{}]   @/@!@{kf}Warnings:  None.@|"),

    "[{}] Warnings: {} completed jobs produced warnings.":
    fmt("[{}]   @/@!@{yf}Warnings:@|  @/@!{}@| @/completed jobs produced warnings.@|"),

    "[{}] Failed: No jobs failed.":
    fmt("[{}]   @/@!@{kf}Failed:    None.@|"),

    "[{}] Failed: {} jobs failed.":
    fmt("[{}]   @/@!@{rf}Failed:@|    @/@!{}@| @/jobs failed.@|"),

    "[{}] Abandoned: No jobs were abandoned.":
    fmt("[{}]   @/@!@{kf}Abandoned: None.@|"),

    "[{}] Abandoned: {} jobs were abandoned.":
    fmt("[{}]   @/@!@{rf}Abandoned:@| @/@!{}@| @/jobs were abandoned.@|"),

    "[{}]  - {}":
    fmt("[{}]     @{cf}{}@|"),

    "[{}] Runtime: {} total.":
    fmt("[{}] @/@!Runtime:@| @/{} total.@|")
}

color_mapper = ColorMapper(_color_translation_map)

clr = color_mapper.clr

class ConsoleStatusController(threading.Thread):

    """Status thread for displaying events to the console."""

    def __init__(
        self,
        label,
        job_labels,
        jobs,
        event_queue,
        show_stage_events=False,
        show_buffered_stdout=False,
        show_buffered_stderr=True,
        show_live_stdout=False,
        show_live_stderr=False,
        show_active_status=True,
        show_full_summary=False,
        active_status_rate=20.0):
        """
        :param label: The label for this task (build, clean, etc)
        :param job_labels: The labels to be used for the jobs (packages, tests, etc)
        :param event_queue: The event queue used by an Executor
        :param show_stage_events: Show events relating to stages in each job
        :param show_buffered_stdout: Show stdout from jobs as they finish
        :param show_buffered_stderr: Show stderr from jobs as they finish
        :param show_live_stdout: Show stdout lines from jobs as they're generated
        :param show_live_stderr: Show stdout lines from jobs as they're generated
        :param show_active_status: Periodically show a status line displaying the active jobs
        :param show_full_summary: Show lists of jobs in each termination category
        :param active_status_rate: The rate in Hz at which the status line should be printed
        """
        super(ConsoleStatusController, self).__init__()

        self.label = label
        self.job_label = job_labels[0]
        self.jobs_label = job_labels[1]
        self.event_queue = event_queue

        self.show_stage_events = show_stage_events
        self.show_buffered_stdout = show_buffered_stdout
        self.show_buffered_stderr = show_buffered_stderr
        self.show_live_stdout = show_live_stdout
        self.show_live_stderr = show_live_stderr
        self.show_active_status = show_active_status
        self.show_full_summary = show_full_summary
        self.active_status_rate = max(active_status_rate, 0.1)

        # Map from jid -> job
        self.jobs = dict([(j.jid, j) for j in jobs])

        # Compute the max job id length when combined with stage labels
        self.max_jid_length = 1+max([len(jid)+max([len(s.label) for s in job.stages] or [0]) for jid, job in self.jobs.items()])

    def run(self):
        pending_jobs = []
        queued_jobs = []
        active_jobs = []
        completed_jobs = {}
        abandoned_jobs = []
        failed_jobs = []
        warned_jobs = []

        start_times = dict()
        end_times = dict()
        active_stages = dict()

        start_time = time.time()

        # Disable the wide log padding if the status is disabled
        if not self.show_active_status:
            disable_wide_log()

        while True:
            # Write a continuously-updated status line
            if self.show_active_status:
                # Try to get an event from the queue (non-blocking)
                try:
                    event = self.event_queue.get(False)
                except Empty:
                    # Print live status (overwrites last line)
                    status_line = clr('[{} {} s] [{}/{} complete] [{}/{} jobs]').format(
                        self.label,
                        format_time_delta_short(time.time() - start_time),
                        len(completed_jobs),
                        len(self.jobs),
                        JobServer.running_jobs(),
                        JobServer.max_jobs(),
                        )
                    # Add active jobs
                    if len(active_jobs) == 0:
                        status_line += clr(' @/@!@{kf}Waiting for jobs...@|')
                    else:
                        status_line += ' '+', '.join([clr('[{}:{} - {}]').format(j,s,format_time_delta_short(time.time()-t)) for j, (s, t) in active_stages.items()])

                    # Print the status line
                    #wide_log(status_line)
                    wide_log(status_line, rhs='', end='\r')
                    sys.stdout.flush()
                    time.sleep(1.0 / self.active_status_rate)
                    continue
            else:
                # Try to get an event from the queue (blocking)
                try:
                    event = self.event_queue.get(True)
                except Empty:
                    break

            # A `None` event is a signal to terminate
            if event is None:
                break

            # Handle the received events
            eid = event.event_id

            if 'JOB_STATUS' == eid:
                pending_jobs = event.data['pending']
                queued_jobs = event.data['queued']
                active_jobs = event.data['active']
                completed_jobs = event.data['completed']
                abandoned_jobs = event.data['abandoned']

                # Check if all jobs have finished in some way
                if all([len(event.data[t]) == 0 for t in ['pending', 'queued', 'active']]):
                    break

            elif 'STARTED_JOB' == eid:
                wide_log(clr('Starting >>> {:<{}}').format(
                    event.data['job_id'],
                    self.max_jid_length))

                start_times[event.data['job_id']] = event.time

            elif 'FINISHED_JOB' == eid:
                end_times[event.data['job_id']] = event.time
                duration = format_time_delta(end_times[event.data['job_id']] - start_times[event.data['job_id']])

                if event.data['succeeded']:
                    wide_log(clr('Finished <<< {:<{}} [ {} ]').format(
                        event.data['job_id'],
                        self.max_jid_length,
                        duration))
                else:
                    failed_jobs.append(event.data['job_id'])
                    wide_log(clr('Failed <<< {:<{}} [ {} ]').format(
                        event.data['job_id'],
                        self.max_jid_length,
                        duration))

            elif 'ABANDONED_JOB' == eid:
                # Create a human-readable reason string
                if 'DEP_FAILED' == event.data['reason']:
                    direct = event.data['dep_job_id'] == event.data['direct_dep_job_id']
                    if direct:
                        reason = clr('Depends on failed job {}').format(event.data['dep_job_id'])
                    else:
                        reason = clr('Depends on failed job {} via {}').format(
                            event.data['dep_job_id'],
                            event.data['direct_dep_job_id'])
                elif 'PEER_FAILED' == event.data['reason']:
                    reason = clr('Unrelated job failed')
                elif 'MISSING_DEPS' == event.data['reason']:
                    reason = clr('Depends on unknown jobs: {}').format(
                        ', '.join([clr('@!{}@|').format(jid) for jid in event.data['dep_ids']]))

                wide_log(clr('Abandoned <<< {:<{}} [ {} ]').format(
                    event.data['job_id'],
                    self.max_jid_length,
                    reason))

            elif 'STARTED_STAGE' == eid:
                active_stages[event.data['job_id']] = (event.data['label'], event.time)
                if self.show_stage_events:
                    wide_log(clr('Starting >> {}:{}').format(
                        event.data['job_id'],
                        event.data['label']))

            elif 'FINISHED_STAGE' == eid:
                del active_stages[event.data['job_id']]

                if len(event.data['interleaved']) > 0:
                    if self.show_buffered_stdout:
                        prefix_color = '@!@{kf}'
                        #wide_log(clr(prefix_color+'/'*(terminal_width()-1)))
                        wide_log(clr('Output << {}:{}').format(
                            event.data['job_id'],
                            event.data['label']))
                        lines = event.data['interleaved'].splitlines()
                        log('\n'.join(lines[:-1]))
                        wide_log(lines[-1])
                        #wide_log(clr(prefix_color+'_'*(terminal_width()-1)))

                if len(event.data['stderr']) > 0:
                    prefix_color = '@!@{yf}' if event.data['succeeded'] else '@!@{rf}'
                    if event.data['succeeded']:
                        if event.data['job_id'] not in warned_jobs:
                            warned_jobs.append(event.data['job_id'])

                        if self.show_buffered_stderr:
                            wide_log(clr(prefix_color+'/'*(terminal_width()-1)+'@|'))
                            wide_log(clr('Warnings << {}:{}').format(
                                event.data['job_id'],
                                event.data['label']))
                    else:
                        if self.show_buffered_stderr:
                            wide_log(clr(prefix_color+'/'*(terminal_width()-1)+'@|'))
                            wide_log(clr('Errors << {}:{}').format(
                                event.data['job_id'],
                                event.data['label']))

                    if self.show_buffered_stderr:
                        prefix = ''#clr(prefix_color + '>  @|')
                        #wide_log(clr(prefix_color+'/'*(terminal_width()-1)))
                        #for line in event.data['stderr'].splitlines():
                            #wide_log(prefix + line)
                        
                        lines = event.data['stderr'].splitlines()
                        log('\n'.join(lines[:-1]))
                        wide_log(lines[-1])
                        wide_log(clr(prefix_color+'_'*(terminal_width()-1)+'@|'))

                if event.data['succeeded']:
                    if self.show_stage_events:
                        wide_log(clr('Finished << {}:{}').format(
                            event.data['job_id'],
                            event.data['label']))
                else:
                    wide_log(clr('Failed << {}:{:<{}} [ Exited with code {} ]').format(
                        event.data['job_id'],
                        event.data['label'],
                        max(0,self.max_jid_length - len(event.data['job_id'])),
                        event.data['retcode']))

            elif 'STDERR' == eid:
                if self.show_live_stderr:
                    prefix = clr('[{}:{}] ').format(
                        event.data['job_id'],
                        event.data['label'])
                    wide_log('\n'.join(prefix + l for l in event.data['data'].splitlines()))

            elif 'STDOUT' == eid:
                if self.show_live_stdout:
                    prefix = clr('[{}:{}] ').format(
                        event.data['job_id'],
                        event.data['label'])
                    wide_log('\n'.join(prefix + l for l in event.data['data'].splitlines()))

        # Print final runtime
        wide_log(clr('[{}] Runtime: {} total.').format(
            self.label,
            format_time_delta(time.time() - start_time)))

        # Print error summary
        if len(failed_jobs) == len(abandoned_jobs) == 0:
            wide_log(clr('[{}] Summary: All {} jobs completed successfully!').format(self.label, len(self.jobs)))
        else:
            wide_log(clr('[{}] Summary: {} of {} jobs completed successfully.').format(
                self.label,
                len([succeeded for jid, succeeded in completed_jobs.items() if succeeded]),
                len(self.jobs)))

        if len(failed_jobs) == 0:
            wide_log(clr('[{}] Failed: No jobs failed.').format(
                self.label))
        else:
            wide_log(clr('[{}] Failed: {} jobs failed.').format(
                self.label,
                len(failed_jobs)))
            if self.show_full_summary:
                for jid in failed_jobs:
                    wide_log(clr('[{}]  - {}').format(
                        self.label,
                        jid))

        if len(abandoned_jobs) == 0:
            wide_log(clr('[{}] Abandoned: No jobs were abandoned.').format(
                self.label))
        else:
            wide_log(clr('[{}] Abandoned: {} jobs were abandoned.').format(
                self.label,
                len(abandoned_jobs)))
            if self.show_full_summary:
                for jid in abandoned_jobs:
                    wide_log(clr('[{}]  - {}').format(
                        self.label,
                        jid))

        if len(warned_jobs) == 0:
            wide_log(clr('[{}] Warnings: No completed jobs produced warnings.').format(
                self.label))
        else:
            wide_log(clr('[{}] Warnings: {} completed jobs produced warnings.').format(
                self.label,
                len(warned_jobs)))
            if self.show_full_summary:
                for jid in warned_jobs:
                    wide_log(clr('[{}]  - {}').format(
                        self.label,
                        jid))
