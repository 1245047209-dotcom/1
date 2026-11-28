using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Runtime;
using System.Runtime.InteropServices;
using System.Security;
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
using System.Windows.Media.Effects;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using Microsoft.Win32;
using NexusUi;

internal sealed class NativeWindow
{
    private const string PersonalizeKey = @"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize";
    private const string WarmWindowProperty = "NexusBatteryCenter.WarmUI";
    private const string WarmActivationMessage = "NexusBatteryCenter.ActivateWarmUI";
    private const int WmSystemCommand = 0x0112;
    private const int WmNonClientHitTest = 0x0084;
    private const int WmNonClientRightButtonUp = 0x00A5;
    private const int WmNonClientDestroy = 0x0082;
    private const int HtMaxButton = 9;
    private const long ScClose = 0xF060;

    private static readonly int[] IntervalSeconds = { 30, 60, 120, 300, 600, 1800 };
    private static readonly string[] IntervalLabels = { "30 秒", "1 分钟", "2 分钟", "5 分钟", "10 分钟", "30 分钟" };

    private readonly Window window;
    private readonly Grid mainLayer;
    private readonly Grid headerArea;
    private readonly Grid ringCard;
    private readonly ItemsControl ringItems;
    private readonly TextBlock listLabel;
    private readonly Grid listCard;
    private readonly TextBlock footerNote;
    private readonly ListBox deviceList;
    private readonly Grid deviceListSurface;
    private readonly Button refreshButton;
    private readonly Button captionMaxButton;
    private readonly HwndSourceHook chromeHook;

    // Adaptive window height & drag-reorder state
    private bool autoSizing;
    private bool userResized;
    private double desiredOverviewHeight = 596;
    private bool reorderSuppressClick;

    // Device details sheet
    private readonly Grid sheetLayer;
    private readonly Border sheetDim;
    private readonly Border sheet;
    private readonly ScrollViewer sheetScroll;
    private readonly StackPanel sheetContent;
    private readonly Button sheetRemove;
    private readonly SparklineChart detailSparkline;
    private readonly DetailView detail = new DetailView();
    private readonly DispatcherTimer removeConfirmTimer;
    private bool sheetOpen;
    private bool sheetClosing;
    private bool removeArmed;

    // Settings sheet & subpages
    private readonly Grid settingsLayer;
    private readonly Border settingsDim;
    private readonly Border settingsSheet;
    private readonly Grid settingsViewport;
    private readonly ScrollViewer settingsHomePage;
    private readonly ScrollViewer settingsFontsPage;
    private readonly FrameworkElement settingsUpdatesPage;
    private readonly Button settingsCancelButton;
    private readonly Button settingsBackButton;
    private readonly TextBlock settingsTitle;
    private readonly Border updateProgressTrack;
    private readonly Border updateProgressFill;
    private readonly SettingsView settings = new SettingsView();
    private readonly DispatcherTimer installConfirmTimer;
    private bool settingsOpen;
    private bool settingsClosing;
    private bool suppressSettingsPreview;
    private string settingsPage = "settings";
    private bool installArmed;
    private string committedThemeMode = "system";
    private bool committedDark;
    private bool committedGlass;
    private double committedGlassLevel = 0.48;
    private bool committedGpuAnimation = true;
    private string committedFont = "system";

    // Native Add Device sheet
    private readonly Grid addLayer;
    private readonly Border addDim;
    private readonly Border addSheet;
    private readonly Grid addSegmentInner;
    private readonly Border addSegmentThumb;
    private readonly ScrollViewer addScroll;
    private readonly ItemsControl addDevicesList;
    private readonly TextBlock addFeedbackText;
    private readonly AddDeviceView addView = new AddDeviceView();
    private bool addOpen;
    private bool addClosing;
    private bool suppressAddEvents;

    private readonly ResourceDictionary darkTheme;
    private readonly FontFamily systemFontFamily;
    private readonly FontWeight systemFontWeight;
    private readonly Dictionary<object, object> lightTheme = new Dictionary<object, object>();
    private readonly PanelView view = new PanelView();
    private readonly Artwork artwork = new Artwork();
    private readonly JavaScriptSerializer outgoing = new JavaScriptSerializer();
    private readonly StreamWriter writer;
    private readonly int backendPid;
    private readonly DispatcherTimer cardBackdropTimer;
    private readonly DispatcherTimer warmIdleTimer;
    private readonly HwndSourceHook warmWindowHook;
    private IntPtr hwnd;
    private bool dark;
    private bool glass;
    private double glassLevel = 0.48;
    private bool gpuAnimation = true;
    private bool nativeBackdropActive;
    private BlurEffect mainRecedeBlur;
    private bool closing;
    private bool warmHidden;
    private bool firstSnapshotApplied;
    private bool entrancePlayed;
    private uint warmActivation;
    private bool? cardBackdropTheme;
    private bool cardSamplePending;
    private int cardSampleGeneration;
    private double cardBackdropBlend;
    private SolidColorBrush adaptiveCardFill;
    private SolidColorBrush adaptiveCardEdge;
    private string fontChoice;

    private NativeWindow(StreamWriter writer, int backendPid, bool startDark, bool startGlass, string startFont, bool keepWarm)
    {
        this.writer = writer;
        this.backendPid = backendPid;
        glass = startGlass;
        glassLevel = Glass.Level;
        committedDark = startDark;
        committedGlass = startGlass;
        committedGlassLevel = glassLevel;
        committedFont = startFont ?? "system";

        window = ReadResource<Window>("NexusWindow.xaml");
        systemFontFamily = window.FontFamily;
        systemFontWeight = window.FontWeight;
        SetFont(startFont);
        window.Title = "Nexus 外设电量中心";
        window.DataContext = view;
        TextOptions.SetTextRenderingMode(window, TextRenderingMode.Grayscale);
        using (Stream icon = ResourceStream("nexus.ico"))
            window.Icon = BitmapFrame.Create(icon, BitmapCreateOptions.PreservePixelFormat, BitmapCacheOption.OnLoad);

        darkTheme = ReadResource<ResourceDictionary>("DarkTheme.xaml");
        foreach (object key in darkTheme.Keys)
            if (window.Resources.Contains(key)) lightTheme.Add(key, window.Resources[key]);

        mainLayer = Find<Grid>("MainLayer");
        headerArea = Find<Grid>("HeaderArea");
        ringCard = Find<Grid>("RingCard");
        ringItems = Find<ItemsControl>("RingItems");
        listLabel = Find<TextBlock>("ListLabel");
        listCard = Find<Grid>("ListCard");
        footerNote = Find<TextBlock>("FooterNote");
        deviceList = Find<ListBox>("DeviceList");
        deviceListSurface = Find<Grid>("DeviceListSurface");
        deviceListSurface.SizeChanged += delegate { UpdateDeviceListClip(); };
        AttachReorderDrag();

        // Wire device details sheet + pager + sparkline
        sheetLayer = Find<Grid>("SheetLayer");
        sheetDim = Find<Border>("SheetDim");
        sheet = Find<Border>("Sheet");
        sheetScroll = Find<ScrollViewer>("SheetScroll");
        sheetContent = Find<StackPanel>("SheetContent");
        sheetRemove = Find<Button>("SheetRemove");
        detailSparkline = Find<SparklineChart>("DetailSparkline");
        sheetLayer.DataContext = detail;
        removeConfirmTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(3.2) };
        removeConfirmTimer.Tick += delegate { DisarmRemove(); };
        sheetDim.MouseLeftButtonDown += delegate { CloseDetails(); };
        Find<Button>("SheetDone").Click += delegate { CloseDetails(); };
        Find<Button>("SheetPrevButton").Click += delegate { SwitchDetailBy(-1); };
        Find<Button>("SheetNextButton").Click += delegate { SwitchDetailBy(1); };
        Find<ItemsControl>("SheetDots").AddHandler(ButtonBase.ClickEvent, new RoutedEventHandler(OnSheetDotClick));
        Find<Button>("SheetRefresh").Click += delegate { if (refreshButton.IsEnabled) SendAction("refresh"); };
        sheetRemove.Click += OnSheetRemoveClick;
        AttachSheetDrag(Find<Grid>("SheetHandle"), sheet, sheetDim, CloseDetails);
        AttachDetailHorizontalSwipe();

