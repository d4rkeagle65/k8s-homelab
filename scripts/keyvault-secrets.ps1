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
reconcile commands to run, then, for each Secret the variable is in (data: or
stringData:), the kubectl rollout restart of every workload in the live cluster
that reads that Secret: a pod only gets a Secret's new value when it restarts. A
value that starts or ends with whitespace is set as given, with a warning.

-Generate N sets a new random value instead: N letters and digits (A-Z, a-z,
0-9) from the system's cryptographic generator, never shown. -Clipboard copies
the value just set, generated or typed, to the clipboard once it reads back.

With -Name and -Get, outputs the secret's current value instead, the only
path that shows one, as Key Vault holds it and as a string, so it can go to a
variable or Set-Clipboard rather than the screen; with -Clipboard it goes
straight to the clipboard and nothing is output. It warns, without the value, when
the value starts or ends with whitespace (a space, tab, newline...).

With -Name and -Delete, deletes the secret, refusing while anything reads it:
the live cluster-secrets ExternalSecret mapping it, another ExternalSecret
reading it straight from Key Vault, a file under kubernetes/ using ${NAME}, or a
Dockhand stack in docker/secret-names.yaml. It asks for the secret's name to be
typed to confirm. Key Vault keeps a deleted secret until its purge date, which
the script reads back and prints; until then a secret of that name can't be
added. -Recover brings a deleted secret back, with the value it had, reads it
back, and force-syncs cluster-secrets when that maps it.

-Export FILE writes every secret in the vault, values included, to FILE,
encrypted with a password typed twice (AES-256 with an HMAC, keys from
PBKDF2-SHA256), then decrypts the file again and checks it holds every value
read. FILE must be new and outside this repository, which is public. A disabled
secret's value can't be read, so it's listed by name only. -Import FILE reads
such a file and reports, by name, what it would add to the vault, what's the
same and what differs; -Apply adds the missing ones, and -Overwrite also
replaces the ones that differ (Key Vault keeps the old value as an earlier
version). Each written value is read back. Neither touches the cluster, so on a
rebuild name the new vault with -Vault. Values are never shown.

A secret an app's own ExternalSecret reads straight from Key Vault (in a
namespace opted in to the azure-keyvault store, not through cluster-secrets) is
named by its Key Vault name, with "-" or "_"; the script says which files read
it, and neither maps it into cluster-secrets nor syncs that.

A variable whose Encoding is UNKNOWN (template) is set or read only with -Raw.
One the ExternalSecret maps whose Encoding is NEEDS b64enc is never set: the
cluster would get the plain value where base64 belongs, so its template entry
comes first. -New, which adds a secret, runs only with -Raw (scripts/README.md's
"A new private value").

It signs in as the Azure CLI's current account (az login), which needs "Key
Vault Secrets Officer" on the vault to set, delete or recover a secret, or "Key
Vault Secrets User" to list, read or -Export (-Import -Apply writes, so it needs Officer). The vault's URL comes from the azure-keyvault-bootstrap Secret
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

.EXAMPLE
./scripts/keyvault-secrets.ps1 SNMP-V3-AUTH-PASSPHRASE -New -Raw -Generate 16 -Clipboard
Adds a secret holding 16 random letters and digits, and copies it to paste into
a device's settings.

.EXAMPLE
./scripts/keyvault-secrets.ps1 OLD_VARIABLE -Delete
Deletes a secret nothing reads any more, once its name is typed.

.EXAMPLE
./scripts/keyvault-secrets.ps1 OLD_VARIABLE -Recover
Brings a deleted secret back before its purge date.

.EXAMPLE
./scripts/keyvault-secrets.ps1 -Export D:\backup\keyvault-2026-10-31.json
Writes every secret to an encrypted file, asking for its password twice.

.EXAMPLE
./scripts/keyvault-secrets.ps1 -Import D:\backup\keyvault-2026-10-31.json -Vault new-vault -Apply
Adds the secrets a new vault lacks from that file, after a dry run without -Apply.
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
    [string]$Vault,
    # Set a random value of this many letters and digits, in place of -Value.
    [ValidateRange(8, 4096)][int]$Generate,
    # Copy the value (set, or read with -Get) to the clipboard.
    [switch]$Clipboard,
    # Delete the secret (soft delete: -Recover brings it back until it's purged),
    # once nothing reads it and its name is typed to confirm.
    [switch]$Delete,
    # Recover a deleted secret, with the value it had.
    [switch]$Recover,
    # Write every secret, values included, to this file, encrypted with a password
    # typed twice. The file must not exist yet, nor be inside this repository.
    [string]$Export,
    # Read a file -Export wrote and report what it would add to the vault; with
    # -Apply, add them. Needs no cluster: name the vault with -Vault.
    [string]$Import,
    # With -Import: write what it reports, instead of only reporting.
    [switch]$Apply,
    # With -Import -Apply: also replace a secret the vault holds with a different
    # value (Key Vault keeps the old value as an earlier version).
    [switch]$Overwrite
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Text.UTF8Encoding]::new($false)

