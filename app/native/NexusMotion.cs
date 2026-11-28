// Motion primitives for the native frontend: iOS-style spring easing, press and hover
// feedback, entrance reveals, animated battery ring, counting percentage, and iOS switch.
// Compiled with the .NET Framework C# 5 compiler, so newer language features are avoided.
using System;
using System.ComponentModel;
using System.Globalization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Effects;

namespace NexusUi
{
    /// <summary>
    /// Damped spring response over normalized time. Damping below 1 gives a small,
    /// single overshoot (0.78 ≈ 2 %, 0.6 ≈ 9 %); 1 or more is critically damped.
    /// </summary>
    public sealed class SpringEase : EasingFunctionBase
    {
        public static readonly DependencyProperty DampingProperty = DependencyProperty.Register(
            "Damping", typeof(double), typeof(SpringEase), new PropertyMetadata(0.8));

        public SpringEase()
        {
            // EaseIn makes WPF evaluate EaseInCore(t) directly instead of mirroring it.
            EasingMode = EasingMode.EaseIn;
        }

        public SpringEase(double damping) : this()
        {
            Damping = damping;
        }

        public double Damping
        {
            get { return (double)GetValue(DampingProperty); }
            set { SetValue(DampingProperty, value); }
        }

        protected override double EaseInCore(double t)
        {
            if (t <= 0) return 0;
            if (t >= 1) return 1;
            double zeta = Math.Max(0.2, Damping);
            if (zeta >= 1)
            {
                const double critical = 9.24; // e^-w (1 + w) ≈ 0.001 at t = 1
                return 1 - Math.Exp(-critical * t) * (1 + critical * t);
            }
            double omega = 6.9 / zeta; // envelope e^-(zeta*omega) ≈ 0.001 at t = 1
            double damped = omega * Math.Sqrt(1 - zeta * zeta);
            double decay = Math.Exp(-zeta * omega * t);
            return 1 - decay * (Math.Cos(damped * t) + zeta * omega / damped * Math.Sin(damped * t));
        }

        protected override Freezable CreateInstanceCore()
        {
            return new SpringEase();
        }
    }

    public static class Motion
    {
        /// <summary>Quick deceleration for fades and press-down.</summary>
        internal static readonly IEasingFunction Snappy = Frozen(new CubicEase { EasingMode = EasingMode.EaseOut });
        /// <summary>Symmetric curve for non-interactive dismissals.</summary>
        internal static readonly IEasingFunction Standard = Frozen(new CubicEase { EasingMode = EasingMode.EaseInOut });
        /// <summary>Critically damped settle for level changes (no overshoot).</summary>
        internal static readonly IEasingFunction Smooth = Frozen(new SpringEase(0.94));
        /// <summary>Sheet &amp; push navigation spring with a crisp, subtle settle.</summary>
        internal static readonly IEasingFunction Sheet = Frozen(new SpringEase(0.86));
        /// <summary>Default spring with a barely visible overshoot.</summary>
        internal static readonly IEasingFunction Gentle = Frozen(new SpringEase(0.78));
        /// <summary>Release spring for buttons, switches, and badges.</summary>
        internal static readonly IEasingFunction Bouncy = Frozen(new SpringEase(0.62));

        public static readonly DependencyProperty PressScaleProperty = DependencyProperty.RegisterAttached(
            "PressScale", typeof(double), typeof(Motion), new PropertyMetadata(1.0, OnInteractiveChanged));

        public static readonly DependencyProperty HoverScaleProperty = DependencyProperty.RegisterAttached(
            "HoverScale", typeof(double), typeof(Motion), new PropertyMetadata(1.0, OnInteractiveChanged));

        private static readonly DependencyProperty HookedProperty = DependencyProperty.RegisterAttached(
            "Hooked", typeof(bool), typeof(Motion), new PropertyMetadata(false));

        private static readonly DependencyProperty PressedProperty = DependencyProperty.RegisterAttached(
            "Pressed", typeof(bool), typeof(Motion), new PropertyMetadata(false));

        /// <summary>Honors the Windows "Animation effects" setting.</summary>
        internal static bool Enabled
        {
            get { return SystemParameters.ClientAreaAnimation; }
        }

