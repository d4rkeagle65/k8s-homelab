<#
.SYNOPSIS
Lists or updates the custom fields of the Vaultwarden item that holds the
cluster-secrets variables, through the bitwarden-cli pod.

.DESCRIPTION
With no -Name, lists every field name (never the values), with:
  Encoding  base64 when the variable is used in a Secret's `data:` (Flux
            substitutes it as is, so the field must hold the base64 form),
            else plain.
  Synced    whether the cluster-secrets ExternalSecret maps it. A field it
            doesn't map never reaches the cluster.
  UsedBy    the Flux Kustomizations whose files use the variable.
Names the ExternalSecret maps but the item lacks are listed too.

With -Name, sets that field. The value is asked for without echoing it
(or taken from -Value, which leaves it in your shell history), base64-encoded
when the variable is used in a Secret, then written to the item. The script
then force-syncs the ExternalSecret, waits for the cluster-secrets Secret to
hold the new value, and prints the Flux reconcile commands to run.

The item ID is read from the live ExternalSecret, and the variable usage from
the kubernetes/ folder of this checkout. Run `git pull` first.

.EXAMPLE
./scripts/vaultwarden-fields.ps1
Lists the fields.

.EXAMPLE
./scripts/vaultwarden-fields.ps1 IMMICH_POWER_TOOLS_APIKEY_IMMICH_APIKEY
Asks for the new value and updates the field.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Name,
    # The new value. Leave it out to be prompted without echo.
    [string]$Value,
    # Store the value exactly as given, even for a variable used in a Secret.
    [switch]$Raw,
    # Allow adding a field that doesn't exist yet. It also needs an entry in
    # kubernetes/secrets/cluster-secrets.yaml before it reaches the cluster.
    [switch]$New,
    # Don't force-sync the ExternalSecret afterwards.
    [switch]$NoSync
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Text.UTF8Encoding]::new($false)

# Runs in the bitwarden-cli pod. Reads a JSON request on stdin and prints one
# JSON line. Field values are never printed. Single quotes only, so the
# argument reaches kubectl intact from Windows PowerShell and PowerShell 7.
$node = "let s='';process.stdin.on('data',d=>s+=d).on('end',async()=>{try{" +
    "const q=JSON.parse(s);const b='http://127.0.0.1:8087';const u=b+'/object/item/'+q.id;" +
    "await fetch(b+'/sync?force=true',{method:'POST'});" +
    "const it=(await (await fetch(u)).json()).data;if(!it){throw new Error('item '+q.id+' not found')}" +
    "const all=it.fields||[];" +
    "if(q.mode==='list'){console.log(JSON.stringify({names:all.filter(x=>x.name).map(x=>x.name)}));return}" +
    "const old=all.find(x=>x.name===q.name);" +
    "if(!old&&!q.create){throw new Error('the item has no field named '+q.name+' (use -New to add it)')}" +
    "if(old){old.value=q.value}else{all.push({name:q.name,value:q.value,type:1,linkedId:null})}" +
    "it.fields=all;" +
    "const r=await (await fetch(u,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(it)})).json();" +
    "if(!r.success){throw new Error('update failed: '+r.message)}" +
    "console.log(JSON.stringify({count:r.data.fields.length,created:!old}))" +
    "}catch(e){console.log(JSON.stringify({error:String(e.message||e)}))}})"

function Invoke-BitwardenCli($request) {
    $out = $request | ConvertTo-Json -Compress |
        kubectl exec -i -n external-secrets deploy/bitwarden-cli -c bitwarden-cli -- node -e $node
    if ($LASTEXITCODE -ne 0) { throw 'kubectl exec into the bitwarden-cli pod failed.' }
    $result = ($out | Select-Object -Last 1) | ConvertFrom-Json
    if ($result.error) { throw $result.error }
    $result
}

# Variable name -> the files under kubernetes/ that use it, and whether each
# is a Secret. `$${X}` is an escape Flux leaves alone, so it doesn't count.
function Get-VariableUsage {
    $root = (Resolve-Path (Join-Path $PSScriptRoot '..\kubernetes')).Path
    $usage = @{}
    foreach ($file in Get-ChildItem $root -Recurse -File -Include *.yaml, *.yml) {
        $text = Get-Content -Raw $file.FullName
        if (-not $text) { continue }
        $isSecret = $text -match '(?m)^kind:\s*Secret\s*$'
        $rel = $file.FullName.Substring($root.Length + 1) -replace '\\', '/'
        foreach ($m in [regex]::Matches($text, '(?<!\$)\$\{([A-Za-z0-9_]+)\}')) {
            $var = $m.Groups[1].Value
            if (-not $usage[$var]) { $usage[$var] = @{} }
            $usage[$var][$rel] = $isSecret
        }
    }
    $usage
}

