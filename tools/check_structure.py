#!/usr/bin/env python3
"""The import graph of `src/repwise`, and what it says about the boundaries.

Exits non-zero on an import cycle or an import that crosses a boundary
`docs/architecture.md` forbids, which is what makes it usable as a hook. The
sections after those two are advisory and never fail the run: module size, the
literals spelled out twice, and the modules with no test module of their own
are all things for a person to weigh.

Answers the questions a person cannot answer reliably by reading 29 modules:
which imports form a cycle, which cross a boundary the architecture says they
may not, which modules are doing too many jobs, and which string literals are
spelled out in more than one place.

Everything here is mechanical. What it means is not: a module with a wide fan-in
may be a shared vocabulary or a god object, and only reading it says which.
"""

import argparse
import ast
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# --- The architecture, as rules -------------------------------------------
#
# Ranks come from docs/architecture.md: every arrow points inward, so a module
# may import its own rank or lower and never higher. Change the doc and change
# this table, or the audit is checking yesterday's design.

RANKS: list[tuple[str, int, str]] = [
    # (module prefix, rank, layer name) - first match wins, so order matters.
    ("__main__", 7, "entry"),
    ("cli", 6, "cli"),
    ("app", 5, "app"),
    ("config", 4, "services"),
    ("planner", 4, "services"),
    ("importer", 4, "services"),
    ("checker", 4, "services"),
    ("garmin", 3, "adapter"),
    ("yamlio", 2, "io"),
    ("dumps", 2, "io"),
    ("errors", 1, "leaf"),
    ("log", 1, "leaf"),
    ("domain", 0, "domain"),
    ("repwise", 0, "root"),
    ("", 0, "root"),  # repwise/__init__.py: the version string, nothing else
]

#: Third-party or stdlib imports that only some modules may make, and why.
EXTERNAL_RULES: list[tuple[str, tuple[str, ...], str]] = [
    ("garminconnect", ("garmin",), "the garminconnect dependency stays in one place"),
    ("yaml", ("yamlio",), "yamlio.py is the only module that touches the file"),
    ("argparse", ("cli",), "argparse lives in cli/ and nowhere else"),
]

#: Internal modules that only some modules may import, and why.
INTERNAL_RULES: list[tuple[str, tuple[str, ...], str]] = [
    ("log", ("cli",), "modules log through the stdlib; only main() configures"),
]

#: What `domain/` may not reach for. It takes plain data and returns plain data.
DOMAIN_FORBIDDEN = {
    "os",
    "io",
    "json",
    "csv",
    "pathlib",
    "shutil",
    "tempfile",
    "socket",
    "urllib",
    "http",
    "subprocess",
    "sqlite3",
    "pickle",
    "yaml",
    "requests",
    "garminconnect",
}

#: A module this long is doing more than one job, or one job at too fine a
#: grain. Not a failure - a place to look.
LONG_MODULE = 400
LONG_FUNCTION = 60
DEEP_NESTING = 4

#: A literal shorter than this is a word, not a key worth naming once.
LITERAL_LENGTH = 4

#: Below this a module is a re-export or a stub, and its tests are elsewhere.
TESTABLE_LINES = 10


def layer(module: str) -> tuple[int, str]:
    head = module.split(".", 1)[0]
    for prefix, rank, name in RANKS:
        if prefix in (head, ""):
            return rank, name
    return 0, "root"


@dataclass
class Module:
    name: str
    path: Path
    package: bool = False
    lines: int = 0
    imports: set[str] = field(default_factory=set)
    external: set[str] = field(default_factory=set)
    defs: int = 0
    classes: int = 0
    longest: tuple[str, int] = ("", 0)
    deepest: tuple[str, int] = ("", 0)
    literals: list[str] = field(default_factory=list)


def module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or root.name


def targets(node: ast.ImportFrom, owner: str, package: bool, root: str) -> set[str]:
    """Every module `from ... import ...` could name, for the caller to filter.

    Two shapes to cover. `from ..config import load_config` names the module in
    `node.module`; `from .. import dumps` names it in the alias, and telling
    those apart needs the module list, which the caller has and this does not.
    So both are returned and the ones that are not modules fall out.

    A package's `__init__.py` *is* its package, so one dot there means the
    package itself; in any other module one dot means the package it sits in.
    """
    parts = [p for p in (owner.split(".") if package else owner.split(".")[:-1]) if p]
    if parts and parts[0] == root:
        parts = parts[1:]
    up = node.level - 1
    base = parts[: len(parts) - up] if up else parts
    prefix = [*base, *(node.module.split(".") if node.module else [])]
    found = {".".join(prefix) or root}
    found |= {".".join([*prefix, alias.name]) for alias in node.names}
    return found