        public static double GetPressScale(DependencyObject element) { return (double)element.GetValue(PressScaleProperty); }
        public static void SetPressScale(DependencyObject element, double value) { element.SetValue(PressScaleProperty, value); }
        public static double GetHoverScale(DependencyObject element) { return (double)element.GetValue(HoverScaleProperty); }
        public static void SetHoverScale(DependencyObject element, double value) { element.SetValue(HoverScaleProperty, value); }

        private static IEasingFunction Frozen(EasingFunctionBase ease)
        {
            ease.Freeze();
            return ease;
        }

        /// <summary>Animates from the displayed value to <paramref name="to"/> and holds it.</summary>
        internal static void To(IAnimatable target, DependencyProperty property, double to, double ms,
                                IEasingFunction ease, EventHandler done = null)
        {
            if (!Enabled || ms <= 0)
            {
                target.BeginAnimation(property, null);
                ((DependencyObject)target).SetValue(property, to);
                if (done != null) done(target, EventArgs.Empty);
                return;
            }
            var animation = new DoubleAnimation(to, new Duration(TimeSpan.FromMilliseconds(ms))) { EasingFunction = ease };
            if (done != null) animation.Completed += done;
            target.BeginAnimation(property, animation, HandoffBehavior.SnapshotAndReplace);
        }

        /// <summary>Pins the displayed value as the base value so it can be driven directly.</summary>
        internal static double Hold(IAnimatable target, DependencyProperty property)
        {
            var element = (DependencyObject)target;
            double value = (double)element.GetValue(property);
            target.BeginAnimation(property, null);
            element.SetValue(property, value);
            return value;
        }

        /// <summary>
        /// Plays from <paramref name="from"/> to the base value after a delay, then releases the
        /// property. The start value is applied immediately, so nothing flashes during the delay.
        /// </summary>
        internal static void Enter(IAnimatable target, DependencyProperty property, double from, double to,
                                   double delayMs, double ms, IEasingFunction ease)
        {
            if (!Enabled)
            {
                target.BeginAnimation(property, null);
                return;
            }
            var frames = new DoubleAnimationUsingKeyFrames { FillBehavior = FillBehavior.Stop };
            frames.KeyFrames.Add(new DiscreteDoubleKeyFrame(from, KeyTime.FromTimeSpan(TimeSpan.Zero)));
            if (delayMs > 0)
                frames.KeyFrames.Add(new DiscreteDoubleKeyFrame(from, KeyTime.FromTimeSpan(TimeSpan.FromMilliseconds(delayMs))));
            frames.KeyFrames.Add(new EasingDoubleKeyFrame(to, KeyTime.FromTimeSpan(TimeSpan.FromMilliseconds(delayMs + ms)), ease));
            target.BeginAnimation(property, frames, HandoffBehavior.SnapshotAndReplace);
        }

        /// <summary>Fades an element in while it rises and optionally grows into place.</summary>
        internal static void Reveal(UIElement element, double delayMs, double offsetY, double fromScale)
        {
            if (element == null || !Enabled) return;
            Enter(element, UIElement.OpacityProperty, 0, 1, delayMs, 240, Snappy);
            if (offsetY != 0)
                Enter(OffsetOf(element), TranslateTransform.YProperty, offsetY, 0, delayMs, 560, Gentle);
            if (fromScale != 1)
            {
                ScaleTransform scale = ScaleOf(element);
                Enter(scale, ScaleTransform.ScaleXProperty, fromScale, 1, delayMs, 560, Gentle);
                Enter(scale, ScaleTransform.ScaleYProperty, fromScale, 1, delayMs, 560, Gentle);
            }
        }

        /// <summary>Brief scale pulse used when a live value updates in place.</summary>
        internal static void Pulse(UIElement element, double peakScale)
        {
            if (element == null || !Enabled) return;
            ScaleTransform scale = ScaleOf(element);
            var frames = new DoubleAnimationUsingKeyFrames { FillBehavior = FillBehavior.Stop };
            frames.KeyFrames.Add(new EasingDoubleKeyFrame(peakScale, KeyTime.FromTimeSpan(TimeSpan.FromMilliseconds(130)), Snappy));
            frames.KeyFrames.Add(new EasingDoubleKeyFrame(1.0, KeyTime.FromTimeSpan(TimeSpan.FromMilliseconds(460)), Gentle));
            scale.BeginAnimation(ScaleTransform.ScaleXProperty, frames, HandoffBehavior.SnapshotAndReplace);
            scale.BeginAnimation(ScaleTransform.ScaleYProperty, frames, HandoffBehavior.SnapshotAndReplace);
        }

