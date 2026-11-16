# Fit the supplied artwork into the header's 34px rounded icon container.
Add-Type -AssemblyName System.Drawing
$assets = Join-Path (Split-Path $PSScriptRoot -Parent) 'assets'
$source = [System.Drawing.Bitmap]::new((Join-Path $assets 'nexus-brand-source.png'))
$hi = [System.Drawing.Bitmap]::new(272, 272)
$g = [System.Drawing.Graphics]::FromImage($hi)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality

# Clip the artwork to the rounded container, keeping its corners transparent.
$diameter = [single](8.8 * 8 * 2)
$edge = [single](272 - $diameter)
$clip = [System.Drawing.Drawing2D.GraphicsPath]::new()
$clip.AddArc(0, 0, $diameter, $diameter, 180, 90)
$clip.AddArc($edge, 0, $diameter, $diameter, 270, 90)
$clip.AddArc($edge, $edge, $diameter, $diameter, 0, 90)
$clip.AddArc(0, $edge, $diameter, $diameter, 90, 90)
$clip.CloseFigure()
$g.SetClip($clip)
$attributes = [System.Drawing.Imaging.ImageAttributes]::new()
$attributes.SetWrapMode([System.Drawing.Drawing2D.WrapMode]::TileFlipXY)
$g.DrawImage($source, [System.Drawing.Rectangle]::new(0, 0, 272, 272),
    0, 0, $source.Width, $source.Height, [System.Drawing.GraphicsUnit]::Pixel, $attributes)
$attributes.Dispose()
$clip.Dispose()
$g.Dispose()
$source.Dispose()

foreach ($size in @(16, 32, 34, 48, 256)) {
    $icon = [System.Drawing.Bitmap]::new($size, $size)
    $output = [System.Drawing.Graphics]::FromImage($icon)
    $output.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
    $output.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $output.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $output.DrawImage($hi, [System.Drawing.Rectangle]::new(0, 0, $size, $size))
    $output.Dispose()
    $name = if ($size -eq 34) { 'nexus-brand.png' } else { "nexus-window-$size.png" }
    $icon.Save((Join-Path $assets $name), [System.Drawing.Imaging.ImageFormat]::Png)
    $icon.Dispose()
}
$hi.Dispose()
Write-Output 'Rendered header icon and 16/32/48/256px window icons.'