def body_depth(node: ast.AST, depth: int = 0) -> int:
    """How deeply the compound statements in a function nest."""
    nests = (ast.If, ast.For, ast.While, ast.With, ast.Try, ast.Match)
    best = depth
    for child in ast.iter_child_nodes(node):
        step = depth + 1 if isinstance(child, nests) else depth
        best = max(best, body_depth(child, step))
    return best


def gather_imports(tree: ast.Module, mod: Module, root: str) -> None:
    """What the module imports, and every string literal it spells out."""
    documented = ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    docstrings = {
        ast.get_docstring(n) for n in ast.walk(tree) if isinstance(n, documented)
    }
    for node in ast.walk(tree):
        match node:
            case ast.Import():
                mod.external |= {a.name.split(".", 1)[0] for a in node.names}
            case ast.ImportFrom() if node.level:
                mod.imports |= targets(node, mod.name, mod.package, root)
            case ast.ImportFrom() if node.module:
                mod.external.add(node.module.split(".", 1)[0])
            case ast.Constant(value=str() as text) if "\n" not in text:
                if len(text) >= LITERAL_LENGTH and text not in docstrings:
                    mod.literals.append(text)


def gather_shape(tree: ast.Module, mod: Module) -> None:
    """How big the module is, and where its longest and deepest function is."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            mod.classes += 1
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            mod.defs += 1

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        span = (node.end_lineno or node.lineno) - node.lineno + 1
        if span > mod.longest[1]:
            mod.longest = (node.name, span)
        deep = body_depth(node)
        if deep > mod.deepest[1]:
            mod.deepest = (node.name, deep)


def read(path: Path, root: Path) -> Module:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    mod = Module(
        name=module_name(path, root), path=path, package=path.name == "__init__.py"
    )
    mod.lines = sum(
        1
        for line in source.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )
    gather_imports(tree, mod, root.name)
    gather_shape(tree, mod)
    return mod


def cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Every strongly connected component with more than one module in it."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    found: list[list[str]] = []
    counter = 0

    def walk(node: str) -> None:
        nonlocal counter
        index[node] = low[node] = counter
        counter += 1
        stack.append(node)
        on.add(node)
        for peer in sorted(graph.get(node, ())):
            if peer not in index:
                walk(peer)
                low[node] = min(low[node], low[peer])
            elif peer in on:
                low[node] = min(low[node], index[peer])
        if low[node] == index[node]:
            group = []
            while True:
                top = stack.pop()
                on.discard(top)
                group.append(top)
                if top == node:
                    break
            if len(group) > 1:
                found.append(sorted(group))

    for node in sorted(graph):
        if node not in index:
            walk(node)
    return found


def allowed(module: str, prefixes: tuple[str, ...]) -> bool:
    return module.split(".", 1)[0] in prefixes


def report_cycles(graph: dict[str, set[str]]) -> list[str]:
    out = []
    for group in cycles(graph):
        out.append(f"- CYCLE: {' -> '.join(group)} -> {group[0]}")
        for name in group:
            inside = sorted(graph[name] & set(group))
            out.append(f"    {name} imports {', '.join(inside)}")
    return out


def report_boundaries(mods: dict[str, Module], graph: dict[str, set[str]]) -> list[str]:
    out: list[str] = []
    for name, mod in sorted(mods.items()):
        rank, where = layer(name)
        for target in sorted(graph[name]):
            trank, twhere = layer(target)
            if trank > rank:
                out.append(
                    f"- INWARD: {name} ({where}, {rank}) imports "
                    f"{target} ({twhere}, {trank}) - arrows point inward only"
                )
        for dep, prefixes, why in EXTERNAL_RULES:
            if dep in mod.external and not allowed(name, prefixes):
                out.append(f"- EXTERNAL: {name} imports {dep} - {why}")
        for dep, prefixes, why in INTERNAL_RULES:
            if dep in graph[name] and not allowed(name, prefixes):
                out.append(f"- INTERNAL: {name} imports {dep} - {why}")
        impure = sorted(mod.external & DOMAIN_FORBIDDEN) if where == "domain" else []
        if impure:
            out.append(
                f"- IMPURE: {name} imports {', '.join(impure)} - domain/ does no I/O"
            )
    return out


def report_table(
    mods: dict[str, Module], graph: dict[str, set[str]], fan_in: Counter[str]
) -> None:
    print(
        f"{'module':<26} {'loc':>5} {'in':>3} {'out':>4} "
        f"{'defs':>5} {'longest fn':>22} {'deepest':>18}"
    )
    for name, mod in sorted(mods.items(), key=lambda kv: -kv[1].lines):
        if not mod.lines:
            continue
        long_fn = f"{mod.longest[0]}:{mod.longest[1]}" if mod.longest[1] else "-"
        deep = f"{mod.deepest[0]}:{mod.deepest[1]}" if mod.deepest[1] else "-"
        print(
            f"{name:<26} {mod.lines:>5} {fan_in[name]:>3} {len(graph[name]):>4} "
            f"{mod.defs + mod.classes:>5} {long_fn:>22} {deep:>18}"
        )
    print()


def report_thresholds(mods: dict[str, Module]) -> list[str]:
    out = []
    for name, mod in sorted(mods.items()):
        if mod.lines > LONG_MODULE:
            out.append(f"- LONG: {name} is {mod.lines} lines - one job, or several?")
        if mod.longest[1] > LONG_FUNCTION:
            out.append(f"- LONG FN: {name}.{mod.longest[0]} is {mod.longest[1]} lines")
        if mod.deepest[1] > DEEP_NESTING:
            out.append(f"- NESTED: {name}.{mod.deepest[0]} nests {mod.deepest[1]} deep")
    return out


def report_literals(mods: dict[str, Module]) -> list[str]:
    where_used: dict[str, set[str]] = defaultdict(set)
    counts: Counter[str] = Counter()
    for name, mod in mods.items():
        for text in mod.literals:
            where_used[text].add(name)
            counts[text] += 1
    shared = {t: m for t, m in where_used.items() if len(m) > 1}
    ranked = sorted(shared.items(), key=lambda kv: -counts[kv[0]])[:30]
    return [
        f'- {counts[text]}x  "{text}"  in {", ".join(sorted(users))}'
        for text, users in ranked
    ]


def report_tests(repo: Path, mods: dict[str, Module]) -> list[str]:
    tests = {p.stem for p in (repo / "tests").glob("test_*.py")}
    out = []
    for name, mod in sorted(mods.items()):
        if mod.package or mod.lines < TESTABLE_LINES or name.endswith("__main__"):
            continue
        stem = name.split(".")[-1]
        if f"test_{stem}" not in tests:
            out.append(f"- {name} ({mod.lines} lines) - no tests/test_{stem}.py")
    return out


def section(title: str, findings: list[str], clean: str) -> None:
    print(f"## {title}\n")
    print("\n".join(findings) if findings else clean)
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".", help="the repwise checkout")
    args = ap.parse_args()

    repo = Path(args.root).resolve()
    src = repo / "src" / "repwise"
    if not src.is_dir():
        print(f"no src/repwise under {repo}", file=sys.stderr)
        return 2

    mods = {m.name: m for m in (read(p, src) for p in sorted(src.rglob("*.py")))}
    graph = {name: {i for i in m.imports if i in mods} for name, m in mods.items()}
    fan_in: Counter[str] = Counter()
    for edges in graph.values():
        fan_in.update(edges)

    print("# Structure\n")
    print(f"{len(mods)} modules, {sum(m.lines for m in mods.values())} lines of code\n")

    # Only these two decide the exit code. Everything below is a pointer for a
    # person to weigh - a module being long is not a failure, and a hook that
    # fails on a judgement call is a hook people learn to skip.
    breaks = report_cycles(graph)
    crossings = report_boundaries(mods, graph)
    section("Import cycles", breaks, "None. Every import points one way.")
    section("Boundary violations", crossings, "None. Every documented rule holds.")

    print("## Module shape\n")
    report_table(mods, graph, fan_in)
    print("\n".join(report_thresholds(mods)) or "Nothing over the thresholds.")
    print()

    section(
        "Literals spelled out in more than one module", report_literals(mods), "None."
    )

    print("## Source modules without a test module of their own\n")
    print("A pointer, not a verdict - one module is covered by another's tests")
    print("more than once here. Coverage says what is actually exercised.\n")
    print("\n".join(report_tests(repo, mods)) or "Every module has one.")
    print()
    return 1 if breaks or crossings else 0


if __name__ == "__main__":
    raise SystemExit(main())
