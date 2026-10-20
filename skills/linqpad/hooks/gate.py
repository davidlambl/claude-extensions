"""Asks before an lprun9 run that touches a saved LINQPad connection: a PreToolUse hook on the Bash and PowerShell
tools, run by hooks.json.

Claude Code pipes the hook's JSON in, and the gate prints one decision or nothing. It asks when a command in the line
runs lprun9 (LPRun9.exe in any case, with or without a folder, or behind timeout, env, cmd /c, Start-Process and the
like) with a -cxname= option, or names a script whose first 8 KB, the path resolved against the hook's cwd, hold a
<Connection> header. A command whose program is not lprun9 is silent, so cat, type, code and Get-Content of a .linq
never prompt. A run it cannot read (a quote that does not close, a path in a variable, a script that is not there)
asks too. Under bypassPermissions and dontAsk there is nobody to ask, so each ask becomes a deny with the same reason.

This is a speed bump, not a boundary: a script's own code can open a connection, and a command can be disguised past
what this reads. The permission rules in the README are the hard guarantee; the gate adds the reason to the prompt.
The fast path, for another tool or a command that names neither lprun9 nor a .linq, imports only json, re and sys.
"""
import json, re, sys

PLUGIN = "linqpad"
CX_TAIL = " — not through sql-read: no shape check, no row cap, no login probe"
HEAD_BYTES = 8192  # how much of a script the gate reads for its header
FILE_BUDGET = 5.0  # seconds for opening scripts, inside hooks.json's 10-second timeout

# Worth reading at all: the line names lprun9, a .linq or a -cx option once quotes and escapes are out of the way.
WORTH_READING = re.compile(r"lprun9|\.linq|(?<![\w/.-])(?:-{1,2}|/)cx", re.I)


# --- Reading a command line ------------------------------------------------------------------------------------------
# Shared with sql-read's gate, which keeps a copy of this section, down to the gate's own section below; a change to one
# goes in the other. It splits a Bash or PowerShell command line into simple commands, finds the program each runs (past
# assignments, call operators, quotes and wrappers such as timeout or cmd /c), reads the command lines nested in their
# words, and answers Claude Code. It errs toward finding a command that is not there, never toward missing one. It uses
# json, re and sys, imported at the top, and imports anything else inside the function that needs it.

SHELLS = {"Bash": "bash", "PowerShell": "powershell"}  # the hook's tool_name, and the shell its command is written for
NOBODY_TO_ASK = frozenset({"bypassPermissions", "dontAsk"})  # permission modes in which an ask becomes a deny
SEPARATORS = {  # what ends a simple command, longest first
    "bash": ("&&", "||", ";;&", ";;", ";&", "|&", ";", "|", "&", "(", ")", "`", "\n"),
    "powershell": ("&&", "||", ";", "|", "&", "(", ")", "{", "}", "\n"),
}
REDIRECTIONS = ("&>>", "<<<", "<<-", "&>", "<<", ">>", ">&", "<&", ">|", "<>", ">", "<")
OPERATOR_CHARS = {"bash": frozenset("<>&|;()`\n"), "powershell": frozenset("<>&|;(){}\n")}
PS_QUOTES = {0x2018: "'", 0x2019: "'", 0x201A: "'", 0x201B: "'", 0x201C: '"', 0x201D: '"', 0x201E: '"'}
HERE_STRING = re.compile(r"[ \t]*\r?\n")  # what follows @' or @" when it opens a PowerShell here-string
GROUP = "$(...)"  # stands in a command for what a ( ) or backtick group inside it computes
MAX_DEPTH = 3  # how deep all_commands reads command lines inside words
NESTED_BUDGET = (400, 500_000)  # how many nested lines it reads in all, and how many characters
NESTED = re.compile(r"[\s;&|()<>{}`]")  # a word that may hold a command line of its own
SUBSTITUTION = re.compile(r"\$\(|`|[<>]\(")  # text Bash runs even as an argument to echo, outside single quotes
PS_SUBSTITUTION = re.compile(r"\$\(")  # and PowerShell, whose backtick is its escape character
QUOTED_PART = re.compile(r"'([^']*)'|\"([^\"]*)\"")  # a quoted part of a word that holds no escape
NETWORK_PATH = "a network or device path, which the gate does not open before you approve"

ASSIGNMENT = re.compile(r"[A-Za-z_]\w*(?:\[[^\]]*\])?\+?=")  # FOO=1 before a Bash command
PS_VARIABLE = re.compile(r"(?:\[[^\]]*\])*\$\S*")  # $x, [int]$x, $env:PATH
PS_SETS = frozenset({"=", "+=", "-=", "*=", "/=", "%=", "??="})
PS_SET = re.compile(r"(?:\[[^\]]*\])*\$[^\s=]+(?:\?\?|[-+*/%])?=")  # $x= glued to the value that follows
KEYWORDS = frozenset("! if then else elif do while until { } elseif try finally return throw exit begin process end "
                     "clean".split())
NO_PROGRAM = frozenset("for select case function foreach switch catch trap param filter class enum".split())

# Programs that run the program named after them (the gate looks past them for the one it watches)
WRAPPERS = frozenset("""timeout env nohup time exec command builtin nice ionice stdbuf setsid sudo doas gsudo runas su
    xargs parallel find winpty start call cmd wsl bash sh zsh dash ksh fish pwsh powershell strace ltrace chrt taskset
    unbuffer watch caffeinate flock source . eval start-process saps invoke-expression iex invoke-command icm
    start-job sajb start-threadjob measure-command trace-command invoke-item ii""".split())
FEEDERS = frozenset({"xargs", "parallel"})  # wrappers that add arguments the line does not show
# PowerShell's Start-Process, which takes its parameters in any order: its name and aliases, one of its parameters
# (-Wait, -FilePath:x), the one that holds the program's arguments, the others that take a value (common ones too;
# not -RedirectStandardInput, whose file the program reads, so it stays among what the gate judges), and its switches
START_PROCESS = frozenset({"start-process", "saps", "start"})
START_PARAMETER = re.compile(r"-([A-Za-z]+)(?::(.*))?", re.S)
START_LIST = frozenset({"argumentlist", "args"})
START_VALUED = frozenset("""filepath pspath path credential runas workingdirectory redirectstandarderror rse
    redirectstandardoutput rso verb windowstyle environment erroraction ea warningaction wa informationaction infa
    errorvariable ev warningvariable wv informationvariable iv outvariable ov outbuffer ob pipelinevariable pv
    progressaction proga""".split())
