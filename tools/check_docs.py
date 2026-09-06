#!/usr/bin/env python3
"""What the documentation claims, checked against what the code does.

Five kinds of drift, each mechanical:

1. The layout tree in docs/architecture.md against the files that exist.
2. docs/commands.md against the parser - every command and flag, both ways.
3. docs/configuration.md against the dataclass fields a config can set.
4. Every `repwise ...` example in the prose, parsed by the real parser.
5. Every internal Markdown link and anchor.

Run it with the project's interpreter: it imports the parser rather than
reading it, so the help text and the docs cannot disagree unnoticed.
"""

import argparse
import ast
import re
import sys
from pathlib import Path

#: Keys config.py reads that a user never writes, so their absence is right.
NOT_A_CONFIG_KEY = {"name"}

#: Listed in architecture.md but absent from a checkout: workouts.yaml is
#: gitignored, and a package __init__ is layout rather than a module.
TREE_EXEMPT = ("workouts.yaml", "__init__.py")


def fences(text: str) -> list[str]:
    """The contents of every fenced code block."""
    return re.findall(r"^```[^\n]*\n(.*?)^```", text, re.MULTILINE | re.DOTALL)


# --- 1. The layout tree ----------------------------------------------------


def tree_paths(architecture: str) -> set[str]:
    """The file paths the layout block names, as paths from the repo root."""
    blocks = fences(architecture)
    if not blocks:
        return set()
    stack: list[tuple[int, str]] = []
    paths: set[str] = set()
    for line in blocks[0].splitlines():
        if not line.strip():
            continue
        token = line.split()[0]
        # Description lines wrap; only a line whose first word looks like a
        # file or a directory is an entry rather than a continuation.
        if not re.fullmatch(r"[\w.\-]+/?|[\w.\-]+/[\w.\-/]+/?", token):
            continue
        if "." not in token and not token.endswith("/"):
            continue
        indent = len(line) - len(line.lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else ""
        full = parent + token
        if token.endswith("/"):
            stack.append((indent, full))
        else:
            paths.add(full)
    return paths


def check_tree(repo: Path) -> list[str]:
    doc = repo / "docs" / "architecture.md"
    if not doc.is_file():
        return ["- MISSING: docs/architecture.md"]
    listed = tree_paths(doc.read_text(encoding="utf-8"))
    actual = {
        str(p.relative_to(repo))
        for p in [
            *(repo / "src").rglob("*.py"),
            *(repo / "tests").glob("*.py"),
            *(repo / "tools").glob("*.py"),
        ]
        if p.name != "__init__.py"
    }
    actual |= {
        str(p.relative_to(repo))
        for p in repo.glob("workouts*.yaml")
        if p.name != "workouts.yaml"  # gitignored; absent from a fresh checkout
    }
    out = []
    listed = {p for p in listed if not p.endswith(TREE_EXEMPT)}
    for path in sorted(listed - actual):
        out.append(f"- STALE: architecture.md lists {path}, which does not exist")
    for path in sorted(actual - listed):
        out.append(f"- UNLISTED: {path} exists but architecture.md does not name it")
    return out


# --- 2 & 4. The parser -----------------------------------------------------


def parser_shape(repo: Path) -> dict[str, set[str]] | None:
    """The commands and flags the real parser accepts, or None if it will not
    import - which is itself the finding, and a louder one than any drift."""
    sys.path.insert(0, str(repo / "src"))
    try:
        from repwise.cli.parser import build_parser
    except Exception as failure:  # noqa: BLE001 - any import failure is the news
        print(f"## The parser will not import\n\n- {failure!r}\n")
        return None

    parser = build_parser()
    # argparse offers no public way to read back the subcommands it was given,
    # so the private attributes are the only way to ask the real parser rather
    # than a second list of flags that could disagree with it. SLF001 names the
    # rule this suppresses, as the other noqa comments in this project do.
    actions = parser._actions  # noqa: SLF001
    subs = next(
        (a for a in actions if isinstance(a, argparse._SubParsersAction)),  # noqa: SLF001
        None,
    )
    flags: dict[str, set[str]] = {"": set()}
    for action in actions:
        flags[""].update(action.option_strings)
    for name, sub in subs.choices.items() if subs else ():
        flags[name] = {o for a in sub._actions for o in a.option_strings}  # noqa: SLF001
    return flags


def check_commands(repo: Path, flags: dict[str, set[str]]) -> list[str]:
    doc = repo / "docs" / "commands.md"
    if not doc.is_file():
        return ["- MISSING: docs/commands.md"]
    text = doc.read_text(encoding="utf-8")
    out = []
    for command, options in sorted(flags.items()):
        if not command:
            continue
        if not re.search(rf"\brepwise {command}\b", text):
            out.append(f"- UNDOCUMENTED: `repwise {command}` is not in commands.md")
            continue
        for flag in sorted(options):
            # Short flags are the long one's alias; documenting `--verbose` is
            # documenting `-v`.
            if flag in ("-h", "--help") or not flag.startswith("--"):
                continue
            if flag not in text:
                out.append(
                    f"- UNDOCUMENTED: `repwise {command} {flag}` is not in commands.md"
                )
    return out


def check_examples(repo: Path, flags: dict[str, set[str]]) -> list[str]:
    """Every `repwise ...` line in the prose, against what the parser accepts."""
    out = []
    docs = [repo / "README.md", *sorted((repo / "docs").glob("*.md"))]
    for path in docs:
        if not path.is_file():
            continue
        for block in fences(path.read_text(encoding="utf-8")):
            for line in block.splitlines():
                words = line.replace("$ ", "", 1).split()
                if not words or words[0] != "repwise":
                    continue
                rest = [w for w in words[1:] if not w.startswith("#")]
                if not rest:
                    continue
                command = rest[0]
                where = path.relative_to(repo)
                if command.startswith("-"):
                    if command not in flags[""]:
                        out.append(
                            f"- BAD EXAMPLE: {where}: `{line.strip()}`"
                            f" - no such global flag {command}"
                        )
                    continue
                if command not in flags:
                    out.append(
                        f"- BAD EXAMPLE: {where}: `{line.strip()}`"
                        f" - no such command `{command}`"
                    )
                    continue
                known = flags[command] | flags[""]
                for word in rest[1:]:
                    stem = word.split("=")[0]
                    if stem.startswith("-") and stem not in known:
                        out.append(
                            f"- BAD EXAMPLE: {where}: `{line.strip()}`"
                            f" - `{command}` does not accept {stem}"
                        )
    return out


# --- 3. Config fields ------------------------------------------------------


def config_keys(source: str) -> set[str]:
    """Every key config.py reads out of the parsed YAML.

    The field names on the dataclasses are the wrong thing to check: a config
    writes `min`, and the field it lands on is `minimum`. What the docs have
    to explain is the key, so the key is what this reads - every literal
    config.py looks up, subscripts, or tests for membership.
    """
    tree = ast.parse(source)
    keys: set[str] = set()
    for node in ast.walk(tree):
        match node:
            case ast.Call(func=ast.Attribute(attr="get" | "pop"), args=args) if args:
                if isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
                    keys.add(args[0].value)
            case ast.Subscript(slice=ast.Constant(value=str() as key)):
                keys.add(key)
            case ast.Compare(
                ops=[ast.In() | ast.NotIn()],  # codespell:ignore notin
                left=ast.Constant(value=str() as key),
            ):
                keys.add(key)
    # SCREAMING_CASE is an environment variable, not something a config writes.
    return {
        k
        for k in keys
        if k and not k.startswith("_") and k not in NOT_A_CONFIG_KEY and not k.isupper()
    }


def check_settings(repo: Path) -> list[str]:
    config = repo / "src" / "repwise" / "config.py"
    doc = repo / "docs" / "configuration.md"
    example = repo / "workouts.example.yaml"
    if not (config.is_file() and doc.is_file()):
        return ["- MISSING: config.py or docs/configuration.md"]
    text = doc.read_text(encoding="utf-8")
    shipped = example.read_text(encoding="utf-8") if example.is_file() else ""
    out = []
    for key in sorted(config_keys(config.read_text(encoding="utf-8"))):
        if not re.search(rf"\b{re.escape(key)}\b", text):
            out.append(
                f"- UNDOCUMENTED: config.py reads `{key}`, "
                f"which configuration.md never mentions"
            )
        elif shipped and not re.search(rf"\b{re.escape(key)}\b", shipped):
            out.append(
                f"- NOT IN EXAMPLE: `{key}` is documented but "
                f"workouts.example.yaml never shows it"
            )
    return out


# --- 5. Links --------------------------------------------------------------


def slug(heading: str) -> str:
    text = re.sub(r"[`*_]", "", heading.strip().lower())
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s+", "-", text).strip("-")


def anchors(path: Path) -> set[str]:
    found = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if match := re.match(r"^#{1,6}\s+(.*)", line):
            found.add(slug(match.group(1)))
    return found


def check_links(repo: Path) -> list[str]:
    docs = [repo / "README.md", *sorted((repo / "docs").glob("*.md"))]
    out = []
    for path in docs:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        found = re.findall(r"\]\(([^)\s]+)\)", text)
        found += re.findall(r"^\[[^\]]+\]:\s*(\S+)$", text, re.MULTILINE)
        for target in found:
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            where = path.relative_to(repo)
            file_part, _, anchor = target.partition("#")
            dest = path if not file_part else (path.parent / file_part).resolve()
            if not dest.is_file():
                out.append(f"- BROKEN LINK: {where} -> {target} (no such file)")
                continue
            if anchor and anchor not in anchors(dest):
                out.append(f"- BROKEN ANCHOR: {where} -> {target}")
    return out


# --- Version ---------------------------------------------------------------


def check_version(repo: Path) -> list[str]:
    pyproject = (repo / "pyproject.toml").read_text(encoding="utf-8")
    init = (repo / "src" / "repwise" / "__init__.py").read_text(encoding="utf-8")
    declared = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    shipped = re.search(r'__version__ = "([^"]+)"', init)
    if declared and shipped and declared.group(1) != shipped.group(1):
        return [
            f"- VERSION: pyproject says {declared.group(1)}, "
            f"__init__.py says {shipped.group(1)}"
        ]
    return []


def section(title: str, findings: list[str], clean: str) -> list[str]:
    print(f"## {title}\n")
    print("\n".join(findings) if findings else clean)
    print()
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".", help="the repwise checkout")
    args = ap.parse_args()
    repo = Path(args.root).resolve()

    print("# Documentation\n")
    found: list[str] = []
    flags = parser_shape(repo)
    found += section(
        "Layout tree in architecture.md", check_tree(repo), "Matches the tree."
    )
    if flags is not None:
        found += section(
            "Commands and flags in commands.md",
            check_commands(repo, flags),
            "Every command and long flag is documented.",
        )
        found += section(
            "Examples in the prose",
            check_examples(repo, flags),
            "Every `repwise ...` example parses.",
        )
    found += section(
        "Config keys in configuration.md",
        check_settings(repo),
        "Every key config.py reads is documented and shown in the example.",
    )
    found += section("Internal links and anchors", check_links(repo), "All resolve.")
    found += section("Version", check_version(repo), "pyproject and __init__ agree.")
    # Every check here is objective drift between a document and the code, so
    # any finding fails the run. There is no judgement call in this file.
    return 1 if flags is None or found else 0


if __name__ == "__main__":
    raise SystemExit(main())
