<#
.SYNOPSIS
Moves a CloudNativePG database onto the local-db StorageClass, one instance at
a time, with a single brief switchover.

.DESCRIPTION
The procedure used for whisparr on 2026-09-29 (notes in the Done section of
`git show ed9e8a8:QUEUE.md`):
  1. Suspend the database's Flux Kustomization.
  2. Point the Cluster's spec.storage.storageClass at local-db. Only new
     instances use it; running ones are untouched.
  3. Replace each replica that isn't on local-db: delete its PVC and pod,
     and CNPG rebuilds it on local-db from the primary.
  4. Once a local-db replica is streaming and caught up, switch over to it
     (the only moment the app loses its database: a few seconds).
  5. Replace the old primary the same way.
  6. Set storageClass in the git manifest and check `flux diff` is clean.
A single-instance database is scaled to 2 first and back to 1 at the end.

Every step checks the live state first, so the script is safe to re-run: if
it stops (a timeout, Ctrl+C), fix the cause and run the same command again.
It never touches git history: commit, push and `flux resume` are left to you,
and it prints those commands at the end. Old NFS volumes are never deleted.

.EXAMPLE
./scripts/migrate-cnpg-to-local-db.ps1 -Namespace media -Cluster prowlarr-postgres -Kustomization prowlarr -GitFile kubernetes/apps/media/prowlarr/app/postgres.yaml -PlanOnly
Shows the current state and what would happen, changing nothing.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Namespace,
    [Parameter(Mandatory)][string]$Cluster,
    [Parameter(Mandatory)][string]$Kustomization,
    [Parameter(Mandatory)][string]$GitFile,
    [string]$StorageClass = 'local-db',
    [int]$TimeoutMinutes = 30,
    # Show the state and the plan, then stop without changing anything.
    [switch]$PlanOnly,
    # Don't ask before starting or before the switchover.
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$OrigInstancesAnnotation = 'homelab.local/pre-migration-instances'
$ClusterRes = 'cluster.postgresql.cnpg.io'
$MaxLagBytes = 16MB

function Say([string]$Message) { Write-Host "$(Get-Date -Format HH:mm:ss)  $Message" }

function Get-Json([string[]]$KubectlArgs) {
    & kubectl @KubectlArgs -o json | ConvertFrom-Json
}

function Get-State {
    $c = Get-Json @('get', $ClusterRes, $Cluster, '-n', $Namespace)
    $pvcs = (Get-Json @('get', 'pvc', '-n', $Namespace, '-l', "cnpg.io/cluster=$Cluster")).items
    $sc = @{}
    foreach ($p in $pvcs) { $sc[$p.metadata.name] = $p.spec.storageClassName }
    $names = @($c.status.instanceNames)
    [pscustomobject]@{
        Instances       = [int]$c.spec.instances
        Ready           = [int]$c.status.readyInstances
        Phase           = $c.status.phase
        Primary         = $c.status.currentPrimary
        Names           = $names
        StorageClass    = $c.spec.storage.storageClass
        PvcClass        = $sc
        NonLocal        = @($names | Where-Object { $sc[$_] -ne $StorageClass })
        Local           = @($names | Where-Object { $sc[$_] -eq $StorageClass })
        OrigInstances   = $c.metadata.annotations.$OrigInstancesAnnotation
        Healthy         = ($c.status.phase -eq 'Cluster in healthy state' -and
                           [int]$c.status.readyInstances -eq [int]$c.spec.instances -and
                           $names.Count -eq [int]$c.spec.instances)
    }
}

function Get-Replication([string]$Primary) {
    $sql = "select application_name || '|' || state || '|' || coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn), -1) from pg_stat_replication"
    $rows = & kubectl exec -n $Namespace $Primary -c postgres -- psql -U postgres -Atc $sql
    foreach ($r in @($rows)) {
        if (-not $r) { continue }
        $f = $r -split '\|'
        [pscustomobject]@{ Name = $f[0]; State = $f[1]; Behind = [double]$f[2] }
    }
}

# Every replica listed by the primary as streaming and caught up.
function Test-ReplicasCaughtUp($s) {
    $rep = @(Get-Replication $s.Primary)
    foreach ($n in $s.Names | Where-Object { $_ -ne $s.Primary }) {
        $r = $rep | Where-Object Name -eq $n
        if (-not $r -or $r.State -ne 'streaming' -or $r.Behind -lt 0 -or $r.Behind -gt $MaxLagBytes) { return $false }
    }
    return $true
}

