# SQL read

Reads from a SQL Server database through a connection saved in LINQPad, production included. Each run is one SELECT: a guard checks its shape before anything runs, the batch stops before the SELECT if the login can write, the rows it returns are capped, and you approve the run at a permission prompt that shows the connection and the SQL.

It comes as a skill: ask Claude for the twenty most recent orders, a count, or a row by its key, and it writes the SQL, shows you the plan, and runs it once you approve. The script also runs on its own. LINQPad 9's `lprun9` runs the SQL and supplies the credential, so nothing here handles a password.

## How it works

1. `read.py` checks the SQL with the guard: one `SELECT` or `WITH` statement, with only `DECLARE @…` and `SET @…` before it, and no word from the deny list. A query it refuses stops there, with the reason, before any connection is looked up.
2. It looks the connection up by name in LINQPad's connection list, `%APPDATA%\LINQPad\ConnectionsV2.xml`: its server, database, sign-in kind, user name and production flag. The password is never read out.
3. It puts a prelude in front of the SQL and hands the batch to the transport, lprun9. The transport writes `<out>/<name>.linq`, a `<Query Kind="SQL" />` header, a blank line and the batch, and runs `lprun9 -cxname=<name> -format=<format> <absolute path>`. Only the connection's name reaches lprun9; LINQPad supplies the credential itself.
4. lprun9's output goes to stdout unchanged, and one summary line to stderr.
5. `hooks/gate.py` is a PreToolUse hook on the Bash and PowerShell tools. For a `read.py query` or `read.py check` command it builds the plan `read.py plan` would print, and asks you with it.

The connection list and lprun9 sit behind two small interfaces, a registry and a transport, so another way to run the batch can be added without touching the guard or the gate.

## The prompt

What you approve, for a read on a production connection:

```
sql-read: read on "Sales" — sales-sql.example.net / Orders — PRODUCTION
up to 200 rows · 60 s timeout · lock timeout 5 s
| SELECT TOP (20) OrderId, Placed
| FROM dbo.Orders
| ORDER BY Placed DESC;
```

It shows the SQL's first 8 lines, at most 600 characters, each marked with `|` so that no line of the SQL can pass for a line of the prompt's own, then how many lines it left out. `⚠ writable login allowed`, `⚠ no row cap` and `NOLOCK (dirty reads)` follow when they apply, and `⚠ details from --connections …` (or `from env SQL_READ_CONNECTIONS …`) when the plan was read from a list other than LINQPad's own, since lprun9 looks the name up in LINQPad's list whatever list `read.py` read. A check reads `login check on "Sales"`, with `lists what this login can write` in place of the row cap, and shows no SQL. A command that will not run says why first, such as `sql-read: will not run (exit 4): the guard refused the SQL: UPDATE is refused: the SQL may only read, as one SELECT`; SQL the guard refuses is not shown. Control and invisible characters in it are shown as escapes, never as themselves.

## Use it

Install it from a Claude Code prompt in a terminal:

```
/plugin install sql-read --marketplace davidlambl/claude-extensions
```

Answer `y` to add the marketplace, then choose a scope.

Requirements: LINQPad 9 with `lprun9`, and the connections saved in it, each with a name; Python 3.11 or later as `python3` on the `PATH`; and, for each connection, a login that can only read, or the connection listed in `SQL_READ_WRITABLE_OK` (below). Built on Windows 11 with LINQPad 9.11.10. The tests run offline, on Windows and on Linux, with lprun9 faked.

To make the prompt hold without the hook, add ask rules to your user settings, `~/.claude/settings.json`:

```json
{
  "permissions": {
    "ask": ["Bash(*un9*)", "Bash(*uN9*)", "Bash(*Un9*)", "Bash(*UN9*)", "PowerShell(*lprun9*)",
            "Bash(*d.p*query*)", "Bash(*D.p*query*)", "Bash(*d.P*query*)", "Bash(*D.P*query*)",
            "Bash(*d.p*check*)", "Bash(*D.p*check*)", "Bash(*d.P*check*)", "Bash(*D.P*check*)",
            "PowerShell(*read.py*query*)", "PowerShell(*read.py*check*)", "Bash(*m read *query*)",
            "Bash(*m read *check*)", "PowerShell(*m read *query*)", "PowerShell(*m read *check*)",
            "Bash(*mread *query*)", "Bash(*mread *check*)", "PowerShell(*mread *query*)",
            "PowerShell(*mread *check*)"]
  }
}
```

