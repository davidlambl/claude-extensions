<#
.SYNOPSIS
Sends one GET the way an Insomnia collection would and prints the exchange as JSON: the api-call record.

.DESCRIPTION
Reads Insomnia's local data, read-only:
  - the global environment named -Environment ("Name", or "Name/Sub-environment") for the base URL and variables
  - the collection named -Collection for the auth on its top-level folder (API key or bearer), that folder's own
    environment, which Insomnia lays over the global one, that folder's headers, and the client certificate the
    collection holds for the request's host

Secrets stay in this process. A secret is a value in a credential position: the API key's value (in a header, a query
parameter or a cookie), the bearer token, a header value the folder templates, a password in the base URL's userinfo
(templated or typed), or its user when it has no password (https://TOKEN@host, as a service such as GitHub takes a
token for the user), and the basic token sent from the userinfo, and a client certificate's passphrase; each together
with every value substituted into it, at any depth. The JSON it prints shows each of them as ********, and a client
certificate by file name only. The rest is configuration, shown as resolved and never a reason to refuse: the base
URL's scheme, host, port and path, header names, the API key's name, the bearer prefix, the base URL's user beside a
password, and the environment, collection and folder names. The script refuses to print if any secret would appear in
its output, in its own letter case, in any form a response could carry it back in: as is, without surrounding
whitespace, percent-encoded, JSON-escaped, or as HTML character references. A secret encoded twice over is not looked
for. An API key or a bearer token that resolves to an empty value stops the call before anything is sent, with a
message that quotes no value; an empty password in the base URL is sent, and its user is then the credential.
Variable names are case-sensitive, as in Insomnia; only the default base URL variable, baseUrl, falls back to the one
variable whose name matches it in another letter case. A value may resolve to at most 65536 characters. A redirect is
recorded as the response, never followed, so a key is sent only to the host the collection named.

.EXAMPLE
pwsh -NoProfile -File insomnia.ps1 -Environment "My API - Test" -Collection "My API" -Path /api/items/42
#>
param(
    [Parameter(Mandatory)][string]$Environment,
    [Parameter(Mandatory)][string]$Collection,
    [Parameter(Mandatory)][string]$Path,
    [string]$Folder,
    [string]$BaseUrlVar = 'baseUrl',
    [string]$InsomniaDir,
    [int]$TimeoutSec = 60
)
$ErrorActionPreference = 'Stop'
$secrets = [Collections.Generic.List[string]]::new()

# Output leaves as UTF-8 whatever the console's code page (on Windows an OEM one such as 437), since call.py reads
# UTF-8. The record is ASCII anyway (ConvertTo-Json escapes the rest); an error message may not be.
function Write-Out([bool]$toError, [string]$text) {
    $redirected = if ($toError) { [Console]::IsErrorRedirected } else { [Console]::IsOutputRedirected }
    if (-not $redirected) {
        if ($toError) { [Console]::Error.WriteLine($text) } else { [Console]::Out.WriteLine($text) }
        return
    }
    $stream = if ($toError) { [Console]::OpenStandardError() } else { [Console]::OpenStandardOutput() }
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($text + "`n")
    $stream.Write($bytes, 0, $bytes.Length)
    $stream.Flush()
}

