# LINQPad

Runs C# and .NET code from the command line with LINQPad 9's `lprun9`, as a .NET 10 file-based app or as a `.linq` script, and asks you before any `lprun9` run that touches a saved LINQPad connection.

It comes as a skill: ask Claude to check how some C# behaves, or to run a `.linq` script, and it writes the code to a file in its scratch folder, runs it, and answers from what it printed. Reading from a database through a saved connection is the job of [sql-read](../sql-read/), which previews the SQL and checks the login on every run.

## How it works

1. The skill writes a file-based app, a `.cs` file of top-level statements that needs no project, and runs it with `lprun9 <absolute path>`, with `-lang=program` in front when lprun9 asks for a language: 9.11.10 on Windows ran the `.cs` bare; 9.10.20 on macOS asked for a language. The same file runs with `dotnet run <path>.cs` wherever the .NET 10 SDK is. It writes a `.linq` script instead when it needs what only LINQPad has: `Dump()`, an Expression one-liner, or your own script's saved connection.
2. `hooks/gate.py` is a PreToolUse hook on the Bash and PowerShell tools. It reads each command that names `lprun9` or a `.linq` file, and asks you before one that runs `lprun9` with `-cxname=`, or on a script whose first 8 KB hold a `<Connection>`. The prompt names the connection:

   ```
   linqpad: lprun9 against saved connection "Sales" — not through sql-read: no shape check, no row cap, no login probe
   ```

## Write a script

A file-based app runs as it is, and a `#:package` line brings in a NuGet package:

```csharp
#:package Humanizer.Core@2.14.1
using Humanizer;

Console.WriteLine(Math.Round(2.5));               // 2: a midpoint rounds to even
Console.WriteLine("file_based_app".Humanize());   // file based app
```

```sh
lprun9 "C:/Users/me/AppData/Local/Temp/round.cs"                                                # 9.11.10 on Windows ran it bare
"/Applications/LINQPad 9.app/Contents/MacOS/lprun9" -lang=program "/Users/me/scratch/round.cs"   # 9.10.20 on macOS asked for a language
dotnet run "C:/Users/me/AppData/Local/Temp/round.cs"
```

A `.linq` script starts with its header, an XML element, and a blank line:

```
<Query Kind="Program">
  <NuGetReference>Humanizer.Core</NuGetReference>
  <Namespace>Humanizer</Namespace>
</Query>

void Main(string[] args)
{
#if CMD
    Console.WriteLine($"lprun9 passed {args.Length} arguments");
#endif
    "file_based_app".Humanize().Dump();
}
```

- `Kind` is the language: `Expression`, `Statements` or `Program` for C#, or `VBExpression`, `VBStatements`, `VBProgram`, `FSharpExpression`, `FSharpProgram`, `SQL` or `ESQL`. A script with no header needs `-lang=` with one of those, or `E`, `S` or `P` for short.
- In a `Program`, `void Main(string[] args)` gets the arguments written after the script's path: `lprun9 C:/scripts/report.linq 2026-10 Sales`.
- `#if CMD` holds when lprun9 runs the script, and not in LINQPad's editor.
- `Dump()` writes to stdout as text, the default `-format`; an array comes out as JSON-like lines.
- A NuGet package, from `#:package` or `<NuGetReference>`, is fetched on the script's first run, which takes longer than the runs after it. A `.cs` file's first `dotnet run` also compiles it. On 9.10.20 a `#:package` line is honoured under `-lang=program`: a `.cs` of `#:package Humanizer@2.14.1`, `using Humanizer;` and `Console.WriteLine("test string".Pascalize());` run so exited 0 and printed `TestString`, with `Downloading package Humanizer 2.14.1 and dependencies from https://api.nuget.org/v3/index.json` on stderr; run bare, it exited 1 with `You must specify a language (-lang=...) with this kind of script.` and printed nothing.
- A `<Connection>` element in the header points the script at a saved connection, and the hook asks before every run of it.

## The traps

lprun9 9.11.10 on Windows, as tested, and for the `.cs` row 9.10.20 on macOS:

