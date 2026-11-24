using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Data;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using Microsoft.Win32;

internal sealed class NativeWindow
{
    private const string PersonalizeKey = @"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize";
    private readonly Window window;
    private readonly ListBox deviceList;
    private readonly Button refreshButton;
    private readonly ResourceDictionary darkTheme;
    private readonly Dictionary<object, object> lightTheme = new Dictionary<object, object>();
    private readonly PanelView view = new PanelView();
    private readonly Artwork artwork = new Artwork();
    private readonly JavaScriptSerializer outgoing = new JavaScriptSerializer();
    private readonly StreamWriter writer;
    private readonly int backendPid;
    private readonly DispatcherTimer cardBackdropTimer;
    private IntPtr hwnd;
    private bool dark;
    private bool glass;
    private bool closing;
    private bool? cardBackdropTheme;
    private bool cardSamplePending;
    private int cardSampleGeneration;
    private double cardBackdropBlend;
    private SolidColorBrush adaptiveCardFill;
    private SolidColorBrush adaptiveCardEdge;
    private Window detailWindow;
    private StackPanel detailContent;
    private string detailId;

    private NativeWindow(StreamWriter writer, int backendPid, bool startDark, bool startGlass)
    {
        this.writer = writer;
        this.backendPid = backendPid;
        glass = startGlass;
        window = ReadResource<Window>("NexusWindow.xaml");
        window.Title = "Nexus 外设电量中心";
        window.DataContext = view;
        TextOptions.SetTextRenderingMode(window, TextRenderingMode.Grayscale);
        using (Stream icon = ResourceStream("nexus.ico"))
            window.Icon = BitmapFrame.Create(icon, BitmapCreateOptions.PreservePixelFormat, BitmapCacheOption.OnLoad);

        darkTheme = ReadResource<ResourceDictionary>("DarkTheme.xaml");
        foreach (object key in darkTheme.Keys)
            if (window.Resources.Contains(key)) lightTheme.Add(key, window.Resources[key]);
        SetTheme(startDark);
        window.SetResourceReference(Control.BackgroundProperty, "BackdropFallbackBrush");
        cardBackdropTimer = new DispatcherTimer(DispatcherPriority.Background, window.Dispatcher)
        {
            Interval = TimeSpan.FromMilliseconds(120)
        };
        cardBackdropTimer.Tick += SampleCardBackdrop;
        window.SizeChanged += delegate { InvalidateCardSample(); };
        window.StateChanged += delegate
        {
            InvalidateCardSample();
            UpdateCardBackdropTimer();
        };

        refreshButton = Find<Button>("RefreshButton");
        deviceList = Find<ListBox>("DeviceList");
        refreshButton.Click += delegate { SendAction("refresh"); };
        Find<Button>("AddButton").Click += delegate { SendAction("add"); };
        Find<Button>("SettingsButton").Click += delegate { SendAction("settings"); };
        window.PreviewKeyDown += OnKeyDown;
        Find<ItemsControl>("RingItems").PreviewMouseLeftButtonUp += OnRingClick;
        deviceList.PreviewMouseLeftButtonUp += OnDeviceClick;
        deviceList.PreviewMouseRightButtonUp += OnDeviceMenu;
        window.SourceInitialized += delegate
        {
            hwnd = new WindowInteropHelper(window).Handle;
            HwndSource.FromHwnd(hwnd).CompositionTarget.BackgroundColor = Colors.Transparent;
            RefreshBackdrop();
            // The backend registers the HWND. It does not need the desktop compositor's first frame.
            Send(new { action = "ready", hwnd = hwnd.ToInt64() });
        };
        window.Activated += delegate { RefreshBackdrop(); };
        window.Deactivated += delegate { RefreshBackdrop(); };
        window.Closed += delegate
        {
            closing = true;
            cardBackdropTimer.Stop();
            SendAction("close");
        };
    }

    [STAThread]
    internal static int Main(string[] args)
    {
        int backendPid = 0;
        bool startDark = Equals(Registry.GetValue(PersonalizeKey, "AppsUseLightTheme", 1), 0);
        bool startGlass = true;
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--dark") startDark = true;
            if (args[i] == "--light") startDark = false;
            if (args[i] == "--glass-off") startGlass = false;
            if (args[i] == "--backend-pid" && i + 1 < args.Length) Int32.TryParse(args[++i], out backendPid);
        }

