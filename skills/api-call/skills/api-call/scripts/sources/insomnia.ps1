<#
.SYNOPSIS
Sends one GET the way an Insomnia collection would and prints the exchange as JSON: the api-call record.

.DESCRIPTION
Reads Insomnia's local data, read-only:
  - the global environment named -Environment ("Name", or "Name/Sub-environment") for the base URL and variables
  - the collection named -Collection for the auth on its top-level folder (API key or bearer), that folder's
    headers, and the client certificate the collection holds for the request's host

Secrets stay in this process. The JSON it prints shows auth and templated header values as ********, a client
certificate by file name only, and an API key sent as a query parameter as ********. The script refuses to print
if any resolved secret would appear in its output. A redirect is recorded as the response, never followed, so a key
is sent only to the host the collection named.

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
# Any error ends the script with its message alone, plain and with every secret masked, instead of PowerShell's
# formatted error view.
trap {
    $text = [string]$_.Exception.Message
    if ($secrets) { foreach ($s in $secrets) { if ($s) { $text = $text.Replace($s, '********') } } }
    [Console]::Error.WriteLine($text)
    exit 1
}

if (-not $InsomniaDir) {
    $InsomniaDir = if ($env:INSOMNIA_DATA) { $env:INSOMNIA_DATA }
    elseif ($IsWindows) { Join-Path $env:APPDATA 'Insomnia' }
    elseif ($IsMacOS) { Join-Path $HOME 'Library/Application Support/Insomnia' }
    else { Join-Path $HOME '.config/Insomnia' }
}

# Insomnia keeps NeDB files: one JSON document per line, the last line for an _id wins, $$deleted marks a removal.
function Read-InsomniaDb([string]$name) {
    $docs = @{}
    $file = Join-Path $InsomniaDir $name
    if (-not (Test-Path $file)) { return $docs }
    foreach ($line in [IO.File]::ReadAllLines($file)) {
        if (-not $line.Trim()) { continue }
        $d = $line | ConvertFrom-Json -AsHashtable
        if (-not $d.ContainsKey('_id')) { continue }
        if ($d['$$deleted']) { $docs.Remove($d['_id']) } else { $docs[$d['_id']] = $d }
    }
    return $docs
}

$workspaces = Read-InsomniaDb 'insomnia.Workspace.db'
$environments = Read-InsomniaDb 'insomnia.Environment.db'

# Variables: the global environment's base, then the chosen sub-environment on top.
$envName, $subName = $Environment.Split('/', 2)
$global = @($workspaces.Values | Where-Object { $_['scope'] -eq 'environment' -and $_['name'] -eq $envName })
if ($global.Count -ne 1) { throw "expected one Insomnia global environment named '$envName', found $($global.Count)" }
$baseEnv = @($environments.Values | Where-Object { $_['parentId'] -eq $global[0]['_id'] })
if ($baseEnv.Count -ne 1) { throw "expected one base environment under '$envName', found $($baseEnv.Count)" }
$vars = @{}
$layers = @($baseEnv[0]['data'])
if ($subName) {
    $sub = @($environments.Values | Where-Object { $_['parentId'] -eq $baseEnv[0]['_id'] -and $_['name'] -eq $subName })
    if ($sub.Count -ne 1) { throw "expected one sub-environment '$subName' under '$envName', found $($sub.Count)" }
    $layers += $sub[0]['data']
}
foreach ($layer in $layers) { if ($layer) { foreach ($k in @($layer.Keys)) { $vars[$k] = $layer[$k] } } }

