# Antialiased controls; no extra runtime dependencies are needed by the app.
Add-Type -AssemblyName System.Drawing
$assets = Join-Path (Split-Path $PSScriptRoot -Parent) 'assets'
function New-RoundPath([float]$x, [float]$y, [float]$w, [float]$h, [float]$r) {
    $d = 2 * $r
    $p = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $p.AddArc($x, $y, $d, $d, 180, 90)
    $p.AddArc(($x + $w - $d), $y, $d, $d, 270, 90)
    $p.AddArc(($x + $w - $d), ($y + $h - $d), $d, $d, 0, 90)
    $p.AddArc($x, ($y + $h - $d), $d, $d, 90, 90)
    $p.CloseFigure()
    return $p
}
function New-ScaledImage($source, [int]$w, [int]$h) {
    $result = [System.Drawing.Bitmap]::new($w, $h)
    $g = [System.Drawing.Graphics]::FromImage($result)
    $g.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.DrawImage($source, [System.Drawing.Rectangle]::new(0, 0, $w, $h))
    $g.Dispose()
    return $result
}

$colors = @('#17191d', '#30353c', '#080a0d', '#333840', '#168351', '#b94748')
$sheet = [System.Drawing.Bitmap]::new(104, (42 * 12))
$sg = [System.Drawing.Graphics]::FromImage($sheet)
for ($focus = 0; $focus -lt 2; $focus++) {
    for ($state = 0; $state -lt 6; $state++) {
        $hi = [System.Drawing.Bitmap]::new(832, 336)
        $g = [System.Drawing.Graphics]::FromImage($hi)
        $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $g.ScaleTransform(8, 8)
        if ($focus) {
            $ring = New-RoundPath 1.3 1.3 101.4 39.4 19.7
            $pen = [System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml('#aab2bd'), [single]1.2)
            $g.DrawPath($pen, $ring)
            $pen.Dispose()
            $ring.Dispose()
        }
        $offset = if ($state -eq 2) { 1 } else { 0 }
        $path = New-RoundPath 4 (4 + $offset) 96 34 17
        $brush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml($colors[$state]))
        $g.FillPath($brush, $path)
        $brush.Dispose()
        $path.Dispose()
        $g.Dispose()
        $image = New-ScaledImage $hi 104 42
        $sg.DrawImageUnscaled($image, 0, (($state + 6 * $focus) * 42))
        $image.Dispose()
        $hi.Dispose()
    }
}
$sg.Dispose()
$sheet.Save((Join-Path $assets 'refresh-button.png'), [System.Drawing.Imaging.ImageFormat]::Png)
$sheet.Dispose()

# Frames 0-2 are refresh/check/error; frames 3-26 are the loading animation.
$sheet = [System.Drawing.Bitmap]::new(18, (18 * 27))
$sg = [System.Drawing.Graphics]::FromImage($sheet)
for ($frame = 0; $frame -lt 27; $frame++) {
    $hi = [System.Drawing.Bitmap]::new(144, 144)
    $g = [System.Drawing.Graphics]::FromImage($hi)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.ScaleTransform(8, 8)
    $pen = [System.Drawing.Pen]::new([System.Drawing.Color]::White, [single]1.8)
    $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    if ($frame -eq 0) {
        $g.DrawArc($pen, [single]3.4, [single]3.4, [single]11.2, [single]11.2, [single]42, [single]285)
        $arrow = [System.Drawing.PointF[]]@(
            [System.Drawing.PointF]::new(14.8, 7.1),
            [System.Drawing.PointF]::new(10.6, 6.6),
            [System.Drawing.PointF]::new(14.2, 2.9))
        $g.FillPolygon([System.Drawing.Brushes]::White, $arrow)
    } elseif ($frame -eq 1) {
        $points = [System.Drawing.PointF[]]@(
            [System.Drawing.PointF]::new(3.8, 9),
            [System.Drawing.PointF]::new(7.4, 12.5),
            [System.Drawing.PointF]::new(14.2, 5.4))
        $g.DrawLines($pen, $points)
    } elseif ($frame -eq 2) {
        $g.DrawLine($pen, [single]9, [single]4, [single]9, [single]9.5)
        $g.FillEllipse([System.Drawing.Brushes]::White, [single]7.9, [single]12, [single]2.2, [single]2.2)
    } else {
        for ($segment = 0; $segment -lt 16; $segment++) {
            $pen.Color = [System.Drawing.Color]::FromArgb((40 + 13 * $segment), 255, 255, 255)
            $angle = ($frame - 3) * 15 + $segment * 16
            $g.DrawArc($pen, [single]3, [single]3, [single]12, [single]12, [single]$angle, [single]14)
        }
    }
    $pen.Dispose()
    $g.Dispose()
    $image = New-ScaledImage $hi 18 18
    $sg.DrawImageUnscaled($image, 0, ($frame * 18))
    $image.Dispose()
    $hi.Dispose()
}
$sg.Dispose()
$sheet.Save((Join-Path $assets 'refresh-symbols.png'), [System.Drawing.Imaging.ImageFormat]::Png)
$sheet.Dispose()
Write-Output 'Rendered smooth refresh button states and 24 loading frames.'