The eighteen `query` and `check` rules cover those two commands, whichever transport runs them, in each form the hook itself catches: `read.py` by its path, `python3 -m read` or `-mread` (from the scripts folder or with `PYTHONPATH`), and a quoted command such as `'query'`. They match the command's text, not what it does, so they miss a copy or a rename of `read.py`, as in `cp read.py r.py && python3 r.py query`, a script or an alias that runs it, and a module name spelled in other letter case, as in `python3 -m READ`. A Bash pattern matches only the letter case it is written in, and Windows does not care how a file name is cased, so the Bash rules and the hook's Bash patterns for `read.py` name the fragment `d.p` in each of its four casings: `READ.PY` and `Read.py` are caught, and so is any other Bash command that holds those letters and, for a rule, the word `query` or `check`. The hook asks for the copy. The five `lprun9` rules make every other run of lprun9 prompt too: the four Bash ones spell `un9` in each casing, which catches `LPRUN9.EXE` and `C:/PROGRA~1/LINQPAD9/LPRUN9.EXE` and also prompts for any other Bash command holding those letters; since `lprun9 -cxname=` reaches the same connections with none of these checks; that includes a run that touches no connection. An ask rule prompts even in auto mode, and even for a command inside `cd x && …` or `$(…)`, and it holds with the plugin disabled. `read.py plan` and `read.py connections` match none of them unless an argument holds the word `query` or `check`, as a `--sql query.sql` would, so a read prompts once. The hook adds what a rule cannot: the plan, with the SQL.

When a connection has no login that can only read, you can let it run with the one it has. List it in `SQL_READ_WRITABLE_OK`, in the `env` of your Claude Code settings: connection names, comma-separated and matched as `--cx` is, or `*` for every connection.

```json
{ "env": { "SQL_READ_WRITABLE_OK": "Web-Prod, Sales" } }
```

The variable changes nothing that runs. It is your leave for Claude to pass `--allow-writable-login` for those connections: `plan` says when a connection is listed, and only that flag, on the command line the hook reads, leaves the probe out. So the prompt always knows: it shows `⚠ writable login allowed (listed in env SQL_READ_WRITABLE_OK)`, or `(NOT listed …)` when the flag comes without your leave, as the hook's own environment has it. The guard still refuses anything but one SELECT, every run still asks, and `check` still probes. The hook asks with "could not read" when a command line sets the variable itself. With the flag, the guard and your approval are all that stand between the SQL and a write.

In auto mode the classifier refuses the flag as a safety bypass, since it cannot see your setting. To let it through to the hook's prompt, add the allow rules that `read.py rules` prints, into `permissions.allow` of your settings:

```sh
python3 <path to read.py> rules
```

They name this install's `read.py` by its full path, quoted, then `query --allow-writable-login`, with a `*` only at the end. One before the subcommand, as in `*read.py*`, would also approve whatever options stand there, and Claude Code warns of it. `plan`'s note gives Claude the command in exactly that form. A rule cannot hold a variable, so when an update moves the plugin, run `rules` again and replace the two lines.

The rule only takes the classifier out of the way: the hook still asks for every run, with the plan and the warning, and an ask beats an allow. Claude never adds this rule; it is yours to add.

```sh
python3 skills/sql-read/skills/sql-read/scripts/read.py connections
python3 skills/sql-read/skills/sql-read/scripts/read.py plan  --cx Sales --sql recent-orders.sql
python3 skills/sql-read/skills/sql-read/scripts/read.py query --cx Sales --sql recent-orders.sql
python3 skills/sql-read/skills/sql-read/scripts/read.py check --cx Sales
```

