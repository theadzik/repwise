"""Shell completion, read off the parser rather than written beside it.

A completion script is a second statement of what the command line accepts,
and the way a hand-written one fails is that it stops agreeing with the first:
a flag added in `parser.py` completes nowhere, and one removed completes into
an error. So nothing here describes repwise. The parser is walked and
rendered, which makes a script correct by construction for whatever the parser
says on the day it is printed.

What is completed is what a parser can be asked: command names, option names,
the words a positional accepts, and filenames for the options that name one.
Ids are not, deliberately - the only place to look a workout or activity id up
is Garmin, and a login on every press of Tab is not a feature. That is also
why this module reaches nothing: `source <(repwise completion bash)` runs from
whatever directory a shell opened in, where there need be no config at all.
"""

import argparse
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum

#: The metavar of an argument that names a file. `parser.py` spells its two
#: that way, so this one word is what decides they complete as paths.
PATH = "PATH"


class Value(Enum):
    """What follows an option, as far as a shell can be told."""

    #: A switch: the next word is not its business.
    NONE = "none"
    #: A filename, which a shell already knows how to offer.
    PATH = "path"
    #: Real, but unguessable - an id, or a name to match on. Completing
    #: filenames into one of these would be worse than completing nothing.
    OPAQUE = "opaque"


@dataclass(frozen=True)
class Option:
    """One flag, and whatever it expects after it."""

    flags: tuple[str, ...]
    help: str
    value: Value
    #: What the value is called, for the shell that shows a description.
    metavar: str


@dataclass(frozen=True)
class Positional:
    """An argument given by position, and the words it is known to accept."""

    metavar: str
    words: tuple[str, ...]
    #: `nargs="*"` or `"+"`: the words go on being offered.
    repeated: bool


@dataclass(frozen=True)
class Command:
    """A subcommand: the word that reaches it, and what it then accepts."""

    name: str
    help: str
    options: tuple[Option, ...]
    positionals: tuple[Positional, ...]

    @property
    def words(self) -> tuple[str, ...]:
        """Every literal this command's positionals accept, in one list."""
        return tuple(word for p in self.positionals for word in p.words)


# --- reading the parser ----------------------------------------------------


