<#
.SYNOPSIS
Lists or updates the Azure Key Vault secrets that hold the cluster-secrets
variables (old-queues/QUEUE-azure-keyvault.md).

.DESCRIPTION
Each cluster-secrets variable is one Key Vault secret, named after it with "_"
replaced by "-" (MEDIA_SONARR_API_KEY is MEDIA-SONARR-API-KEY); the
cluster-secrets ExternalSecret maps each variable to its secret.

With no -Name, outputs one object per variable (never the values), with:
  Name          the variable's name. For a Key Vault secret the ExternalSecret
                doesn't map, its name with "-" turned back into "_".
  KeyVaultName  its secret's name in Key Vault.
  Encoding      how the variable reaches what uses it. Key Vault holds every
                value plain. plain (b64enc) when the cluster-secrets template
                encodes it, as exactly {{ .NAME | b64enc }}, for a Secret's
                `data:`, which takes base64; plain when only other files use
                it; empty when nothing uses it. NEEDS b64enc when a Secret's
                `data:` uses it and the template doesn't encode it: add
                NAME: "{{ .NAME | b64enc }}" to the template. UNKNOWN
                (template) when the template builds it any other way.
  Synced        yes when the ExternalSecret maps it and Key Vault holds it;
                Dockhand when the ExternalSecret doesn't map it and only Dockhand
                stacks read it (docker/secret-names.yaml), rightly outside the
                cluster; no when Key Vault holds it but neither reads it (so it
                never reaches the cluster); DISABLED when Key Vault holds
                it disabled (External Secrets can't read it); MISSING IN KEY
                VAULT for a name the ExternalSecret maps but the vault lacks.
  UsedBy        the Flux Kustomizations whose files use the variable, and
                docker/<stack> for each Dockhand stack that reads it, as an
                array (empty when unused).
They show as a table (keyvault-secrets.format.ps1xml), and filter and sort like
any other objects.

With -Name (a variable's name, with "_"), sets its secret. The value is asked
for without echoing it (or taken from -Value, which leaves it in your shell
history), then written exactly as given as a new version of the secret and
read back, compared by SHA-256. The script then force-syncs the ExternalSecret,
waits for the cluster-secrets Secret to hold the new value, and prints the Flux
reconcile commands to run.

With -Name and -Get, outputs the secret's current value instead, the only
path that shows one, as Key Vault holds it and as a string, so it can go to a
variable or Set-Clipboard rather than the screen.

A variable whose Encoding is UNKNOWN (template) is set or read only with -Raw.
One the ExternalSecret maps whose Encoding is NEEDS b64enc is never set: the
cluster would get the plain value where base64 belongs, so its template entry
comes first. -New, which adds a secret, runs only with -Raw (scripts/README.md's
"A new private value").

It signs in as the Azure CLI's current account (az login), which needs "Key
Vault Secrets Officer" on the vault to set a secret, or "Key Vault Secrets
User" to list. The vault's URL comes from the azure-keyvault-bootstrap Secret
in flux-system (or -Vault names the vault), the variable mapping and template
from the live ExternalSecret, and the variable usage from the kubernetes/
folder of this checkout. Run `git pull` first.

.EXAMPLE
./scripts/keyvault-secrets.ps1
Lists the variables.

.EXAMPLE
./scripts/keyvault-secrets.ps1 | Where-Object Synced -notin 'yes', 'Dockhand'
The secrets nothing reads, and the names Key Vault lacks.

.EXAMPLE
./scripts/keyvault-secrets.ps1 | Where-Object UsedBy -contains 'mealie'
The variables the mealie Kustomization uses.

.EXAMPLE
./scripts/keyvault-secrets.ps1 IMMICH_POWER_TOOLS_APIKEY_IMMICH_APIKEY
Asks for the new value and sets the secret.

.EXAMPLE
./scripts/keyvault-secrets.ps1 IMMICH_POWER_TOOLS_APIKEY_IMMICH_APIKEY -Get | Set-Clipboard
Copies the secret's current value, without showing it.

.EXAMPLE
./scripts/keyvault-secrets.ps1 NEW_VARIABLE -New -Raw
Asks for a new variable's value and adds its secret, plain.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Name,
    # The new value. Leave it out to be prompted without echo.
    [string]$Value,
    # Output the secret's current value instead of setting it.
    [switch]$Get,
    # Set or read a variable the cluster-secrets template builds other than as
    # {{ .NAME | b64enc }}, whose form isn't known. -New needs it too.
    [switch]$Raw,
    # Allow adding a secret that doesn't exist yet; only with -Raw. It also needs
    # an entry in kubernetes/secrets/cluster-secrets.yaml before it reaches the cluster.
    [switch]$New,
    # Don't force-sync the ExternalSecret afterwards.
    [switch]$NoSync,
    # The vault's name, in place of the URL in the azure-keyvault-bootstrap Secret.
    [string]$Vault
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Text.UTF8Encoding]::new($false)

# Adding a secret states that it goes in exactly as given. Checked before anything is
# read, so a refused run touches nothing.
if ($New -and -not $Raw -and -not $Get) {
    throw "-New needs -Raw: Key Vault holds a new value plain, as given, and cluster-secrets' template encodes it where a Secret's data: reads it."
}

$ApiVersion = '7.4'
$StoreName = 'azure-keyvault'

# Variable name -> the files under kubernetes/ that use it, and whether each
# is a Secret. `$${X}` is an escape Flux leaves alone, so it doesn't count.
# Files come from git, tracked or new, so what .gitignore keeps out (capture's
# local copies in .local/ and raw/, which Flux never applies) is left out too.
function Get-VariableUsage {
    $repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
    $listed = git -C $repo ls-files -z --cached --others --exclude-standard -- kubernetes/
    if ($LASTEXITCODE -ne 0) { throw 'git ls-files failed, so the variable usage is unknown.' }
    $usage = @{}
    foreach ($path in ($listed -join '') -split "`0") {
        if ($path -notmatch '^kubernetes/(.+\.ya?ml)$') { continue }
        $rel = $Matches[1]
        $full = Join-Path $repo $path
        if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { continue }   # deleted, not yet committed
        $text = Get-Content -Raw -Encoding UTF8 -LiteralPath $full
        if (-not $text) { continue }
        $isSecret = $text -match '(?m)^kind:\s*Secret\s*$'
        # Only a Secret's data: holds base64; its stringData: takes the value as
        # is. So what counts is the top-level key a variable sits under.
        $section = ''
        foreach ($line in $text -split "`n") {
            if ($line -match '^([A-Za-z]+):') { $section = $Matches[1] }
            foreach ($m in [regex]::Matches($line, '(?<!\$)\$\{([A-Za-z0-9_]+)\}')) {
                $var = $m.Groups[1].Value
                if (-not $usage[$var]) { $usage[$var] = @{} }
                $usage[$var][$rel] = $isSecret -and $section -eq 'data'
            }
        }
    }
    $usage
}

# Key Vault secret name (lower case) -> the Dockhand stacks that read it, from
# docker/secret-names.yaml: each "  VARIABLE: NAME" under a "stack:" line, and each
# ${NAME} in a {built: "..."} template. A {none: ...} entry reads nothing.
function Get-DockhandUsage {
    $map = Join-Path (Join-Path $PSScriptRoot '..') 'docker/secret-names.yaml'
    if (-not (Test-Path -LiteralPath $map -PathType Leaf)) { throw "No $map, so which secrets Dockhand reads is unknown." }
    $readers = @{}
    $stack = ''
    foreach ($line in Get-Content -Encoding UTF8 -LiteralPath $map) {
        if ($line -match '^([A-Za-z0-9_.-]+):\s*$') { $stack = $Matches[1]; continue }
        if (-not $stack -or $line -notmatch '^\s+[A-Za-z0-9_]+:\s*(.+?)\s*$') { continue }
        $value = $Matches[1]
        $names = if ($value -match '^[A-Z0-9_]+$') { @($value) }
                 elseif ($value -match '^\{\s*built:') { @([regex]::Matches($value, '\$\{([A-Z0-9_]+)\}') | ForEach-Object { $_.Groups[1].Value }) }
                 else { @() }
        foreach ($n in $names) {
            $key = ($n -replace '_', '-').ToLowerInvariant()
            if (-not $readers[$key]) { $readers[$key] = [Collections.Generic.List[string]]::new() }
            if (-not $readers[$key].Contains("docker/$stack")) { $readers[$key].Add("docker/$stack") }
        }
    }
    if ($readers.Count -eq 0) { throw "$map lists no secrets, so it wasn't read as expected." }
    $readers
}

# The Flux Kustomization that applies a file, from its path under kubernetes/
# (CLAUDE.md's layout table).
function Get-KustomizationName([string]$rel) {
    $parts = $rel -split '/'
    switch ($parts[0]) {
        # prod/<category>/<namespace>/<release>/app/...: the release's own
        # Kustomization, which ks.yaml names after the folder. Its ks.yaml and
        # everything above it is applied by `cluster`.
        'prod' { if ($parts.Count -gt 5) { return $parts[3] } else { return 'cluster' } }
        'cluster' { return 'cluster-resources' }
        'flux' { if ($parts[1] -eq 'meta') { return 'cluster-meta' } else { return 'flux-system' } }
        'secrets' { return 'cluster-secrets' }
        'shared' { return 'cluster-shared' }
        'test' { return 'cluster-test' }
    }
    "(unknown: $rel)"
}

function Get-Sha256([string]$text) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { -join ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($text)) | ForEach-Object { $_.ToString('x2') }) }
    finally { $sha.Dispose() }
}

