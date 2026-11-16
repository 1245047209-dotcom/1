param([string] $Destination)
Add-Type -AssemblyName System.Drawing
New-Item -ItemType Directory -Force -Path "$Destination/navigation","$Destination/view-switcher" | Out-Null
function PathRound([single]$x,[single]$y,[single]$w,[single]$h,[single]$r) {
    $p=[System.Drawing.Drawing2D.GraphicsPath]::new(); $d=2*$r
    $p.AddArc($x,$y,$d,$d,180,90); $p.AddArc($x+$w-$d,$y,$d,$d,270,90)
    $p.AddArc($x+$w-$d,$y+$h-$d,$d,$d,0,90); $p.AddArc($x,$y+$h-$d,$d,$d,90,90)
    $p.CloseFigure(); return $p
}
function PaintRound($x,$y,$w,$h,$r,[string]$hex,[int]$alpha=255) {
    $p=PathRound $x $y $w $h $r
    $b=[System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb($alpha,[System.Drawing.ColorTranslator]::FromHtml($hex)))
    $script:g.FillPath($b,$p); $b.Dispose(); $p.Dispose()
}
function StrokeRound($x,$y,$w,$h,$r,[string]$hex,[single]$weight=1) {
    $p=PathRound $x $y $w $h $r
    $pen=[System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml($hex),$weight)
    $script:g.DrawPath($pen,$p); $pen.Dispose(); $p.Dispose()
}
function Asset([string]$name,[int]$w,[int]$h,[scriptblock]$draw,[double]$ratio=.85) {
    $large=[System.Drawing.Bitmap]::new(($w*4),($h*4))
    $script:g=[System.Drawing.Graphics]::FromImage($large)
    $script:g.ScaleTransform(4,4); $script:g.SmoothingMode=[System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    & $draw; $script:g.Dispose()
    $pw=[int][Math]::Round($w*$ratio); $ph=[int][Math]::Round($h*$ratio)
    $small=[System.Drawing.Bitmap]::new($pw,$ph)
    $down=[System.Drawing.Graphics]::FromImage($small)
    $down.InterpolationMode=[System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $down.PixelOffsetMode=[System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $down.DrawImage($large,0,0,$pw,$ph); $down.Dispose(); $large.Dispose()
    $small.Save((Join-Path $Destination "$name.png"),[System.Drawing.Imaging.ImageFormat]::Png); $small.Dispose()
}
$toolbar={
    PaintRound 1 2 1070 41 15 '#172335' 4
    PaintRound 1.5 .5 1069 42 14 '#ffffff'
    StrokeRound 2 1 1068 41 13.5 '#e9edf2' .8
}
Asset 'navigation/toolbar' 1072 44 $toolbar 1
Asset 'navigation/toolbar-85' 1072 44 $toolbar
Asset 'navigation/hover' 48 38 { PaintRound 1 2 46 32 11 '#f5f7fa' }
Asset 'navigation/focus' 48 38 { StrokeRound 1.5 2.5 45 32 10 '#9ba9bb' 1.1 }
Asset 'navigation/selection' 30 3 { PaintRound 0 0 30 3 1.4 '#242a34' }
Asset 'view-switcher/track' 224 38 { PaintRound 0 0 224 38 1 '#ffffff' }
Asset 'view-switcher/thumb' 112 36 {
    PaintRound 3 4 106 30 10 '#17202e' 15
    PaintRound 3 3 106 30 10 '#242a34'
    StrokeRound 3.5 3.5 105 29 9.5 '#303743' .7
}
Asset 'view-switcher/hover' 106 30 { PaintRound 0 0 106 30 10 '#f4f6f8' }
Asset 'view-switcher/focus' 224 38 { StrokeRound 1 1 222 36 12 '#9ba9bb' 1.1 }
$colors=@{active='#ffffff';idle='#7b8695';hover='#303947'}
foreach($kind in @('all','single')) { foreach($state in @('active','idle','hover')) {
    Asset "view-switcher/$kind-$state" 16 16 {
        $pen=[System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml($colors[$state]),1.25)
        if($kind -eq 'all') {
            foreach($x in @(1,10)) { foreach($y in @(1,10)) {
                $p=PathRound $x $y 5 5 1.1; $script:g.DrawPath($pen,$p); $p.Dispose()
            } }
        } else {
            $p=PathRound 1 1.5 14 13 2.3; $script:g.DrawPath($pen,$p)
            $script:g.DrawLine($pen,6,2,6,14); $p.Dispose()
        }
        $pen.Dispose()
    }
} }
$colors=@{active='#2a3442';idle='#8793a3';hover='#46556a'}
foreach($kind in @('keyboard','mouse','beats-studio-buds','headphones','usb')) {
    foreach($state in @('active','idle','hover')) {
        Asset "navigation/$kind-$state" 24 24 {
            $tile=if($state -eq 'active'){'#e9eef4'}elseif($state -eq 'hover'){'#eef2f7'}else{'#f5f7fa'}
            PaintRound 0 0 24 24 7 $tile
            $pen=[System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml($colors[$state]),1.3)
            $pen.StartCap=$pen.EndCap=[System.Drawing.Drawing2D.LineCap]::Round
            $brush=[System.Drawing.SolidBrush]::new($pen.Color)
            if($kind -eq 'keyboard') {
                $p=PathRound 3.2 6.2 17.6 11.6 2.1; $script:g.DrawPath($pen,$p); $p.Dispose()
                foreach($x in @(6.2,10.1,14,17.6)) { $script:g.DrawLine($pen,$x,9.3,($x+.5),9.3) }
                foreach($x in @(6.2,10.1,14,17.6)) { $script:g.DrawLine($pen,$x,12.2,($x+.5),12.2) }
                $script:g.DrawLine($pen,8.8,15.1,15.4,15.1)
            } elseif($kind -eq 'mouse') {
                $p=PathRound 6.1 3 11.8 18 5.4; $script:g.DrawPath($pen,$p); $p.Dispose()
                $script:g.DrawLine($pen,12,3.4,12,8.5); $script:g.FillEllipse($brush,10.9,8,2.2,3.5)
            } elseif($kind -eq 'beats-studio-buds') {
                $script:g.DrawEllipse($pen,3,4,7.8,9); $script:g.DrawEllipse($pen,13.2,4,7.8,9)
                $script:g.FillEllipse($brush,5.4,7,2.7,3.2); $script:g.FillEllipse($brush,15.8,7,2.7,3.2)
                $script:g.DrawLine($pen,7.5,13,9.3,18.5); $script:g.DrawLine($pen,16.5,13,14.7,18.5)
            } elseif($kind -eq 'headphones') {
                $script:g.DrawArc($pen,4,3.5,16,18,180,180)
                PaintRound 3.3 11 4 8 1.7 $colors[$state]; PaintRound 16.7 11 4 8 1.7 $colors[$state]
            } else {
                $p=PathRound 7 5 10 15 2; $script:g.DrawPath($pen,$p); $p.Dispose()
                $script:g.DrawRectangle($pen,9,2.8,6,3); $script:g.DrawLine($pen,10,11,14,11)
            }
            $brush.Dispose(); $pen.Dispose()
        }
    }
}
Write-Output 'Prepared navigation surfaces and antialiased icons.'