def _actions(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    """Everything a parser accepts.

    argparse offers no public way to ask it. The alternative is a second
    description of the command line kept in step by hand, which is the thing
    this module exists to avoid, so the private attribute is read here and in
    `_subcommands` below - and nowhere else.
    """
    return parser._actions  # noqa: SLF001


def _text(help_text: str | None) -> str:
    """A help string as one line, however it was wrapped in the source."""
    return " ".join((help_text or "").split())


def _metavar(action: argparse.Action) -> str:
    """What to call this argument's value: argparse's own answer.

    A metavar can also be a tuple, one name per word of a multi-word value.
    Nothing here has one, and the dest is a fair name for it if anything ever
    does, so that case falls through rather than being spelled out.
    """
    if isinstance(action.metavar, str):
        return action.metavar
    return action.dest.upper()


def _value(action: argparse.Action) -> Value:
    """Whether a flag takes a value, and whether a shell can offer one."""
    if action.nargs == 0:
        return Value.NONE
    return Value.PATH if action.metavar == PATH else Value.OPAQUE


def _options(parser: argparse.ArgumentParser) -> tuple[Option, ...]:
    return tuple(
        Option(
            flags=tuple(action.option_strings),
            help=_text(action.help),
            value=_value(action),
            metavar=_metavar(action),
        )
        for action in _actions(parser)
        if action.option_strings
    )


def _positionals(parser: argparse.ArgumentParser) -> tuple[Positional, ...]:
    found: list[Positional] = []
    for action in _actions(parser):
        if action.option_strings:
            continue
        found.append(
            Positional(
                metavar=_metavar(action),
                words=tuple(action.choices or ()),
                repeated=action.nargs in {"*", "+"},
            )
        )
    return tuple(found)


def _commands(parser: argparse.ArgumentParser) -> tuple[Command, ...]:
    """Every subcommand, in the order the help lists them."""
    for action in _actions(parser):
        # The subparsers action is the one whose `choices` maps a name to a
        # parser; any other choices is a plain sequence of strings.
        if not isinstance(action.choices, dict):
            continue
        # The one-line help of a command is not on the parser it belongs to.
        # argparse keeps it on a pseudo-action of its own, whose dest is the
        # command's name.
        helps = {
            choice.dest: _text(choice.help)
            for choice in action._choices_actions  # type: ignore[attr-defined]  # noqa: SLF001
        }
        return tuple(
            Command(
                name=name,
                help=helps.get(name, ""),
                options=_options(sub),
                positionals=_positionals(sub),
            )
            for name, sub in action.choices.items()
        )
    return ()


def _flags(options: Iterable[Option], value: Value) -> set[str]:
    """Every flag among these that expects a value of the given kind."""
    return {flag for o in options if o.value is value for flag in o.flags}


# --- bash ------------------------------------------------------------------


def _bash(parser: argparse.ArgumentParser) -> str:
    top = _options(parser)
    commands = sorted(_commands(parser), key=lambda c: c.name)
    everywhere = list(top) + [o for c in commands for o in c.options]
    paths = sorted(_flags(everywhere, Value.PATH))
    opaque = sorted(_flags(everywhere, Value.OPAQUE))
    # Only the options that come before a command matter to the scan below,
    # which is looking for the first word that is not one of them.
    consuming = sorted(_flags(top, Value.PATH) | _flags(top, Value.OPAQUE))

    lines = [
        "# bash completion for repwise, generated by `repwise completion bash`.",
        "#",
        "# Load it by adding this to ~/.bashrc:",
        "#",
        "#     source <(repwise completion bash)",
        "",
        "_repwise() {",
        "    local cur prev command word opts words skip i",
        "",
        '    cur="${COMP_WORDS[COMP_CWORD]}"',
        '    prev="${COMP_WORDS[COMP_CWORD-1]}"',
        "",
        "    # What is being completed is the value of the option before it, if",
        "    # that option takes one. A filename it can offer; an id it cannot,",
        "    # and offering filenames instead would be worse than nothing.",
        '    case "$prev" in',
    ]
    if paths:
        lines += [
            f"        {'|'.join(paths)})",
            '            mapfile -t COMPREPLY < <(compgen -f -- "$cur")',
            "            compopt -o filenames 2>/dev/null",
            "            return",
            "            ;;",
        ]
    if opaque:
        lines += [
            f"        {'|'.join(opaque)})",
            "            return",
            "            ;;",
        ]
    lines += [
        "    esac",
        "",
        "    # The first word that is neither an option nor the value of one",
        "    # names the command, and the command decides all the rest.",
        '    command=""',
        '    skip=""',
        "    for ((i = 1; i < COMP_CWORD; i++)); do",
        '        word="${COMP_WORDS[i]}"',
        "        if [[ -n $skip ]]; then",
        '            skip=""',
        "            continue",
        "        fi",
        '        case "$word" in',
    ]
    if consuming:
        lines.append(f"            {'|'.join(consuming)}) skip=1 ;;")
    lines += [
        "            -*) ;;",
        '            *) command="$word"; break ;;',
        "        esac",
        "    done",
        "",
        '    case "$command" in',
    ]
    for command in commands:
        lines += [
            f"        {command.name})",
            f'            opts="{_bash_words(command.options)}"',
            f'            words="{" ".join(command.words)}"',
            "            ;;",
        ]
    lines += [
        "        *)",
        f'            opts="{_bash_words(top)}"',
        f'            words="{" ".join(c.name for c in commands)}"',
        "            ;;",
        "    esac",
        "",
        "    if [[ $cur == -* ]]; then",
        '        mapfile -t COMPREPLY < <(compgen -W "$opts" -- "$cur")',
        "    else",
        '        mapfile -t COMPREPLY < <(compgen -W "$words" -- "$cur")',
        "    fi",
        "}",
        "",
        "complete -F _repwise repwise",
        "",
    ]
    return "\n".join(lines)


def _bash_words(options: Iterable[Option]) -> str:
    """The flags of these options as one word list, for `compgen -W`."""
    return " ".join(sorted({flag for o in options for flag in o.flags}))


# --- zsh -------------------------------------------------------------------


def _zsh_quote(text: str) -> str:
    """Wrap in single quotes, inside which a shell reads nothing as syntax.

    A quote of its own is the one thing that cannot appear there, so it is
    closed, escaped and reopened - which is why `Garmin's` survives the trip.
    """
    return "'" + text.replace("'", "'\\''") + "'"


def _zsh_spec_text(text: str) -> str:
    """Escape what `_arguments` reads as syntax rather than as prose.

    A spec is colon-separated with its description in brackets, so those are
    the characters a help string cannot contain untouched.
    """
    for char in ("\\", "[", "]", ":"):
        text = text.replace(char, "\\" + char)
    return text


def _zsh_option(option: Option) -> str:
    """One `_arguments` spec for one flag.

    A flag with an alias is written as the pair it is, and told to exclude
    itself, so that `-v` and `--verbose` are one entry to choose from rather
    than two ways to say a thing already said.
    """
    body = f"[{_zsh_spec_text(option.help)}]"
    if option.value is Value.PATH:
        body += f":{_zsh_spec_text(option.metavar.lower())}:_files"
    elif option.value is Value.OPAQUE:
        body += f":{_zsh_spec_text(option.metavar.lower())}:"
    if len(option.flags) == 1:
        return _zsh_quote(option.flags[0] + body)
    exclusive = _zsh_quote("(" + " ".join(option.flags) + ")")
    return exclusive + "{" + ",".join(option.flags) + "}" + _zsh_quote(body)


def _zsh_positional(index: int, positional: Positional) -> str:
    slot = "*" if positional.repeated else str(index)
    offered = f"({' '.join(positional.words)})" if positional.words else ""
    message = _zsh_spec_text(positional.metavar.lower())
    return _zsh_quote(f"{slot}:{message}:{offered}")


def _zsh_arguments(specs: Iterable[str], indent: str) -> list[str]:
    """An `_arguments` call, one spec per line and continued to the next."""
    specs = list(specs)
    lines = [f"{indent}_arguments \\"]
    lines += [f"{indent}    {spec} \\" for spec in specs[:-1]]
    lines.append(f"{indent}    {specs[-1]}")
    return lines


def _zsh(parser: argparse.ArgumentParser) -> str:
    top = _options(parser)
    commands = _commands(parser)

    lines = [
        "#compdef repwise",
        "# zsh completion for repwise, generated by `repwise completion zsh`.",
        "#",
        "# Load it by adding this to ~/.zshrc, after compinit:",
        "#",
        "#     source <(repwise completion zsh)",
        "",
        "_repwise() {",
        '    local curcontext="$curcontext" state line',
        "    local -A opt_args",
        "    local -a commands",
        "",
        "    commands=(",
    ]
    lines += [
        f"        {_zsh_quote(command.name + ':' + _zsh_spec_text(command.help))}"
        for command in commands
    ]
    lines += [
        "    )",
        "",
        "    # -C so that the two states below are reachable: which command was",
        "    # given is what decides everything after it.",
        "    _arguments -C \\",
    ]
    lines += [f"        {_zsh_option(option)} \\" for option in top]
    lines += [
        "        '1:command:->command' \\",
        "        '*::arg:->args'",
        "",
        "    case $state in",
        "        command)",
        "            _describe -t commands 'repwise command' commands",
        "            ;;",
        "        args)",
        "            case $words[1] in",
    ]
    for command in commands:
        specs = [_zsh_option(option) for option in command.options]
        specs += [
            _zsh_positional(index, positional)
            for index, positional in enumerate(command.positionals, start=1)
        ]
        lines.append(f"                {command.name})")
        lines += _zsh_arguments(specs, indent=" " * 20)
        lines.append("                    ;;")
    lines += [
        "            esac",
        "            ;;",
        "    esac",
        "}",
        "",
        "# compdef is what compinit defines, so sourcing this before compinit",
        "# would otherwise fail with nothing but `command not found`.",
        "if (( $+functions[compdef] )); then",
        "    compdef _repwise repwise",
        "else",
        "    print -u2 'repwise: source this after compinit, "
        "or completion is not registered'",
        "fi",
        "",
    ]
    return "\n".join(lines)


# --- what `repwise completion` calls ---------------------------------------

Renderer = Callable[[argparse.ArgumentParser], str]

#: One renderer per shell `parser.py` accepts. Adding a shell is a function
#: here and a word in SHELLS there; a test holds the two to each other.
RENDERERS: dict[str, Renderer] = {"bash": _bash, "zsh": _zsh}


def render(parser: argparse.ArgumentParser, shell: str) -> str:
    """A completion script for `shell`, describing `parser`.

    The shell is one argparse already accepted, so an unknown one is a bug
    here rather than a mistake at the command line.
    """
    return RENDERERS[shell](parser)