START_SWITCHES = frozenset("""wait nonewwindow nnw passthru loaduserprofile lup usenewenvironment verbose vb debug db
    whatif wi confirm cf""".split())
# Programs that read text as code: with one in the line, no text is taken as only text
INTERPRETERS = frozenset("""bash sh zsh dash ksh fish csh tcsh pwsh powershell cmd wsl iex invoke-expression source .
    eval python python3 py pythonw node deno bun perl ruby php lua dotnet csi dotnet-script scriptcs cscript wscript
    mshta osascript""".split())
SCRIPT_FILE = re.compile(r"\.(?:ps1|psm1|sh|bash|zsh|bat|cmd|py|pyw|js|mjs|cjs|vbs|wsf|rb|pl)$", re.I)
# Programs whose arguments are text, such as a commit message or the words echo prints
TEXT_PROGRAMS = frozenset("""echo printf git gh grep egrep fgrep rg ag ack findstr select-string sls write-output write
    write-host write-error write-warning write-verbose write-information code code-insiders notepad notepad++ vim vi
    nvim nano emacs less more head tail wc sort uniq cut diff cmp fc jq yq set-content add-content out-file tee
    tee-object new-item ni""".split())
CD = frozenset("cd chdir pushd popd set-location sl push-location pop-location".split())
CD_OPTION = re.compile(r"-C|--chdir(?:=.*)?|(?i:-wo\w*|-execdir|/d)", re.S)  # env -C, Start-Process -WorkingDirectory
# Programs that read the files they name and write none, so naming a file beside the watched run changes nothing
READ_ONLY = frozenset("""cat tac type get-content gc head tail less more grep egrep fgrep rg ag ack findstr
    select-string sls wc code code-insiders notepad notepad++ ls dir get-childitem gci get-item gi stat file du echo
    printf write-output write-host test [ [[ md5sum sha1sum sha256sum get-filehash diff cmp fc jq which where
    get-command gcm resolve-path rvpa""".split())
UNEXPANDED = re.compile(r"[$`*?{}\[\]]|%[^%\s]+%|^[~@]")  # a path only the shell can finish
PROGRAM_EXPANSION = re.compile(r"[$`*?]|%[^%\s]+%|^[~@]")  # a program held in a variable or a substitution


class Command:
    """One simple command: its words with quotes removed, the targets of its redirections, the text it gets on
    standard input from a heredoc or a <<< here-string (stdin), whether anything in the line feeds its standard input
    (reads_input: a pipe, a < file, a heredoc or a here-string), whether PowerShell's & call operator runs it, whether
    it stands inside a $( ), backtick or <( ) group, which runs even in text (substituted), and the shell it was read
    as. all_commands adds the command whose word it was found in (parent), and whether it sits inside literal text
    (inert)."""

    def __init__(self, shell):
        self.shell, self.words, self.redirects, self.stdin, self.called = shell, [], [], [], False
        self.reads_input, self.substituted, self.parent, self.inert = False, False, None, False

    def __repr__(self):
        return (f"Command({self.words!r}, redirects={self.redirects!r}, stdin={self.stdin!r}, called={self.called}, "
                f"reads_input={self.reads_input}, shell={self.shell!r}, inert={self.inert})")

    def within(self, ancestors):
        """Whether this command was found inside a word of one of ancestors (a set of ids), at any depth."""
        parent = self.parent
        while parent is not None:
            if id(parent) in ancestors:
                return True
            parent = parent.parent
        return False


class Word(str):
    """A word as tokens hands it on, quotes off, or a heredoc's body; live says whether its shell runs a command
    inside it ($( ) or a backtick outside single quotes, an unquoted heredoc's), and quoted whether it was written
    whole in quotes."""

    def __new__(cls, text, live, quoted=False):
        word = super().__new__(cls, text)
        word.live, word.quoted = live, quoted
        return word


def substitutes(text, shell):
    """Whether text, as shell reads it outside single quotes, runs a command: $( ), a backtick or <( ) in Bash; $( )
    in PowerShell, whose backtick is an escape."""
    return (SUBSTITUTION if shell == "bash" else PS_SUBSTITUTION).search(text) is not None


def runs_inside(text):
    """Whether a word or a heredoc's body runs a command where it stands: as tokens read it, or, for text it did not
    read (the words after PowerShell's --%), whenever it holds $(, a backtick or <(."""
    return text.live if isinstance(text, Word) else SUBSTITUTION.search(text) is not None


def dequote(raw, shell):
    """A word as its shell hands it to a program: quotes removed and escapes applied, by shlex in POSIX mode, with
    PowerShell's backtick as its escape character. Expansions ($HOME, $(...), globs) stay as written. A word with no
    escape character in it only has its quotes taken off, as shlex would, without shlex, which is slow on a long one."""
    if ("`" if shell == "powershell" else "\\") not in raw:
        return QUOTED_PART.sub(lambda part: part.group(1) if part.group(1) is not None else part.group(2), raw)
    import shlex
    lexer = shlex.shlex(raw, posix=True)
    lexer.whitespace, lexer.commenters, lexer.whitespace_split = "", "", True
    if shell == "powershell":
        lexer.escape, lexer.escapedquotes = "`", '"'
    return "".join(lexer)