| You might try | What happens | Instead |
| --- | --- | --- |
| Inline code, `lprun9 -lang=e "2 + 2"` | `Unable to locate file '2 + 2'`: a script file is mandatory | Write the code to a file |
| `-format=json` | `Valid values are: Text, HTML, HTMLFrag, CSV, CSVi`. Text is the default | Serialize inside the script, with `System.Text.Json.JsonSerializer` |
| A `.linq` with no header | `You must specify a language` | Write a `<Query Kind="…">` header, or pass `-lang=` |
| A `.cs` file alone, `lprun9 app.cs` | On 9.10.20 on macOS, `You must specify a language (-lang=...) with this kind of script.`, exit 1; 9.11.10 on Windows ran it bare | When lprun9 asks for a language, pass `-lang=program` before the path; a `#:package` line is honoured there too |
| A relative path, `lprun9 round.linq` | Looked for in the current folder, then in LINQPad's My Scripts folder, which may be a network share | Pass the absolute path |
| `-compileonly` as a dry run of a SQL script with `-cxname=` | It blocked for over two minutes and was killed | It is no dry run: never point it at a connection |
| `-cxname=` with a connection that has no display name | Unnamed connections are ambiguous, so it cannot be chosen | Give it a name in LINQPad |

An error goes to stderr, and lprun9 exits with a code other than 0.

## Use it

Install it from a Claude Code prompt in a terminal:

```
/plugin install linqpad --marketplace davidlambl/claude-extensions
```

Answer `y` to add the marketplace, then choose a scope.

Requirements: LINQPad 9 with `lprun9`, or the .NET 10 SDK for `dotnet run`; and Python 3.11 or later as `python3` on the `PATH`, for the hook. On Windows `lprun9` installs as `C:\Program Files\LINQPad9\LPRun9.exe` (or `%LOCALAPPDATA%\Programs\LINQPad9\LPRun9.exe`) and is on the `PATH`; on macOS it is `/Applications/LINQPad 9.app/Contents/MacOS/lprun9` (or the same under `~/Applications`), on no `PATH` unless you put it there, so the command names it by that path, quoted, and the hook reads it there as it reads a bare `lprun9`. Built and tested on Windows 11, with LINQPad 9.11.10 and the .NET 10 SDK, and on macOS with LINQPad 9.10.20, the builds on the machines this was tested on; on the Mac a `.cs` file ran with `-lang=program`, and bare it asked for a language.

To make the prompt a guarantee rather than a hook, add ask rules to your user settings, `~/.claude/settings.json`:

```json
{
  "permissions": {
    "ask": ["Bash(*un9*)", "Bash(*uN9*)", "Bash(*Un9*)", "Bash(*UN9*)", "PowerShell(*lprun9*)",
            "Bash(*d.py*query*)", "Bash(*d.pY*query*)", "Bash(*d.Py*query*)", "Bash(*d.PY*query*)",
            "Bash(*D.py*query*)", "Bash(*D.pY*query*)", "Bash(*D.Py*query*)", "Bash(*D.PY*query*)",
            "Bash(*d.py*check*)", "Bash(*d.pY*check*)", "Bash(*d.Py*check*)", "Bash(*d.PY*check*)",
            "Bash(*D.py*check*)", "Bash(*D.pY*check*)", "Bash(*D.Py*check*)", "Bash(*D.PY*check*)",
            "PowerShell(*read.py*query*)", "PowerShell(*read.py*check*)", "Bash(*m read *query*)",
            "Bash(*m read *check*)", "PowerShell(*m read *query*)", "PowerShell(*m read *check*)",
            "Bash(*mread *query*)", "Bash(*mread *check*)", "PowerShell(*mread *query*)",
            "PowerShell(*mread *check*)"]
  }
}
```

