"""Exclusive wall-clock spans and bounded, disposable process caches."""
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter

STAGES = ("usdTraversalGather", "triangulation", "geometryHashing", "bvhBuild",
          "depthCasting", "targetRays", "resultWriting")
_active = ContextVar("cctv_timings", default=None)


class Timings:
    def __init__(self):
        self.seconds = dict.fromkeys(STAGES, 0.0)
        self.stack = []

    @contextmanager
    def measure(self, name):
        start = perf_counter()
        frame = [name, 0.0]
        self.stack.append(frame)
        try:
            yield
        finally:
            elapsed = perf_counter() - start
            self.stack.pop()
            self.seconds[name] += elapsed - frame[1]
            if self.stack:
                self.stack[-1][1] += elapsed


@contextmanager
def recording(timings):
    token = _active.set(timings)
    try:
        yield
    finally:
        _active.reset(token)


@contextmanager
def span(name):
    timings = _active.get()
    if timings is None:
        yield
    else:
        with timings.measure(name):
            yield


def timed(name):
    def decorate(function):
        from functools import wraps
        @wraps(function)
        def wrapped(*args, **kwargs):
            with span(name):
                return function(*args, **kwargs)
        return wrapped
    return decorate


class ContentCache:
    """LRU limited by payload bytes and entries; keys are resolved content digests."""
    def __init__(self, max_bytes, max_entries):
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self.clear()

    def clear(self):
        self.entries = OrderedDict()
        self.bytes = 0

    def get(self, key):
        entry = self.entries.get(key)
        if entry is not None:
            self.entries.move_to_end(key)
            return entry[0]

    def put(self, key, value, size):
        if size > self.max_bytes:
            return value
        old = self.entries.pop(key, None)
        if old:
            self.bytes -= old[1]
        while self.entries and (self.bytes + size > self.max_bytes or len(self.entries) >= self.max_entries):
            _, (_, used) = self.entries.popitem(last=False)
            self.bytes -= used
        self.entries[key] = (value, size)
        self.bytes += size
        return value


def file_token(path):
    """Replacement-sensitive file identity; notices cover unsaved USD edits."""
    import os
    try:
        st = os.stat(path)
        return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns
    except OSError:
        return None


class StageMemo:
    """Bounded stage cache with selective notices and layer-stack verification.

    File metadata is an additional invalidator, never a substitute for notices:
    a layer can stay dirty across arbitrarily many unsaved edits. New prims,
    composition changes and changes at a dependency or its ancestors invalidate.
    Only outputs published by this memo may be ignored during attach/detach.
    """
    def __init__(self, stage):
        from pxr import Tf, Usd
        self.stage = stage
        self.values, self.prepared, self.reports = {}, {}, {}
        self.dependencies = set()
        self.outputs = {}
        self.signature = self.stack_signature()
        self.listener = Tf.Notice.Register(Usd.Notice.ObjectsChanged, self.changed, stage)

    def stack_signature(self):
        import hashlib
        from pxr import Sdf
        session = self.stage.GetSessionLayer()
        copy = Sdf.Layer.CreateAnonymous()
        copy.TransferContent(session)
        copy.subLayerPaths = [p for p in copy.subLayerPaths if p not in self.outputs]
        layers = tuple(sorted((l.identifier, l.dirty, file_token(l.realPath) if l.realPath else None)
                              for l in self.stage.GetUsedLayers()
                              if l != session and l.identifier not in self.outputs))
        return (layers, tuple(sorted(self.stage.GetMutedLayers())),
                hashlib.sha256(copy.ExportToString().encode()).digest())

    def invalidate(self):
        self.values.clear()
        self.prepared.clear()
        self.reports.clear()

    def refresh(self):
        current = self.stack_signature()
        if current != self.signature:
            self.invalidate()
            self.signature = current

    def changed(self, notice, sender):
        from pxr import Sdf
        resync = list(notice.GetResyncedPaths())
        info = list(notice.GetChangedInfoOnlyPaths())
        fields = {p: notice.GetChangedFields(p) for p in info}
        # Output attachment sends resyncs for its new shells/results and empty
        # info notices for their ancestors. The normalized inputs must agree.
        composition = any("subLayers" in names for names in fields.values())
        if composition:
            current = self.stack_signature()
            output_paths = {p for paths in self.outputs.values() for p in paths}
            output_only = all(any(q in output_paths for q in p.GetPrefixes()) for p in resync)
            output_only &= all(not names or set(names) <= {"subLayers", "subLayerOffsets", "customLayerData"} for p, names in fields.items())
            if current == self.signature and output_only:
                return
        relevant = False
        for path in resync + info:
            # Creating a session over reports empty info notices on existing
            # ancestors; only its actual property/resync carries input changes.
            if path.IsPrimPath() and path in fields and not fields[path]:
                continue
            prim = path.GetPrimPath()
            if prim == Sdf.Path.absoluteRootPath or (path in resync and path.IsPrimPath()):
                relevant = True
                break
            if any(dep.HasPrefix(prim) for dep in self.dependencies):
                relevant = True
                break
        if relevant or not self.dependencies:
            self.invalidate()
        # An unrelated non-obstacle property edit can alter session text or
        # layer dirtiness without changing a single gathered study input.
        self.signature = self.stack_signature()

    def published(self, layer):
        from pxr import Sdf
        paths = []
        def collect(path):
            spec = layer.GetPrimAtPath(path)
            if path.IsPropertyPath() or (spec and spec.specifier == Sdf.SpecifierDef):
                paths.append(path)
        layer.Traverse(Sdf.Path.absoluteRootPath, collect)
        self.outputs[layer.identifier] = paths
        self.signature = self.stack_signature()

    def close(self):
        self.listener.Revoke()


_stage_memos = OrderedDict()


def stage_memo(stage):
    if stage not in _stage_memos:
        while len(_stage_memos) >= 4:
            _, previous = _stage_memos.popitem(last=False)
            previous.close()
        _stage_memos[stage] = StageMemo(stage)
    _stage_memos.move_to_end(stage)
    return _stage_memos[stage]


def clear_stage_memos():
    for memo in _stage_memos.values():
        memo.close()
    _stage_memos.clear()
