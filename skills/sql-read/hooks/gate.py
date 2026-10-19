"""Asks before a read.py query or check, with the plan it would run as the reason: a PreToolUse hook on the Bash and
PowerShell tools, run by hooks.json.

Claude Code pipes the hook's JSON in, and the gate prints one decision or nothing. It asks when a command in the line
runs read.py (python, python3 or py with a path ending in read.py, quoted or not, or read.py itself, or python -m read,
also behind timeout, env, cmd /c, Start-Process and the like) with the query or check subcommand. The reason is
read.py's own text for the plan read.py plan would print for the same arguments: the connection, its server and
database, whether it is production, the limits, the SQL's first lines and any warning, and a warning when the details
come from a list other than LINQPad's own. The gate makes it with the read.py beside this hook, never with one the
command names, since it runs before anything is approved. read.py plan and read.py connections touch no database and
pass silently. A run whose plan the gate cannot make true to what will run (a quote that does not close, an argument
the shell computes, a relative path after cd, a network path, a SQL file the same line may write, an environment
variable the plan reads that the line changes, a script the line may write from read.py, as in cp read.py r.py &&
python3 r.py query) asks with the could-not-read reason. Under bypassPermissions and dontAsk there is nobody to ask, so
each ask becomes a deny with the same reason.

This is a speed bump, not a boundary: a command can be disguised past what this reads. The permission rules in the
README hold where this does not, for the forms they match; the gate adds the plan to the prompt. The fast path, for
another tool or a command that does not name read.py, imports only json, re and sys.
"""
import json, re, sys

PLUGIN = "sql-read"
PLAN_BUDGET = 6.0  # seconds for reading the line, loading read.py and making the plans, inside hooks.json's 10
MAX_PLANS = 3  # how many runs' plans one prompt shows

# Worth reading at all: the line names read.py, or has python run a module named read, once quotes and escapes are out
# of the way.
WORTH_READING = re.compile(r"read\.py|-\w*m\s*read\b", re.I)


# --- Reading a command line ------------------------------------------------------------------------------------------
# Shared with linqpad's gate, which keeps a copy of this section, down to the gate's own section below; a change to one
# goes in the other. It splits a Bash or PowerShell command line into simple commands, finds the program each runs (past
# assignments, call operators, quotes and wrappers such as timeout or cmd /c), reads the command lines nested in their
# words, and answers Claude Code. It errs toward finding a command that is not there, never toward missing one. It uses
# json, re and sys, imported at the top, and imports anything else inside the function that needs it.

SHELLS = {"Bash": "bash", "PowerShell": "powershell"}  # the hook's tool_name, and the shell its command is written for
NOBODY_TO_ASK = frozenset({"bypassPermissions", "dontAsk"})  # permission modes in which an ask becomes a deny
UNREAD = "the gate could not read this command; approve only if you have read the SQL"
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


# --- The read.py gate -------------------------------------------------------------------------------------------------

READ_PY = re.compile(r"read\.py(?::\S*)?")  # a base_name: read.py, or one of its NTFS streams such as read.py::$data
DATABASE = frozenset({"query", "check"})  # the subcommands that reach a database, matched in any case
PATH_OPTIONS = ("--sql", "--connections", "--lprun")  # the options that name a file the plan reads
NOT_RUNNERS = READ_ONLY | TEXT_PROGRAMS  # programs that read the files they name, or take their words as text
STDIN_SCRIPT = re.compile(r"-|/dev/(?:stdin|fd/\d+)|/proc/self/fd/\d+")  # python's script, read from standard input
MODULE, MODULE_READ = re.compile(r"-\w*m"), re.compile(r"-\w*mread")  # python's -m (-Im), and -mread: read.py run so
DEFINERS = frozenset("alias set-alias new-alias sal nal doskey".split())  # programs that make a name stand for another
BASH_FUNCTION = re.compile(r"(?:^|[\s;&|(){}])[^\s;&|()<>{}'\"`$=]+[ \t]*\([ \t]*\)")  # name() { ...; }
# The environment variables the plan reads: for the connection list SQL_READ_CONNECTIONS, else APPDATA or the home
# folder; for lprun9 LPRUN9, PATH and LOCALAPPDATA. A line that changes one can make the gate's plan untrue, and one
# that changes where python finds code (PYTHONPATH, PYTHONHOME and the like) can change what runs as read.py.
ENVIRONMENT = (r"(?:sql_read_connections|lprun9|localappdata|appdata|path|home|userprofile|homedrive|homepath"
               r"|python(?:path|home|userbase|pycacheprefix|platlibdir|caseok|inspect|startup|breakpoint|executable))")
