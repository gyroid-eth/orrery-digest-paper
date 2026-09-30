# zotero_helper.ps1 - run by zotero_lookup.py from WSL through interop.
#
# Receives one request as $RequestB64 (base64 JSON, prepended by the caller),
# checks it against a fixed allowlist, sends it to Zotero on THIS computer's
# localhost:23119 only, and prints one line of JSON: {"status": N, "body": "..."}
# or {"error": "..."}. Read-only: GET of fixed paths and four Better BibTeX
# lookup methods. No proxy, no redirects; nothing else is reachable.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Out-Json($obj) { [Console]::Out.WriteLine(($obj | ConvertTo-Json -Compress -Depth 4)) }

try {
    $req = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($RequestB64)) | ConvertFrom-Json
} catch {
    Out-Json @{ error = 'bad request' }; exit 0
}

$base = 'http://127.0.0.1:23119'
# Same list as API_PATH in zotero_lookup.py (personal library, read-only).
$apiPath = '^/api/(|users/0/items/[A-Z0-9]{8}|users/0/items/[A-Z0-9]{8}/children|users/0/items/top\?q=[A-Za-z0-9%._~-]{1,300}&qmode=everything&limit=25|users/0/items/top\?limit=100&start=\d{1,6})$'
$allowedMethods = @('api.ready', 'item.citationkey', 'item.attachments')

# Invoke-WebRequest in Windows PowerShell 5.1 sends even loopback requests
# through the system proxy, which closes the connection (Windows test machine, 2026-09-29).
# Use HttpWebRequest directly: no proxy, no keep-alive, no redirects.
$uri = $null
$method = 'GET'
$bodyBytes = $null
$contentType = $null
$connectorMetadata = $null
if ($req.op -eq 'get') {
    $path = [string]$req.path
    if (-not ($path -eq '/connector/ping' -or $path -cmatch $apiPath)) {
        Out-Json @{ error = 'request not allowed' }; exit 0
    }
    $uri = $base + $path
} elseif ($req.op -eq 'rpc') {
    if ($allowedMethods -notcontains [string]$req.method) {
        Out-Json @{ error = 'request not allowed' }; exit 0
    }
    $rpc = @{ jsonrpc = '2.0'; method = [string]$req.method; params = @($req.params); id = 1 }
    $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes(($rpc | ConvertTo-Json -Compress -Depth 6))
    $uri = $base + '/better-bibtex/json-rpc'
    $method = 'POST'
} elseif ($req.op -eq 'connector') {
    # Write path, used only by zotero_register.py for a run that asked for a
    # new paper: read the save target, save one item, attach one PDF.
    $connectorPaths = @('/connector/getSelectedCollection', '/connector/saveItems', '/connector/saveAttachment')
    if ($connectorPaths -notcontains [string]$req.path) {
        Out-Json @{ error = 'request not allowed' }; exit 0
    }
    $uri = $base + [string]$req.path
    $method = 'POST'
    $connectorMetadata = [string]$req.metadata
    if ([string]$req.path -eq '/connector/saveAttachment') {
        # The PDF comes from a file, never through the command line.
        $bodyBytes = [System.IO.File]::ReadAllBytes([string]$req.file)
        $contentType = 'application/pdf'
    } else {
        $bodyBytes = [Convert]::FromBase64String([string]$req.body_b64)
        $contentType = 'application/json'
    }
} else {
    Out-Json @{ error = 'request not allowed' }; exit 0
}

function Read-Body($response) {
    $reader = New-Object System.IO.StreamReader($response.GetResponseStream(), [System.Text.Encoding]::UTF8)
    try { return $reader.ReadToEnd() } finally { $reader.Close() }
}

try {
    $web = [System.Net.HttpWebRequest]::Create($uri)
    $web.Method = $method
    $web.Proxy = $null
    $web.KeepAlive = $false
    $web.AllowAutoRedirect = $false
    $web.Timeout = 10000
    $web.ReadWriteTimeout = 10000
    $web.Headers.Add('Zotero-API-Version', '3')
    if ($req.op -eq 'connector') {
        $web.Headers.Add('X-Zotero-Connector-API-Version', '3')
        if ($connectorMetadata) { $web.Headers.Add('X-Metadata', $connectorMetadata) }
    }
    if ($bodyBytes -ne $null) {
        $web.ContentType = $(if ($contentType) { $contentType } else { 'application/json' })
        $web.ContentLength = $bodyBytes.Length
        $stream = $web.GetRequestStream()
        try { $stream.Write($bodyBytes, 0, $bodyBytes.Length) } finally { $stream.Close() }
    }
    $response = $web.GetResponse()
    try { Out-Json @{ status = [int]$response.StatusCode; body = (Read-Body $response) } }
    finally { $response.Close() }
} catch {
    $ex = $_.Exception
    while ($ex -ne $null -and -not ($ex -is [System.Net.WebException])) { $ex = $ex.InnerException }
    if ($ex -ne $null -and $ex.Response -ne $null) {
        # A non-2xx answer (403 from a disabled Local API, 404, a redirect).
        $response = $ex.Response
        try { Out-Json @{ status = [int]$response.StatusCode; body = (Read-Body $response) } }
        finally { $response.Close() }
    } else {
        Out-Json @{ error = ('no answer from Zotero on localhost:23119: ' + $_.Exception.Message) }
    }
}