| Command | What it does |
| --- | --- |
| `connections [--json]` | Lists the saved connections, named or not: name, sign-in kind (`sql`, `windows` or `aad`), production flag, server and database, and user name. One with no name is listed as `(no name)` and cannot be chosen until you name it in LINQPad. `--json` prints an object with `source`, the list's path, and `connections`, each with `name`, `server`, `database`, `auth`, `user` and `production`. Touches no database. |
| `rules` | Prints, as JSON for your settings, the two allow rules that let `query --allow-writable-login` past auto mode's classifier, with this `read.py`'s own path. Touches no database. |
| `plan --cx NAME [--sql FILE] [query options]` | Prints what `query` would run, or `check` without `--sql`, as one JSON object, and runs nothing. Its fields: `command`, `transport`, `connection`, `server`, `database`, `production`, `auth`, `sql`, `guard` (`ok` and `reason`), `max_rows`, `timeout`, `lock_timeout_ms`, `nolock`, `allow_writable_login`, `format`, `artifact` (the `.linq` it would write), `argv` (the command it would run), `exit` and `error`. `sql` is `null` when the guard refuses the SQL, so a wrong `--sql` file is never copied into the output. It exits 2 for the command line or the `--sql` file, 4 when the guard refuses the SQL, 3 when the connection or lprun9 cannot be used, else 0. |
| `query --cx NAME --sql FILE [query options]` | Runs the SQL once, after the guard and with the prelude below. |
| `check --cx NAME [--timeout S] [--out DIR] [--name NAME]` | Lists what the login can write: one row for each write role, database permission or schema permission it holds. Exits 0 when it holds none, 5 when it holds any, with a one-line verdict on stderr, and 1 when its output has no `kind,name` header. |

| Option | What it does |
| --- | --- |
| `--cx NAME` | The connection, by the name LINQPad shows, in any letter case, with spaces around it ignored. |
| `--sql FILE` | The SQL, as UTF-8, or as UTF-16 with a byte order mark. It must be a regular file. |
| `--max-rows N` | The most rows the SELECT returns, through `SET ROWCOUNT`; 200 by default, and 0 for no cap. |
| `--format F` | `csv` (the default), `csvi`, `text` or `html`, passed to lprun9 as its own format. Rows are counted for `csv` and `csvi` only. |
| `--timeout S` | Seconds before the run is stopped, above 0 and at most 86400; 60 by default. |
| `--lock-timeout MS` | Milliseconds a read waits for a lock before it fails; 5000 by default. |
| `--nolock` | Reads uncommitted rows rather than wait for locks: dirty reads. |
| `--allow-writable-login` | Leaves out the probe that stops the run when the login can write. Claude passes it for a connection listed in the environment variable `SQL_READ_WRITABLE_OK`, or when you ask. |
| `--out DIR` | Where the run's `.linq` file goes. By default a `sql-read` folder beside the `--sql` file, for `check`, a `sql-read` folder in the temp folder. Whatever the folder, the run replaces only a `.linq` that sql-read wrote: if anything else is at `<out>/<name>.linq`, a query you saved there included, it is left as it is and the run exits 3 with nothing run. |
| `--name NAME` | The `.linq` file's name, without extension: letters, digits, `.`, `_` and `-`. By default the `--sql` file's name, and `check` for `check`. |
| `--connections FILE` | The connection list. By default the file the environment variable `SQL_READ_CONNECTIONS` names, else `%APPDATA%\LINQPad\ConnectionsV2.xml`. |
| `--via lprun9` | The transport that runs the batch; lprun9 is the only one. |
| `--lprun PATH` | lprun9 itself. By default the program the environment variable `LPRUN9` names, else `lprun9` on the `PATH`, else `C:\Program Files\LINQPad9\LPRun9.exe`, else `%LOCALAPPDATA%\Programs\LINQPad9\LPRun9.exe`. |

Every option is spelled out in full: `--allow` does not stand for `--allow-writable-login`. `--max-rows` and `--lock-timeout` take whole numbers up to 2,147,483,647.

Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | It ran; for `check`, the login holds no write role, no database permission and no schema permission to write. |
| 1 | lprun9 failed, with its error shown, or could not start; `check` printed no `kind,name` header; or another error. |
| 2 | The command line, or a `--sql` file that is missing, is not a regular file, or is not UTF-8 text. |
| 3 | lprun9 is missing; something sql-read did not write is where its `.linq` goes; the connection list cannot be read; the connection is not found or has no name; or the transport cannot sign in the way the connection does (lprun9 serves all three kinds). |
| 4 | The guard refused the SQL. |
| 5 | The login can write, so nothing ran. |
| 6 | It timed out. |
| 130 | Stopped with Ctrl+C. |

