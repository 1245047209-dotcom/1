# Render the reference's two bold arcs and rounded triangular signal point.
Add-Type -AssemblyName System.Drawing
$hi = [System.Drawing.Bitmap]::new(240, 208)
$g = [System.Drawing.Graphics]::FromImage($hi)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.ScaleTransform(8, 8)
$pen = [System.Drawing.Pen]::new([System.Drawing.Color]::Black, [single]4.2)
$pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
$pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
$path = [System.Drawing.Drawing2D.GraphicsPath]::new()
$path.AddBezier([single]3, [single]8, [single]9.5, [single]0, [single]20.5, [single]0, [single]27, [single]8)
$g.DrawPath($pen, $path)
$path.Reset()
$path.AddBezier([single]7.6, [single]14.2, [single]11.4, [single]10, [single]18.6, [single]10, [single]22.4, [single]14.2)
$g.DrawPath($pen, $path)
$path.Reset()
$path.AddBezier([single]10.8, [single]18.9, [single]12.7, [single]16.6, [single]17.3, [single]16.6, [single]19.2, [single]18.9)
$path.AddBezier([single]19.2, [single]18.9, [single]20, [single]19.9, [single]16.2, [single]23.6, [single]15, [single]24)
$path.AddBezier([single]15, [single]24, [single]13.8, [single]23.6, [single]10, [single]19.9, [single]10.8, [single]18.9)
$path.CloseFigure()
$g.FillPath([System.Drawing.Brushes]::Black, $path)
$path.Dispose()
$pen.Dispose()
$g.Dispose()

$image = [System.Drawing.Bitmap]::new(20, 17)
$output = [System.Drawing.Graphics]::FromImage($image)
$output.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
$output.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$output.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
$output.DrawImage($hi, [System.Drawing.Rectangle]::new(0, 0, 20, 17))
$output.Dispose()
$hi.Dispose()
$asset = Join-Path (Split-Path $PSScriptRoot -Parent) 'assets\wireless-icon.png'
$image.Save($asset, [System.Drawing.Imaging.ImageFormat]::Png)
$image.Dispose()
Write-Output 'Rendered wireless-icon.png (20x17, transparent background).'