# JSON string escapes decoded: \uXXXX, \/, \" and the rest, as any JSON reader of the record decodes them.
function ConvertFrom-JsonEscapes([string]$text) {
    if ($text.IndexOf([char]'\') -lt 0) { return $text }
    return [regex]::Replace($text, '\\(?:u([0-9A-Fa-f]{4})|(["\\/bfnrt]))', {
            param($m)
            if ($m.Groups[1].Success) { return [string][char][Convert]::ToInt32($m.Groups[1].Value, 16) }
            switch -CaseSensitive ($m.Groups[2].Value) { 'b' { "`b" } 'f' { "`f" } 'n' { "`n" } 'r' { "`r" } 't' { "`t" } default { $_ } }
        })
}

# Every HTML5 named reference that stands for ASCII, as Python's html.entities.html5 lists them, with what it stands
# for. Names are case-sensitive: &AMP; is &, but &Colon; is not a colon. The eight without a ; are the legacy ones,
# which a reader decodes without it.
$htmlNames = & {
    $t = [Collections.Hashtable]::new([StringComparer]::Ordinal)
    $pairs = @('Tab;', "`t", 'NewLine;', "`n", 'excl;', '!', 'QUOT', '"', 'QUOT;', '"', 'quot', '"', 'quot;', '"',
        'num;', '#', 'dollar;', '$', 'percnt;', '%', 'AMP', '&', 'AMP;', '&', 'amp', '&', 'amp;', '&', 'apos;', "'",
        'lpar;', '(', 'rpar;', ')', 'ast;', '*', 'midast;', '*', 'plus;', '+', 'comma;', ',', 'period;', '.', 'sol;', '/',
        'colon;', ':', 'semi;', ';', 'LT', '<', 'LT;', '<', 'lt', '<', 'lt;', '<', 'equals;', '=', 'GT', '>', 'GT;', '>',
        'gt', '>', 'gt;', '>', 'quest;', '?', 'commat;', '@', 'lbrack;', '[', 'lsqb;', '[', 'bsol;', '\', 'rbrack;', ']',
        'rsqb;', ']', 'Hat;', '^', 'UnderBar;', '_', 'lowbar;', '_', 'DiacriticalGrave;', '`', 'grave;', '`',
        'fjlig;', 'fj', 'lbrace;', '{', 'lcub;', '{', 'VerticalLine;', '|', 'verbar;', '|', 'vert;', '|', 'rbrace;', '}',
        'rcub;', '}')
    for ($i = 0; $i -lt $pairs.Count; $i += 2) { $t[$pairs[$i]] = $pairs[$i + 1] }
    $t
}

# HTML character references decoded as Python's html.unescape reads them: &#70; and &#x46; with the ; optional and
# any number of leading zeros; every named reference that stands for ASCII (see $htmlNames), a legacy one also with
# no ; and at the start of a longer name, as &ampx reads &x; and the rest of the HTML 4 set, such as &eacute;, as
# .NET's WebUtility decodes it. A code point out of range reads as U+FFFD.
function ConvertFrom-HtmlReferences([string]$text) {
    if ($text.IndexOf([char]'&') -lt 0) { return $text }
    return [regex]::Replace($text, '&(?:#([0-9]+);?|#[xX]([0-9A-Fa-f]+);?|([A-Za-z][A-Za-z0-9]{0,31})(;?))', {
            param($m)
            if ($m.Groups[1].Success -or $m.Groups[2].Success) {
                $digits = ($m.Groups[1].Value + $m.Groups[2].Value).TrimStart('0')
                $n = if (-not $digits -or $digits.Length -gt 8) { -1 } elseif ($m.Groups[1].Success) { [long]$digits } else { [Convert]::ToInt64($digits, 16) }
                if ($n -le 0 -or $n -gt 0x10FFFF -or ($n -ge 0xD800 -and $n -le 0xDFFF)) { return [string][char]0xFFFD }
                return [char]::ConvertFromUtf32([int]$n)
            }
            $name = $m.Groups[3].Value; $semi = $m.Groups[4].Value
            if ($semi) {
                if ($htmlNames.ContainsKey("$name;")) { return $htmlNames["$name;"] }
                $decoded = [Net.WebUtility]::HtmlDecode($m.Value)
                if ($decoded -cne $m.Value) { return $decoded }
            }
            foreach ($legacy in 'QUOT', 'quot', 'AMP', 'amp', 'LT', 'lt', 'GT', 'gt') {
                if ($name.StartsWith($legacy, [StringComparison]::Ordinal)) { return $htmlNames[$legacy] + $name.Substring($legacy.Length) + $semi }
            }
            return $m.Value
        })
}

# The ways a text can be read, each one layer of encoding deep: as is and with its JSON escapes decoded, each of those
# also percent-decoded (with a + read as a space or not) or with its HTML character references decoded; and a
# percent-decoded reading with its JSON escapes decoded, as in a Location whose \u escapes .NET re-encoded as %5Cu.
function Get-Readings([string]$text) {
    $readings = [Collections.Generic.List[string]]::new()
    foreach ($t in @($text, (ConvertFrom-JsonEscapes $text))) {
        $u = [Uri]::UnescapeDataString($t)
        foreach ($r in @($t, $u, [Uri]::UnescapeDataString($t.Replace('+', ' ')), (ConvertFrom-JsonEscapes $u), (ConvertFrom-HtmlReferences $t))) {
            if (-not $readings.Contains($r)) { $readings.Add($r) }
        }
    }
    return , $readings
}

# The forms of a secret looked for: as resolved, and without the whitespace HTTP strips from around a header value.
# A value under six characters is too short to tell from ordinary text, and is not checked for.
function Get-SecretForms($list) {
    $forms = [Collections.Generic.List[string]]::new()
    foreach ($s in $list) {
        if (-not $s) { continue }
        foreach ($f in @($s, $s.Trim())) { if ($f.Length -ge 6 -and -not $forms.Contains($f)) { $forms.Add($f) } }
    }
    return , $forms
}

# True when any secret shows in any reading of any of the texts, in its own letter case. A secret is a credential's
# value, which a server echoes as it was sent; the parts a server echoes case-folded (header names, a host) are not
# secrets, so another letter case is another text.
function Test-Exposed($texts, $list) {
    $forms = Get-SecretForms $list
    if ($forms.Count -eq 0) { return $false }
    foreach ($t in $texts) {
        if (-not $t) { continue }
        foreach ($r in (Get-Readings ([string]$t))) {
            foreach ($f in $forms) { if ($r.Contains($f, [StringComparison]::Ordinal)) { return $true } }
        }
    }
    return $false
}

# An error message with every secret masked, as is, trimmed and percent-encoded (in upper- or lower-case hex); withheld
# whole if one still shows.
function Hide-Secrets([string]$text, $list) {
    foreach ($s in @($list | Where-Object { $_ } | Sort-Object Length -Descending)) {
        foreach ($f in @($s.Trim(), $s)) {
            if (-not $f) { continue }
            $e = [Uri]::EscapeDataString($f)
            $lower = [regex]::Replace($e, '%[0-9A-F]{2}', { param($m) $m.Value.ToLowerInvariant() })
            foreach ($form in @($f, $e, $e.Replace('%20', '+'), $lower, $lower.Replace('%20', '+'))) {
                $text = $text.Replace($form, '********', [StringComparison]::Ordinal)
            }
        }
    }
    if (Test-Exposed @($text) $list) { return 'the error message would have shown a secret, so it is withheld' }
    return $text
}

# Any error ends the script with its message alone, plain and with every secret masked, instead of PowerShell's
# formatted error view.
trap {
    $text = [string]$_.Exception.Message
    try { $text = Hide-Secrets $text $secrets } catch { $text = 'the insomnia source failed, and its error message could not be checked for secrets' }
    try { Write-Out $true $text } catch { [Console]::Error.WriteLine($text) }
    exit 1
}

if (-not $InsomniaDir) {
    $InsomniaDir = if ($env:INSOMNIA_DATA) { $env:INSOMNIA_DATA }
    elseif ($IsWindows) { Join-Path $env:APPDATA 'Insomnia' }
    elseif ($IsMacOS) { Join-Path $HOME 'Library/Application Support/Insomnia' }
    else { Join-Path $HOME '.config/Insomnia' }
}

# A JSON value as dictionaries, arrays and plain values. System.Text.Json reads strings as strings: ConvertFrom-Json
# on PowerShell 7.4 turns an ISO date in one into a DateTime, which then renders in another format and time zone. An
# object keeps its keys in the file's order, as Insomnia's Object.keys does, and case-sensitive.
function ConvertFrom-JsonElement([Text.Json.JsonElement]$e) {
    switch ($e.ValueKind.ToString()) {
        'Object' {
            $h = [Collections.Specialized.OrderedDictionary]::new([StringComparer]::Ordinal)
            foreach ($p in $e.EnumerateObject()) { $h[$p.Name] = ConvertFrom-JsonElement $p.Value }
            return $h
        }
        'Array' { return , @(foreach ($i in $e.EnumerateArray()) { , (ConvertFrom-JsonElement $i) }) }
        'String' { return $e.GetString() }
        'Number' { $n = 0L; if ($e.TryGetInt64([ref]$n)) { return $n }; return $e.GetDouble() }
        'True' { return $true }
        'False' { return $false }
        default { return $null }
    }
}

# Insomnia keeps NeDB files: one JSON document per line, the last line for an _id wins, $$deleted marks a removal.
function Read-InsomniaDb([string]$name) {
    $docs = @{}
    $file = Join-Path $InsomniaDir $name
    if (-not (Test-Path $file)) { return $docs }
    foreach ($line in [IO.File]::ReadAllLines($file)) {
        if (-not $line.Trim()) { continue }
        $parsed = [Text.Json.JsonDocument]::Parse($line)
        try { $d = ConvertFrom-JsonElement $parsed.RootElement } finally { $parsed.Dispose() }
        if ($d -isnot [Collections.IDictionary] -or -not $d.Contains('_id')) { continue }
        if ($d['$$deleted']) { $docs.Remove($d['_id']) } else { $docs[$d['_id']] = $d }
    }
    return $docs
}

# A variable's value as Insomnia's template engine renders it: true, not True; nothing for null.
function Format-Value($v) {
    if ($null -eq $v) { return '' }
    if ($v -is [bool]) { return $(if ($v) { 'true' } else { 'false' }) }
    if ($v -is [double]) { return $v.ToString('R', [Globalization.CultureInfo]::InvariantCulture) }
    if ($v -is [Collections.IDictionary]) { return '[object Object]' }
    if ($v -is [Array]) { return (@(foreach ($i in $v) { Format-Value $i }) -join ',') }
    return [string]$v
}

# {{ name }}, {{ _.name }} and {{ _['name'] }} only; Nunjucks tags such as {% response %} are not supported.
$templateRx = '\{\{\s*(?:_\.([A-Za-z0-9_\-]+)|_\[\s*[''"]([^''"]+)[''"]\s*\]|([A-Za-z0-9_\-]+))\s*\}\}'
# Variable names are case-sensitive, as in Insomnia: apiKey and APIKEY are two variables.
$vars = [Collections.Hashtable]::new([StringComparer]::Ordinal)
# A value that extends its own variable, as token = {{ token }}-v2 does, is resolved as its layer is laid (see
# Add-Layer); its resolution is kept here by the variable's name.
$extended = [Collections.Hashtable]::new([StringComparer]::Ordinal)
# Every other variable's resolution, made once and reused, so that a value naming another many times over cannot make
# the work grow with each layer of nesting. It is emptied whenever a variable changes.
$resolved = [Collections.Hashtable]::new([StringComparer]::Ordinal)
# How many characters a resolved value may run to, as in the Postman source.
$longest = 65536

function Get-TemplateName($m) {
    return @($m.Groups[1].Value, $m.Groups[2].Value, $m.Groups[3].Value) | Where-Object { $_ } | Select-Object -First 1
}

function Get-NestingError([string]$holder) { return "variable '$holder' in '$Environment' nests templates more than five deep, or refers to itself" }

function Get-LengthError([string]$holder) {
    if ($holder) { return "variable '$holder' in '$Environment' resolves to more than $longest characters" }
    return "a value in '$Environment' or '$Collection' resolves to more than $longest characters"
}

# A variable's resolution, read at $depth: its extension if it has one, else the one made before, else made now.
function Get-Variable([string]$name, [string]$holder, [int]$depth) {
    $r = if ($extended.ContainsKey($name)) { $extended[$name] } elseif ($resolved.ContainsKey($name)) { $resolved[$name] } else { $null }
    if ($null -eq $r) {
        $r = Resolve-Template (Format-Value $vars[$name]) $holder $depth
        $resolved[$name] = $r
    } elseif ($depth + $r.Height -gt 5) { throw (Get-NestingError $holder) }
    return $r
}

# A text with its templates resolved, as a hashtable:
#   Value   the text as sent
#   Parts   every value substituted into the text at any depth: each variable's value fully resolved, and the values
#           substituted into that variable's own value, and so on down
#   Subs    each value substituted into this text itself: where it starts in Value, and its own resolution (R)
#   Height  how many layers of variables lie beneath the text
# $holder names the variable whose value the text is, if it is one. A name inside a variable's value is part of that
# value, which may be a secret, so an error names the holder instead.
function Resolve-Template([string]$text, [string]$holder, [int]$depth) {
    if ($depth -gt 5) { throw (Get-NestingError $holder) }
    $parts = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $subs = [Collections.Generic.List[object]]::new()
    $value = [Text.StringBuilder]::new()
    $height = 0
    $at = 0
    foreach ($m in [regex]::Matches($text, $templateRx)) {
        [void]$value.Append($text, $at, $m.Index - $at); $at = $m.Index + $m.Length
        $name = Get-TemplateName $m
        if (-not $vars.ContainsKey($name)) {
            if ($holder) { throw "variable '$holder' in '$Environment' names a variable that is not there" }
            throw "variable '$name' is not in environment '$Environment'"
        }
        $r = Get-Variable $name $(if ($holder) { $holder } else { $name }) ($depth + 1)
        if ($r.Height + 1 -gt $height) { $height = $r.Height + 1 }
        if ($r.Value.Length -gt 0) {
            $subs.Add(@{ Start = $value.Length; R = $r })
            [void]$value.Append($r.Value)
            [void]$parts.Add($r.Value); $parts.UnionWith($r.Parts)
        }
        if ($value.Length -gt $longest) { throw (Get-LengthError $holder) }
    }
    [void]$value.Append($text, $at, $text.Length - $at)
    if ($value.Length -gt $longest) { throw (Get-LengthError $holder) }
    $v = $value.ToString()
    if ($v -match '\{\{|\{%') { throw "a value in '$Environment' or '$Collection' uses a template this script cannot resolve" }
    return @{ Value = $v; Parts = $parts; Subs = $subs; Height = $height }
}

# A secret's resolved value, with it and every part substituted into it registered as secrets.
function Resolve-Secret([string]$text) {
    $r = Resolve-Template $text
    $secrets.Add($r.Value)
    $secrets.AddRange($r.Parts)
    return $r.Value
}

# Every value substituted into the characters from $start to $end of a resolution's Value, at any depth, added to
# $into: a value that lies wholly inside with all its parts, and of one that runs across an edge, the piece inside and
# the parts of its own that lie inside it.
function Add-PartsWithin($r, [int]$start, [int]$end, $into) {
    foreach ($s in $r.Subs) {
        $length = $s.R.Value.Length
        if ($s.Start -ge $end) { break }
        $from = [Math]::Max($start, $s.Start); $to = [Math]::Min($end, $s.Start + $length)
        if ($from -ge $to) { continue }
        if ($from -eq $s.Start -and $to -eq $s.Start + $length) { [void]$into.Add($s.R.Value); $into.UnionWith($s.R.Parts); continue }
        [void]$into.Add($r.Value.Substring($from, $to - $from))
        Add-PartsWithin $s.R ($from - $s.Start) ($to - $s.Start) $into
    }
}

# A URL split on its text, as the Postman source splits it, so that whatever a template put in its scheme, host or
# port cannot change where its password is: scheme://, then the authority up to the first / ? or #, in which a
# user:password@ ends at the last @ and the user at its first :. Returns the URL without that userinfo, the user and
# the password ($null for one it does not have), and where each starts in the URL.
function Split-BaseUrl([string]$url) {
    $m = [regex]::Match($url, '^([^/?#]*://)?([^/?#]*)(.*)$', [Text.RegularExpressions.RegexOptions]::Singleline)
    $scheme = $m.Groups[1].Value; $authority = $m.Groups[2].Value
    $out = @{ Url = $url; User = $null; Password = $null; UserAt = -1; PasswordAt = -1 }
    # A char, not a string: a string is searched in the user's culture, and one such as Thai ignores punctuation.
    $at = $authority.LastIndexOf([char]'@')
    if ($at -lt 0) { return $out }
    $info = $authority.Substring(0, $at)
    $out.Url = $scheme + $authority.Substring($at + 1) + $m.Groups[3].Value
    $out.UserAt = $scheme.Length
    $colon = $info.IndexOf([char]':')
    if ($colon -lt 0) { $out.User = $info; return $out }
    $out.User = $info.Substring(0, $colon); $out.Password = $info.Substring($colon + 1); $out.PasswordAt = $scheme.Length + $colon + 1
    return $out
}

# One layer of variables laid over those beneath it, as Insomnia lays them. A value that names its own variable in any
# form, as baseUrl = {{ baseUrl }}/v2 or {{ _.baseUrl }}/v2 does, extends the one beneath. The layer's other values are
# laid first, then each extension is resolved in the file's order, against them and what lies beneath, so the result
# is the same on every run; an extension that names another one of the layer reads it as it was beneath when that one
# comes later in the file.
function Add-Layer($layer) {
    if ($layer -isnot [Collections.IDictionary]) { return }
    $extending = [Collections.Generic.List[string]]::new()
    foreach ($k in @($layer.Keys)) {
        $v = $layer[$k]
        $extends = $v -is [string] -and $vars.ContainsKey($k) -and
            @([regex]::Matches($v, $templateRx) | Where-Object { (Get-TemplateName $_) -ceq $k }).Count -gt 0
        if ($extends) { $extending.Add($k) } else { $vars[$k] = $v; $extended.Remove($k) }
    }
    $resolved.Clear()
    foreach ($k in $extending) {
        $r = Resolve-Template $layer[$k] $k 0
        $extended[$k] = $r; $vars[$k] = $r.Value
        $resolved.Clear()
    }
}

# The collection and the top-level folder whose auth, environment and headers apply.
$workspaces = Read-InsomniaDb 'insomnia.Workspace.db'
$environments = Read-InsomniaDb 'insomnia.Environment.db'
$collectionDoc = @($workspaces.Values | Where-Object { $_['scope'] -eq 'collection' -and $_['name'] -eq $Collection })
if ($collectionDoc.Count -ne 1) { throw "expected one Insomnia collection named '$Collection', found $($collectionDoc.Count)" }
$collectionId = $collectionDoc[0]['_id']
$topFolders = @((Read-InsomniaDb 'insomnia.RequestGroup.db').Values | Where-Object { $_['parentId'] -eq $collectionId })

# Auth is set when it has a type, is not disabled, and is not 'none': Insomnia's No Auth. Inherit is {}.
function Test-Auth($doc) {
    $a = $doc['authentication']
    return [bool]($a -is [Collections.IDictionary] -and $a['type'] -and $a['type'] -ne 'none' -and -not $a['disabled'])
}
$folderDoc = $null
if ($Folder) {
    $match = @($topFolders | Where-Object { $_['name'] -eq $Folder })
    if ($match.Count -ne 1) { throw "expected one top-level folder '$Folder' in '$Collection', found $($match.Count)" }
    $folderDoc = $match[0]
} else {
    $withAuth = @($topFolders | Where-Object { Test-Auth $_ })
    if ($withAuth.Count -gt 1) { throw "'$Collection' has $($withAuth.Count) top-level folders with auth; pass -Folder" }
    if ($withAuth.Count -eq 1) { $folderDoc = $withAuth[0] }
}
$folderName = if ($folderDoc) { [string]$folderDoc['name'] } else { $null }

# The message for a credential that resolves to an empty value: it names the folder and the environment, labels both,
# and never the value or a variable inside it.
function Get-EmptyCredentialError([string]$what) {
    return "the $what on folder '$folderName' resolves to an empty value: is a variable it uses empty in '$Environment', or kept in a sub-environment not chosen?"
}

# Variables, as Insomnia layers them: the global environment's base, the chosen sub-environment, then the folder's own
# environment (see Add-Layer).
$envName, $subName = $Environment.Split('/', 2)
$global = @($workspaces.Values | Where-Object { $_['scope'] -eq 'environment' -and $_['name'] -eq $envName })
if ($global.Count -ne 1) { throw "expected one Insomnia global environment named '$envName', found $($global.Count)" }
$baseEnv = @($environments.Values | Where-Object { $_['parentId'] -eq $global[0]['_id'] })
if ($baseEnv.Count -ne 1) { throw "expected one base environment under '$envName', found $($baseEnv.Count)" }
$layers = @(, $baseEnv[0]['data'])
if ($subName) {
    $sub = @($environments.Values | Where-Object { $_['parentId'] -eq $baseEnv[0]['_id'] -and $_['name'] -eq $subName })
    if ($sub.Count -ne 1) { throw "expected one sub-environment '$subName' under '$envName', found $($sub.Count)" }
    $layers += , $sub[0]['data']
}
if ($folderDoc) { $layers += , $folderDoc['environment'] }
foreach ($layer in $layers) { Add-Layer $layer }

# The base URL is configuration, and the record shows it as resolved: scheme, host, port and path. Only its userinfo
# holds a credential, typed or templated: the password, or the user when there is no password (see $userIsKey), with
# every value substituted into it. Variable names are case-sensitive, but the default name, baseUrl, also finds the
# one variable spelt baseURL or BaseUrl, if there is only one.
if (-not $vars.ContainsKey($BaseUrlVar)) {
    $like = [string[]]@($vars.Keys | Where-Object { $_ -ieq $BaseUrlVar })
    [Array]::Sort($like, [StringComparer]::Ordinal)
    if ($BaseUrlVar -ceq 'baseUrl' -and $like.Count -eq 1) { $BaseUrlVar = $like[0] }
    elseif ($like.Count -gt 0) {
        $names = @($like | ForEach-Object { "'$_'" }) -join ' and '
        throw "environment '$Environment' has no '$BaseUrlVar' variable; variable names are case-sensitive, and it has $names`: name one with --base-url-var"
    } else { throw "environment '$Environment' has no '$BaseUrlVar' variable" }
}
$b = Get-Variable $BaseUrlVar '' 0
$baseUrl = $b.Value.TrimEnd('/')
if (-not $baseUrl.StartsWith('https://', [StringComparison]::Ordinal)) { throw "'$BaseUrlVar' must be an https:// URL" }
if (-not $Path.StartsWith('/', [StringComparison]::Ordinal)) { $Path = '/' + $Path }
$split = Split-BaseUrl $baseUrl
# A user with no password, or an empty one (https://TOKEN@host, or TOKEN:@host), is the credential itself, as the
# Postman source holds: a service such as GitHub takes a token for the user. Beside a password, the user is shown.
$userIsKey = [bool]$split.User -and -not $split.Password
$within = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
if ($split.Password) {
    [void]$within.Add($split.Password)
    Add-PartsWithin $b $split.PasswordAt ($split.PasswordAt + $split.Password.Length) $within
} elseif ($userIsKey) {
    [void]$within.Add($split.User)
    Add-PartsWithin $b $split.UserAt ($split.UserAt + $split.User.Length) $within
}
foreach ($s in $within) { $secrets.Add($s); $secrets.Add([Uri]::UnescapeDataString($s)) }

# Auth and headers from the folder. $shownHeaders holds each header as the record shows it: its name and a value that
# is not a secret as resolved, a secret as ********. An API key or a bearer token that resolves to an empty value was
# never supplied, so the call stops here, before anything is sent, as in the Postman source.
$headers = [ordered]@{ 'Accept' = 'application/json'; 'User-Agent' = 'api-call' }
$shownHeaders = [ordered]@{ 'Accept' = 'application/json'; 'User-Agent' = 'api-call' }
$authText = 'none'
$queryKey = $null; $queryValue = $null
$cookieKey = $null; $cookieValue = $null
if ($folderDoc -and (Test-Auth $folderDoc)) {
    $a = $folderDoc['authentication']
    switch ($a['type']) {
        'apikey' {
            $k = (Resolve-Template ([string]$a['key'])).Value
            $v = Resolve-Secret ([string]$a['value'])
            if (-not $v) { throw (Get-EmptyCredentialError 'API key') }
            switch ([string]$a['addTo']) {
                'queryParams' { $queryKey = $k; $queryValue = $v; $authText = "API key in query parameter $k" }
                'cookie' { $cookieKey = $k; $cookieValue = $v; $authText = "API key in cookie $k" }
                { $_ -in '', 'header' } { $headers[$k] = $v; $shownHeaders[$k] = '********'; $authText = "API key in header $k" }
                default { throw "API key on folder '$folderName' is added to '$($a['addTo'])', which is not supported (header, query parameter or cookie)" }
            }
        }
        'bearer' {
            $t = Resolve-Secret ([string]$a['token'])
            if (-not $t) { throw (Get-EmptyCredentialError 'bearer token') }
            $p = if ($a['prefix']) { (Resolve-Template ([string]$a['prefix'])).Value } else { 'Bearer' }
            $headers['Authorization'] = "$p $t"; $shownHeaders['Authorization'] = "$p ********"; $authText = 'bearer token'
        }
        default { throw "auth type '$($a['type'])' on folder '$folderName' is not supported (API key and bearer only)" }
    }
}
foreach ($h in @($folderDoc ? $folderDoc['headers'] : @())) {
    if ($h -isnot [Collections.IDictionary] -or $h['disabled'] -or -not $h['name']) { continue }
    $name = (Resolve-Template ([string]$h['name'])).Value
    $raw = [string]$h['value']
    if ($raw -match '\{\{') { $headers[$name] = Resolve-Secret $raw; $shownHeaders[$name] = '********' }
    else { $value = (Resolve-Template $raw).Value; $headers[$name] = $value; $shownHeaders[$name] = $value }
}
# An API key added to a cookie goes first in the Cookie header, ahead of any the folder sets. A cookie is one
# name=value pair: a ; or , in the value, or a ; , or = in the name, would send the key as two cookies or more, part
# of it as a name of its own.
if ($null -ne $cookieKey) {
    if ($cookieKey -match '[;,=]' -or $cookieValue -match '[;,]') {
        throw "the API key on folder '$folderName' cannot be sent as one cookie: its name or value holds a ; or a , (or its name an =)"
    }
    $pair = "$cookieKey=$cookieValue"; $shownPair = "$cookieKey=********"
    if ($headers.Contains('Cookie')) { $pair += '; ' + $headers['Cookie']; $shownPair += '; ' + $shownHeaders['Cookie'] }
    $headers['Cookie'] = $pair; $shownHeaders['Cookie'] = $shownPair
}

# The URL goes without its userinfo, which .NET would not send. The url the record shows is the one sent, normalized
# as .NET sends it, with the userinfo put back as the base URL had it and its password masked, and its user too when
# that is the credential: both are found on the text (see Split-BaseUrl), never by parsing it.
$builder = [UriBuilder]::new($split.Url + $Path)
$shownUrl = $builder.Uri.AbsoluteUri
if ($null -ne $split.User) {
    $start = $builder.Uri.Scheme + '://'
    $shownUser = if ($userIsKey) { '********' } else { $split.User }
    $shownUrl = $start + $shownUser + $(if ($null -ne $split.Password) { ':********' }) + '@' + $shownUrl.Substring($start.Length)
}
if ($queryKey) {
    $pair = [Uri]::EscapeDataString($queryKey) + '=' + [Uri]::EscapeDataString($queryValue)
    $existing = $builder.Query.TrimStart('?')
    $builder.Query = if ($existing) { "$existing&$pair" } else { $pair }
    $shownUrl = $shownUrl + ($(if ($existing) { '&' } else { '?' })) + [Uri]::EscapeDataString($queryKey) + '=********'
}
# As Insomnia (libcurl) and Postman do: the URL's user and password, percent-decoded, go as basic auth, unless the
# auth already set Authorization. The user is shown beside a password; the password, a user with none, and the basic
# token are secrets.
if (($split.User -or $null -ne $split.Password) -and -not @($headers.Keys | Where-Object { $_ -ieq 'Authorization' })) {
    $plain = [Uri]::UnescapeDataString($split.User) + ':' + $(if ($null -ne $split.Password) { [Uri]::UnescapeDataString($split.Password) } else { '' })
    $basic = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($plain))
    $secrets.Add($basic)
    $headers['Authorization'] = "Basic $basic"; $shownHeaders['Authorization'] = 'Basic ********'
    $via = if ($userIsKey) { 'basic auth from the base URL as ********' }
    elseif ($split.User) { "basic auth from the base URL as $($split.User)" }
    else { 'basic auth from the base URL, with an empty user' }
    $authText = if ($authText -eq 'none') { $via } else { "$authText, and $via" }
}
$uri = $builder.Uri