SETS_ENVIRONMENT = re.compile(
    rf"(?<![\w.:/\\$@%-]){ENVIRONMENT}\s*\+?=(?!=)"  # NAME=x before a command; export, env, declare or cmd's set
    rf"|env:[\\/]?{ENVIRONMENT}\b|SetEnvironmentVariable"  # PowerShell: $env:NAME = x, Remove-Item Env:\NAME
    rf"|\bunset\b[^;&|\n]*?\b{ENVIRONMENT}\b"  # unset NAME
    rf"|\benv\b[^;&|\n]*?\s(?:-u\s*|--unset[=\s]\s*){ENVIRONMENT}\b",  # env -u NAME
    re.I)
CONNECTIONS_NOTE = "\u26a0 details from {}; lprun9 looks the name up in LINQPad's own list"


class Findings:
    """What the gate found in one command line: the read.py runs it can plan, as jobs, and what it could not read. A
    job is (read.py's arguments, the folder they are relative to, the --sql files they name as (path, as written), a
    note for the prompt or None)."""

    def __init__(self):
        self.jobs, self.unread = [], []

    def plan(self, arguments, cwd, sql_files, note):
        job = (tuple(arguments), cwd, tuple(sql_files), note)
        if job not in self.jobs:
            self.jobs.append(job)

    def cannot_read(self, why):
        if why not in self.unread:
            self.unread.append(why)


def is_read_py(word):
    """Whether a word names read.py: in any folder, in any case, or as one of its NTFS streams (read.py::$DATA)."""
    return READ_PY.fullmatch(base_name(word)) is not None


def mentions_read_py(text):
    """Whether text names read.py anywhere, in any case, once quotes and escapes are out of the way."""
    return WORTH_READING.search(flat(text)) is not None


def unsure(word):
    """Whether the shell may hand a program something other than word: an expansion, a glob, or cmd's ^ escape."""
    return UNEXPANDED.search(word) is not None or "^" in word


def read_py_run(command):
    """(the index of the word naming read.py, the index of the program) when a Command runs read.py, else None. The
    word is the program itself (./read.py) or any word after it, so read.py is found behind any interpreter, option or
    wrapper (python3 -I read.py, py -3 read.py, uv run read.py, timeout 60 python3 read.py); or it is the module
    python's -m runs, read (python3 -m read, -mread), which is read.py in the folder python starts in. A program that
    reads the files it names or takes its words as text (cat, grep, echo, git) runs nothing."""
    found = program_index(command.words, command.shell)
    if found is None or base_name(found[1]) in NOT_RUNNERS:
        return None
    words = command.words
    for k in range(found[0], len(words)):
        if (is_read_py(words[k]) or MODULE_READ.fullmatch(words[k])
                or (words[k] == "read" and k > found[0] and MODULE.fullmatch(words[k - 1]))):
            return k, found[0]
    return None


def subcommand(arguments):
    """The subcommand among read.py's arguments, as argparse finds it: the first that is not an option (a -- may stand
    before it), or None."""
    return next((word for word in arguments if not word.startswith("-")), None)


def option_values(arguments, names=PATH_OPTIONS):
    """(option, value) for each of the options names among arguments, written --name value or --name=value."""
    for k, word in enumerate(arguments):
        for name in names:
            if word == name and k + 1 < len(arguments):
                yield name, arguments[k + 1]
            elif word.startswith(name + "="):
                yield name, word[len(name) + 1:]


def sets_environment(line):
    """What a line changes in the environment the plan reads, or python's, as words for the prompt; or None."""
    match = SETS_ENVIRONMENT.search(line)
    if match is None:
        return None
    name = re.search(ENVIRONMENT, match.group(0), re.I)
    if name is None:
        return "the line changes an environment variable, which the plan reads"
    if name.group(0).lower().startswith("python"):
        return f"the line changes {name.group(0)}, which changes how python runs read.py"
    return f"the line changes {name.group(0)}, which the plan reads"


def defines_a_function(commands, line, shell):
    """Whether a line defines a function or an alias, which may stand for any program the line runs."""
    for command in commands:
        if command.words[:1] and command.words[0].lower() in ("function", "filter"):
            return True
        program = program_of(command)
        if program is not None and base_name(program) in DEFINERS:
            return True
    return shell == "bash" and BASH_FUNCTION.search(line) is not None


