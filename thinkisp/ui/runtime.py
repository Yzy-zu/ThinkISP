"""Trabajos de fondo del panel: nunca acceden a curses ni al estado visual."""

from queue import Empty, Queue
import threading


class PanelTasks:
    def __init__(self, poll, interval=2.0):
        self.poll = poll
        self.interval = interval
        self.results = Queue()
        self.jobs = Queue()
        self.wake = threading.Event()
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.generation = 0
        self.busy = False
        self.mutating = False
        self.paused = False
        self.threads = []

    def start(self):
        for target in (self._poll_loop, self._job_loop):
            thread = threading.Thread(target=target, daemon=True)
            self.threads.append(thread)
            thread.start()

    def submit(self, operation, callback, mutation=True):
        with self.lock:
            if self.busy or self.closed.is_set():
                return False
            self.busy = True
            self.mutating = mutation
            self.generation += 1
            self.jobs.put((operation, callback))
        return True

    def pause(self, enabled):
        with self.lock:
            self.paused = enabled
            self.generation += 1
        self.wake.set()

    def _poll_loop(self):
        while not self.closed.is_set():
            self.wake.clear()
            with self.lock:
                generation = self.generation
                allowed = not (self.mutating or self.paused)
            if allowed:
                try:
                    self.results.put(('poll', generation, self.poll(), None))
                except Exception as exc:
                    self.results.put(('poll_error', generation, str(exc), None))
            if self.closed.is_set():
                break
            self.wake.wait(self.interval)

    def _job_loop(self):
        while not self.closed.is_set():
            job = self.jobs.get()
            if job is None:
                break
            operation, callback = job
            try:
                result, error = operation(), None
            except Exception as exc:
                result, error = None, str(exc)
            # El hilo de UI confirma la finalización antes de aceptar otra acción.
            self.results.put(('job', error, result, callback))

    def drain(self):
        while True:
            try:
                yield self.results.get_nowait()
            except Empty:
                return

    def finish_job(self):
        with self.lock:
            self.busy = False
            self.mutating = False
        self.wake.set()

    def close(self):
        self.closed.set()
        self.wake.set()
        self.jobs.put(None)