# {{ name }}, {{ _.name }} and {{ _['name'] }} only; Nunjucks tags such as {% response %} are not supported.
$templateRx = '\{\{\s*(?:_\.([A-Za-z0-9_\-]+)|_\[\s*[''"]([^''"]+)[''"]\s*\]|([A-Za-z0-9_\-]+))\s*\}\}'
function Resolve-Template([string]$text) {
    if ($null -eq $text) { return '' }
    $value = $text
    for ($i = 0; $i -lt 5 -and $value -match '\{\{'; $i++) {
        foreach ($m in [regex]::Matches($value, $templateRx)) {
            $name = @($m.Groups[1].Value, $m.Groups[2].Value, $m.Groups[3].Value) | Where-Object { $_ } | Select-Object -First 1
            if (-not $vars.ContainsKey($name)) { throw "variable '$name' is not in environment '$Environment'" }
            $value = $value.Replace($m.Value, [string]$vars[$name])
        }
    }
    if ($value -match '\{\{|\{%') { throw "a value in '$Environment' or '$Collection' uses a template this script cannot resolve" }
    return $value
}

if (-not $vars.ContainsKey($BaseUrlVar)) { throw "environment '$Environment' has no '$BaseUrlVar' variable" }
$baseUrl = (Resolve-Template ([string]$vars[$BaseUrlVar])).TrimEnd('/')
if (-not $baseUrl.StartsWith('https://')) { throw "'$BaseUrlVar' must be an https:// URL" }
if (-not $Path.StartsWith('/')) { $Path = '/' + $Path }

# Auth and headers from the collection's top-level folder.
$collectionDoc = @($workspaces.Values | Where-Object { $_['scope'] -eq 'collection' -and $_['name'] -eq $Collection })
if ($collectionDoc.Count -ne 1) { throw "expected one Insomnia collection named '$Collection', found $($collectionDoc.Count)" }
$collectionId = $collectionDoc[0]['_id']
$topFolders = @((Read-InsomniaDb 'insomnia.RequestGroup.db').Values | Where-Object { $_['parentId'] -eq $collectionId })
$folderDoc = $null
if ($Folder) {
    $match = @($topFolders | Where-Object { $_['name'] -eq $Folder })
    if ($match.Count -ne 1) { throw "expected one top-level folder '$Folder' in '$Collection', found $($match.Count)" }
    $folderDoc = $match[0]
} else {
    $withAuth = @($topFolders | Where-Object { $_['authentication'] -and $_['authentication']['type'] -and -not $_['authentication']['disabled'] })
    if ($withAuth.Count -gt 1) { throw "'$Collection' has $($withAuth.Count) top-level folders with auth; pass -Folder" }
    if ($withAuth.Count -eq 1) { $folderDoc = $withAuth[0] }
}

$headers = [ordered]@{ 'Accept' = 'application/json'; 'User-Agent' = 'api-call' }
$shownHeaders = [ordered]@{ 'Accept' = 'application/json'; 'User-Agent' = 'api-call' }
$secrets = [Collections.Generic.List[string]]::new()
$authText = 'none'
$queryKey = $null; $queryValue = $null
if ($folderDoc -and $folderDoc['authentication'] -and $folderDoc['authentication']['type'] -and -not $folderDoc['authentication']['disabled']) {
    $a = $folderDoc['authentication']
    switch ($a['type']) {
        'apikey' {
            $k = Resolve-Template ([string]$a['key']); $v = Resolve-Template ([string]$a['value'])
            $secrets.Add($v)
            if ($a['addTo'] -eq 'queryParams') { $queryKey = $k; $queryValue = $v; $authText = "API key in query parameter $k" }
            else { $headers[$k] = $v; $shownHeaders[$k] = '********'; $authText = "API key in header $k" }
        }
        'bearer' {
            $t = Resolve-Template ([string]$a['token'])
            $p = if ($a['prefix']) { Resolve-Template ([string]$a['prefix']) } else { 'Bearer' }
            $secrets.Add($t); $headers['Authorization'] = "$p $t"; $shownHeaders['Authorization'] = "$p ********"; $authText = 'bearer token'
        }
        default { throw "auth type '$($a['type'])' on folder '$($folderDoc['name'])' is not supported (API key and bearer only)" }
    }
}
foreach ($h in @($folderDoc ? $folderDoc['headers'] : @())) {
    if (-not $h -or $h['disabled'] -or -not $h['name']) { continue }
    $name = Resolve-Template ([string]$h['name']); $raw = [string]$h['value']; $value = Resolve-Template $raw
    $headers[$name] = $value
    if ($raw -match '\{\{') { $secrets.Add($value); $shownHeaders[$name] = '********' } else { $shownHeaders[$name] = $value }
}

