# Dispatch Version Bumper & Release Manager
# Enforces Semantic Versioning (SemVer) and Android versionCode monotonicity.
#
# Usage:
#   powershell -File scripts\bump_version.ps1                  # Auto-detects based on git commits since last tag
#   powershell -File scripts\bump_version.ps1 -Type minor     # Explicit minor bump (e.g. 1.1.23 -> 1.2.0)
#   powershell -File scripts\bump_version.ps1 -Type patch     # Explicit patch bump (e.g. 1.1.23 -> 1.1.24)
#   powershell -File scripts\bump_version.ps1 -Type major     # Explicit major bump (e.g. 1.1.23 -> 2.0.0)
#   powershell -File scripts\bump_version.ps1 -Version 1.2.0 -Code 28  # Explicit override
#
# SCOPE / WHAT THIS SCRIPT DOES *NOT* TOUCH
#   Everything it writes is UTF-8 WITHOUT a BOM (see Write-Utf8NoBom). A BOM would
#   break `npm ci` (JSON.parse in package.json), the Android updater's org.json
#   parsing of update.json, and Kotlin Gradle script parsing.
#   This script only rewrites VERSION TOKENS. It cannot know the size or SHA-256 of
#   an APK that has not been built yet, so it deliberately never fabricates them:
#     - site/update.json: apkSizeBytes/apkSha256 keep their previous (now stale)
#       values until scripts/prepare_release.ps1 regenerates the whole manifest from
#       the real APK. The script warns about this instead of pretending otherwise.
#     - site/index.html: download URLs + "Alpha X.Y.Z" pills are rewritten, but the
#       human-readable APK size copy (~21 MB / 21.1 MB) is NOT rewritten. TODO:
#       derive that copy from the real APK size at release/deploy time.

param(
    [ValidateSet("patch", "minor", "major", "auto")]
    [string]$Type = "auto",
    [string]$Version = "",
    [int]$Code = 0,
    [switch]$DryRun = $false
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# PowerShell 5.1 `Set-Content -Encoding UTF8` emits a BOM, and PowerShell 7's
# `utf8NoBOM` is unavailable on 5.1. This helper writes UTF-8 without a BOM on both.
function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text
    )
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Text, $encoding)
}

# 1. Read current version from android/app/build.gradle.kts
$gradlePath = Join-Path $root "android\app\build.gradle.kts"
$gradleContent = Get-Content -Path $gradlePath -Raw -Encoding UTF8

$codeMatch = [regex]::Match($gradleContent, 'versionCode\s*=\s*(\d+)')
$nameMatch = [regex]::Match($gradleContent, 'versionName\s*=\s*"([^"]+)"')

if (-not $codeMatch.Success -or -not $nameMatch.Success) {
    throw "FATAL: Could not parse versionCode or versionName from $gradlePath"
}

$curCode = [int]$codeMatch.Groups[1].Value
$curVersion = $nameMatch.Groups[1].Value

Write-Host "=================================================="
Write-Host " Dispatch Version Governance"
Write-Host " Current Version: $curVersion"
Write-Host " Current Code:    $curCode"
Write-Host "=================================================="

# Parse SemVer
$semverMatch = [regex]::Match($curVersion, '^(\d+)\.(\d+)\.(\d+)(?:-.*)?$')
if (-not $semverMatch.Success) {
    throw "FATAL: Current version '$curVersion' is not valid SemVer (X.Y.Z)"
}

$major = [int]$semverMatch.Groups[1].Value
$minor = [int]$semverMatch.Groups[2].Value
$patch = [int]$semverMatch.Groups[3].Value

# 2. Inspect commits since last git tag
$latestTag = (git describe --tags --abbrev=0 2>$null)
Write-Host " Latest Git Tag:  $latestTag"

$commits = @()
if ($latestTag) {
    $commits = git log "$latestTag..HEAD" --oneline
} else {
    $commits = git log -n 20 --oneline
}

$featCount = 0
$fixCount = 0
$breakingCount = 0

foreach ($c in $commits) {
    if ($c -match '(?i)breaking|!:') { $breakingCount++ }
    elseif ($c -match '(?i)^[a-f0-9]+\s+feat') { $featCount++ }
    elseif ($c -match '(?i)^[a-f0-9]+\s+fix') { $fixCount++ }
}

Write-Host " Changes since tag ($($commits.Count) commits):"
Write-Host "   - Breaking changes: $breakingCount"
Write-Host "   - Feature commits:  $featCount"
Write-Host "   - Fix commits:      $fixCount"

# 3. Determine next version
if ($Version -eq "") {
    if ($Type -eq "auto") {
        if ($breakingCount -gt 0) {
            $Type = "major"
        } elseif ($featCount -gt 0) {
            $Type = "minor"
        } else {
            $Type = "patch"
        }
        Write-Host " Auto-detected bump type: $Type"
    }

    if ($Type -eq "major") {
        $major++
        $minor = 0
        $patch = 0
    } elseif ($Type -eq "minor") {
        $minor++
        $patch = 0
    } elseif ($Type -eq "patch") {
        $patch++
    }
    $nextVersion = "$major.$minor.$patch"
} else {
    $nextVersion = $Version
}

if ($Code -le 0) {
    $nextCode = $curCode + 1
} else {
    $nextCode = $Code
}

