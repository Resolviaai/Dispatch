# Dispatch release preparation script.
# Builds the signed release APK, verifies package identity + signature, and
# generates update.json FROM THE ACTUAL APK (no hand-typed metadata).
# Fails loudly on: missing keystore, signing errors, version mismatch, missing files.
#
# Prerequisites: JAVA_HOME=jdk-17, Android SDK build-tools, git-ignored
#   android/keystore.properties + android/keystore/dispatch-release.jks
# Usage:  powershell -File scripts\prepare_release.ps1 [-Tag v1.0.3-alpha]
# It NEVER publishes; candidate artifacts land in release-out/ for manual review.

param([string]$Tag = "")
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$gradle = Join-Path $root "android\gradlew.bat"
$apkOut = Join-Path $root "android\app\build\outputs\apk\release\app-release.apk"
$outDir = Join-Path $root "release-out"
$meta = Join-Path $root "android\keystore.properties"

if (-not (Test-Path $meta)) { throw "FATAL: android/keystore.properties missing - release signing not configured." }
if (-not ($env:JAVA_HOME)) { throw "FATAL: set JAVA_HOME to JDK 17 first." }

Write-Host "== 1. assembleRelease =="
& $gradle -p (Join-Path $root "android") ":app:assembleRelease" "--console=plain" -q
if ($LASTEXITCODE -ne 0) { throw "FATAL: assembleRelease failed." }
if (-not (Test-Path $apkOut)) { throw "FATAL: APK not found at $apkOut" }

Write-Host "== 2. identity verification (package, version, signature) =="
$buildTools = Get-ChildItem "$env:LOCALAPPDATA\Android\Sdk\build-tools" | Sort-Object Name | Select-Object -Last 1
$aapt = Join-Path $buildTools.FullName "aapt.exe"
$badging = & $aapt dump badging $apkOut
$pkg = ($badging | Select-String "^package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'").Matches
if (-not $pkg) { throw "FATAL: cannot parse APK badging." }
$pkgName = $pkg.Groups[1].Value; $versionCode = [int]$pkg.Groups[2].Value; $versionName = $pkg.Groups[3].Value
if ($pkgName -ne "com.resolvia.dispatch") { throw "FATAL: wrong package $pkgName" }
$apksigner = Join-Path $buildTools.FullName "apksigner.bat"
$certs = & $apksigner verify --print-certs $apkOut
$fp = ($certs | Select-String "SHA-256 digest: ([0-9a-f]+)").Matches.Groups[1].Value
if (-not $fp) { throw "FATAL: APK is not signed or signature unreadable." }
Write-Host "   package=$pkgName versionCode=$versionCode versionName=$versionName"
Write-Host "   cert sha256=$fp"

$expected = [regex]::Match((Get-Content (Join-Path $root "android\app\build.gradle.kts") -Raw), 'versionCode\s*=\s*(\d+)').Groups[1].Value
if ([int]$expected -ne $versionCode) { throw "FATAL: gradle versionCode $expected != APK versionCode $versionCode" }

Write-Host "== 3. update.json from the actual APK =="
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$apkDest = Join-Path $outDir "Dispatch.apk"
Copy-Item $apkOut $apkDest -Force
$hash = (Get-FileHash $apkDest -Algorithm SHA256).Hash.ToLower()
$size = (Get-Item $apkDest).Length
if ($Tag -eq "") { $Tag = "v$versionName-alpha" }
$notes = "Dispatch $versionName alpha build."
$updateJson = [ordered]@{
  schemaVersion = 1
  packageName   = $pkgName
  versionCode   = $versionCode
  versionName   = $versionName
  releaseTag    = $Tag
  publishedAt   = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  releaseNotes  = $notes
  apkAsset      = "Dispatch.apk"
  apkSizeBytes  = $size
  apkSha256     = $hash
}
$jsonPath = Join-Path $outDir "update.json"
$updateJson | ConvertTo-Json | Set-Content $jsonPath -Encoding utf8

Write-Host "== 4. candidate artifacts in release-out/ =="
Get-ChildItem $outDir | Format-Table Name, Length
Write-Host "DONE. Review, then attach BOTH files to GitHub release '$Tag' manually."
Write-Host "Signer certificate (compare with previous release): $fp"
