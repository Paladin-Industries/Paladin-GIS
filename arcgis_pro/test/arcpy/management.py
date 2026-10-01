import os, copy
DB = {"gdbs": set(), "tables": {}, "domains": {}}
def _exists(p): return p in DB["gdbs"] or p in DB["tables"]
def CreateFileGDB(folder, name): DB["gdbs"].add(os.path.join(folder, name))
def _new(path, geom): DB["tables"][path] = {"fields": {}, "rows": {}, "next": 1, "geom": geom, "defaults": {}, "subtypes": {}, "domains": {}}
def CreateFeatureclass(gdb, name, geom, spatial_reference=None): _new(os.path.join(gdb, name), geom)
def CreateTable(gdb, name): _new(os.path.join(gdb, name), None)
def AddField(t, name, ftype, field_length=None, field_alias=None): DB["tables"][t]["fields"][name] = (ftype, field_length)
def CreateDomain(ws, name, desc, ftype, dtype): DB["domains"].setdefault(ws, {})[name] = {}
def AddCodedValueToDomain(ws, name, code, label): DB["domains"][ws][name][code] = label
def AssignDomainToField(t, f, d): DB["tables"][t]["domains"][f] = d
def AssignDefaultToField(t, f, v): DB["tables"][t]["defaults"][f] = v
def SetSubtypeField(t, f): DB["tables"][t]["subfield"] = f
def AddSubtype(t, code, desc): DB["tables"][t]["subtypes"][code] = desc
def SetDefaultSubtype(t, code): DB["tables"][t]["defaults"][DB["tables"][t]["subfield"]] = code
def Delete(t): DB["tables"].pop(t, None)
