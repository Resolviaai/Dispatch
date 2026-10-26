# Dispatch Version Bumper & Release Manager
# Enforces Semantic Versioning (SemVer) and Android versionCode monotonicity.
#
# Usage:
#   powershell -File scripts\bump_version.ps1                  # Auto-detects based on git commits since last tag
#   powershell -File scripts\bump_version.ps1 -Type minor     # Explicit minor bump (e.g. 1.1.23 -> 1.2.0)
#   powershell -File scripts\bump_version.ps1 -Type patch     # Explicit patch bump (e.g. 1.1.23 -> 1.1.24)
#   powershell -File scripts\bump_version.ps1 -Type major     # Explicit major bump (e.g. 1.1.23 -> 2.0.0)
#   powershell -File scripts\bump_version.ps1 -Version 1.2.0 -Code 28  # Explicit override

param(
    [ValidateSet("patch", "minor", "major", "auto")]
    [string]$Type = "auto",
    [string]$Version = "",
    [int]$Code = 0,
    [switch]$DryRun = $false
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# 1. Read current version from android/app/build.gradle.kts
$gradlePath = Join-Path $root "android\app\build.gradle.kts"
$gradleContent = Get-Content $gradlePath -Raw

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
Set-Content -Path $gradlePath -Value $newGradle -NoNewline
Write-Host "[x] Updated $gradlePath"

# 5. Update frontend/package.json
$pkgPath = Join-Path $root "frontend\package.json"
if (Test-Path $pkgPath) {
    $pkgContent = Get-Content $pkgPath -Raw
    $newPkg = [regex]::Replace($pkgContent, '"version":\s*"[^"]+"', "`"version`": `"$nextVersion`"")
    Set-Content -Path $pkgPath -Value $newPkg -NoNewline
    Write-Host "[x] Updated $pkgPath"
}

# 6. Update site/index.html & site/update.json
$siteHtmlPath = Join-Path $root "site\index.html"
if (Test-Path $siteHtmlPath) {
    $htmlContent = Get-Content $siteHtmlPath -Raw
    # Update version text
    $newHtml = [regex]::Replace($htmlContent, 'Alpha\s+\d+\.\d+\.\d+', "Alpha $nextVersion")
    # Update release download URLs
    $newHtml = [regex]::Replace($newHtml, 'https://github\.com/Resolviaai/Dispatch/releases/download/v[\w.-]+/Dispatch\.apk', "https://github.com/Resolviaai/Dispatch/releases/download/v$nextVersion-alpha/Dispatch.apk")
    Set-Content -Path $siteHtmlPath -Value $newHtml -NoNewline
    Write-Host "[x] Updated $siteHtmlPath (links and version pills)"
}

$siteUpdatePath = Join-Path $root "site\update.json"
if (Test-Path $siteUpdatePath) {
    $updateJsonContent = Get-Content $siteUpdatePath -Raw | ConvertFrom-Json
    $updateJsonContent.versionCode = $nextCode
    $updateJsonContent.versionName = $nextVersion
    $updateJsonContent.releaseTag = "v$nextVersion-alpha"
    $updateJsonContent.releaseNotes = "Dispatch $nextVersion alpha build."
    $updateJsonContent | ConvertTo-Json | Set-Content -Path $siteUpdatePath -Encoding utf8
    Write-Host "[x] Updated $siteUpdatePath"
}

# 7. Update README.md
$readmePath = Join-Path $root "README.md"
if (Test-Path $readmePath) {
    $readmeContent = Get-Content $readmePath -Raw
    $newReadme = [regex]::Replace($readmeContent, 'Android\s+1\.\d+\.\d+\s+\(version\s+code\s+\d+\)', "Android $nextVersion (version code $nextCode)")
    Set-Content -Path $readmePath -Value $newReadme -NoNewline
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
