"""The guard's decisions, run with: python3 -m unittest discover -s skills/sql-read/tests

guard(sql) is a shape check; the login is the boundary. Every change to the deny list or the shape rules carries cases
in both directions here: a write, an EXEC, a lock or a second statement refused, and an ordinary read passed (a CTE, a
DECLARE prelude, WITH (NOLOCK), FOR JSON). A refusal must name what it refused, so the reason is checked as well as the
verdict. Characters outside ASCII are written as escapes."""
import re, sys, unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN / "skills" / "sql-read" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import read  # noqa: E402

# The deny list exactly as the plan names it. The guard may refuse more; it may never refuse less.
PLAN_DENY = """INSERT UPDATE DELETE MERGE INTO EXEC EXECUTE CREATE ALTER DROP TRUNCATE GRANT REVOKE DENY BACKUP RESTORE
BULK OPENROWSET OPENQUERY OPENDATASOURCE OPENXML DBCC KILL SHUTDOWN RECONFIGURE WAITFOR RAISERROR THROW PRINT GOTO
RETURN IF WHILE BEGIN COMMIT ROLLBACK SAVE TRAN TRANSACTION USE ENABLE DISABLE SETUSER REVERT xp_* sp_*
UPDLOCK XLOCK TABLOCKX HOLDLOCK""".split()