        internal static ScaleTransform ScaleOf(UIElement element)
        {
            return (ScaleTransform)Group(element).Children[0];
        }

        internal static TranslateTransform OffsetOf(UIElement element)
        {
            return (TranslateTransform)Group(element).Children[1];
        }

        private static TransformGroup Group(UIElement element)
        {
            var group = element.RenderTransform as TransformGroup;
            if (group != null && !group.IsFrozen && group.Children.Count == 2
                && group.Children[0] is ScaleTransform && group.Children[1] is TranslateTransform)
                return group;
            group = new TransformGroup();
            group.Children.Add(new ScaleTransform());
            group.Children.Add(new TranslateTransform());
            element.RenderTransform = group;
            element.RenderTransformOrigin = new Point(0.5, 0.5);
            return group;
        }

        private static void OnInteractiveChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
        {
            var element = sender as FrameworkElement;
            if (element == null || (bool)element.GetValue(HookedProperty)) return;
            element.SetValue(HookedProperty, true);
            element.MouseEnter += delegate { Settle(element, false); };
            element.MouseLeave += delegate
            {
                element.SetValue(PressedProperty, false);
                Settle(element, false);
            };
            var button = element as ButtonBase;
            if (button != null)
            {
                // IsPressed also covers Space/Enter presses and cancelling by dragging away.
                DependencyPropertyDescriptor.FromProperty(ButtonBase.IsPressedProperty, typeof(ButtonBase))
                    .AddValueChanged(button, delegate { Settle(button, !button.IsPressed); });
                return;
            }
            element.PreviewMouseLeftButtonDown += delegate
            {
                element.SetValue(PressedProperty, true);
                Settle(element, false);
            };
            element.PreviewMouseLeftButtonUp += delegate
            {
                bool wasPressed = (bool)element.GetValue(PressedProperty);
                element.SetValue(PressedProperty, false);
                Settle(element, wasPressed);
            };
        }

        private static void Settle(FrameworkElement element, bool released)
        {
            var button = element as ButtonBase;
            bool pressed = button != null ? button.IsPressed : (bool)element.GetValue(PressedProperty);
            double target = pressed ? GetPressScale(element)
                : element.IsMouseOver && element.IsEnabled ? GetHoverScale(element) : 1.0;
            ScaleTransform scale = ScaleOf(element);
            if (Math.Abs(scale.ScaleX - target) < 0.0005 && !scale.HasAnimatedProperties) return;
            // Press follows the pointer quickly; release springs back with a small overshoot.
            double ms = pressed ? 105 : released ? 580 : 380;
            IEasingFunction ease = pressed ? Snappy : released ? Bouncy : Gentle;
            To(scale, ScaleTransform.ScaleXProperty, target, ms, ease);
            To(scale, ScaleTransform.ScaleYProperty, target, ms, ease);
        }

        internal static double LevelDuration(double delta)
        {
            return Math.Min(640, 300 + Math.Abs(delta) * 3.6);
        }
    }

    /// <summary>Battery ring that sweeps to new levels and cross-fades its color.</summary>
    public sealed class BatteryRing : FrameworkElement
    {
        public static readonly DependencyProperty ValueProperty = DependencyProperty.Register(
            "Value", typeof(double), typeof(BatteryRing), new PropertyMetadata(0.0, OnValueChanged));

        public static readonly DependencyProperty StrokeProperty = DependencyProperty.Register(
            "Stroke", typeof(Brush), typeof(BatteryRing),
            new FrameworkPropertyMetadata(null, FrameworkPropertyMetadataOptions.AffectsRender, OnStrokeChanged));

        public static readonly DependencyProperty TrackProperty = DependencyProperty.Register(
            "Track", typeof(Brush), typeof(BatteryRing),
            new FrameworkPropertyMetadata(null, FrameworkPropertyMetadataOptions.AffectsRender));

        public static readonly DependencyProperty ThicknessProperty = DependencyProperty.Register(
            "Thickness", typeof(double), typeof(BatteryRing),
            new FrameworkPropertyMetadata(6.0, FrameworkPropertyMetadataOptions.AffectsRender));