The first five cover `lprun9`; the other twenty-six cover sql-read's runs (its README says which forms), and cost nothing without it. A Bash pattern keeps the letter case it is written in (Claude Code compiles it to a case-sensitive match; a PowerShell pattern ignores case), and Windows, and macOS by default, ignore case in a file name, so the four Bash rules spell `un9` in each of its casings: together they match `lprun9` however it is written, `LPRUN9.EXE` and `C:/PROGRA~1/LINQPAD9/LPRUN9.EXE` included, where `Bash(*lprun9*)` alone would miss both. They also prompt for any other Bash command that holds those letters, such as `git log --grep=run9`. A PowerShell pattern ignores case, so one rule covers it. An ask rule prompts even in auto mode, and even for a command inside `cd x && …` or `$(…)`, and it holds with the plugin disabled. It cannot read a script, so it prompts for every `lprun9` run, a scratch `.cs` file included; `dotnet run` runs one without a prompt. The hook adds what a rule cannot: which connection the run touches.

## What it costs and touches

- **It runs code on your machine, as you.** What the script does, it does. Scripts go in Claude's scratch folder unless you say otherwise.
- **The hook asks before `lprun9` touches a saved connection:** a run with `-cxname=`, or of a script whose first 8 KB hold a `<Connection>`, read from the path resolved against the session's folder. It finds `lprun9` in any letter case, with or without a folder, and behind `timeout`, `env`, `cmd /c`, `Start-Process` and the like. A command that does not run `lprun9` is silent, so `cat q.linq`, `type q.linq` and `code q.linq` never prompt. A run it cannot read, such as a quote that does not close, a path in a variable or a script that is not there, asks too, with `linqpad: the gate could not read this command; approve only if you have read what it runs`.
- **With nobody to ask, it refuses.** In the `bypassPermissions` and `dontAsk` modes each ask becomes a deny, with the same reason.
- **It is a speed bump, not a boundary.** A script's own code can open a connection with a connection string, and a command can be disguised past what the hook reads. Claude Code calls an `if` pattern best-effort, and runs the hook anyway when it cannot parse the command. A Bash pattern keeps the letter case written, so the Bash handlers match `un9` in its four casings (`*un9*`, `*uN9*`, `*Un9*` and `*UN9*`), which together catch `lprun9` in any letter case, `LPRUN9.EXE` included; a PowerShell pattern ignores case, so one handler matches `lprun9` there. The `.linq` handlers catch a run of a `.linq` file however the program is spelled. A mod that handles `tool.check` can approve past the hook. The ask rules above are the guarantee.
- **Forms that get past the hook, on purpose.** These are set-on-it disguises, and the ask rules cover only those that still name `lprun9`: a copy of a connection-holding script made by a program the hook does not know writes files (`dd if=conn.linq of=plain.linq && lprun9 plain.linq`, `curl -o`, `wget -O`), where `cp`, `mv`, `tee`, `sed -i` and redirects are seen; a .NET `ProcessStartInfo` with `.FileName = 'lprun9'` and `.Arguments = 'conn.linq'`; and a drive-relative path such as `C:conn.linq`. The script's own code can open a connection by connection string whatever the hook does. A `List<Connection>` in C# below a header asks, as a script that holds a saved connection does.
- **Its cost is one Python start, about a quarter of a second, and only for a command that names a `.linq` or, in Bash, holds `un9` in any letter case (in PowerShell, `lprun9`).** Each handler carries an `if` pattern, so any other command starts nothing, and a Bash command that holds `un9` without running `lprun9`, such as `git log --grep=run9`, starts the hook and is let through silently. A command that matches two handlers, such as `lprun9 x.linq`, still gives one prompt.
- **A failed hook blocks the command.** If the hook crashes, overruns its 10-second timeout, or finds no `python3` on the `PATH`, Claude Code blocks the command (`onFailure: "block"`), so a broken hook never lets a run through unasked. That applies only to the commands that start the hook, those above. The hook is started with no shell, so `python3` must be a program on the `PATH`; a Git Bash alias does not count.
- **It leaves LINQPad's own files alone.** The hook reads only the scripts a command names, and the skill tells Claude never to open `ConnectionsV2.xml`, where LINQPad keeps your connections and their passwords.

## Dependencies

Python's standard library, for the hook. LINQPad 9 for `lprun9`, or the .NET 10 SDK for `dotnet run`.