def tokens(line, shell, comments=True):
    """A command line as ("word", text), ("op", separator), ("redirect", operator), ("heredoc", operator) and
    ("stdin", text) tokens, in order.

    shell is "bash" or "powershell". Quotes keep what they hold together, an operator inside quotes included; a word's
    quotes and escapes come off through dequote. Comments are dropped, unless comments is False (for text read only
    for the commands its substitutions run, in which a # is text), and an escaped newline joins two lines. A Bash
    heredoc comes out as a "heredoc" token where it opens (its delimiter word is consumed), and its body as a "stdin"
    token just before the newline that ends its line, in the order the heredocs opened. For PowerShell, curly
    quotes count as quotes, a comma separates arguments, and a here-string (@'...'@) or what follows the --%
    stop-parsing token is taken as it is written. A word or a body comes out as a Word, which knows whether its shell
    runs a command inside it: not in single quotes, an @'...'@ here-string, or a heredoc whose delimiter is quoted.
    Raises ValueError when a quote or a comment does not close."""
    ps = shell == "powershell"
    if ps:
        line = line.translate(PS_QUOTES)
    esc = "`" if ps else "\\"
    separators, operators = SEPARATORS[shell], OPERATOR_CHARS[shell]
    out, raw, live, i, n = [], [], [], 0, len(line)  # live: the word's text outside single quotes and escapes
    heredocs, awaiting = [], []  # (delimiter, strip tabs, quoted) for each heredoc opened on the line; a << owed a word

    def flush():
        if raw:
            text, unquoted = "".join(raw), "".join(live)
            raw.clear()
            live.clear()
            if awaiting:
                quoted = re.search(r"[\"'\\]", text) is not None  # which keeps the body as written
                heredocs.append((dequote(text, shell), awaiting.pop() == "<<-", quoted))
            elif not ps and text in ("{", "}"):
                out.append(("op", text))
            else:
                out.append(("word", Word(dequote(text, shell), substitutes(unquoted, shell), text[:1] in ("'", '"'))))

    def bodies(start):
        """Emits the queued heredocs' bodies, the lines from start to each delimiter; returns where they end."""
        for delimiter, strip_tabs, quoted in heredocs:
            body = []
            while start < n:
                end = line.find("\n", start)
                end = n if end < 0 else end
                text, start = line[start:end], end + 1
                if (text.rstrip("\r").lstrip("\t") if strip_tabs else text.rstrip("\r")) == delimiter:
                    break
                body.append(text)
            text = "\n".join(body)
            out.append(("stdin", Word(text, not quoted and substitutes(text, shell))))
        heredocs.clear()
        return min(start, n)

    while i < n:
        c = line[i]
        if c == esc:
            if line.startswith("\r\n", i + 1):
                i += 3
            elif line.startswith("\n", i + 1):
                i += 2
            else:
                raw.append(line[i:i + 2])
                i += 2
        elif c == "'":
            j = i + 1
            while True:
                j = line.find("'", j)
                if j < 0:
                    raise ValueError("a quote does not close")
                if not (ps and line.startswith("''", j)):
                    break
                j += 2
            # PowerShell doubles a quote inside single quotes; shlex takes it as close, "'", reopen
            raw.append("'" + line[i + 1:j].replace("''", "'\"'\"'") + "'" if ps else line[i:j + 1])
            i = j + 1
        elif c == '"':
            j = i + 1
            while j < n:
                if line[j] == esc or (ps and line.startswith('""', j)):
                    j += 2
                elif line[j] == '"':
                    break
                else:
                    j += 1
            if j >= n:
                raise ValueError("a quote does not close")
            body = line[i + 1:j]
            raw.append('"' + (body.replace('""', '`"') if ps else body) + '"')
            live.append(body)
            i = j + 1
        elif not ps and c == "$" and line.startswith("'", i + 1):  # $'...', with backslash escapes
            j = i + 2
            while j < n and line[j] != "'":
                j += 2 if line[j] == "\\" else 1
            if j >= n:
                raise ValueError("a quote does not close")
            raw.append("$'" + re.sub(r"[\\']", "", line[i + 2:j]) + "'")
            i = j + 1
        elif ps and c == "@" and not raw and line[i + 1:i + 2] in ("'", '"') and HERE_STRING.match(line, i + 2):
            end = re.compile(r"\r?\n" + line[i + 1] + "@").search(line, i + 2)
            if end is None:
                raise ValueError("a here-string does not close")
            body = line[line.index("\n", i + 2) + 1:end.start()]
            out.append(("word", Word(body, line[i + 1] == '"' and substitutes(body, shell), True)))
            i = end.end()
        elif ps and c == "$" and line.startswith("{", i + 1):  # ${name}, braces and all
            j = line.find("}", i + 2)
            if j < 0:
                raise ValueError("a ${ does not close")
            raw.append(re.sub(r"[\"'`]", "", line[i:j + 1]))
            live.append(raw[-1])
            i = j + 1
        elif c == "#" and not raw and comments:
            j = line.find("\n", i)
            i = n if j < 0 else j
        elif ps and not raw and comments and line.startswith("<#", i):
            j = line.find("#>", i + 2)
            if j < 0:
                raise ValueError("a comment does not close")
            i = j + 2
        elif c.isspace() and c != "\n":
            flush()
            i += 1
        elif ps and c == ",":
            flush()
            i += 1
        elif ps and not raw and line.startswith("--%", i) and (i + 3 == n or line[i + 3].isspace()):
            stop = min([k for k in (line.find("\n", i), line.find("|", i)) if k >= 0] or [n])
            out.append(("word", "--%"))
            out += [("word", piece.replace('"', "")) for piece in re.findall(r'(?:[^\s"]+|"[^"]*")+', line[i + 3:stop])]
            i = stop
        elif c in operators:
            redirect = next((r for r in REDIRECTIONS if line.startswith(r, i) and not (ps and r[0] == "&")), None)
            if redirect:
                if raw and ("".join(raw).isdigit() or (ps and raw == ["*"])):
                    raw.clear()  # the file descriptor of 2> or 2>&1, or PowerShell's *>
                    live.clear()
                else:
                    flush()
                if redirect in ("<<", "<<-"):
                    awaiting[:] = [redirect]  # its delimiter is the next word
                    out.append(("heredoc", redirect))
                else:
                    out.append(("redirect", redirect))
                i += len(redirect)
            else:
                separator = next(s for s in separators if line.startswith(s, i))
                flush()
                i += len(separator)
                if separator == "\n" and heredocs:
                    i = bodies(i)
                out.append(("op", separator))
        else:
            raw.append(c)
            live.append(c)
            i += 1
    flush()
    return out