# The vault's base URL: from -Vault, or from the bootstrap Secret External
# Secrets signs in with.
function Get-VaultUrl {
    if ($Vault) {
        if ($Vault -notmatch '^[A-Za-z][A-Za-z0-9-]{1,22}[A-Za-z0-9]$') { throw "-Vault: '$Vault' isn't a Key Vault name (3-24 letters, digits and '-')." }
        return "https://$Vault.vault.azure.net"
    }
    $b64 = kubectl get secret azure-keyvault-bootstrap -n flux-system -o 'jsonpath={.data.AZURE_KEYVAULT_URL}'
    if ($LASTEXITCODE -ne 0 -or -not $b64) { throw 'Could not read AZURE_KEYVAULT_URL from the azure-keyvault-bootstrap Secret in flux-system; name the vault with -Vault.' }
    ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64))).Trim().TrimEnd('/')
}

function Get-AccessToken {
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) { throw "The Azure CLI (az) isn't on PATH." }
    $token = az account get-access-token --resource https://vault.azure.net --query accessToken -o tsv
    if ($LASTEXITCODE -ne 0 -or -not $token) { throw 'az account get-access-token failed: run az login first.' }
    $token
}

# One Key Vault REST call: its HTTP status and JSON body, an error's too. Works
# in Windows PowerShell and PowerShell 7, which report an HTTP error differently.
function Invoke-KeyVault([string]$Method, [string]$Url, $Body) {
    $params = @{ Method = $Method; Uri = $Url; Headers = @{ Authorization = "Bearer $script:token" }; UseBasicParsing = $true }
    if ($null -ne $Body) {
        $params.Body = [Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Compress))
        $params.ContentType = 'application/json'
    }
    try {
        $response = Invoke-WebRequest @params
        $content = if ($response.Content -is [byte[]]) { [Text.Encoding]::UTF8.GetString($response.Content) } else { $response.Content }
        [pscustomobject]@{ Status = [int]$response.StatusCode; Body = if ($content) { $content | ConvertFrom-Json } else { $null } }
    }
    catch {
        $response = $_.Exception.Response
        if ($null -eq $response) { throw "Key Vault couldn't be reached ($Method $Url): $($_.Exception.Message)" }
        $parsed = $null
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
            try { $parsed = $_.ErrorDetails.Message | ConvertFrom-Json } catch { $parsed = $null }
        }
        [pscustomobject]@{ Status = [int]$response.StatusCode; Body = $parsed }
    }
}

