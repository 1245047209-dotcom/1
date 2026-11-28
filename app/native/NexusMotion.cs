// Motion & Liquid Glass primitives for the native frontend: iOS-style spring easing,
// pointer-reactive specular refraction & press bloom, glossy liquid battery ring with
// center glass well, counting percentage, crystal iOS switch, liquid lens slider,
// smooth height expander, and animated battery sparkline chart.
// Compiled with the .NET Framework C# 5 compiler, so newer language features are avoided.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Effects;

namespace NexusUi
{
    /// <summary>
    /// Shared optical state for Liquid Glass surfaces, modulated by theme, backdrop state,
    /// user-configured glass_level (0..1), and GPU animation guardrails.
    /// </summary>
    public static class Glass
    {
        public static bool Active = true;
        public static bool Dark;
        public static double Level = 0.48;
        public static event EventHandler OpticalChanged;

        public static void NotifyChanged()
        {
            EventHandler handler = OpticalChanged;
            if (handler != null) handler(null, EventArgs.Empty);
        }
    }

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
                const double critical = 9.24;
                return 1 - Math.Exp(-critical * t) * (1 + critical * t);
            }
            double omega = 6.9 / zeta;
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
        public static bool GpuEnabled = true;

        internal static readonly IEasingFunction Snappy = Frozen(new CubicEase { EasingMode = EasingMode.EaseOut });
        internal static readonly IEasingFunction Standard = Frozen(new CubicEase { EasingMode = EasingMode.EaseInOut });
        internal static readonly IEasingFunction Smooth = Frozen(new SpringEase(0.94));
        internal static readonly IEasingFunction Sheet = Frozen(new SpringEase(0.86));
        internal static readonly IEasingFunction Gentle = Frozen(new SpringEase(0.78));
        internal static readonly IEasingFunction Bouncy = Frozen(new SpringEase(0.62));
        internal static readonly IEasingFunction Breath = Frozen(new SineEase { EasingMode = EasingMode.EaseInOut });

        public static readonly DependencyProperty PressScaleProperty = DependencyProperty.RegisterAttached(
            "PressScale", typeof(double), typeof(Motion), new PropertyMetadata(1.0, OnInteractiveChanged));

        public static readonly DependencyProperty HoverScaleProperty = DependencyProperty.RegisterAttached(
            "HoverScale", typeof(double), typeof(Motion), new PropertyMetadata(1.0, OnInteractiveChanged));

        public static readonly DependencyProperty SmoothScrollProperty = DependencyProperty.RegisterAttached(
            "SmoothScroll", typeof(bool), typeof(Motion), new PropertyMetadata(false, OnSmoothScrollChanged));

        private static readonly DependencyProperty SmoothScrollControllerProperty = DependencyProperty.RegisterAttached(
            "SmoothScrollController", typeof(SmoothScrollController), typeof(Motion), new PropertyMetadata(null));

        private static readonly DependencyProperty HookedProperty = DependencyProperty.RegisterAttached(
            "Hooked", typeof(bool), typeof(Motion), new PropertyMetadata(false));

        private static readonly DependencyProperty PressedProperty = DependencyProperty.RegisterAttached(
            "Pressed", typeof(bool), typeof(Motion), new PropertyMetadata(false));

        internal static bool Enabled
        {
            get { return GpuEnabled && SystemParameters.ClientAreaAnimation && !SystemParameters.HighContrast; }
        }

        public static double GetPressScale(DependencyObject element) { return (double)element.GetValue(PressScaleProperty); }
        public static void SetPressScale(DependencyObject element, double value) { element.SetValue(PressScaleProperty, value); }
        public static double GetHoverScale(DependencyObject element) { return (double)element.GetValue(HoverScaleProperty); }
        public static void SetHoverScale(DependencyObject element, double value) { element.SetValue(HoverScaleProperty, value); }
        public static bool GetSmoothScroll(DependencyObject element) { return (bool)element.GetValue(SmoothScrollProperty); }
        public static void SetSmoothScroll(DependencyObject element, bool value) { element.SetValue(SmoothScrollProperty, value); }

        private static IEasingFunction Frozen(EasingFunctionBase ease)
        {
            ease.Freeze();
            return ease;
        }

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

        internal static double Hold(IAnimatable target, DependencyProperty property)
        {
            var element = (DependencyObject)target;
            double value = (double)element.GetValue(property);
            target.BeginAnimation(property, null);
            element.SetValue(property, value);
            return value;
        }

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

        internal static void RevealX(UIElement element, double offsetX)
        {
            if (element == null || !Enabled) return;
            Enter(element, UIElement.OpacityProperty, 0, 1, 0, 220, Snappy);
            Enter(OffsetOf(element), TranslateTransform.XProperty, offsetX, 0, 0, 440, Sheet);
        }

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
            double ms = pressed ? 105 : released ? 580 : 380;
            IEasingFunction ease = pressed ? Snappy : released ? Bouncy : Gentle;
            To(scale, ScaleTransform.ScaleXProperty, target, ms, ease);
            To(scale, ScaleTransform.ScaleYProperty, target, ms, ease);
        }

        internal static double LevelDuration(double delta)
        {
            return Math.Min(640, 300 + Math.Abs(delta) * 3.6);
        }

        private static void OnSmoothScrollChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
        {
            var viewer = sender as ScrollViewer;
            if (viewer == null || (bool)args.NewValue != true) return;
            if (viewer.GetValue(SmoothScrollControllerProperty) == null)
                viewer.SetValue(SmoothScrollControllerProperty, new SmoothScrollController(viewer));
        }

        private sealed class SmoothScrollController
        {
            private readonly ScrollViewer viewer;
            private bool running;
            private double currentOffset;
            private double targetOffset;
            private double lastAppliedOffset;
            private double overscroll;
            private long lastTicks;

            public SmoothScrollController(ScrollViewer viewer)
            {
                this.viewer = viewer;
                viewer.PreviewMouseWheel += OnPreviewMouseWheel;
                viewer.ScrollChanged += OnScrollChanged;
                viewer.Unloaded += delegate { Stop(true); };
            }

            private void OnPreviewMouseWheel(object sender, MouseWheelEventArgs e)
            {
                if (!Motion.Enabled || viewer.ScrollableHeight <= 0.5) return;
                e.Handled = true;
                if (!running)
                {
                    currentOffset = targetOffset = lastAppliedOffset = viewer.VerticalOffset;
                    overscroll = 0.0;
                }
                double raw = targetOffset - e.Delta * 0.72;
                if (raw < 0.0)
                {
                    targetOffset = 0.0;
                    overscroll = Math.Max(-22.0, Math.Min(22.0, overscroll - raw * 0.22));
                }
                else if (raw > viewer.ScrollableHeight)
                {
                    targetOffset = viewer.ScrollableHeight;
                    overscroll = Math.Max(-22.0, Math.Min(22.0, overscroll - (raw - viewer.ScrollableHeight) * 0.22));
                }
                else
                {
                    targetOffset = raw;
                }
                Start();
            }

            private void OnScrollChanged(object sender, ScrollChangedEventArgs e)
            {
                if (running && Math.Abs(e.VerticalOffset - lastAppliedOffset) > 1.5)
                {
                    Stop(true);
                    currentOffset = targetOffset = lastAppliedOffset = e.VerticalOffset;
                }
            }

            private void Start()
            {
                if (running) return;
                running = true;
                lastTicks = Stopwatch.GetTimestamp();
                CompositionTarget.Rendering -= OnRendering;
                CompositionTarget.Rendering += OnRendering;
            }

            private void Stop(bool resetOverscroll)
            {
                if (running)
                {
                    running = false;
                    CompositionTarget.Rendering -= OnRendering;
                }
                if (resetOverscroll)
                {
                    overscroll = 0.0;
                    var content = viewer.Content as UIElement;
                    if (content != null) Motion.OffsetOf(content).Y = 0.0;
                }
            }

            private void OnRendering(object sender, EventArgs args)
            {
                if (!running || !viewer.IsVisible)
                {
                    Stop(true);
                    return;
                }
                long now = Stopwatch.GetTimestamp();
                double dt = Math.Min(0.05, Math.Max(0.001, (now - lastTicks) / (double)Stopwatch.Frequency));
                lastTicks = now;

                double alpha = 1.0 - Math.Exp(-dt * 12.5);
                currentOffset += (targetOffset - currentOffset) * alpha;
                overscroll *= Math.Exp(-dt * 14.0);

                double clamped = Math.Max(0.0, Math.Min(viewer.ScrollableHeight, currentOffset));
                lastAppliedOffset = clamped;
                viewer.ScrollToVerticalOffset(clamped);

                var content = viewer.Content as UIElement;
                if (content != null) Motion.OffsetOf(content).Y = overscroll;

                if (Math.Abs(targetOffset - currentOffset) < 0.35 && Math.Abs(overscroll) < 0.2)
                {
                    lastAppliedOffset = targetOffset;
                    viewer.ScrollToVerticalOffset(targetOffset);
                    Stop(true);
                }
            }
        }
    }

    /// <summary>
    /// Zero-idle-cost Liquid Glass optical overlay: renders a soft pointer-following radial
    /// sheen, localized Fresnel rim refraction near the cursor, and a liquid bloom on press.
    /// </summary>
    public sealed class SpecularOverlay : FrameworkElement
    {
        public static readonly DependencyProperty CornerRadiusProperty = DependencyProperty.Register(
            "CornerRadius", typeof(double), typeof(SpecularOverlay),
            new FrameworkPropertyMetadata(16.0, FrameworkPropertyMetadataOptions.AffectsRender));

        public static readonly DependencyProperty IntensityProperty = DependencyProperty.Register(
            "Intensity", typeof(double), typeof(SpecularOverlay),
            new FrameworkPropertyMetadata(1.0, FrameworkPropertyMetadataOptions.AffectsRender));

        public static readonly DependencyProperty RimOnlyProperty = DependencyProperty.Register(
            "RimOnly", typeof(bool), typeof(SpecularOverlay),
            new FrameworkPropertyMetadata(false, FrameworkPropertyMetadataOptions.AffectsRender));

        private static readonly DependencyProperty GlowProperty = DependencyProperty.Register(
            "Glow", typeof(double), typeof(SpecularOverlay),
            new FrameworkPropertyMetadata(0.0, FrameworkPropertyMetadataOptions.AffectsRender));

        private static readonly DependencyProperty BloomProperty = DependencyProperty.Register(
            "Bloom", typeof(double), typeof(SpecularOverlay),
            new FrameworkPropertyMetadata(0.0, FrameworkPropertyMetadataOptions.AffectsRender));

        private UIElement host;
        private Point pointer;
        private bool hasPointer;
        private bool activeHover;

        public SpecularOverlay()
        {
            IsHitTestVisible = false;
            Focusable = false;
            Loaded += OnLoaded;
            Unloaded += OnUnloaded;
        }

        public double CornerRadius
        {
            get { return (double)GetValue(CornerRadiusProperty); }
            set { SetValue(CornerRadiusProperty, value); }
        }

        public double Intensity
        {
            get { return (double)GetValue(IntensityProperty); }
            set { SetValue(IntensityProperty, value); }
        }

        public bool RimOnly
        {
            get { return (bool)GetValue(RimOnlyProperty); }
            set { SetValue(RimOnlyProperty, value); }
        }

        public void PreviewAt(double normX, double normY, double bloom)
        {
            double w = ActualWidth > 2 ? ActualWidth : 200;
            double h = ActualHeight > 2 ? ActualHeight : 80;
            pointer = new Point(w * normX, h * normY);
            hasPointer = true;
            BeginAnimation(GlowProperty, null);
            BeginAnimation(BloomProperty, null);
            SetValue(GlowProperty, 1.0);
            SetValue(BloomProperty, bloom);
            InvalidateVisual();
        }

        private void OnLoaded(object sender, RoutedEventArgs args)
        {
            Glass.OpticalChanged -= OnOpticalChanged;
            Glass.OpticalChanged += OnOpticalChanged;
            UIElement parent = VisualTreeHelper.GetParent(this) as UIElement;
            if (parent == host) return;
            DetachHost();
            host = parent;
            if (host == null) return;
            host.MouseEnter += OnHostMouseEnter;
            host.MouseMove += OnHostMouseMove;
            host.MouseLeave += OnHostMouseLeave;
            host.PreviewMouseLeftButtonDown += OnHostMouseDown;
            host.PreviewMouseLeftButtonUp += OnHostMouseUp;
        }

        private void OnUnloaded(object sender, RoutedEventArgs args)
        {
            Glass.OpticalChanged -= OnOpticalChanged;
            DetachHost();
        }

        private void DetachHost()
        {
            if (host == null) return;
            host.MouseEnter -= OnHostMouseEnter;
            host.MouseMove -= OnHostMouseMove;
            host.MouseLeave -= OnHostMouseLeave;
            host.PreviewMouseLeftButtonDown -= OnHostMouseDown;
            host.PreviewMouseLeftButtonUp -= OnHostMouseUp;
            host = null;
        }

        private void OnOpticalChanged(object sender, EventArgs args)
        {
            if (!Glass.Active && ((double)GetValue(GlowProperty) > 0 || (double)GetValue(BloomProperty) > 0))
            {
                activeHover = false;
                BeginAnimation(GlowProperty, null);
                BeginAnimation(BloomProperty, null);
                SetValue(GlowProperty, 0.0);
                SetValue(BloomProperty, 0.0);
            }
            else if (hasPointer && ((double)GetValue(GlowProperty) > 0.005 || (double)GetValue(BloomProperty) > 0.005))
            {
                InvalidateVisual();
            }
        }

        private void OnHostMouseEnter(object sender, MouseEventArgs args)
        {
            if (!Glass.Active || !Motion.Enabled) return;
            pointer = args.GetPosition(this);
            hasPointer = true;
            activeHover = true;
            Motion.To(this, GlowProperty, 1.0, 160, Motion.Snappy);
        }

        private void OnHostMouseMove(object sender, MouseEventArgs args)
        {
            if (!Glass.Active || !Motion.Enabled) return;
            Point next = args.GetPosition(this);
            if (hasPointer && Math.Abs(next.X - pointer.X) < 0.6 && Math.Abs(next.Y - pointer.Y) < 0.6)
                return;
            pointer = next;
            hasPointer = true;
            if (!activeHover)
            {
                activeHover = true;
                Motion.To(this, GlowProperty, 1.0, 160, Motion.Snappy);
            }
            InvalidateVisual();
        }

        private void OnHostMouseLeave(object sender, MouseEventArgs args)
        {
            activeHover = false;
            if ((double)GetValue(GlowProperty) > 0.001)
                Motion.To(this, GlowProperty, 0.0, 260, Motion.Snappy);
            if ((double)GetValue(BloomProperty) > 0.001)
                Motion.To(this, BloomProperty, 0.0, 260, Motion.Snappy);
        }

        private void OnHostMouseDown(object sender, MouseButtonEventArgs args)
        {
            if (!Glass.Active || !Motion.Enabled) return;
            pointer = args.GetPosition(this);
            hasPointer = true;
            Motion.To(this, BloomProperty, 1.0, 105, Motion.Snappy);
        }

        private void OnHostMouseUp(object sender, MouseButtonEventArgs args)
        {
            if ((double)GetValue(BloomProperty) > 0.001)
                Motion.To(this, BloomProperty, 0.0, 420, Motion.Gentle);
        }

        protected override void OnRender(DrawingContext context)
        {
            double glow = (double)GetValue(GlowProperty);
            double bloom = (double)GetValue(BloomProperty);
            if ((glow <= 0.004 && bloom <= 0.004) || !hasPointer || !Glass.Active) return;

            double w = ActualWidth, h = ActualHeight;
            if (w <= 2 || h <= 2) return;

            double k = Math.Max(0.2, Intensity) * (0.58 + 0.68 * Glass.Level);
            double span = Math.Max(w, h);
            double radius = Math.Min(w, h) / 2.0;
            double cr = Math.Max(0.0, Math.Min(radius, CornerRadius));

            if (!RimOnly)
            {
                double sheenR = Math.Max(64.0, span * (0.56 + 0.28 * bloom));
                int maxSheen = Glass.Dark ? 28 : 66;
                double rawSheen = Glass.Dark ? (9.5 * glow + 13.0 * bloom) * k : (20.0 * glow + 28.0 * bloom) * k;
                byte sheenA = (byte)Math.Max(0, Math.Min(maxSheen, (int)Math.Round(rawSheen)));
                if (sheenA > 0)
                {
                    Color tint = Glass.Dark
                        ? Color.FromArgb(sheenA, 0xDC, 0xEC, 0xFF)
                        : Color.FromArgb(sheenA, 0xFF, 0xFF, 0xFF);
                    var sheen = new RadialGradientBrush(tint, Color.FromArgb(0, tint.R, tint.G, tint.B))
                    {
                        MappingMode = BrushMappingMode.Absolute,
                        Center = pointer,
                        GradientOrigin = pointer,
                        RadiusX = sheenR,
                        RadiusY = sheenR
                    };
                    sheen.Freeze();
                    context.DrawRoundedRectangle(sheen, null, new Rect(0, 0, w, h), cr, cr);
                }
            }

            double rimR = Math.Max(46.0, Math.Min(145.0, span * 0.48)) * (1.0 + 0.32 * bloom);
            int maxRim = Glass.Dark ? 64 : 215;
            double rawRim = Glass.Dark ? (30.0 * glow + 20.0 * bloom) * k : (122.0 * glow + 54.0 * bloom) * k;
            byte rimA = (byte)Math.Max(0, Math.Min(maxRim, (int)Math.Round(rawRim)));
            if (rimA > 0 && w > 2 && h > 2)
            {
                Color rimColor = Glass.Dark
                    ? Color.FromArgb(rimA, 0xDE, 0xEE, 0xFF)
                    : Color.FromArgb(rimA, 0xFF, 0xFF, 0xFF);
                var rimBrush = new RadialGradientBrush(rimColor, Color.FromArgb(0, rimColor.R, rimColor.G, rimColor.B))
                {
                    MappingMode = BrushMappingMode.Absolute,
                    Center = pointer,
                    GradientOrigin = pointer,
                    RadiusX = rimR,
                    RadiusY = rimR
                };
                rimBrush.Freeze();
                double insetCr = Math.Max(0.0, cr - 0.5);
                context.DrawRoundedRectangle(null, new Pen(rimBrush, 1.0), new Rect(0.5, 0.5, w - 1.0, h - 1.0), insetCr, insetCr);
            }
        }
    }

    /// <summary>
    /// Battery ring with recessed liquid glass center medallion, grooved glass track,
    /// axial glossy tube highlight, specular tip droplet, and charging halo breath.
    /// </summary>
    public sealed class BatteryRing : FrameworkElement
    {
        public static readonly DependencyProperty ValueProperty = DependencyProperty.Register(
            "Value", typeof(double), typeof(BatteryRing), new PropertyMetadata(0.0, OnValueChanged));

        public static readonly DependencyProperty ChargingProperty = DependencyProperty.Register(
            "Charging", typeof(bool), typeof(BatteryRing), new PropertyMetadata(false, OnChargingChanged));

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

        private static readonly DependencyProperty HaloProperty = DependencyProperty.Register(
            "Halo", typeof(double), typeof(BatteryRing),
            new FrameworkPropertyMetadata(0.0, FrameworkPropertyMetadataOptions.AffectsRender));

        private SolidColorBrush liveStroke;
        private bool shown;

        public BatteryRing()
        {
            Loaded += delegate
            {
                Glass.OpticalChanged -= OnOpticalChanged;
                Glass.OpticalChanged += OnOpticalChanged;
                if (!shown) Replay(0);
                SyncBreathing();
            };
            Unloaded += delegate { Glass.OpticalChanged -= OnOpticalChanged; };
            IsVisibleChanged += delegate { SyncBreathing(); };
        }

        private void OnOpticalChanged(object sender, EventArgs args)
        {
            InvalidateVisual();
        }

        public double Value { get { return (double)GetValue(ValueProperty); } set { SetValue(ValueProperty, value); } }
        public bool Charging { get { return (bool)GetValue(ChargingProperty); } set { SetValue(ChargingProperty, value); } }
        public Brush Stroke { get { return (Brush)GetValue(StrokeProperty); } set { SetValue(StrokeProperty, value); } }
        public Brush Track { get { return (Brush)GetValue(TrackProperty); } set { SetValue(TrackProperty, value); } }
        public double Thickness { get { return (double)GetValue(ThicknessProperty); } set { SetValue(ThicknessProperty, value); } }

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

        private static void OnChargingChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
        {
            ((BatteryRing)sender).SyncBreathing();
        }

        private void SyncBreathing()
        {
            if (Charging && IsVisible && Motion.Enabled)
            {
                var pulse = new DoubleAnimation(0.06, 0.24, new Duration(TimeSpan.FromMilliseconds(1500)))
                {
                    AutoReverse = true,
                    RepeatBehavior = RepeatBehavior.Forever,
                    EasingFunction = Motion.Breath
                };
                BeginAnimation(HaloProperty, pulse, HandoffBehavior.SnapshotAndReplace);
            }
            else
            {
                BeginAnimation(HaloProperty, null);
                SetValue(HaloProperty, 0.0);
            }
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

            // Convex Liquid Glass center medallion behind the device glyph
            double wellRadius = radius - thickness * 0.96;
            if (Glass.Active && wellRadius > 6.0)
            {
                double strength = 0.65 + 0.55 * Glass.Level;
                byte topA = (byte)Math.Min(140, Math.Round((Glass.Dark ? 24 : 96) * strength));
                byte botA = (byte)Math.Min(60, Math.Round((Glass.Dark ? 7 : 26) * strength));
                Color baseTint = Glass.Dark ? Color.FromRgb(0xDC, 0xE8, 0xFA) : Colors.White;
                var wellFill = new LinearGradientBrush(
                    Color.FromArgb(topA, baseTint.R, baseTint.G, baseTint.B),
                    Color.FromArgb(botA, baseTint.R, baseTint.G, baseTint.B),
                    new Point(0.15, 0.05), new Point(0.85, 0.95));
                wellFill.Freeze();

                byte rimTopA = (byte)Math.Min(185, Math.Round((Glass.Dark ? 30 : 138) * strength));
                byte rimBotA = (byte)Math.Min(80, Math.Round((Glass.Dark ? 10 : 48) * strength));
                var wellRim = new LinearGradientBrush { StartPoint = new Point(0, 0), EndPoint = new Point(1, 1) };
                wellRim.GradientStops.Add(new GradientStop(Color.FromArgb(rimTopA, baseTint.R, baseTint.G, baseTint.B), 0.0));
                wellRim.GradientStops.Add(new GradientStop(Color.FromArgb(0, baseTint.R, baseTint.G, baseTint.B), 0.55));
                wellRim.GradientStops.Add(new GradientStop(Color.FromArgb(rimBotA, baseTint.R, baseTint.G, baseTint.B), 1.0));
                wellRim.Freeze();

                context.DrawEllipse(wellFill, new Pen(wellRim, 0.85), center, wellRadius, wellRadius);
            }

            if (Track != null)
            {
                context.DrawEllipse(null, new Pen(Track, thickness), center, radius, radius);
                if (Glass.Active)
                {
                    byte grooveA = (byte)(Glass.Dark ? 16 : 52);
                    var grooveRim = new LinearGradientBrush(
                        Color.FromArgb(grooveA, 0xFF, 0xFF, 0xFF),
                        Color.FromArgb(0, 0xFF, 0xFF, 0xFF),
                        new Point(0, 0), new Point(0.8, 0.85));
                    grooveRim.Freeze();
                    context.DrawEllipse(null, new Pen(grooveRim, 0.8), center, radius + thickness * 0.42, radius + thickness * 0.42);
                }
            }

            Brush stroke = liveStroke != null ? liveStroke : Stroke;
            double value = Math.Max(0, Math.Min(100, (double)GetValue(DisplayProperty)));
            if (stroke == null || value <= 0.2) return;

            Geometry arc = null;
            bool full = value >= 99.8;
            Point tipPoint = new Point(center.X, center.Y - radius);
            if (!full)
            {
                double angle = value / 100 * 2 * Math.PI;
                tipPoint = new Point(center.X + Math.Sin(angle) * radius, center.Y - Math.Cos(angle) * radius);
                var stream = new StreamGeometry();
                using (StreamGeometryContext figure = stream.Open())
                {
                    figure.BeginFigure(new Point(center.X, center.Y - radius), false, false);
                    figure.ArcTo(tipPoint, new Size(radius, radius), 0, value > 50, SweepDirection.Clockwise, true, true);
                }
                stream.Freeze();
                arc = stream;
            }

            double halo = (double)GetValue(HaloProperty);
            if (halo > 0.01)
            {
                var solid = stroke as SolidColorBrush;
                if (solid != null)
                {
                    var glowBrush = new SolidColorBrush(Color.FromArgb(
                        (byte)Math.Round(halo * 255), solid.Color.R, solid.Color.G, solid.Color.B));
                    var glowPen = new Pen(glowBrush, thickness + 4.5)
                    {
                        StartLineCap = PenLineCap.Round,
                        EndLineCap = PenLineCap.Round
                    };
                    if (full) context.DrawEllipse(null, glowPen, center, radius, radius);
                    else context.DrawGeometry(null, glowPen, arc);
                }
            }

            var pen = new Pen(stroke, thickness) { StartLineCap = PenLineCap.Round, EndLineCap = PenLineCap.Round };
            if (full) context.DrawEllipse(null, pen, center, radius, radius);
            else context.DrawGeometry(null, pen, arc);

            // Liquid glass tube specular highlight + rounded tip pearl
            if (Glass.Active)
            {
                byte glossTop = (byte)(Glass.Dark ? 68 : 98);
                byte glossBot = (byte)(Glass.Dark ? 10 : 20);
                var glossBrush = new LinearGradientBrush(
                    Color.FromArgb(glossTop, 0xFF, 0xFF, 0xFF),
                    Color.FromArgb(glossBot, 0xFF, 0xFF, 0xFF),
                    new Point(0.2, 0.0), new Point(0.8, 1.0));
                glossBrush.Freeze();
                var glossPen = new Pen(glossBrush, Math.Max(1.2, thickness * 0.34))
                {
                    StartLineCap = PenLineCap.Round,
                    EndLineCap = PenLineCap.Round
                };
                if (full) context.DrawEllipse(null, glossPen, center, radius - thickness * 0.14, radius - thickness * 0.14);
                else context.DrawGeometry(null, glossPen, arc);

                if (!full && value > 3.0)
                {
                    var tipPearl = new SolidColorBrush(Color.FromArgb((byte)(Glass.Dark ? 92 : 132), 0xFF, 0xFF, 0xFF));
                    tipPearl.Freeze();
                    context.DrawEllipse(tipPearl, null, tipPoint, thickness * 0.24, thickness * 0.24);
                }
            }
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
    /// iOS-style pill toggle switch (46x28) upgraded with a convex crystal pearl thumb
    /// that stretches into a translucent liquid droplet while pressed.
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
        private SolidColorBrush thumbFill;
        private bool ready;

        public IosSwitch()
        {
            Width = 46;
            Height = 28;
            Cursor = Cursors.Hand;
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
            thumbFill = new SolidColorBrush(Color.FromArgb(0xFA, 0xFF, 0xFF, 0xFF));
            thumbSlide = new TranslateTransform();

            var thumbRim = new LinearGradientBrush(
                Color.FromArgb(0xF0, 0xFF, 0xFF, 0xFF),
                Color.FromArgb(0x66, 0xDC, 0xE8, 0xFA),
                new Point(0, 0), new Point(1, 1));
            thumbRim.Freeze();

            var thumbGloss = new LinearGradientBrush(
                Color.FromArgb(0x88, 0xFF, 0xFF, 0xFF),
                Color.FromArgb(0x00, 0xFF, 0xFF, 0xFF),
                new Point(0, 0), new Point(0, 1));
            thumbGloss.Freeze();

            thumb = new Border
            {
                Width = 24,
                Height = 24,
                CornerRadius = new CornerRadius(12),
                Background = thumbFill,
                BorderBrush = thumbRim,
                BorderThickness = new Thickness(1),
                HorizontalAlignment = HorizontalAlignment.Left,
                VerticalAlignment = VerticalAlignment.Center,
                Margin = new Thickness(1, 0, 0, 0),
                RenderTransform = thumbSlide,
                Child = new Border
                {
                    CornerRadius = new CornerRadius(11),
                    Margin = new Thickness(1, 1, 1, 8),
                    Background = thumbGloss,
                    IsHitTestVisible = false
                },
                Effect = new DropShadowEffect
                {
                    BlurRadius = 6,
                    ShadowDepth = 1.5,
                    Direction = 270,
                    Opacity = 0.22,
                    RenderingBias = RenderingBias.Performance
                }
            };

            var trackRim = new LinearGradientBrush(
                Color.FromArgb(0x2E, 0xFF, 0xFF, 0xFF),
                Color.FromArgb(0x0A, 0xFF, 0xFF, 0xFF),
                new Point(0, 0), new Point(1, 1));
            trackRim.Freeze();

            track = new Border
            {
                CornerRadius = new CornerRadius(14),
                Background = trackBrush,
                BorderBrush = trackRim,
                BorderThickness = new Thickness(1),
                Child = thumb
            };
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
            double thumbWidth = pressed ? 28.0 : 24.0;
            double x = on ? (pressed ? 14.0 : 18.0) : 0.0;
            Color color = TargetColor();
            Color targetThumb = pressed && Glass.Active
                ? Color.FromArgb(0xB8, 0xF5, 0xFA, 0xFF)
                : Color.FromArgb(0xFA, 0xFF, 0xFF, 0xFF);

            if (!animate || !Motion.Enabled)
            {
                thumb.BeginAnimation(WidthProperty, null);
                thumb.Width = thumbWidth;
                thumbSlide.BeginAnimation(TranslateTransform.XProperty, null);
                thumbSlide.X = x;
                trackBrush.BeginAnimation(SolidColorBrush.ColorProperty, null);
                trackBrush.Color = color;
                thumbFill.BeginAnimation(SolidColorBrush.ColorProperty, null);
                thumbFill.Color = targetThumb;
                return;
            }

            Motion.To(thumb, WidthProperty, thumbWidth, pressed ? 105 : 320, pressed ? Motion.Snappy : Motion.Bouncy);
            Motion.To(thumbSlide, TranslateTransform.XProperty, x, 380, Motion.Bouncy);
            trackBrush.BeginAnimation(SolidColorBrush.ColorProperty,
                new ColorAnimation(color, new Duration(TimeSpan.FromMilliseconds(220))) { EasingFunction = Motion.Snappy },
                HandoffBehavior.SnapshotAndReplace);
            thumbFill.BeginAnimation(SolidColorBrush.ColorProperty,
                new ColorAnimation(targetThumb, new Duration(TimeSpan.FromMilliseconds(pressed ? 95 : 240))) { EasingFunction = Motion.Snappy },
                HandoffBehavior.SnapshotAndReplace);
        }
    }

    /// <summary>
    /// Interactive iOS 26 Liquid Glass slider (0.0 .. 1.0). Renders a sleek elongated frosted
    /// capsule at rest and morphs into a transparent chromatic-Fresnel glass lens while dragged.
    /// </summary>
    public sealed class LiquidSlider : FrameworkElement
    {
        public static readonly DependencyProperty ValueProperty = DependencyProperty.Register(
            "Value", typeof(double), typeof(LiquidSlider),
            new FrameworkPropertyMetadata(0.48, FrameworkPropertyMetadataOptions.BindsTwoWayByDefault, OnValueChanged));

        public static readonly DependencyProperty TrackBrushProperty = DependencyProperty.Register(
            "TrackBrush", typeof(Brush), typeof(LiquidSlider), new PropertyMetadata(null, OnBrushChanged));

        public static readonly DependencyProperty FillBrushProperty = DependencyProperty.Register(
            "FillBrush", typeof(Brush), typeof(LiquidSlider), new PropertyMetadata(null, OnBrushChanged));

        private Grid root;
        private Border trackHost;
        private Border fillBar;
        private Grid thumb;
        private Border thumbBody;
        private Border lensRim;
        private SolidColorBrush thumbFill;
        private SolidColorBrush thumbRim;
        private SolidColorBrush trackEdgeBrush;
        private TranslateTransform thumbSlide;
        private ScaleTransform thumbScale;
        private bool dragging;
        private bool previewDragging;
        private double lastPointerX;
        private double dragStretch;

        public LiquidSlider()
        {
            Height = 34;
            MinWidth = 120;
            Cursor = Cursors.Hand;
            Focusable = true;
            FocusVisualStyle = null;
            Loaded += OnLoaded;
            Unloaded += OnUnloaded;
            SizeChanged += delegate { SyncPosition(false); };
        }

        private bool IsActiveDrag
        {
            get { return dragging || previewDragging; }
        }

        public double Value
        {
            get { return (double)GetValue(ValueProperty); }
            set { SetValue(ValueProperty, Math.Max(0.0, Math.Min(1.0, value))); }
        }

        public Brush TrackBrush
        {
            get { return (Brush)GetValue(TrackBrushProperty); }
            set { SetValue(TrackBrushProperty, value); }
        }

        public Brush FillBrush
        {
            get { return (Brush)GetValue(FillBrushProperty); }
            set { SetValue(FillBrushProperty, value); }
        }

        public void SetPreviewDragging(bool active)
        {
            previewDragging = active;
            dragStretch = 0.0;
            SyncLensState(false);
        }

        private void OnLoaded(object sender, RoutedEventArgs args)
        {
            Glass.OpticalChanged -= OnOpticalChanged;
            Glass.OpticalChanged += OnOpticalChanged;
            BuildVisuals();
            SyncVisuals(false);
        }

        private void OnUnloaded(object sender, RoutedEventArgs args)
        {
            Glass.OpticalChanged -= OnOpticalChanged;
        }

        private void OnOpticalChanged(object sender, EventArgs args)
        {
            if (trackEdgeBrush != null)
                trackEdgeBrush.Color = Glass.Dark ? Color.FromArgb(0x16, 0xDC, 0xE8, 0xFA) : Color.FromArgb(0x14, 0x0F, 0x23, 0x3C);
            SyncLensState(false);
        }

        private static void OnValueChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
        {
            var slider = (LiquidSlider)sender;
            slider.SyncPosition(!slider.dragging);
        }

        private static void OnBrushChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
        {
            var slider = (LiquidSlider)sender;
            if (slider.trackHost != null) slider.trackHost.Background = slider.TrackBrush;
            if (slider.fillBar != null) slider.fillBar.Background = slider.FillBrush;
        }

        private void BuildVisuals()
        {
            if (root != null) return;
            root = new Grid { Background = Brushes.Transparent };

            fillBar = new Border
            {
                HorizontalAlignment = HorizontalAlignment.Left,
                CornerRadius = new CornerRadius(3),
                Background = FillBrush ?? new SolidColorBrush(Color.FromRgb(0x25, 0x84, 0xF4))
            };

            trackEdgeBrush = new SolidColorBrush(Glass.Dark
                ? Color.FromArgb(0x16, 0xDC, 0xE8, 0xFA)
                : Color.FromArgb(0x14, 0x0F, 0x23, 0x3C));

            trackHost = new Border
            {
                Height = 6,
                CornerRadius = new CornerRadius(3),
                Margin = new Thickness(15, 0, 15, 0),
                VerticalAlignment = VerticalAlignment.Center,
                Background = TrackBrush ?? new SolidColorBrush(Color.FromArgb(0x28, 0x0F, 0x23, 0x3C)),
                BorderBrush = trackEdgeBrush,
                BorderThickness = new Thickness(1),
                ClipToBounds = true,
                Child = fillBar
            };

            thumbFill = new SolidColorBrush(Color.FromArgb(0xF5, 0xFF, 0xFF, 0xFF));
            thumbRim = new SolidColorBrush(Color.FromArgb(0x66, 0xE8, 0xF2, 0xFF));
            thumbScale = new ScaleTransform(1.0, 1.0);
            thumbSlide = new TranslateTransform();
            var group = new TransformGroup();
            group.Children.Add(thumbScale);
            group.Children.Add(thumbSlide);

            thumbBody = new Border
            {
                CornerRadius = new CornerRadius(9),
                Background = thumbFill,
                BorderBrush = thumbRim,
                BorderThickness = new Thickness(1.0),
                Effect = new DropShadowEffect
                {
                    BlurRadius = 6,
                    ShadowDepth = 1.0,
                    Direction = 270,
                    Opacity = 0.18,
                    RenderingBias = RenderingBias.Performance
                }
            };

            var chromaticRim = new LinearGradientBrush { StartPoint = new Point(0, 0), EndPoint = new Point(1, 1) };
            chromaticRim.GradientStops.Add(new GradientStop(Color.FromArgb(0xF0, 0xFF, 0xFF, 0xFF), 0.0));
            chromaticRim.GradientStops.Add(new GradientStop(Color.FromArgb(0x58, 0x90, 0xD0, 0xFF), 0.48));
            chromaticRim.GradientStops.Add(new GradientStop(Color.FromArgb(0xAA, 0xFF, 0xFF, 0xFF), 1.0));
            chromaticRim.Freeze();

            var innerCaustic = new LinearGradientBrush { StartPoint = new Point(0.15, 0), EndPoint = new Point(0.85, 1) };
            innerCaustic.GradientStops.Add(new GradientStop(Color.FromArgb(0x40, 0xFF, 0xFF, 0xFF), 0.0));
            innerCaustic.GradientStops.Add(new GradientStop(Color.FromArgb(0x06, 0xFF, 0xFF, 0xFF), 0.45));
            innerCaustic.GradientStops.Add(new GradientStop(Color.FromArgb(0x1C, 0x88, 0xC8, 0xFF), 1.0));
            innerCaustic.Freeze();

            lensRim = new Border
            {
                CornerRadius = new CornerRadius(9),
                BorderBrush = chromaticRim,
                BorderThickness = new Thickness(1.25),
                Opacity = 0.0,
                IsHitTestVisible = false,
                Child = new Border
                {
                    CornerRadius = new CornerRadius(7.8),
                    Margin = new Thickness(1.2),
                    Background = innerCaustic,
                    IsHitTestVisible = false
                }
            };

            thumb = new Grid
            {
                Width = 30,
                Height = 18,
                HorizontalAlignment = HorizontalAlignment.Left,
                VerticalAlignment = VerticalAlignment.Center,
                RenderTransformOrigin = new Point(0.5, 0.5),
                RenderTransform = group,
                Children = { thumbBody, lensRim }
            };

            root.Children.Add(trackHost);
            root.Children.Add(thumb);
            AddVisualChild(root);
            AddLogicalChild(root);
        }

        protected override int VisualChildrenCount
        {
            get { return root != null ? 1 : 0; }
        }

        protected override Visual GetVisualChild(int index)
        {
            if (root == null || index != 0) throw new ArgumentOutOfRangeException("index");
            return root;
        }

        protected override Size MeasureOverride(Size constraint)
        {
            BuildVisuals();
            root.Measure(constraint);
            return new Size(
                Double.IsInfinity(constraint.Width) ? Math.Max(140, root.DesiredSize.Width) : constraint.Width,
                34);
        }

        protected override Size ArrangeOverride(Size arrangeBounds)
        {
            if (root != null) root.Arrange(new Rect(arrangeBounds));
            SyncPosition(false);
            return arrangeBounds;
        }

        protected override void OnMouseEnter(MouseEventArgs e)
        {
            base.OnMouseEnter(e);
            if (!IsActiveDrag) SyncLensState(true);
        }

        protected override void OnMouseLeave(MouseEventArgs e)
        {
            base.OnMouseLeave(e);
            if (!IsActiveDrag) SyncLensState(true);
        }

        protected override void OnMouseLeftButtonDown(MouseButtonEventArgs e)
        {
            base.OnMouseLeftButtonDown(e);
            Focus();
            dragging = true;
            dragStretch = 0.0;
            Point pt = e.GetPosition(this);
            lastPointerX = pt.X;
            CaptureMouse();
            UpdateFromPoint(pt);
            SyncLensState(true);
            e.Handled = true;
        }

        protected override void OnMouseMove(MouseEventArgs e)
        {
            base.OnMouseMove(e);
            if (!dragging) return;
            Point pt = e.GetPosition(this);
            double dx = pt.X - lastPointerX;
            lastPointerX = pt.X;
            dragStretch = Math.Min(0.07, Math.Abs(dx) * 0.008);
            UpdateFromPoint(pt);
            if (thumbScale != null)
                Motion.To(thumbScale, ScaleTransform.ScaleXProperty, 1.32 + dragStretch, 90, Motion.Snappy);
            e.Handled = true;
        }

        protected override void OnMouseLeftButtonUp(MouseButtonEventArgs e)
        {
            base.OnMouseLeftButtonUp(e);
            if (!dragging) return;
            dragging = false;
            dragStretch = 0.0;
            ReleaseMouseCapture();
            SyncLensState(true);
            e.Handled = true;
        }

        protected override void OnLostMouseCapture(MouseEventArgs e)
        {
            base.OnLostMouseCapture(e);
            if (!dragging) return;
            dragging = false;
            dragStretch = 0.0;
            SyncLensState(true);
        }

        protected override void OnMouseWheel(MouseWheelEventArgs e)
        {
            base.OnMouseWheel(e);
            if (!IsKeyboardFocused) return;
            double delta = e.Delta > 0 ? 0.04 : -0.04;
            SetValueFromUser(Value + delta);
            e.Handled = true;
        }

        protected override void OnKeyDown(KeyEventArgs e)
        {
            base.OnKeyDown(e);
            if (e.Key == Key.Left || e.Key == Key.Down)
            {
                SetValueFromUser(Value - 0.05);
                e.Handled = true;
            }
            else if (e.Key == Key.Right || e.Key == Key.Up)
            {
                SetValueFromUser(Value + 0.05);
                e.Handled = true;
            }
            else if (e.Key == Key.Home)
            {
                SetValueFromUser(0.0);
                e.Handled = true;
            }
            else if (e.Key == Key.End)
            {
                SetValueFromUser(1.0);
                e.Handled = true;
            }
        }

        private void UpdateFromPoint(Point pt)
        {
            double usable = Math.Max(1.0, ActualWidth - 30.0);
            double ratio = Math.Max(0.0, Math.Min(1.0, (pt.X - 15.0) / usable));
            SetValueFromUser(Math.Round(ratio * 100.0) / 100.0);
        }

        private void SetValueFromUser(double next)
        {
            double clamped = Math.Max(0.0, Math.Min(1.0, next));
            if (Math.Abs(clamped - Value) > 0.0005)
                Value = clamped;
        }

        private void SyncVisuals(bool animate)
        {
            SyncPosition(animate);
            SyncLensState(animate);
        }

        private void SyncPosition(bool animate)
        {
            if (root == null || ActualWidth <= 30) return;
            double ratio = Math.Max(0.0, Math.Min(1.0, Value));
            double usable = Math.Max(0.0, ActualWidth - 30.0);
            double x = usable * ratio;
            double fillWidth = Math.Max(0.0, x);

            if (!animate || !Motion.Enabled || dragging)
            {
                thumbSlide.BeginAnimation(TranslateTransform.XProperty, null);
                thumbSlide.X = x;
                fillBar.BeginAnimation(WidthProperty, null);
                fillBar.Width = fillWidth;
                return;
            }

            Motion.To(thumbSlide, TranslateTransform.XProperty, x, 220, Motion.Gentle);
            Motion.To(fillBar, WidthProperty, fillWidth, 220, Motion.Gentle);
        }

        private void SyncLensState(bool animate)
        {
            if (thumb == null || thumbBody == null) return;
            bool active = IsActiveDrag;
            bool hover = !active && IsMouseOver && IsEnabled;
            double sx = active ? (1.32 + dragStretch) : hover ? 1.06 : 1.0;
            double sy = active ? 1.38 : hover ? 1.06 : 1.0;
            double targetTrackH = active ? 7.5 : 6.0;
            double targetLensOpacity = active ? 1.0 : 0.0;

            Color targetFill = active
                ? (Glass.Dark ? Color.FromArgb(0x1E, 0xDC, 0xEE, 0xFF) : Color.FromArgb(0x32, 0xFF, 0xFF, 0xFF))
                : (Glass.Dark ? Color.FromArgb(0xDC, 0xF0, 0xF6, 0xFF) : Color.FromArgb(0xF5, 0xFF, 0xFF, 0xFF));
            Color targetRim = active
                ? (Glass.Dark ? Color.FromArgb(0x10, 0xFF, 0xFF, 0xFF) : Color.FromArgb(0x18, 0xFF, 0xFF, 0xFF))
                : (Glass.Dark ? Color.FromArgb(0x66, 0xE8, 0xF2, 0xFF) : Color.FromArgb(0x26, 0x0F, 0x23, 0x3C));

            trackHost.CornerRadius = new CornerRadius(active ? 3.75 : 3.0);
            fillBar.CornerRadius = new CornerRadius(active ? 3.75 : 3.0);

            if (!animate || !Motion.Enabled)
            {
                thumbScale.BeginAnimation(ScaleTransform.ScaleXProperty, null);
                thumbScale.BeginAnimation(ScaleTransform.ScaleYProperty, null);
                thumbScale.ScaleX = sx;
                thumbScale.ScaleY = sy;
                trackHost.BeginAnimation(HeightProperty, null);
                trackHost.Height = targetTrackH;
                lensRim.BeginAnimation(OpacityProperty, null);
                lensRim.Opacity = targetLensOpacity;
                thumbFill.BeginAnimation(SolidColorBrush.ColorProperty, null);
                thumbFill.Color = targetFill;
                thumbRim.BeginAnimation(SolidColorBrush.ColorProperty, null);
                thumbRim.Color = targetRim;
                return;
            }

            double ms = active ? 160 : 360;
            IEasingFunction ease = active ? Motion.Bouncy : Motion.Bouncy;
            Motion.To(thumbScale, ScaleTransform.ScaleXProperty, sx, ms, ease);
            Motion.To(thumbScale, ScaleTransform.ScaleYProperty, sy, ms, ease);
            Motion.To(trackHost, HeightProperty, targetTrackH, active ? 140 : 280, Motion.Snappy);
            Motion.To(lensRim, OpacityProperty, targetLensOpacity, active ? 120 : 220, Motion.Snappy);
            thumbFill.BeginAnimation(SolidColorBrush.ColorProperty,
                new ColorAnimation(targetFill, new Duration(TimeSpan.FromMilliseconds(active ? 110 : 240))) { EasingFunction = Motion.Snappy },
                HandoffBehavior.SnapshotAndReplace);
            thumbRim.BeginAnimation(SolidColorBrush.ColorProperty,
                new ColorAnimation(targetRim, new Duration(TimeSpan.FromMilliseconds(active ? 110 : 240))) { EasingFunction = Motion.Snappy },
                HandoffBehavior.SnapshotAndReplace);
        }
    }

    /// <summary>Smoothly animates its child's layout height from 0 to natural size when expanded.</summary>
    public sealed class ExpandBox : Decorator
    {
        public static readonly DependencyProperty IsExpandedProperty = DependencyProperty.Register(
            "IsExpanded", typeof(bool), typeof(ExpandBox), new PropertyMetadata(false, OnExpandedChanged));

        private static readonly DependencyProperty ProgressProperty = DependencyProperty.Register(
            "Progress", typeof(double), typeof(ExpandBox),
            new FrameworkPropertyMetadata(0.0, FrameworkPropertyMetadataOptions.AffectsMeasure));

        public ExpandBox()
        {
            ClipToBounds = true;
            Opacity = 0.0;
            IsHitTestVisible = false;
            Loaded += delegate { Sync(false); };
        }

        public bool IsExpanded
        {
            get { return (bool)GetValue(IsExpandedProperty); }
            set { SetValue(IsExpandedProperty, value); }
        }

        private static void OnExpandedChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
        {
            var box = (ExpandBox)sender;
            box.Sync(box.IsLoaded);
        }

        private void Sync(bool animate)
        {
            bool open = IsExpanded;
            IsHitTestVisible = open;
            double target = open ? 1.0 : 0.0;
            if (!animate || !Motion.Enabled)
            {
                BeginAnimation(ProgressProperty, null);
                BeginAnimation(OpacityProperty, null);
                SetValue(ProgressProperty, target);
                Opacity = target;
                return;
            }
            Motion.To(this, ProgressProperty, target, open ? 360 : 260, open ? Motion.Sheet : Motion.Snappy);
            Motion.To(this, OpacityProperty, target, open ? 220 : 160, Motion.Snappy);
        }

        protected override Size MeasureOverride(Size constraint)
        {
            UIElement child = Child;
            if (child == null) return new Size();
            double progress = Math.Max(0.0, Math.Min(1.0, (double)GetValue(ProgressProperty)));
            if (progress <= 0.001 && !IsExpanded)
                return new Size(0, 0);
            child.Measure(new Size(constraint.Width, Double.PositiveInfinity));
            return new Size(child.DesiredSize.Width, child.DesiredSize.Height * progress);
        }

        protected override Size ArrangeOverride(Size arrangeSize)
        {
            UIElement child = Child;
            if (child != null)
                child.Arrange(new Rect(0, 0, arrangeSize.Width, Math.Max(arrangeSize.Height, child.DesiredSize.Height)));
            return arrangeSize;
        }
    }

    /// <summary>iOS Battery-style smooth sparkline chart with soft gradient fill and left-to-right reveal.</summary>
    public sealed class SparklineChart : FrameworkElement
    {
        public static readonly DependencyProperty PointsProperty = DependencyProperty.Register(
            "Points", typeof(IList<double>), typeof(SparklineChart),
            new FrameworkPropertyMetadata(null, FrameworkPropertyMetadataOptions.AffectsRender, OnPointsChanged));

        public static readonly DependencyProperty StrokeProperty = DependencyProperty.Register(
            "Stroke", typeof(Brush), typeof(SparklineChart),
            new FrameworkPropertyMetadata(null, FrameworkPropertyMetadataOptions.AffectsRender));

        public static readonly DependencyProperty GuideBrushProperty = DependencyProperty.Register(
            "GuideBrush", typeof(Brush), typeof(SparklineChart),
            new FrameworkPropertyMetadata(null, FrameworkPropertyMetadataOptions.AffectsRender));

        private static readonly DependencyProperty RevealProperty = DependencyProperty.Register(
            "Reveal", typeof(double), typeof(SparklineChart),
            new FrameworkPropertyMetadata(1.0, FrameworkPropertyMetadataOptions.AffectsRender));

        public SparklineChart()
        {
            Height = 76;
            Loaded += delegate { Replay(); };
        }

        public IList<double> Points
        {
            get { return (IList<double>)GetValue(PointsProperty); }
            set { SetValue(PointsProperty, value); }
        }

        public Brush Stroke
        {
            get { return (Brush)GetValue(StrokeProperty); }
            set { SetValue(StrokeProperty, value); }
        }

        public Brush GuideBrush
        {
            get { return (Brush)GetValue(GuideBrushProperty); }
            set { SetValue(GuideBrushProperty, value); }
        }

        public void Replay()
        {
            if (!Motion.Enabled)
            {
                BeginAnimation(RevealProperty, null);
                SetValue(RevealProperty, 1.0);
                return;
            }
            BeginAnimation(RevealProperty, null);
            SetValue(RevealProperty, 0.0);
            Motion.To(this, RevealProperty, 1.0, 560, Motion.Smooth);
        }

        private static void OnPointsChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
        {
            var chart = (SparklineChart)sender;
            if (chart.IsLoaded && args.OldValue == null && args.NewValue != null)
                chart.Replay();
        }

        protected override void OnRender(DrawingContext context)
        {
            double width = ActualWidth, height = ActualHeight;
            if (width <= 12 || height <= 16) return;
            const double padX = 5.0, padTop = 7.0, padBottom = 7.0;
            double plotW = width - padX * 2;
            double plotH = height - padTop - padBottom;

            if (GuideBrush != null)
            {
                var guidePen = new Pen(GuideBrush, 1.0) { DashStyle = new DashStyle(new double[] { 3, 3 }, 0) };
                for (int g = 0; g <= 2; g++)
                {
                    double gy = Math.Round(padTop + plotH * g / 2.0) + 0.5;
                    context.DrawLine(guidePen, new Point(padX, gy), new Point(width - padX, gy));
                }
            }

            IList<double> raw = Points;
            if (raw == null || raw.Count == 0 || Stroke == null) return;

            int count = raw.Count;
            var pts = new Point[count == 1 ? 2 : count];
            if (count == 1)
            {
                double y = padTop + plotH * (1.0 - Clamp01(raw[0] / 100.0));
                pts[0] = new Point(padX, y);
                pts[1] = new Point(width - padX, y);
            }
            else
            {
                for (int i = 0; i < count; i++)
                {
                    double x = padX + plotW * i / (count - 1);
                    double y = padTop + plotH * (1.0 - Clamp01(raw[i] / 100.0));
                    pts[i] = new Point(x, y);
                }
            }

            var lineGeo = new StreamGeometry();
            using (StreamGeometryContext gc = lineGeo.Open())
            {
                gc.BeginFigure(pts[0], false, false);
                AppendSmoothCurve(gc, pts);
            }
            lineGeo.Freeze();

            var areaGeo = new StreamGeometry();
            using (StreamGeometryContext gc = areaGeo.Open())
            {
                gc.BeginFigure(new Point(pts[0].X, height - padBottom), true, true);
                gc.LineTo(pts[0], false, false);
                AppendSmoothCurve(gc, pts);
                gc.LineTo(new Point(pts[pts.Length - 1].X, height - padBottom), false, false);
            }
            areaGeo.Freeze();

            double reveal = Clamp01((double)GetValue(RevealProperty));
            context.PushClip(new RectangleGeometry(new Rect(0, 0, padX + plotW * reveal + 6, height)));

            var solid = Stroke as SolidColorBrush;
            if (solid != null)
            {
                Color c = solid.Color;
                var fill = new LinearGradientBrush(
                    Color.FromArgb(56, c.R, c.G, c.B),
                    Color.FromArgb(0, c.R, c.G, c.B),
                    new Point(0, 0), new Point(0, 1));
                fill.Freeze();
                context.DrawGeometry(fill, null, areaGeo);
            }

            var strokePen = new Pen(Stroke, 2.2)
            {
                StartLineCap = PenLineCap.Round,
                EndLineCap = PenLineCap.Round,
                LineJoin = PenLineJoin.Round
            };
            context.DrawGeometry(null, strokePen, lineGeo);

            Point tip = pts[pts.Length - 1];
            if (solid != null && reveal > 0.85)
            {
                var halo = new SolidColorBrush(Color.FromArgb(52, solid.Color.R, solid.Color.G, solid.Color.B));
                context.DrawEllipse(halo, null, tip, 6.0, 6.0);
            }
            context.DrawEllipse(Stroke, new Pen(Brushes.White, 1.4), tip, 3.3, 3.3);
            context.Pop();
        }

        private static void AppendSmoothCurve(StreamGeometryContext gc, Point[] pts)
        {
            if (pts.Length == 2)
            {
                gc.LineTo(pts[1], true, true);
                return;
            }
            for (int i = 0; i < pts.Length - 1; i++)
            {
                Point p0 = i > 0 ? pts[i - 1] : pts[i];
                Point p1 = pts[i];
                Point p2 = pts[i + 1];
                Point p3 = i + 2 < pts.Length ? pts[i + 2] : p2;
                var c1 = new Point(p1.X + (p2.X - p0.X) / 6.0, p1.Y + (p2.Y - p0.Y) / 6.0);
                var c2 = new Point(p2.X - (p3.X - p1.X) / 6.0, p2.Y - (p3.Y - p1.Y) / 6.0);
                gc.BezierTo(c1, c2, p2, true, true);
            }
        }

        private static double Clamp01(double v)
        {
            return v < 0 ? 0 : v > 1 ? 1 : v;
        }
    }
}