def split_commands(line, shell, comments=True):
    """The simple commands of a command line, as Command objects; command lines nested in words stay in their words.
    comments is as for tokens.

    Every shell operator (&&, ||, ;, |, &, a newline, a brace in PowerShell) ends one command and starts the next. The
    word after a redirection is its target, not an argument; a heredoc's body and a <<< here-string are the stdin of
    the command that opened them. A command after a pipe, or with a <, heredoc or here-string redirection, reads input.
    A PowerShell & that starts a command is the call operator and marks it called. A ( ) or backtick group inside a
    command, as an argument ($(...), PowerShell's (Resolve-Path x)) or as the program (& (Get-Command x), `which x`),
    leaves GROUP among that command's words, and the group's own commands come out beside it; a group that starts a
    command is a subshell, whose commands simply come out. Raises ValueError as tokens does."""
    commands, current, target, groups, heredocs, word = [], Command(shell), None, [], [], None

    def finish():
        nonlocal current
        kept = bool(current.words or current.redirects or current.stdin)
        if kept:
            commands.append(current)
        passed_on = current.reads_input and not kept  # an empty command, such as the { after a pipe, passes input on
        current = Command(shell)
        current.reads_input, current.substituted = passed_on, bool(groups) and groups[-1][2]

    for kind, text in tokens(line, shell, comments):
        previous, word = word, text if kind == "word" else None  # previous: the word just before this token
        if kind == "word":
            (current.stdin if target == "<<<" else current.redirects if target else current.words).append(text)
            target = None
            continue
        if kind == "redirect":
            target, current.reads_input = text, current.reads_input or text.startswith("<")
            continue
        if kind == "heredoc":
            heredocs.append(current)
            current.reads_input = True
            continue
        if kind == "stdin":
            (heredocs.pop(0) if heredocs else current).stdin.append(text)
            continue
        into_target, target = target, None
        closes = text == ")" or (text == "`" and groups and groups[-1][1] == "`")
        if text == "&" and shell == "powershell" and not current.words and not current.redirects:
            current.called = True
        elif text in ("(", "`") and not closes:
            holder = None
            # a substitution: $( ), a backtick group, <( ) or >( ), or any group inside one
            substituted = (text == "`" or into_target is not None or (previous or "").endswith("$")
                           or bool(groups) and groups[-1][2])
            if current.words or current.redirects or current.called or text == "`":
                holder = current
                if into_target:
                    (holder.stdin if into_target == "<<<" else holder.redirects).append(GROUP)
                elif holder.words and holder.words[-1].endswith("$"):
                    holder.words[-1] = holder.words[-1][:-1] + GROUP
                else:
                    holder.words.append(GROUP)
            groups.append((holder, text, substituted))
            inner = Command(shell)
            inner.reads_input = holder is None and current.reads_input  # a subshell after a pipe reads its input
            inner.substituted = substituted
            current = inner
        elif closes:
            finish()
            if groups:
                holder = groups.pop()[0]
                if holder is not None:
                    current = holder
        else:
            finish()
            if text in ("|", "|&"):
                current.reads_input = True
    finish()
    commands += [holder for holder, _, _ in groups if holder is not None]  # groups left open at the end
    return commands


def program_index(words, shell):
    """(index, program) for the word a simple command runs, or None when it runs nothing (an assignment alone, a
    for or case header). Leading VAR=value assignments, keywords such as if, do and !, and in PowerShell an
    assignment to a variable and the . call operator come off first. program is words[index], or the part after
    the = when a PowerShell assignment is glued to it ($x=lprun9)."""
    i = 0
    while i < len(words):
        word = words[i]
        low = word.lower()
        if ASSIGNMENT.match(word) or low in KEYWORDS:
            i += 1
            continue
        if low in NO_PROGRAM:
            return None
        if shell == "powershell":
            if word == "." or (PS_VARIABLE.fullmatch(word) and i + 1 < len(words) and words[i + 1] in PS_SETS):
                i += 1 if word == "." else 2
                continue
            glued = PS_SET.match(word)
            if glued:
                if glued.end() == len(word):
                    i += 1
                    continue
                return i, word[glued.end():]
        return i, word
    return None


def base_name(word):
    """A program word's name, compared as Windows compares it: the last part of its path, in lower case, with cmd's
    ^ escapes, trailing dots and spaces, and an .exe, .com, .bat or .cmd extension dropped."""
    name = re.split(r"[\\/]", word.replace("^", ""))[-1].rstrip(" .").lower()
    return re.sub(r"\.(?:exe|com|bat|cmd)$", "", name)


def program_of(command):
    """The program word a Command runs, or None."""
    found = program_index(command.words, command.shell)
    return None if found is None else found[1]


def find_run(command, wanted, direct=True):
    """Where a Command runs a program that wanted(word) accepts, as its own program or behind wrappers such as timeout,
    env, nohup, cmd /c, Start-Process or xargs (WRAPPERS): (the program word's index in command.words, whether a
    wrapper runs it, whether xargs or parallel adds arguments the line does not show), else None. The program's
    arguments are command.words[index + 1:]. With direct=False only a program behind a wrapper counts."""
    found = program_index(command.words, command.shell)
    if found is None:
        return None
    i, program = found
    if direct and wanted(program):
        return i, False, False
    if base_name(program) in WRAPPERS:
        for j in range(i + 1, len(command.words)):
            if wanted(command.words[j]):
                fed = any(base_name(word) in FEEDERS for word in [program, *command.words[i + 1:j]])
                return j, True, fed
    return None


def handed_on(command):
    """For PowerShell's Start-Process, which takes its parameters in any order, the words it hands on, as (index in
    command.words, text): a program given by position and its -ArgumentList, wherever each stands, but none of
    Start-Process's own parameters and their values (-Wait, -WorkingDirectory dir, -FilePath x). None for any other
    command."""
    found = program_index(command.words, command.shell)
    if command.shell != "powershell" or found is None or base_name(found[1]) not in START_PROCESS:
        return None
    words, handed, k = command.words, [], found[0] + 1
    while k < len(words):
        own = START_PARAMETER.fullmatch(words[k])
        name = own.group(1).lower() if own else ""
        if name in START_LIST:
            if own.group(2):
                handed.append((k, own.group(2)))  # -ArgumentList:x
        elif name in START_VALUED:
            k += own.group(2) is None  # its value is the next word
        elif name not in START_SWITCHES and words[k]:
            handed.append((k, words[k]))
        k += 1
    return handed


def run_arguments(command, index):
    """The arguments of the program at command.words[index]: the words after it, or, behind Start-Process, every word
    it hands on but the program, before it or after."""
    handed = handed_on(command)
    return command.words[index + 1:] if handed is None else [text for k, text in handed if k != index]


def held_in_a_variable(command):
    """The arguments after a program the shell computes first ($LP, & $lp, . $lp, $(which x), & (Get-Command
    x).Source), else None. In PowerShell only the & or . call operator runs a variable; alone, it is a value."""
    found = program_index(command.words, command.shell)
    if found is None or not PROGRAM_EXPANSION.search(found[1]):
        return None
    if command.shell == "powershell" and not command.called and "." not in command.words[:found[0]]:
        return None
    arguments = command.words[found[0] + 1:]
    while arguments and arguments[0][:1] in (".", "["):  # .Source, [0]: still the program
        arguments = arguments[1:]
    return arguments


def split_argument_string(argument):
    """An argument string a wrapper passes on whole (Start-Process -ArgumentList "-a x.linq", cmd /c's line), split as
    a Windows command line splits it: on spaces outside double quotes, the quotes dropped."""
    if not re.search(r"\s", argument):
        return [argument]
    return [piece.replace('"', "") for piece in re.findall(r'(?:[^\s"]+|"[^"]*")+', argument)]


