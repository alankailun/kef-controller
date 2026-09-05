param([string]$IsccPath)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$spec = Join-Path $projectRoot 'KEF Controller.spec'
$installerScript = Join-Path $projectRoot 'installer\KEF_Controller.iss'
foreach ($required in @($python, $spec, $installerScript)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required build file missing: $required"
    }
}

if (-not $IsccPath) {
    $compiler = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
    if ($compiler) { $IsccPath = $compiler.Source }
    else {
        $registryKeys = @(
            'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1',
            'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1',
            'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1'
        )
        foreach ($key in $registryKeys) {
            $location = (Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue).InstallLocation
            if ($location) {
                $candidate = Join-Path $location 'ISCC.exe'
                if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                    $IsccPath = $candidate
                    break
                }
            }
        }
    }
}
if (-not $IsccPath -or -not (Test-Path -LiteralPath $IsccPath -PathType Leaf)) {
    throw 'Inno Setup compiler not found. Use ./build.ps1 -IsccPath "path\to\ISCC.exe".'
}
$IsccPath = (Resolve-Path -LiteralPath $IsccPath).Path
$buildDir = Join-Path $projectRoot 'build'
$distDir = Join-Path $projectRoot 'dist'
$outputDir = Join-Path $projectRoot 'installer\output'

# Validate all deletion targets first; refuse links outside this checkout.
foreach ($directory in @($buildDir, $distDir)) {
    $fullPath = [IO.Path]::GetFullPath($directory)
    if ([IO.Path]::GetDirectoryName($fullPath) -ne $projectRoot) {
        throw "Build directory is outside the project: $fullPath"
    }
    if (Test-Path -LiteralPath $directory) {
        $item = Get-Item -LiteralPath $directory -Force
        if (-not $item.PSIsContainer -or $item.LinkType) {
            throw "Expected an ordinary build directory: $directory"
        }
        $links = Get-ChildItem -LiteralPath $directory -Recurse -Force | Where-Object { $_.LinkType }
        if ($links) { throw "Build directory contains links: $directory" }
    }
}

Push-Location $projectRoot
try {
    foreach ($directory in @($buildDir, $distDir)) {
        if (Test-Path -LiteralPath $directory) {
            Remove-Item -LiteralPath $directory -Recurse -Force
        }
    }
    New-Item -ItemType Directory -Path $buildDir -Force | Out-Null
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
    Write-Host 'Building application into dist\KEF Controller...'
    $pyinstallerLog = Join-Path $buildDir 'pyinstaller.log'
    & $python -m PyInstaller --clean --noconfirm --distpath $distDir --workpath $buildDir $spec *> $pyinstallerLog
    if ($LASTEXITCODE -ne 0) {
        Get-Content -LiteralPath $pyinstallerLog -Tail 40
        throw "PyInstaller failed. See $pyinstallerLog"
    }
    Write-Host 'Building installer/output/KEF_Controller_Setup.exe...'
    $installerLog = Join-Path $buildDir 'installer.log'
    $buildSource = Join-Path $distDir 'KEF Controller'
    & $IsccPath "/DBuildSource=$buildSource" "/O$outputDir" '/FKEF_Controller_Setup' $installerScript *> $installerLog
    if ($LASTEXITCODE -ne 0) {
        Get-Content -LiteralPath $installerLog -Tail 40
        throw "Installer build failed. See $installerLog"
    }
    $installer = Get-Item -LiteralPath (Join-Path $outputDir 'KEF_Controller_Setup.exe')
    Write-Host "Built version $($installer.VersionInfo.ProductVersion.Trim()): $($installer.FullName)"
} finally {
    Pop-Location
}