        try
        {
            using (var writer = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true })
            {
                var application = new Application { ShutdownMode = ShutdownMode.OnMainWindowClose };
                var frontend = new NativeWindow(writer, backendPid, startDark, startGlass);
                var reader = new Thread(frontend.ReadMessages) { IsBackground = true, Name = "Nexus device events" };
                reader.Start();
                return application.Run(frontend.window);
            }
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error);
            return 2;
        }
    }

    private static Stream ResourceStream(string filename)
    {
        Stream result = Assembly.GetExecutingAssembly().GetManifestResourceStream(filename);
        if (result == null) throw new InvalidOperationException("Missing resource: " + filename);
        return result;
    }

    private static T ReadResource<T>(string filename)
    {
        using (Stream stream = ResourceStream(filename)) return (T)XamlReader.Load(stream);
    }

    private T Find<T>(string name) where T : FrameworkElement
    {
        var element = window.FindName(name) as T;
        if (element == null) throw new InvalidOperationException("Nexus window is missing " + name);
        return element;
    }

    private void ReadMessages()
    {
        var serializer = new JavaScriptSerializer();
        try
        {
            using (var input = new StreamReader(Console.OpenStandardInput(), Encoding.UTF8, false))
            {
                string line;
                while ((line = input.ReadLine()) != null)
                {
                    PanelMessage message;
                    try { message = serializer.Deserialize<PanelMessage>(line); }
                    catch (ArgumentException) { continue; }
                    catch (InvalidOperationException) { continue; }
                    if (message != null)
                        window.Dispatcher.BeginInvoke(new Action(delegate { ApplyMessage(message); }), DispatcherPriority.Background);
                }
            }
        }
        catch (IOException) { }
        catch (ObjectDisposedException) { }
        finally
        {
            if (!window.Dispatcher.HasShutdownStarted)
                window.Dispatcher.BeginInvoke(new Action(delegate { if (!closing) window.Close(); }));
        }
    }

    private void ApplyMessage(PanelMessage message)
    {
        if (closing) return;
        switch (message.type)
        {
            case "snapshot":
                bool nextDark = message.theme == "dark";
                bool nextGlass = message.glass ?? true;
                bool appearanceChanged = nextDark != dark || nextGlass != glass;
                SetTheme(nextDark);
                glass = nextGlass;
                view.Update(message, artwork, ResourceBrush);
                refreshButton.IsEnabled = !message.refreshing;
                if (appearanceChanged) RefreshBackdrop();
                RefreshDetails();
                break;
            case "show":
                if (window.WindowState == WindowState.Minimized) window.WindowState = WindowState.Normal;
                window.Activate();
                break;
            case "close":
                window.Close();
                break;
        }
    }

    private void SendAction(string action, string id = null)
    {
        if (backendPid > 0 && (action == "settings" || action == "add" || action == "updates"))
            DesktopAcrylic.AllowBackendFocus(backendPid);
        if (id == null) Send(new { action = action });
        else Send(new { action = action, id = id });
    }

    private void Send(object payload)
    {
        try { writer.WriteLine(outgoing.Serialize(payload)); }
        catch (IOException) { }
        catch (ObjectDisposedException) { }
    }

    private void SetTheme(bool nextDark)
    {
        if (nextDark == dark) return;
        dark = nextDark;
        foreach (object key in lightTheme.Keys)
            window.Resources[key] = dark ? darkTheme[key] : lightTheme[key];
    }

    private Brush ResourceBrush(string key)
    {
        var brush = window.TryFindResource(key) as Brush;
        if (brush != null) return brush;
        if (key == "MediumRingBrush") return dark ? Brushes.Gold : Brushes.Goldenrod;
        return dark ? Brushes.White : Brushes.Black;
    }

    private void RefreshBackdrop()
    {
        if (hwnd == IntPtr.Zero || closing) return;
        DesktopAcrylic.SetTheme(hwnd, dark);
        bool effectsEnabled = !Equals(Registry.GetValue(PersonalizeKey, "EnableTransparency", 1), 0);
        bool displayNative = glass && effectsEnabled && !SystemParameters.HighContrast
            && !DesktopAcrylic.IsBatterySaverEnabled() && DesktopAcrylic.TryEnable(hwnd, dark);
        if (displayNative)
        {
            window.SetResourceReference(Control.BackgroundProperty, "ClientTintBrush");
            if (dark)
                DesktopAcrylic.SetCaption(hwnd, ((SolidColorBrush)ResourceBrush("BackdropFallbackBrush")).Color,
                    ((SolidColorBrush)ResourceBrush("TextBrush")).Color);
            else
                DesktopAcrylic.ResetCaption(hwnd);
        }
        else
        {
            window.SetResourceReference(Control.BackgroundProperty, "BackdropFallbackBrush");
            DesktopAcrylic.Disable(hwnd);
            DesktopAcrylic.SetCaption(hwnd, ((SolidColorBrush)ResourceBrush("BackdropFallbackBrush")).Color,
                ((SolidColorBrush)ResourceBrush("TextBrush")).Color);
        }

        bool? nextBackdropTheme = displayNative ? (bool?)dark : null;
        if (cardBackdropTheme != nextBackdropTheme)
        {
            cardBackdropTheme = nextBackdropTheme;
            InvalidateCardSample();
            SetAdaptiveCards(displayNative);
        }
        UpdateCardBackdropTimer();
    }

    private void UpdateCardBackdropTimer()
    {
        if (cardBackdropTheme.HasValue && window.WindowState != WindowState.Minimized)
            cardBackdropTimer.Start();
        else
            cardBackdropTimer.Stop();
    }

    private void InvalidateCardSample()
    {
        cardSampleGeneration++;
    }

    private void SampleCardBackdrop(object sender, EventArgs args)
    {
        var content = (FrameworkElement)window.Content;
        if (cardSamplePending || !window.IsVisible || !content.IsLoaded || content.ActualHeight <= 0)
            return;

        // Sample the blank gutters inside the outer grid's 14-DIP side margins.
        // These points stay outside the cards, buttons and scrollbar when resized.
        var points = new Point[6];
        for (int row = 0; row < 3; row++)
        {
            double y = content.ActualHeight * (row + 1) / 4;
            points[row * 2] = content.PointToScreen(new Point(-7, y));
            points[row * 2 + 1] = content.PointToScreen(new Point(content.ActualWidth + 7, y));
        }
        int generation = cardSampleGeneration;
        var dispatcher = window.Dispatcher;
        var handle = hwnd;
        cardSamplePending = true;
        ThreadPool.QueueUserWorkItem(delegate
        {
            double? brightness = WindowBackdropBrightness.Read(handle, points);
            if (dispatcher.HasShutdownStarted) return;
            dispatcher.BeginInvoke(new Action(delegate
            {
                cardSamplePending = false;
                if (!closing && cardBackdropTimer.IsEnabled && generation == cardSampleGeneration)
                    UpdateCardBackdropBrightness(brightness);
            }), DispatcherPriority.Background);
        });
    }

    private void SetAdaptiveCards(bool enabled)
    {
        if (adaptiveCardFill != null)
        {
            adaptiveCardFill.BeginAnimation(SolidColorBrush.ColorProperty, null);
            adaptiveCardEdge.BeginAnimation(SolidColorBrush.ColorProperty, null);
            adaptiveCardFill = adaptiveCardEdge = null;
        }
        if (!enabled)
        {
            window.Resources["CardFillBrush"] = ThemeCardBrush("CardFillBrush");
            window.Resources["CardEdgeBrush"] = ThemeCardBrush("CardEdgeBrush");
            return;
        }
        // Dark uses its original card on dim backdrops. Light starts with its soft card.
        adaptiveCardFill = CreateAdaptiveBrush(DimCardBrush("CardFillBrush"));
        adaptiveCardEdge = CreateAdaptiveBrush(DimCardBrush("CardEdgeBrush"));
        window.Resources["CardFillBrush"] = adaptiveCardFill;
        window.Resources["CardEdgeBrush"] = adaptiveCardEdge;
        cardBackdropBlend = 0.0;
    }

    private SolidColorBrush CreateAdaptiveBrush(SolidColorBrush source)
    {
        var brush = source.Clone();
        // WPF freezes plain resource brushes. Keep this copy animatable while
        // retaining the theme brush's opacity.
        BindingOperations.SetBinding(brush, Brush.OpacityProperty, new Binding
        {
            Source = source,
            Path = new PropertyPath(Brush.OpacityProperty),
            Mode = BindingMode.OneWay
        });
        return brush;
    }

    private void UpdateCardBackdropBrightness(double? brightness)
    {
        if (!brightness.HasValue) return;
        // Keep the established light mapping. Dark acrylic stays at its base
        // palette in dim scenes and blends to the brighter rim on bright scenes.
        double scaled = dark ? (brightness.Value - 33.0) / 13.0 : (Math.Floor(brightness.Value) - 190) / 45.0;
        double blend = Math.Max(0.0, Math.Min(1.0, scaled));
        blend = blend * blend * (3.0 - 2.0 * blend);
        if (blend == cardBackdropBlend || (blend > 0.0 && blend < 1.0 && Math.Abs(blend - cardBackdropBlend) < 0.018))
            return;
        cardBackdropBlend = blend;
        AnimateBrush(adaptiveCardFill, CardColor("CardFillBrush", blend));
        AnimateBrush(adaptiveCardEdge, CardColor("CardEdgeBrush", blend));
    }

    private SolidColorBrush ThemeCardBrush(string key)
    {
        return (SolidColorBrush)(dark ? darkTheme[key] : lightTheme[key]);
    }

    private SolidColorBrush DimCardBrush(string key)
    {
        return dark ? ThemeCardBrush(key) : (SolidColorBrush)window.Resources["Soft" + key];
    }

    private Color CardColor(string key, double blend)
    {
        Color dim = DimCardBrush(key).Color;
        Color bright = (dark ? (SolidColorBrush)darkTheme["Bright" + key] : ThemeCardBrush(key)).Color;
        return Color.FromArgb(BlendChannel(dim.A, bright.A, blend), BlendChannel(dim.R, bright.R, blend),
            BlendChannel(dim.G, bright.G, blend), BlendChannel(dim.B, bright.B, blend));
    }

    private static byte BlendChannel(byte start, byte end, double blend)
    {
        return (byte)Math.Round(start + (end - start) * blend);
    }

    private static void AnimateBrush(SolidColorBrush brush, Color target)
    {
        Color displayed = brush.Color;
        var fade = new ColorAnimation(displayed, target, new Duration(TimeSpan.FromMilliseconds(320)))
        {
            EasingFunction = new QuadraticEase { EasingMode = EasingMode.EaseOut },
            FillBehavior = FillBehavior.HoldEnd
        };
        brush.BeginAnimation(SolidColorBrush.ColorProperty, fade, HandoffBehavior.SnapshotAndReplace);
    }

    private void OnKeyDown(object sender, KeyEventArgs args)
    {
        if (args.Key == Key.F5 || (args.Key == Key.R && Keyboard.Modifiers == ModifierKeys.Control))
        {
            if (refreshButton.IsEnabled) SendAction("refresh");
            args.Handled = true;
        }
        else if (args.Key == Key.Enter && deviceList.IsKeyboardFocusWithin)
        {
            var entry = deviceList.SelectedItem as DeviceEntry;
            if (entry != null) OpenDetails(entry);
            args.Handled = true;
        }
    }

    private DeviceEntry DeviceAt(object originalSource)
    {
        var element = originalSource as DependencyObject;
        var row = element == null ? null : ItemsControl.ContainerFromElement(deviceList, element) as ListBoxItem;
        return row == null ? null : row.DataContext as DeviceEntry;
    }

    private void OnDeviceClick(object sender, MouseButtonEventArgs args)
    {
        var entry = DeviceAt(args.OriginalSource);
        if (entry != null && args.ClickCount == 1) OpenDetails(entry);
    }

    private void OnRingClick(object sender, MouseButtonEventArgs args)
    {
        var element = args.OriginalSource as DependencyObject;
        var cell = element == null ? null : ItemsControl.ContainerFromElement((ItemsControl)sender, element) as FrameworkElement;
        var ring = cell == null ? null : cell.DataContext as RingEntry;
        if (ring == null || String.IsNullOrEmpty(ring.Id)) return;
        DeviceEntry entry = FindDevice(ring.Id);
        if (entry != null) OpenDetails(entry);
    }

    private void OnDeviceMenu(object sender, MouseButtonEventArgs args)
    {
        var entry = DeviceAt(args.OriginalSource);
        if (entry == null) return;
        var menu = new ContextMenu { PlacementTarget = deviceList, Placement = PlacementMode.MousePoint };
        var details = new MenuItem { Header = "查看设备信息" };
        details.Click += delegate { OpenDetails(entry); };
        menu.Items.Add(details);
        if (entry.CanRemove)
        {
            var remove = new MenuItem { Header = "移除设备" };
            remove.Click += delegate
            {
                if (MessageBox.Show(window, "移除“" + entry.Name + "”？", "移除设备", MessageBoxButton.YesNo,
                        MessageBoxImage.Question, MessageBoxResult.No) == MessageBoxResult.Yes)
                    SendAction("remove", entry.Id);
            };
            menu.Items.Add(remove);
        }
        menu.IsOpen = true;
        args.Handled = true;
    }

    private void OpenDetails(DeviceEntry entry)
    {
        detailId = entry.Id;
        if (detailWindow == null)
        {
            detailContent = new StackPanel { Margin = new Thickness(20) };
            detailWindow = new Window
            {
                Owner = window, WindowStartupLocation = WindowStartupLocation.CenterOwner,
                Width = 410, SizeToContent = SizeToContent.Height, MaxHeight = 660, ResizeMode = ResizeMode.NoResize,
                ShowInTaskbar = false, Icon = window.Icon, FontFamily = window.FontFamily,
                Content = new ScrollViewer { Content = detailContent, VerticalScrollBarVisibility = ScrollBarVisibility.Auto }
            };
            detailWindow.PreviewKeyDown += delegate(object sender, KeyEventArgs args)
            {
                if (args.Key == Key.Escape) detailWindow.Close();
            };
            detailWindow.Closed += delegate { detailWindow = null; detailContent = null; detailId = null; };
            RefreshDetails();
            detailWindow.Show();
        }
        else
        {
            RefreshDetails();
            detailWindow.Activate();
        }
    }

    private void RefreshDetails()
    {
        if (detailWindow == null) return;
        DeviceEntry entry = FindDevice(detailId);
        if (entry == null) { detailWindow.Close(); return; }

        string title = entry.Name + " · 设备信息";
        if (detailWindow.Title != title) detailWindow.Title = title;
        Brush background = ResourceBrush("BackdropFallbackBrush");
        Brush foreground = ResourceBrush("TextBrush");
        if (detailWindow.Background != background) detailWindow.Background = background;
        if (detailWindow.Foreground != foreground) detailWindow.Foreground = foreground;
        int line = 0;
        SetDetailLine(ref line, entry.Name, 21, true);
        DeviceDetails info = entry.Details;
        if (info != null)
        {
            SetDetailLine(ref line, info.level, 27, true);
            SetDetailLine(ref line, info.caption, 13, false);
            SetDetailLine(ref line, info.timestamp, 12, false);
            foreach (ComponentDetail component in info.components ?? new ComponentDetail[0])
                SetDetailLine(ref line, component.label + "  " + component.level + (component.charging ? "  充电中" : "") +
                    (String.IsNullOrEmpty(component.time) ? "" : "  " + component.time), 13, false);
            foreach (DetailRow row in info.rows ?? new DetailRow[0])
                if (!String.IsNullOrEmpty(row.value)) SetDetailLine(ref line, row.label + "  " + row.value, 13, false);
            SetDetailLine(ref line, info.detail, 12, false);
            SetDetailLine(ref line, info.source, 12, false);
        }
        else
        {
            SetDetailLine(ref line, entry.Status, 13, false);
            SetDetailLine(ref line, entry.Connection, 13, false);
            SetDetailLine(ref line, entry.Power, 13, false);
        }
        while (detailContent.Children.Count > line)
            detailContent.Children.RemoveAt(detailContent.Children.Count - 1);
    }

    private DeviceEntry FindDevice(string id)
    {
        foreach (DeviceEntry entry in view.Devices)
            if (entry.Id == id) return entry;
        return null;
    }

    private void SetDetailLine(ref int index, string value, double size, bool strong)
    {
        if (String.IsNullOrWhiteSpace(value)) return;
        FontWeight weight = strong ? FontWeights.SemiBold : FontWeights.Normal;
        if (index == detailContent.Children.Count)
        {
            detailContent.Children.Add(new TextBlock { Text = value, FontSize = size,
                TextWrapping = TextWrapping.Wrap, FontWeight = weight, Margin = new Thickness(0, 0, 0, 8) });
        }
        else
        {
            var line = (TextBlock)detailContent.Children[index];
            if (line.Text != value) line.Text = value;
            if (line.FontSize != size) line.FontSize = size;
            if (line.FontWeight != weight) line.FontWeight = weight;
        }
        index++;
    }
}