def computed_command(command):
    """The word by which a Command runs something the shell computes ($C --sql q.sql, iex "$c ...", bash -c "$C"),
    or None."""
    found = program_index(command.words, command.shell)
    if found is None or any(word.lower() in ("exit", "return", "throw") for word in command.words[:found[0]]):
        return None  # runs nothing, or the word is an exit code: exit $?
    if held_in_a_variable(command) is not None:
        return found[1]
    if is_interpreter(command) or base_name(found[1]) in WRAPPERS:
        return next((word for word in command.words[found[0] + 1:] if PROGRAM_EXPANSION.search(word)), None)
    return None


def nesting(command):
    """How many command lines deep a Command was found: 0 for one in the line itself."""
    depth, parent = 0, command.parent
    while parent is not None:
        depth, parent = depth + 1, parent.parent
    return depth


def defined_by_alias(command):
    """Whether a Command was found inside an alias definition, which adds the arguments it is given where it is
    used."""
    parent = command.parent
    while parent is not None:
        program = program_of(parent)
        if program is not None and base_name(program) == "alias":
            return True
        parent = parent.parent
    return False


def judge_other(command, found, interpreted):
    """A Command that runs no read.py itself, in a line that names it: it may run read.py out of the gate's sight when
    a query or check word follows a program or an argument the shell computes ($PY $R query), code (python3 -c
    "exec(...)" query), or a script python reads from standard input or a substitution (cat read.py | python3 -
    query, python3 <(cat read.py) query). With a shell or another interpreter in the line (interpreted), the words a
    program only prints may run too (echo python3 read.py query ... | bash)."""
    start = program_index(command.words, command.shell)
    if start is None:
        return
    i, words = start[0], command.words
    if base_name(start[1]) in NOT_RUNNERS:
        if interpreted and any(word.lower() in DATABASE for word in words[i + 1:]):
            found.cannot_read(f"{shown(' '.join(words), 60)}: text that a shell in the line may run")
        return
    for k in range(i + 1, len(words)):
        if words[k].lower() not in DATABASE:
            continue
        for j in range(i, k):
            if PROGRAM_EXPANSION.search(words[j]):
                return found.cannot_read(f"{shown(words[j], 60)}: the gate cannot expand it")
            if j > i and NESTED.search(words[j]):
                return found.cannot_read(f"{shown(words[j], 60)}: code the gate cannot follow")
        fed = command.reads_input and any(STDIN_SCRIPT.fullmatch(word) for word in words[i + 1:k])
        if fed or GROUP in command.redirects:
            return found.cannot_read("a script from standard input or a substitution, which may be read.py")


def judge_run(command, k, i, base, moved, changed, may_write, found):
    """A Command that runs read.py, named by command.words[k], with its program at [i]: a query or a check becomes a
    job to plan, unless the gate cannot make its plan true to what will run; any other subcommand (plan, connections)
    or none reads nothing and passes. read.py's arguments are the words after it, or, behind Start-Process, those it
    hands on after it. base is the folder relative paths start from, or None when unknown; moved, that the line changes
    directory; changed, an environment variable the line changes; may_write, whether the line may write a file, by its
    name."""
    handed = handed_on(command)
    arguments = command.words[k + 1:] if handed is None else [text for j, text in handed if j > k]
    if command.shell == "powershell":
        arguments = [word for word in arguments if word != "--%"]  # PowerShell's stop-parsing token stays behind
    name = subcommand(arguments)
    if name is None:
        if handed and any(text.lower() in DATABASE for j, text in handed if j < k):
            found.cannot_read("Start-Process hands read.py arguments that stand before it, which the gate cannot place")
        return
    if unsure(name):
        return found.cannot_read(f"{shown(name, 60)}: the gate cannot expand it")
    if name.lower() not in DATABASE:
        return
    whys = ["an alias, which adds the arguments it is given where it is used"] if defined_by_alias(command) else []
    for j in range(i, k):
        if PROGRAM_EXPANSION.search(command.words[j]):
            whys.append(f"{shown(command.words[j], 60)}: the gate cannot expand it")
        elif j > i and NESTED.search(command.words[j]):
            whys.append(f"{shown(command.words[j], 60)}: code the gate cannot follow, before read.py")
    if any(base_name(word) in FEEDERS for word in command.words[i:k]):
        whys.append("xargs or parallel supplies read.py's arguments")
    whys += [f"{shown(word, 60)}: the gate cannot expand it" for word in arguments if unsure(word)]
    if changed:
        whys.append(changed)
    sql_files = []
    for option, value in option_values(arguments):
        if unsure(value) or (option == "--lprun" and not re.search(r"[\\/]", value)):
            continue  # said above, or a program name, which read.py looks up on PATH
        label = shown(value, 120)
        full, why = resolve(value, base)
        if full is None:
            moved_away = moved and why.startswith("a relative path")
            whys.append(f"{label}: " + ("a relative path after a change of directory" if moved_away else why))
            continue
        if option == "--sql":
            sql_files.append((full, label))
        file_name = re.split(r"[\\/]", value)[-1].rstrip(" .").lower()
        if file_name and may_write(file_name):
            whys.append(f"{shown(file_name, 80)}: this command may write it before read.py reads it")
    for why in whys:
        found.cannot_read(why)
    if not whys:
        lists = [value for _, value in option_values(arguments, ("--connections",))]
        found.plan(arguments, base, sql_files, connections_note(lists))