def is_text_program(command):
    """Whether a Command's program takes its arguments as text: git's message, the words echo prints; or whether the
    Command is text itself: in PowerShell, a quoted string alone, with no & before it, is a value ('...' | Out-File
    x), not a program."""
    program = program_of(command)
    if command.shell == "powershell" and len(command.words) == 1 and getattr(program, "quoted", False):
        return not command.called
    return program is not None and base_name(program) in TEXT_PROGRAMS


def is_interpreter(command):
    """Whether a Command's program reads text as code: a shell, Invoke-Expression, eval, python and the like, or a
    script file (./run.sh, .\\run.ps1), which may have been written a moment before."""
    program = program_of(command)
    if program is None:
        return False
    name = base_name(program)
    return (name in INTERPRETERS or re.fullmatch(r"python[\d.]*w?|node\d*", name) is not None
            or SCRIPT_FILE.search(program.rstrip(" .")) is not None)


def all_commands(line, shell):
    """Every simple command in a command line: split_commands' own, and those inside any word that holds a command
    line of its own (the string bash -c, cmd /c, pwsh -Command, eval or Invoke-Expression runs, a quoted $(...)). A
    nested line is read both as Bash and as PowerShell, since either may run it, down to MAX_DEPTH levels and within
    NESTED_BUDGET.

    A command found inside literal text, such as git commit -m's message, the words echo prints or the heredoc cat
    reads, is marked inert; a word or a heredoc in which its shell runs a command ($( ) or a backtick outside single
    quotes, an unquoted heredoc's) is never literal, and the stdin of a shell or another interpreter is code. Returns
    (commands, unparsed), unparsed being the nested texts that read as neither shell or that the budget left unread.
    Raises ValueError when the line itself does not read."""
    commands, unparsed, budget = [], [], list(NESTED_BUDGET)
    _gather(line, shell, 0, None, False, commands, unparsed, budget)
    return commands, unparsed


def _gather(line, shell, depth, parent, inert, commands, unparsed, budget, prose=False):
    """all_commands' worker: adds line's commands, read as shell, to commands with parent and inert set, then reads
    the command lines nested in their words and stdin one level deeper, while depth and budget allow. prose says line
    is text read only for the commands its substitutions run (echo "see $(x)", a heredoc cat reads): a # in it is
    text, not a comment, and every command in it but those inside a substitution is text too, so inert."""
    found = split_commands(line, shell, not prose)
    for command in found:
        command.parent, command.inert = parent, inert or (prose and not command.substituted)
    commands += found
    if depth >= MAX_DEPTH:
        return
    for command in found:
        literal, interpreted = is_text_program(command), is_interpreter(command)
        texts = [(word, command.inert or (literal and not runs_inside(word)), literal)
                 for word in command.words + command.redirects if word != GROUP and NESTED.search(word)]
        texts += [(text, command.inert or not (interpreted or runs_inside(text)), not interpreted)
                  for text in command.stdin if text != GROUP]
        for text, inside_text, prose in texts:
            if budget[0] <= 0 or budget[1] < len(text):
                if not inside_text:
                    unparsed.append(text)
                continue
            budget[0] -= 1
            budget[1] -= len(text)
            failures = 0
            for nested_shell in ("bash", "powershell"):
                try:
                    _gather(text, nested_shell, depth + 1, command, inside_text, commands, unparsed, budget,
                            prose and not inside_text)
                except ValueError:
                    failures += 1
            if failures == 2 and not inside_text:
                unparsed.append(text)


def live_commands(commands):
    """The commands of all_commands' list that may run: every one when the line holds a shell or another interpreter
    outside literal text, since it may be reading any text in the line as code (echo '...' | bash); else those not
    inside literal text and not themselves a text program (the echo whose words are only printed)."""
    interpreted = any(not c.inert and is_interpreter(c) for c in commands)
    return [c for c in commands if interpreted or not (c.inert or is_text_program(c))]


def changes_directory(command):
    """Whether a Command moves the working directory of what follows: cd, pushd, Set-Location and their kin, or an
    option such as env -C or Start-Process -WorkingDirectory."""
    found = program_index(command.words, command.shell)
    if found is None:
        return False
    i, program = found
    names = [base_name(program)]
    if names[0] in WRAPPERS:
        names += [base_name(w) for w in command.words[i + 1:]]
    return any(name in CD for name in names) or any(CD_OPTION.fullmatch(w) for w in command.words[i + 1:])


def cwd_path(cwd):
    """The hook's cwd as an absolute path this process can open, or None: Git Bash's /c/... becomes C:/... ."""
    import os
    if not isinstance(cwd, str) or not cwd:
        return None
    if os.name == "nt":
        cwd = re.sub(r"^/([A-Za-z])(?=/|$)", r"\1:", cwd)
    return cwd if os.path.isabs(cwd) else None


def resolve(path, cwd):
    """(absolute path, None) for a file argument, or (None, why) when it cannot be known from here: a relative path
    with no cwd, a Git Bash mount other than a drive (/tmp), a drive-relative Windows path (C:file); or when the gate
    must not look, before anything is approved: a network or device path (//host/share, \\\\?\\UNC\\..., \\\\.\\pipe),
    which Windows opens by signing in to the host it names as the user."""
    import os
    if re.match(r"[\\/]{2}", path):
        return None, NETWORK_PATH
    if os.name == "nt":
        drive = re.match(r"/([A-Za-z])(?=/|$)", path)
        if drive:
            path = drive.group(1) + ":/" + path[3:]
        elif re.match(r"[/\\](?![/\\])|[A-Za-z]:(?![/\\])", path):
            return None, "the gate cannot tell which file this is"
    if os.path.isabs(path):
        return os.path.normpath(path), None
    if cwd is None:
        return None, "a relative path, and the gate cannot tell what it is relative to"
    return os.path.normpath(os.path.join(cwd, path)), None


def written(commands, runs):
    """The words by which a line may write files before the run the gate watches reads them: every redirection
    target, and every word of a command that is not such a run (runs, a set of command ids) and does not only read
    (READ_ONLY); none from literal text, nor from a command inside a run's own arguments."""
    for command in commands:
        if command.inert or command.within(runs):
            continue
        yield from command.redirects
        program = program_of(command)
        if id(command) not in runs and (program is None or base_name(program) not in READ_ONLY):
            yield from command.words


def names_file(word, name):
    """Whether a word names the file called name (lower case), in any folder, or matches it as a glob."""
    import fnmatch
    last = re.split(r"[\\/]", word)[-1].rstrip(" .").lower()
    return last == name or (re.search(r"[*?\[]", last) is not None and fnmatch.fnmatchcase(name, last))