function Wait-For([string]$What, [scriptblock]$Condition) {
    Say "waiting: $What"
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    $lastNote = Get-Date
    while ($true) {
        $s = Get-State
        if (& $Condition $s) { Say "done: $What"; return $s }
        if ((Get-Date) -gt $deadline) {
            throw "Timed out after $TimeoutMinutes min waiting for: $What. Flux stays suspended for '$Kustomization'. Check 'kubectl get $ClusterRes $Cluster -n $Namespace' and the pods, then re-run this command."
        }
        if (((Get-Date) - $lastNote).TotalSeconds -ge 30) {
            Say "  still waiting ($($s.Ready)/$($s.Instances) ready, $($s.Phase))"
            $lastNote = Get-Date
        }
        Start-Sleep -Seconds 5
    }
}

function Wait-Healthy([string]$What) {
    Wait-For $What { param($s) $s.Healthy -and (Test-ReplicasCaughtUp $s) }
}

function Invoke-PatchFile([string[]]$KubectlArgs, $Patch) {
    $file = New-TemporaryFile
    try {
        Set-Content -Path $file -Value ($Patch | ConvertTo-Json -Compress -Depth 5) -NoNewline
        & kubectl @KubectlArgs --type merge --patch-file $file.FullName | Out-Null
    } finally {
        Remove-Item -LiteralPath $file.FullName -Force
    }
}

function Set-Instances([int]$Count) {
    Invoke-PatchFile @('patch', $ClusterRes, $Cluster, '-n', $Namespace) @{ spec = @{ instances = $Count } }
}

function Show-State($s) {
    Say "$Namespace/$Cluster  $($s.Phase)  $($s.Ready)/$($s.Instances) ready  primary: $($s.Primary)  spec.storageClass: $($s.StorageClass)"
    foreach ($n in $s.Names) {
        $role = if ($n -eq $s.Primary) { 'primary' } else { 'replica' }
        Say ("  {0,-28} {1,-8} on {2}" -f $n, $role, $s.PvcClass[$n])
    }
}

# ---- preflight ------------------------------------------------------------------

if (-not (Test-Path $GitFile)) { throw "GitFile not found: $GitFile (run from the repo root)" }
& kubectl get storageclass $StorageClass -o name | Out-Null
$s = Get-State
Show-State $s

$gitText = [IO.File]::ReadAllText((Resolve-Path $GitFile))
$gitMatches = [regex]::Matches($gitText, '(?m)^(\s*storageClass:[ \t]*)(\S+)[ \t]*$')
$single = $s.Instances -eq 1 -and -not $s.OrigInstances

Say "plan:"
if (-not $s.NonLocal) {
    Say "  all instances are already on ${StorageClass}: only the git check remains"
} else {
    Say "  suspend Flux Kustomization '$Kustomization'; set spec.storage.storageClass: $StorageClass"
    if ($single) { Say "  single instance: scale to 2, switch over to the new one, scale back to 1" }
    else { Say "  replace each replica on local disk, switch over once one is caught up, then replace the old primary" }
}
Say "  git: $GitFile storageClass: $(if ($gitMatches.Count) { $gitMatches[0].Groups[2].Value } else { '?' }) -> $StorageClass"
if ($gitMatches.Count -ne 1) { Say "  WARNING: expected exactly one 'storageClass:' line in $GitFile, found $($gitMatches.Count); the git edit will be skipped" }

if ($PlanOnly) { Say "PlanOnly: nothing changed."; return }
if (-not $s.Healthy) { throw "The database isn't healthy ($($s.Ready)/$($s.Instances), $($s.Phase)). Not starting." }
if (-not $Yes -and (Read-Host "Proceed? (y/N)") -ne 'y') { Say "Stopped: nothing changed."; return }

# ---- migrate ------------------------------------------------------------------------

if ($s.NonLocal) {
    & flux suspend kustomization $Kustomization | Out-Null
    Say "Flux Kustomization '$Kustomization' suspended"

    if (-not $s.OrigInstances) {
        Invoke-PatchFile @('patch', $ClusterRes, $Cluster, '-n', $Namespace) @{ metadata = @{ annotations = @{ $OrigInstancesAnnotation = "$($s.Instances)" } } }
    }
    if ($s.StorageClass -ne $StorageClass) {
        Invoke-PatchFile @('patch', $ClusterRes, $Cluster, '-n', $Namespace) @{ spec = @{ storage = @{ storageClass = $StorageClass } } }
        Say "spec.storage.storageClass -> $StorageClass (new instances only)"
    }
}

