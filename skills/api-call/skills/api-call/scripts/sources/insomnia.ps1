<#
.SYNOPSIS
Sends one GET the way an Insomnia collection would and prints the exchange as JSON: the api-call record.

.DESCRIPTION
Reads Insomnia's local data, read-only:
  - the global environment named -Environment ("Name", or "Name/Sub-environment") for the base URL and variables
  - the collection named -Collection for the auth on its top-level folder (API key or bearer), that folder's own
    environment, which Insomnia lays over the global one, that folder's headers, and the client certificate the
    collection holds for the request's host

Secrets stay in this process. Every value substituted from a variable is a secret, wherever it lands: a header value
or name, the API key or its name, the bearer prefix, a cookie, a query parameter, the base URL. So is every value
substituted into that variable's own value, at any depth, and a password in the base URL's userinfo, templated or
not. The JSON it prints shows each of them as ********: a templated header value whole, a templated part of a name,
prefix or base URL, and a client certificate by file name only. The script refuses to print if any secret would
appear in its output in any form a response could carry it back in: as is, without surrounding whitespace,
percent-encoded, JSON-escaped, or as HTML character references, in any letter case. A secret encoded twice over is
not looked for. Variable names are case-sensitive, as in Insomnia. A redirect is recorded as the response, never
followed, so a key is sent only to the host the collection named.

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

