import sys, types, enum
class _Any:
    def __init__(self, *a, **k): pass
    def __call__(self, *a, **k): return _Any()
    def __getattr__(self, n): return _Any()
class _Meta(type):
    def __getattr__(cls, n):
        if n.startswith("__"): raise AttributeError(n)
        return _Meta(n, (_Any,), {})
class _Mod(types.ModuleType):
    def __getattr__(self, n):
        if n.startswith("__"): raise AttributeError(n)
        return _Meta(n, (_Any,), {})
class Signal:
    def __init__(self): self.subs = []
    def connect(self, f): self.subs.append(f)
    def emit(self, *a): [f(*a) for f in self.subs]
class pyqtSignal:
    def __init__(self, *t): pass
    def __set_name__(self, o, n): self.n = "_sig_" + n
    def __get__(self, inst, o):
        if inst is None: return self
        if self.n not in inst.__dict__: inst.__dict__[self.n] = Signal()
        return inst.__dict__[self.n]
class QThread:
    def __init__(self): pass
    def start(self): self.run()
class Qgis:
    class GeometryType(enum.Enum): Point = 0; Line = 1; Polygon = 2
    class MessageLevel(enum.Enum): Info = 0; Warning = 1; Critical = 2
for name in ["qgis", "qgis.core", "qgis.gui", "qgis.PyQt", "qgis.PyQt.QtCore", "qgis.PyQt.QtGui", "qgis.PyQt.QtWidgets"]:
    sys.modules[name] = _Mod(name)
sys.modules["qgis.core"].Qgis = Qgis
sys.modules["qgis.core"].QgsMessageLog = type("L", (), {"logMessage": staticmethod(lambda *a: None)})
qc = sys.modules["qgis.PyQt.QtCore"]; qc.pyqtSignal = pyqtSignal; qc.QThread = QThread
