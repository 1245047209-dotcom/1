param(
    [string]$OutputPath = (Join-Path $PSScriptRoot 'NexusNative.exe'),
    [string]$AssetsPath = (Join-Path $PSScriptRoot '../assets')
)

$ErrorActionPreference = 'Stop'
$framework = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319'
$compiler = Join-Path $framework 'csc.exe'
$icon = (Resolve-Path -LiteralPath (Join-Path $AssetsPath 'nexus.ico')).Path
$output = [IO.Path]::GetFullPath($OutputPath)
$references = @('WPF/PresentationFramework.dll', 'WPF/PresentationCore.dll',
    'WPF/WindowsBase.dll', 'System.Xaml.dll', 'System.Web.Extensions.dll') |
    ForEach-Object { '/reference:' + (Join-Path $framework $_) }

Push-Location -LiteralPath $PSScriptRoot
try {
    & $compiler /nologo /target:winexe /platform:x64 /optimize+ /codepage:65001 `
        ('/win32icon:' + $icon) ('/out:' + $output) @references `
        /resource:NexusWindow.xaml /resource:DarkTheme.xaml ('/resource:' + $icon + ',nexus.ico') `
        NexusMotion.cs NexusNative.cs
    if ($LASTEXITCODE -ne 0) { throw "Nexus native frontend compilation failed with exit code $LASTEXITCODE." }
} finally {
    Pop-Location
}
