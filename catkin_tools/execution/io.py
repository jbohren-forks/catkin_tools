
from osrf_pycommon.process_utils import AsyncSubprocessProtocol

from .events import ExecutionEvent


class IOBufferContainer(object):

    """A simple buffer container for use in logging."""

    def __init__(self):
        self.stdout_buffer = b""
        self.stderr_buffer = b""
        self.interleaved_buffer = b""


class IOBufferLogger(IOBufferContainer):

    """This is a logging class to be used instead of sys.stdout and sys.stderr
    in FunStage operations.

    This class also generates `stdout` and `stderr` events.
    """

    def __init__(self, job_id, label, event_queue):
        IOBufferContainer.__init__(self)
        self.job_id = job_id
        self.label = label
        self.event_queue = event_queue

    def out(self, data):
        self.stdout_buffer += data.rstrip() + '\n'
        self.interleaved_buffer += data.rstrip() + '\n'

        self.event_queue.put(ExecutionEvent(
            'STDOUT',
            job_id=self.job_id,
            label=self.label,
            data=data))

    def err(self, data):
        self.stderr_buffer += data.rstrip() + '\n'
        self.interleaved_buffer += data.rstrip() + '\n'

        self.event_queue.put(ExecutionEvent(
            'STDERR',
            job_id=self.job_id,
            label=self.label,
            data=data))


class IOBufferProtocol(IOBufferContainer, AsyncSubprocessProtocol):

    """An asyncio protocol that collects stdout and stderr.

    This class also generates `stdout` and `stderr` events.

    Since the underlying asyncio API constructs the actual protocols, this
    class provides a factory method to inject the job and stage information
    into the created protocol.
    """

    def __init__(self, job_id, label, event_queue, *args, **kwargs):
        IOBufferContainer.__init__(self)
        AsyncSubprocessProtocol.__init__(self, *args, **kwargs)
        self.job_id = job_id
        self.label = label
        self.event_queue = event_queue

    @staticmethod
    def factory(job_id, label, event_queue):
        """Factory method for constructing with job metadata."""

        def init_proxy(*args, **kwargs):
            return IOBufferProtocol(job_id, label, event_queue, *args, **kwargs)

        return init_proxy

    def on_stdout_received(self, data):
        self.stdout_buffer += data
        self.interleaved_buffer += data

        self.event_queue.put(ExecutionEvent(
            'STDOUT',
            job_id=self.job_id,
            label=self.label,
            data=data))

    def on_stderr_received(self, data):
        self.stderr_buffer += data
        self.interleaved_buffer += data

        self.event_queue.put(ExecutionEvent(
            'STDERR',
            job_id=self.job_id,
            label=self.label,
            data=data))