        // Wire settings sheet & subpages
        settingsLayer = Find<Grid>("SettingsLayer");
        settingsDim = Find<Border>("SettingsDim");
        settingsSheet = Find<Border>("SettingsSheet");
        settingsViewport = Find<Grid>("SettingsViewport");
        settingsHomePage = Find<ScrollViewer>("SettingsHomePage");
        settingsFontsPage = Find<ScrollViewer>("SettingsFontsPage");
        settingsUpdatesPage = Find<FrameworkElement>("SettingsUpdatesPage");
        settingsCancelButton = Find<Button>("SettingsCancelButton");
        settingsBackButton = Find<Button>("SettingsBackButton");
        settingsTitle = Find<TextBlock>("SettingsTitle");
        updateProgressTrack = Find<Border>("UpdateProgressTrack");
        updateProgressFill = Find<Border>("UpdateProgressFill");
        settingsLayer.DataContext = settings;
        settings.PropertyChanged += OnSettingsPropertyChanged;
        installConfirmTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(3.5) };
        installConfirmTimer.Tick += delegate { DisarmInstall(); };

        settingsDim.MouseLeftButtonDown += delegate { CancelSettings(); };
        settingsCancelButton.Click += delegate { CancelSettings(); };
        settingsBackButton.Click += delegate { SelectSettingsPage("settings", true); };
        Find<Button>("SettingsDoneButton").Click += delegate { SaveSettings(); };
        Find<Button>("LightModeCard").Click += delegate { PickThemeCard("light"); };
        Find<Button>("DarkModeCard").Click += delegate { PickThemeCard("dark"); };
        Find<Button>("FontRowButton").Click += delegate { OpenFontPage(); };
        Find<Button>("IntervalRowButton").Click += OnIntervalClick;
        Find<Button>("UpdateRowButton").Click += delegate { OpenUpdatesSubpage(true); };
        Find<Button>("TestAlertButton").Click += delegate { SendAction("testAlert"); };
        Find<Button>("RebuildLinksButton").Click += delegate { SendAction("rebuildLinks"); };
        Find<Button>("GetPuHuiTiButton").Click += delegate { OpenExternal("https://www.alibabafonts.com/#/font"); };
        Find<ItemsControl>("FontChoicesList").AddHandler(ButtonBase.ClickEvent, new RoutedEventHandler(OnFontChoiceClick));
        Find<Button>("OpenReleasesButton").Click += delegate { SendAction("openReleases"); };
        Find<Button>("IgnoreUpdateButton").Click += delegate { SendAction("updateIgnore"); };
        Find<Button>("UpdatePrimaryButton").Click += OnUpdatePrimaryClick;
        updateProgressTrack.SizeChanged += delegate { SyncUpdateProgress(false); };
        AttachSheetDrag(Find<Grid>("SettingsHandle"), settingsSheet, settingsDim, CancelSettings);

        // Wire native Add Device sheet
        addLayer = Find<Grid>("AddLayer");
        addDim = Find<Border>("AddDim");
        addSheet = Find<Border>("AddSheet");
        addSegmentInner = Find<Grid>("AddSegmentInner");
        addSegmentThumb = Find<Border>("AddSegmentThumb");
        addScroll = Find<ScrollViewer>("AddScroll");
        addDevicesList = Find<ItemsControl>("AddDevicesList");
        addFeedbackText = Find<TextBlock>("AddFeedbackText");
        addLayer.DataContext = addView;
        addView.PropertyChanged += OnAddViewPropertyChanged;
        addDim.MouseLeftButtonDown += delegate { CloseAddDevices(); };
        Find<Button>("AddDoneButton").Click += delegate { CloseAddDevices(); };
        Find<Button>("AddRescanButton").Click += delegate { TriggerSearchScan(); };
        Find<Button>("AddUsbTab").Click += delegate { SelectSearchMode("usb"); };
        Find<Button>("AddBtTab").Click += delegate { SelectSearchMode("bluetooth"); };
        addSegmentInner.SizeChanged += delegate { SyncSegmentThumb(false); };
        addDevicesList.AddHandler(ButtonBase.ClickEvent, new RoutedEventHandler(OnAddListButtonClick));
        addDevicesList.MouseLeftButtonUp += OnAddListRowClick;
        addDevicesList.MouseRightButtonUp += OnAddListRowMenu;
        AttachSheetDrag(Find<Grid>("AddHandle"), addSheet, addDim, CloseAddDevices);

        SetTheme(startDark, false);
        ApplyGlassMaterial();
        window.SetResourceReference(Control.BackgroundProperty, "BackdropFallbackBrush");
        cardBackdropTimer = new DispatcherTimer(DispatcherPriority.Background, window.Dispatcher)
        {
            Interval = TimeSpan.FromMilliseconds(120)
        };
        cardBackdropTimer.Tick += SampleCardBackdrop;
        if (keepWarm)
        {
            warmWindowHook = WarmWindowHook;
            warmIdleTimer = new DispatcherTimer(DispatcherPriority.Background, window.Dispatcher)
            {
                Interval = TimeSpan.FromMinutes(3)
            };
            warmIdleTimer.Tick += delegate { if (warmHidden) window.Close(); };
        }
        window.SizeChanged += delegate(object sender, SizeChangedEventArgs args)
        {
            InvalidateCardSample();
            if (args.HeightChanged && !autoSizing && window.IsLoaded && window.WindowState == WindowState.Normal
                && !AnySheetOpen)
            {
                userResized = true;
            }
        };
        window.StateChanged += delegate
        {
            InvalidateCardSample();
            UpdateCardBackdropTimer();
        };

        refreshButton = Find<Button>("RefreshButton");
        refreshButton.Click += delegate { SendAction("refresh"); };
        Find<Button>("AddButton").Click += delegate { OpenAddDevices(); };
        Find<Button>("EmptyAddButton").Click += delegate { OpenAddDevices(); };
        Find<Button>("SettingsButton").Click += delegate { OpenSettings("settings"); };
        Find<Button>("CaptionMinButton").Click += delegate { SystemCommands.MinimizeWindow(window); };
        captionMaxButton = Find<Button>("CaptionMaxButton");
        captionMaxButton.Click += delegate
        {
            if (window.WindowState == WindowState.Maximized) SystemCommands.RestoreWindow(window);
            else SystemCommands.MaximizeWindow(window);
        };
        Find<Button>("CaptionCloseButton").Click += delegate { SystemCommands.CloseWindow(window); };
        chromeHook = ChromeWindowHook;
        window.PreviewKeyDown += OnKeyDown;
        ringItems.PreviewMouseLeftButtonUp += OnRingClick;
        deviceList.PreviewMouseLeftButtonUp += OnDeviceClick;
        deviceList.PreviewMouseRightButtonUp += OnDeviceMenu;
        window.Loaded += delegate
        {
            if (firstSnapshotApplied)
            {
                SyncAutoHeight(false);
                PlayEntrance();
            }
        };
        window.SourceInitialized += delegate
        {
            hwnd = new WindowInteropHelper(window).Handle;
            HwndSource source = HwndSource.FromHwnd(hwnd);
            source.CompositionTarget.BackgroundColor = Colors.Transparent;
            source.AddHook(chromeHook);
            DesktopAcrylic.EnableRoundedCorners(hwnd);
            if (keepWarm)
            {
                uint activation = RegisterWindowMessage(WarmActivationMessage);
                if (activation != 0 && SetProp(hwnd, WarmWindowProperty, new IntPtr(1)))
                {
                    warmActivation = activation;
                    source.AddHook(warmWindowHook);
                }
            }
            RefreshBackdrop();
            Send(new { action = "ready", hwnd = hwnd.ToInt64() });
        };
        window.Activated += delegate { RefreshBackdrop(); };
        window.Deactivated += delegate { RefreshBackdrop(); };
        window.Closed += delegate
        {
            closing = true;
            cardBackdropTimer.Stop();
            removeConfirmTimer.Stop();
            installConfirmTimer.Stop();
            if (warmIdleTimer != null) warmIdleTimer.Stop();
            SendAction("close");
        };
    }

    private bool AnySheetOpen
    {
        get { return sheetOpen || settingsOpen || addOpen; }
    }

    [STAThread]
    internal static int Main(string[] args)
    {
        StartStartupProfile();
        return Run(args);
    }

    private static void StartStartupProfile()
    {
        string localData = Environment.GetEnvironmentVariable("LOCALAPPDATA");
        if (String.IsNullOrWhiteSpace(localData)) return;

        try
        {
            string cache = Path.Combine(localData, "NexusBatteryCenter", "JitProfiles");
            Directory.CreateDirectory(cache);
            ProfileOptimization.SetProfileRoot(cache);
            string buildId = typeof(NativeWindow).Module.ModuleVersionId.ToString("N");
            ProfileOptimization.StartProfile("native-" + buildId + ".profile");
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        catch (ArgumentException) { }
        catch (NotSupportedException) { }
        catch (SecurityException) { }
    }

    private static bool ReadSystemDark()
    {
        return Equals(Registry.GetValue(PersonalizeKey, "AppsUseLightTheme", 1), 0);
    }

    private static int Run(string[] args)
    {
        int backendPid = 0;
        bool startDark = ReadSystemDark();
        bool startGlass = true;
        bool keepWarm = false;
        string startFont = "system";
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--dark") startDark = true;
            if (args[i] == "--light") startDark = false;
            if (args[i] == "--glass-off") startGlass = false;
            if (args[i] == "--keep-warm") keepWarm = true;
            if (args[i] == "--font" && i + 1 < args.Length) startFont = args[++i];
            if (args[i] == "--glass-level" && i + 1 < args.Length)
            {
                double parsed;
                if (Double.TryParse(args[++i], NumberStyles.Float, CultureInfo.InvariantCulture, out parsed))
                    Glass.Level = ClampGlassLevel(parsed);
            }
            if (args[i] == "--backend-pid" && i + 1 < args.Length) Int32.TryParse(args[++i], out backendPid);
        }

        try
        {
            using (var writer = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true })
            {
                var application = new Application { ShutdownMode = ShutdownMode.OnMainWindowClose };
                var frontend = new NativeWindow(writer, backendPid, startDark, startGlass, startFont, keepWarm);
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

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern uint RegisterWindowMessage(string name);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern bool SetProp(IntPtr window, string name, IntPtr value);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr RemoveProp(IntPtr window, string name);

    [DllImport("user32.dll")]
    private static extern bool IsIconic(IntPtr window);

    private IntPtr ChromeWindowHook(IntPtr handle, int message, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        if (message == WmNonClientHitTest && captionMaxButton != null && captionMaxButton.IsVisible
            && PresentationSource.FromVisual(captionMaxButton) != null)
        {
            long xy = lParam.ToInt64();
            var screen = new Point((short)(xy & 0xFFFF), (short)((xy >> 16) & 0xFFFF));
            Point local = captionMaxButton.PointFromScreen(screen);
            if (local.X >= 0 && local.X < captionMaxButton.ActualWidth && local.Y >= 0 && local.Y < captionMaxButton.ActualHeight)
            {
                handled = true;
                return new IntPtr(HtMaxButton);
            }
        }
        else if (message == WmNonClientRightButtonUp)
        {
            long xy = lParam.ToInt64();
            var screen = new Point((short)(xy & 0xFFFF), (short)((xy >> 16) & 0xFFFF));
            SystemCommands.ShowSystemMenu(window, screen);
            handled = true;
        }
        return IntPtr.Zero;
    }

    private IntPtr WarmWindowHook(IntPtr handle, int message, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        if (message == WmNonClientDestroy)
            RemoveProp(handle, WarmWindowProperty);
        else if (message == warmActivation)
        {
            handled = true;
            window.Dispatcher.BeginInvoke(new Action(ShowWindow), DispatcherPriority.Normal);
        }
        else if (message == WmSystemCommand && (wParam.ToInt64() & 0xFFF0) == ScClose)
        {
            handled = true;
            window.Dispatcher.BeginInvoke(new Action(HideWarmWindow), DispatcherPriority.Normal);
        }
        return IntPtr.Zero;
    }

    private void HideWarmWindow()
    {
        if (closing || warmHidden || !window.IsVisible) return;
        warmHidden = true;
        entrancePlayed = false;
        CloseDetailsImmediate();
        CancelSettingsImmediate();
        CloseAddImmediate();
        window.Hide();
        InvalidateCardSample();
        cardBackdropTimer.Stop();
        warmIdleTimer.Start();
        SendAction("hidden");
    }

    private void ShowWindow()
    {
        if (closing) return;
        bool wasHidden = warmHidden;
        if (wasHidden)
        {
            warmIdleTimer.Stop();
            warmHidden = false;
            window.Show();
            InvalidateCardSample();
            RefreshBackdrop();
            SyncAutoHeight(false);
            PlayEntrance();
        }
        if (warmActivation != 0 && IsIconic(hwnd)) SystemCommands.RestoreWindow(window);
        else if (window.WindowState == WindowState.Minimized) window.WindowState = WindowState.Normal;
        window.Activate();
        if (wasHidden) SendAction("shown");
    }

    // ==================== ADAPTIVE WINDOW HEIGHT ====================

    private double ComputeIdealOverviewHeight()
    {
        int devices = view.Devices.Count;
        double height;
        if (devices == 0) height = 472;
        else if (devices == 1) height = 468;
        else if (devices == 2) height = 536;
        else if (devices == 3) height = 602;
        else height = 656;

        if (devices > 0 && !String.IsNullOrEmpty(view.FooterNote))
            height += 26;
        if (view.Rings.Count > 4)
            height += 94;

        Rect work = SystemParameters.WorkArea;
        double maxAllowed = Math.Max(window.MinHeight, work.Height - 36);
        return Math.Max(window.MinHeight, Math.Min(maxAllowed, height));
    }

    private void SyncAutoHeight(bool animate)
    {
        if (window.WindowState != WindowState.Normal) return;
        desiredOverviewHeight = ComputeIdealOverviewHeight();
        double target;
        if (AnySheetOpen)
        {
            double baseHeight = userResized ? window.ActualHeight : desiredOverviewHeight;
            target = Math.Max(584, baseHeight);
        }
        else
        {
            if (userResized) return;
            target = desiredOverviewHeight;
        }

        Rect work = SystemParameters.WorkArea;
        target = Math.Max(window.MinHeight, Math.Min(Math.Max(window.MinHeight, work.Height - 32), target));
        double current = window.ActualHeight > 0 ? window.ActualHeight : window.Height;
        if (Math.Abs(current - target) < 2.0)
        {
            if (!window.IsLoaded)
            {
                autoSizing = true;
                window.Height = target;
                autoSizing = false;
            }
            return;
        }

        autoSizing = true;
        if (window.IsLoaded && window.Top + target > work.Bottom - 8)
            window.Top = Math.Max(work.Top + 8, work.Bottom - target - 8);

        if (!animate || !Motion.Enabled || !window.IsLoaded)
        {
            window.BeginAnimation(FrameworkElement.HeightProperty, null);
            window.Height = target;
            autoSizing = false;
            return;
        }

        var anim = new DoubleAnimation(current, target, new Duration(TimeSpan.FromMilliseconds(340)))
        {
            EasingFunction = Motion.Smooth,
            FillBehavior = FillBehavior.Stop
        };
        anim.Completed += delegate
        {
            window.Height = target;
            autoSizing = false;
        };
        window.Height = target;
        window.BeginAnimation(FrameworkElement.HeightProperty, anim, HandoffBehavior.SnapshotAndReplace);
    }

    private void PlayEntrance()
    {
        if (entrancePlayed || !Motion.Enabled || !window.IsLoaded) return;
        entrancePlayed = true;
        Motion.Reveal(headerArea, 0, 10, 1.0);
        Motion.Reveal(ringCard, 35, 14, 0.976);
        Motion.Reveal(listLabel, 75, 10, 1.0);
        Motion.Reveal(listCard, 90, 16, 0.985);
        Motion.Reveal(footerNote, 130, 8, 1.0);

        window.Dispatcher.BeginInvoke(new Action(delegate
        {
            for (int i = 0; i < ringItems.Items.Count; i++)
            {
                var entry = ringItems.Items[i] as RingEntry;
                if (entry != null && entry.IsPlaceholder) continue;
                var cell = ringItems.ItemContainerGenerator.ContainerFromIndex(i) as DependencyObject;
                if (cell == null) continue;
                double delay = 30 + i * 30;
                BatteryRing ring = FindDescendant<BatteryRing>(cell);
                if (ring != null) ring.Replay(delay);
                CountingText number = FindDescendant<CountingText>(cell);
                if (number != null) number.Replay(delay);
            }
            for (int i = 0; i < deviceList.Items.Count; i++)
            {
                var row = deviceList.ItemContainerGenerator.ContainerFromIndex(i) as UIElement;
                if (row != null) Motion.Reveal(row, 85 + i * 36, 12, 1.0);
            }
        }), DispatcherPriority.Loaded);
    }

    private static T FindDescendant<T>(DependencyObject root) where T : DependencyObject
    {
        int count = VisualTreeHelper.GetChildrenCount(root);
        for (int i = 0; i < count; i++)
        {
            DependencyObject child = VisualTreeHelper.GetChild(root, i);
            var match = child as T;
            if (match != null) return match;
            match = FindDescendant<T>(child);
            if (match != null) return match;
        }
        return null;
    }

    private void ReadMessages()
    {
        var serializer = new JavaScriptSerializer { MaxJsonLength = 1 << 22 };
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
        if (closing || message == null) return;
        switch (message.type)
        {
            case "snapshot":
                ApplySnapshot(message);
                break;
            case "settings":
                ApplySettingsState(message);
                break;
            case "settingsSaved":
                ApplySettingsSaved(message);
                break;
            case "toolFeedback":
                settings.SetToolFeedback(message.feedback);
                Motion.Reveal(Find<TextBlock>("SettingsFeedback"), 0, 6, 1.0);
                break;
            case "updateState":
                settings.ApplyUpdate(message, ResourceBrush);
                SyncUpdateProgress(true);
                break;
            case "searchState":
                suppressAddEvents = true;
                bool hadFeedback = !String.IsNullOrEmpty(message.feedback);
                addView.Apply(message, artwork, ResourceBrush, window.FontWeight,
                    (FontWeight)window.Resources["UiStrongWeight"]);
                suppressAddEvents = false;
                SyncSegmentThumb(true);
                if (hadFeedback) Motion.Reveal(addFeedbackText, 0, 5, 1.0);
                break;
            case "openSettings":
                OpenSettings(String.IsNullOrEmpty(message.page) ? "settings" : message.page);
                break;
            case "openAdd":
                OpenAddDevices();
                break;
            case "show":
                ShowWindow();
                break;
            case "close":
                window.Close();
                break;
        }
    }

    private void ApplySnapshot(PanelMessage message)
    {
        bool first = !firstSnapshotApplied;
        firstSnapshotApplied = true;

        bool nextDark = message.theme == "dark";
        bool nextGlass = message.glass ?? true;
        double nextGlassLevel = message.glassLevel.HasValue ? ClampGlassLevel(message.glassLevel.Value) : glassLevel;
        bool nextGpu = message.gpu ?? gpuAnimation;
        if (!settingsOpen)
        {
            committedDark = nextDark;
            committedGlass = nextGlass;
            committedGlassLevel = nextGlassLevel;
            committedGpuAnimation = nextGpu;
            committedFont = message.font ?? "system";
            bool appearanceChanged = nextDark != dark || nextGlass != glass;
            bool materialChanged = Math.Abs(nextGlassLevel - glassLevel) > 0.002 || nextGpu != gpuAnimation;
            gpuAnimation = nextGpu;
            Motion.GpuEnabled = gpuAnimation;
            glassLevel = nextGlassLevel;
            SetTheme(nextDark, !first);
            SetFont(message.font);
            glass = nextGlass;
            if (appearanceChanged) RefreshBackdrop();
            else if (materialChanged || first) ApplyGlassMaterial();
        }

        int previousCount = view.Devices.Count;
        List<string> addedIds = view.Update(message, artwork, ResourceBrush);
        refreshButton.IsEnabled = !message.refreshing;
        RefreshDetails();
        settings.RefreshBrushes(ResourceBrush);
        addView.RefreshBrushes(ResourceBrush, window.FontWeight, (FontWeight)window.Resources["UiStrongWeight"]);

        if (first || view.Devices.Count != previousCount)
            SyncAutoHeight(!first);

        if (first && window.IsLoaded)
        {
            PlayEntrance();
        }
        else if (addedIds.Count > 0 && previousCount > 0 && window.IsLoaded)
        {
            window.Dispatcher.BeginInvoke(new Action(delegate
            {
                for (int i = 0; i < view.Devices.Count; i++)
                {
                    if (addedIds.Contains(view.Devices[i].Id))
                    {
                        var row = deviceList.ItemContainerGenerator.ContainerFromIndex(i) as UIElement;
                        if (row != null) Motion.Reveal(row, 20, 14, 0.98);
                    }
                }
            }), DispatcherPriority.Loaded);
        }
    }

    private void SetFont(string choice)
    {
        choice = choice ?? "system";
        if (choice == fontChoice) return;

        FontFamily family;
        FontWeight body, emphasis;
        bool custom = PanelFonts.TryResolve(choice, out family, out body, out emphasis);
        window.FontFamily = custom ? family : systemFontFamily;
        window.FontWeight = custom ? body : systemFontWeight;
        window.Resources["UiStrongWeight"] = custom ? emphasis : FontWeights.SemiBold;
        window.Resources["UiHeadingWeight"] = custom ? emphasis : FontWeights.Bold;
        if (custom)
        {
            window.Resources["UiTooltipFont"] = window.FontFamily;
            window.Resources["UiTooltipWeight"] = body;
        }
        else
        {
            window.Resources.Remove("UiTooltipFont");
            window.Resources.Remove("UiTooltipWeight");
        }
        fontChoice = choice;
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

    private void SetTheme(bool nextDark, bool animate)
    {
        if (nextDark == dark && lightTheme.Count > 0 && window.Resources["DeviceRow"] != null)
        {
            deviceList.SetResourceReference(ItemsControl.ItemContainerStyleProperty, dark ? "DarkDeviceRow" : "DeviceRow");
            deviceList.Padding = new Thickness(dark ? 0 : 2);
            return;
        }
        dark = nextDark;
        bool canAnimate = animate && window.IsLoaded && Motion.Enabled;
        foreach (object key in lightTheme.Keys)
        {
            object target = dark ? darkTheme[key] : lightTheme[key];
            var targetSolid = target as SolidColorBrush;
            var currentSolid = window.Resources[key] as SolidColorBrush;
            if (canAnimate && targetSolid != null && currentSolid != null)
            {
                var animated = new SolidColorBrush(currentSolid.Color);
                animated.BeginAnimation(SolidColorBrush.ColorProperty,
                    new ColorAnimation(targetSolid.Color, new Duration(TimeSpan.FromMilliseconds(240))) { EasingFunction = Motion.Snappy });
                window.Resources[key] = animated;
            }
            else
            {
                window.Resources[key] = target;
            }
        }
        deviceList.SetResourceReference(ItemsControl.ItemContainerStyleProperty, dark ? "DarkDeviceRow" : "DeviceRow");
        deviceList.Padding = new Thickness(dark ? 0 : 2);
        UpdateDeviceListClip();
        ApplyGlassMaterial();
        view.RefreshBrushes(ResourceBrush);
        RefreshDetails();
        settings.RefreshBrushes(ResourceBrush);
        addView.RefreshBrushes(ResourceBrush, window.FontWeight, (FontWeight)window.Resources["UiStrongWeight"]);
    }

    private void UpdateDeviceListClip()
    {
        if (!dark)
        {
            deviceListSurface.Clip = null;
            return;
        }
        var frame = (Border)deviceListSurface.Parent;
        double radius = Math.Max(0, frame.CornerRadius.TopLeft - frame.BorderThickness.Left / 2);
        var clip = new RectangleGeometry(new Rect(deviceListSurface.RenderSize), radius, radius);
        clip.Freeze();
        deviceListSurface.Clip = clip;
    }

    private Brush ResourceBrush(string key)
    {
        var brush = window.TryFindResource(key) as Brush;
        if (brush != null) return brush;
        if (key == "MediumRingBrush") return dark ? Brushes.Gold : Brushes.Goldenrod;
        return dark ? Brushes.White : Brushes.Black;
    }

    private Color ThemeColor(string key)
    {
        var solid = (dark ? darkTheme[key] : lightTheme[key]) as SolidColorBrush;
        return solid != null ? solid.Color : Colors.Black;
    }

    private void RefreshBackdrop()
    {
        if (hwnd == IntPtr.Zero || closing || warmHidden) return;
        DesktopAcrylic.SetTheme(hwnd, dark);
        bool effectsEnabled = !Equals(Registry.GetValue(PersonalizeKey, "EnableTransparency", 1), 0);
        bool displayNative = glass && effectsEnabled && !SystemParameters.HighContrast
            && !DesktopAcrylic.IsBatterySaverEnabled() && DesktopAcrylic.TryEnable(hwnd, dark);
        nativeBackdropActive = displayNative;
        if (displayNative)
        {
            window.SetResourceReference(Control.BackgroundProperty, "ClientTintBrush");
            DesktopAcrylic.ResetCaption(hwnd);
        }
        else
        {
            window.SetResourceReference(Control.BackgroundProperty, "BackdropFallbackBrush");
            DesktopAcrylic.Disable(hwnd);
            DesktopAcrylic.SetCaption(hwnd, ThemeColor("BackdropFallbackBrush"), ThemeColor("TextBrush"));
        }

        bool? nextBackdropTheme = displayNative ? (bool?)dark : null;
        if (cardBackdropTheme != nextBackdropTheme)
        {
            cardBackdropTheme = nextBackdropTheme;
            InvalidateCardSample();
            SetAdaptiveCards(displayNative);
        }
        else if (!displayNative)
        {
            SetAdaptiveCards(false);
        }
        ApplyGlassMaterial();
        UpdateCardBackdropTimer();
    }

    private static double ClampGlassLevel(double value)
    {
        if (Double.IsNaN(value) || Double.IsInfinity(value)) return 0.48;
        return Math.Max(0.0, Math.Min(1.0, value));
    }

    private void ApplyGlassMaterial()
    {
        bool useGlass = glass && !SystemParameters.HighContrast && (hwnd == IntPtr.Zero || nativeBackdropActive);
        Glass.Active = useGlass;
        Glass.Dark = dark;
        Glass.Level = glassLevel;

        if (!useGlass)
        {
            window.Resources["SheetFillBrush"] = ThemeCardBrush("SolidSheetFillBrush");
            window.Resources["GroupFillBrush"] = ThemeCardBrush("SolidGroupFillBrush");
            window.Resources["MenuFillBrush"] = ThemeCardBrush("SolidGroupFillBrush");
            window.Resources["CardFillBrush"] = ThemeCardBrush("SolidCardFillBrush");
            window.Resources["CardEdgeBrush"] = ThemeCardBrush("SolidCardEdgeBrush");
            if (mainLayer.Effect != null) mainLayer.Effect = null;
            Glass.NotifyChanged();
            return;
        }

        double t = glassLevel;
        if (dark)
        {
            window.Resources["ClientTintBrush"] = new SolidColorBrush(Color.FromArgb(BlendChannel(156, 86, t), 0x17, 0x1C, 0x26));
            window.Resources["SheetFillBrush"] = new SolidColorBrush(Color.FromArgb(BlendChannel(244, 180, t), 0x19, 0x20, 0x2E));
            window.Resources["GroupFillBrush"] = new SolidColorBrush(Color.FromArgb(BlendChannel(76, 44, t), 0x60, 0x72, 0x8E));
            window.Resources["MenuFillBrush"] = new SolidColorBrush(Color.FromArgb(BlendChannel(246, 204, t), 0x20, 0x28, 0x39));
        }
        else
        {
            window.Resources["ClientTintBrush"] = new SolidColorBrush(Color.FromArgb(BlendChannel(36, 0, t), 0xF3, 0xF6, 0xFB));
            window.Resources["SheetFillBrush"] = new SolidColorBrush(Color.FromArgb(BlendChannel(244, 174, t), 0xEE, 0xF3, 0xFA));
            window.Resources["GroupFillBrush"] = new SolidColorBrush(Color.FromArgb(BlendChannel(238, 144, t), 0xFF, 0xFF, 0xFF));
            window.Resources["MenuFillBrush"] = new SolidColorBrush(Color.FromArgb(BlendChannel(248, 198, t), 0xF8, 0xFB, 0xFF));
        }

        if (adaptiveCardFill != null)
        {
            adaptiveCardFill.BeginAnimation(SolidColorBrush.ColorProperty, null);
            adaptiveCardFill.Color = CardColor("CardFillBrush", cardBackdropBlend);
            adaptiveCardEdge.BeginAnimation(SolidColorBrush.ColorProperty, null);
            adaptiveCardEdge.Color = CardColor("CardEdgeBrush", cardBackdropBlend);
        }
        else
        {
            window.Resources["CardFillBrush"] = new SolidColorBrush(CardColor("CardFillBrush", 0.0));
            window.Resources["CardEdgeBrush"] = new SolidColorBrush(CardColor("CardEdgeBrush", 0.0));
        }

        if (AnySheetOpen)
        {
            if (Motion.GpuEnabled)
            {
                if (mainRecedeBlur == null)
                    mainRecedeBlur = new BlurEffect { RenderingBias = RenderingBias.Performance };
                mainRecedeBlur.BeginAnimation(BlurEffect.RadiusProperty, null);
                mainRecedeBlur.Radius = 6.0 + 7.5 * t;
                if (mainLayer.Effect != mainRecedeBlur) mainLayer.Effect = mainRecedeBlur;
            }
            else if (mainLayer.Effect != null)
            {
                mainLayer.Effect = null;
            }
        }

        Glass.NotifyChanged();
    }

    private void PreviewCardSpecular(double normX)
    {
        SpecularOverlay cardSpec = FindDescendant<SpecularOverlay>(ringCard);
        if (cardSpec != null) cardSpec.PreviewAt(normX, 0.24, 0.25);
        SpecularOverlay pillSpec = FindDescendant<SpecularOverlay>(refreshButton);
        if (pillSpec != null) pillSpec.PreviewAt(0.35, 0.3, 0.2);
    }

    private void PreviewSliderDrag(bool active)
    {
        LiquidSlider slider = Find<LiquidSlider>("GlassLevelSlider");
        if (slider != null) slider.SetPreviewDragging(active);
    }

    private void UpdateCardBackdropTimer()
    {
        if (!warmHidden && !AnySheetOpen && cardBackdropTheme.HasValue && window.WindowState != WindowState.Minimized)
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

        var points = new Point[6];
        for (int row = 0; row < 3; row++)
        {
            double y = content.ActualHeight * (row + 1) / 4;
            points[row * 2] = content.PointToScreen(new Point(6, y));
            points[row * 2 + 1] = content.PointToScreen(new Point(Math.Max(8, content.ActualWidth - 6), y));
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
            window.Resources["CardFillBrush"] = ThemeCardBrush("SolidCardFillBrush");
            window.Resources["CardEdgeBrush"] = ThemeCardBrush("SolidCardEdgeBrush");
            return;
        }
        adaptiveCardFill = CreateAdaptiveBrush(DimCardBrush("CardFillBrush"));
        adaptiveCardEdge = CreateAdaptiveBrush(DimCardBrush("CardEdgeBrush"));
        window.Resources["CardFillBrush"] = adaptiveCardFill;
        window.Resources["CardEdgeBrush"] = adaptiveCardEdge;
        cardBackdropBlend = 0.0;
    }

    private SolidColorBrush CreateAdaptiveBrush(SolidColorBrush source)
    {
        var brush = source.Clone();
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
        if (!brightness.HasValue || adaptiveCardFill == null) return;
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
        byte baseA = BlendChannel(dim.A, bright.A, blend);
        if (key == "CardFillBrush")
        {
            double factor = 1.22 - 0.46 * glassLevel;
            int minA = dark ? 30 : 146;
            int maxA = dark ? 68 : 248;
            baseA = (byte)Math.Max(minA, Math.Min(maxA, (int)Math.Round(baseA * factor)));
        }
        return Color.FromArgb(baseA, BlendChannel(dim.R, bright.R, blend),
            BlendChannel(dim.G, bright.G, blend), BlendChannel(dim.B, bright.B, blend));
    }

    private static byte BlendChannel(byte start, byte end, double blend)
    {
        return (byte)Math.Round(start + (end - start) * blend);
    }

    private static void AnimateBrush(SolidColorBrush brush, Color target)
    {
        if (brush == null) return;
        Color displayed = brush.Color;
        var fade = new ColorAnimation(displayed, target, new Duration(TimeSpan.FromMilliseconds(320)))
        {
            EasingFunction = new QuadraticEase { EasingMode = EasingMode.EaseOut },
            FillBehavior = FillBehavior.HoldEnd
        };
        brush.BeginAnimation(SolidColorBrush.ColorProperty, fade, HandoffBehavior.SnapshotAndReplace);
    }

    // ==================== SPRING DRAG-TO-REORDER ON DEVICE LIST ====================

    private void AttachReorderDrag()
    {
        ListBoxItem candidateRow = null;
        int sourceIndex = -1;
        int targetIndex = -1;
        bool dragging = false;
        Point startPoint = new Point();
        double[] centers = null;
        double[] shifts = null;
        double stride = 66;

        deviceList.PreviewMouseLeftButtonDown += delegate(object sender, MouseButtonEventArgs args)
        {
            reorderSuppressClick = false;
            if (view.Devices.Count < 2) { candidateRow = null; return; }
            var element = args.OriginalSource as DependencyObject;
            candidateRow = element == null ? null : ItemsControl.ContainerFromElement(deviceList, element) as ListBoxItem;
            if (candidateRow == null) return;
            sourceIndex = deviceList.ItemContainerGenerator.IndexFromContainer(candidateRow);
            targetIndex = sourceIndex;
            startPoint = args.GetPosition(deviceList);
            dragging = false;
        };

        deviceList.PreviewMouseMove += delegate(object sender, MouseEventArgs args)
        {
            if (candidateRow == null || args.LeftButton != MouseButtonState.Pressed || view.Devices.Count < 2)
                return;
            Point pos = args.GetPosition(deviceList);
            double dy = pos.Y - startPoint.Y;
            double dx = pos.X - startPoint.X;

            if (!dragging)
            {
                if (Math.Abs(dy) < 7.0 || Math.Abs(dy) <= Math.Abs(dx)) return;
                dragging = true;
                reorderSuppressClick = true;
                int n = view.Devices.Count;
                centers = new double[n];
                shifts = new double[n];
                for (int i = 0; i < n; i++)
                {
                    var item = deviceList.ItemContainerGenerator.ContainerFromIndex(i) as ListBoxItem;
                    if (item != null)
                    {
                        Point top = item.TranslatePoint(new Point(0, 0), deviceList);
                        centers[i] = top.Y + item.ActualHeight / 2.0;
                        if (i == sourceIndex && item.ActualHeight > 20) stride = item.ActualHeight;
                    }
                }
                Panel.SetZIndex(candidateRow, 30);
                candidateRow.Opacity = 0.95;
                ScaleTransform sc = Motion.ScaleOf(candidateRow);
                Motion.To(sc, ScaleTransform.ScaleXProperty, 1.022, 150, Motion.Snappy);
                Motion.To(sc, ScaleTransform.ScaleYProperty, 1.022, 150, Motion.Snappy);
                deviceList.CaptureMouse();
            }

            double minDy = centers[0] - centers[sourceIndex] - 8;
            double maxDy = centers[centers.Length - 1] - centers[sourceIndex] + 8;
            double clamped = Math.Max(minDy, Math.Min(maxDy, dy));
            TranslateTransform dragOffset = Motion.OffsetOf(candidateRow);
            dragOffset.BeginAnimation(TranslateTransform.YProperty, null);
            dragOffset.Y = clamped;

            double draggedCenter = centers[sourceIndex] + clamped;
            int nextTarget = sourceIndex;
            for (int i = 0; i < centers.Length; i++)
            {
                if (i < sourceIndex && draggedCenter < centers[i]) { nextTarget = i; break; }
                if (i > sourceIndex && draggedCenter > centers[i]) nextTarget = i;
            }
            targetIndex = nextTarget;

            for (int i = 0; i < centers.Length; i++)
            {
                if (i == sourceIndex) continue;
                double desired = 0;
                if (targetIndex > sourceIndex && i > sourceIndex && i <= targetIndex) desired = -stride;
                else if (targetIndex < sourceIndex && i >= targetIndex && i < sourceIndex) desired = stride;
                if (Math.Abs(shifts[i] - desired) > 0.5)
                {
                    shifts[i] = desired;
                    var neighbor = deviceList.ItemContainerGenerator.ContainerFromIndex(i) as UIElement;
                    if (neighbor != null)
                        Motion.To(Motion.OffsetOf(neighbor), TranslateTransform.YProperty, desired, 260, Motion.Gentle);
                }
            }
        };

        Action finishReorder = delegate
        {
            if (candidateRow == null) return;
            ListBoxItem finishedRow = candidateRow;
            candidateRow = null;
            if (!dragging) return;
            dragging = false;
            deviceList.ReleaseMouseCapture();

            for (int i = 0; i < view.Devices.Count; i++)
            {
                var item = deviceList.ItemContainerGenerator.ContainerFromIndex(i) as UIElement;
                if (item == null) continue;
                Panel.SetZIndex(item, 0);
                item.Opacity = 1.0;
                TranslateTransform off = Motion.OffsetOf(item);
                off.BeginAnimation(TranslateTransform.YProperty, null);
                off.Y = 0;
                ScaleTransform sc = Motion.ScaleOf(item);
                Motion.To(sc, ScaleTransform.ScaleXProperty, 1.0, 320, Motion.Gentle);
                Motion.To(sc, ScaleTransform.ScaleYProperty, 1.0, 320, Motion.Gentle);
            }

            if (sourceIndex >= 0 && targetIndex >= 0 && sourceIndex != targetIndex
                && sourceIndex < view.Devices.Count && targetIndex < view.Devices.Count)
            {
                view.Devices.Move(sourceIndex, targetIndex);
                window.Dispatcher.BeginInvoke(new Action(delegate
                {
                    var moved = deviceList.ItemContainerGenerator.ContainerFromIndex(targetIndex) as UIElement;
                    if (moved != null) Motion.Pulse(moved, 1.015);
                }), DispatcherPriority.Loaded);

                var orderedIds = new string[view.Devices.Count];
                for (int i = 0; i < view.Devices.Count; i++) orderedIds[i] = view.Devices[i].Id;
                Send(new { action = "reorder", ids = orderedIds });
            }
            else
            {
                Motion.To(Motion.OffsetOf(finishedRow), TranslateTransform.YProperty, 0, 280, Motion.Sheet);
            }
        };

        deviceList.PreviewMouseLeftButtonUp += delegate(object sender, MouseButtonEventArgs args)
        {
            bool wasDragging = dragging;
            finishReorder();
            if (wasDragging) args.Handled = true;
        };
        deviceList.LostMouseCapture += delegate { if (dragging) finishReorder(); };
    }

    // ==================== KEYBOARD & POINTER ROUTING ====================

    private void OnKeyDown(object sender, KeyEventArgs args)
    {
        if (warmActivation != 0 && args.Key == Key.Q && Keyboard.Modifiers == ModifierKeys.Control)
        {
            args.Handled = true;
            window.Close();
            return;
        }
        if (args.Key == Key.OemComma && Keyboard.Modifiers == ModifierKeys.Control)
        {
            args.Handled = true;
            if (!settingsOpen) OpenSettings("settings");
            return;
        }
        if (args.Key == Key.Escape)
        {
            if (addOpen)
            {
                args.Handled = true;
                CloseAddDevices();
                return;
            }
            if (settingsOpen)
            {
                args.Handled = true;
                if (settingsPage != "settings") SelectSettingsPage("settings", true);
                else CancelSettings();
                return;
            }
            if (sheetOpen)
            {
                args.Handled = true;
                CloseDetails();
                return;
            }
        }
        if (sheetOpen && Keyboard.Modifiers == ModifierKeys.None)
        {
            if (args.Key == Key.Left) { args.Handled = true; SwitchDetailBy(-1); return; }
            if (args.Key == Key.Right) { args.Handled = true; SwitchDetailBy(1); return; }
        }
        if (addOpen && Keyboard.Modifiers == ModifierKeys.None
            && (Find<Button>("AddUsbTab").IsKeyboardFocused || Find<Button>("AddBtTab").IsKeyboardFocused))
        {
            if (args.Key == Key.Left) { args.Handled = true; SelectSearchMode("usb"); Find<Button>("AddUsbTab").Focus(); return; }
            if (args.Key == Key.Right) { args.Handled = true; SelectSearchMode("bluetooth"); Find<Button>("AddBtTab").Focus(); return; }
        }
        if (settingsOpen && args.SystemKey == Key.Left && Keyboard.Modifiers == ModifierKeys.Alt && settingsPage != "settings")
        {
            args.Handled = true;
            SelectSettingsPage("settings", true);
            return;
        }
        if (args.Key == Key.F5 || (args.Key == Key.R && Keyboard.Modifiers == ModifierKeys.Control))
        {
            args.Handled = true;
            if (addOpen) TriggerSearchScan();
            else if (refreshButton.IsEnabled && !settingsOpen) SendAction("refresh");
        }
        else if (args.Key == Key.Enter && !AnySheetOpen && deviceList.IsKeyboardFocusWithin)
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
        if (reorderSuppressClick)
        {
            reorderSuppressClick = false;
            return;
        }
        var entry = DeviceAt(args.OriginalSource);
        if (entry != null && args.ClickCount == 1) OpenDetails(entry);
    }

    private void OnRingClick(object sender, MouseButtonEventArgs args)
    {
        var element = args.OriginalSource as DependencyObject;
        var cell = element == null ? null : ItemsControl.ContainerFromElement((ItemsControl)sender, element) as FrameworkElement;
        var ring = cell == null ? null : cell.DataContext as RingEntry;
        if (ring == null || ring.IsPlaceholder || String.IsNullOrEmpty(ring.Id)) return;
        DeviceEntry entry = FindDevice(ring.Id);
        if (entry != null) OpenDetails(entry);
    }

    private void OnDeviceMenu(object sender, MouseButtonEventArgs args)
    {
        var entry = DeviceAt(args.OriginalSource);
        if (entry == null) return;
        var menu = new ContextMenu
        {
            Style = (Style)window.FindResource("NexusMenu"),
            PlacementTarget = deviceList,
            Placement = PlacementMode.MousePoint,
            FontFamily = window.FontFamily,
            FontWeight = window.FontWeight
        };
        var details = new MenuItem { Header = "查看设备信息" };
        details.Click += delegate { OpenDetails(entry); };
        menu.Items.Add(details);
        if (entry.CanRemove)
        {
            var remove = new MenuItem { Header = "移除设备", Foreground = ResourceBrush("WarningTextBrush") };
            remove.Click += delegate
            {
                OpenDetails(entry);
                ArmRemove(entry.Name);
            };
            menu.Items.Add(remove);
        }
        menu.IsOpen = true;
        args.Handled = true;
    }

    // ==================== SHARED SHEET PRESENTATION & DRAG ====================

    private void SetMainRecede(double progress, bool animate, double ms, IEasingFunction ease)
    {
        progress = Math.Max(0.0, Math.Min(1.0, progress));
        double targetScale = 1.0 - 0.035 * progress;
        double targetY = -4.0 * progress;
        double targetOpacity = 1.0 - 0.08 * progress;
        double maxBlur = 6.0 + 7.5 * glassLevel;
        double targetBlur = Glass.Active && Motion.GpuEnabled ? maxBlur * progress : 0.0;
        ScaleTransform scale = Motion.ScaleOf(mainLayer);
        TranslateTransform offset = Motion.OffsetOf(mainLayer);
        if (!animate || !Motion.Enabled)
        {
            scale.BeginAnimation(ScaleTransform.ScaleXProperty, null);
            scale.BeginAnimation(ScaleTransform.ScaleYProperty, null);
            offset.BeginAnimation(TranslateTransform.YProperty, null);
            mainLayer.BeginAnimation(UIElement.OpacityProperty, null);
            scale.ScaleX = scale.ScaleY = targetScale;
            offset.Y = targetY;
            mainLayer.Opacity = targetOpacity;
            if (targetBlur <= 0.05)
            {
                if (mainRecedeBlur != null) mainRecedeBlur.BeginAnimation(BlurEffect.RadiusProperty, null);
                mainLayer.Effect = null;
            }
            else
            {
                if (mainRecedeBlur == null)
                    mainRecedeBlur = new BlurEffect { RenderingBias = RenderingBias.Performance };
                mainRecedeBlur.BeginAnimation(BlurEffect.RadiusProperty, null);
                mainRecedeBlur.Radius = targetBlur;
                if (mainLayer.Effect != mainRecedeBlur) mainLayer.Effect = mainRecedeBlur;
            }
            return;
        }
        Motion.To(scale, ScaleTransform.ScaleXProperty, targetScale, ms, ease);
        Motion.To(offset, TranslateTransform.YProperty, targetY, ms, ease);
        Motion.To(scale, ScaleTransform.ScaleYProperty, targetScale, ms, ease);
        Motion.To(mainLayer, UIElement.OpacityProperty, targetOpacity, Math.Min(260, ms), Motion.Snappy);
        if (targetBlur > 0.05)
        {
            if (mainRecedeBlur == null)
                mainRecedeBlur = new BlurEffect { RenderingBias = RenderingBias.Performance };
            if (mainLayer.Effect != mainRecedeBlur) mainLayer.Effect = mainRecedeBlur;
            Motion.To(mainRecedeBlur, BlurEffect.RadiusProperty, targetBlur, Math.Min(320, ms), Motion.Snappy);
        }
        else if (mainLayer.Effect != null && mainRecedeBlur != null)
        {
            Motion.To(mainRecedeBlur, BlurEffect.RadiusProperty, 0.0, Math.Min(220, ms), Motion.Snappy, delegate
            {
                if (!AnySheetOpen) mainLayer.Effect = null;
            });
        }
    }

    private void AttachSheetDrag(FrameworkElement handle, Border card, Border dim, Action dismiss)
    {
        bool dragging = false;
        Point start = new Point();
        double lastY = 0;
        long lastTicks = 0;
        double velocity = 0;

        handle.MouseLeftButtonDown += delegate(object sender, MouseButtonEventArgs args)
        {
            if (args.OriginalSource is DependencyObject)
            {
                for (DependencyObject d = (DependencyObject)args.OriginalSource; d != null && d != handle; d = VisualTreeHelper.GetParent(d))
                    if (d is ButtonBase) return;
            }
            var slide = (TranslateTransform)card.RenderTransform;
            Motion.Hold(slide, TranslateTransform.YProperty);
            Motion.Hold(dim, UIElement.OpacityProperty);
            dragging = true;
            start = args.GetPosition(window);
            lastY = start.Y;
            lastTicks = Stopwatch.GetTimestamp();
            velocity = 0;
            handle.CaptureMouse();
            args.Handled = true;
        };

        handle.MouseMove += delegate(object sender, MouseEventArgs args)
        {
            if (!dragging) return;
            Point current = args.GetPosition(window);
            long now = Stopwatch.GetTimestamp();
            double dt = (double)(now - lastTicks) / Stopwatch.Frequency;
            if (dt > 0.001)
            {
                velocity = (current.Y - lastY) / dt;
                lastY = current.Y;
                lastTicks = now;
            }
            double delta = current.Y - start.Y;
            double y = delta >= 0 ? delta : delta * 0.18;
            var slide = (TranslateTransform)card.RenderTransform;
            slide.Y = y;
            double height = Math.Max(220, card.ActualHeight);
            double progress = Math.Max(0.0, Math.Min(1.0, 1.0 - Math.Max(0, y) / height));
            dim.Opacity = progress;
            SetMainRecede(progress, false, 0, null);
        };

        MouseButtonEventHandler finish = delegate
        {
            if (!dragging) return;
            dragging = false;
            handle.ReleaseMouseCapture();
            var slide = (TranslateTransform)card.RenderTransform;
            double y = slide.Y;
            if (y > 92 || velocity > 640)
            {
                dismiss();
            }
            else
            {
                Motion.To(slide, TranslateTransform.YProperty, 0, 440, Motion.Sheet);
                Motion.To(dim, UIElement.OpacityProperty, 1, 220, Motion.Snappy);
                SetMainRecede(1.0, true, 420, Motion.Sheet);
            }
        };
        handle.MouseLeftButtonUp += finish;
        handle.LostMouseCapture += delegate { if (dragging) finish(handle, null); };
    }

    // ==================== DEVICE DETAILS SHEET + SPARKLINE + PAGER ====================

    private void AttachDetailHorizontalSwipe()
    {
        Point swipeStart = new Point();
        bool tracking = false;
        sheetContent.PreviewMouseLeftButtonDown += delegate(object sender, MouseButtonEventArgs args)
        {
            if (args.OriginalSource is DependencyObject)
            {
                for (DependencyObject d = (DependencyObject)args.OriginalSource; d != null && d != sheetContent; d = VisualTreeHelper.GetParent(d))
                    if (d is ButtonBase) return;
            }
            swipeStart = args.GetPosition(sheet);
            tracking = view.Devices.Count > 1;
        };
        sheetContent.PreviewMouseLeftButtonUp += delegate(object sender, MouseButtonEventArgs args)
        {
            if (!tracking) return;
            tracking = false;
            Point end = args.GetPosition(sheet);
            double dx = end.X - swipeStart.X;
            double dy = end.Y - swipeStart.Y;
            if (Math.Abs(dx) > 46 && Math.Abs(dx) > Math.Abs(dy) * 1.6)
                SwitchDetailBy(dx < 0 ? 1 : -1);
        };
    }

    private void SwitchDetailBy(int step)
    {
        if (view.Devices.Count < 2 || String.IsNullOrEmpty(detail.Id)) return;
        int current = -1;
        for (int i = 0; i < view.Devices.Count; i++)
            if (view.Devices[i].Id == detail.Id) { current = i; break; }
        if (current < 0) return;
        int next = (current + step + view.Devices.Count) % view.Devices.Count;
        OpenDetailsWithDirection(view.Devices[next], step >= 0 ? 1 : -1);
    }

    private void OnSheetDotClick(object sender, RoutedEventArgs args)
    {
        var btn = args.OriginalSource as Button;
        if (btn == null || !(btn.Tag is string)) return;
        string targetId = (string)btn.Tag;
        if (targetId == detail.Id) return;
        int fromIdx = 0, toIdx = 0;
        for (int i = 0; i < view.Devices.Count; i++)
        {
            if (view.Devices[i].Id == detail.Id) fromIdx = i;
            if (view.Devices[i].Id == targetId) toIdx = i;
        }
        DeviceEntry entry = FindDevice(targetId);
        if (entry != null) OpenDetailsWithDirection(entry, toIdx >= fromIdx ? 1 : -1);
    }

    private void OpenDetails(DeviceEntry entry)
    {
        OpenDetailsWithDirection(entry, 1);
    }

    private void OpenDetailsWithDirection(DeviceEntry entry, int direction)
    {
        if (entry == null) return;
        if (settingsOpen) CancelSettingsImmediate();
        if (addOpen) CloseAddImmediate();

        bool switching = sheetOpen && !sheetClosing && detail.Id != entry.Id;
        DisarmRemove();
        detail.Update(entry, view.Devices, ResourceBrush);

        if (!sheetOpen || sheetClosing)
        {
            sheetOpen = true;
            sheetClosing = false;
            mainLayer.IsHitTestVisible = false;
            SyncAutoHeight(true);
            UpdateCardBackdropTimer();
            sheetScroll.ScrollToTop();
            sheetLayer.Visibility = Visibility.Visible;
            sheetLayer.UpdateLayout();

            double travel = Math.Max(320, sheet.ActualHeight > 0 ? sheet.ActualHeight : window.ActualHeight);
            var slide = (TranslateTransform)sheet.RenderTransform;
            slide.BeginAnimation(TranslateTransform.YProperty, null);
            slide.Y = Motion.Enabled ? travel : 0;
            sheetDim.BeginAnimation(UIElement.OpacityProperty, null);
            sheetDim.Opacity = 0;

            Motion.To(sheetDim, UIElement.OpacityProperty, 1, 250, Motion.Snappy);
            Motion.To(slide, TranslateTransform.YProperty, 0, 480, Motion.Sheet);
            SetMainRecede(1.0, true, 460, Motion.Sheet);
            CountingText heroNumber = FindDescendant<CountingText>(sheetContent);
            if (heroNumber != null) heroNumber.Replay(50);
            detailSparkline.Replay();
            Find<Button>("SheetDone").Focus();
        }
        else if (switching)
        {
            sheetScroll.ScrollToTop();
            Motion.RevealX(sheetContent, direction * 34);
            CountingText heroNumber = FindDescendant<CountingText>(sheetContent);
            if (heroNumber != null) heroNumber.Replay(20);
            detailSparkline.Replay();
        }
    }

    private void CloseDetails()
    {
        if (!sheetOpen || sheetClosing) return;
        sheetClosing = true;
        DisarmRemove();
        mainLayer.IsHitTestVisible = true;

        double travel = Math.Max(320, sheet.ActualHeight > 0 ? sheet.ActualHeight : window.ActualHeight);
        var slide = (TranslateTransform)sheet.RenderTransform;
        Motion.To(sheetDim, UIElement.OpacityProperty, 0, 220, Motion.Snappy);
        SetMainRecede(0.0, true, 340, Motion.Smooth);
        Motion.To(slide, TranslateTransform.YProperty, travel, 280, Motion.Standard, delegate
        {
            if (!sheetClosing) return;
            sheetOpen = false;
            sheetClosing = false;
            sheetLayer.Visibility = Visibility.Collapsed;
            SyncAutoHeight(true);
            UpdateCardBackdropTimer();
        });
    }

    private void CloseDetailsImmediate()
    {
        if (!sheetOpen) return;
        DisarmRemove();
        sheetOpen = false;
        sheetClosing = false;
        mainLayer.IsHitTestVisible = true;
        sheetLayer.Visibility = Visibility.Collapsed;
        SetMainRecede(0.0, false, 0, null);
    }

    private void RefreshDetails()
    {
        if (!sheetOpen || String.IsNullOrEmpty(detail.Id)) return;
        DeviceEntry entry = FindDevice(detail.Id);
        if (entry == null) { CloseDetails(); return; }
        detail.Update(entry, view.Devices, ResourceBrush);
    }

    private void OnSheetRemoveClick(object sender, RoutedEventArgs args)
    {
        DeviceEntry entry = FindDevice(detail.Id);
        if (entry == null || !entry.CanRemove) return;
        if (!removeArmed)
        {
            ArmRemove(entry.Name);
            return;
        }
        string id = entry.Id;
        DisarmRemove();
        CloseDetails();
        SendAction("remove", id);
    }

    private void ArmRemove(string name)
    {
        removeArmed = true;
        detail.SetRemoveLabel("再次点击确认移除“" + name + "”");
        Motion.Pulse(sheetRemove, 1.025);
        removeConfirmTimer.Stop();
        removeConfirmTimer.Start();
    }

    private void DisarmRemove()
    {
        removeConfirmTimer.Stop();
        removeArmed = false;
        detail.SetRemoveLabel("移除设备");
    }

    private DeviceEntry FindDevice(string id)
    {
        foreach (DeviceEntry entry in view.Devices)
            if (entry.Id == id) return entry;
        return null;
    }

    // ==================== NATIVE ADD DEVICE SHEET ====================

    private void OpenAddDevices()
    {
        if (sheetOpen) CloseDetailsImmediate();
        if (settingsOpen) CancelSettingsImmediate();

        if (!addOpen || addClosing)
        {
            addOpen = true;
            addClosing = false;
            mainLayer.IsHitTestVisible = false;
            SyncAutoHeight(true);
            UpdateCardBackdropTimer();

            suppressAddEvents = true;
            addView.ResetForOpen(ResourceBrush, window.FontWeight, (FontWeight)window.Resources["UiStrongWeight"]);
            suppressAddEvents = false;

            addScroll.ScrollToTop();
            addLayer.Visibility = Visibility.Visible;
            addLayer.UpdateLayout();
            SyncSegmentThumb(false);

            double travel = Math.Max(360, addSheet.ActualHeight > 0 ? addSheet.ActualHeight : window.ActualHeight);
            var slide = (TranslateTransform)addSheet.RenderTransform;
            slide.BeginAnimation(TranslateTransform.YProperty, null);
            slide.Y = Motion.Enabled ? travel : 0;
            addDim.BeginAnimation(UIElement.OpacityProperty, null);
            addDim.Opacity = 0;

            Motion.To(addDim, UIElement.OpacityProperty, 1, 250, Motion.Snappy);
            Motion.To(slide, TranslateTransform.YProperty, 0, 480, Motion.Sheet);
            SetMainRecede(1.0, true, 460, Motion.Sheet);
            TriggerSearchScan(true);
            Find<Button>("AddDoneButton").Focus();
        }
    }

    private void CloseAddDevices()
    {
        if (!addOpen || addClosing) return;
        addClosing = true;
        mainLayer.IsHitTestVisible = true;

        double travel = Math.Max(360, addSheet.ActualHeight > 0 ? addSheet.ActualHeight : window.ActualHeight);
        var slide = (TranslateTransform)addSheet.RenderTransform;
        Motion.To(addDim, UIElement.OpacityProperty, 0, 220, Motion.Snappy);
        SetMainRecede(0.0, true, 340, Motion.Smooth);
        Motion.To(slide, TranslateTransform.YProperty, travel, 280, Motion.Standard, delegate
        {
            if (!addClosing) return;
            addOpen = false;
            addClosing = false;
            addLayer.Visibility = Visibility.Collapsed;
            SyncAutoHeight(true);
            UpdateCardBackdropTimer();
        });
    }

    private void CloseAddImmediate()
    {
        if (!addOpen) return;
        addOpen = false;
        addClosing = false;
        mainLayer.IsHitTestVisible = true;
        addLayer.Visibility = Visibility.Collapsed;
        SetMainRecede(0.0, false, 0, null);
    }

    private void SelectSearchMode(string mode)
    {
        if (mode == addView.Mode) return;
        suppressAddEvents = true;
        addView.SetMode(mode, ResourceBrush, window.FontWeight, (FontWeight)window.Resources["UiStrongWeight"]);
        suppressAddEvents = false;
        SyncSegmentThumb(true);
        TriggerSearchScan(true);
    }

    private void SyncSegmentThumb(bool animate)
    {
        double half = addSegmentInner.ActualWidth / 2.0;
        if (half <= 2) return;
        double targetX = addView.Mode == "bluetooth" ? half : 0.0;
        TranslateTransform slide = Motion.OffsetOf(addSegmentThumb);
        Motion.To(slide, TranslateTransform.XProperty, targetX, animate ? 340 : 0, Motion.Gentle);
        if (animate && Motion.Enabled)
        {
            ScaleTransform scale = Motion.ScaleOf(addSegmentThumb);
            var stretchX = new DoubleAnimationUsingKeyFrames { FillBehavior = FillBehavior.Stop };
            stretchX.KeyFrames.Add(new EasingDoubleKeyFrame(1.028, KeyTime.FromTimeSpan(TimeSpan.FromMilliseconds(100)), Motion.Snappy));
            stretchX.KeyFrames.Add(new EasingDoubleKeyFrame(1.0, KeyTime.FromTimeSpan(TimeSpan.FromMilliseconds(360)), Motion.Bouncy));
            var stretchY = new DoubleAnimationUsingKeyFrames { FillBehavior = FillBehavior.Stop };
            stretchY.KeyFrames.Add(new EasingDoubleKeyFrame(0.985, KeyTime.FromTimeSpan(TimeSpan.FromMilliseconds(100)), Motion.Snappy));
            stretchY.KeyFrames.Add(new EasingDoubleKeyFrame(1.0, KeyTime.FromTimeSpan(TimeSpan.FromMilliseconds(360)), Motion.Bouncy));
            scale.BeginAnimation(ScaleTransform.ScaleXProperty, stretchX, HandoffBehavior.SnapshotAndReplace);
            scale.BeginAnimation(ScaleTransform.ScaleYProperty, stretchY, HandoffBehavior.SnapshotAndReplace);
        }
    }

    private void TriggerSearchScan(bool force = false)
    {
        if (!force && addView.Busy) return;
        suppressAddEvents = true;
        addView.MarkScanning(ResourceBrush);
        suppressAddEvents = false;
        Send(new { action = "searchScan", mode = addView.Mode, showOther = addView.ShowOtherUsb });
    }

    private void OnAddViewPropertyChanged(object sender, PropertyChangedEventArgs args)
    {
        if (suppressAddEvents || !addOpen) return;
        if (args.PropertyName == "ShowOtherUsb")
            Send(new { action = "searchShowOther", enabled = addView.ShowOtherUsb });
    }

    private SearchEntry SearchEntryFromSource(object source, out Button clickedButton)
    {
        clickedButton = null;
        var dep = source as DependencyObject;
        while (dep != null && dep != addDevicesList)
        {
            if (clickedButton == null && dep is Button) clickedButton = (Button)dep;
            var fe = dep as FrameworkElement;
            if (fe != null && fe.DataContext is SearchEntry) return (SearchEntry)fe.DataContext;
            dep = VisualTreeHelper.GetParent(dep);
        }
        return null;
    }

    private void OnAddListRowClick(object sender, MouseButtonEventArgs args)
    {
        Button btn;
        SearchEntry entry = SearchEntryFromSource(args.OriginalSource, out btn);
        if (entry == null || btn != null) return;
        addView.ToggleExpanded(entry.InstanceId);
        args.Handled = true;
    }

    private void OnAddListRowMenu(object sender, MouseButtonEventArgs args)
    {
        Button btn;
        SearchEntry entry = SearchEntryFromSource(args.OriginalSource, out btn);
        if (entry == null) return;
        var menu = new ContextMenu
        {
            Style = (Style)window.FindResource("NexusMenu"),
            PlacementTarget = addDevicesList,
            Placement = PlacementMode.MousePoint,
            FontFamily = window.FontFamily,
            FontWeight = window.FontWeight
        };
        var toggle = new MenuItem { Header = entry.Expanded ? "收起识别信息" : "查看识别信息" };
        toggle.Click += delegate { addView.ToggleExpanded(entry.InstanceId); };
        menu.Items.Add(toggle);
        var copy = new MenuItem { Header = "复制设备信息" };
        copy.Click += delegate { CopySearchEntry(entry); };
        menu.Items.Add(copy);
        menu.IsOpen = true;
        args.Handled = true;
    }

    private void OnAddListButtonClick(object sender, RoutedEventArgs args)
    {
        Button btn;
        SearchEntry entry = SearchEntryFromSource(args.OriginalSource, out btn);
        if (entry == null || btn == null) return;

        if (btn.Name == "AddRowActionBtn")
        {
            Motion.Pulse(btn, 1.06);
            Send(new { action = "searchAdd", instanceId = entry.InstanceId });
        }
        else if (btn.Name == "CopyInfoBtn")
        {
            CopySearchEntry(entry);
        }
        else if (btn.Name == "AulaModelBtn")
        {
            OpenAulaModelMenu(btn, entry);
        }
        else if (btn.Name == "AulaVariantBtn")
        {
            OpenAulaVariantMenu(btn, entry);
        }
        args.Handled = true;
    }

    private void CopySearchEntry(SearchEntry entry)
    {
        try
        {
            if (!String.IsNullOrEmpty(entry.FullDetails))
                Clipboard.SetText(entry.FullDetails);
            addView.SetLocalFeedback("已复制设备信息。", ResourceBrush);
            Motion.Reveal(addFeedbackText, 0, 5, 1.0);
        }
        catch (Exception) { }
    }

    private void OpenAulaModelMenu(FrameworkElement anchor, SearchEntry entry)
    {
        var menu = new ContextMenu
        {
            Style = (Style)window.FindResource("NexusMenu"),
            PlacementTarget = anchor,
            Placement = PlacementMode.Bottom,
            MaxHeight = 280,
            FontFamily = window.FontFamily,
            FontWeight = window.FontWeight
        };
        foreach (string modelName in addView.AulaModels)
        {
            string captured = modelName;
            var item = new MenuItem { Header = captured, IsChecked = entry.SelectedModelLabel == captured };
            item.Click += delegate { Send(new { action = "searchSetModel", instanceId = entry.InstanceId, model = captured }); };
            menu.Items.Add(item);
        }
        menu.IsOpen = true;
    }

    private void OpenAulaVariantMenu(FrameworkElement anchor, SearchEntry entry)
    {
        if (entry.Variants == null || entry.Variants.Length == 0) return;
        var menu = new ContextMenu
        {
            Style = (Style)window.FindResource("NexusMenu"),
            PlacementTarget = anchor,
            Placement = PlacementMode.Bottom,
            FontFamily = window.FontFamily,
            FontWeight = window.FontWeight
        };
        for (int i = 0; i < entry.Variants.Length; i++)
        {
            int idx = i;
            VariantChoice variant = entry.Variants[i];
            var row = new StackPanel { Orientation = Orientation.Horizontal };
            row.Children.Add(new System.Windows.Shapes.Ellipse
            {
                Width = 11, Height = 11, Fill = SearchEntry.ParseHexBrush(variant.FirstColor, ResourceBrush("SoftFillBrush")),
                Stroke = ResourceBrush("DividerBrush"), StrokeThickness = 1, VerticalAlignment = VerticalAlignment.Center,
                Margin = new Thickness(0, 0, 3, 0)
            });
            row.Children.Add(new System.Windows.Shapes.Ellipse
            {
                Width = 11, Height = 11, Fill = SearchEntry.ParseHexBrush(variant.SecondColor, ResourceBrush("SoftFillBrush")),
                Stroke = ResourceBrush("DividerBrush"), StrokeThickness = 1, VerticalAlignment = VerticalAlignment.Center,
                Margin = new Thickness(0, 0, 8, 0)
            });
            row.Children.Add(new TextBlock { Text = variant.Label, VerticalAlignment = VerticalAlignment.Center });

            var item = new MenuItem { Header = row, IsChecked = entry.SelectedVariantIndex == idx };
            item.Click += delegate { Send(new { action = "searchSetVariant", instanceId = entry.InstanceId, variant = idx }); };
            menu.Items.Add(item);
        }
        menu.IsOpen = true;
    }

    // ==================== NATIVE SETTINGS SHEET & SUBPAGES ====================

    private void OpenSettings(string targetPage)
    {
        if (sheetOpen) CloseDetailsImmediate();
        if (addOpen) CloseAddImmediate();
        DisarmInstall();

        if (!settingsOpen || settingsClosing)
        {
            committedDark = dark;
            committedGlass = glass;
            committedGlassLevel = glassLevel;
            committedGpuAnimation = gpuAnimation;
            committedFont = fontChoice ?? "system";

            suppressSettingsPreview = true;
            settings.BeginSession(committedThemeMode, dark, glass, glassLevel, gpuAnimation, committedFont,
                PanelFonts.DiscoverChoices(systemFontFamily, systemFontWeight), ResourceBrush);
            suppressSettingsPreview = false;

            settingsOpen = true;
            settingsClosing = false;
            mainLayer.IsHitTestVisible = false;
            SyncAutoHeight(true);
            UpdateCardBackdropTimer();

            SelectSettingsPage(targetPage == "updates" ? "updates" : "settings", false);
            settingsHomePage.ScrollToTop();
            settingsLayer.Visibility = Visibility.Visible;
            settingsLayer.UpdateLayout();

            double travel = Math.Max(360, settingsSheet.ActualHeight > 0 ? settingsSheet.ActualHeight : window.ActualHeight);
            var slide = (TranslateTransform)settingsSheet.RenderTransform;
            slide.BeginAnimation(TranslateTransform.YProperty, null);
            slide.Y = Motion.Enabled ? travel : 0;
            settingsDim.BeginAnimation(UIElement.OpacityProperty, null);
            settingsDim.Opacity = 0;

            Motion.To(settingsDim, UIElement.OpacityProperty, 1, 250, Motion.Snappy);
            Motion.To(slide, TranslateTransform.YProperty, 0, 480, Motion.Sheet);
            SetMainRecede(1.0, true, 460, Motion.Sheet);
            SendAction("querySettings");
            if (targetPage == "updates") Send(new { action = "updateCheck", manual = true });
            Find<Button>("SettingsDoneButton").Focus();
        }
        else if (!String.IsNullOrEmpty(targetPage) && targetPage != settingsPage)
        {
            SelectSettingsPage(targetPage, true);
            if (targetPage == "updates") Send(new { action = "updateCheck", manual = true });
        }
    }

    private void OpenFontPage()
    {
        settings.ReloadFontChoices(PanelFonts.DiscoverChoices(systemFontFamily, systemFontWeight));
        SelectSettingsPage("fonts", true);
    }

    private void OpenUpdatesSubpage(bool checkIfIdle)
    {
        SelectSettingsPage("updates", true);
        SyncUpdateProgress(false);
        if (checkIfIdle) Send(new { action = "updateCheck", manual = false });
    }

    private void SelectSettingsPage(string nextPage, bool animate)
    {
        if (nextPage != "fonts" && nextPage != "updates") nextPage = "settings";
        string previous = settingsPage;
        settingsPage = nextPage;
        DisarmInstall();

        FrameworkElement incoming = PageElement(nextPage);
        FrameworkElement outgoingPage = PageElement(previous);
        string title = nextPage == "fonts" ? "界面字体" : nextPage == "updates" ? "软件更新" : "设置";
        settingsTitle.Text = title;

        bool root = nextPage == "settings";
        settingsCancelButton.Visibility = root ? Visibility.Visible : Visibility.Collapsed;
        settingsBackButton.Visibility = root ? Visibility.Collapsed : Visibility.Visible;

        if (!animate || !Motion.Enabled || incoming == outgoingPage)
        {
            ResetPageElement(settingsHomePage, nextPage == "settings");
            ResetPageElement(settingsFontsPage, nextPage == "fonts");
            ResetPageElement(settingsUpdatesPage, nextPage == "updates");
            return;
        }

        double width = settingsViewport.ActualWidth > 40 ? settingsViewport.ActualWidth : 390;
        bool pushing = previous == "settings" && nextPage != "settings";
        var inSlide = (TranslateTransform)incoming.RenderTransform;
        var outSlide = (TranslateTransform)outgoingPage.RenderTransform;

        incoming.Visibility = Visibility.Visible;
        incoming.BeginAnimation(UIElement.OpacityProperty, null);
        incoming.Opacity = pushing ? 0.35 : 0.35;
        inSlide.BeginAnimation(TranslateTransform.XProperty, null);
        inSlide.X = pushing ? width : -width * 0.24;

        Motion.Reveal(settingsTitle, 0, 0, 0.98);
        Motion.Enter(Motion.OffsetOf(settingsTitle), TranslateTransform.XProperty, pushing ? 18 : -18, 0, 0, 340, Motion.Sheet);
        if (!root) Motion.Enter(Motion.OffsetOf(settingsBackButton), TranslateTransform.XProperty, 14, 0, 0, 340, Motion.Sheet);

        Motion.To(inSlide, TranslateTransform.XProperty, 0, 420, Motion.Sheet);
        Motion.To(incoming, UIElement.OpacityProperty, 1.0, 240, Motion.Snappy);

        double outTargetX = pushing ? -width * 0.24 : width;
        Motion.To(outgoingPage, UIElement.OpacityProperty, 0.0, 190, Motion.Snappy);
        Motion.To(outSlide, TranslateTransform.XProperty, outTargetX, 400, Motion.Sheet, delegate
        {
            if (settingsPage != previous)
            {
                outgoingPage.Visibility = Visibility.Collapsed;
                outgoingPage.BeginAnimation(UIElement.OpacityProperty, null);
                outgoingPage.Opacity = 1.0;
            }
        });
    }

    private FrameworkElement PageElement(string name)
    {
        if (name == "fonts") return settingsFontsPage;
        if (name == "updates") return settingsUpdatesPage;
        return settingsHomePage;
    }

    private static void ResetPageElement(FrameworkElement element, bool visible)
    {
        var slide = (TranslateTransform)element.RenderTransform;
        slide.BeginAnimation(TranslateTransform.XProperty, null);
        slide.X = 0;
        element.BeginAnimation(UIElement.OpacityProperty, null);
        element.Opacity = 1.0;
        element.Visibility = visible ? Visibility.Visible : Visibility.Collapsed;
    }

    private void PickThemeCard(string mode)
    {
        suppressSettingsPreview = true;
        settings.AutoTheme = false;
        settings.SetThemeMode(mode, mode == "dark", ResourceBrush);
        suppressSettingsPreview = false;
        ApplyLivePreview();
    }

    private void OnSettingsPropertyChanged(object sender, PropertyChangedEventArgs args)
    {
        if (suppressSettingsPreview || !settingsOpen) return;
        if (args.PropertyName == "AutoTheme")
        {
            suppressSettingsPreview = true;
            if (settings.AutoTheme)
                settings.SetThemeMode("system", ReadSystemDark(), ResourceBrush);
            else
                settings.SetThemeMode(dark ? "dark" : "light", dark, ResourceBrush);
            suppressSettingsPreview = false;
            ApplyLivePreview();
        }
        else if (args.PropertyName == "GlassBackdrop" || args.PropertyName == "GlassLevel" || args.PropertyName == "GpuAnimation")
        {
            ApplyLivePreview();
        }
        else if (args.PropertyName == "UpdateAutoCheck")
        {
            Send(new { action = "updateAuto", enabled = settings.UpdateAutoCheck });
        }
    }

    private void OnFontChoiceClick(object sender, RoutedEventArgs args)
    {
        var button = args.OriginalSource as Button;
        if (button == null)
        {
            var dep = args.OriginalSource as DependencyObject;
            while (dep != null && !(dep is Button)) dep = VisualTreeHelper.GetParent(dep);
            button = dep as Button;
        }
        if (button == null || !(button.Tag is string)) return;
        string key = (string)button.Tag;
        settings.SelectFont(key);
        ApplyLivePreview();
    }

    private void OnIntervalClick(object sender, RoutedEventArgs args)
    {
        var anchor = (FrameworkElement)sender;
        var menu = new ContextMenu
        {
            Style = (Style)window.FindResource("NexusMenu"),
            PlacementTarget = anchor,
            Placement = PlacementMode.Bottom,
            HorizontalOffset = Math.Max(0, anchor.ActualWidth - 170),
            FontFamily = window.FontFamily,
            FontWeight = window.FontWeight
        };
        for (int i = 0; i < IntervalSeconds.Length; i++)
        {
            int sec = IntervalSeconds[i];
            string label = IntervalLabels[i];
            var item = new MenuItem { Header = label, IsChecked = settings.IntervalSeconds == sec };
            item.Click += delegate { settings.SetInterval(sec, label); };
            menu.Items.Add(item);
        }
        menu.IsOpen = true;
    }

    private void ApplyLivePreview()
    {
        bool targetDark = settings.ThemeMode == "system" ? ReadSystemDark() : settings.ThemeMode == "dark";
        bool targetGlass = settings.GlassBackdrop;
        double targetLevel = ClampGlassLevel(settings.GlassLevel);
        bool targetGpu = settings.GpuAnimation;
        bool backdropChanged = targetDark != dark || targetGlass != glass;
        gpuAnimation = targetGpu;
        Motion.GpuEnabled = gpuAnimation;
        glassLevel = targetLevel;
        SetTheme(targetDark, true);
        SetFont(settings.FontChoice);
        glass = targetGlass;
        if (backdropChanged) RefreshBackdrop();
        else ApplyGlassMaterial();
    }

    private void ApplySettingsState(PanelMessage message)
    {
        committedThemeMode = String.IsNullOrEmpty(message.themeMode) ? "system" : message.themeMode;
        if (message.glassBackdrop.HasValue) committedGlass = message.glassBackdrop.Value;
        if (message.glassLevel.HasValue) committedGlassLevel = ClampGlassLevel(message.glassLevel.Value);
        if (message.gpuAnimation.HasValue) committedGpuAnimation = message.gpuAnimation.Value;
        if (!settingsOpen) return;
        suppressSettingsPreview = true;
        settings.ApplyBackendState(message, IntervalSeconds, IntervalLabels, ResourceBrush);
        suppressSettingsPreview = false;
        ApplyLivePreview();
        SyncUpdateProgress(false);
    }

    private void SaveSettings()
    {
        settings.SetSaveError(String.Empty);
        Send(new
        {
            action = "saveSettings",
            interval = settings.IntervalSeconds,
            autoRefresh = settings.AutoRefresh,
            autostart = settings.Autostart,
            lowBatteryAlert = settings.LowBatteryAlert,
            gpuAnimation = settings.GpuAnimation,
            themeMode = settings.ThemeMode,
            glassBackdrop = settings.GlassBackdrop,
            glassLevel = Math.Round(settings.GlassLevel, 3),
            uiFont = settings.FontChoice
        });
    }

    private void ApplySettingsSaved(PanelMessage message)
    {
        if (message.ok)
        {
            committedThemeMode = settings.ThemeMode;
            committedDark = dark;
            committedGlass = glass;
            committedGlassLevel = glassLevel;
            committedGpuAnimation = gpuAnimation;
            committedFont = fontChoice ?? "system";
            DismissSettingsSheet();
        }
        else
        {
            settings.SetSaveError(String.IsNullOrEmpty(message.error) ? "设置保存失败，请重试" : message.error);
        }
    }

    private void CancelSettings()
    {
        if (!settingsOpen || settingsClosing) return;
        RevertPreview();
        DismissSettingsSheet();
    }

    private void CancelSettingsImmediate()
    {
        if (!settingsOpen) return;
        RevertPreview();
        DisarmInstall();
        settingsOpen = false;
        settingsClosing = false;
        mainLayer.IsHitTestVisible = true;
        settingsLayer.Visibility = Visibility.Collapsed;
        SetMainRecede(0.0, false, 0, null);
    }

    private void RevertPreview()
    {
        bool backdropChanged = dark != committedDark || glass != committedGlass;
        gpuAnimation = committedGpuAnimation;
        Motion.GpuEnabled = gpuAnimation;
        glassLevel = committedGlassLevel;
        SetTheme(committedDark, true);
        SetFont(committedFont);
        glass = committedGlass;
        if (backdropChanged) RefreshBackdrop();
        else ApplyGlassMaterial();
    }

    private void DismissSettingsSheet()
    {
        if (!settingsOpen || settingsClosing) return;
        settingsClosing = true;
        DisarmInstall();
        mainLayer.IsHitTestVisible = true;

        double travel = Math.Max(360, settingsSheet.ActualHeight > 0 ? settingsSheet.ActualHeight : window.ActualHeight);
        var slide = (TranslateTransform)settingsSheet.RenderTransform;
        Motion.To(settingsDim, UIElement.OpacityProperty, 0, 220, Motion.Snappy);
        SetMainRecede(0.0, true, 340, Motion.Smooth);
        Motion.To(slide, TranslateTransform.YProperty, travel, 280, Motion.Standard, delegate
        {
            if (!settingsClosing) return;
            settingsOpen = false;
            settingsClosing = false;
            settingsLayer.Visibility = Visibility.Collapsed;
            SyncAutoHeight(true);
            UpdateCardBackdropTimer();
        });
    }

    private void OnUpdatePrimaryClick(object sender, RoutedEventArgs args)
    {
        if (settings.UpdateAction == "install")
        {
            if (!installArmed)
            {
                installArmed = true;
                settings.SetInstallArmed(true);
                Motion.Pulse(Find<Button>("UpdatePrimaryButton"), 1.03);
                installConfirmTimer.Stop();
                installConfirmTimer.Start();
                return;
            }
            DisarmInstall();
            SendAction("updateInstall");
            return;
        }
        DisarmInstall();
        Send(new { action = "updatePrimary", mode = settings.UpdateAction });
    }

    private void DisarmInstall()
    {
        installConfirmTimer.Stop();
        if (!installArmed) return;
        installArmed = false;
        settings.SetInstallArmed(false);
    }

    private void SyncUpdateProgress(bool animate)
    {
        double trackWidth = updateProgressTrack.ActualWidth;
        if (trackWidth <= 0) return;
        double target = Math.Max(0, Math.Min(100, settings.UpdateProgress)) / 100.0 * trackWidth;
        if (target > 0 && target < 6) target = 6;
        Motion.To(updateProgressFill, FrameworkElement.WidthProperty, target, animate ? 260 : 0, Motion.Snappy);
    }

    private static void OpenExternal(string url)
    {
        try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }
        catch (Exception) { }
    }
}

internal static class PanelFonts
{
    private const string PuHuiTiName = "Alibaba PuHuiTi 2.0";
    private static readonly string FontDirectory =
        Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", "assets", "fonts"));
    private static readonly FontFamily LocalPuHuiTi =
        new FontFamily(new Uri(FontDirectory + Path.DirectorySeparatorChar), "./#" + PuHuiTiName);
    private static readonly FontFamily InstalledPuHuiTi = new FontFamily(PuHuiTiName);
    private static readonly string[] Choices =
    {
        "alibaba-thin", "alibaba-light", "alibaba-regular", "alibaba-medium", "alibaba-semibold",
        "alibaba-bold", "alibaba-extrabold", "alibaba-heavy", "alibaba-black"
    };
    private static readonly string[] Labels =
    {
        "纤细", "细体", "常规", "中等", "半粗", "粗体", "特粗", "重体", "最粗"
    };
    private static readonly int[] Weights = { 250, 300, 400, 500, 600, 700, 800, 900, 999 };

    internal static List<FontOption> DiscoverChoices(FontFamily systemFamily, FontWeight systemWeight)
    {
        var list = new List<FontOption>
        {
            new FontOption("system", "系统默认", systemFamily, systemWeight, true)
        };
        for (int i = 0; i < Choices.Length; i++)
        {
            FontFamily family;
            FontWeight body, emphasis;
            if (TryResolve(Choices[i], out family, out body, out emphasis))
                list.Add(new FontOption(Choices[i], "阿里巴巴普惠体 · " + Labels[i], family, body, false));
        }
        return list;
    }

    internal static bool TryResolve(string choice, out FontFamily family, out FontWeight body, out FontWeight emphasis)
    {
        family = null;
        body = emphasis = FontWeights.Normal;
        int index = Array.IndexOf(Choices, choice);
        if (index < 0) return false;

        body = FontWeight.FromOpenTypeWeight(Weights[index]);
        string directory = null;
        if (Directory.Exists(FontDirectory) && HasTypeface(LocalPuHuiTi, body, FontDirectory))
        {
            family = LocalPuHuiTi;
            directory = FontDirectory;
        }
        else if (HasTypeface(InstalledPuHuiTi, body, null))
        {
            family = InstalledPuHuiTi;
        }
        else return false;

        emphasis = FontWeight.FromOpenTypeWeight(Weights[Math.Min(index + 1, Weights.Length - 1)]);
        if (emphasis != body && !HasTypeface(family, emphasis, directory)) emphasis = body;
        return true;
    }

    private static bool HasTypeface(FontFamily family, FontWeight weight, string directory)
    {
        try
        {
            GlyphTypeface glyph;
            var typeface = new Typeface(family, FontStyles.Normal, weight, FontStretches.Normal);
            if (!typeface.TryGetGlyphTypeface(out glyph) || glyph.Weight != weight || !glyph.FontUri.IsFile)
                return false;
            if (directory != null && !String.Equals(Path.GetDirectoryName(glyph.FontUri.LocalPath), directory,
                                                     StringComparison.OrdinalIgnoreCase))
                return false;
            foreach (string name in glyph.FamilyNames.Values)
                if (String.Equals(name, PuHuiTiName, StringComparison.OrdinalIgnoreCase)) return true;
            return false;
        }
        catch (IOException) { return false; }
        catch (UnauthorizedAccessException) { return false; }
        catch (ArgumentException) { return false; }
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
    private const int WindowCornerPreference = 33;
    private const int RoundCorners = 2;
    private const int CaptionColor = 35;
    private const int TextColor = 36;
    private const int SystemBackdropType = 38;
    private const int DesktopAcrylicBackdrop = 3;
    private const int NoBackdrop = 1;
    private const int DefaultColor = -1;
    private const int AccentPolicyAttribute = 19;
    private const int AcrylicBlurBehind = 4;
    private const uint DarkAcrylicTint = 0xB5251E18U;

    private static int SetAttribute(IntPtr window, int attribute, int value)
    {
        return DwmSetWindowAttribute(window, attribute, ref value, sizeof(int));
    }

    internal static void EnableRoundedCorners(IntPtr window) { SetAttribute(window, WindowCornerPreference, RoundCorners); }

    internal static void SetTheme(IntPtr window, bool dark) { SetAttribute(window, UseImmersiveDarkMode, dark ? 1 : 0); }

    internal static bool IsBatterySaverEnabled()
    {
        SystemPowerStatus status;
        return GetSystemPowerStatus(out status) && status.SystemStatusFlag == 1;
    }

    internal static void AllowBackendFocus(int processId) { AllowSetForegroundWindow(processId); }

    internal static bool TryEnable(IntPtr window, bool dark)
    {
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
    private struct ScreenPoint { public int X, Y; }

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

// ==================== WIRE DTOs ====================

public sealed class PanelMessage
{
    public string type { get; set; }
    public string theme { get; set; }
    public string themeMode { get; set; }
    public string font { get; set; }
    public string summary { get; set; }
    public string updated { get; set; }
    public bool summaryLow { get; set; }
    public bool refreshing { get; set; }
    public bool? glass { get; set; }
    public double? glassLevel { get; set; }
    public bool? gpu { get; set; }
    public string earbudNote { get; set; }
    public RingData[] rings { get; set; }
    public DeviceData[] devices { get; set; }

    // Settings & Updates fields
    public string page { get; set; }
    public bool? glassBackdrop { get; set; }
    public int? interval { get; set; }
    public bool? autoRefresh { get; set; }
    public bool? autostart { get; set; }
    public bool? lowBatteryAlert { get; set; }
    public bool? gpuAnimation { get; set; }
    public string version { get; set; }
    public string updateStatus { get; set; }
    public string updateNotes { get; set; }
    public string updateAction { get; set; }
    public string updatePrimaryLabel { get; set; }
    public bool? updatePrimaryEnabled { get; set; }
    public bool? canIgnoreUpdate { get; set; }
    public bool? updateAutoCheck { get; set; }
    public bool? hasUpdate { get; set; }
    public double? updateProgress { get; set; }
    public bool ok { get; set; }
    public string error { get; set; }
    public string feedback { get; set; }
    public bool feedbackError { get; set; }

    // Native Add Device search fields
    public string searchMode { get; set; }
    public bool? searchBusy { get; set; }
    public bool? showOther { get; set; }
    public string status { get; set; }
    public string placeholderTitle { get; set; }
    public string placeholderDetail { get; set; }
    public string hint { get; set; }
    public string[] aulaModels { get; set; }
    public SearchItemData[] searchItems { get; set; }
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
    public bool? connected { get; set; }
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
    public string status { get; set; }
    public bool low { get; set; }
    public double[] history { get; set; }
    public string historySpan { get; set; }
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

public sealed class SearchVariantData
{
    public string label { get; set; }
    public string[] colors { get; set; }
}

public sealed class SearchItemData
{
    public string instanceId { get; set; }
    public string model { get; set; }
    public string subtitle { get; set; }
    public string action { get; set; }
    public string transport { get; set; }
    public string asset { get; set; }
    public bool aulaEligible { get; set; }
    public string selectedModel { get; set; }
    public int selectedVariant { get; set; }
    public SearchVariantData[] variants { get; set; }
    public string details { get; set; }
    public string fullDetails { get; set; }
}

// ==================== VIEW MODELS ====================

public abstract class ViewState : INotifyPropertyChanged
{
    public event PropertyChangedEventHandler PropertyChanged;

    protected void Changed()
    {
        Notify(String.Empty);
    }

    protected void Notify(string name)
    {
        var changed = PropertyChanged;
        if (changed != null) changed(this, new PropertyChangedEventArgs(name));
    }
}

public sealed class PanelView : ViewState
{
    public ObservableCollection<RingEntry> Rings { get; private set; }
    public ObservableCollection<DeviceEntry> Devices { get; private set; }
    public string HeaderSummary { get; private set; }
    public string HeaderTime { get; private set; }
    public bool HasLow { get; private set; }
    public bool Refreshing { get; private set; }
    public string RefreshLabel { get; private set; }
    public string FooterNote { get; private set; }

    public PanelView()
    {
        Rings = new ObservableCollection<RingEntry>();
        Devices = new ObservableCollection<DeviceEntry>();
        HeaderSummary = "正在读取设备…";
        RefreshLabel = "刷新";
    }

    internal List<string> Update(PanelMessage message, Artwork artwork, Func<string, Brush> brush)
    {
        HeaderSummary = message.summary ?? String.Empty;
        HeaderTime = String.IsNullOrEmpty(message.updated) ? String.Empty : " · " + message.updated;
        HasLow = message.summaryLow;
        Refreshing = message.refreshing;
        RefreshLabel = message.refreshing ? "读取中" : "刷新";
        FooterNote = message.earbudNote;

        RingData[] rings = message.rings ?? new RingData[0];
        int targetRingCount = rings.Length > 0 && rings.Length < 4 ? 4 : rings.Length;
        for (int index = 0; index < targetRingCount; index++)
        {
            if (index == Rings.Count) Rings.Add(new RingEntry());
            if (index < rings.Length) Rings[index].Update(rings[index], brush);
            else Rings[index].SetPlaceholder();
        }
        while (Rings.Count > targetRingCount) Rings.RemoveAt(Rings.Count - 1);

        var added = new List<string>();
        DeviceData[] devices = message.devices ?? new DeviceData[0];
        bool canReorder = devices.Length > 1;
        for (int index = 0; index < devices.Length; index++)
        {
            DeviceData data = devices[index];
            int previous = index;
            while (previous < Devices.Count && Devices[previous].Id != data.id) previous++;
            if (previous == Devices.Count)
            {
                Devices.Insert(index, new DeviceEntry(data.id));
                if (!String.IsNullOrEmpty(data.id)) added.Add(data.id);
            }
            else if (previous != index) Devices.Move(previous, index);
            Devices[index].Update(data, canReorder, artwork, brush);
        }
        while (Devices.Count > devices.Length) Devices.RemoveAt(Devices.Count - 1);
        Changed();
        return added;
    }

    internal void RefreshBrushes(Func<string, Brush> brush)
    {
        foreach (RingEntry ring in Rings) ring.RefreshBrushes(brush);
        foreach (DeviceEntry device in Devices) device.RefreshBrushes(brush);
    }
}

public sealed class RingEntry : ViewState
{
    public string Id { get; private set; }
    public string Label { get; private set; }
    public int? Level { get; private set; }
    public double LevelValue { get; private set; }
    public bool Charging { get; private set; }
    public bool IsPlaceholder { get; private set; }
    public Visibility GhostVisibility { get { return IsPlaceholder ? Visibility.Visible : Visibility.Collapsed; } }
    public Visibility ContentVisibility { get { return IsPlaceholder ? Visibility.Collapsed : Visibility.Visible; } }
    public string Glyph { get; private set; }
    public Brush Color { get; private set; }
    public Brush TextBrush { get; private set; }

    internal void SetPlaceholder()
    {
        Id = null;
        Label = String.Empty;
        Level = null;
        LevelValue = 0;
        Charging = false;
        IsPlaceholder = true;
        Changed();
    }

    internal void Update(RingData data, Func<string, Brush> brush)
    {
        IsPlaceholder = false;
        Id = data.id;
        Label = data.label;
        Charging = data.charging;
        Glyph = DeviceGlyph.For(data.kind);
        Level = data.level.HasValue ? (int?)Math.Min(100, Math.Max(0, data.level.Value)) : null;
        LevelValue = Level ?? 0;
        RefreshBrushes(brush);
        Changed();
    }

    internal void RefreshBrushes(Func<string, Brush> brush)
    {
        if (IsPlaceholder) return;
        int level = Level ?? 0;
        Color = brush(Charging || level > 60 ? "HealthyRingBrush" : level > 20 ? "MediumRingBrush" : "LowRingBrush");
        TextBrush = brush(Level.HasValue && level <= 20 && !Charging ? "WarningTextBrush" : Level.HasValue ? "TextBrush" : "MutedTextBrush");
        Changed();
    }
}

public sealed class DeviceEntry : ViewState
{
    private bool low;
    private bool chargingPower;

    public string Id { get; private set; }
    public string Name { get; private set; }
    public string Status { get; private set; }
    public string Connection { get; private set; }
    public string Power { get; private set; }
    public string Glyph { get; private set; }
    public ImageSource Artwork { get; private set; }
    public Brush StatusBrush { get; private set; }
    public Brush PowerBrush { get; private set; }
    public bool Disconnected { get; private set; }
    public bool CanReorder { get; private set; }
    public bool CanRemove { get { return !String.IsNullOrEmpty(Id); } }
    internal DeviceDetails Details { get; private set; }

    internal DeviceEntry(string id) { Id = id; }

    internal void Update(DeviceData data, bool canReorder, Artwork artwork, Func<string, Brush> brush)
    {
        Name = data.name;
        Status = data.status;
        Connection = data.connection;
        Power = data.power;
        Glyph = DeviceGlyph.For(data.kind);
        Details = data.details;
        Artwork = artwork.Load(data.asset);
        low = data.low;
        CanReorder = canReorder;
        Disconnected = data.connected == false
            || (data.status != null && data.status.IndexOf("未连接", StringComparison.Ordinal) >= 0);
        chargingPower = data.power != null && data.power.IndexOf("充电", StringComparison.Ordinal) >= 0
            && data.power.IndexOf("未充电", StringComparison.Ordinal) < 0;
        RefreshBrushes(brush);
        Changed();
    }

    internal void RefreshBrushes(Func<string, Brush> brush)
    {
        StatusBrush = brush(low ? "WarningTextBrush" : "MutedTextBrush");
        PowerBrush = brush(chargingPower ? "HealthyTextBrush" : "MutedTextBrush");
        Changed();
    }
}

public sealed class PageDot
{
    public string Id { get; set; }
    public double DotWidth { get; set; }
    public Brush DotBrush { get; set; }
}

public sealed class DetailComponentRow
{
    public string Label { get; set; }
    public string Level { get; set; }
    public string Time { get; set; }
    public bool Charging { get; set; }
    public bool IsFirst { get; set; }
}

public sealed class DetailItemRow
{
    public string Label { get; set; }
    public string Value { get; set; }
    public bool IsFirst { get; set; }
}

public sealed class DetailView : ViewState
{
    public string Id { get; private set; }
    public string Name { get; private set; }
    public string Glyph { get; private set; }
    public ImageSource Artwork { get; private set; }
    public int? LevelValue { get; private set; }
    public string LevelText { get; private set; }
    public Brush LevelBrush { get; private set; }
    public string Caption { get; private set; }
    public Brush StatusBrush { get; private set; }
    public string Timestamp { get; private set; }
    public string Footer { get; private set; }
    public string Source { get; private set; }
    public bool CanRemove { get; private set; }
    public string RemoveLabel { get; private set; }
    public bool HasMultipleDevices { get { return PageDots.Count > 1; } }
    public bool HasHistory { get { return HistoryPoints != null && HistoryPoints.Count > 0; } }
    public IList<double> HistoryPoints { get; private set; }
    public Brush SparklineBrush { get; private set; }
    public string TrendSummary { get; private set; }
    public string HistoryStartLabel { get; private set; }
    public bool HasComponents { get { return Components.Count > 0; } }
    public bool HasRows { get { return Rows.Count > 0; } }
    public ObservableCollection<PageDot> PageDots { get; private set; }
    public ObservableCollection<DetailComponentRow> Components { get; private set; }
    public ObservableCollection<DetailItemRow> Rows { get; private set; }

    public DetailView()
    {
        PageDots = new ObservableCollection<PageDot>();
        Components = new ObservableCollection<DetailComponentRow>();
        Rows = new ObservableCollection<DetailItemRow>();
        RemoveLabel = "移除设备";
    }

    internal void Update(DeviceEntry entry, ObservableCollection<DeviceEntry> allDevices, Func<string, Brush> brush)
    {
        Id = entry.Id;
        Name = entry.Name;
        Glyph = entry.Glyph;
        Artwork = entry.Artwork;
        CanRemove = entry.CanRemove;

        PageDots.Clear();
        if (allDevices != null && allDevices.Count > 1)
        {
            for (int i = 0; i < allDevices.Count; i++)
            {
                bool active = allDevices[i].Id == entry.Id;
                PageDots.Add(new PageDot
                {
                    Id = allDevices[i].Id,
                    DotWidth = active ? 16.0 : 6.0,
                    DotBrush = brush(active ? "AccentTextBrush" : "GrabberBrush")
                });
            }
        }

        DeviceDetails info = entry.Details;
        if (info != null)
        {
            LevelText = String.IsNullOrEmpty(info.level) ? "--" : info.level;
            LevelValue = ParsePercent(info.level);
            LevelBrush = brush(info.low ? "WarningTextBrush" : LevelValue.HasValue ? "TextBrush" : "MutedTextBrush");
            Caption = !String.IsNullOrEmpty(info.status) ? info.status : (info.caption ?? String.Empty);
            StatusBrush = brush(info.low ? "WarningTextBrush" : "MutedTextBrush");
            Timestamp = info.timestamp ?? String.Empty;
            Footer = info.detail ?? String.Empty;
            Source = info.source ?? String.Empty;

            // Populate sparkline points & summary
            double[] rawHistory = info.history;
            if ((rawHistory == null || rawHistory.Length == 0) && LevelValue.HasValue)
                rawHistory = new double[] { LevelValue.Value };
            if (rawHistory != null && rawHistory.Length > 0)
            {
                HistoryPoints = new List<double>(rawHistory);
                double minVal = rawHistory[0], maxVal = rawHistory[0];
                for (int i = 1; i < rawHistory.Length; i++)
                {
                    if (rawHistory[i] < minVal) minVal = rawHistory[i];
                    if (rawHistory[i] > maxVal) maxVal = rawHistory[i];
                }
                int latest = (int)Math.Round(rawHistory[rawHistory.Length - 1]);
                SparklineBrush = brush(latest > 60 ? "HealthyRingBrush" : latest > 20 ? "MediumRingBrush" : "LowRingBrush");
                HistoryStartLabel = !String.IsNullOrEmpty(info.historySpan) ? info.historySpan : "近期采样";
                TrendSummary = rawHistory.Length >= 2 && Math.Abs(maxVal - minVal) >= 1
                    ? String.Format(CultureInfo.InvariantCulture, "范围 {0:0}% – {1:0}%", minVal, maxVal)
                    : String.Format(CultureInfo.InvariantCulture, "平稳 · {0:0}%", latest);
            }
            else
            {
                HistoryPoints = null;
                TrendSummary = String.Empty;
                HistoryStartLabel = String.Empty;
            }

            Components.Clear();
            ComponentDetail[] parts = info.components ?? new ComponentDetail[0];
            for (int i = 0; i < parts.Length; i++)
            {
                Components.Add(new DetailComponentRow
                {
                    Label = parts[i].label,
                    Level = parts[i].level,
                    Time = parts[i].time,
                    Charging = parts[i].charging,
                    IsFirst = i == 0
                });
            }

            Rows.Clear();
            DetailRow[] rows = info.rows ?? new DetailRow[0];
            for (int i = 0; i < rows.Length; i++)
            {
                if (String.IsNullOrEmpty(rows[i].value)) continue;
                Rows.Add(new DetailItemRow
                {
                    Label = rows[i].label,
                    Value = rows[i].value,
                    IsFirst = Rows.Count == 0
                });
            }
        }
        else
        {
            LevelText = "--";
            LevelValue = null;
            LevelBrush = brush("MutedTextBrush");
            Caption = entry.Status ?? String.Empty;
            StatusBrush = entry.StatusBrush;
            Timestamp = Footer = Source = String.Empty;
            HistoryPoints = null;
            Components.Clear();
            Rows.Clear();
        }
        Changed();
    }

    internal void SetRemoveLabel(string text)
    {
        RemoveLabel = text;
        Notify("RemoveLabel");
    }

    private static int? ParsePercent(string raw)
    {
        if (String.IsNullOrEmpty(raw)) return null;
        string trimmed = raw.Trim();
        if (trimmed.EndsWith("%", StringComparison.Ordinal))
            trimmed = trimmed.Substring(0, trimmed.Length - 1).Trim();
        int value;
        if (Int32.TryParse(trimmed, NumberStyles.Integer, CultureInfo.InvariantCulture, out value))
            return Math.Max(0, Math.Min(100, value));
        return null;
    }
}

public sealed class VariantChoice
{
    public string Label { get; set; }
    public string FirstColor { get; set; }
    public string SecondColor { get; set; }
}

public sealed class SearchEntry : ViewState
{
    private bool expanded;

    public string InstanceId { get; private set; }
    public string Model { get; private set; }
    public string Subtitle { get; private set; }
    public string ActionLabel { get; private set; }
    public bool ShowAddedLabel { get { return ActionLabel == "已添加"; } }
    public bool ShowActionButton { get { return ActionLabel != "已添加"; } }
    public ImageSource Artwork { get; private set; }
    public string Glyph { get; private set; }
    public bool AulaEligible { get; private set; }
    public string SelectedModelLabel { get; private set; }
    public int SelectedVariantIndex { get; private set; }
    public string SelectedVariantLabel { get; private set; }
    public bool HasVariants { get { return Variants != null && Variants.Length > 0; } }
    public Brush PrimarySwatch { get; private set; }
    public Brush SecondarySwatch { get; private set; }
    public VariantChoice[] Variants { get; private set; }
    public string DetailsText { get; private set; }
    public string FullDetails { get; private set; }
    public bool IsFirst { get; set; }

    public bool Expanded
    {
        get { return expanded; }
        set { if (expanded != value) { expanded = value; Notify("Expanded"); } }
    }

    internal SearchEntry(string instanceId)
    {
        InstanceId = instanceId;
    }

    internal void Update(SearchItemData data, bool isFirst, Artwork artwork, Func<string, Brush> brush)
    {
        IsFirst = isFirst;
        Model = data.model ?? String.Empty;
        Subtitle = data.subtitle ?? String.Empty;
        ActionLabel = String.IsNullOrEmpty(data.action) ? "添加" : data.action;
        Artwork = artwork.Load(data.asset);
        Glyph = data.transport == "bluetooth" ? "\uE702" : "\uE88E";
        AulaEligible = data.aulaEligible;
        SelectedModelLabel = String.IsNullOrEmpty(data.selectedModel) ? "自动识别" : data.selectedModel;
        DetailsText = data.details ?? String.Empty;
        FullDetails = data.fullDetails ?? DetailsText;

        SearchVariantData[] rawVars = data.variants ?? new SearchVariantData[0];
        Variants = new VariantChoice[rawVars.Length];
        for (int i = 0; i < rawVars.Length; i++)
        {
            string[] cols = rawVars[i].colors ?? new string[0];
            Variants[i] = new VariantChoice
            {
                Label = rawVars[i].label ?? ("配色 " + (i + 1)),
                FirstColor = cols.Length > 0 ? cols[0] : null,
                SecondColor = cols.Length > 1 ? cols[cols.Length - 1] : (cols.Length > 0 ? cols[0] : null)
            };
        }
        SelectedVariantIndex = Variants.Length > 0 ? Math.Max(0, Math.Min(Variants.Length - 1, data.selectedVariant)) : 0;
        if (Variants.Length > 0)
        {
            VariantChoice active = Variants[SelectedVariantIndex];
            SelectedVariantLabel = active.Label;
            Brush fallback = brush("SoftFillBrush");
            PrimarySwatch = ParseHexBrush(active.FirstColor, fallback);
            SecondarySwatch = ParseHexBrush(active.SecondColor, fallback);
        }
        else
        {
            SelectedVariantLabel = String.Empty;
            PrimarySwatch = SecondarySwatch = brush("SoftFillBrush");
        }
        Changed();
    }

    internal static Brush ParseHexBrush(string hex, Brush fallback)
    {
        if (String.IsNullOrEmpty(hex)) return fallback;
        try
        {
            object converted = ColorConverter.ConvertFromString(hex);
            if (converted is Color)
            {
                var solid = new SolidColorBrush((Color)converted);
                solid.Freeze();
                return solid;
            }
        }
        catch (FormatException) { }
        return fallback;
    }
}

public sealed class AddDeviceView : ViewState
{
    private bool showOtherUsb;
    private bool feedbackError;
    private string expandedInstanceId;

    public string Mode { get; private set; }
    public bool Busy { get; private set; }
    public bool CanRescan { get { return !Busy; } }
    public string RescanLabel { get { return Busy ? "搜索中…" : "重新搜索"; } }
    public bool IsUsbMode { get { return Mode != "bluetooth"; } }
    public string StatusText { get; private set; }
    public string PlaceholderTitle { get; private set; }
    public string PlaceholderDetail { get; private set; }
    public bool ShowPlaceholder { get { return !String.IsNullOrEmpty(PlaceholderTitle); } }
    public bool HasItems { get { return Items.Count > 0; } }
    public string FeedbackText { get; private set; }
    public Brush FeedbackBrush { get; private set; }
    public string HintText { get; private set; }
    public Brush UsbTabBrush { get; private set; }
    public Brush BtTabBrush { get; private set; }
    public FontWeight UsbTabWeight { get; private set; }
    public FontWeight BtTabWeight { get; private set; }
    public string[] AulaModels { get; private set; }
    public ObservableCollection<SearchEntry> Items { get; private set; }

    public bool ShowOtherUsb
    {
        get { return showOtherUsb; }
        set { if (showOtherUsb != value) { showOtherUsb = value; Notify("ShowOtherUsb"); } }
    }

    public AddDeviceView()
    {
        Mode = "usb";
        Items = new ObservableCollection<SearchEntry>();
        AulaModels = new[] { "自动识别" };
        StatusText = "准备搜索…";
        PlaceholderTitle = "正在查找设备…";
        PlaceholderDetail = String.Empty;
        FeedbackText = String.Empty;
        HintText = "打开后也会列出无法确认能否读取电量的 USB 设备。点开狼蛛键盘一行可以更改型号和配色。";
    }

    internal void ResetForOpen(Func<string, Brush> brush, FontWeight normal, FontWeight strong)
    {
        FeedbackText = String.Empty;
        expandedInstanceId = null;
        RefreshBrushes(brush, normal, strong);
        Changed();
    }

    internal void SetMode(string mode, Func<string, Brush> brush, FontWeight normal, FontWeight strong)
    {
        Mode = mode == "bluetooth" ? "bluetooth" : "usb";
        expandedInstanceId = null;
        FeedbackText = String.Empty;
        Items.Clear();
        HintText = Mode == "bluetooth"
            ? "列表来自 Windows 已配对的蓝牙设备。新设备请先在 Windows「蓝牙和其他设备」里配对。"
            : "打开后也会列出无法确认能否读取电量的 USB 设备。点开狼蛛键盘一行可以更改型号和配色。";
        RefreshBrushes(brush, normal, strong);
        Changed();
    }

    internal void MarkScanning(Func<string, Brush> brush)
    {
        Busy = true;
        StatusText = Mode == "bluetooth" ? "正在读取已配对蓝牙设备…" : "正在扫描 USB 接收器…";
        PlaceholderTitle = "正在查找设备…";
        PlaceholderDetail = String.Empty;
        Items.Clear();
        FeedbackBrush = brush("MutedTextBrush");
        Changed();
    }

    internal void Apply(PanelMessage message, Artwork artwork, Func<string, Brush> brush, FontWeight normal, FontWeight strong)
    {
        if (!String.IsNullOrEmpty(message.searchMode) && message.searchMode != Mode)
        {
            Mode = message.searchMode;
            if (message.feedback == null) FeedbackText = String.Empty;
        }
        if (message.searchBusy.HasValue) Busy = message.searchBusy.Value;
        if (message.showOther.HasValue) showOtherUsb = message.showOther.Value;
        if (message.status != null) StatusText = message.status;
        PlaceholderTitle = message.placeholderTitle ?? String.Empty;
        PlaceholderDetail = message.placeholderDetail ?? String.Empty;
        if (message.hint != null) HintText = message.hint;
        if (message.aulaModels != null && message.aulaModels.Length > 0) AulaModels = message.aulaModels;
        if (message.feedback != null)
        {
            FeedbackText = message.feedback;
            feedbackError = message.feedbackError;
        }

        SearchItemData[] incoming = message.searchItems ?? new SearchItemData[0];
        for (int i = 0; i < incoming.Length; i++)
        {
            SearchItemData row = incoming[i];
            int prev = i;
            while (prev < Items.Count && Items[prev].InstanceId != row.instanceId) prev++;
            if (prev == Items.Count) Items.Insert(i, new SearchEntry(row.instanceId));
            else if (prev != i) Items.Move(prev, i);
            Items[i].Update(row, i == 0, artwork, brush);
            Items[i].Expanded = !String.IsNullOrEmpty(expandedInstanceId) && row.instanceId == expandedInstanceId;
        }
        while (Items.Count > incoming.Length) Items.RemoveAt(Items.Count - 1);

        RefreshBrushes(brush, normal, strong);
        Changed();
    }

    internal void ToggleExpanded(string instanceId)
    {
        expandedInstanceId = expandedInstanceId == instanceId ? null : instanceId;
        foreach (SearchEntry item in Items)
            item.Expanded = item.InstanceId == expandedInstanceId;
    }

    internal void SetLocalFeedback(string text, Func<string, Brush> brush)
    {
        FeedbackText = text ?? String.Empty;
        feedbackError = false;
        FeedbackBrush = brush("MutedTextBrush");
        Changed();
    }

    internal void RefreshBrushes(Func<string, Brush> brush, FontWeight normal, FontWeight strong)
    {
        bool bt = Mode == "bluetooth";
        UsbTabBrush = brush(bt ? "MutedTextBrush" : "TextBrush");
        BtTabBrush = brush(bt ? "TextBrush" : "MutedTextBrush");
        UsbTabWeight = bt ? normal : strong;
        BtTabWeight = bt ? strong : normal;
        FeedbackBrush = brush(feedbackError ? "WarningTextBrush" : "MutedTextBrush");
        Changed();
    }
}

public sealed class FontOption : ViewState
{
    private bool selected;

    public string Key { get; private set; }
    public string Label { get; private set; }
    public FontFamily Family { get; private set; }
    public FontWeight Weight { get; private set; }
    public bool IsFirst { get; private set; }

    public bool Selected
    {
        get { return selected; }
        set { if (selected != value) { selected = value; Notify("Selected"); } }
    }

    public FontOption(string key, string label, FontFamily family, FontWeight weight, bool isFirst)
    {
        Key = key;
        Label = label;
        Family = family;
        Weight = weight;
        IsFirst = isFirst;
    }
}

public sealed class SettingsView : ViewState
{
    private bool autoTheme = true;
    private bool glassBackdrop = true;
    private double glassLevel = 0.48;
    private bool autoRefresh = true;
    private bool autostart = true;
    private bool lowBatteryAlert = true;
    private bool gpuAnimation = true;
    private bool updateAutoCheck;
    private bool effectiveDark;
    private bool hasUpdate;
    private string basePrimaryLabel = "检查更新";

    public string ThemeMode { get; private set; }
    public string FontChoice { get; private set; }
    public string FontLabel { get; private set; }
    public int IntervalSeconds { get; private set; }
    public string IntervalLabel { get; private set; }
    public string VersionBadge { get; private set; }
    public Brush VersionBadgeBrush { get; private set; }
    public string CurrentVersionText { get; private set; }
    public string UpdateStatus { get; private set; }
    public string UpdateNotes { get; private set; }
    public string UpdateAction { get; private set; }
    public string UpdatePrimaryLabel { get; private set; }
    public bool UpdatePrimaryEnabled { get; private set; }
    public bool CanIgnoreUpdate { get; private set; }
    public double UpdateProgress { get; private set; }
    public string ToolFeedback { get; private set; }
    public string SaveError { get; private set; }
    public Brush LightCardBorder { get; private set; }
    public Brush DarkCardBorder { get; private set; }
    public Visibility LightCheckedVisibility { get { return !effectiveDark ? Visibility.Visible : Visibility.Collapsed; } }
    public Visibility LightUncheckedVisibility { get { return effectiveDark ? Visibility.Visible : Visibility.Collapsed; } }
    public Visibility DarkCheckedVisibility { get { return effectiveDark ? Visibility.Visible : Visibility.Collapsed; } }
    public Visibility DarkUncheckedVisibility { get { return !effectiveDark ? Visibility.Visible : Visibility.Collapsed; } }
    public bool ShowGetFontHint { get { return FontChoices.Count <= 1; } }
    public ObservableCollection<FontOption> FontChoices { get; private set; }

    public bool AutoTheme
    {
        get { return autoTheme; }
        set { if (autoTheme != value) { autoTheme = value; Notify("AutoTheme"); } }
    }

    public bool GlassBackdrop
    {
        get { return glassBackdrop; }
        set { if (glassBackdrop != value) { glassBackdrop = value; Notify("GlassBackdrop"); } }
    }

    public double GlassLevel
    {
        get { return glassLevel; }
        set
        {
            double clamped = Double.IsNaN(value) || Double.IsInfinity(value) ? 0.48 : Math.Max(0.0, Math.Min(1.0, value));
            if (Math.Abs(glassLevel - clamped) > 0.0005)
            {
                glassLevel = clamped;
                Notify("GlassLevel");
                Notify("GlassLevelLabel");
            }
        }
    }

    public string GlassLevelLabel
    {
        get { return Math.Round(glassLevel * 100.0).ToString("0", CultureInfo.InvariantCulture) + "%"; }
    }

    public bool AutoRefresh
    {
        get { return autoRefresh; }
        set { if (autoRefresh != value) { autoRefresh = value; Notify("AutoRefresh"); } }
    }

    public bool Autostart
    {
        get { return autostart; }
        set { if (autostart != value) { autostart = value; Notify("Autostart"); } }
    }

    public bool LowBatteryAlert
    {
        get { return lowBatteryAlert; }
        set { if (lowBatteryAlert != value) { lowBatteryAlert = value; Notify("LowBatteryAlert"); } }
    }

    public bool GpuAnimation
    {
        get { return gpuAnimation; }
        set { if (gpuAnimation != value) { gpuAnimation = value; Notify("GpuAnimation"); } }
    }

    public bool UpdateAutoCheck
    {
        get { return updateAutoCheck; }
        set { if (updateAutoCheck != value) { updateAutoCheck = value; Notify("UpdateAutoCheck"); } }
    }

    public SettingsView()
    {
        FontChoices = new ObservableCollection<FontOption>();
        ThemeMode = "system";
        FontChoice = "system";
        FontLabel = "系统默认";
        IntervalSeconds = 60;
        IntervalLabel = "1 分钟";
        VersionBadge = "v74.0";
        CurrentVersionText = "当前版本 v74.0";
        UpdateStatus = "点击“检查更新”获取最新正式版本。";
        UpdateNotes = "新的功能改进和设备适配会在这里显示。点击“检查更新”获取最新正式版本。";
        UpdateAction = "check";
        UpdatePrimaryLabel = "检查更新";
        UpdatePrimaryEnabled = true;
        ToolFeedback = String.Empty;
        SaveError = String.Empty;
    }

    internal void BeginSession(string mode, bool isDark, bool glass, double level, bool gpu, string font, List<FontOption> choices, Func<string, Brush> brush)
    {
        ThemeMode = String.IsNullOrEmpty(mode) ? "system" : mode;
        autoTheme = ThemeMode == "system";
        effectiveDark = isDark;
        glassBackdrop = glass;
        glassLevel = Math.Max(0.0, Math.Min(1.0, level));
        gpuAnimation = gpu;
        ToolFeedback = String.Empty;
        SaveError = String.Empty;
        ReloadFontChoices(choices);
        SelectFont(font);
        RefreshBrushes(brush);
        Changed();
    }

    internal void ReloadFontChoices(List<FontOption> choices)
    {
        FontChoices.Clear();
        foreach (FontOption option in choices)
        {
            option.Selected = option.Key == FontChoice;
            FontChoices.Add(option);
        }
        UpdateFontLabel();
        Changed();
    }

    internal void SelectFont(string key)
    {
        key = String.IsNullOrEmpty(key) ? "system" : key;
        bool matched = false;
        foreach (FontOption option in FontChoices)
        {
            bool match = option.Key == key;
            option.Selected = match;
            if (match) matched = true;
        }
        if (!matched && FontChoices.Count > 0)
        {
            key = "system";
            FontChoices[0].Selected = true;
        }
        FontChoice = key;
        UpdateFontLabel();
        Changed();
    }

    private void UpdateFontLabel()
    {
        FontLabel = "系统默认";
        foreach (FontOption option in FontChoices)
        {
            if (option.Key == FontChoice)
            {
                FontLabel = option.Label.Replace("阿里巴巴普惠体 · ", "");
                break;
            }
        }
    }

    internal void SetThemeMode(string mode, bool isDark, Func<string, Brush> brush)
    {
        ThemeMode = mode;
        effectiveDark = isDark;
        RefreshBrushes(brush);
        Changed();
    }

    internal void SetInterval(int seconds, string label)
    {
        IntervalSeconds = seconds;
        IntervalLabel = label;
        Changed();
    }

    internal void ApplyBackendState(PanelMessage message, int[] secondsList, string[] labels, Func<string, Brush> brush)
    {
        if (!String.IsNullOrEmpty(message.themeMode))
        {
            ThemeMode = message.themeMode;
            autoTheme = ThemeMode == "system";
        }
        if (message.interval.HasValue)
        {
            IntervalSeconds = message.interval.Value;
            int idx = Array.IndexOf(secondsList, IntervalSeconds);
            IntervalLabel = idx >= 0 ? labels[idx] : (IntervalSeconds + " 秒");
        }
        if (message.glassBackdrop.HasValue) glassBackdrop = message.glassBackdrop.Value;
        if (message.glassLevel.HasValue)
            glassLevel = Math.Max(0.0, Math.Min(1.0, message.glassLevel.Value));
        if (message.autoRefresh.HasValue) autoRefresh = message.autoRefresh.Value;
        if (message.autostart.HasValue) autostart = message.autostart.Value;
        if (message.lowBatteryAlert.HasValue) lowBatteryAlert = message.lowBatteryAlert.Value;
        if (message.gpuAnimation.HasValue) gpuAnimation = message.gpuAnimation.Value;
        ApplyUpdate(message, brush);
        Changed();
    }

    internal void ApplyUpdate(PanelMessage message, Func<string, Brush> brush)
    {
        if (!String.IsNullOrEmpty(message.version))
        {
            CurrentVersionText = "当前版本 " + message.version;
            VersionBadge = message.hasUpdate == true ? "发现新版本" : message.version;
        }
        if (message.hasUpdate.HasValue)
        {
            hasUpdate = message.hasUpdate.Value;
            if (hasUpdate) VersionBadge = "发现新版本";
        }
        if (!String.IsNullOrEmpty(message.updateStatus)) UpdateStatus = message.updateStatus;
        if (!String.IsNullOrEmpty(message.updateNotes)) UpdateNotes = message.updateNotes;
        if (!String.IsNullOrEmpty(message.updateAction)) UpdateAction = message.updateAction;
        if (!String.IsNullOrEmpty(message.updatePrimaryLabel))
        {
            basePrimaryLabel = message.updatePrimaryLabel;
            UpdatePrimaryLabel = basePrimaryLabel;
        }
        if (message.updatePrimaryEnabled.HasValue) UpdatePrimaryEnabled = message.updatePrimaryEnabled.Value;
        if (message.canIgnoreUpdate.HasValue) CanIgnoreUpdate = message.canIgnoreUpdate.Value;
        if (message.updateAutoCheck.HasValue) updateAutoCheck = message.updateAutoCheck.Value;
        if (message.updateProgress.HasValue) UpdateProgress = message.updateProgress.Value;
        RefreshBrushes(brush);
        Changed();
    }

    internal void SetInstallArmed(bool armed)
    {
        UpdatePrimaryLabel = armed ? "确认安装" : basePrimaryLabel;
        Notify("UpdatePrimaryLabel");
    }

    internal void SetToolFeedback(string text)
    {
        ToolFeedback = text ?? String.Empty;
        Notify("ToolFeedback");
    }

    internal void SetSaveError(string text)
    {
        SaveError = text ?? String.Empty;
        Notify("SaveError");
    }

    internal void RefreshBrushes(Func<string, Brush> brush)
    {
        Brush accent = brush("AccentTextBrush");
        Brush edge = brush("DividerBrush");
        LightCardBorder = !effectiveDark ? accent : edge;
        DarkCardBorder = effectiveDark ? accent : edge;
        VersionBadgeBrush = brush(hasUpdate ? "AccentTextBrush" : "MutedTextBrush");
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
    private readonly string assets = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", "assets"));

    internal ImageSource Load(string relativePath)
    {
        if (String.IsNullOrEmpty(relativePath) || Path.IsPathRooted(relativePath)) return null;
        string absolute = Path.GetFullPath(Path.Combine(assets, "..", relativePath));
        if (!absolute.StartsWith(assets + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) return null;
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
