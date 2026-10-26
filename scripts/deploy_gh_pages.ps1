# Dispatch GitHub Pages Deployment Script
# Atomically synchronizes root and site/ directories on gh-pages branch
# with candidate release artifacts (Dispatch.apk, update.json, landing page).
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
$updateJson = Get-Content $updateSource -Raw | ConvertFrom-Json
$versionName = $updateJson.versionName
if ($Tag -eq "") { $Tag = "v$versionName-alpha" }

Write-Host "=================================================="
Write-Host " Deploying to gh-pages branch"
Write-Host " Version: $versionName"
Write-Host " Tag:     $Tag"
Write-Host "=================================================="

$tempWorktree = Join-Path $root ".temp-gh-pages"

# Clean up any leftover worktree from a previous interrupted run
if (Test-Path $tempWorktree) {
    git worktree remove $tempWorktree --force 2>$null
    Remove-Item $tempWorktree -Recurse -Force -ErrorAction SilentlyContinue
}

try {
    Write-Host "== 1. Creating temporary git worktree for gh-pages =="
    git worktree add $tempWorktree gh-pages
    if ($LASTEXITCODE -ne 0) { throw "FATAL: Failed to add git worktree for gh-pages" }

    Write-Host "== 2. Mirroring landing page and artifacts to root AND site/ =="
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

    Write-Host "== 3. Committing and pushing gh-pages =="
    Push-Location $tempWorktree
    try {
        git add -A
        $status = git status --porcelain
        if ($status) {
            git commit -m "deploy: update landing page and direct APK mirror for $Tag"
            git push origin gh-pages
            Write-Host "[x] Pushed updated landing page and APK to origin/gh-pages"
        } else {
            Write-Host "gh-pages is already up to date; nothing to commit."
        }
    } finally {
        Pop-Location
    }
} finally {
    Write-Host "== 4. Cleaning up temporary worktree =="
    git worktree remove $tempWorktree --force 2>$null
    Remove-Item $tempWorktree -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "=================================================="
Write-Host " gh-pages deployment complete!"
Write-Host " Landing: https://resolviaai.github.io/Dispatch/"
Write-Host " Direct:  https://resolviaai.github.io/Dispatch/Dispatch.apk"
Write-Host " Manifest: https://resolviaai.github.io/Dispatch/update.json"
Write-Host "=================================================="
