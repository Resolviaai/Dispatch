# Dispatch release preparation script.
# Builds the signed release APK, verifies package identity + signature, and
# generates update.json FROM THE ACTUAL APK (no hand-typed metadata).
# Fails loudly on: dirty working tree, unexpected HEAD commit, missing keystore,
# signing errors, version mismatch, missing files.
#
# Prerequisites: JAVA_HOME=jdk-17, Android SDK build-tools, git-ignored
#   android/keystore.properties + android/keystore/dispatch-release.jks
# Usage:  powershell -File scripts\prepare_release.ps1 [-Tag v1.0.3-alpha] [-ExpectedCommit <sha>] [-AllowDirty]
# It NEVER publishes; candidate artifacts land in release-out/ for manual review.

param(
    [string]$Tag = "",
    [string]$ExpectedCommit = "",
    [switch]$AllowDirty = $false
)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$gradle = Join-Path $root "android\gradlew.bat"
$gradleFile = Join-Path $root "android\app\build.gradle.kts"
$apkOut = Join-Path $root "android\app\build\outputs\apk\release\app-release.apk"
$outDir = Join-Path $root "release-out"
$meta = Join-Path $root "android\keystore.properties"

# update.json is consumed by the Android updater (org.json). A UTF-8 BOM is not
# guaranteed to survive that parse path, and PowerShell 5.1's `Set-Content
# -Encoding UTF8` writes one, so write BOM-less UTF-8 explicitly (same as PS 7's
# `utf8NoBOM`).
function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text
    )
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Text, $encoding)
}

# --- release provenance guard -------------------------------------------------
Write-Host "== 0. release provenance guard =="
$porcelain = @(git status --porcelain)
if ($LASTEXITCODE -ne 0) { throw "FATAL: git status failed - run this from a git checkout." }
if ($porcelain.Count -gt 0 -and -not $AllowDirty) {
    $porcelain | ForEach-Object { Write-Host "   $_" }
    throw "FATAL: working tree is dirty ($($porcelain.Count) path(s)). Commit or stash before releasing, or pass -AllowDirty for a throwaway build."
}
if ($porcelain.Count -gt 0) {
    Write-Warning "Working tree is dirty; continuing because -AllowDirty was passed. Do NOT publish this artifact."
}

$headSha = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $headSha) { throw "FATAL: cannot resolve HEAD." }

$gradleRaw = Get-Content -Path $gradleFile -Raw -Encoding UTF8
$gradleCodeMatch = [regex]::Match($gradleRaw, 'versionCode\s*=\s*(\d+)')
$gradleNameMatch = [regex]::Match($gradleRaw, 'versionName\s*=\s*"([^"]+)"')
if (-not $gradleCodeMatch.Success -or -not $gradleNameMatch.Success) {
    throw "FATAL: cannot parse versionCode/versionName from $gradleFile"
}
$gradleCode = $gradleCodeMatch.Groups[1].Value
$gradleVersionName = $gradleNameMatch.Groups[1].Value

if ($Tag -eq "") { $Tag = "v$gradleVersionName-alpha" }

if ($ExpectedCommit -ne "") {
    $expectedSha = (git rev-parse --verify --quiet "$ExpectedCommit^{commit}")
    if ($LASTEXITCODE -ne 0 -or -not $expectedSha) {
        throw "FATAL: -ExpectedCommit '$ExpectedCommit' does not resolve to a commit."
    }
    if ($expectedSha.Trim() -ne $headSha) {
        throw "FATAL: HEAD is $headSha but the expected release commit is $($expectedSha.Trim()). Check out the release commit or pass the correct -ExpectedCommit."
    }
    Write-Host "   HEAD $headSha matches -ExpectedCommit"
} else {
    # Default expectation: if the release tag already exists it must point at HEAD,
    # i.e. HEAD is the tagged release commit.
    $tagSha = (git rev-parse --verify --quiet "refs/tags/$Tag^{commit}")
    if ($LASTEXITCODE -eq 0 -and $tagSha) {
        if ($tagSha.Trim() -ne $headSha) {
            throw "FATAL: tag '$Tag' points at $($tagSha.Trim()) but HEAD is $headSha. Check out the tagged release commit, re-tag, or pass -ExpectedCommit to override."
        }
        Write-Host "   HEAD $headSha matches tag $Tag"
    } else {
        Write-Warning "Tag '$Tag' does not exist yet; building from HEAD $headSha. Tag this commit after the build."
    }
}

if (-not (Test-Path $meta)) { throw "FATAL: android/keystore.properties missing - release signing not configured." }
if (-not ($env:JAVA_HOME)) { throw "FATAL: set JAVA_HOME to JDK 17 first." }

Write-Host "== 1. assembleRelease =="
& $gradle -p (Join-Path $root "android") ":app:assembleRelease" "--console=plain" -q
if ($LASTEXITCODE -ne 0) { throw "FATAL: assembleRelease failed." }
if (-not (Test-Path $apkOut)) { throw "FATAL: APK not found at $apkOut" }