# The client certificate the collection holds for this host. As in Insomnia, a host may carry a * wildcard, and a
# port, which is compared as a number with 443 assumed when either side has none.
function Test-HostMatch([string]$pattern, [Uri]$u) {
    $p = ($pattern.Trim() -replace '^[A-Za-z][A-Za-z0-9+.\-]*://', '') -replace '[/?#].*$', ''
    $hostPart = $p; $portPart = ''
    if ($p -match '^(.*):([^:\]]*)$') { $hostPart = $Matches[1]; $portPart = $Matches[2] }
    $hostRx = '^' + ([regex]::Escape($hostPart) -replace '\\\*', '.*') + '$'
    if ($u.Host -notmatch $hostRx) { return $false }
    if ($portPart.Contains('*')) {
        $portRx = '^' + ([regex]::Escape($portPart) -replace '\\\*', '.*') + '$'
        return ($(if ($u.IsDefaultPort) { '' } else { [string]$u.Port })) -match $portRx
    }
    $want = 0
    if (-not [int]::TryParse($portPart, [ref]$want) -or $want -le 0) { $want = 443 }
    return $u.Port -eq $want
}
$certDocs = @((Read-InsomniaDb 'insomnia.ClientCertificate.db').Values | Where-Object {
        $_['parentId'] -eq $collectionId -and -not $_['disabled'] -and $_['host'] -and (Test-HostMatch ([string]$_['host']) $uri) })