def connections_note(lists):
    """The prompt's warning when the plan's details come from a list other than LINQPad's own, in which lprun9 looks
    the name up: the list --connections names (the last of lists), or else env SQL_READ_CONNECTIONS, as read.py reads
    it; or None."""
    import os
    if lists:
        return CONNECTIONS_NOTE.format(f"--connections {shown(lists[-1], 120)}")
    given = os.environ.get("SQL_READ_CONNECTIONS", "").strip()
    if len(given) >= 2 and given[0] == given[-1] == '"':
        given = given[1:-1].strip()
    return CONNECTIONS_NOTE.format(f"env SQL_READ_CONNECTIONS {shown(given, 120)}") if given else None


def write_test(writes):
    """may_write(name) for the words by which a line may write files: whether one names the file called name (lower
    case), in any folder, or matches it as a glob, as names_file decides it; each name is looked up once, so a line
    of many runs costs no more than one."""
    import fnmatch
    plain, globs, seen = set(), set(), {}
    for word in writes:
        last = re.split(r"[\\/]", word)[-1].rstrip(" .").lower()
        (globs if re.search(r"[*?\[]", last) else plain).add(last)

    def may_write(name):
        if name not in seen:
            seen[name] = name in plain or any(fnmatch.fnmatchcase(name, glob) for glob in globs)
        return seen[name]

    return may_write


def judge_copies(live, commands, ran, found):
    """A script the line may write from read.py, under another name, runs read.py out of the gate's sight (cp read.py
    r.py && python3 r.py query): in a line whose commands name read.py other than as text, a query or check run of a
    script the line may write asks."""
    runs = []
    for c in live:
        start = program_index(c.words, c.shell)
        if id(c) in ran or start is None or base_name(start[1]) in NOT_RUNNERS:
            continue
        k = next((k for k in range(start[0] + 1, len(c.words)) if c.words[k].lower() in DATABASE), None)
        if k is not None:
            runs.append((c, start[0], k))
    if not runs or not any(is_read_py(word) for c in live if not is_text_program(c) for word in c.words + c.redirects):
        return
    may_write = write_test(written(commands, {id(c) for c, _, _ in runs}))  # cp read.py r.py counts as a run
    for c, i, k in runs:
        for word in c.words[i:k]:
            name = re.split(r"[\\/]", word)[-1].rstrip(" .").lower()
            if name and not word.startswith("-") and may_write(name):
                found.cannot_read(f"{shown(name, 80)}: the line may write it from read.py before it runs")


def judge(line, shell, cwd):
    """The Findings for one command line, from every simple command in it, nested ones included."""
    found = Findings()
    try:
        commands, unparsed = all_commands(line, shell)
    except ValueError as e:
        found.cannot_read(f"the command line: {e}")
        return found
    for text in unparsed:
        if mentions_read_py(text):
            found.cannot_read(f"a command inside it: {shown(text, 60)}")
    live = live_commands(commands)
    for c in live:
        if nesting(c) >= MAX_DEPTH and any(NESTED.search(text) and mentions_read_py(text)
                                           for text in c.words + c.redirects + c.stdin):
            found.cannot_read("a command line nested deeper than the gate reads names read.py")
    runs = [(c, place) for c in live for place in [read_py_run(c)] if place is not None]
    ran = {id(c) for c, _ in runs}
    interpreted = any(not c.inert and is_interpreter(c) for c in commands)  # as live_commands decides it
    for c in live:
        if id(c) not in ran:
            judge_other(c, found, interpreted)
    judge_copies(live, commands, ran, found)
    if not runs:
        return found
    if defines_a_function(commands, line, shell):
        found.cannot_read("the line defines a function or an alias, which may stand for a program it runs")
    for c in live:
        computed = None if id(c) in ran else computed_command(c)
        if computed is not None:
            found.cannot_read(f"{shown(computed, 60)}: the gate cannot expand it")
    moved = any(changes_directory(c) for c in live)
    # A run with code before read.py (bash -c '...' read.py) may write files from inside its own words. A word that is
    # a command line naming read.py (bash -c 'cp a q.sql; python3 read.py ...') counts as the commands read from it.
    clear = {id(c) for c, (k, i) in runs if not any(NESTED.search(word) for word in c.words[i + 1:k])}
    may_write = write_test(word for word in written(commands, clear)
                           if not (NESTED.search(word) and mentions_read_py(word)))
    changed = sets_environment(line)
    base = None if moved else cwd_path(cwd)
    for c, (k, i) in runs:
        judge_run(c, k, i, base, moved, changed, may_write, found)
    return found


