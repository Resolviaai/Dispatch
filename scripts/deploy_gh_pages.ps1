# Dispatch GitHub Pages Deployment Script
# Atomically synchronizes root and site/ directories on gh-pages branch
# with candidate release artifacts (Dispatch.apk, update.json, landing page).
#
# Safety gates:
#   1. The APK's SHA-256 and byte size MUST match release-out/update.json.
#   2. origin is fetched before the local gh-pages branch is touched; a diverged
#      (non-fast-forward) local branch aborts the deploy instead of force-pushing.
#
# Usage:
#   powershell -File scripts\deploy_gh_pages.ps1 -Tag v1.2.14-alpha

param(
    [string]$Tag = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

$apkSource = Join-Path $root "release-out\Dispatch.apk"
$updateSource = Join-Path $root "release-out\update.json"
$siteDir = Join-Path $root "site"

if (-not (Test-Path $apkSource)) { throw "FATAL: release-out\Dispatch.apk not found. Run scripts\prepare_release.ps1 first." }
if (-not (Test-Path $updateSource)) { throw "FATAL: release-out\update.json not found. Run scripts\prepare_release.ps1 first." }

# Read version from update.json
$updateJson = Get-Content -Path $updateSource -Raw -Encoding UTF8 | ConvertFrom-Json
$versionName = $updateJson.versionName
if (-not $versionName) { throw "FATAL: release-out\update.json has no versionName. Regenerate it with scripts\prepare_release.ps1." }
if ($Tag -eq "") {
    $Tag = "$($updateJson.releaseTag)"
    if (-not $Tag) { $Tag = "v$versionName-alpha" }
} elseif ($updateJson.releaseTag -and $Tag -ne $updateJson.releaseTag) {
    Write-Warning "Requested tag '$Tag' does not match update.json releaseTag '$($updateJson.releaseTag)'."
}

Write-Host "=================================================="
Write-Host " Deploying to gh-pages branch"
Write-Host " Version: $versionName"
Write-Host " Tag:     $Tag"
Write-Host "=================================================="

# --- artifact integrity gate (nothing is copied or pushed until this passes) ---
Write-Host "== 0. Verifying artifacts against release-out\update.json =="
$manifestHash = "$($updateJson.apkSha256)".Trim().ToLowerInvariant()
if ($manifestHash -notmatch '^[0-9a-f]{64}$') {
    throw "FATAL: release-out\update.json apkSha256 is missing or malformed ('$($updateJson.apkSha256)')."
}
$manifestSize = 0L
try { $manifestSize = [long]$updateJson.apkSizeBytes } catch { $manifestSize = 0L }
if ($manifestSize -le 0) {
    throw "FATAL: release-out\update.json apkSizeBytes is missing or invalid ('$($updateJson.apkSizeBytes)')."
}
$apkHash = (Get-FileHash $apkSource -Algorithm SHA256).Hash.ToLowerInvariant()
$apkSize = (Get-Item $apkSource).Length
if ($apkHash -ne $manifestHash) {
    throw "FATAL: release-out\Dispatch.apk SHA-256 $apkHash does not match update.json apkSha256 $manifestHash. Re-run scripts\prepare_release.ps1."
}
if ($apkSize -ne $manifestSize) {
    throw "FATAL: release-out\Dispatch.apk size $apkSize bytes does not match update.json apkSizeBytes $manifestSize. Re-run scripts\prepare_release.ps1."
}
Write-Host "   apk sha256=$apkHash size=$apkSize (matches manifest)"

$tempWorktree = Join-Path $root ".temp-gh-pages"
$originRef = "refs/remotes/origin/gh-pages"
$localRef = "refs/heads/gh-pages"

# Clean up any leftover worktree from a previous interrupted run
if (Test-Path $tempWorktree) {
    try { git worktree remove $tempWorktree --force 2>$null } catch { }
    Remove-Item $tempWorktree -Recurse -Force -ErrorAction SilentlyContinue
}
git worktree prune

Write-Host "== 1. Fetching origin (before touching the local gh-pages branch) =="
git fetch origin --prune
if ($LASTEXITCODE -ne 0) { throw "FATAL: git fetch origin failed. Deploy aborted; nothing was published." }

$hasOrigin = $false
git show-ref --verify --quiet $originRef
if ($LASTEXITCODE -eq 0) { $hasOrigin = $true }
$hasLocal = $false
git show-ref --verify --quiet $localRef
if ($LASTEXITCODE -eq 0) { $hasLocal = $true }

if ($hasOrigin) {
    $originSha = (git rev-parse $originRef).Trim()
    if ($hasLocal) {
        $localSha = (git rev-parse $localRef).Trim()
        if ($localSha -ne $originSha) {
            # Local may only move forward: it must be an ancestor of origin/gh-pages.
            git merge-base --is-ancestor $localRef $originRef
            if ($LASTEXITCODE -ne 0) {
                throw "FATAL: local gh-pages ($localSha) has diverged from origin/gh-pages ($originSha) - pushing would be a non-fast-forward. Reconcile the branches manually and re-run."
            }
            git update-ref $localRef $originSha $localSha
            if ($LASTEXITCODE -ne 0) { throw "FATAL: failed to fast-forward local gh-pages to origin/gh-pages." }
            Write-Host "   Fast-forwarded local gh-pages $localSha -> $originSha"
        }
    } else {
        git branch --track gh-pages $originRef
        if ($LASTEXITCODE -ne 0) { throw "FATAL: failed to create local gh-pages from origin/gh-pages." }
        Write-Host "   Created local gh-pages from origin/gh-pages"
    }
} elseif ($hasLocal) {
    Write-Warning "origin/gh-pages does not exist yet; deploying the existing local gh-pages branch."
} else {
    throw "FATAL: no gh-pages branch found locally or on origin. Create it first (git checkout --orphan gh-pages)."
}

try {
    Write-Host "== 2. Creating temporary git worktree for gh-pages =="
    git worktree add $tempWorktree gh-pages
    if ($LASTEXITCODE -ne 0) { throw "FATAL: Failed to add git worktree for gh-pages" }

    Write-Host "== 3. Mirroring landing page and artifacts to root AND site/ =="
    # Ensure site/ exists inside worktree
    $wtSiteDir = Join-Path $tempWorktree "site"
    New-Item -ItemType Directory -Force -Path $wtSiteDir | Out-Null

    # Copy APK to root and site/
    Copy-Item $apkSource (Join-Path $tempWorktree "Dispatch.apk") -Force
    Copy-Item $apkSource (Join-Path $wtSiteDir "Dispatch.apk") -Force

    # Copy update.json to root and site/
    Copy-Item $updateSource (Join-Path $tempWorktree "update.json") -Force
    Copy-Item $updateSource (Join-Path $wtSiteDir "update.json") -Force

    # Copy site/ files to both root and site/
    Copy-Item (Join-Path $siteDir "index.html") (Join-Path $tempWorktree "index.html") -Force
    Copy-Item (Join-Path $siteDir "index.html") (Join-Path $wtSiteDir "index.html") -Force

    if (Test-Path (Join-Path $siteDir "admin.html")) {
        Copy-Item (Join-Path $siteDir "admin.html") (Join-Path $tempWorktree "admin.html") -Force
        Copy-Item (Join-Path $siteDir "admin.html") (Join-Path $wtSiteDir "admin.html") -Force
    }

    if (Test-Path (Join-Path $siteDir "favicon.svg")) {
        Copy-Item (Join-Path $siteDir "favicon.svg") (Join-Path $tempWorktree "favicon.svg") -Force
        Copy-Item (Join-Path $siteDir "favicon.svg") (Join-Path $wtSiteDir "favicon.svg") -Force
    }

    if (Test-Path (Join-Path $siteDir "privacy.html")) {
        Copy-Item (Join-Path $siteDir "privacy.html") (Join-Path $tempWorktree "privacy.html") -Force
        Copy-Item (Join-Path $siteDir "privacy.html") (Join-Path $wtSiteDir "privacy.html") -Force
    }

    if (Test-Path (Join-Path $siteDir "terms.html")) {
        Copy-Item (Join-Path $siteDir "terms.html") (Join-Path $tempWorktree "terms.html") -Force
        Copy-Item (Join-Path $siteDir "terms.html") (Join-Path $wtSiteDir "terms.html") -Force
    }

    Write-Host "== 4. Committing and pushing gh-pages =="
    Push-Location $tempWorktree
    try {
        git add -A
        $status = git status --porcelain
        if ($status) {
            git commit -m "deploy: update landing page and direct APK mirror for $Tag"
            if ($LASTEXITCODE -ne 0) { throw "FATAL: git commit failed in the gh-pages worktree (check user.name/user.email). Nothing was pushed." }
            git push origin gh-pages
            if ($LASTEXITCODE -ne 0) {
                throw "FATAL: git push origin gh-pages was rejected (non-fast-forward or remote moved). Nothing was published; re-run after reconciling origin/gh-pages."
            }
            Write-Host "[x] Pushed updated landing page and APK to origin/gh-pages"
        } else {
            Write-Host "gh-pages is already up to date; nothing to commit."
        }
    } finally {
        Pop-Location
    }
} finally {
    Write-Host "== 5. Cleaning up temporary worktree =="
    try { git worktree remove $tempWorktree --force 2>$null } catch { }
    Remove-Item $tempWorktree -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "=================================================="
Write-Host " gh-pages deployment complete!"
Write-Host " Landing: https://resolviaai.github.io/Dispatch/"
Write-Host " Direct:  https://resolviaai.github.io/Dispatch/Dispatch.apk"
Write-Host " Manifest: https://resolviaai.github.io/Dispatch/update.json"
Write-Host "=================================================="