$builder = [UriBuilder]::new($baseUrl + $Path)
$shownUrl = $builder.Uri.AbsoluteUri
if ($queryKey) {
    $pair = [Uri]::EscapeDataString($queryKey) + '=' + [Uri]::EscapeDataString($queryValue)
    $shownPair = [Uri]::EscapeDataString($queryKey) + '=********'
    $existing = $builder.Query.TrimStart('?')
    $builder.Query = if ($existing) { "$existing&$pair" } else { $pair }
    $shownUrl = $shownUrl + ($(if ($existing) { '&' } else { '?' })) + $shownPair
}
$uri = $builder.Uri

# The client certificate the collection holds for this host (Insomnia hosts may carry a port or a * wildcard).
function Test-HostMatch([string]$pattern, [Uri]$u) {
    $p = ($pattern -replace '^https?://', '').TrimEnd('/')
    $rx = '^' + ([regex]::Escape($p) -replace '\\\*', '.*') + '$'
    return ($u.Host -match $rx) -or ($u.Authority -match $rx)
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

# The request goes through HttpClient, as Invoke-WebRequest's does, with redirects off: a redirect is recorded as the
# response, never followed, since .NET would resend a key header to whatever host it points at.
$handler = [Net.Http.HttpClientHandler]::new()
$handler.AllowAutoRedirect = $false
if ($cert) { [void]$handler.ClientCertificates.Add($cert) }
$client = [Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds($TimeoutSec)
$message = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Get, $uri)
foreach ($k in $headers.Keys) { [void]$message.Headers.TryAddWithoutValidation($k, [string]$headers[$k]) }
$sent = [DateTimeOffset]::Now
$watch = [Diagnostics.Stopwatch]::StartNew()
try {
    $response = $client.SendAsync($message).GetAwaiter().GetResult()
    $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
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

# .NET keeps Content-* headers on the content and the rest on the response; gather both, then pick the four shown.
$all = @{}
foreach ($pair in $response.Headers) { $all[$pair.Key.ToLowerInvariant()] = ($pair.Value -join ', ') }
foreach ($pair in $response.Content.Headers) { $all[$pair.Key.ToLowerInvariant()] = ($pair.Value -join ', ') }
$responseHeaders = [ordered]@{}
foreach ($h in 'Content-Type', 'Content-Length', 'Date', 'Location') { if ($all.ContainsKey($h.ToLowerInvariant())) { $responseHeaders[$h] = $all[$h.ToLowerInvariant()] } }

$json = [ordered]@{
    source          = 'insomnia'
    environment     = $Environment
    collection      = $Collection
    folder          = $folderDoc ? $folderDoc['name'] : $null
    method          = 'GET'
    path            = $Path
    url             = $shownUrl
    auth            = $authText
    request_headers = $shownHeaders
    client_cert     = $certName
    status          = [int]$response.StatusCode
    elapsed_ms      = [int]$watch.ElapsedMilliseconds
    sent            = $sent.ToString('yyyy-MM-ddTHH:mm:ss.fffzzz')
    sent_utc        = $sent.UtcDateTime.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
    response_headers = $responseHeaders
    body            = $body
} | ConvertTo-Json -Depth 6 -Compress

foreach ($s in $secrets) { if ($s -and $s.Length -ge 6 -and $json.Contains($s)) { throw 'refusing to print: a secret would appear in the output' } }
$json