internal static class DesktopAcrylic
{
    [StructLayout(LayoutKind.Sequential)]
    private struct Margins { public int Left, Right, Top, Bottom; }

    [StructLayout(LayoutKind.Sequential)]
    private struct AccentPolicy { public int State, Flags; public uint Tint; public int Animation; }

    [StructLayout(LayoutKind.Sequential)]
    private struct CompositionAttribute { public int Attribute; public IntPtr Data, Size; }

    [StructLayout(LayoutKind.Sequential)]
    private struct SystemPowerStatus
    {
        public byte ACLineStatus, BatteryFlag, BatteryLifePercent, SystemStatusFlag;
        public uint BatteryLifeTime, BatteryFullLifeTime;
    }

    [DllImport("dwmapi.dll")]
    private static extern int DwmExtendFrameIntoClientArea(IntPtr window, ref Margins margins);

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int size);

    [DllImport("kernel32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetSystemPowerStatus(out SystemPowerStatus status);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool AllowSetForegroundWindow(int processId);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetWindowCompositionAttribute(IntPtr window, ref CompositionAttribute attribute);

    private const int UseImmersiveDarkMode = 20;
    private const int CaptionColor = 35;
    private const int TextColor = 36;
    private const int SystemBackdropType = 38;
    private const int DesktopAcrylicBackdrop = 3;
    private const int NoBackdrop = 1;
    private const int DefaultColor = -1;
    private const int AccentPolicyAttribute = 19;
    private const int AcrylicBlurBehind = 4;
    private const uint DarkAcrylicTint = 0xBF22180FU; // 75% dark tint in ABGR; the client also has a tint layer.

    private static int SetAttribute(IntPtr window, int attribute, int value)
    {
        return DwmSetWindowAttribute(window, attribute, ref value, sizeof(int));
    }

    internal static void SetTheme(IntPtr window, bool dark) { SetAttribute(window, UseImmersiveDarkMode, dark ? 1 : 0); }

    internal static bool IsBatterySaverEnabled()
    {
        SystemPowerStatus status;
        return GetSystemPowerStatus(out status) && status.SystemStatusFlag == 1;
    }

    internal static void AllowBackendFocus(int processId) { AllowSetForegroundWindow(processId); }

    internal static bool TryEnable(IntPtr window, bool dark)
    {
        // Use the same compositor material in both activation states. The DWM
        // system backdrop remains a fallback if the accent policy is unavailable.
        var normalMargins = new Margins();
        DwmExtendFrameIntoClientArea(window, ref normalMargins);
        SetAttribute(window, SystemBackdropType, NoBackdrop);
        if (SetAccent(window, AcrylicBlurBehind, dark ? DarkAcrylicTint : 0xA0FFFFFFU)) return true;
        var margins = new Margins { Left = -1, Right = -1, Top = -1, Bottom = -1 };
        return DwmExtendFrameIntoClientArea(window, ref margins) >= 0
            && SetAttribute(window, SystemBackdropType, DesktopAcrylicBackdrop) >= 0;
    }

    private static bool SetAccent(IntPtr window, int state, uint tint)
    {
        var policy = new AccentPolicy { State = state, Tint = tint };
        int size = Marshal.SizeOf(policy);
        IntPtr data = Marshal.AllocHGlobal(size);
        try
        {
            Marshal.StructureToPtr(policy, data, false);
            var attribute = new CompositionAttribute { Attribute = AccentPolicyAttribute, Data = data, Size = new IntPtr(size) };
            return SetWindowCompositionAttribute(window, ref attribute);
        }
        catch (EntryPointNotFoundException) { return false; }
        finally { Marshal.FreeHGlobal(data); }
    }

    internal static void Disable(IntPtr window)
    {
        var margins = new Margins();
        SetAccent(window, 0, 0);
        SetAttribute(window, SystemBackdropType, NoBackdrop);
        DwmExtendFrameIntoClientArea(window, ref margins);
    }

    internal static void ResetCaption(IntPtr window)
    {
        SetAttribute(window, CaptionColor, DefaultColor);
        SetAttribute(window, TextColor, DefaultColor);
    }

    internal static void SetCaption(IntPtr window, Color fill, Color text)
    {
        SetAttribute(window, CaptionColor, fill.R | fill.G << 8 | fill.B << 16);
        SetAttribute(window, TextColor, text.R | text.G << 8 | text.B << 16);
    }
}

internal static class WindowBackdropBrightness
{
    [StructLayout(LayoutKind.Sequential)]
    private struct ScreenPoint
    {
        public int X, Y;
    }

    [DllImport("user32.dll")]
    private static extern IntPtr WindowFromPoint(ScreenPoint point);
    [DllImport("user32.dll")]
    private static extern IntPtr GetDC(IntPtr window);
    [DllImport("user32.dll")]
    private static extern int ReleaseDC(IntPtr window, IntPtr dc);
    [DllImport("gdi32.dll")]
    private static extern uint GetPixel(IntPtr dc, int x, int y);

    internal static double? Read(IntPtr window, Point[] points)
    {
        IntPtr dc = GetDC(IntPtr.Zero);
        if (dc == IntPtr.Zero) return null;
        try
        {
            int sum = 0, count = 0;
            foreach (var point in points)
            {
                var pixelPoint = new ScreenPoint { X = (int)Math.Round(point.X), Y = (int)Math.Round(point.Y) };
                // The compositor has already blurred these pixels. Ignore any point
                // where another window is in front of Nexus or the point is off-screen.
                if (WindowFromPoint(pixelPoint) != window) continue;
                uint pixel = GetPixel(dc, pixelPoint.X, pixelPoint.Y);
                if (pixel == UInt32.MaxValue) continue;
                int r = (byte)pixel, g = (byte)(pixel >> 8), b = (byte)(pixel >> 16);
                sum += 2126 * r + 7152 * g + 722 * b;
                count++;
            }
            return count >= 4 ? (double?)(sum / (10000.0 * count)) : null;
        }
        finally { ReleaseDC(IntPtr.Zero, dc); }
    }
}

public sealed class PanelMessage
{
    public string type { get; set; }
    public string theme { get; set; }
    public string summary { get; set; }
    public string updated { get; set; }
    public bool summaryLow { get; set; }
    public bool refreshing { get; set; }
    public bool? glass { get; set; }
    public string earbudNote { get; set; }
    public RingData[] rings { get; set; }
    public DeviceData[] devices { get; set; }
}

public sealed class RingData
{
    public string id { get; set; }
    public string kind { get; set; }
    public string label { get; set; }
    public int? level { get; set; }
    public bool charging { get; set; }
}

public sealed class DeviceData
{
    public string id { get; set; }
    public string name { get; set; }
    public string kind { get; set; }
    public string status { get; set; }
    public string connection { get; set; }
    public string power { get; set; }
    public bool low { get; set; }
    public string asset { get; set; }
    public DeviceDetails details { get; set; }
}

public sealed class DeviceDetails
{
    public string level { get; set; }
    public string caption { get; set; }
    public string timestamp { get; set; }
    public string detail { get; set; }
    public string source { get; set; }
    public ComponentDetail[] components { get; set; }
    public DetailRow[] rows { get; set; }
}

public sealed class ComponentDetail
{
    public string label { get; set; }
    public string level { get; set; }
    public string time { get; set; }
    public bool charging { get; set; }
}

public sealed class DetailRow
{
    public string label { get; set; }
    public string value { get; set; }
}

public abstract class ViewState : INotifyPropertyChanged
{
    public event PropertyChangedEventHandler PropertyChanged;
    protected void Changed()
    {
        var changed = PropertyChanged;
        if (changed != null) changed(this, new PropertyChangedEventArgs(String.Empty));
    }
}

public sealed class PanelView : ViewState
{
    public ObservableCollection<RingEntry> Rings { get; private set; }
    public ObservableCollection<DeviceEntry> Devices { get; private set; }
    public string HeaderSummary { get; private set; }
    public string HeaderTime { get; private set; }
    public bool HasLow { get; private set; }
    public string RefreshLabel { get; private set; }
    public string FooterNote { get; private set; }

    public PanelView()
    {
        Rings = new ObservableCollection<RingEntry>();
        Devices = new ObservableCollection<DeviceEntry>();
        HeaderSummary = "正在读取设备…";
        RefreshLabel = "刷新";
    }

    internal void Update(PanelMessage message, Artwork artwork, Func<string, Brush> brush)
    {
        HeaderSummary = message.summary ?? String.Empty;
        HeaderTime = String.IsNullOrEmpty(message.updated) ? String.Empty : " · " + message.updated;
        HasLow = message.summaryLow;
        RefreshLabel = message.refreshing ? "读取中" : "刷新";
        FooterNote = message.earbudNote;
        RingData[] rings = message.rings ?? new RingData[0];
        for (int index = 0; index < rings.Length; index++)
        {
            if (index == Rings.Count) Rings.Add(new RingEntry());
            Rings[index].Update(rings[index], brush);
        }
        while (Rings.Count > rings.Length) Rings.RemoveAt(Rings.Count - 1);

        DeviceData[] devices = message.devices ?? new DeviceData[0];
        for (int index = 0; index < devices.Length; index++)
        {
            DeviceData data = devices[index];
            int previous = index;
            while (previous < Devices.Count && Devices[previous].Id != data.id) previous++;
            if (previous == Devices.Count) Devices.Insert(index, new DeviceEntry(data.id));
            else if (previous != index) Devices.Move(previous, index);
            Devices[index].Update(data, artwork, brush);
        }
        while (Devices.Count > devices.Length) Devices.RemoveAt(Devices.Count - 1);
        Changed();
    }
}

public sealed class RingEntry : ViewState
{
    public string Id { get; private set; }
    public string Label { get; private set; }
    public string LevelText { get; private set; }
    public bool Charging { get; private set; }
    public string Glyph { get; private set; }
    public Geometry Path { get; private set; }
    public Brush Color { get; private set; }
    public Brush TextBrush { get; private set; }

    internal void Update(RingData data, Func<string, Brush> brush)
    {
        Id = data.id;
        Label = data.label;
        Charging = data.charging;
        Glyph = DeviceGlyph.For(data.kind);
        int level = Math.Min(100, Math.Max(0, data.level ?? 0));
        LevelText = data.level.HasValue ? level.ToString(CultureInfo.InvariantCulture) + "%" : "--";
        Color = brush(Charging || level > 60 ? "HealthyRingBrush" : level > 20 ? "MediumRingBrush" : "LowRingBrush");
        TextBrush = brush(data.level.HasValue && level <= 20 && !Charging ? "WarningTextBrush" : "TextBrush");
        Path = MakeArc(level);
        Changed();
    }

    private static Geometry MakeArc(int level)
    {
        const double center = 26.5;
        const double radius = 23.75;
        if (level <= 0) return Geometry.Empty;
        if (level >= 100) return Geometry.Parse("M26.5,2.75 A23.75,23.75 0 0 1 26.5,50.25 A23.75,23.75 0 0 1 26.5,2.75");
        double radians = 2 * Math.PI * level / 100.0;
        double x = center + Math.Sin(radians) * radius;
        double y = center - Math.Cos(radians) * radius;
        return Geometry.Parse(String.Format(CultureInfo.InvariantCulture,
            "M26.5,2.75 A23.75,23.75 0 {0} 1 {1:0.###},{2:0.###}", level > 50 ? 1 : 0, x, y));
    }
}

public sealed class DeviceEntry : ViewState
{
    public string Id { get; private set; }
    public string Name { get; private set; }
    public string Status { get; private set; }
    public string Connection { get; private set; }
    public string Power { get; private set; }
    public string Glyph { get; private set; }
    public ImageSource Artwork { get; private set; }
    public Brush StatusBrush { get; private set; }
    public bool CanRemove { get { return !String.IsNullOrEmpty(Id); } }
    internal DeviceDetails Details { get; private set; }

    internal DeviceEntry(string id) { Id = id; }

    internal void Update(DeviceData data, Artwork artwork, Func<string, Brush> brush)
    {
        Name = data.name;
        Status = data.status;
        Connection = data.connection;
        Power = data.power;
        Glyph = DeviceGlyph.For(data.kind);
        Details = data.details;
        Artwork = artwork.Load(data.asset);
        StatusBrush = brush(data.low ? "WarningTextBrush" : "MutedTextBrush");
        Changed();
    }
}

internal static class DeviceGlyph
{
    internal static string For(string kind)
    {
        switch (kind)
        {
            case "keyboard": return "\uE765";
            case "mouse": return "\uE962";
            case "audio":
            case "headphones":
            case "beats-studio-buds": return "\uE7F6";
            case "case": return "\uE83F";
            case "bluetooth": return "\uE702";
            default: return "\uE88E";
        }
    }
}

internal sealed class Artwork
{
    private readonly Dictionary<string, ImageSource> cache = new Dictionary<string, ImageSource>(StringComparer.OrdinalIgnoreCase);
    private readonly string assets = System.IO.Path.GetFullPath(System.IO.Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", "assets"));

    internal ImageSource Load(string relativePath)
    {
        if (String.IsNullOrEmpty(relativePath) || System.IO.Path.IsPathRooted(relativePath)) return null;
        string absolute = System.IO.Path.GetFullPath(System.IO.Path.Combine(assets, "..", relativePath));
        if (!absolute.StartsWith(assets + System.IO.Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) return null;
        ImageSource result;
        if (cache.TryGetValue(absolute, out result)) return result;
        if (File.Exists(absolute))
        {
            try
            {
                using (var stream = File.OpenRead(absolute))
                {
                    var bitmap = new BitmapImage();
                    bitmap.BeginInit();
                    bitmap.CacheOption = BitmapCacheOption.OnLoad;
                    bitmap.DecodePixelWidth = 150;
                    bitmap.StreamSource = stream;
                    bitmap.EndInit();
                    bitmap.Freeze();
                    result = bitmap;
                }
            }
            catch (IOException) { }
            catch (NotSupportedException) { }
        }
        if (cache.Count >= 128) cache.Clear();
        cache[absolute] = result;
        return result;
    }
}