# The Flux Kustomization that applies a file, from its path under kubernetes/.
function Get-KustomizationName([string]$rel) {
    $parts = $rel -split '/'
    switch ($parts[0]) {
        'apps' { if ($parts.Count -gt 3) { return $parts[2] } }
        'cluster' { return 'cluster-resources' }
        'flux' { if ($parts[1] -eq 'meta') { return 'cluster-meta' } }
        'secrets' { return 'cluster-secrets' }
        'shared' { return 'cluster-shared' }
        'test' { return 'cluster-test' }
    }
    "(unknown: $rel)"
}

$es = kubectl get externalsecret cluster-secrets -n flux-system -o json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Could not read the cluster-secrets ExternalSecret.' }
$itemId = @($es.spec.data.remoteRef.key | Select-Object -Unique)
if ($itemId.Count -ne 1) { throw "Expected one Vaultwarden item in the ExternalSecret, found: $($itemId -join ', ')" }
$itemId = $itemId[0]
$mapped = @($es.spec.data | Where-Object { $_.remoteRef.property -eq $_.secretKey } | ForEach-Object secretKey)
$usage = Get-VariableUsage

if (-not $Name) {
    $names = (Invoke-BitwardenCli @{ mode = 'list'; id = $itemId }).names
    $rows = foreach ($n in @($names) + @($mapped | Where-Object { $_ -notin $names })) {
        $files = $usage[$n]
        [pscustomobject]@{
            Name     = $n
            Encoding = if (-not $files) { '' } elseif ($files.Values -contains $true) { 'base64' } else { 'plain' }
            Synced   = if ($n -notin $names) { 'MISSING IN VAULTWARDEN' } elseif ($n -in $mapped) { 'yes' } else { 'no' }
            UsedBy   = if ($files) { ($files.Keys | ForEach-Object { Get-KustomizationName $_ } | Sort-Object -Unique) -join ', ' } else { '(unused)' }
        }
    }
    $rows | Sort-Object Name | Format-Table -AutoSize | Out-String -Width 4096 | Write-Host
    return
}

$files = $usage[$Name]
$inSecret = $files -and ($files.Values -contains $true)
if ($files -and $inSecret -and ($files.Values -contains $false) -and -not $Raw) {
    throw "$Name is used both in a Secret's data (base64) and elsewhere (plain), so no one value fits both. Files: $($files.Keys -join ', ')"
}
if (-not $files) { Write-Warning "Nothing under kubernetes/ uses `${$Name}` in this checkout." }

if (-not $PSBoundParameters.ContainsKey('Value')) {
    $secure = Read-Host "New value for $Name" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $Value = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
if ($Value -eq '') { throw 'Empty value; nothing changed.' }
$stored = if ($inSecret -and -not $Raw) { [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Value)) } else { $Value }

$result = Invoke-BitwardenCli @{ mode = 'set'; id = $itemId; name = $Name; value = $stored; create = [bool]$New }
$how = if ($stored -ne $Value) { ' (base64-encoded)' } else { '' }
Write-Host "$(if ($result.created) { 'Added' } else { 'Updated' }) $Name$how. The item has $($result.count) fields."

if ($Name -notin $mapped) {
    Write-Warning "The ExternalSecret doesn't map $Name yet. Add it to kubernetes/secrets/cluster-secrets.yaml and push that before anything uses it."
    return
}
if (-not $NoSync) {
    kubectl annotate externalsecret cluster-secrets -n flux-system "force-sync=$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())" --overwrite | Out-Null
    $want = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($stored))
    $deadline = (Get-Date).AddSeconds(60)
    do {
        Start-Sleep -Seconds 2
        $have = kubectl get secret cluster-secrets -n flux-system -o "jsonpath={.data.$Name}"
    } until ($have -eq $want -or (Get-Date) -gt $deadline)
    if ($have -ne $want) { throw 'The cluster-secrets Secret still has the old value after 60s. Check: kubectl describe externalsecret cluster-secrets -n flux-system' }
    Write-Host 'The cluster-secrets Secret has the new value.'
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