while ($true) {
    $s = Wait-Healthy 'database healthy, every replica streaming and caught up'
    $orig = if ($s.OrigInstances) { [int]$s.OrigInstances } else { $s.Instances }
    if (-not $s.NonLocal) { break }

    $nonLocalReplicas = @($s.NonLocal | Where-Object { $_ -ne $s.Primary })
    $primaryIsLocal = $s.Local -contains $s.Primary

    if ($s.Instances -eq 1) {
        Say "single instance: scaling to 2 so a copy can be built on $StorageClass"
        Set-Instances 2
        Wait-For 'second instance created' { param($x) $x.Names.Count -eq 2 } | Out-Null
        continue
    }
    if ($primaryIsLocal -and $orig -lt $s.Instances -and $nonLocalReplicas.Count -eq ($s.Instances - $orig)) {
        Say "scaling back to ${orig}: CNPG removes the old replica ($($nonLocalReplicas -join ', '))"
        Set-Instances $orig
        Wait-For "back to $orig instance(s)" { param($x) $x.Names.Count -eq $orig -and -not $x.NonLocal } | Out-Null
        continue
    }
    if ($nonLocalReplicas) {
        $old = $nonLocalReplicas[0]
        Say "replacing replica $old (on $($s.PvcClass[$old])): CNPG rebuilds it on $StorageClass from the primary"
        & kubectl delete pvc $old -n $Namespace --wait=false | Out-Null
        & kubectl delete pod $old -n $Namespace --wait=true --timeout=180s | Out-Null
        Wait-For "$old replaced" { param($x) -not ($x.Names -contains $old) -and $x.Names.Count -eq $x.Instances } | Out-Null
        continue
    }
    # Only the primary is left on the old storage: switch over to a local replica.
    $target = @($s.Local | Where-Object { $_ -ne $s.Primary })[0]
    if (-not $Yes -and (Read-Host "Switch over $($s.Primary) -> $target now? The app loses its database for a few seconds. (y/N)") -ne 'y') {
        throw "Stopped before the switchover. Flux stays suspended for '$Kustomization'; re-run to continue."
    }
    Say "switching over: $($s.Primary) -> $target"
    Invoke-PatchFile @('patch', $ClusterRes, $Cluster, '-n', $Namespace, '--subresource=status') @{ status = @{
        targetPrimary          = $target
        targetPrimaryTimestamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.ffffffZ')
        phase                  = 'Switchover in progress'
        phaseReason            = "Switching over to $target"
    } }
    Wait-For "$target is primary" { param($x) $x.Primary -eq $target -and $x.Healthy } | Out-Null
}

$s = Get-State
if ($s.OrigInstances) {
    & kubectl annotate $ClusterRes $Cluster -n $Namespace "$OrigInstancesAnnotation-" | Out-Null
}
Show-State $s

# ---- git ------------------------------------------------------------------------------

if ($gitMatches.Count -eq 1 -and $gitMatches[0].Groups[2].Value -ne $StorageClass) {
    $newText = [regex]::Replace($gitText, '(?m)^(\s*storageClass:[ \t]*)(\S+)([ \t]*)$', "`${1}$StorageClass`${3}")
    [IO.File]::WriteAllText((Resolve-Path $GitFile), $newText)
    Say "git: $GitFile now says storageClass: $StorageClass"
}
$appDir = (Split-Path -Parent $GitFile) -replace '\\', '/'
$PSNativeCommandUseErrorActionPreference = $false
& flux diff kustomization $Kustomization --path "./$appDir" --progress-bar=false | Out-Host
$diffExit = $LASTEXITCODE
$PSNativeCommandUseErrorActionPreference = $true
if ($diffExit -eq 0) { Say "flux diff: git matches the live cluster" }
else { Say "flux diff reported differences (exit $diffExit): review them before pushing" }

$leftover = @((Get-Json @('get', 'pvc', '-n', $Namespace, '-l', "cnpg.io/cluster=$Cluster")).items |
    Where-Object { $_.spec.storageClassName -ne $StorageClass } | ForEach-Object { $_.metadata.name })
if ($leftover) { Say "note: PVCs still on old storage (not in use by an instance): $($leftover -join ', ')" }

Write-Host ""
Say "Done. Next, from the repo root:"
Write-Host "    git add $GitFile"
Write-Host "    git commit -m `"Move $Cluster to $StorageClass`""
Write-Host "    git push"
Write-Host "    flux resume kustomization $Kustomization"
Say "The old NFS volumes are Released/Retain; delete them when you no longer want them as a fallback."