def shown(text, limit):
    """text as a prompt may show it: whitespace runs as one space, control and format characters (bidi overrides,
    zero-width spaces) as ?, and cut to limit characters."""
    import unicodedata
    text = re.sub(r"\s+", " ", str(text)).strip()
    text = "".join("?" if unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp") else ch for ch in text)
    return text if len(text) <= limit else text[:limit - 3] + "..."


def flat(command):
    """The command with quotes, escapes and cmd's ^ out of the way, for the fast path's look at it."""
    return re.sub(r"[\"'`^\\]", "", command)


def say(mode, reason):
    """Prints the PreToolUse decision for reason, which starts with the plugin's name: ask, or deny when the
    permission_mode leaves nobody to ask (NOBODY_TO_ASK). json.dumps escapes everything past ASCII, so no console
    code page can choke on a dash or a warning sign; a crash would only block the command, with no reason given."""
    decision = "deny" if isinstance(mode, str) and mode in NOBODY_TO_ASK else "ask"
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                                        "permissionDecisionReason": reason}}) + "\n")
    sys.stdout.flush()


# --- The lprun9 gate --------------------------------------------------------------------------------------------------

UNREAD = "the gate could not read this command; approve only if you have read what it runs"

LPRUN9 = re.compile(r"lprun9(?:-[a-z0-9]+|~\d+)?")  # a base_name: LPRun9-x86, and an 8.3 name such as LPRUN9~1
DASHES = {code: "-" for code in (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0xFE58, 0xFE63, 0xFF0D)}
ALIASING = frozenset({"set-alias", "new-alias", "sal", "nal", "alias"})
# Wrappers that join the words after them into one command line and read it again, so a word the shell kept whole
# may reach lprun9 split (eval, watch, wsl, powershell) or whole (cmd /c, start, runas): split_again reads both ways.
# timeout, env, nohup and the like hand on the shell's words as they are.
REREAD = frozenset("cmd start call runas eval watch wsl pwsh powershell iex invoke-expression gsudo".split())
PIECES, BOTH = "pieces", "both"
CX_OPTION = re.compile(r"(?:-{1,2}|/)cx[\w-]*(?:[=:](.*))?", re.I | re.S)  # a whole argument: -cxname=Sales
CX_ANYWHERE = re.compile(r"(?<![\w/.-])(?:-{1,2}cx[\w-]*(?:[=:]([^\s,;|&)\]}'\"]*))?|/cx[\w-]*[=:]([^\s,;|&)\]}'\"]*))",
                         re.I)
LINQ_END = re.compile(r"\.linq(?![\w-]|\.\w)", re.I)
PATH_BREAK = re.compile(r"[\s'\"`,;|&<>()\[\]{}=]")  # what cannot sit in a path written inside code
BOMS = "".join(map(chr, (0xFEFF, 0xFFFE, 0xFF, 0xFE, 0xEF, 0xBB, 0xBF))) + " \t\r\n"
# LINQPad's header is the XML element <Query ...> at the top of a script; the Connection element inside it names the
# saved connection. A header's end is looked for once comments, CDATA and processing instructions are out of the way,
# so a </Query> hidden in one does not cut it short.
XML_ASIDES = re.compile(r"<!--.*?(?:-->|$)|<!\[CDATA\[.*?(?:\]\]>|$)|<\?.*?(?:\?>|$)", re.S)
QUERY_END = re.compile(r"</\s*Query\s*>|<\s*Query\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*/>", re.I)
CONNECTION_TAG = re.compile(r"<\s*(?:[\w.-]+:)?Connection\w*", re.I)
CONNECTION_ATTRIBUTE = re.compile(r"<\s*(?:[\w.-]+:)?Query\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*?\bConnection\w*\s*=", re.I)
CONNECTION_BLOCK = re.compile(r"<\s*(?:[\w.-]+:)?Connection\w*\s*>\s*<", re.I)  # an element, outside a header
CONNECTION_ELEMENT = re.compile(r"<\s*(?:[\w.-]+:)?Connection\w*\b[^>]*>(.*?)<\s*/\s*(?:[\w.-]+:)?Connection\w*\s*>",
                                re.I | re.S)
DTD = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)", re.I)  # an entity can write a Connection the text does not show
INCOMPLETE = object()


class Findings:
    """What the gate found in one command line: the connections it reaches, and the parts it could not read."""

    def __init__(self, reached=(), unread=()):
        self.reached, self.unread = [], []
        for text in reached:
            self.reach(text)
        for why in unread:
            self.cannot_read(why)

    def reach(self, text):
        if text not in self.reached:
            self.reached.append(text)

    def cannot_read(self, why):
        if why not in self.unread:
            self.unread.append(why)

    def __bool__(self):
        return bool(self.reached or self.unread)

    def reason(self):
        """The prompt's reason, the plugin's name first."""
        if self.reached:
            more = len(self.reached) - 4
            text = "; ".join(self.reached[:4]) + (f"; and {more} more" if more > 0 else "") + CX_TAIL
            if self.unread:
                text += "; the gate also could not read part of it (" + "; ".join(self.unread[:3]) + ")"
        else:
            text = f"{UNREAD} ({'; '.join(self.unread[:3])})"
        return f"{PLUGIN}: {text}"


def is_lprun9(word):
    """Whether a program word names lprun9: lprun9, LPRun9.exe, LPRun9-x86.exe or LPRUN9~1.EXE, in any case and any
    folder."""
    return LPRUN9.fullmatch(base_name(word)) is not None


def mentions_lprun9(text):
    """Whether text names lprun9 anywhere, in any case, once cmd's ^ escapes are out of the way."""
    return "lprun9" in text.replace("^", "").lower()


def undashed(text):
    """text with the dashes PowerShell takes for a hyphen (en dash, em dash, minus...) as hyphens, one character for
    one, so an offset into the result holds in text."""
    return text.translate(DASHES)


def cx_part(name):
    """The reason's words for a -cx option, naming the connection when the option gives its name."""
    return f'lprun9 against saved connection "{shown(name, 80)}"' if name else "lprun9 against a saved connection"


def lprun9_aliases(commands):
    """The names a line makes stand for lprun9: Set-Alias lp lprun9, New-Alias -Name lp -Value lprun9, alias
    lp=lprun9."""
    names = set()
    for command in commands:
        found = program_index(command.words, command.shell)
        if found is None or base_name(found[1]) not in ALIASING:
            continue
        values = [w for w in command.words[found[0] + 1:] if not w.startswith("-")]
        for value in values:
            name, _, target = value.partition("=")
            if target.split() and is_lprun9(target.split()[0]):
                names.add(base_name(name))
        if len(values) >= 2 and is_lprun9(values[1]):
            names.add(base_name(values[0]))
    return names