function Get-ErrorCode($result) { if ($result.Body -and $result.Body.error) { $result.Body.error.code } else { '' } }

function Get-SecretUrl([string]$secretName) { "$vaultUrl/secrets/$([uri]::EscapeDataString($secretName))?api-version=$ApiVersion" }

# Every secret in the vault: name -> enabled. Names compare without case, as
# Key Vault's do.
function Get-VaultSecrets {
    $secrets = [ordered]@{}
    $url = "$vaultUrl/secrets?api-version=$ApiVersion&maxresults=25"
    while ($url) {
        $result = Invoke-KeyVault GET $url $null
        if ($result.Status -ne 200) { throw "Key Vault refused the list of secrets: HTTP $($result.Status) $(Get-ErrorCode $result)" }
        foreach ($item in @($result.Body.value)) {
            $secrets[($item.id -split '/')[-1]] = [bool]$item.attributes.enabled
        }
        $url = $result.Body.nextLink
    }
    $secrets
}

$es = kubectl get externalsecret cluster-secrets -n flux-system -o json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Could not read the cluster-secrets ExternalSecret.' }
if ($es.spec.secretStoreRef.name -ne $StoreName) {
    throw "cluster-secrets reads the '$($es.spec.secretStoreRef.name)' store, not '$StoreName': Key Vault isn't where its values come from."
}
# Variable -> its Key Vault secret's name.
$mapped = [ordered]@{}
foreach ($entry in @($es.spec.data)) { $mapped[$entry.secretKey] = $entry.remoteRef.key }
# Variable -> how the template treats it, when it has a key of the variable's own name
# (which replaces the variable's value): b64enc for exactly {{ .NAME | b64enc }}, so Key
# Vault holds it plain; other for anything else, whose form in Key Vault isn't known.
$templated = @{}
if ($es.spec.target.template.data) {
    foreach ($key in $es.spec.target.template.data.PSObject.Properties) {
        if (-not $mapped.Contains($key.Name)) { continue }   # built from other variables
        $templated[$key.Name] = if ($key.Value -ceq "{{ .$($key.Name) | b64enc }}") { 'b64enc' } else { 'other' }
    }
}
$usage = Get-VariableUsage
$vaultUrl = Get-VaultUrl
$token = Get-AccessToken