if ($certDocs.Count -gt 1) { throw "'$Collection' has $($certDocs.Count) client certificates for $($uri.Host)" }
$cert = $null; $certName = $null
if ($certDocs.Count -eq 1) {
    $c = $certDocs[0]
    $pass = [string]$c['passphrase']
    if ($pass) { $secrets.Add($pass) }
    $pfx = if ($c['pfx']) { [string]$c['pfx'] } elseif ([string]$c['cert'] -match '\.(pfx|p12)$') { [string]$c['cert'] } else { $null }
    if ($pfx) {
        $cert = [Security.Cryptography.X509Certificates.X509Certificate2]::new($pfx, $pass)
        $certName = [IO.Path]::GetFileName($pfx)
    } elseif ($c['cert'] -and $c['key']) {
        $cert = if ($pass) { [Security.Cryptography.X509Certificates.X509Certificate2]::CreateFromEncryptedPemFile([string]$c['cert'], $pass, [string]$c['key']) }
        else { [Security.Cryptography.X509Certificates.X509Certificate2]::CreateFromPemFile([string]$c['cert'], [string]$c['key']) }
        $certName = [IO.Path]::GetFileName([string]$c['cert'])
    }
}

# The headers go on the message only if they are well formed. A line break or other control character in a name or a
# value would let a shared collection write headers of its own, or a second request, so it is refused. A content
# header such as Content-Type has nowhere to go on a GET, which carries no content: .NET does not send it, so neither
# does the record list it.
$message = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Get, $uri)
foreach ($k in @($headers.Keys)) {
    $v = [string]$headers[$k]
    if ($k -match '[\x00-\x1f\x7f]') { throw "a header name on folder '$folderName' holds a line break or another control character; refusing to send it" }
    if ($v -match '[\x00-\x08\x0a-\x1f\x7f]') { throw "header '$k' on folder '$folderName' holds a line break or another control character in its value; refusing to send it" }
    if ($message.Headers.TryAddWithoutValidation($k, $v)) { continue }
    if ([Net.Http.ByteArrayContent]::new([byte[]]@()).Headers.TryAddWithoutValidation($k, $v)) { $shownHeaders.Remove($k); continue }
    throw "header '$k' on folder '$folderName' is not a valid HTTP header name"
}
$requestHeaders = $shownHeaders

