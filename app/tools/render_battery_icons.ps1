param([string]$PreviewPath = '')

# Build the antialiased Tk sprite sheet with Windows' built-in drawing library.
Add-Type -AssemblyName System.Drawing
$assetDirectory = Join-Path (Split-Path $PSScriptRoot -Parent) 'assets'

function New-RoundPath([float]$x, [float]$y, [float]$w, [float]$h, [float]$r) {
    $r = [Math]::Min($r, [Math]::Min($w, $h) / 2)
    $d = 2 * $r
    $path = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $path.AddArc($x, $y, $d, $d, 180, 90)
    $path.AddArc(($x + $w - $d), $y, $d, $d, 270, 90)
    $path.AddArc(($x + $w - $d), ($y + $h - $d), $d, $d, 0, 90)
    $path.AddArc($x, ($y + $h - $d), $d, $d, 90, 90)
    $path.CloseFigure()
    return $path
}

function Paint-Round($graphics, $brush, $x, $y, $w, $h, $r) {
    $path = New-RoundPath $x $y $w $h $r
    $graphics.FillPath($brush, $path)
    $path.Dispose()
}

function New-BatteryImage([int]$level, [bool]$charging, [int]$size = 1) {
    $hi = [System.Drawing.Bitmap]::new(384, 192)
    $g = [System.Drawing.Graphics]::FromImage($hi)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.ScaleTransform(8, 8)
    $frame = [System.Drawing.Drawing2D.LinearGradientBrush]::new(
        [System.Drawing.PointF]::new(0, 2), [System.Drawing.PointF]::new(0, 22),
        [System.Drawing.ColorTranslator]::FromHtml('#626466'),
        [System.Drawing.ColorTranslator]::FromHtml('#505254'))
    $inside = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml('#fbfbfc'))

    # Reference proportions: a 2:1 body, moderate corners, thin white inset,
    # and a separate half-oval terminal with a visible two-pixel gap.
    Paint-Round $g $frame 1 2 40.5 20 5.8
    Paint-Round $g $inside 2.7 3.7 37.1 16.6 4.2
    $terminal = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $terminal.AddArc([single]41, [single]7.7, [single]5.2, [single]8.6, [single]270, [single]180)
    $terminal.CloseFigure()
    $g.FillPath($frame, $terminal)
    $terminal.Dispose()

    if ($level -ge 0 -and $level -gt 0) {
        $color = if ($level -lt 30) { '#ef4444' } else { '#20c878' }
        $fill = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml($color))
        Paint-Round $g $fill 4.5 5.5 (33.5 * $level / 100) 13 2.7
        $fill.Dispose()
    }
    if ($charging) {
        $bolt = [System.Drawing.PointF[]]@(
            [System.Drawing.PointF]::new(23.1, 5.5),
            [System.Drawing.PointF]::new(17.4, 12.7),
            [System.Drawing.PointF]::new(20.9, 12.7),
            [System.Drawing.PointF]::new(19.4, 18.5),
            [System.Drawing.PointF]::new(25.2, 10.4),
            [System.Drawing.PointF]::new(21.7, 10.4))
        $g.FillPolygon([System.Drawing.Brushes]::White, $bolt)
    }
    $frame.Dispose()
    $inside.Dispose()
    $g.Dispose()

    $image = [System.Drawing.Bitmap]::new((34 * $size), (17 * $size))
    $output = [System.Drawing.Graphics]::FromImage($image)
    $output.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
    $output.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $output.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $output.DrawImage($hi, [System.Drawing.Rectangle]::new(0, 0, $image.Width, $image.Height))
    $output.Dispose()
    $hi.Dispose()
    return $image
}

# Two groups of 102 frames: percentages 0-100 and an unknown/empty state.
$sheet = [System.Drawing.Bitmap]::new(34, (17 * 204))
$sheetGraphics = [System.Drawing.Graphics]::FromImage($sheet)
$sheetGraphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
foreach ($charging in @($false, $true)) {
    for ($index = 0; $index -le 101; $index++) {
        $level = if ($index -eq 101) { -1 } else { $index }
        $image = New-BatteryImage $level $charging
        $row = $index + $(if ($charging) { 102 } else { 0 })
        $sheetGraphics.DrawImageUnscaled($image, 0, ($row * 17))
        $image.Dispose()
    }
}
$sheetGraphics.Dispose()
$sheet.Save((Join-Path $assetDirectory 'battery-icons.png'), [System.Drawing.Imaging.ImageFormat]::Png)
$sheet.Dispose()

if ($PreviewPath) {
    $preview = [System.Drawing.Bitmap]::new(720, 170)
    $pg = [System.Drawing.Graphics]::FromImage($preview)
    $pg.Clear([System.Drawing.ColorTranslator]::FromHtml('#fbfbfc'))
    $pg.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    $font = [System.Drawing.Font]::new('Microsoft YaHei UI', 11)
    $labels = @('正常 80%', '充电 70%', '低电量 15%')
    $levels = @(80, 70, 15)
    for ($i = 0; $i -lt 3; $i++) {
        $large = New-BatteryImage $levels[$i] ($i -eq 1) 3
        $small = New-BatteryImage $levels[$i] ($i -eq 1)
        $pg.DrawImageUnscaled($large, (120 + 240 * $i - $large.Width / 2), 16)
        $pg.DrawImageUnscaled($small, (120 + 240 * $i - $small.Width / 2), 98)
        $pg.DrawString($labels[$i], $font, [System.Drawing.Brushes]::DimGray, [single](82 + 240 * $i), [single]133)
        $large.Dispose()
        $small.Dispose()
    }
    $font.Dispose()
    $pg.Dispose()
    $preview.Save($PreviewPath, [System.Drawing.Imaging.ImageFormat]::Png)
    $preview.Dispose()
}
Write-Output 'Rendered battery-icons.png (204 states, 34x17 each).'
