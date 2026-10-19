---
name: linqpad
description: Run C# and .NET code — a .NET 10 file-based app or a .linq script — from the command line with LINQPad 9's lprun9. Use when asked to run, try or verify some C# or .NET behaviour quickly, or to run a .linq script, even when LINQPad or lprun is not named.
---

# LINQPad

Runs C# from a script file with `lprun9`, LINQPad 9's command-line runner, and prints what the script writes. The plugin's hook asks the user before any `lprun9` run that touches a saved LINQPad connection.

## What it needs

- LINQPad 9's `lprun9` on `PATH`, installed as `C:\Program Files\LINQPad9\LPRun9.exe`. Without it, the .NET 10 SDK runs the same `.cs` file with `dotnet run`.
- `python3` on `PATH`, for the hook. Without it, every command that names `lprun9` or a `.linq` file is blocked.

## Run some C#

1. Write a .NET 10 file-based app: a `.cs` file of top-level statements, in the session's scratch folder (the temp folder when there is none), never in the repo unless the user asks. It needs no project and no `dotnet new`. A NuGet package comes in with a line at the top, such as `#:package Humanizer.Core@2.14.1`.
2. Run it by its absolute path, quoted, with forward slashes:

   ```sh
   lprun9 "C:/Users/<you>/AppData/Local/Temp/scratch/round.cs"
   ```

   `dotnet run "<path>.cs"` runs the same file wherever the .NET 10 SDK is. Its first run compiles, so it takes longer.
3. Answer from what the run printed, not from memory.

A `.linq` script is for what only LINQPad gives: `Dump()`, an Expression one-liner, or the user's own script with a saved connection. It starts with a header and a blank line:

```
<Query Kind="Statements" />

new[] { 3, 1, 2 }.OrderBy(n => n).Dump();
```

`Kind` is `Expression`, `Statements` or `Program` for C#. In a `Program`, `void Main(string[] args)` gets the arguments written after the script's path.

lprun9's traps, each a common wrong guess:

- It takes no inline code: `lprun9 -lang=e "2 + 2"` fails with `Unable to locate file '2 + 2'`. Write the code to a file every time; never pipe it in.
- It has no JSON format. `-format=` takes `Text` (the default), `HTML`, `HTMLFrag`, `CSV` or `CSVi`. For JSON, serialize inside the script with `System.Text.Json.JsonSerializer.Serialize`.
- A `.linq` with no header needs `-lang=`, or it stops with `You must specify a language`. Write the header instead.
- A relative path is looked for in the current folder, then in LINQPad's My Scripts folder, so it can run some other script. Always pass the absolute path.

## What comes out

What the script writes, `Dump()` included, comes out on stdout as text. An error goes to stderr, with a non-zero exit code. Report the output as printed, and say what it shows.

## Rules

- Code runs from a file, by its absolute path: never inline, never piped in.
- Touch no saved connection from here: pass no `-cxname=`, write no `<Connection>` element or connection string into a script, and never ask the user for one. Reads from a database go through the sql-read skill. A write, even a small one, is the user's to run in LINQPad or SSMS: say so, and at most hand them the SQL.
- Running the user's own `.linq` that holds a `<Connection>` prompts first. That prompt is the approval: do not ask again in chat, and run nothing that avoids it.
- Never open, read or grep LINQPad's own files in `%APPDATA%\LINQPad` or `%LOCALAPPDATA%\LINQPad`. `ConnectionsV2.xml` there holds the saved connections' passwords.