def load_read():
    """read.py from this plugin, beside the hook, never one the command names: the gate runs before anything is
    approved, and importing a read.py runs its code."""
    import importlib.util
    from pathlib import Path
    sys.dont_write_bytecode = True  # the gate writes nothing, not even a .pyc beside read.py
    path = Path(__file__).resolve().parent.parent / "skills" / "sql-read" / "scripts" / "read.py"
    spec = importlib.util.spec_from_file_location("sql_read", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def regular(path):
    """Whether path is a regular file, which the gate and read.py can read; never opens it."""
    import os, stat
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except (OSError, ValueError):
        return False


def prompts(jobs, deadline):
    """(the prompt's text, None) for each job, or (None, why) for one the gate could not plan. read.py makes the plan
    and its text in a thread of its own, within the deadline, so a slow disk or network path cannot hold the gate past
    its timeout, which would block the command with no reason given."""
    import threading, time
    results = []

    def work():
        try:
            read = load_read()
        except BaseException as e:
            results.append(e)
            return
        for arguments, cwd, sql_files, note in jobs:
            closed = [label for full, label in sql_files if not regular(full)]
            try:
                if closed:
                    results.append((None, f"{closed[0]}: the gate cannot open it"))
                else:
                    text = read.reason(read.plan_for_command(list(arguments), cwd))
                    results.append((text + ("\n" + note if note else ""), None))
            except BaseException as e:
                results.append((None, f"read.py could not make the plan ({type(e).__name__})"))

    if not jobs:
        return []
    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(max(0.0, deadline - time.monotonic()))
    done = list(results)
    if done and isinstance(done[0], BaseException):
        return [(None, f"the gate could not load read.py ({type(done[0]).__name__})")]
    return done + [(None, "read.py took too long to make the plan")] * (len(jobs) - len(done))


def decide(line, shell, cwd):
    """The reason to ask with for a command line, the plugin's name first, or None to pass it in silence."""
    import time
    deadline = time.monotonic() + PLAN_BUDGET
    found = judge(line, shell, cwd)
    jobs, texts = found.jobs[:MAX_PLANS], []
    for text, why in prompts(jobs, deadline):
        if why is None:
            texts.append(text)
        else:
            found.cannot_read(why)
    lines = [f"{PLUGIN}: {UNREAD} ({'; '.join(found.unread[:3])})"] if found.unread else []
    lines += texts
    more = len(found.jobs) - len(jobs)
    if more > 0:
        lines.append(f"{PLUGIN}: and {more} more read.py run{'s' if more > 1 else ''} in this command")
    return "\n".join(lines) or None


def main():
    try:
        hook = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        hook = None
    if not isinstance(hook, dict):
        return say(None, f"{PLUGIN}: {UNREAD} (the hook's input is not a JSON object)")
    tool = hook.get("tool_name")
    if hook.get("hook_event_name", "PreToolUse") != "PreToolUse" or not isinstance(tool, str) or tool not in SHELLS:
        return
    shell, mode = SHELLS[tool], hook.get("permission_mode")
    tool_input = hook.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if command is None:
        return
    if not isinstance(command, str):
        return say(mode, f"{PLUGIN}: {UNREAD} (the command is not text)")
    if not WORTH_READING.search(flat(command)):
        return
    try:
        reason = decide(command, shell, hook.get("cwd"))
    except Exception as e:  # a bug here asks; a crash would only block, with no reason given
        reason = f"{PLUGIN}: {UNREAD} (the gate failed: {type(e).__name__})"
    if reason:
        say(mode, reason)


if __name__ == "__main__":
    main()
