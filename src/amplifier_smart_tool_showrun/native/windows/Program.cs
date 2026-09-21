using System.IO;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Windows.Automation;

sealed class Fault(string code) : Exception(code);
static class Native {
    internal delegate bool EnumProc(nint hwnd, nint arg);
    [DllImport("user32.dll")] internal static extern bool EnumWindows(EnumProc callback, nint arg);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] internal static extern int GetWindowText(nint h, StringBuilder text, int count);
    [DllImport("user32.dll")] internal static extern uint GetWindowThreadProcessId(nint h, out uint pid);
    [DllImport("user32.dll")] internal static extern bool IsWindowVisible(nint h);
    [DllImport("user32.dll")] internal static extern bool IsIconic(nint h);
    [DllImport("user32.dll")] internal static extern bool GetWindowRect(nint h, out Rect rect);
    [DllImport("user32.dll")] internal static extern bool PrintWindow(nint h, nint dc, uint flags);
    [DllImport("user32.dll")] internal static extern bool SetWindowPos(nint h, nint after, int x,int y,int w,int height,uint flags);
    [DllImport("user32.dll")] internal static extern bool SetProcessDpiAwarenessContext(nint value);
    [DllImport("user32.dll")] internal static extern nint OpenInputDesktop(uint flags,bool inherit,uint access);
    [DllImport("user32.dll")] internal static extern bool CloseDesktop(nint desktop);
    [StructLayout(LayoutKind.Sequential)] internal struct Rect { public int Left,Top,Right,Bottom; }
    [DllImport("user32.dll")] internal static extern bool SetForegroundWindow(nint h);
    [DllImport("user32.dll")] internal static extern nint GetForegroundWindow();
    [DllImport("user32.dll")] internal static extern short GetAsyncKeyState(int key);
    [DllImport("user32.dll")] static extern uint SendInput(uint count, Input[] inputs, int size);
    [StructLayout(LayoutKind.Sequential)] internal struct Keyboard { public ushort Key,Scan; public uint Flags,Time; public nuint Extra; }
    [StructLayout(LayoutKind.Sequential)] internal struct Mouse { public int X,Y; public uint Data,Flags,Time; public nuint Extra; }
    [StructLayout(LayoutKind.Explicit)] internal struct InputUnion { [FieldOffset(0)] public Keyboard Keyboard; [FieldOffset(0)] public Mouse Mouse; }
    [StructLayout(LayoutKind.Sequential)] internal struct Input { public uint Type; public InputUnion Data; }
    internal static void Key(ushort key, ushort scan, uint flags) {
        var inputs=new[]{new Input{Type=1,Data=new InputUnion{Keyboard=new Keyboard{Key=key,Scan=scan,Flags=flags}}},
                         new Input{Type=1,Data=new InputUnion{Keyboard=new Keyboard{Key=key,Scan=scan,Flags=flags|2}}}};
        if(SendInput(2,inputs,Marshal.SizeOf<Input>())!=2)throw new Fault("desktop_action_uncertain");
    }
    [DllImport("user32.dll")] internal static extern bool SetCursorPos(int x,int y);
    internal static void Click() {
        var inputs=new[]{new Input{Type=0,Data=new InputUnion{Mouse=new Mouse{Flags=2}}},
                         new Input{Type=0,Data=new InputUnion{Mouse=new Mouse{Flags=4}}}};
        if(SendInput(2,inputs,Marshal.SizeOf<Input>())!=2)throw new Fault("desktop_action_uncertain");
    }
    internal static string Title(nint h) { var s=new StringBuilder(2048); GetWindowText(h,s,s.Capacity);return s.ToString(); }
}
sealed class Bridge {
    nint hwnd;
    Process? process;
    long started;
    string title="";
    AutomationElement? root;
    AutomationElement? typingElement;
    string typingText="", typingPrevious="";
    int[] typingBoundaries=Array.Empty<int>();
    int typingIndex;
    int generation;
    Dictionary<string,AutomationElement> refs=new();
    void Check() {
        var desktop=Native.OpenInputDesktop(0,false,0x0100);
        if(desktop==0) throw new Fault("desktop_session_unavailable");
        Native.CloseDesktop(desktop);
        // Title selects the initial window; editing may change it. Retain HWND/process identity.
        Native.GetWindowThreadProcessId(hwnd,out var pid);
        if(process==null || process.HasExited || process.StartTime.ToUniversalTime().Ticks!=started || pid!=process.Id ||
           !Native.IsWindowVisible(hwnd) || Native.IsIconic(hwnd))
            throw new Fault("desktop_surface_changed");
    }
    public object Bind(JsonElement request) {
        var pid=request.GetProperty("pid").GetInt32(); title=request.GetProperty("window_title").GetString()!;
        process=Process.GetProcessById(pid);
        if(process.SessionId!=Process.GetCurrentProcess().SessionId || process.SessionId==0)
            throw new Fault("desktop_session_unavailable");
        started=process.StartTime.ToUniversalTime().Ticks;
        var matches=new List<nint>();
        Native.EnumWindows((h,_)=>{Native.GetWindowThreadProcessId(h,out var owner);if(owner==pid && Native.IsWindowVisible(h) && Native.Title(h)==title)matches.Add(h);return true;},0);
        if(matches.Count!=1)throw new Fault("desktop_window_ambiguous");
        hwnd=matches[0];root=AutomationElement.FromHandle(hwnd);Check();
        return new {status="ready",pid,window_id=(long)hwnd,session_id=process.SessionId};
    }
    List<AutomationElement> Tree() {
        Check(); var nodes=new List<AutomationElement>();
        void Walk(AutomationElement e,int depth) {
            if(depth>40 || nodes.Count>=2000) throw new Fault("desktop_observation_limit");
            if(e.Current.IsPassword)throw new Fault("sensitive_surface");
            nodes.Add(e);
            var walker=TreeWalker.ControlViewWalker;
            for(var child=walker.GetFirstChild(e);child!=null;child=walker.GetNextSibling(child))Walk(child,depth+1);
        }
        Walk(root!,0);return nodes;
    }
    // UIA text providers can report CR, CRLF or LF for the same line break.
    static string NormalizeLines(string value) => value.Replace("\r\n","\n").Replace("\r","\n");
    static string Value(AutomationElement e) => e.TryGetCurrentPattern(ValuePattern.Pattern,out var v)?NormalizeLines(((ValuePattern)v).Current.Value):"";
    public object Observe() {
        var nodes=Tree(); generation++;refs.Clear(); var text=new List<string>();var controls=new List<object>();
        foreach(var e in nodes) {
            var c=e.Current;if(c.IsOffscreen)continue;
            var label=c.Name;var value=Value(e);text.Add(label);text.Add(value);
            var actions=new List<string>();
            if(c.IsEnabled && (e.TryGetCurrentPattern(InvokePattern.Pattern,out _) ||
                e.TryGetCurrentPattern(SelectionItemPattern.Pattern,out _) ||
                e.TryGetCurrentPattern(ExpandCollapsePattern.Pattern,out _) ||
                e.TryGetCurrentPattern(TogglePattern.Pattern,out _)))actions.Add("click");
            if(c.IsEnabled && e.TryGetCurrentPattern(ValuePattern.Pattern,out var v) && !((ValuePattern)v).Current.IsReadOnly)actions.Add("fill");
            if(actions.Count==0)continue;
            var reference=$"g{generation}.e{refs.Count}";refs[reference]=e;
            controls.Add(new { @ref=reference,label,value,enabled=c.IsEnabled,role=c.ControlType.ProgrammaticName,
                identity=string.Join(".",e.GetRuntimeId()),
                fill_method=c.ControlType==ControlType.DataItem && e.TryGetCurrentPattern(GridItemPattern.Pattern,out _)?"focused_grid_keyboard":"value_pattern",
                actions});
        }
        var combined=string.Join("\n",text.Where(t=>t.Length>0));
        if(combined.Length>64000)throw new Fault("desktop_observation_limit");
        return new {generation,text=combined,controls};
    }
    public object Screenshot() {
        Tree();if(!Native.GetWindowRect(hwnd,out var rect))throw new Fault("desktop_capture_failed");
        int width=rect.Right-rect.Left,height=rect.Bottom-rect.Top;
        if(width<1 || height<1 || width>7680 || height>7680)throw new Fault("desktop_capture_failed");
        using var bitmap=new Bitmap(width,height,PixelFormat.Format32bppRgb);
        using(var graphics=Graphics.FromImage(bitmap)) {
            var dc=graphics.GetHdc();bool ok;
            try {ok=Native.PrintWindow(hwnd,dc,2);} finally {graphics.ReleaseHdc(dc);}
            if(!ok)throw new Fault("desktop_capture_failed");
        }
        Tree();using var stream=new MemoryStream();bitmap.Save(stream,ImageFormat.Png);
        if(stream.Length>24*1024*1024)throw new Fault("desktop_capture_failed");
        return new {width,height,png=Convert.ToBase64String(stream.ToArray())};
    }
    public object Resize(JsonElement request) {
        Check();int width=request.GetProperty("width").GetInt32(),height=request.GetProperty("height").GetInt32();
        if(width<320 || height<320 || width>3840 || height>3840)throw new Fault("invalid_action");
        if(!Native.SetWindowPos(hwnd,0,0,0,width,height,0x0002|0x0004|0x0010))throw new Fault("desktop_resize_unavailable");
        refs.Clear();Thread.Sleep(250);Check();Native.GetWindowRect(hwnd,out var r);
        return new {width=r.Right-r.Left,height=r.Bottom-r.Top};
    }
    public object TypePreview(JsonElement request) {
        var element=typingElement;
        if(element==null)throw new Fault("invalid_action");
        try {
            Check();
            if(!Tree().Any(node=>Automation.Compare(node,element)) || !element.Current.IsEnabled ||
               element.Current.IsOffscreen || Value(element)!=typingPrevious)
                throw new Fault("desktop_action_uncertain");
            string suffix=request.GetProperty("suffix").GetString()!;
            if(suffix.Length>1 || (suffix.Length==1 && !char.IsAsciiLetter(suffix[0])))throw new Fault("invalid_action");
            int end=typingIndex==typingBoundaries.Length?typingText.Length:typingBoundaries[typingIndex];
            string preview=typingText[..end]+suffix;
            ((ValuePattern)element.GetCurrentPattern(ValuePattern.Pattern)).SetValue(preview);
            if(Value(element)!=NormalizeLines(preview))throw new Fault("desktop_action_uncertain");
            typingPrevious=NormalizeLines(preview);
            return new {status="typing"};
        } catch(Fault) {typingElement=null;throw;}
          catch {typingElement=null;throw new Fault("desktop_action_uncertain");}
    }
    public object TypeNext() {
        var element=typingElement;
        if(element==null)throw new Fault("invalid_action");
        try {
            Check();
            if(!Tree().Any(node=>Automation.Compare(node,element)) || !element.Current.IsEnabled ||
               element.Current.IsOffscreen || Value(element)!=typingPrevious)
                throw new Fault("desktop_action_uncertain");
            int start=typingIndex==typingBoundaries.Length?typingText.Length:typingBoundaries[typingIndex];
            typingIndex=Math.Min(typingBoundaries.Length,typingIndex+1);
            int end=typingIndex==typingBoundaries.Length?typingText.Length:typingBoundaries[typingIndex];
            string prefix=typingText[..end];
            ((ValuePattern)element.GetCurrentPattern(ValuePattern.Pattern)).SetValue(prefix);
            if(Value(element)!=NormalizeLines(prefix))throw new Fault("desktop_action_uncertain");
            typingPrevious=NormalizeLines(prefix);
            bool done=end==typingText.Length;
            if(done)typingElement=null;
            return new {status=done?"returned":"typing",character=typingText[start..end]};
        } catch(Fault) {typingElement=null;throw;}
          catch {typingElement=null;throw new Fault("desktop_action_uncertain");}
    }
    void CheckInputFocus() {
        Check();
        if(Native.GetForegroundWindow()!=hwnd || new[]{0x10,0x11,0x12,0x5b,0x5c}.Any(k=>Native.GetAsyncKeyState(k)<0))
            throw new Fault("desktop_action_uncertain");
        var focus=AutomationElement.FocusedElement;
        if(focus.Current.ProcessId!=process!.Id || focus.Current.IsPassword)
            throw new Fault("desktop_action_uncertain");
        var current=focus;
        for(int depth=0;current!=null && depth<40;depth++) {
            if(Automation.Compare(current,root))return;
            current=TreeWalker.ControlViewWalker.GetParent(current);
        }
        throw new Fault("desktop_action_uncertain");
    }
    void FillGrid(AutomationElement element,string text) {
        if(text.Length==0 || text.Any(char.IsControl))throw new Fault("invalid_action");
        Native.SetForegroundWindow(hwnd);
        if(element.TryGetCurrentPattern(SelectionItemPattern.Pattern,out var selection))
            ((SelectionItemPattern)selection).Select();
        element.SetFocus();Thread.Sleep(100);
        Check();
        if(Native.GetForegroundWindow()!=hwnd || !Automation.Compare(AutomationElement.FocusedElement,element))
            throw new Fault("desktop_action_uncertain");
        // Exact Unicode only, no clipboard, shortcut syntax, or caller-selected keys.
        foreach(char character in text) {
            CheckInputFocus();
            Native.Key(0,character,4);
        }
        Thread.Sleep(100);
        CheckInputFocus();
        Native.Key(0x0d,0,0);Thread.Sleep(200);
    }
    void ClickObserved(AutomationElement element) {
        Native.SetForegroundWindow(hwnd);Thread.Sleep(100);CheckInputFocus();
        var point=element.GetClickablePoint();
        var hit=AutomationElement.FromPoint(point);
        bool matched=false;
        for(int depth=0;hit!=null && depth<20;depth++) {
            if(Automation.Compare(hit,element)){matched=true;break;}
            hit=TreeWalker.ControlViewWalker.GetParent(hit);
        }
        if(!matched || !Native.SetCursorPos((int)point.X,(int)point.Y))throw new Fault("stale_ref");
        CheckInputFocus();Native.Click();
    }
    public object Act(JsonElement request) {
        Check();if(request.GetProperty("generation").GetInt32()!=generation || !refs.TryGetValue(request.GetProperty("ref").GetString()!,out var e))throw new Fault("stale_ref");
        if(!Tree().Any(node=>Automation.Compare(node,e)) || !e.Current.IsEnabled || e.Current.IsOffscreen)throw new Fault("stale_ref");
        refs.Clear();
        try {
            switch(request.GetProperty("action").GetString()) {
            case "click":
                if(e.Current.ControlType==ControlType.TabItem || e.Current.ControlType==ControlType.MenuItem ||
                   e.TryGetCurrentPattern(ExpandCollapsePattern.Pattern,out _)) {ClickObserved(e);break;}
                if(e.TryGetCurrentPattern(InvokePattern.Pattern,out var invoke))((InvokePattern)invoke).Invoke();
                else if(e.TryGetCurrentPattern(ExpandCollapsePattern.Pattern,out var expand)) {
                    var pattern=(ExpandCollapsePattern)expand;
                    if(pattern.Current.ExpandCollapseState==ExpandCollapseState.Expanded)pattern.Collapse();else pattern.Expand();
                }
                else if(e.TryGetCurrentPattern(TogglePattern.Pattern,out var toggle))((TogglePattern)toggle).Toggle();
                else if(e.TryGetCurrentPattern(SelectionItemPattern.Pattern,out var select))((SelectionItemPattern)select).Select();
                else throw new Fault("invalid_action");
                break;
            case "fill":
                var text=request.GetProperty("text").GetString()!;
                if(text.Length>4000)throw new Fault("invalid_action");
                if(e.Current.ControlType==ControlType.DataItem && e.TryGetCurrentPattern(GridItemPattern.Pattern,out _)) {
                    if(request.TryGetProperty("incremental",out var gridPaced) && gridPaced.GetBoolean())throw new Fault("invalid_action");
                    FillGrid(e,text);break;
                }
                if(request.TryGetProperty("incremental",out var incremental) && incremental.GetBoolean()) {
                    typingElement=e;typingText=text;typingPrevious=Value(e);typingIndex=0;
                    typingBoundaries=System.Globalization.StringInfo.ParseCombiningCharacters(text);
                    return new {status="typing"};
                }
                ((ValuePattern)e.GetCurrentPattern(ValuePattern.Pattern)).SetValue(text);
                if(Value(e)!=NormalizeLines(text))throw new Fault("desktop_action_uncertain");break;
            default:throw new Fault("invalid_action");
            }
        } catch(Fault){throw;} catch {throw new Fault("desktop_action_uncertain");}
        return new {status="returned"};
    }
}
static class Program {
    [STAThread] static void Main(string[] args) {
        Native.SetProcessDpiAwarenessContext(-4);
        if(args.Length!=4 || args[0]!="--port" || args[2]!="--token" || args[3].Length!=64)return;
        using var client=new TcpClient();client.Connect("127.0.0.1",int.Parse(args[1]));client.ReceiveTimeout=300000;
        using var reader=new StreamReader(client.GetStream(),Encoding.UTF8);
        using var writer=new StreamWriter(client.GetStream(),new UTF8Encoding(false)){AutoFlush=true};
        writer.WriteLine(JsonSerializer.Serialize(new {token=args[3]}));var bridge=new Bridge();
        while(reader.ReadLine() is string line) {
            object result;
            try {
                if(line.Length>65536)throw new Fault("invalid_action");
                using var doc=JsonDocument.Parse(line);var r=doc.RootElement;
                result=r.GetProperty("operation").GetString() switch {
                    "bind"=>bridge.Bind(r), "observe"=>bridge.Observe(), "screenshot"=>bridge.Screenshot(),
                    "resize"=>bridge.Resize(r), "act"=>bridge.Act(r), "type_next"=>bridge.TypeNext(), "type_preview"=>bridge.TypePreview(r),
                    "permissions"=>new {session_id=Process.GetCurrentProcess().SessionId,ready=Process.GetCurrentProcess().SessionId!=0},
                    _=>throw new Fault("invalid_action")};
            } catch(Fault e){result=new {error=e.Message};} catch {result=new {error="desktop_bridge_failed"};}
            writer.WriteLine(JsonSerializer.Serialize(result));
        }
    }
}