PASSES = {
    "a plain SELECT": "SELECT TOP 20 OrderId, CustomerId, Created FROM dbo.Orders ORDER BY Created DESC",
    "a CTE after a leading semicolon": (
        ";WITH recent AS (\n"
        "    SELECT TOP 20 OrderId, Created FROM dbo.Orders ORDER BY Created DESC\n"
        ")\n"
        "SELECT * FROM recent ORDER BY Created"),
    "two CTEs with no leading semicolon": "WITH a AS (SELECT 1 AS n), b AS (SELECT n + 1 AS n FROM a) SELECT * FROM b",
    "a DECLARE and SET @ prelude without semicolons": (
        "DECLARE @since date = '2026-01-01'\n"
        "DECLARE @n int, @status varchar(10)\n"
        "SET @n = 20\n"
        "SET @status = (SELECT TOP 1 Code FROM dbo.Statuses ORDER BY Code)\n"
        "SELECT TOP (@n) * FROM dbo.Orders WHERE Created >= @since AND Status = @status ORDER BY Created DESC"),
    "a DECLARE prelude with semicolons, then a CTE": (
        "DECLARE @id int = 4411;\n"
        ";WITH o AS (SELECT * FROM dbo.Orders WHERE CustomerId = @id) SELECT COUNT(*) AS orders FROM o;"),
    "a table variable declared": "DECLARE @t TABLE (id int PRIMARY KEY)\nSELECT id FROM @t",
    "WITH (NOLOCK)": (
        "SELECT TOP 5 o.OrderId FROM dbo.Orders AS o WITH (NOLOCK)\n"
        "JOIN dbo.Customers c WITH (NOLOCK, INDEX(IX_Customers)) ON c.Id = o.CustomerId"),
    "FOR JSON PATH": "SELECT TOP 5 OrderId, Created FROM dbo.Orders ORDER BY Created DESC FOR JSON PATH, ROOT('orders')",
    "FOR XML PATH": "SELECT Name FROM dbo.Customers FOR XML PATH('customer'), TYPE",
    "UNION, EXCEPT and INTERSECT": (
        "SELECT Id, 'order' AS kind FROM dbo.Orders UNION ALL SELECT Id, 'refund' FROM dbo.Refunds\n"
        "EXCEPT SELECT Id, 'order' FROM dbo.Archive INTERSECT SELECT Id, 'order' FROM dbo.Orders"),
    "subqueries": (
        "SELECT c.Name, (SELECT COUNT(*) FROM dbo.Orders o WHERE o.CustomerId = c.Id) AS orders\n"
        "FROM dbo.Customers c\n"
        "WHERE c.Id IN (SELECT CustomerId FROM dbo.Orders WHERE Total > 100)\n"
        "  AND EXISTS (SELECT 1 FROM dbo.Addresses a WHERE a.CustomerId = c.Id)"),
    "refused words inside comments, strings and identifiers": (
        "-- UPDATE this later; DELETE FROM nothing; EXEC sp_who\n"
        "/* DROP TABLE dbo.Orders; /* a nested comment: TRUNCATE TABLE x; */ still a comment: GRANT ALL */\n"
        "SELECT 'INSERT INTO t; DELETE' AS note, N'it''s; EXEC xp_cmdshell ''dir''' AS quoted,\n"
        "       [Update], [Delete], [Insert Into], \"Exec\", [sp_who], [a]]b], \"say \"\"GO\"\"\"\n"
        "FROM dbo.[Transaction] WHERE [Into] = 'WAITFOR DELAY ''0:0:5''' -- SHUTDOWN"),
    "bracketed names that are refused bare": "SELECT [save], [use], [return], [if], [print], [kill] FROM dbo.Settings",
    "Unicode text and names": (
        "SELECT N'caf\u00e9 \u2713 \u65e5\u672c' AS name, [Gr\u00f6\u00dfe], \u00c4rtikel,\n"
        "       N'\U0001f468\u200d\U0001f4bb' AS coder\n"
        "FROM dbo.Lager WHERE Bezeichnung LIKE N'%M\u00fcller%'"),
    "names that only contain refused words": (
        "SELECT Updated, Deleted, Inserted, IsDisabled, UserName, Transactions, Executed, Printed, Saved, Ifs,\n"
        "       Sender, Received, Additions, Into_Date, Kills, Returned FROM dbo.Audit"),
    "@ variables and # names that hold refused words": "DECLARE @update int = 1\nSELECT @update AS u FROM #delete_me",
    "a variable set by a SELECT": "DECLARE @n int\nSELECT @n = COUNT(*) FROM dbo.Orders",
    "OFFSET and FETCH": "SELECT OrderId FROM dbo.Orders ORDER BY OrderId OFFSET 20 ROWS FETCH NEXT 10 ROWS ONLY",
    "OFFSET and FETCH FIRST ROW": "SELECT OrderId FROM dbo.Orders ORDER BY OrderId OFFSET 1 ROW FETCH FIRST 1 ROW ONLY",
    "TOP WITH TIES, CASE, IIF and OPTION": (
        "SELECT TOP (10) WITH TIES Name, CASE WHEN Total > 100 THEN 'big' ELSE 'small' END AS size,\n"
        "       IIF(Total > 0, 1, 0) AS paid FROM dbo.Orders ORDER BY Total DESC OPTION (RECOMPILE, MAXDOP 1)"),
    "OPENJSON with a schema": (
        "DECLARE @j nvarchar(max) = N'[{\"a\":1}]'\nSELECT * FROM OPENJSON(@j) WITH (a int '$.a') AS j"),
    "a trailing semicolon, more semicolons and a comment": "SELECT 1 AS one; -- done\n;\n",
    "CRLF line endings and tabs": "SELECT\tOrderId\r\nFROM dbo.Orders\r\nWHERE Created > '2026-01-01';\r\n",
    "money, binary and float literals": "SELECT $12.50 AS price, 0x1F AS flag, 1.5e3 AS big, .5 AS half, 2. AS two",
    "window functions and a temporal table": (
        "SELECT Id, ROW_NUMBER() OVER (PARTITION BY CustomerId ORDER BY Created) AS rn\n"
        "FROM dbo.Orders FOR SYSTEM_TIME AS OF '2026-01-01'"),
    "GO as a name in the middle of a line": "SELECT 1 AS GO, 'GO' AS b, [GO] AS c",
    "a comment line that begins with the word Go": "/*\nGo back seven days\n*/\nSELECT 1 AS n",
    "GO inside a comment that does not start a line": "SELECT 1 AS n /* GO */",
    "a SELECT with no FROM": "SELECT GETDATE() AS now, @@VERSION AS version, DB_NAME() AS db",
    "the check query read.py sends": read.CHECK_SQL,
    "a semicolon right before the main SELECT": "DECLARE @a int = 1;SELECT @a AS a",
    "a dotted name and a four-part name": "SELECT TOP 1 * FROM LinkedServer.Sales.dbo.Orders",
    "lower case": "select top 5 * from dbo.orders with (nolock) where 1 = 1 order by 1",
    "a lower-case prelude": "declare @n int = 5\nset @n = 6\nselect @n as n",
    "a mixed-case CTE": "With a As (Select 1 As n) Select n From a",
    "a bracketed name holding ]] before a refused word": "SELECT [odd]]DELETE] AS x FROM dbo.Things",
    # MERGE right before JOIN or UNION is a hint that picks how a read is done, not the MERGE statement.
    "MERGE join hints": ("SELECT o.OrderId, l.Sku FROM dbo.Orders o\n"
                         "INNER MERGE JOIN dbo.Lines l ON l.OrderId = o.OrderId\n"
                         "LEFT OUTER MERGE JOIN dbo.Notes n ON n.OrderId = o.OrderId"),
    "OPTION (MERGE JOIN)": "SELECT * FROM dbo.Orders o JOIN dbo.Lines l ON l.OrderId = o.OrderId OPTION (MERGE JOIN)",
    "OPTION (MERGE UNION), lower case, a comment between": (
        "select 1 as n union select 2 as n option (merge /* x */ union)"),
}