if (-not $Name) {
    if ($Get) { throw '-Get needs the name of the variable to read.' }
    Update-FormatData -PrependPath (Join-Path $PSScriptRoot 'keyvault-secrets.format.ps1xml')
    $inVault = Get-VaultSecrets
    $held = @{}
    foreach ($k in $inVault.Keys) { $held[$k.ToLowerInvariant()] = $inVault[$k] }
    $mappedNames = @{}
    foreach ($v in $mapped.Keys) { $mappedNames[$mapped[$v].ToLowerInvariant()] = $true }
    $dockhand = Get-DockhandUsage
    $row = {
        param($variable, $secretName, $synced)
        $files = $usage[$variable]
        $stacks = $dockhand[$secretName.ToLowerInvariant()]
        [pscustomobject]@{
            PSTypeName   = 'Homelab.KeyVaultSecret'
            Name         = $variable
            KeyVaultName = $secretName
            Encoding     = if ($templated[$variable] -eq 'b64enc') { 'plain (b64enc)' }
                           elseif ($templated[$variable]) { 'UNKNOWN (template)' }
                           elseif (-not $files) { '' } elseif ($files.Values -contains $true) { 'NEEDS b64enc' } else { 'plain' }
            Synced       = $synced
            UsedBy       = [string[]]@(@(if ($files) { $files.Keys | ForEach-Object { Get-KustomizationName $_ } | Sort-Object -Unique }) + @(if ($stacks) { $stacks | Sort-Object }))
        }
    }
    $rows = @(foreach ($variable in $mapped.Keys) {
        $secretName = $mapped[$variable]
        $key = $secretName.ToLowerInvariant()
        $synced = if (-not $held.ContainsKey($key)) { 'MISSING IN KEY VAULT' } elseif (-not $held[$key]) { 'DISABLED' } else { 'yes' }
        & $row $variable $secretName $synced
    })
    $rows += @(foreach ($secretName in $inVault.Keys) {
        if ($mappedNames.ContainsKey($secretName.ToLowerInvariant())) { continue }
        $synced = if (-not $inVault[$secretName]) { 'DISABLED' } elseif ($dockhand.ContainsKey($secretName.ToLowerInvariant())) { 'Dockhand' } else { 'no' }
        & $row ($secretName -replace '-', '_') $secretName $synced
    })
    $rows | Sort-Object Name
    return
}