# HTML character references decoded as Python's html.unescape reads them: &#70; and &#x46; with the ; optional, and
# the named ones a server would use for + / = & < > " '. A code point out of range reads as U+FFFD.
function ConvertFrom-HtmlReferences([string]$text) {
    if ($text.IndexOf([char]'&') -lt 0) { return $text }
    return [regex]::Replace($text, '&(?:#([0-9]+);?|#[xX]([0-9A-Fa-f]+);?|(plus|sol|equals|apos);|(amp|lt|gt|quot);?)', {
            param($m)
            $digits = $m.Groups[1].Value + $m.Groups[2].Value
            if ($digits) {
                $n = if ($digits.Length -gt 8) { -1 } elseif ($m.Groups[1].Success) { [long]$digits } else { [Convert]::ToInt64($digits, 16) }
                if ($n -le 0 -or $n -gt 0x10FFFF -or ($n -ge 0xD800 -and $n -le 0xDFFF)) { return [string][char]0xFFFD }
                return [char]::ConvertFromUtf32([int]$n)
            }
            switch -CaseSensitive ($m.Groups[3].Value + $m.Groups[4].Value) {
                'plus' { '+' } 'sol' { '/' } 'equals' { '=' } 'apos' { "'" } 'amp' { '&' } 'lt' { '<' } 'gt' { '>' } default { '"' }
            }
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

# True when any secret shows in any reading of any of the texts, in any letter case: a server echoes a header name
# lower-cased (HTTP/2 always does), and .NET lower-cases a host.
function Test-Exposed($texts, $list) {
    $forms = Get-SecretForms $list
    if ($forms.Count -eq 0) { return $false }
    foreach ($t in $texts) {
        if (-not $t) { continue }
        foreach ($r in (Get-Readings ([string]$t))) {
            foreach ($f in $forms) { if ($r.Contains($f, [StringComparison]::OrdinalIgnoreCase)) { return $true } }
        }
    }
    return $false
}

# An error message with every secret masked, as is, trimmed and percent-encoded; withheld whole if one still shows.
function Hide-Secrets([string]$text, $list) {
    foreach ($s in @($list | Where-Object { $_ } | Sort-Object Length -Descending)) {
        foreach ($f in @($s.Trim(), $s)) {
            if (-not $f) { continue }
            $e = [Uri]::EscapeDataString($f)
            foreach ($form in @($f, $e, $e.Replace('%20', '+'))) { $text = $text.Replace($form, '********', [StringComparison]::OrdinalIgnoreCase) }
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

# A JSON value as hashtables, arrays and plain values. System.Text.Json reads strings as strings: ConvertFrom-Json
# on PowerShell 7.4 turns an ISO date in one into a DateTime, which then renders in another format and time zone.
function ConvertFrom-JsonElement([Text.Json.JsonElement]$e) {
    switch ($e.ValueKind.ToString()) {
        'Object' {
            $h = [Collections.Hashtable]::new([StringComparer]::Ordinal)
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
        if ($d -isnot [Collections.IDictionary] -or -not $d.ContainsKey('_id')) { continue }
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
# A value that extends its own variable, as token = {{ token }}-v2 does, is resolved as its layer is read (see the
# layers below); its resolution is kept here by the variable's name.
$extended = [Collections.Hashtable]::new([StringComparer]::Ordinal)
# What stands in for a masked part while a shown text is built: lower-case letters and digits, which a URL keeps as
# they are wherever they fall, the host included. Format-Shown turns it into ********.
$mark = 'apicallmasked7c1e'

function Get-TemplateName($m) {
    return @($m.Groups[1].Value, $m.Groups[2].Value, $m.Groups[3].Value) | Where-Object { $_ } | Select-Object -First 1
}

function Format-Shown([string]$text) { return $text.Replace($mark, '********') }

# A text with its templates resolved, as a hashtable:
#   Value  the text as sent
#   Shown  the text with each value substituted from a variable replaced by $mark
#   Parts  every value substituted into the text at any depth: each variable's value fully resolved, and the values
#          substituted into that variable's own value, and so on down
#   Own    when the text extends its own variable, named by $self: that variable's values beneath this layer, whose
#          text Shown keeps, and whose parts Parts holds
# $holder names the variable whose value the text is, if it is one. A name inside a variable's value is part of that
# value, a secret, so an error names the holder instead.
function Resolve-Template([string]$text, [string]$holder, [string]$self, [int]$depth) {
    if ($depth -gt 5) { throw "variable '$holder' in '$Environment' nests templates more than five deep, or refers to itself" }
    $parts = [Collections.Generic.List[string]]::new()
    $own = [Collections.Generic.List[string]]::new()
    $value = [Text.StringBuilder]::new(); $shown = [Text.StringBuilder]::new()
    $at = 0
    foreach ($m in [regex]::Matches($text, $templateRx)) {
        $literal = $text.Substring($at, $m.Index - $at); $at = $m.Index + $m.Length
        [void]$value.Append($literal); [void]$shown.Append($literal)
        $name = Get-TemplateName $m
        if (-not $vars.ContainsKey($name)) {
            if ($holder) { throw "variable '$holder' in '$Environment' names a variable that is not there" }
            throw "variable '$name' is not in environment '$Environment'"
        }
        $r = if ($extended.ContainsKey($name)) { $extended[$name] } else {
            Resolve-Template (Format-Value $vars[$name]) $(if ($holder) { $holder } else { $name }) '' ($depth + 1)
        }
        [void]$value.Append($r.Value)
        $parts.AddRange($r.Parts)
        if ($self -and $name -ceq $self) { [void]$shown.Append($r.Shown); $own.Add($r.Value); $own.AddRange($r.Own) }
        else { [void]$shown.Append($mark); $parts.Add($r.Value); $parts.AddRange($r.Own) }
    }
    $rest = $text.Substring($at)
    [void]$value.Append($rest); [void]$shown.Append($rest)
    $v = $value.ToString()
    if ($v -match '\{\{|\{%') { throw "a value in '$Environment' or '$Collection' uses a template this script cannot resolve" }
    return @{ Value = $v; Shown = $shown.ToString(); Parts = $parts; Own = $own }
}

# A secret's resolved value, with it and every part substituted into it registered as secrets.
function Resolve-Secret([string]$text) {
    $r = Resolve-Template $text
    $secrets.Add($r.Value)
    $secrets.AddRange($r.Parts)
    return $r.Value
}

# A text the record shows, such as a header name or the bearer prefix: its own text is shown as typed, and every part
# substituted into it is registered as a secret and masked in Shown.
function Resolve-Shown([string]$text) {
    $r = Resolve-Template $text
    $secrets.AddRange($r.Parts)
    return $r
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

# Variables, as Insomnia layers them: the global environment's base, the chosen sub-environment, then the folder's own
# environment. A value that names its own variable in any form, as baseUrl = {{ baseUrl }}/v2 or {{ _.baseUrl }}/v2
# does, extends the one beneath it.
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
foreach ($layer in $layers) {
    if ($layer -isnot [Collections.IDictionary]) { continue }
    foreach ($k in @($layer.Keys)) {
        $v = $layer[$k]
        $extends = $v -is [string] -and $vars.ContainsKey($k) -and
            @([regex]::Matches($v, $templateRx) | Where-Object { (Get-TemplateName $_) -ceq $k }).Count -gt 0
        if ($extends) {
            $r = Resolve-Template $v $k $k
            $extended[$k] = $r; $vars[$k] = $r.Value
        } else {
            $vars[$k] = $v; $extended.Remove($k)
        }
    }
}

# The base URL's own text is configuration, and the record shows it, with what it extends; a value substituted into
# it is a secret like any other, masked in the record's url.
if (-not $vars.ContainsKey($BaseUrlVar)) { throw "environment '$Environment' has no '$BaseUrlVar' variable" }
$b = if ($extended.ContainsKey($BaseUrlVar)) { $extended[$BaseUrlVar] } else { Resolve-Template (Format-Value $vars[$BaseUrlVar]) }
$secrets.AddRange($b.Parts)
$baseUrl = $b.Value.TrimEnd('/'); $shownBase = $b.Shown.TrimEnd('/')
if (-not $baseUrl.StartsWith('https://')) { throw "'$BaseUrlVar' must be an https:// URL" }
if (-not $Path.StartsWith('/')) { $Path = '/' + $Path }

# Auth and headers from the folder. $shownHeaders holds each header's shown value under its real name, and
# $shownNames the name shown for one with a templated part.
$headers = [ordered]@{ 'Accept' = 'application/json'; 'User-Agent' = 'api-call' }
$shownHeaders = [ordered]@{ 'Accept' = 'application/json'; 'User-Agent' = 'api-call' }
$shownNames = @{}
$authText = 'none'
$queryKey = $null; $queryValue = $null
$cookieKey = $null; $cookieValue = $null
if ($folderDoc -and (Test-Auth $folderDoc)) {
    $a = $folderDoc['authentication']
    switch ($a['type']) {
        'apikey' {
            $key = Resolve-Shown ([string]$a['key']); $k = $key.Value; $kShown = Format-Shown $key.Shown
            $v = Resolve-Secret ([string]$a['value'])
            switch ([string]$a['addTo']) {
                'queryParams' { $queryKey = $k; $queryValue = $v; $authText = "API key in query parameter $kShown" }
                'cookie' { $cookieKey = $k; $cookieValue = $v; $authText = "API key in cookie $kShown" }
                { $_ -in '', 'header' } { $headers[$k] = $v; $shownHeaders[$k] = '********'; $shownNames[$k] = $kShown; $authText = "API key in header $kShown" }
                default { throw "API key on folder '$folderName' is added to '$($a['addTo'])', which is not supported (header, query parameter or cookie)" }
            }
        }
        'bearer' {
            $t = Resolve-Secret ([string]$a['token'])
            $p = 'Bearer'; $pShown = 'Bearer'
            if ($a['prefix']) { $prefix = Resolve-Shown ([string]$a['prefix']); $p = $prefix.Value; $pShown = Format-Shown $prefix.Shown }
            $headers['Authorization'] = "$p $t"; $shownHeaders['Authorization'] = "$pShown ********"; $authText = 'bearer token'
        }
        default { throw "auth type '$($a['type'])' on folder '$folderName' is not supported (API key and bearer only)" }
    }
}
foreach ($h in @($folderDoc ? $folderDoc['headers'] : @())) {
    if ($h -isnot [Collections.IDictionary] -or $h['disabled'] -or -not $h['name']) { continue }
    $n = Resolve-Shown ([string]$h['name']); $name = $n.Value; $shownNames[$name] = Format-Shown $n.Shown
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
    $pair = "$cookieKey=$cookieValue"; $shownPair = "$(Format-Shown $key.Shown)=********"
    if ($headers.Contains('Cookie')) { $pair += '; ' + $headers['Cookie']; $shownPair += '; ' + $shownHeaders['Cookie'] }
    $headers['Cookie'] = $pair; $shownHeaders['Cookie'] = $shownPair
}

# The url the record shows: the base URL's text with each value substituted into it masked, and a password in its
# userinfo masked whatever its source, normalized as .NET sends the URL. .NET sends no userinfo, but a password
# there is a secret all the same. A shown URL .NET cannot read, as with a templated port, is shown as built.
$builder = [UriBuilder]::new($baseUrl + $Path)
$userInfo = $builder.Uri.UserInfo
if ($userInfo.Contains(':')) { $pw = $userInfo.Split(':', 2)[1]; $secrets.Add($pw); $secrets.Add([Uri]::UnescapeDataString($pw)) }
$shownUrl = $shownBase + $Path
try { $s = [Uri]::new($shownUrl) } catch { $s = $null }
if ($s -and $s.IsAbsoluteUri) {
    $shownUrl = $s.GetComponents([UriComponents]::AbsoluteUri -band -bnot [UriComponents]::UserInfo, [UriFormat]::UriEscaped)
    $start = $s.Scheme + '://'
    if ($s.UserInfo -and $shownUrl.StartsWith($start)) {
        $user, $password = $s.UserInfo.Split(':', 2)
        $shownUrl = $start + $user + $(if ($null -ne $password) { ':' + $mark }) + '@' + $shownUrl.Substring($start.Length)
    }
}
$shownUrl = Format-Shown $shownUrl
if ($queryKey) {
    $pair = [Uri]::EscapeDataString($queryKey) + '=' + [Uri]::EscapeDataString($queryValue)
    $shownPair = (Format-Shown ([Uri]::EscapeDataString($key.Shown))) + '=********'
    $existing = $builder.Query.TrimStart('?')
    $builder.Query = if ($existing) { "$existing&$pair" } else { $pair }
    $shownUrl = $shownUrl + ($(if ($existing) { '&' } else { '?' })) + $shownPair
}
# As Insomnia (libcurl) and Postman do: the URL's user and password, percent-decoded, go as basic auth, unless the
# auth already set Authorization. .NET would send neither.
if ($userInfo -and -not @($headers.Keys | Where-Object { $_ -ieq 'Authorization' })) {
    $u, $pwPart = $userInfo.Split(':', 2)
    $plain = [Uri]::UnescapeDataString($u) + ':' + $(if ($null -ne $pwPart) { [Uri]::UnescapeDataString($pwPart) } else { '' })
    $basic = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($plain))
    $secrets.Add($basic)
    $headers['Authorization'] = "Basic $basic"; $shownHeaders['Authorization'] = 'Basic ********'
    $shownUser = if ($s -and $s.UserInfo) { Format-Shown ($s.UserInfo.Split(':', 2)[0]) } else { '********' }
    $via = "basic auth from the base URL as $shownUser"
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
    $label = if ($shownNames.ContainsKey($k)) { $shownNames[$k] } else { $k }
    if ($k -match '[\x00-\x1f\x7f]') { throw "a header name on folder '$folderName' holds a line break or another control character; refusing to send it" }
    if ($v -match '[\x00-\x08\x0a-\x1f\x7f]') { throw "header '$label' on folder '$folderName' holds a line break or another control character in its value; refusing to send it" }
    if ($message.Headers.TryAddWithoutValidation($k, $v)) { continue }
    if ([Net.Http.ByteArrayContent]::new([byte[]]@()).Headers.TryAddWithoutValidation($k, $v)) { $shownHeaders.Remove($k); continue }
    throw "header '$label' on folder '$folderName' is not a valid HTTP header name"
}
# The request headers as the record shows them, each under its shown name; two names that show the same are numbered.
$requestHeaders = [ordered]@{}
foreach ($k in @($shownHeaders.Keys)) {
    $n = if ($shownNames.ContainsKey($k)) { $shownNames[$k] } else { $k }
    $label = $n; $i = 2
    while ($requestHeaders.Contains($label)) { $label = "$n ($i)"; $i++ }
    $requestHeaders[$label] = $shownHeaders[$k]
}

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

# No secret may appear in what is printed or drawn from it: in any string of the record or in the JSON, read every way
# Get-Readings knows. render.py draws the body as returned; the decoded readings go further, for any reader that
# decodes it.
$shown = @($Environment, $Collection, $folderName, $shownUrl, $authText, $certName, $body, $json) +
    @($requestHeaders.Keys) + @($requestHeaders.Values) + @($responseHeaders.Values)
if (Test-Exposed $shown $secrets) { throw 'refusing to print: a secret would appear in the output' }
Write-Out $false $json