def lprun9_run(command, aliases=frozenset()):
    """When a Command runs lprun9, as its program or behind a wrapper such as timeout, env, cmd /c or Start-Process:
    (lprun9's arguments, whether a wrapper runs it, whether xargs or parallel adds arguments); else None. aliases are
    names the line made for lprun9."""
    run = find_run(command, lambda word: is_lprun9(word) or base_name(word) in aliases)
    return None if run is None else (run_arguments(command, run[0]), split_again(command, run), run[2])


def split_again(command, run):
    """How lprun9's arguments reach it through the wrappers before it, find_run's run: None when the words the shell
    split reach it as they are (timeout, env, nohup); PIECES when Start-Process joins them, unquoted, into a line lprun9
    splits again; BOTH when a wrapper joins them into a command line it reads again (REREAD), where a quoted word may or
    may not stay whole."""
    if not run[1]:
        return None
    if handed_on(command) is not None:
        return PIECES
    start = program_index(command.words, command.shell)[0]
    return BOTH if any(base_name(word) in REREAD for word in command.words[start:run[0]]) else None


def computed_run(command):
    """A run of a program the shell computes, as lprun9_run gives it: held in a variable ($LP x, & $lp x), or so
    behind a wrapper (timeout 120 $LP x, Start-Process -FilePath $lp -ArgumentList x); else None."""
    held = held_in_a_variable(command)
    if held is not None:
        return held, False, False
    run = find_run(command, lambda word: not word.startswith("-") and PROGRAM_EXPANSION.search(word) is not None,
                   direct=False)
    return None if run is None else (run_arguments(command, run[0]), split_again(command, run), run[2])


def linq_paths(word):
    """The .linq paths written inside a word, such as the 'q.linq' of python's ['lprun9', 'q.linq']."""
    for piece in PATH_BREAK.split(word):
        for end in LINQ_END.finditer(piece):
            yield piece[:end.end()]


def read_head(path, deadline):
    """The first 8 KB of a regular file, or None when it is not one, cannot be opened, or does not answer before the
    deadline (a network path). A FIFO or a device such as CON is never opened: reading one could wait forever."""
    import os, stat, threading, time
    left = deadline - time.monotonic()
    if left <= 0:
        return None
    box = []

    def read():
        try:
            if stat.S_ISREG(os.stat(path).st_mode):
                with open(path, "rb") as f:
                    box.append(f.read(HEAD_BYTES))
        except (OSError, ValueError):
            pass

    worker = threading.Thread(target=read, daemon=True)
    worker.start()
    worker.join(left)
    return box[0] if box else None