REFUSALS = {
    # The plan's table: each refused, with what it refused named in the reason.
    "UPDATE": ("UPDATE dbo.Orders SET Status = 'shipped' WHERE OrderId = 1", "UPDATE"),
    "DELETE": ("DELETE FROM dbo.Orders WHERE OrderId = 1", "DELETE"),
    "SELECT ... INTO": ("SELECT * INTO dbo.OrdersCopy FROM dbo.Orders", "INTO"),
    "EXEC": ("EXEC dbo.usp_ShipOrder 1", "EXEC"),
    "sp_executesql": ("EXECUTE sp_executesql N'SELECT 1'", "sp_executesql"),
    "xp_cmdshell": ("SELECT 1 AS x\nEXEC master..xp_cmdshell 'dir'", "xp_cmdshell"),
    "two statements": ("SELECT * FROM dbo.Orders; SELECT * FROM dbo.Customers", "one statement"),
    "a SET ROWCOUNT 0 prelude": ("SET ROWCOUNT 0\nSELECT * FROM dbo.Orders", "SET ROWCOUNT"),
    "BEGIN TRAN": ("BEGIN TRAN\nSELECT * FROM dbo.Orders", "BEGIN"),
    "WAITFOR": ("WAITFOR DELAY '00:01:00'\nSELECT 1 AS x", "WAITFOR"),
    "UPDLOCK": ("SELECT * FROM dbo.Orders WITH (UPDLOCK, ROWLOCK) WHERE OrderId = 1", "UPDLOCK"),
    "GO": ("SELECT 1 AS x\nGO\nSELECT 2 AS y", "GO"),
    "empty": ("", "empty"),
    "IF ... SELECT": ("IF EXISTS (SELECT 1 FROM dbo.Orders) SELECT 1 AS x", "IF"),
    # A deliberate false refusal: an unbracketed column named save. [save] passes (PASSES above).
    "an unbracketed save": ("SELECT save FROM dbo.Settings", "save"),
    # More writes, in every position a write can take.
    "INSERT": ("INSERT INTO dbo.Log (Note) VALUES ('x')", "INSERT"),
    "a CTE that deletes": ("WITH old AS (SELECT * FROM dbo.Orders WHERE Created < '2020-01-01') DELETE FROM old",
                           "DELETE"),
    "an UPDATE after a SELECT with no semicolon": ("SELECT 1 AS x\nUPDATE dbo.Orders SET Status = 'x'", "UPDATE"),
    "MERGE": ("MERGE dbo.Orders AS t USING dbo.Staging AS s ON t.Id = s.Id WHEN MATCHED THEN DELETE;", "MERGE"),
    "MERGE into a table named [Join]": ("MERGE [Join] USING dbo.Staging AS s ON 1 = 1 WHEN MATCHED THEN DELETE;",
                                        "MERGE"),
    "MERGE as the last word": ("SELECT 1 AS x MERGE", "MERGE"),
    "a write after a MERGE JOIN hint": ("SELECT * FROM dbo.A a INNER MERGE JOIN dbo.B b ON a.Id = b.Id\n"
                                        "DELETE FROM dbo.A", "DELETE"),
    # Markup: a .linq script or LINQPad's connection list given as the SQL.
    "a .linq script": ('<Query Kind="SQL" />\n\nSELECT 1 AS x', "<"),
    "SELECT ... INTO a temp table": ("SELECT * INTO #copy FROM dbo.Orders", "INTO"),
    "OUTPUT INTO": ("DELETE FROM dbo.Orders OUTPUT deleted.* INTO dbo.Bin WHERE Id = 1", "DELETE"),
    "WRITETEXT after a SELECT": ("DECLARE @p varbinary(16)\n"
                                 "SELECT @p = TEXTPTR(Notes) FROM dbo.Orders WHERE OrderId = 1\n"
                                 "WRITETEXT dbo.Orders.Notes @p 'overwritten'", "WRITETEXT"),
    "UPDATETEXT after a SELECT": ("DECLARE @p varbinary(16)\n"
                                  "SELECT @p = TEXTPTR(Notes) FROM dbo.Orders WHERE OrderId = 1\n"
                                  "UPDATETEXT dbo.Orders.Notes @p 0 NULL 'x'", "UPDATETEXT"),
    "RECEIVE from a queue": ("SELECT 1 AS x\nRECEIVE TOP (1) * FROM dbo.TargetQueue", "RECEIVE"),
    "SEND on a conversation": ("DECLARE @h uniqueidentifier = NEWID()\nSELECT @h AS h\n"
                               "SEND ON CONVERSATION @h (N'x')", "SEND"),
    "ADD SENSITIVITY CLASSIFICATION": ("SELECT 1 AS x\n"
                                       "ADD SENSITIVITY CLASSIFICATION TO dbo.Customers.Email WITH (LABEL = 'x')",
                                       "ADD"),
    "NEXT VALUE FOR, which advances a sequence": ("SELECT NEXT VALUE FOR dbo.OrderNumbers AS n", "NEXT VALUE FOR"),
    "NEXT VALUE FOR with a comment inside": ("SELECT NEXT /* x */ VALUE\nFOR dbo.OrderNumbers AS n", "NEXT VALUE FOR"),
    # Locks.
    "HOLDLOCK": ("SELECT * FROM dbo.Orders WITH (HOLDLOCK)", "HOLDLOCK"),
    "SERIALIZABLE, which is HOLDLOCK": ("SELECT * FROM dbo.Orders WITH (SERIALIZABLE)", "SERIALIZABLE"),
    "REPEATABLEREAD": ("SELECT * FROM dbo.Orders WITH (REPEATABLEREAD)", "REPEATABLEREAD"),
    "TABLOCK": ("SELECT COUNT(*) AS n FROM dbo.Orders WITH (TABLOCK)", "TABLOCK"),
    "TABLOCKX": ("SELECT COUNT(*) AS n FROM dbo.Orders WITH (TABLOCKX)", "TABLOCKX"),
    "XLOCK in lower case": ("select * from dbo.Orders with (xlock)", "xlock"),
    "SET TRANSACTION ISOLATION LEVEL": ("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE\nSELECT 1 AS x", "TRANSACTION"),
    # The shape: what may come before the SELECT, and that nothing comes after it.
    "SET NOCOUNT OFF before the SELECT": ("SET NOCOUNT OFF;\nSELECT 1 AS x", "SET NOCOUNT"),
    "SET LOCK_TIMEOUT before the SELECT, no semicolons": ("DECLARE @a int\nSET LOCK_TIMEOUT 0\nSELECT 1 AS x",
                                                          "SET LOCK_TIMEOUT"),
    "a cursor declared": ("DECLARE c CURSOR FOR SELECT * FROM dbo.Orders", "DECLARE c"),
    "SET ROWCOUNT 0 after the SELECT, with no semicolon": ("SELECT TOP 1 * FROM dbo.Orders\nSET ROWCOUNT 0\n"
                                                           "SELECT * FROM dbo.Orders", "SET starts another"),
    "a DECLARE after the SELECT": ("SELECT 1 AS x\nDECLARE @y int", "DECLARE starts another"),
    "a cursor opened after the SELECT": ("DECLARE @c CURSOR\nSET @c = CURSOR FOR SELECT OrderId FROM dbo.Orders\n"
                                         "OPEN @c\nFETCH NEXT FROM @c", "OPEN starts another"),
    "a FETCH after the SELECT": ("SELECT 1 AS x\nFETCH NEXT FROM c", "FETCH starts another"),
    "CHECKPOINT after the SELECT": ("SELECT 1 AS x\nCHECKPOINT", "CHECKPOINT starts another"),
    "READTEXT after the SELECT": ("SELECT 1 AS x\nREADTEXT dbo.Orders.Notes @p 0 10", "READTEXT starts another"),
    "CLOSE after the SELECT": ("SELECT 1 AS x\nCLOSE c", "CLOSE starts another"),
    "DEALLOCATE after the SELECT": ("SELECT 1 AS x\nDEALLOCATE c", "DEALLOCATE starts another"),
    "a lower-case set after the select": ("select 1 as x\nset rowcount 0\nselect 2 as y", "set starts another"),
    "OPEN before the SELECT": ("OPEN c\nSELECT 1 AS x", "not OPEN c"),
    "SET @@ before the SELECT": ("SET @@ROWCOUNT = 0\nSELECT 1 AS x", "not SET @@ROWCOUNT"),
    "a fullwidth SET after the SELECT": ("SELECT 1 AS x\n\uff33\uff25\uff34 ROWCOUNT 0\nSELECT 2 AS y",
                                         "starts another"),
    "a statement after the final semicolon": ("SELECT 1 AS x; DECLARE @x int", "one statement"),
    "a bare procedure after the semicolon, named as written": ("SELECT 1 AS x;\n  dbo.usp_Purge 7",
                                                               "and dbo.usp_Purge follows"),
    "a DECLARE alone": ("DECLARE @x int = 1", "SELECT or WITH"),
    "a query in parentheses": ("(SELECT 1 AS x)", "SELECT or WITH"),
    "USE": ("USE master; SELECT 1 AS x", "USE"),
    # Batch separators and client commands, wherever they sit, comments and strings included.
    "GO after a comment on its line": ("SELECT 1 AS x\n/* x */ GO\nSELECT 2 AS y", "GO"),
    "GO on its own line inside a block comment": ("SELECT 1 AS x /*\nGO\nDELETE FROM dbo.Orders\n--*/", "GO"),
    "GO with a count, in lower case": ("SELECT 1 AS x\n  go 5\n", "GO"),
    "GO with a comment after it": ("SELECT 1 AS x\nGO -- next batch\nSELECT 2 AS y", "GO"),
    "GO on its own line inside a string": ("SELECT 'a\nGO\nb' AS x", "GO"),
    "a sqlcmd :r line": ("SELECT 1 AS x\n:r C:/elsewhere.sql", "client command"),
    "a sqlcmd !! line": ("SELECT 1 AS x\n  !!del /q C:/x", "client command"),
    # What the tokenizer cannot agree on with SQL Server is refused, not guessed.
    "an unclosed comment": ("SELECT 1 AS x /* never closed", "/*"),
    "an unclosed nested comment": ("SELECT 1 AS x /* /* */ DELETE FROM dbo.Orders", "/*"),
    "an unclosed string": ("SELECT 'never closed AS x", "string"),
    "an unclosed bracketed name": ("SELECT [never closed FROM dbo.Orders", "["),
    "an unclosed quoted name": ("SELECT \"never closed FROM dbo.Orders", '"'),
    "an extra opening parenthesis": ("SELECT (1 AS x FROM dbo.Orders", "parentheses"),
    "an extra closing parenthesis": ("SELECT 1) AS x FROM dbo.Orders", "parentheses"),
    "a parenthesis closed before one opens, hiding a semicolon": ("SELECT 1 AS x) ; SELECT 2 AS y (", "parentheses"),
    "a lone CR inside a -- comment": ("SELECT 1 AS x -- note\rDELETE FROM dbo.Orders", "-- comment"),
    "a line separator inside a -- comment": ("SELECT 1 AS x -- note\u2028'\nDELETE FROM dbo.Orders --'",
                                             "-- comment"),
    "a zero-width space outside a string": ("SELECT 1 AS x\u200b", "U+200B"),
    "a right-to-left override outside a string": ("SELECT 1 AS x /* */ \u202e", "U+202E"),
    "a NUL outside a string": ("SELECT 1 AS x\x00", "U+0000"),
    "a byte order mark inside the text": ("SELECT 1 AS x \ufeff", "U+FEFF"),
    "UPDATE in fullwidth letters": ("SELECT 1 AS x\n\uff35\uff30\uff24\uff21\uff34\uff25 dbo.Orders "
                                    "SET Status = 'x'", "UPDATE"),
    "UPDATE glued to a number": ("SELECT 1UPDATE dbo.Orders SET Status = 'x'", "UPDATE"),
    "UPDATE glued to an exponent": ("SELECT 1eUPDATE dbo.Orders SET Status = 'x'", "UPDATE"),
    "UPDATE glued to a decimal point": ("SELECT 1.UPDATE dbo.Orders SET Status = 'x'", "UPDATE"),
    "UPDATE glued to a binary literal": ("SELECT 0x1UPDATE dbo.Orders SET Status = 'x'", "UPDATE"),
    "UPDATE glued to a superscript two": ("SELECT \u00b2UPDATE dbo.Orders SET Status = 'x'", "UPDATE"),
    "UPDATE glued after a string": ("SELECT 'a'UPDATE dbo.Orders SET Status = 'x'", "UPDATE"),
    "UPDATE glued after a bracketed name": ("SELECT [a]UPDATE dbo.Orders SET Status = 'x'", "UPDATE"),
    "INSERT with a dotless i": ("SELECT 1 AS x\n\u0131nsert dbo.Log VALUES (1)", "INSERT"),
}