        private static readonly DependencyProperty DisplayProperty = DependencyProperty.Register(
            "Display", typeof(double), typeof(BatteryRing),
            new FrameworkPropertyMetadata(0.0, FrameworkPropertyMetadataOptions.AffectsRender));

        private SolidColorBrush liveStroke;
        private bool shown;

        public BatteryRing()
        {
            Loaded += delegate { if (!shown) Replay(0); };
        }

        public double Value { get { return (double)GetValue(ValueProperty); } set { SetValue(ValueProperty, value); } }
        public Brush Stroke { get { return (Brush)GetValue(StrokeProperty); } set { SetValue(StrokeProperty, value); } }
        public Brush Track { get { return (Brush)GetValue(TrackProperty); } set { SetValue(TrackProperty, value); } }
        public double Thickness { get { return (double)GetValue(ThicknessProperty); } set { SetValue(ThicknessProperty, value); } }

        /// <summary>Sweeps from empty to the current level.</summary>
        public void Replay(double delayMs)
        {
            shown = true;
            double target = Value;
            if (!Motion.Enabled)
            {
                BeginAnimation(DisplayProperty, null);
                SetValue(DisplayProperty, target);
                return;
            }
            BeginAnimation(DisplayProperty, null);
            SetValue(DisplayProperty, 0.0);
            var sweep = new DoubleAnimation(0.0, target, new Duration(TimeSpan.FromMilliseconds(Motion.LevelDuration(target))))
            {
                BeginTime = TimeSpan.FromMilliseconds(Math.Max(0, delayMs)),
                EasingFunction = Motion.Smooth,
                FillBehavior = FillBehavior.HoldEnd
            };
            BeginAnimation(DisplayProperty, sweep, HandoffBehavior.SnapshotAndReplace);
        }

        private static void OnValueChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
        {
            var ring = (BatteryRing)sender;
            if (!ring.shown) return;
            double to = (double)args.NewValue;
            double from = (double)ring.GetValue(DisplayProperty);
            Motion.To(ring, DisplayProperty, to, Motion.LevelDuration(to - from), Motion.Smooth);
        }

        private static void OnStrokeChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
        {
            var ring = (BatteryRing)sender;
            var previous = args.OldValue as SolidColorBrush;
            var next = args.NewValue as SolidColorBrush;
            if (previous == null || next == null || !ring.shown || !Motion.Enabled)
            {
                ring.liveStroke = null;
                return;
            }
            Color from = ring.liveStroke != null ? ring.liveStroke.Color : previous.Color;
            var live = new SolidColorBrush(from);
            live.BeginAnimation(SolidColorBrush.ColorProperty,
                new ColorAnimation(next.Color, new Duration(TimeSpan.FromMilliseconds(380))) { EasingFunction = Motion.Snappy });
            ring.liveStroke = live;
        }

        protected override void OnRender(DrawingContext context)
        {
            double size = Math.Min(ActualWidth, ActualHeight);
            double thickness = Thickness;
            if (size <= thickness) return;
            double radius = (size - thickness) / 2;
            var center = new Point(ActualWidth / 2, ActualHeight / 2);
            if (Track != null) context.DrawEllipse(null, new Pen(Track, thickness), center, radius, radius);

            Brush stroke = liveStroke != null ? liveStroke : Stroke;
            double value = Math.Max(0, Math.Min(100, (double)GetValue(DisplayProperty)));
            if (stroke == null || value <= 0.2) return;
            var pen = new Pen(stroke, thickness) { StartLineCap = PenLineCap.Round, EndLineCap = PenLineCap.Round };
            if (value >= 99.8)
            {
                context.DrawEllipse(null, pen, center, radius, radius);
                return;
            }
            double angle = value / 100 * 2 * Math.PI;
            var geometry = new StreamGeometry();
            using (StreamGeometryContext figure = geometry.Open())
            {
                figure.BeginFigure(new Point(center.X, center.Y - radius), false, false);
                figure.ArcTo(new Point(center.X + Math.Sin(angle) * radius, center.Y - Math.Cos(angle) * radius),
                    new Size(radius, radius), 0, value > 50, SweepDirection.Clockwise, true, true);
            }
            geometry.Freeze();
            context.DrawGeometry(null, pen, geometry);
        }
    }