def readings(data):
    """A script's first bytes as text, the ways LINQPad might read them: by the byte order mark (UTF-8, UTF-16,
    UTF-32), as UTF-16 when NULs show it is that without one, else as UTF-8; and again with every NUL dropped."""
    for mark, codec in ((b"\xff\xfe\x00\x00", "utf-32-le"), (b"\x00\x00\xfe\xff", "utf-32-be"),
                        (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"), (b"\xef\xbb\xbf", "utf-8")):
        if data.startswith(mark):
            body = data[len(mark):]
            break
    else:
        body, codec = data, "utf-8"
        if data[1:2] == b"\x00":
            codec = "utf-16-le"
        elif data[:1] == b"\x00":
            codec = "utf-16-be"
    return body.decode(codec, "replace"), data.replace(b"\x00", b"").decode("latin-1")


def xml_text(value):
    """An element's text with XML's character references undone."""
    value = re.sub(r"&#[xX]([0-9a-fA-F]{1,6});", lambda m: chr(min(int(m.group(1), 16), 0x10FFFF)), value)
    value = re.sub(r"&#([0-9]{1,7});", lambda m: chr(min(int(m.group(1)), 0x10FFFF)), value)
    for entity, char in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&")):
        value = value.replace(entity, char)
    return value


def shown_path(text, limit):
    """A path as a prompt may show it: shown() with the middle cut out of a long one, so the end, the file's name,
    stays."""
    text = shown(text, 10**9)
    if len(text) <= limit:
        return text
    tail = limit * 2 // 3
    return text[:limit - tail - 3] + "..." + text[-tail:]


def header_field(text, tag):
    """A connection's DisplayName, Server or Database as the prompt shows it; one that reads like a connection
    string or a password shows as (not shown)."""
    found = re.search(rf"<\s*{tag}\s*>(.*?)<\s*/\s*{tag}\s*>", text, re.I | re.S)
    value = xml_text(found.group(1)).strip() if found else ""
    if not value:
        return None
    if re.search(r"[=;]|password|pwd", value, re.I):
        return "(not shown)"
    return shown(value, 80)


def describe(region):
    """'saved connection "Sales" (server / database)', from the text that holds the <Connection>; never its
    Password, its user name or a connection string."""
    element = CONNECTION_ELEMENT.search(region)
    inner = element.group(1) if element else ""
    display, server, database = (header_field(inner, tag) for tag in ("DisplayName", "Server", "Database"))
    what = f'saved connection "{display}"' if display else "a saved connection"
    where = " / ".join(value for value in (server, database) if value)
    return f"{what} ({where})" if where else what


def connection_in(data):
    """What a script's first 8 KB say about a saved connection: None for none; INCOMPLETE when a header starts there
    and does not end, or defines entities; else a description.

    In a script with a header, a Connection element anywhere in the 8 KB counts, or a Connection attribute on
    <Query>: generous, so a header that hides its end cannot slip one past, at the cost of asking about a
    List<Connection> in code below a header. With no header, only a whole <Connection><...> element counts."""
    unsure = False
    for text in readings(data):
        body = text.lstrip(BOMS)
        if body.startswith("<"):
            if CONNECTION_TAG.search(body) or CONNECTION_ATTRIBUTE.search(body):
                return describe(body)
            unsure = unsure or DTD.search(body) is not None or not QUERY_END.search(XML_ASIDES.sub("", body))
        elif CONNECTION_BLOCK.search(body):
            return describe(body)
    return INCOMPLETE if unsure else None


class Reader:
    """Opens the scripts a line names, against the hook's cwd, within one time budget, and records what they say."""

    def __init__(self, found, cwd, moved, deadline):
        self.found, self.cwd, self.moved, self.deadline = found, cwd, moved, deadline
        self.names = set()  # the scripts lprun9 surely runs, by file name
        self.checked = set()  # (path, run) for each check made, so a long line naming one script often opens it once

    def check(self, path, run=True):
        """Asks when the script at path holds a saved connection. run says lprun9 surely runs it, and then a path
        the gate cannot read asks too; a .linq merely named beside lprun9 asks only for a connection it can see."""
        if (path, run) in self.checked:
            return
        self.checked.add((path, run))
        label = shown_path(path, 120)
        if UNEXPANDED.search(path):
            if run:
                self.found.cannot_read(f"{label}: the gate cannot expand it")
            return
        full, why = resolve(path, self.cwd)
        if full is None:
            if run:
                moved = self.moved and why.startswith("a relative path")
                self.found.cannot_read(f"{label}: " + ("a relative path after a change of directory" if moved else why))
            return
        if run:
            self.names.add(re.split(r"[\\/]", path)[-1].rstrip(" .").lower())
        head = read_head(full, self.deadline)
        if head is None:
            if run:
                self.found.cannot_read(f"{label}: the gate cannot open it")
            return
        verdict = connection_in(head)
        if verdict is INCOMPLETE:
            self.found.cannot_read(f"{label}: its header does not end within the first 8 KB, or defines entities")
        elif verdict:
            self.found.reach(f'lprun9 {"runs" if run else "may run"} "{label}", which holds {verdict}')


def judge_run(arguments, split, fed, reads_input, reader):
    """lprun9's arguments, as split_again says they reach it: split again (PIECES), as the shell split them (None), or
    read both ways (BOTH), every finding of either reading kept. fed says xargs or parallel adds arguments."""
    if fed:
        reader.found.cannot_read("xargs or parallel supplies lprun9's arguments")
    pieces = [piece for argument in arguments for piece in split_argument_string(argument)]
    if split != PIECES or pieces == arguments:
        judge_arguments(arguments, fed, reads_input, reader)
    if split in (PIECES, BOTH) and pieces != arguments:
        judge_arguments(pieces, fed, reads_input, reader)


def judge_arguments(arguments, fed, reads_input, reader):
    """lprun9's arguments, one reading of them: a -cx option names a saved connection; the script (the first argument
    that is not an option) and any other .linq named must hold no <Connection>. @file, -, and no script at all while
    the line feeds lprun9's standard input (reads_input) leave the gate unable to tell."""
    found = reader.found
    script, skip = None, False
    for k, argument in enumerate(arguments):
        if skip:
            skip = False
            continue
        plain = undashed(argument)
        option = CX_OPTION.fullmatch(plain)
        if option:
            name = None if option.group(1) is None else argument[option.start(1):]
            if name is None and k + 1 < len(arguments):
                name, skip = arguments[k + 1], True
            found.reach(cx_part(name))
        elif argument.startswith("@"):
            found.cannot_read(f"{shown(argument, 60)}: a response file or a splatted variable")
        elif plain == "-":
            found.cannot_read("a script read from standard input")
        elif plain.startswith("-"):
            continue
        elif script is None:
            script = argument
        elif argument.rstrip(" .").lower().endswith(".linq"):
            reader.check(argument)
    if script is not None:
        reader.check(script)
    elif reads_input and not fed:
        found.cannot_read("a script read from standard input")


def judge_mentions(words, reader, linq=True):
    """Words that name lprun9 without running it where the gate can see (python's subprocess, a list built for
    Start-Process): any -cx option in them asks, and so does a .linq they name that holds a connection."""
    for word in words:
        for option in CX_ANYWHERE.finditer(undashed(word)):
            group = 1 if option.group(1) is not None else 2
            reader.found.reach(cx_part(word[option.start(group):option.end(group)] if option.group(group) else None))
        if linq:
            for path in linq_paths(word):
                reader.check(path, run=False)


def judge(command, shell, cwd):
    """The Findings for one command line, from every simple command in it, nested ones included."""
    import time
    found = Findings()
    try:
        commands, unparsed = all_commands(command, shell)
    except ValueError as e:
        if mentions_lprun9(command) or ".linq" in command.lower():
            found.cannot_read(f"the command line: {e}")
        return found
    for text in unparsed:
        if mentions_lprun9(text):
            found.cannot_read(f"a command inside it: {shown(text, 60)}")
    live = live_commands(commands)
    moved = any(changes_directory(c) for c in live)
    reader = Reader(found, None if moved else cwd_path(cwd), moved, time.monotonic() + FILE_BUDGET)
    names_lprun9 = any(mentions_lprun9(w) for c in live for w in c.words + c.redirects)
    aliases = lprun9_aliases(live) if names_lprun9 else frozenset()
    runs, judged, unjudged = False, set(), []
    for c in live:
        run = lprun9_run(c, aliases)
        runs = runs or run is not None
        if run is None and names_lprun9:  # a variable or a substitution in a line that names lprun9 may hold it
            run = computed_run(c)
        if run is not None:
            judged.add(id(c))
            judge_run(*run, c.reads_input, reader)
        elif not c.within(judged):  # an argument of a run judged above, such as "-cxname=RF Prod", read as a line
            unjudged += c.words + c.redirects
            if computed_run(c) is not None or any(mentions_lprun9(w) for w in c.words):
                judge_mentions(c.words + c.redirects, reader)
    if names_lprun9:
        judge_mentions(unjudged, reader, linq=not runs)
    if reader.names:
        writes = list(written(commands, judged))
        for name in sorted(reader.names):
            if any(names_file(word, name) for word in writes):
                found.cannot_read(f"{shown(name, 80)}: this command may write it before lprun9 reads it")
    return found


def main():
    try:
        hook = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        hook = None
    if not isinstance(hook, dict):
        return say(None, Findings(unread=["the hook's input is not a JSON object"]).reason())
    tool = hook.get("tool_name")
    if hook.get("hook_event_name", "PreToolUse") != "PreToolUse" or not isinstance(tool, str) or tool not in SHELLS:
        return
    shell, mode = SHELLS[tool], hook.get("permission_mode")
    tool_input = hook.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if command is None:
        return
    if not isinstance(command, str):
        return say(mode, Findings(unread=["the command is not text"]).reason())
    if not WORTH_READING.search(flat(command)):
        return
    try:
        found = judge(command, shell, hook.get("cwd"))
    except Exception as e:  # a bug here asks; a crash would only block, with no reason given
        found = Findings(unread=[f"the gate failed: {type(e).__name__}"])
    if found:
        say(mode, found.reason())


if __name__ == "__main__":
    main()