`query` and `check` print lprun9's output to stdout, byte for byte. Everything else `read.py` writes while it runs goes to stderr as UTF-8, and ends with one summary line; `connections`, `plan` and `rules` print their listing, JSON and rules to stdout, with any note on stderr:

```
sql-read: Sales (sales-sql.example.net / Orders, production): 200 rows in 1.8 s — cap reached; there may be more: raise --max-rows or add TOP and ORDER BY
```

Rows are counted with Python's `csv` module, as the records after the header, for `csv` and `csvi` only. The note after the dash appears only when the count reaches the cap. Exit 5 says `this login can write to this database, so nothing ran`, and leaves out lprun9's own report of the probe's error. Exit 6 says `timed out after 60 s; if the connection signs in with Azure AD interactively, open it once in LINQPad first to cache the token`. Nothing writes the results to disk.

## The batch

`query` sends lprun9 one batch: this prelude, then the SQL byte for byte. The comments say which option changes a line; they are not sent.

```sql
SET NOCOUNT ON;
SET LOCK_TIMEOUT 5000;                                   -- --lock-timeout
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;        -- only with --nolock
IF IS_SRVROLEMEMBER('sysadmin') = 1                      -- the probe, left out only with --allow-writable-login
   OR IS_MEMBER('db_owner') = 1 OR IS_MEMBER('db_datawriter') = 1 OR IS_MEMBER('db_ddladmin') = 1
   OR EXISTS (SELECT 1 FROM sys.fn_my_permissions(NULL, 'DATABASE') WHERE permission_name IN
      ('INSERT','UPDATE','DELETE','ALTER','CONTROL','CREATE TABLE','CREATE PROCEDURE','CREATE FUNCTION',
       'CREATE VIEW','ALTER ANY SCHEMA','TAKE OWNERSHIP','ALTER ANY USER','ALTER ANY ROLE'))
   OR EXISTS (SELECT 1 FROM sys.schemas AS s CROSS APPLY sys.fn_my_permissions(s.name, 'SCHEMA') AS p
      WHERE p.permission_name IN ('INSERT','UPDATE','DELETE','ALTER','CONTROL','TAKE OWNERSHIP'))
  THROW 50001, 'sql-read-guard: this login can write to this database', 1;
SET ROWCOUNT 200;                                        -- --max-rows, left out with --max-rows 0
```

`SET ROWCOUNT` caps the rows the SELECT returns without rewriting it, where a `TOP (n) * FROM (…)` wrapper would break on `ORDER BY` and on duplicate column names. The batch is written for SQL Server 2016 and later and for Azure SQL. `check` sends the first two lines, then one SELECT that returns a row for each write role and permission the probe looks for, such as `role, db_owner`, `permission, INSERT` or `permission, UPDATE ON SCHEMA::dbo`; no rows means it holds none of them. `check` ends read-only only when that SELECT's own `kind,name` header comes back: empty output, or output that starts any other way, is exit 1 and no verdict.

## The guard

The guard decides before anything runs, and its refusal (exit 4) says why. Comments, nested `/* */` ones included, are dropped first, and `'strings'`, `[bracketed]` names and `"quoted"` names are taken whole, so nothing inside them counts. It accepts the SQL only when:

1. it is not empty, is at most 64 KB as UTF-8, holds no markup (it does not start with `<`, and has no `</`, `<?`, `<!`, `/>` or `<Word>` tag anywhere, as a `.linq` script or a connection list has), and has no line a client would take for a command of its own: `GO`, with or without a count or a comment after it, or a line that starts with `:` or `!!`;
2. only `DECLARE @…` and `SET @…` statements come before the main one, so `SET ROWCOUNT 0` and `DECLARE c CURSOR` are refused;
3. the main statement begins `SELECT` or `WITH`, a leading `;` allowed, and is the last: after its top-level `;` come only more `;`, and no word that starts a statement (`DECLARE`, `SET`, `OPEN`, `CLOSE`, `DEALLOCATE`, `READTEXT`, `CHECKPOINT`, or `FETCH` other than after `ROW` or `ROWS`) follows it outside parentheses;
4. no word of the deny list stands anywhere, at any depth, in any letter case, and neither does `NEXT VALUE FOR`, which advances a sequence. The one exception is `MERGE` right before `JOIN` or `UNION`, the hint in `INNER MERGE JOIN` and `OPTION (MERGE JOIN)` or `OPTION (MERGE UNION)`, which picks how a read is done and is not the `MERGE` statement.

