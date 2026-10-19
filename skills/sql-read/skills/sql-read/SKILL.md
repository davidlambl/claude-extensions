---
name: sql-read
description: Read from a SQL Server database through a saved LINQPad connection, production included, with a check and an approval on every run. Use when asked to query, look up, count or pull rows from a database that a LINQPad connection reaches, even when LINQPad is not named.
---

# SQL read

Runs one SELECT against a SQL Server database through a connection saved in LINQPad, with LINQPad 9's `lprun9`, which supplies the credential. The batch stops before the SELECT if the login can write, and caps the rows it returns. The user approves every run at a permission prompt that shows the connection and the SQL. The script is `scripts/read.py` under this skill's base directory.

## What it needs

- LINQPad 9's `lprun9`: on `PATH`, in LINQPad 9's install folder, or named by the environment variable `LPRUN9`.
- Python 3.11 or later, as `python3` on `PATH`.
- The connection saved in LINQPad with a name, and signed in with a login that can only read: `query` refuses one that can write.

## Read

1. List the saved connections, and pick one by its name:

   ```sh
   python3 "<base>/scripts/read.py" connections
   ```

   This list is the only way to see them. Never open, read or grep `ConnectionsV2.xml` or LINQPad's other files, and never ask the user for a connection string or a password. A connection listed as `(no name)` cannot be chosen: ask the user to give it a name in LINQPad.
2. Write the SQL to `<scratch>/<name>.sql`, in the session's scratch folder: one `SELECT` or `WITH` statement, with `TOP` and `ORDER BY`. Only `DECLARE @…` and `SET @…` may come before it: no `GO`, no `USE`, no second statement.
3. Preview it, and show the user the plan in chat: the connection, its server and database, whether it is production, the row cap and the SQL.

   ```sh
   python3 "<base>/scripts/read.py" plan --cx "<name>" --sql "<scratch>/<name>.sql"
   ```

   `plan` runs nothing. Exit 4 means the guard refused the SQL, and its message says why; a column named like a keyword passes in brackets, as `[save]`. Exit 3 means the connection or `lprun9` cannot be used.
4. When the plan exits 0, run it: the same command with `query` in place of `plan`. The permission prompt that appears is the approval: do not ask again in chat, do not run anything that avoids it, and never add an allow rule for it. If the user declines, stop. Never pass `--allow-writable-login` unless the user asked for it in this turn.
5. Report the rows in the reply, with the count, the time and any cap note from the summary line. Never write the results to a file, nor redirect them to one: they are business data.

`--max-rows N` sets the cap (200 by default, 0 for none) and `--timeout S` the time allowed (60 s by default). `check --cx "<name>"` lists what the login can write; it prompts like `query`.

## What comes out

stdout is lprun9's output unchanged, CSV by default. stderr ends with one line, such as `sql-read: Sales (sales-sql.example.net / Orders, production): 200 rows in 1.8 s — cap reached; there may be more: raise --max-rows or add TOP and ORDER BY`; the part from `cap reached` on appears only when the cap was reached.

Exit codes: 0 ran; 1 lprun9 failed, with its error shown; 2 the command line or the `--sql` file; 3 `lprun9` or the connection cannot be used (missing, not found or unnamed), or a file sql-read did not write is where its `.linq` goes (choose another `--name`; never move that file); 4 the guard refused the SQL; 5 the login can write, so nothing ran; 6 timed out. On 6, a connection that signs in with Azure AD interactively must be opened once in LINQPad to cache its token.

## Rules

- The guard is a shape check, and the login is the boundary. Say so when the probe refuses (exit 5): the user needs a login that can only read.
- No writes through this skill, not even small ones, and not with approval: an `UPDATE`, `INSERT`, `DELETE` or schema change is the user's to run in LINQPad or SSMS. Say so; at most, read the rows and hand the user the SQL.
- No other SQL client against these connections: no `sqlcmd`, `Invoke-Sqlcmd`, ODBC or ADO.NET code, and no `lprun9 -cxname=` or `.linq` of your own. Point the user to LINQPad or SSMS.
- Never copy a `<Connection>` block into a scratch file, and never read or print a password from `ConnectionsV2.xml`.
- When an MCP server in the session covers the database, use it. sql-read is for connections it does not cover, or when the user asks for LINQPad.