Write-Host "== 2. identity verification (package, version, signature) =="
# Select build-tools by NUMERIC version order (lexicographic ordering ranks
# "9.0.0" above "35.0.0" once two-digit versions appear).
$buildToolsRoot = Join-Path $env:LOCALAPPDATA "Android\Sdk\build-tools"
if (-not (Test-Path $buildToolsRoot)) { throw "FATAL: Android SDK build-tools directory not found at $buildToolsRoot" }
$buildToolsCandidates = Get-ChildItem -Path $buildToolsRoot -Directory |
    Where-Object { $_.Name -match '^\d+(?:\.\d+)+' } |
    ForEach-Object {
        [pscustomobject]@{
            Directory = $_
            Version   = [version]([regex]::Match($_.Name, '^\d+(?:\.\d+)+').Value)
            # stable releases (no "-rc"/"-beta" suffix) win ties against previews
            Stable    = $(if ($_.Name -match '-') { 0 } else { 1 })
        }
    }
$buildTools = ($buildToolsCandidates | Sort-Object Version, Stable | Select-Object -Last 1).Directory
if (-not $buildTools) { throw "FATAL: no versioned build-tools directory found under $buildToolsRoot" }
Write-Host "   build-tools=$($buildTools.Name)"
$aapt = Join-Path $buildTools.FullName "aapt.exe"
if (-not (Test-Path $aapt)) { throw "FATAL: aapt.exe not found in $($buildTools.FullName)" }
$badging = & $aapt dump badging $apkOut
$pkg = ($badging | Select-String "^package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'").Matches
if (-not $pkg) { throw "FATAL: cannot parse APK badging." }
$pkgName = $pkg.Groups[1].Value; $versionCode = [int]$pkg.Groups[2].Value; $versionName = $pkg.Groups[3].Value
if ($pkgName -ne "com.resolvia.dispatch") { throw "FATAL: wrong package $pkgName" }
$apksigner = Join-Path $buildTools.FullName "apksigner.bat"
$certs = & $apksigner verify --print-certs $apkOut
$fp = ($certs | Select-String "SHA-256 digest: ([0-9a-f]+)").Matches.Groups[1].Value
if (-not $fp) { throw "FATAL: APK is not signed or signature unreadable." }
# ENFORCED trusted release identity (must match scripts/prepare_release.ps1 AND the
# updater constant in DispatchUpdateManager; test_release_identity.py keeps them in sync)
$trustedFingerprint = "94f778490fde8ffd2c7e644d0c8169249f2e79829d769b5f71aa0301c5643804"
if ($fp -ne $trustedFingerprint) { throw "FATAL: APK signer $fp does not match the trusted Dispatch release identity $trustedFingerprint" }
# stale-output guard: remove artifacts from any previous failed run before copying
Remove-Item (Join-Path $outDir "*") -ErrorAction SilentlyContinue
Write-Host "   package=$pkgName versionCode=$versionCode versionName=$versionName"
Write-Host "   cert sha256=$fp"

if ([int]$gradleCode -ne $versionCode) { throw "FATAL: gradle versionCode $gradleCode != APK versionCode $versionCode" }
if ($gradleVersionName -ne $versionName) { throw "FATAL: gradle versionName $gradleVersionName != APK versionName $versionName" }

Write-Host "== 3. update.json from the actual APK =="
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$apkDest = Join-Path $outDir "Dispatch.apk"
Copy-Item $apkOut $apkDest -Force
$hash = (Get-FileHash $apkDest -Algorithm SHA256).Hash.ToLower()
$size = (Get-Item $apkDest).Length
$notes = "Dispatch $versionName alpha build."
$updateJson = [ordered]@{
  schemaVersion = 1
  packageName   = $pkgName
  versionCode   = $versionCode
  versionName   = $versionName
  releaseTag    = $Tag
  generatedAt   = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  releaseNotes  = $notes
  apkAsset      = "Dispatch.apk"
  apkSizeBytes  = $size
  apkSha256     = $hash
}
$jsonPath = Join-Path $outDir "update.json"
Write-Utf8NoBom -Path $jsonPath -Text ($updateJson | ConvertTo-Json -Depth 5)
$writtenManifest = Get-Content -Path $jsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([int]$writtenManifest.versionCode -ne [int]$versionCode -or
    "$($writtenManifest.apkSha256)" -ne $hash) {
    throw "FATAL: written update.json does not match the built APK; refusing to hand off a corrupt manifest."
}

Write-Host "== 4. candidate artifacts in release-out/ =="
Get-ChildItem $outDir | Format-Table Name, Length
Write-Host "DONE. Review, then attach BOTH files to GitHub release '$Tag' manually."
Write-Host "Signer certificate (compare with previous release): $fp"