class Guard(unittest.TestCase):
    def test_ordinary_reads_pass(self):
        for name, sql in PASSES.items():
            with self.subTest(name):
                ok, why = read.guard(sql)
                self.assertTrue(ok, why)
                self.assertTrue(why)

    def test_writes_locks_and_second_statements_are_refused_with_the_reason_named(self):
        for name, (sql, named) in REFUSALS.items():
            with self.subTest(name):
                ok, why = read.guard(sql)
                self.assertFalse(ok, f"passed: {sql!r}")
                self.assertIn(named.lower(), why.lower())

    def test_an_unbracketed_name_that_is_refused_says_how_to_write_it(self):
        ok, why = read.guard("SELECT save FROM dbo.Settings")
        self.assertFalse(ok)
        self.assertIn("[save]", why)
        self.assertEqual(read.guard("SELECT [save] FROM dbo.Settings")[0], True)

    def test_every_word_on_the_deny_list_is_refused_bare_and_passes_in_brackets(self):
        for entry in read.DENY_LIST.split():
            word = entry.replace("*", "anything")
            for spelling in (word.upper(), word.lower(), word.capitalize()):
                with self.subTest(spelling):
                    ok, why = read.guard(f"SELECT {spelling} FROM dbo.Things")
                    self.assertFalse(ok, f"{spelling} passed")
                    self.assertIn(spelling.lower(), why.lower())
                    ok, why = read.guard(f"SELECT [{spelling}] FROM dbo.Things")
                    self.assertTrue(ok, why)

    def test_merge_as_a_hint_passes_as_hash_and_loop_do(self):
        joins, unions = ("MERGE", "merge", "HASH", "LOOP"), ("MERGE", "Merge", "HASH", "CONCAT")
        for sql, hints in (("SELECT * FROM dbo.A a INNER {} JOIN dbo.B b ON a.Id = b.Id", joins),
                           ("SELECT * FROM dbo.A a FULL OUTER {} JOIN dbo.B b ON a.Id = b.Id", joins),
                           ("SELECT * FROM dbo.A a JOIN dbo.B b ON a.Id = b.Id OPTION ({} JOIN)", joins),
                           ("SELECT 1 AS n UNION SELECT 2 OPTION ({} UNION)", unions)):
            for hint in hints:
                with self.subTest(sql.format(hint)):
                    self.assertEqual(read.guard(sql.format(hint)), (True, "one SELECT or WITH statement"))

    def test_markup_is_refused_without_quoting_any_of_it(self):
        # A .linq script with a connection, or LINQPad's connection list, holds a password: the reason must not
        # quote a word of it, even one the deny list would name, as sp_ here.
        linq = ('<Query Kind="SQL">\n  <Connection>\n    <Server>sql-prod-01</Server>\n'
                "    <Password>sp_FAKE-pw-7f3a9c2e</Password>\n  </Connection>\n</Query>\n\nSELECT 1 AS x")
        listing = (PLUGIN / "tests" / "fixtures" / "connections.xml").read_text(encoding="utf-8")
        for name, text in (("a .linq with a connection", linq), ("the connection list", listing),
                           ("after blank lines", "\r\n\n  " + linq)):
            with self.subTest(name):
                ok, why = read.guard(text)
                self.assertFalse(ok)
                self.assertTrue(why.startswith("the SQL starts with <"), why)
                self.assertNotIn("FAKE", why)
                self.assertNotIn("Password", why)

    def test_the_deny_list_holds_every_word_the_plan_names(self):
        self.assertEqual([w for w in PLAN_DENY if w not in read.DENY_LIST.split()], [])
        self.assertEqual(len(read.DENY_LIST.split()), len(set(read.DENY_LIST.split())), "a word is listed twice")

    def test_whitespace_or_comments_alone_are_empty(self):
        for sql in ("", "   \r\n\t", "-- only a comment", "/* only */ -- comments\n"):
            with self.subTest(repr(sql)):
                ok, why = read.guard(sql)
                self.assertFalse(ok)
                self.assertIn("empty", why)

    def test_64_kb_passes_and_one_byte_more_is_refused(self):
        head = "SELECT 1 AS x -- "
        at_limit = head + "a" * (read.MAX_SQL_BYTES - len(head))
        self.assertEqual(len(at_limit.encode("utf-8")), 64 * 1024)
        self.assertTrue(read.guard(at_limit)[0])
        ok, why = read.guard(at_limit + "a")
        self.assertFalse(ok)
        self.assertIn("64 KB", why)
        # Measured in UTF-8 bytes, not characters: 32,768 two-byte characters fill it.
        wide = "SELECT N'" + "\u00e9" * 32768 + "' AS x"
        self.assertFalse(read.guard(wide)[0])

    def test_a_refusal_names_each_refused_word_once(self):
        ok, why = read.guard("EXEC sp_executesql N'x'\nEXEC sp_executesql N'y'")
        self.assertFalse(ok)
        self.assertEqual(len(re.findall(r"\bsp_executesql\b", why)), 1, why)
        self.assertEqual(len(re.findall(r"\bEXEC\b", why)), 1, why)

    def test_text_that_is_not_a_string_is_refused(self):
        for value in (None, b"SELECT 1", 42):
            with self.subTest(value):
                self.assertFalse(read.guard(value)[0])

    def test_the_readme_mirrors_the_deny_list(self):
        readme = PLUGIN / "README.md"
        if not readme.exists():
            self.skipTest("the plugin's README.md is not written yet")
        text = readme.read_text(encoding="utf-8")
        missing = [w for w in read.DENY_LIST.split() if not re.search(rf"(?<![\w*]){re.escape(w)}(?![\w*])", text)]
        self.assertEqual(missing, [], "README.md must list the deny list verbatim, and these are missing from it")


if __name__ == "__main__":
    unittest.main()