A refusal's reason never quotes the SQL: it names the deny-list entry (a word as written; `sp_*` or `xp_*` for a name that starts so, or the word it reads as for a spelling that is not plain ASCII), a keyword the shape check knows, or a line number, so that a password, an `.env` line or a token in a file given as the SQL is not repeated on stderr, in the plan or in the prompt.

The deny list, as `read.py` holds it in `DENY_LIST`:

```
INSERT UPDATE DELETE MERGE INTO EXEC EXECUTE CREATE ALTER DROP TRUNCATE GRANT REVOKE DENY BACKUP RESTORE BULK
OPENROWSET OPENQUERY OPENDATASOURCE OPENXML DBCC KILL SHUTDOWN RECONFIGURE WAITFOR RAISERROR THROW PRINT GOTO
RETURN IF WHILE BEGIN COMMIT ROLLBACK SAVE TRAN TRANSACTION USE ENABLE DISABLE SETUSER REVERT
ADD WRITETEXT UPDATETEXT RECEIVE SEND CONVERSATION DIALOG
xp_* sp_*
UPDLOCK XLOCK TABLOCKX HOLDLOCK SERIALIZABLE REPEATABLEREAD TABLOCK
```

`xp_*` and `sp_*` stand for any word that starts so. The last line is the lock hints, which take or hold locks a read does not need.

What the guard cannot be sure SQL Server reads the same way, it refuses rather than guess: a comment, string or name that is not closed; parentheses that do not balance; a `--` comment holding a line break other than LF or CRLF; a control or invisible character outside a string. A word joined to a character no name holds is checked part by part, and in its NFKC form, so a refused word cannot hide in it.

These pass: `CASE … WHEN … END`, `WITH (NOLOCK)`, `OPTION (RECOMPILE)` and the other `OPTION (…)` hints without `USE`, `FOR JSON` and `FOR XML`, CTEs, subqueries, `UNION`, `OFFSET … FETCH`, and a `DECLARE @…` or `SET @…` prelude with or without semicolons.

**Deliberate false refusals.** These are reads, and the guard refuses them:

- a column or table whose name is on the list, unbracketed, such as `save`, `use`, `return`, `if`, `print`, `kill`, `send`, `receive`, `add`, `conversation` or `dialog`. Written `[save]` or `"save"`, it passes, and the refusal says so;
- `OPTION (USE HINT (…))` and `OPTION (USE PLAN …)`, since `USE` is on the list;
- a line that starts with `GO`, such as one that starts `GO.dbo.Orders`, and a `GO` line inside a string or a comment;
- a form feed or a vertical tab outside a string.

**Known gaps.** These pass the guard, and only the login stops them:

- a function with side effects, called from the SELECT;
- a second `SELECT` with no `;` before it, which is a read too, and which `SET ROWCOUNT` also caps;
- four-part names that reach a linked server;

## What it costs and touches

