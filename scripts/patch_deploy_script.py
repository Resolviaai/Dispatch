with open('scripts/deploy_gh_pages.ps1', 'r', encoding='utf-8') as f:
    text = f.read()

target = '''    if (Test-Path (Join-Path $siteDir "data-deletion.html")) {
        Copy-Item (Join-Path $siteDir "data-deletion.html") (Join-Path $tempWorktree "data-deletion.html") -Force
        Copy-Item (Join-Path $siteDir "data-deletion.html") (Join-Path $wtSiteDir "data-deletion.html") -Force
    }'''

replacement = '''    if (Test-Path (Join-Path $siteDir "data-deletion.html")) {
        Copy-Item (Join-Path $siteDir "data-deletion.html") (Join-Path $tempWorktree "data-deletion.html") -Force
        Copy-Item (Join-Path $siteDir "data-deletion.html") (Join-Path $wtSiteDir "data-deletion.html") -Force
    }

    # Mirror assets/ directory (CSS, JS bundles, fonts, panoramas) to root and site/
    if (Test-Path (Join-Path $siteDir "assets")) {
        Copy-Item (Join-Path $siteDir "assets") (Join-Path $tempWorktree "assets") -Recurse -Force
        Copy-Item (Join-Path $siteDir "assets") (Join-Path $wtSiteDir "assets") -Recurse -Force
    }

    # Mirror gen/ directory (card visuals) to root and site/
    if (Test-Path (Join-Path $siteDir "gen")) {
        Copy-Item (Join-Path $siteDir "gen") (Join-Path $tempWorktree "gen") -Recurse -Force
        Copy-Item (Join-Path $siteDir "gen") (Join-Path $wtSiteDir "gen") -Recurse -Force
    }'''

if target in text:
    text = text.replace(target, replacement)
    with open('scripts/deploy_gh_pages.ps1', 'w', encoding='utf-8') as f:
        f.write(text)
    print("Updated deploy_gh_pages.ps1 to mirror assets/ and gen/ directories!")
else:
    print("Target not found directly, check diff")