    /// <summary>Percentage text that counts to new values in step with <see cref="BatteryRing"/>.</summary>
    public sealed class CountingText : TextBlock
    {
        public static readonly DependencyProperty ValueProperty = DependencyProperty.Register(
            "Value", typeof(object), typeof(CountingText), new PropertyMetadata(null, OnValueChanged));

        public static readonly DependencyProperty SuffixProperty = DependencyProperty.Register(
            "Suffix", typeof(string), typeof(CountingText), new PropertyMetadata("%", OnFormatChanged));

        public static readonly DependencyProperty PlaceholderProperty = DependencyProperty.Register(
            "Placeholder", typeof(string), typeof(CountingText), new PropertyMetadata("--", OnFormatChanged));

        public static readonly DependencyProperty PulseOnChangeProperty = DependencyProperty.Register(
            "PulseOnChange", typeof(bool), typeof(CountingText), new PropertyMetadata(true));

        private static readonly DependencyProperty DisplayProperty = DependencyProperty.Register(
            "Display", typeof(double), typeof(CountingText), new PropertyMetadata(0.0, OnFormatChanged));

        private bool shown;

        public CountingText()
        {
            // Equal-width digits keep the text from jittering while it counts.
            System.Windows.Documents.Typography.SetNumeralAlignment(this, FontNumeralAlignment.Tabular);
            Loaded += delegate { if (!shown) Replay(0); };
        }

        public object Value { get { return GetValue(ValueProperty); } set { SetValue(ValueProperty, value); } }
        public string Suffix { get { return (string)GetValue(SuffixProperty); } set { SetValue(SuffixProperty, value); } }
        public string Placeholder { get { return (string)GetValue(PlaceholderProperty); } set { SetValue(PlaceholderProperty, value); } }
        public bool PulseOnChange { get { return (bool)GetValue(PulseOnChangeProperty); } set { SetValue(PulseOnChangeProperty, value); } }

        public void Replay(double delayMs)
        {
            shown = true;
            double? target = Number(Value);
            if (!target.HasValue)
            {
                BeginAnimation(DisplayProperty, null);
                Format();
                return;
            }
            if (!Motion.Enabled)
            {
                BeginAnimation(DisplayProperty, null);
                SetValue(DisplayProperty, target.Value);
                Format();
                return;
            }
            BeginAnimation(DisplayProperty, null);
            SetValue(DisplayProperty, 0.0);
            var count = new DoubleAnimation(0.0, target.Value, new Duration(TimeSpan.FromMilliseconds(Motion.LevelDuration(target.Value))))
            {
                BeginTime = TimeSpan.FromMilliseconds(Math.Max(0, delayMs)),
                EasingFunction = Motion.Smooth,
                FillBehavior = FillBehavior.HoldEnd
            };
            BeginAnimation(DisplayProperty, count, HandoffBehavior.SnapshotAndReplace);
            Format();
        }

        private static double? Number(object value)
        {
            if (value == null) return null;
            try { return Convert.ToDouble(value, CultureInfo.InvariantCulture); }
            catch (FormatException) { return null; }
            catch (InvalidCastException) { return null; }
        }

        private static void OnValueChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
        {
            var text = (CountingText)sender;
            double? previous = Number(args.OldValue), next = Number(args.NewValue);
            if (!text.shown || !next.HasValue)
            {
                text.Format();
                return;
            }
            if (!previous.HasValue)
            {
                text.Replay(0);
                return;
            }
            double from = (double)text.GetValue(DisplayProperty);
            if (Math.Abs(next.Value - from) >= 1 && text.PulseOnChange)
                Motion.Pulse(text, 1.06);
            Motion.To(text, DisplayProperty, next.Value, Motion.LevelDuration(next.Value - from), Motion.Smooth);
        }

        private static void OnFormatChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
        {
            ((CountingText)sender).Format();
        }

        private void Format()
        {
            string text;
            if (Number(Value).HasValue)
            {
                double display = Math.Max(0, Math.Min(100, (double)GetValue(DisplayProperty)));
                text = Math.Round(display).ToString("0", CultureInfo.InvariantCulture) + Suffix;
            }
            else text = Placeholder ?? String.Empty;
            if (Text != text) Text = text;
        }
    }

    /// <summary>
    /// Authentic iOS-style pill toggle switch (46x28) with spring-sliding circular thumb,
    /// press-stretch feedback, and smooth track color cross-fade.
    /// </summary>
    public sealed class IosSwitch : ToggleButton
    {
        public static readonly DependencyProperty OnBrushProperty = DependencyProperty.Register(
            "OnBrush", typeof(Brush), typeof(IosSwitch), new PropertyMetadata(null, OnVisualStateChanged));

