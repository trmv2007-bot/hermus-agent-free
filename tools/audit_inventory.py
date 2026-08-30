#!/usr/bin/env python3
"""Consolidation inventories (Hermus §19): import graph, routes, tools, providers, UI action seams."""
import ast, json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXCLUDE = {".venv", "__pycache__", ".git", "data", "node_modules", "artifacts"}

def iter_py(path):
    p = ROOT / path
    paths = [p] if p.is_file() else list(p.rglob("*.py"))
    for f in paths:
        s = str(f)
        if any(x in s for x in EXCLUDE):
            continue
        yield f

def func_nodes(tree):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield n

def resolve_imports(mod_path, node):
    """Best-effort absolute module name for an Import/ImportFrom node."""
    rel = str(mod_path.relative_to(ROOT))
    parts = list(rel.replace("\\", "/").split("/")[:-1])
    out = []
    if isinstance(node, ast.Import):
        for a in node.names:
            out.append(a.name)
    elif isinstance(node, ast.ImportFrom):
        base = ".".join(parts)
        if node.level and node.module:
            # relative: drop (level-1) trailing packages from package root
            drop = node.level - 1
            # correct resolution requires walking back from package; approximate
            mod_core = base.rsplit(".", node.level)[0] if node.level <= len(parts) else base
        if node.module:
            out.append(node.module.replace("/", "."))
        elif node.level == 0 and node.module:
            out.append(node.module)
    return out

imports = {}
for f in list(iter_py("core")) + list(iter_py("gateway")) + list(iter_py("tools")):
    try:
        tree = ast.parse(f.read_text())
    except Exception as e:
        imports[str(f.relative_to(ROOT))] = {"error": str(e)}
        continue
    lst = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            lst.extend(resolve_imports(f, node))
    imports[str(f.relative_to(ROOT))] = sorted(set(lst))

# Routes
routes = []
for f in iter_py("gateway"):
    try:
        tree = ast.parse(f.read_text())
    except Exception:
        continue
    for fn in func_nodes(tree):
        for dec in fn.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) \
               and dec.func.attr in ("get","post","put","delete","patch","websocket"):
                try:
                    path = ast.literal_eval(dec.args[0])
                except Exception:
                    path = None
                routes.append({"method": dec.func.attr.upper(), "path": path,
                               "handler": fn.name, "module": str(f.relative_to(ROOT))})

# Tools: find tool_registry tool decorators/register calls + ToolDescriptor-ish classes
tool_refs = []
for f in iter_py("core"):
    try:
        tree = ast.parse(f.read_text())
    except Exception:
        continue
    for fn in func_nodes(tree):
        for dec in fn.decorator_list:
            if isinstance(dec, ast.Call) and getattr(dec.func, "attr", None) in ("tool","register","register_tool"):
                tool_refs.append({"tool": fn.name, "module": str(f.relative_to(ROOT)), "decorator": dec.func.attr})

# Provider/credential seams
provider_refs = []
for f in list(iter_py("core")):
    s = f.read_text()
    for pat, label in [(r"OPENROUTER|OPENAI|GROQ|OLLAMA|GEMINI|DEEPSEEK|HF_|HUGGINGFACE", "env-key"),
                       (r"def .*provider|class .*Provider", "provider-def")]:
        for m in re.finditer(pat, s):
            provider_refs.append({"module": str(f.relative_to(ROOT)), "kind": label, "match": m.group(0)})
            break

inv = {
  "repo": "hermus-agent-free", "head": "a0dc13d",
  "module_files": len(imports),
  "import_graph": imports,
  "routes": routes, "route_count": len(routes),
  "tool_decorators": tool_refs, "tool_count": len(tool_refs),
  "provider_seams": provider_refs,
}
(ROOT/"artifacts"/"inventory.json").write_text(json.dumps(inv, indent=2))
print(f"modules={len(imports)} routes={len(routes)} tools={len(tool_refs)} providers_seams={len(provider_refs)}")
print("routes by prefix:")
from collections import Counter
pref = Counter()
for r in routes:
    p = r["path"] or ""
    pref["/".join(p.split("/")[:2])] += 1
for k,v in sorted(pref.items()):
    print(f"   {k}: {v}")
