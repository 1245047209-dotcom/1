$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$repoRoot = Split-Path $PSScriptRoot -Parent
$meta = Get-Content -LiteralPath (Join-Path $repoRoot 'release-input/release.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$buildRoot = Join-Path $env:RUNNER_TEMP ('nexus-build-' + $meta.tag)
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
$dist = Join-Path $repoRoot 'dist'
$payload = Join-Path $buildRoot 'payload'
$baseline = Join-Path $buildRoot 'baseline'
$compiler = Join-Path $buildRoot 'compiler'
function FetchVerified($spec, [string]$target) {
    Invoke-WebRequest -Uri $spec.url -OutFile $target -MaximumRetryCount 3
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $spec.sha256) {
        throw "Downloaded file failed SHA-256 verification: $target"
    }
}
function RunSetup([string]$file, [string[]]$setupArguments) {
    $process = Start-Process -FilePath $file -ArgumentList $setupArguments -WindowStyle Hidden -PassThru -Wait
    if ($process.ExitCode -ne 0) { throw "Setup failed with exit code $($process.ExitCode): $file" }
}
$baseSetup = Join-Path $buildRoot 'base-setup.exe'
$compilerSetup = Join-Path $buildRoot 'compiler-setup.exe'
FetchVerified $meta.base_installer $baseSetup
FetchVerified $meta.compiler $compilerSetup
RunSetup $baseSetup @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/NOICONS',('/DIR="'+$baseline+'"'),('/LOG="'+(Join-Path $buildRoot 'baseline.log')+'"'))
RunSetup $compilerSetup @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/CURRENTUSER','/NOICONS','/TASKS=""',('/DIR="'+$compiler+'"'),('/LOG="'+(Join-Path $buildRoot 'compiler.log')+'"'))
python (Join-Path $PSScriptRoot 'stage_payload.py') $repoRoot $baseline $payload
if ($LASTEXITCODE -ne 0) { throw 'Payload staging failed' }
$testRoot = Join-Path $buildRoot 'source-test'
python -c 'import shutil,sys;shutil.copytree(sys.argv[1],sys.argv[2])' $payload $testRoot
if ($LASTEXITCODE -ne 0) { throw 'Isolated test staging failed' }
python (Join-Path $PSScriptRoot 'check_release.py') $testRoot
if ($LASTEXITCODE -ne 0) { throw 'Source regression tests failed' }
python (Join-Path $PSScriptRoot 'gui_smoke.py') $testRoot
if ($LASTEXITCODE -ne 0) { throw 'GUI smoke check failed' }
$unexpected = Get-ChildItem -LiteralPath $payload -File | Where-Object {
    ($_.Extension -eq '.json' -and $_.Name -ne 'SOURCE_MANIFEST.json') -or $_.Name -eq 'tray_pid.txt' -or $_.Extension -in @('.log','.lock')
}
if ($unexpected) { throw ('Unexpected runtime state in pristine payload: ' + ($unexpected.Name -join ', ')) }
New-Item -ItemType Directory -Path $dist -Force | Out-Null
$numericVersion = $meta.tag.Substring(1)
while (($numericVersion.Split('.')).Count -lt 3) { $numericVersion += '.0' }
& (Join-Path $compiler 'ISCC.exe') ('/DPayloadDir='+$payload) ('/DDistDir='+$dist) ('/DReleaseTag='+$meta.tag) ('/DAppVersion='+$numericVersion) (Join-Path $PSScriptRoot 'Nexus.iss')
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed' }

# Exercise the actual upgrade package in this disposable runner, preserving data.
$smokeRoot = Join-Path $buildRoot 'installed-smoke'
New-Item -ItemType Directory -Path $smokeRoot -Force | Out-Null
$sentinels = @{
    'added_devices.json' = '{"version":1,"selected":"aula-f87-default","view":"all","devices":[]}'
    'tray_config.json' = '{"interval":120,"autostart":false,"low_battery_alert":false}'
    'battery_activity.json' = '{"release_smoke_sentinel":true}'
}
foreach ($name in $sentinels.Keys) { [IO.File]::WriteAllText((Join-Path $smokeRoot $name),$sentinels[$name]) }
$installer = Join-Path $dist $meta.installer
RunSetup $installer @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/NOICONS','/TASKS=""',('/DIR="'+$smokeRoot+'"'),('/LOG="'+(Join-Path $buildRoot 'smoke-install.log')+'"'))
foreach ($name in $sentinels.Keys) {
    if ([IO.File]::ReadAllText((Join-Path $smokeRoot $name)) -ne $sentinels[$name]) { throw "Upgrade replaced user data: $name" }
}
python (Join-Path $PSScriptRoot 'check_release.py') $smokeRoot --installed
if ($LASTEXITCODE -ne 0) { throw 'Installed payload verification failed' }
& (Join-Path $smokeRoot 'runtime/python.exe') -X utf8 (Join-Path $smokeRoot 'my_devices.py') --help
if ($LASTEXITCODE -ne 0) { throw 'Installed runtime entry failed' }
python (Join-Path $PSScriptRoot 'finalize_release.py') $payload $dist
if ($LASTEXITCODE -ne 0) { throw 'Artifact/checksum preparation failed' }
Write-Output "Verified source, GUI, runtime and upgrade preservation for $($meta.tag)."