if ($Name -notmatch '^[A-Za-z0-9_]+$') { throw "'$Name' isn't a variable name (letters, digits and '_')." }
$secretName = if ($mapped.Contains($Name)) { $mapped[$Name] } else { $Name -replace '_', '-' }
if ($secretName -notmatch '^[A-Za-z0-9-]{1,127}$') { throw "$secretName isn't a valid Key Vault name (letters, digits and '-', 1-127)." }

$files = $usage[$Name]
$inSecret = $files -and ($files.Values -contains $true)
$plainUses = @(if ($files) { $files.Keys | Where-Object { -not $files[$_] } | Sort-Object })
$encodes = $templated[$Name]
if ($encodes -eq 'other' -and -not $Raw) {
    throw "cluster-secrets' template builds $Name other than as {{ .$Name | b64enc }}, so the form Key Vault should hold it in isn't known. -Raw sets or reads it as Key Vault holds it."
}
# Key Vault holds every value plain. A Secret's data: takes base64, which only the
# template's {{ .NAME | b64enc }} entry gives it, and that entry gives every other file
# using the variable base64 too.
$needsEntry = $inSecret -and -not $encodes
if ($encodes -eq 'b64enc' -and $plainUses) {
    Write-Warning "cluster-secrets' template encodes $Name, so the files that use it outside a Secret's data get base64: $($plainUses -join ', ')"
}
elseif ($needsEntry -and $plainUses) {
    Write-Warning "A Secret's data: uses $Name, so cluster-secrets' template is to encode it, and then the files that use it outside a Secret's data get base64 too: $($plainUses -join ', ')"
}
# Once mapped, a value set now would reach that Secret plain. The template read is the
# live one, so an entry merged but not yet applied by Flux doesn't count.
if ($needsEntry -and $mapped.Contains($Name)) {
    $missing = "A Secret's data: uses $Name, but cluster-secrets' template doesn't encode it, so the cluster gets the plain value where base64 belongs. Add $Name`: `"{{ .$Name | b64enc }}`" to its template in kubernetes/secrets/cluster-secrets.yaml first; if that's merged, wait for Flux to apply cluster-secrets."
    if (-not $Get) { throw $missing }
    Write-Warning $missing
}

if ($Get) {
    if ($PSBoundParameters.ContainsKey('Value') -or $New -or $NoSync) { throw '-Get only reads: leave out -Value, -New and -NoSync.' }
    $current = Invoke-KeyVault GET (Get-SecretUrl $secretName) $null
    if ($current.Status -eq 404) { throw "Key Vault has no secret named $secretName." }
    # A disabled secret's read is refused (403), so it lands here too.
    if ($current.Status -ne 200) { throw "Key Vault refused to read $secretName`: HTTP $($current.Status) $(Get-ErrorCode $current)" }
    return [string]$current.Body.value
}

if (-not $files) { Write-Warning "Nothing under kubernetes/ uses `${$Name}` in this checkout." }