        public static readonly DependencyProperty OffBrushProperty = DependencyProperty.Register(
            "OffBrush", typeof(Brush), typeof(IosSwitch), new PropertyMetadata(null, OnVisualStateChanged));

        private Border track;
        private Border thumb;
        private TranslateTransform thumbSlide;
        private SolidColorBrush trackBrush;
        private bool ready;

        public IosSwitch()
        {
            Width = 46;
            Height = 28;
            Cursor = System.Windows.Input.Cursors.Hand;
            Checked += delegate { Sync(true); };
            Unchecked += delegate { Sync(true); };
            Loaded += delegate
            {
                BuildVisuals();
                ready = true;
                Sync(false);
            };
            DependencyPropertyDescriptor.FromProperty(IsPressedProperty, typeof(ButtonBase))
                .AddValueChanged(this, delegate { if (ready) Sync(true); });
        }

        public Brush OnBrush { get { return (Brush)GetValue(OnBrushProperty); } set { SetValue(OnBrushProperty, value); } }
        public Brush OffBrush { get { return (Brush)GetValue(OffBrushProperty); } set { SetValue(OffBrushProperty, value); } }

        private static void OnVisualStateChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
        {
            var control = (IosSwitch)sender;
            if (control.ready) control.Sync(true);
        }

        private void BuildVisuals()
        {
            if (track != null) return;
            trackBrush = new SolidColorBrush(TargetColor());
            thumbSlide = new TranslateTransform();
            thumb = new Border
            {
                Width = 24,
                Height = 24,
                CornerRadius = new CornerRadius(12),
                Background = Brushes.White,
                HorizontalAlignment = HorizontalAlignment.Left,
                VerticalAlignment = VerticalAlignment.Center,
                Margin = new Thickness(2, 0, 0, 0),
                RenderTransform = thumbSlide,
                Effect = new DropShadowEffect
                {
                    BlurRadius = 6,
                    ShadowDepth = 1.5,
                    Direction = 270,
                    Opacity = 0.22,
                    RenderingBias = RenderingBias.Performance
                }
            };
            track = new Border
            {
                CornerRadius = new CornerRadius(14),
                Background = trackBrush,
                Child = thumb
            };
            // Replace the system CheckBox/ToggleButton chrome with our pill container.
            var factory = new FrameworkElementFactory(typeof(ContentPresenter));
            Template = new ControlTemplate(typeof(ToggleButton)) { VisualTree = factory };
            Content = track;
            ApplyTemplate();
        }

        private Color TargetColor()
        {
            var source = (IsChecked == true ? OnBrush : OffBrush) as SolidColorBrush;
            if (source != null) return source.Color;
            return IsChecked == true ? Color.FromRgb(0x30, 0xD1, 0x58) : Color.FromArgb(0x28, 0x0F, 0x23, 0x3C);
        }

        private void Sync(bool animate)
        {
            if (track == null) return;
            bool on = IsChecked == true;
            bool pressed = IsPressed && IsEnabled;
            double thumbWidth = pressed ? 27.0 : 24.0;
            double x = on ? (pressed ? 15.0 : 18.0) : 0.0;
            Color color = TargetColor();

            if (!animate || !Motion.Enabled)
            {
                thumb.BeginAnimation(WidthProperty, null);
                thumb.Width = thumbWidth;
                thumbSlide.BeginAnimation(TranslateTransform.XProperty, null);
                thumbSlide.X = x;
                trackBrush.BeginAnimation(SolidColorBrush.ColorProperty, null);
                trackBrush.Color = color;
                return;
            }

            Motion.To(thumb, WidthProperty, thumbWidth, pressed ? 110 : 320, pressed ? Motion.Snappy : Motion.Bouncy);
            Motion.To(thumbSlide, TranslateTransform.XProperty, x, 380, Motion.Bouncy);
            trackBrush.BeginAnimation(SolidColorBrush.ColorProperty,
                new ColorAnimation(color, new Duration(TimeSpan.FromMilliseconds(220))) { EasingFunction = Motion.Snappy },
                HandoffBehavior.SnapshotAndReplace);
        }
    }
}