# The body as text: a byte order mark wins, as in .NET's own reading, then the declared charset if .NET knows it,
# then UTF-8. An unknown charset, such as the common misspelling utf8, is no reason to lose a response.
function ConvertFrom-Body([byte[]]$bytes, [string]$charset) {
    foreach ($e in @([Text.UTF8Encoding]::new($true), [Text.UTF32Encoding]::new($false, $true), [Text.UnicodeEncoding]::new($false, $true),
            [Text.UnicodeEncoding]::new($true, $true), [Text.UTF32Encoding]::new($true, $true))) {
        $bom = $e.GetPreamble()
        if ($bytes.Length -ge $bom.Length -and [Linq.Enumerable]::SequenceEqual([byte[]]$bytes[0..($bom.Length - 1)], $bom)) {
            return $e.GetString($bytes, $bom.Length, $bytes.Length - $bom.Length)
        }
    }
    $encoding = $null
    if ($charset) { try { $encoding = [Text.Encoding]::GetEncoding($charset.Trim().Trim('"', "'")) } catch { $encoding = $null } }
    if (-not $encoding) { $encoding = [Text.UTF8Encoding]::new($false) }
    return $encoding.GetString($bytes)
}

# The request goes through HttpClient, as Invoke-WebRequest's does, with redirects off: a redirect is recorded as the
# response, never followed, since .NET would resend a key header to whatever host it points at.
$handler = [Net.Http.HttpClientHandler]::new()
$handler.AllowAutoRedirect = $false
if ($cert) { [void]$handler.ClientCertificates.Add($cert) }
$client = [Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds($TimeoutSec)
$sent = [DateTimeOffset]::Now
$watch = [Diagnostics.Stopwatch]::StartNew()
try {
    $response = $client.SendAsync($message).GetAwaiter().GetResult()
    $bytes = $response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
} catch {
    # The .NET exception under PowerShell's wrapper, with its cause; the trap masks any secret the message quotes.
    $e = $_.Exception
    while ($e -is [Management.Automation.MethodInvocationException] -and $e.InnerException) { $e = $e.InnerException }
    $text = [string]$e.Message
    if ($e.InnerException -and $e.InnerException.Message -and -not $text.Contains($e.InnerException.Message)) { $text += ' ' + $e.InnerException.Message }
    throw "the request failed: $text"
} finally {
    $watch.Stop()
    $client.Dispose()
    if ($cert) { $cert.Dispose() }
}
$body = ConvertFrom-Body $bytes ($response.Content.Headers.ContentType ? $response.Content.Headers.ContentType.CharSet : $null)

# .NET keeps Content-* headers on the content and the rest on the response; gather both, then pick the four shown.
$all = @{}
foreach ($pair in $response.Headers) { $all[$pair.Key.ToLowerInvariant()] = ($pair.Value -join ', ') }
foreach ($pair in $response.Content.Headers) { $all[$pair.Key.ToLowerInvariant()] = ($pair.Value -join ', ') }
$responseHeaders = [ordered]@{}
foreach ($h in 'Content-Type', 'Content-Length', 'Date', 'Location') { if ($all.ContainsKey($h.ToLowerInvariant())) { $responseHeaders[$h] = $all[$h.ToLowerInvariant()] } }

# Times in ISO 8601 whatever the user's culture, whose time separator or calendar could otherwise change them.
$invariant = [Globalization.CultureInfo]::InvariantCulture
$json = [ordered]@{
    source          = 'insomnia'
    environment     = $Environment
    collection      = $Collection
    folder          = $folderName
    method          = 'GET'
    path            = $Path
    url             = $shownUrl
    auth            = $authText
    request_headers = $requestHeaders
    client_cert     = $certName
    status          = [int]$response.StatusCode
    elapsed_ms      = [int]$watch.ElapsedMilliseconds
    sent            = $sent.ToString("yyyy'-'MM'-'dd'T'HH':'mm':'ss'.'fffzzz", $invariant)
    sent_utc        = $sent.UtcDateTime.ToString("yyyy'-'MM'-'dd'T'HH':'mm':'ss'.'fff'Z'", $invariant)
    response_headers = $responseHeaders
    body            = $body
} | ConvertTo-Json -Depth 6 -Compress -EscapeHandling EscapeNonAscii

# No secret may appear in what is printed or drawn from it: in any string of the record a request or a response put
# there, and in those strings as the JSON writes them, read every way Get-Readings knows. render.py draws the body as
# returned; the decoded readings go further, for any reader that decodes it. The environment, collection and folder
# names are the record's own labels, chosen by the user, and the field names and times hold nothing from a variable,
# so none of them is a reason to refuse.
$shown = @($shownUrl, $authText, $certName, $body) + @($requestHeaders.Keys) + @($requestHeaders.Values) + @($responseHeaders.Values)
$shown += ConvertTo-Json -InputObject $shown -Compress -EscapeHandling EscapeNonAscii
if (Test-Exposed $shown $secrets) { throw 'refusing to print: a secret would appear in the output' }
Write-Out $false $json