# Its current value, by hash only. A read Key Vault refuses stops the run: it
# isn't an absent secret.
$current = Invoke-KeyVault GET (Get-SecretUrl $secretName) $null
if ($current.Status -eq 404) {
    if (-not $New) { throw "Key Vault has no secret named $secretName (use -New to add it)." }
    $currentHash = $null
}
elseif ($current.Status -eq 200) {
    if (-not $current.Body.attributes.enabled) { throw "$secretName is disabled in Key Vault, so External Secrets can't read it: enable it first." }
    $currentHash = Get-Sha256 $current.Body.value
}
else { throw "Key Vault refused to read $secretName`: HTTP $($current.Status) $(Get-ErrorCode $current)" }

if (-not $PSBoundParameters.ContainsKey('Value')) {
    $secure = Read-Host "New value for $Name" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $Value = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
if ($Value -eq '') { throw 'Empty value; nothing changed.' }

if ($currentHash -eq (Get-Sha256 $Value)) {
    Write-Host "$secretName already holds that value; nothing written."
}
else {
    $put = Invoke-KeyVault PUT (Get-SecretUrl $secretName) @{ value = $Value; tags = @{ source = 'cluster-secrets' } }
    if ($put.Status -eq 409 -and (Get-ErrorCode $put) -eq 'Conflict') {
        throw "$secretName is deleted but recoverable: recover or purge it in Key Vault first."
    }
    if ($put.Status -ne 200) { throw "Key Vault refused to set $secretName`: HTTP $($put.Status) $(Get-ErrorCode $put)" }
    $back = Invoke-KeyVault GET (Get-SecretUrl $secretName) $null
    if ($back.Status -ne 200 -or (Get-Sha256 $back.Body.value) -ne (Get-Sha256 $Value)) {
        throw "Read back, $secretName doesn't hold the value just set (HTTP $($back.Status)): check it in Key Vault."
    }
    Write-Host "$(if ($null -eq $currentHash) { 'Added' } else { 'Updated' }) $secretName for $Name, read back."
}

if (-not $mapped.Contains($Name)) {
    $entry = if ($needsEntry) { ", with $Name`: `"{{ .$Name | b64enc }}`" in its template since a Secret's data: uses it," } else { '' }
    Write-Warning "The ExternalSecret doesn't map $Name yet. Add it to kubernetes/secrets/cluster-secrets.yaml (remoteRef key $secretName)$entry and merge that before anything uses it."
    return
}
if (-not $NoSync) {
    kubectl annotate externalsecret cluster-secrets -n flux-system "force-sync=$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())" --overwrite | Out-Null
    if ($encodes -eq 'other') {
        Write-Warning "Force-synced, but cluster-secrets' template builds $Name, so whether its Secret has the new value isn't checked."
    }
    else {
        # The Secret's value is what Key Vault holds, encoded once more where the template encodes it.
        $inCluster = if ($encodes) { [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Value)) } else { $Value }
        $want = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($inCluster))
        $deadline = (Get-Date).AddSeconds(60)
        do {
            Start-Sleep -Seconds 2
            $have = kubectl get secret cluster-secrets -n flux-system -o "jsonpath={.data.$Name}"
        } until ($have -eq $want -or (Get-Date) -gt $deadline)
        if ($have -ne $want) { throw 'The cluster-secrets Secret still has the old value after 60s. Check: kubectl describe externalsecret cluster-secrets -n flux-system' }
        Write-Host 'The cluster-secrets Secret has the new value.'
    }
}

if ($files) {
    Write-Host "`nApply it with:"
    $files.Keys | ForEach-Object { Get-KustomizationName $_ } | Sort-Object -Unique |
        ForEach-Object { Write-Host "  flux reconcile kustomization $_ --with-source" }
    if ($inSecret) {
        Write-Host "`nThen restart the pods that read these Secrets (env vars from a Secret only change on restart):"
        $files.Keys | Where-Object { $files[$_] } | ForEach-Object { Write-Host "  $_" }
    }
}