- **It runs real reads, production included,** through your saved connection and as its login. A read can be slow or wait on locks: the timeout, the lock timeout and the row cap bound it, and `--nolock` trades the wait for dirty reads.
- **The login is the boundary.** The guard checks the SQL's shape and the probe checks the login's rights; neither makes a login read-only. With a login that can only read, nothing they miss can write. A run with `--allow-writable-login`, which Claude passes for a connection listed in `SQL_READ_WRITABLE_OK`, gives that up: there only the guard and the prompt stand in the way.
- **The probe sees roles, database-wide permissions and schema grants, not grants on single objects.** It looks for the server role `sysadmin`, the database roles `db_owner`, `db_datawriter` and `db_ddladmin`, the database permissions the batch lists, and `INSERT`, `UPDATE`, `DELETE`, `ALTER`, `CONTROL` and `TAKE OWNERSHIP` on each schema the login can see, asking `fn_my_permissions` for each schema by its name. A login granted `UPDATE` on one table passes it; one granted `UPDATE` on a schema does not, unless the schema's name holds a dot, which the function reads as several parts. `fn_my_permissions` answers NULL, which reads as no permission, for a name it cannot resolve, so only the end-to-end tests show that the schema clause finds anything on a real server (`SQL_READ_E2E_CX`, and `SQL_READ_E2E_SCHEMA_CX` for a login that holds only a schema grant). On Azure SQL `IS_SRVROLEMEMBER` returns 0 or NULL, so there the database roles and permissions carry the check. `check` has the same reach.
- **The password stays LINQPad's.** The connection list's `Password` element is never printed, logged or written, and is not in `--json`, the plan, a message, or the `.linq` file, which names the connection only. LINQPad encrypts a stored password for your Windows user, so any process running as you can use it, another SQL client included. There the login's permissions are the only boundary.
- **Each run writes one `.linq` file**, the header and the batch, to the `--out` folder. The results are written nowhere: they are business data, and go to stdout only.
- **A timeout stops lprun9 and the query it hosts.** lprun9 can run the query in a child process, so on Windows the whole process tree is ended with `taskkill /T /F`; elsewhere its process group is killed. Exit 6 follows. When `taskkill` fails (it cannot run, is refused, or does not answer) or the group cannot be killed, stderr says so, and that the query host may still be running.
- **The hook asks before every `query` and `check`,** with the plan as its reason, built as `read.py plan` builds it, so the preview in chat and the prompt cannot differ. `read.py plan` and `read.py connections` touch no database and never prompt. A command it cannot read asks with `sql-read: the gate could not read this command; approve only if you have read the SQL`. In the `bypassPermissions` and `dontAsk` modes, with nobody to ask, it refuses instead.
- **The hook is a speed bump, not a boundary.** Claude Code calls an `if` pattern best-effort, and runs the hook anyway when it cannot parse the command. A mod that handles `tool.check` can approve past the hook. The ask rules above hold where the hook does not, for the forms they match.
- **Forms that get past both, on purpose.** Someone set on it can run `read.py` without its name in the command: `python3 -c "import read; read.main()" query …` from the scripts folder, a module name spelled in other letter case (`-m READ`), a drive-relative path such as `C:read.py`, or a copy made by a program the gate does not know writes files. The login, not the hook, is the boundary there.
- **The `rules` allow rule outlives the hook.** `read.py rules` prints rules that let `--allow-writable-login` past auto mode's classifier, and they stay in your settings when the plugin is disabled: then nothing asks. Keep the ask rules above beside them.
- **Its cost is one Python start, for a Bash command that holds `d.p` in any letter case, a PowerShell command that names `read.py`, or either shell's `python3 -m read`.** A Bash `if` pattern keeps the case it is written in, so the Bash handlers for `read.py` are the fragment `d.p` in each of its four casings, which catches `READ.PY` and also `build.py`, `word.pdf` and `pd.pivot`. Each handler carries an `if` pattern, so any other command starts nothing; whatever starts the hook and does not run `read.py query` or `check`, such as `read.py plan`, `read.py connections`, `cat read.py` and `build.py`, passes silently. A `--sql`, `--connections` or `--lprun` on a network path (`//host/share/…`) asks without the plan, since the hook does not open a network path before you approve. The hook reads the SQL file and the connection list to build the plan, within a 10-second timeout.
- **A failed hook blocks the command.** If the hook crashes, overruns its timeout, or finds no `python3` on the `PATH`, Claude Code blocks the command (`onFailure: "block"`), so a broken hook never lets a read through unasked. That applies to every command that starts the hook, those above: with no `python3`, a Bash `ls build.py` is blocked as well as a `read.py query`. The hook is started with no shell, so `python3` must be a program on the `PATH`; a Git Bash alias does not count.

## Dependencies

Python's standard library. LINQPad 9 for `lprun9`, which runs the SQL with the credentials LINQPad holds.