if ($nextCode -le $curCode) {
    throw "FATAL: Next versionCode ($nextCode) must be strictly greater than current versionCode ($curCode)!"
}

Write-Host "--------------------------------------------------"
Write-Host " Target Version: $nextVersion"
Write-Host " Target Code:    $nextCode"
Write-Host " Tag to Create:  v$nextVersion-alpha"
Write-Host "--------------------------------------------------"

if ($DryRun) {
    Write-Host "Dry run enabled. No files modified."
    exit 0
}

# 4. Update android/app/build.gradle.kts
$newGradle = [regex]::Replace($gradleContent, 'versionCode\s*=\s*\d+', "versionCode = $nextCode")
$newGradle = [regex]::Replace($newGradle, 'versionName\s*=\s*"[^"]+"', "versionName = `"$nextVersion`"")
Write-Utf8NoBom -Path $gradlePath -Text $newGradle
Write-Host "[x] Updated $gradlePath"

# 5. Update frontend/package.json
$pkgPath = Join-Path $root "frontend\package.json"
if (Test-Path $pkgPath) {
    $pkgContent = Get-Content -Path $pkgPath -Raw -Encoding UTF8
    $newPkg = [regex]::Replace($pkgContent, '"version":\s*"[^"]+"', "`"version`": `"$nextVersion`"")
    Write-Utf8NoBom -Path $pkgPath -Text $newPkg
    Write-Host "[x] Updated $pkgPath"
}

# 6a. Update site/index.html version tokens (pills + release download URLs ONLY)
$siteHtmlPath = Join-Path $root "site\index.html"
if (Test-Path $siteHtmlPath) {
    $htmlContent = Get-Content -Path $siteHtmlPath -Raw -Encoding UTF8
    # Version pills / copy, e.g. "Alpha 1.5.3" or "Alpha 2.0.0"
    $newHtml = [regex]::Replace($htmlContent, 'Alpha\s+v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?', "Alpha $nextVersion")
    # Release download URLs (tag prefix changes with every bump)
    $newHtml = [regex]::Replace($newHtml, 'https://github\.com/Resolviaai/Dispatch/releases/download/v[\w.-]+/Dispatch\.apk', "https://github.com/Resolviaai/Dispatch/releases/download/v$nextVersion-alpha/Dispatch.apk")
    Write-Utf8NoBom -Path $siteHtmlPath -Text $newHtml
    Write-Host "[x] Updated $siteHtmlPath (version pills and release download URLs)"
    # TODO(release): the landing page also carries human-readable APK size copy
    # ("~21&nbsp;MB", "21.1 MB"). Size is only knowable after the APK is built, so it
    # is intentionally left alone here and must be refreshed by hand (or by a future
    # deploy-time rewrite) from release-out/update.json apkSizeBytes.
    Write-Warning "site/index.html APK size copy was NOT updated (unknown until the APK is built)."
}

# 6b. Update site/update.json version fields.
# apkSha256 / apkSizeBytes describe an APK that does not exist yet, so they are left
# untouched (still referencing the previous build) and a warning is emitted.
# scripts/prepare_release.ps1 regenerates this whole manifest from the real APK.
$siteUpdatePath = Join-Path $root "site\update.json"
if (Test-Path $siteUpdatePath) {
    $updateJsonContent = Get-Content -Path $siteUpdatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $updateJsonContent.versionCode = $nextCode
    $updateJsonContent.versionName = $nextVersion
    $updateJsonContent.releaseTag = "v$nextVersion-alpha"
    $updateJsonContent.releaseNotes = "Dispatch $nextVersion alpha build."
    $newJson = $updateJsonContent | ConvertTo-Json -Depth 5
    Write-Utf8NoBom -Path $siteUpdatePath -Text $newJson
    Write-Host "[x] Updated $siteUpdatePath (versionCode, versionName, releaseTag, releaseNotes)"
    Write-Warning "site/update.json apkSha256/apkSizeBytes still describe the PREVIOUS APK. Run scripts/prepare_release.ps1 and deploy release-out/update.json before publishing."
}

# 7. Update README.md (works for MAJOR bumps too: e.g. "Android 2.0.0 (version code 51)")
$readmePath = Join-Path $root "README.md"
if (Test-Path $readmePath) {
    $readmeContent = Get-Content -Path $readmePath -Raw -Encoding UTF8
    $newReadme = [regex]::Replace($readmeContent, 'Android\s+v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?\s+\(version\s+code\s+\d+\)', "Android $nextVersion (version code $nextCode)")
    Write-Utf8NoBom -Path $readmePath -Text $newReadme
    Write-Host "[x] Updated $readmePath"
}

Write-Host "=================================================="
Write-Host " Version bump complete!"
Write-Host " Next steps:"
Write-Host "   1. Compile APK:    cmd /c `"set JAVA_HOME=C:\Program Files\Android\Android Studio\jbr&& powershell -ExecutionPolicy Bypass -File scripts\prepare_release.ps1`""
Write-Host "   2. Git commit:     git commit -am `"chore(release): bump to v$nextVersion-alpha (code $nextCode)`""
Write-Host "   3. Git tag:        git tag v$nextVersion-alpha"
Write-Host "   4. Push & Release: git push origin main --tags"
Write-Host "=================================================="