# Adding a secret states that it goes in exactly as given. Checked before anything is
# read, so a refused run touches nothing.
if ($New -and -not $Raw -and -not $Get) {
    throw "-New needs -Raw: Key Vault holds a new value plain, as given, and cluster-secrets' template encodes it where a Secret's data: reads it."
}
if ($Generate -and ($PSBoundParameters.ContainsKey('Value') -or $Get)) {
    throw '-Generate makes the value: leave out -Value and -Get.'
}
if ($Delete -or $Recover) {
    $others = @('Value', 'Get', 'Raw', 'New', 'Generate', 'Clipboard') | Where-Object { $PSBoundParameters.ContainsKey($_) }
    if ($Delete -and $Recover) { throw '-Delete and -Recover undo each other: give one.' }
    if ($others) { throw "$(if ($Delete) { '-Delete' } else { '-Recover' }) takes only a name (and -Vault, -NoSync): leave out -$($others -join ', -')." }
    if (-not $Name) { throw "$(if ($Delete) { '-Delete' } else { '-Recover' }) needs the name of the secret." }
}
if ($Export -or $Import) {
    $mode = if ($Export) { '-Export' } else { '-Import' }
    if ($Export -and $Import) { throw '-Export and -Import go in opposite directions: give one.' }
    $others = @('Name', 'Value', 'Get', 'Raw', 'New', 'NoSync', 'Generate', 'Clipboard', 'Delete', 'Recover') | Where-Object { $PSBoundParameters.ContainsKey($_) }
    if ($others) { throw "$mode takes only a file (and -Vault): leave out -$($others -join ', -')." }
}
if (($Apply -or $Overwrite) -and -not $Import) { throw '-Apply and -Overwrite go with -Import.' }
if ($Overwrite -and -not $Apply) { throw '-Overwrite only changes what -Apply writes: give both.' }
if ($Clipboard -and -not (Get-Command Set-Clipboard -ErrorAction SilentlyContinue)) {
    throw "-Clipboard needs Set-Clipboard, which this PowerShell doesn't have."
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
        # is. So what counts is the top-level key a variable sits under: 'data' for a
        # Secret's data:, 'secret' for anywhere else in a Secret, '' outside one.
        $section = ''
        foreach ($line in $text -split "`n") {
            if ($line -match '^([A-Za-z]+):') { $section = $Matches[1] }
            foreach ($m in [regex]::Matches($line, '(?<!\$)\$\{([A-Za-z0-9_]+)\}')) {
                $var = $m.Groups[1].Value
                if (-not $usage[$var]) { $usage[$var] = @{} }
                $usage[$var][$rel] = if (-not $isSecret) { '' } elseif ($section -eq 'data') { 'data' } else { 'secret' }
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

# $length letters and digits, each drawn evenly from the 62 by rejection: a byte at or
# above 248 (62 * 4) is thrown away, so none is likelier than another.
function New-RandomString([int]$length) {
    $alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $chars = [Text.StringBuilder]::new($length)
        $buffer = [byte[]]::new(64)
        while ($chars.Length -lt $length) {
            $rng.GetBytes($buffer)
            foreach ($b in $buffer) {
                if ($b -ge 248) { continue }
                [void]$chars.Append($alphabet[$b % 62])
                if ($chars.Length -eq $length) { break }
            }
        }
        $chars.ToString()
    }
    finally { $rng.Dispose() }
}

# The files whose ExternalSecret, other than cluster-secrets, reads a Key Vault secret by
# this name straight from the vault: a `key: NAME` in a file of kind ExternalSecret.
function Get-DirectReaders([string]$secretName) {
    $repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
    $listed = git -C $repo ls-files -z --cached --others --exclude-standard -- kubernetes/
    if ($LASTEXITCODE -ne 0) { throw 'git ls-files failed, so which ExternalSecrets read the secret is unknown.' }
    $pattern = '(?im)\bkey:\s*["'']?' + [regex]::Escape($secretName) + '["'']?\s*[,}\r\n]'
    @(foreach ($path in ($listed -join '') -split "`0") {
        if ($path -notmatch '^kubernetes/(.+\.ya?ml)$' -or $path -like 'kubernetes/secrets/*') { continue }
        $full = Join-Path $repo $path
        if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { continue }
        $text = Get-Content -Raw -Encoding UTF8 -LiteralPath $full
        if ($text -and $text -match '(?m)^kind:\s*ExternalSecret\s*$' -and $text -match $pattern) { $path -replace '^kubernetes/', '' }
    })
}

# What whitespace a value starts or ends with, named, or $null when it has none: a copied
# value easily picks up a newline or a space, and the app then rejects it. Never the value.
function Get-EdgeWhitespace([string]$text) {
    $names = @{ ' ' = 'space'; "`t" = 'tab'; "`r" = 'CR'; "`n" = 'newline (LF)'; [string][char]0xA0 = 'non-breaking space' }
    $describe = {
        param([string]$run)
        ($run.ToCharArray() | ForEach-Object { $c = [string]$_; if ($names.ContainsKey($c)) { $names[$c] } else { 'U+{0:X4}' -f [int]$_ } }) -join ', '
    }
    $parts = @()
    if ($text -match '^(\s+)') { $parts += "starts with $(& $describe $Matches[1])" }
    if ($text -match '(\s+)$') { $parts += "ends with $(& $describe $Matches[1])" }
    if ($parts) { $parts -join '; ' } else { $null }
}

# The kubectl restart commands for the workloads in the live cluster that read the Secret
# a file under kubernetes/ defines (env, envFrom or a volume), or the file itself when that
# can't be read: a pod only gets a Secret's new value when it restarts.
function Get-RestartCommands([string]$rel) {
    $full = Join-Path (Join-Path (Join-Path $PSScriptRoot '..') 'kubernetes') $rel
    $text = Get-Content -Raw -Encoding UTF8 -LiteralPath $full
    $secret = if ($text -match '(?m)^metadata:\s*\r?\n(?:[ \t]+.*\r?\n)*?[ \t]+name:\s*["'']?([A-Za-z0-9.-]+)') { $Matches[1] } else { '' }
    $ns = if ($text -match '(?m)^[ \t]+namespace:\s*["'']?([a-z0-9-]+)') { $Matches[1] }
          elseif (($rel -split '/')[0] -eq 'prod') { ($rel -split '/')[2] } else { '' }
    if (-not $secret -or -not $ns) { return @("  (restart what reads the Secret in $rel)") }
    $json = kubectl get deployment,statefulset,daemonset -n $ns -o json 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $json) { return @("  (couldn't list the workloads in ${ns}: restart what reads Secret $secret)") }
    $found = @(foreach ($w in @(($json | ConvertFrom-Json).items)) {
        $spec = $w.spec.template.spec
        $reads = @($spec.volumes | Where-Object { $_.secret.secretName -eq $secret }).Count -gt 0
        foreach ($c in @($spec.containers) + @($spec.initContainers)) {
            if (@($c.envFrom | Where-Object { $_.secretRef.name -eq $secret }).Count -gt 0) { $reads = $true }
            if (@($c.env | Where-Object { $_.valueFrom.secretKeyRef.name -eq $secret }).Count -gt 0) { $reads = $true }
        }
        if ($reads) { "  kubectl -n $ns rollout restart $($w.kind.ToLowerInvariant())/$($w.metadata.name)" }
    })
    if ($found) { $found } else { @("  (nothing in $ns reads Secret $secret yet)") }
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
    # Invoke-WebRequest's own progress bar would draw over the script's at every call, and
    # slows Windows PowerShell's downloads; set here, it ends with this function.
    $ProgressPreference = 'SilentlyContinue'
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

function Get-DeletedSecretUrl([string]$secretName, [string]$action) {
    "$vaultUrl/deletedsecrets/$([uri]::EscapeDataString($secretName))$action`?api-version=$ApiVersion"
}

# A Key Vault time (seconds since 1970) as local time, or 'unknown'.
function Format-VaultTime($seconds) {
    if ($seconds) { [DateTimeOffset]::FromUnixTimeSeconds([long]$seconds).ToLocalTime().ToString('yyyy-MM-dd HH:mm zzz') } else { 'unknown' }
}

# Key Vault deletes and recovers in the background: polls GET $url until it answers
# $want, up to 60 seconds, and returns the last answer.
function Wait-KeyVault([string]$url, [int]$want) {
    $deadline = (Get-Date).AddSeconds(60)
    do {
        $result = Invoke-KeyVault GET $url $null
        if ($result.Status -eq $want) { return $result }
        Start-Sleep -Seconds 2
    } until ((Get-Date) -gt $deadline)
    $result
}

# A progress bar for a run's long steps: $done of $total, with the time left estimated
# from the pace so far ($started, when the step began). Write-Progress draws on the
# console only, never into the output, and PowerShell clears it when the script ends.
function Show-Progress([string]$activity, [int]$done, [int]$total, [string]$current, [datetime]$started) {
    $progress = @{ Id = 1; Activity = $activity; Status = "$done of $total$(if ($current) { ": $current" })" }
    if ($total -gt 0) {
        $progress.PercentComplete = [Math]::Min(100, [int](100 * $done / $total))
        if ($done -gt 0) { $progress.SecondsRemaining = [int](((Get-Date) - $started).TotalSeconds / $done * ($total - $done)) }
    }
    Write-Progress @progress
}

# A step whose length isn't known, such as deriving the keys, shown by name alone.
function Show-Step([string]$activity, [string]$status) { Write-Progress -Id 1 -Activity $activity -Status $status }

# Every secret in the vault: name -> enabled. Names compare without case, as
# Key Vault's do. Key Vault sends 25 a page and doesn't say how many there are, so the
# progress shown is the pages read so far.
function Get-VaultSecrets {
    $secrets = [ordered]@{}
    $url = "$vaultUrl/secrets?api-version=$ApiVersion&maxresults=25"
    $page = 0
    while ($url) {
        $page++
        Show-Step 'Listing the vault' "page $page, $($secrets.Count) secrets so far"
        $result = Invoke-KeyVault GET $url $null
        if ($result.Status -ne 200) { throw "Key Vault refused the list of secrets: HTTP $($result.Status) $(Get-ErrorCode $result)" }
        foreach ($item in @($result.Body.value)) {
            $secrets[($item.id -split '/')[-1]] = [bool]$item.attributes.enabled
        }
        $url = $result.Body.nextLink
    }
    Write-Progress -Id 1 -Activity 'Listing the vault' -Completed
    $secrets
}

# The export file: JSON holding the format, PBKDF2's iteration count and salt, AES-256-CBC's
# IV and ciphertext, and an HMAC-SHA256 over all of them (encrypt-then-MAC), so a wrong
# password or a changed byte is caught before anything is decrypted. The keys are 64 bytes
# of PBKDF2-SHA256 of the password: the first 32 encrypt, the last 32 authenticate. AES-GCM
# would be simpler, but Windows PowerShell's .NET doesn't have it.
$ExportFormat = 'homelab-keyvault-export'
$ExportVersion = 1
$ExportIterations = 600000

function Read-Password([string]$prompt) {
    $secure = Read-Host $prompt -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Get-ExportKeys([string]$password, [byte[]]$salt, [int]$iterations) {
    $kdf = [Security.Cryptography.Rfc2898DeriveBytes]::new([Text.Encoding]::UTF8.GetBytes($password), $salt, $iterations, [Security.Cryptography.HashAlgorithmName]::SHA256)
    try { $bytes = $kdf.GetBytes(64) } finally { $kdf.Dispose() }
    @{ Enc = [byte[]]$bytes[0..31]; Mac = [byte[]]$bytes[32..63] }
}

function Get-ExportMac([byte[]]$key, $file) {
    $hmac = [Security.Cryptography.HMACSHA256]::new($key)
    try {
        $signed = @($file.format, $file.version, $file.iterations, $file.salt, $file.iv, $file.ciphertext) -join '|'
        [Convert]::ToBase64String($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($signed)))
    }
    finally { $hmac.Dispose() }
}

function Protect-Export([string]$plain, [string]$password) {
    $salt = [byte[]]::new(16)
    $iv = [byte[]]::new(16)
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($salt); $rng.GetBytes($iv) } finally { $rng.Dispose() }
    $keys = Get-ExportKeys $password $salt $ExportIterations
    $bytes = [Text.Encoding]::UTF8.GetBytes($plain)
    $aes = [Security.Cryptography.Aes]::Create()
    try {
        $aes.Mode = 'CBC'; $aes.Padding = 'PKCS7'; $aes.Key = $keys.Enc; $aes.IV = $iv
        $encryptor = $aes.CreateEncryptor()
        try { $cipher = $encryptor.TransformFinalBlock($bytes, 0, $bytes.Length) } finally { $encryptor.Dispose() }
    }
    finally { $aes.Dispose(); [Array]::Clear($bytes, 0, $bytes.Length) }
    $file = [pscustomobject][ordered]@{
        format = $ExportFormat; version = $ExportVersion; iterations = $ExportIterations
        salt = [Convert]::ToBase64String($salt); iv = [Convert]::ToBase64String($iv)
        ciphertext = [Convert]::ToBase64String($cipher); mac = ''
    }
    $file.mac = Get-ExportMac $keys.Mac $file
    $file | ConvertTo-Json
}

# The decrypted text of an export file, once its MAC proves the password and the bytes.
function Unprotect-Export([string]$path, [string]$password) {
    try { $file = Get-Content -Raw -Encoding UTF8 -LiteralPath $path | ConvertFrom-Json }
    catch { throw "$path isn't an export file: it isn't JSON." }
    if ($file.format -ne $ExportFormat) { throw "$path isn't an export file (format '$($file.format)')." }
    if ($file.version -ne $ExportVersion) { throw "$path is export version $($file.version); this script reads version $ExportVersion." }
    if ([int]$file.iterations -lt 100000) { throw "$path asks for $($file.iterations) PBKDF2 iterations, too few to be one this script wrote." }
    $keys = Get-ExportKeys $password ([Convert]::FromBase64String($file.salt)) ([int]$file.iterations)
    $want = [Convert]::FromBase64String((Get-ExportMac $keys.Mac $file))
    $have = [Convert]::FromBase64String([string]$file.mac)
    # Compared in full, not stopping at the first difference.
    $diff = $want.Length -bxor $have.Length
    for ($i = 0; $i -lt [Math]::Min($want.Length, $have.Length); $i++) { $diff = $diff -bor ($want[$i] -bxor $have[$i]) }
    if ($diff -ne 0) { throw "Wrong password, or $path was changed or damaged: nothing read from it." }
    $cipher = [Convert]::FromBase64String($file.ciphertext)
    $aes = [Security.Cryptography.Aes]::Create()
    try {
        $aes.Mode = 'CBC'; $aes.Padding = 'PKCS7'; $aes.Key = $keys.Enc; $aes.IV = [Convert]::FromBase64String($file.iv)
        $decryptor = $aes.CreateDecryptor()
        try { $bytes = $decryptor.TransformFinalBlock($cipher, 0, $cipher.Length) } finally { $decryptor.Dispose() }
    }
    finally { $aes.Dispose() }
    try { [Text.Encoding]::UTF8.GetString($bytes) } finally { [Array]::Clear($bytes, 0, $bytes.Length) }
}

# A path as given, made absolute against the current folder.
function Resolve-ExportPath([string]$given) {
    [IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($given))
}

if ($Export) {
    $path = Resolve-ExportPath $Export
    # This repository is public: a file inside it is one `git add` from being published,
    # encrypted or not.
    $repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\', '/')
    foreach ($sep in [IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar) {
        if ($path.StartsWith("$repo$sep", [StringComparison]::OrdinalIgnoreCase)) { throw "$path is inside this repository, which is public: export outside it." }
    }
    if (Test-Path -LiteralPath $path) { throw "$path already exists: export to a new file." }
    $folder = Split-Path -Parent $path
    if (-not (Test-Path -LiteralPath $folder -PathType Container)) { throw "There's no folder $folder." }
    $vaultUrl = Get-VaultUrl
    $token = Get-AccessToken
    $password = Read-Password 'A password for the export (at least 12 characters)'
    if ($password.Length -lt 12) { throw 'The password is shorter than 12 characters; nothing exported.' }
    if ((Read-Password 'The same password again') -cne $password) { throw "The two passwords differ; nothing exported." }
    $inVault = Get-VaultSecrets
    $secrets = [Collections.Generic.List[object]]::new()
    $disabled = [Collections.Generic.List[string]]::new()
    $started = Get-Date
    $done = 0
    foreach ($secretName in $inVault.Keys) {
        Show-Progress 'Exporting: reading each secret' $done $inVault.Count $secretName $started
        $done++
        $read = Invoke-KeyVault GET (Get-SecretUrl $secretName) $null
        if ($read.Status -eq 200) {
            $secrets.Add([ordered]@{ name = $secretName; value = [string]$read.Body.value; contentType = $read.Body.contentType; tags = $read.Body.tags })
            continue
        }
        # A disabled secret's value can't be read (403): it's listed by name only.
        if (-not $inVault[$secretName] -and $read.Status -eq 403) { $disabled.Add($secretName); continue }
        throw "Key Vault refused to read $secretName`: HTTP $($read.Status) $(Get-ErrorCode $read), so the export would be incomplete; nothing written."
    }
    $plain = [ordered]@{ vault = $vaultUrl; exported = (Get-Date).ToUniversalTime().ToString('o'); secrets = $secrets.ToArray(); disabled = $disabled.ToArray() } |
        ConvertTo-Json -Depth 6 -Compress
    Show-Step 'Exporting' 'Encrypting the file (deriving the key from the password takes a few seconds)'
    [IO.File]::WriteAllText($path, (Protect-Export $plain $password), [Text.UTF8Encoding]::new($false))
    # Read back: the file as written decrypts with the password and holds every value read.
    Show-Step 'Exporting' 'Reading the file back with the password'
    $back = (Unprotect-Export $path $password) | ConvertFrom-Json
    Write-Progress -Id 1 -Activity 'Exporting' -Completed
    $wrote = @{}
    foreach ($s in @($back.secrets)) { $wrote[$s.name] = Get-Sha256 $s.value }
    $missing = @($secrets | Where-Object { $wrote[$_.name] -ne (Get-Sha256 $_.value) } | ForEach-Object { $_.name })
    if ($wrote.Count -ne $secrets.Count -or $missing) {
        throw "Read back, $path doesn't hold what was read ($($wrote.Count) of $($secrets.Count) secrets$(if ($missing) { "; different: $($missing -join ', ')" })): don't rely on it."
    }
    Write-Host "Exported $($secrets.Count) secrets from $vaultUrl to $path ($([Math]::Ceiling((Get-Item -LiteralPath $path).Length / 1KB)) KB), read back with the password."
    if ($disabled.Count) { Write-Warning "$($disabled.Count) disabled, so their values can't be read and aren't in it: $($disabled -join ', ')" }
    Write-Host 'Keep the file and its password apart, both off this machine; without the password the file is useless.'
    return
}

if ($Import) {
    $path = Resolve-ExportPath $Import
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "There's no file $path." }
    $vaultUrl = Get-VaultUrl
    $token = Get-AccessToken
    $password = Read-Password "The password for $path"
    Show-Step 'Importing' 'Decrypting the file (deriving the key from the password takes a few seconds)'
    $data = (Unprotect-Export $path $password) | ConvertFrom-Json
    $imported = @($data.secrets)
    Write-Host "$path holds $($imported.Count) secrets, exported from $($data.vault) at $($data.exported); into $vaultUrl."
    if (@($data.disabled).Count) { Write-Warning "$(@($data.disabled).Count) were disabled at export, so their values aren't in the file: $(@($data.disabled) -join ', ')" }
    # What each would do: add (absent here), same, differs, deleted (held as deleted here,
    # so it can't be added until recovered or purged), or unreadable (disabled here).
    $started = Get-Date
    $done = 0
    $plan = foreach ($s in $imported) {
        Show-Progress 'Importing: comparing with the vault' $done $imported.Count $s.name $started
        $done++
        $read = Invoke-KeyVault GET (Get-SecretUrl $s.name) $null
        $action = if ($read.Status -eq 200) { if ((Get-Sha256 $read.Body.value) -eq (Get-Sha256 $s.value)) { 'same' } else { 'differs' } }
                  elseif ($read.Status -eq 403) { 'unreadable' }
                  elseif ($read.Status -eq 404) {
                      $held = Invoke-KeyVault GET (Get-DeletedSecretUrl $s.name '') $null
                      if ($held.Status -eq 200) { 'deleted' } elseif ($held.Status -eq 404) { 'add' }
                      else { throw "Key Vault refused to read deleted $($s.name): HTTP $($held.Status) $(Get-ErrorCode $held); nothing written." }
                  }
                  else { throw "Key Vault refused to read $($s.name): HTTP $($read.Status) $(Get-ErrorCode $read); nothing written." }
        [pscustomobject]@{ Secret = $s; Action = $action }
    }
    $plan = @($plan)
    Write-Progress -Id 1 -Activity 'Importing' -Completed
    $count = { param($a) @($plan | Where-Object Action -eq $a).Count }
    Write-Host "$(& $count 'add') to add, $(& $count 'differs') different here, $(& $count 'same') the same, $(& $count 'deleted') held as deleted here, $(& $count 'unreadable') disabled here."
    foreach ($p in $plan | Where-Object Action -ne 'same') {
        $note = switch ($p.Action) {
            'add' { 'add' }
            'differs' { if ($Overwrite) { 'replace' } else { 'differs, kept (-Overwrite replaces it)' } }
            'deleted' { 'held as deleted here: -Recover it, or purge it, first' }
            'unreadable' { 'disabled here: left alone' }
        }
        Write-Host "  $($p.Secret.name): $note"
    }
    $writes = @($plan | Where-Object { $_.Action -eq 'add' -or ($_.Action -eq 'differs' -and $Overwrite) })
    if (-not $Apply) { Write-Host "Dry run: nothing written (-Apply writes the $($writes.Count))."; return }
    $failed = [Collections.Generic.List[string]]::new()
    $started = Get-Date
    $done = 0
    foreach ($p in $writes) {
        $s = $p.Secret
        Show-Progress 'Importing: writing and reading back' $done $writes.Count $s.name $started
        $done++
        $body = @{ value = [string]$s.value }
        if ($s.contentType) { $body.contentType = $s.contentType }
        if ($s.tags) { $body.tags = $s.tags }
        $put = Invoke-KeyVault PUT (Get-SecretUrl $s.name) $body
        if ($put.Status -ne 200) { $failed.Add("$($s.name) (HTTP $($put.Status) $(Get-ErrorCode $put))"); continue }
        $back = Invoke-KeyVault GET (Get-SecretUrl $s.name) $null
        if ($back.Status -ne 200 -or (Get-Sha256 $back.Body.value) -ne (Get-Sha256 $s.value)) { $failed.Add("$($s.name) (read back HTTP $($back.Status), or a different value)") }
    }
    Write-Progress -Id 1 -Activity 'Importing' -Completed
    Write-Host "Wrote $($writes.Count - $failed.Count) of $($writes.Count), each read back."
    if ($failed.Count) { throw "Not written or not read back: $($failed -join '; ')" }
    return
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
                           elseif (-not $files) { '' } elseif ($files.Values -contains 'data') { 'NEEDS b64enc' } else { 'plain' }
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

if ($Name -notmatch '^[A-Za-z0-9_-]+$') { throw "'$Name' isn't a variable or secret name (letters, digits, '_' and '-')." }
# A Key Vault name given with '-' is the same secret as its variable name with '_'.
$Name = $Name -replace '-', '_'
$secretName = if ($mapped.Contains($Name)) { $mapped[$Name] } else { $Name -replace '_', '-' }
$direct = @(Get-DirectReaders $secretName)
if ($secretName -notmatch '^[A-Za-z0-9-]{1,127}$') { throw "$secretName isn't a valid Key Vault name (letters, digits and '-', 1-127)." }

$files = $usage[$Name]

if ($Delete) {
    # Every reader the repo and the live ExternalSecret show; any one of them stops it,
    # since a deleted secret fails what reads it at its next sync or deploy.
    $readers = @(
        if ($mapped.Contains($Name)) { "the live cluster-secrets ExternalSecret (remove its entry in kubernetes/secrets/cluster-secrets.yaml, and let Flux apply that, first)" }
        $direct | ForEach-Object { "ExternalSecret in kubernetes/$_" }
        if ($files) { $files.Keys | Sort-Object | ForEach-Object { "kubernetes/$_ (`${$Name})" } }
        $dockhandReaders = (Get-DockhandUsage)[$secretName.ToLowerInvariant()]
        if ($dockhandReaders) { $dockhandReaders | Sort-Object | ForEach-Object { "$_ (docker/secret-names.yaml)" } }
    )
    if ($readers) { throw "$secretName is still read, so it isn't deleted:`n  $($readers -join "`n  ")" }
    $current = Invoke-KeyVault GET (Get-SecretUrl $secretName) $null
    if ($current.Status -eq 404) { throw "Key Vault has no secret named $secretName (a deleted one is brought back with -Recover)." }
    # A disabled secret's read is refused (403) but it can still be deleted.
    if ($current.Status -ne 200 -and $current.Status -ne 403) { throw "Key Vault refused to read $secretName`: HTTP $($current.Status) $(Get-ErrorCode $current)" }
    Write-Host "Nothing in this checkout or the live cluster-secrets reads $secretName."
    $typed = Read-Host "Type $secretName to delete it"
    if ($typed -cne $secretName) { throw 'Not the same name; nothing deleted.' }
    $deleted = Invoke-KeyVault DELETE (Get-SecretUrl $secretName) $null
    if ($deleted.Status -ne 200) { throw "Key Vault refused to delete $secretName`: HTTP $($deleted.Status) $(Get-ErrorCode $deleted)" }
    if (-not $deleted.Body.recoveryId) { throw "Key Vault deleted $secretName without keeping it to recover: the vault's soft delete is off, so it's gone." }
    $gone = Wait-KeyVault (Get-SecretUrl $secretName) 404
    $kept = Wait-KeyVault (Get-DeletedSecretUrl $secretName '') 200
    if ($gone.Status -ne 404 -or $kept.Status -ne 200) {
        throw "Read back, $secretName isn't yet both gone (HTTP $($gone.Status)) and held as deleted (HTTP $($kept.Status)): check it in Key Vault."
    }
    Write-Host "Deleted $secretName, read back. Recoverable with -Recover until $(Format-VaultTime $kept.Body.scheduledPurgeDate), when Key Vault purges it."
    return
}

if ($Recover) {
    $kept = Invoke-KeyVault GET (Get-DeletedSecretUrl $secretName '') $null
    if ($kept.Status -eq 404) { throw "Key Vault holds no deleted secret named $secretName (already purged, recovered, or never deleted)." }
    if ($kept.Status -ne 200) { throw "Key Vault refused to read deleted $secretName`: HTTP $($kept.Status) $(Get-ErrorCode $kept)" }
    $recovered = Invoke-KeyVault POST (Get-DeletedSecretUrl $secretName '/recover') $null
    if ($recovered.Status -ne 200) { throw "Key Vault refused to recover $secretName`: HTTP $($recovered.Status) $(Get-ErrorCode $recovered)" }
    $back = Wait-KeyVault (Get-SecretUrl $secretName) 200
    if ($back.Status -ne 200) { throw "Read back, $secretName isn't readable yet (HTTP $($back.Status)): check it in Key Vault." }
    Write-Host "Recovered $secretName (deleted $(Format-VaultTime $kept.Body.deletedDate)), with the value it had, read back."
    if ($mapped.Contains($Name) -and -not $NoSync) {
        kubectl annotate externalsecret cluster-secrets -n flux-system "force-sync=$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())" --overwrite | Out-Null
        Write-Host 'Force-synced cluster-secrets; check it with: kubectl get externalsecret cluster-secrets -n flux-system'
    }
    elseif ($direct) { Write-Host "Read straight from Key Vault by: $($direct -join ', '); they pick it up within their refreshInterval." }
    return
}

$inSecret = $files -and ($files.Values -contains 'data')
$plainUses = @(if ($files) { $files.Keys | Where-Object { $files[$_] -ne 'data' } | Sort-Object })
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
    $edge = Get-EdgeWhitespace ([string]$current.Body.value)
    if ($edge) { Write-Warning "$secretName $edge, which an app reading it usually rejects; set it again without." }
    if ($Clipboard) {
        Set-Clipboard -Value ([string]$current.Body.value)
        Write-Host "Copied $secretName to the clipboard."
        return
    }
    return [string]$current.Body.value
}

if ($direct) { Write-Host "Read straight from Key Vault by: $($direct -join ', ')" }
elseif (-not $files) { Write-Warning "Nothing under kubernetes/ uses `${$Name}` in this checkout, and no ExternalSecret reads $secretName." }

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

if ($Generate) {
    $Value = New-RandomString $Generate
}
elseif (-not $PSBoundParameters.ContainsKey('Value')) {
    $secure = Read-Host "New value for $Name" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $Value = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
if ($Value -eq '') { throw 'Empty value; nothing changed.' }
$edge = Get-EdgeWhitespace $Value
if ($edge) { Write-Warning "The value $edge; it's set exactly as given. If that wasn't meant, set it again without." }

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
if ($Clipboard) {
    Set-Clipboard -Value $Value
    Write-Host "Copied $secretName to the clipboard."
}

if (-not $mapped.Contains($Name) -and $direct) {
    Write-Host 'Not a cluster-secrets variable: those ExternalSecrets pick it up within their refreshInterval, or annotate one with force-sync.'
    return
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
    $secretFiles = @($files.Keys | Where-Object { $files[$_] } | Sort-Object)
    if ($secretFiles) {
        Write-Host "`nThen restart what reads the Secrets it's in (a pod only gets a Secret's new value when it restarts):"
        $secretFiles | ForEach-Object { Get-RestartCommands $_ } | Sort-Object -Unique | ForEach-Object { Write-Host $_ }
    }
}
