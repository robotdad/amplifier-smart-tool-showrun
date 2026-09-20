using System.Diagnostics;
using System.Text;
using System.Text.Json;

sealed class Fixture : Form
{
    readonly Process host;
    readonly TextBox draft = new() { AccessibleName = "Task name", MaxLength = 100 };
    readonly TextBox note = new() { AccessibleName = "Detail note", MaxLength = 100 };
    readonly Label noteLabel = new() { Text = "Detail note" };
    readonly Label saved = new() { Text = "Saved task: none" };
    readonly Label status = new() { Text = "Ready" };
    readonly System.Windows.Forms.Timer timer = new() { Interval = 100 };
    Button save = null!, saveNote = null!, start = null!, cancel = null!;
    int generation = -1;
    JsonElement state;
    readonly bool selfTest;
    readonly Dictionary<string, string> args;

    public Fixture(Dictionary<string, string> args, bool selfTest)
    {
        this.args = args; this.selfTest = selfTest;
        Text = "Showrun Fixture - " + args["run-id"][..8];
        ClientSize = new Size(760, 460);
        FormBorderStyle = FormBorderStyle.FixedSingle;
        MaximizeBox = false;
        var info = new ProcessStartInfo(args["python"]) {
            RedirectStandardInput = true, RedirectStandardOutput = true,
            StandardInputEncoding = new UTF8Encoding(false), StandardOutputEncoding = Encoding.UTF8,
            UseShellExecute = false, CreateNoWindow = true
        };
        foreach (var value in new[] { args["host"], "--state", args["state"], "--run-id", args["run-id"], "--platform", "windows" })
            info.ArgumentList.Add(value);
        host = Process.Start(info) ?? throw new Exception("Cannot start state host");
        LabelAt("Computer-use fixture · fictional data only", 24, 20);
        LabelAt("Task name", 24, 65);
        Place(draft, 24, 95, 400, 30);
        draft.TextChanged += (_, _) => Act("set_draft", draft.Text);
        MakeButton("More options", 24, 150, () => Act("show_details"));
        MakeButton("Open dialog", 250, 150, OpenDialog);
        MakeButton("Move Save button", 476, 150, () => Act("move_save"));
        Place(noteLabel, 24, 200, 300, 25);
        Place(note, 24, 230, 400, 30);
        note.TextChanged += (_, _) => Act("set_note", note.Text);
        saveNote = MakeButton("Save note", 476, 228, () => Act("save_note"));
        start = MakeButton("Start delayed save", 24, 290, () => Act("start_delay"));
        cancel = MakeButton("Cancel operation", 250, 290, () => Act("cancel_delay"));
        Place(saved, 24, 370, 450, 30); Place(status, 24, 410, 700, 30);
        Act("snapshot");
        timer.Tick += (_, _) => Act("tick");
        Shown += OnShown;
        FormClosed += (_, _) => {
            timer.Stop(); host.StandardInput.Close();
            if (!host.WaitForExit(5000)) host.Kill();
            host.Dispose();
        };
    }
    void Place(Control control, int x, int y, int width, int height)
    { control.SetBounds(x, y, width, height); Controls.Add(control); }
    void LabelAt(string text, int x, int y) { Place(new Label { Text = text }, x, y, 650, 25); }
    Button MakeButton(string text, int x, int y, Action action)
    {
        var button = new Button { Text = text, AccessibleName = text };
        Place(button, x, y, 210, 34); button.Click += (_, _) => action(); return button;
    }
    string TextState(string key) => state.GetProperty(key).GetString()!;
    void Act(string action, string? value = null)
    {
        host.StandardInput.WriteLine(JsonSerializer.Serialize(new { action, value }));
        host.StandardInput.Flush();
        using var reply = JsonDocument.Parse(host.StandardOutput.ReadLine() ?? throw new Exception("State host closed"));
        if (reply.RootElement.TryGetProperty("error", out var error)) throw new Exception(error.GetString());
        state = reply.RootElement.GetProperty("state").Clone();
        var next = state.GetProperty("save_control_generation").GetInt32();
        if (next != generation) {
            if (save != null) { Controls.Remove(save); save.Dispose(); }
            generation = next;
            save = MakeButton("Save task", 476, next % 2 == 0 ? 92 : 345, () => Act("save"));
        }
        save.Enabled = !string.IsNullOrWhiteSpace(TextState("draft"));
        note.Visible = noteLabel.Visible = saveNote.Visible = state.GetProperty("details_visible").GetBoolean();
        saveNote.Enabled = !string.IsNullOrWhiteSpace(TextState("note_draft"));
        start.Enabled = save.Enabled && TextState("operation_status") != "pending";
        cancel.Enabled = TextState("operation_status") == "pending";
        saved.Text = "Saved task: " + (TextState("saved_task") is "" ? "none" : TextState("saved_task"));
        status.Text = TextState("status_text");
    }
    void OpenDialog()
    {
        Act("open_dialog");
        using var dialog = new Form { Text = "Confirm task", ClientSize = new Size(360, 140),
            FormBorderStyle = FormBorderStyle.FixedDialog, StartPosition = FormStartPosition.CenterParent,
            MinimizeBox = false, MaximizeBox = false };
        var label = new Label { Text = draft.Text, Bounds = new Rectangle(20, 20, 320, 30) };
        var confirm = new Button { Text = "Confirm", AccessibleName = "Confirm", DialogResult = DialogResult.OK,
            Bounds = new Rectangle(160, 80, 85, 30) };
        var cancelButton = new Button { Text = "Cancel", AccessibleName = "Cancel", DialogResult = DialogResult.Cancel,
            Bounds = new Rectangle(255, 80, 85, 30) };
        dialog.Controls.AddRange(new Control[] { label, confirm, cancelButton });
        dialog.AcceptButton = confirm; dialog.CancelButton = cancelButton;
        if (selfTest) dialog.Shown += (_, _) => dialog.BeginInvoke(() => confirm.PerformClick());
        var choice = dialog.ShowDialog(this);
        Act(choice == DialogResult.OK ? "confirm_dialog" : "cancel_dialog");
    }
    async void OnShown(object? sender, EventArgs e)
    {
        File.WriteAllText(args["ready"], JsonSerializer.Serialize(new {
            run_id = args["run-id"], platform = "windows", window_title = Text,
            pid = Environment.ProcessId, toolkit = "WinForms"
        }), Encoding.UTF8);
        timer.Start();
        if (!selfTest) return;
        if (save.Enabled) throw new Exception("Save should initially be disabled");
        draft.Text = "Widget trial"; save.PerformClick();
        if (TextState("saved_task") != "Widget trial") throw new Exception("Save failed");
        Act("move_save"); save.PerformClick();
        Act("show_details"); note.Text = "Widget note"; saveNote.PerformClick(); OpenDialog();
        if (TextState("dialog_status") != "confirmed") throw new Exception("Dialog failed");
        start.PerformClick();
        await Task.Delay(3400);
        if (TextState("operation_status") != "completed") throw new Exception("Delayed save failed");
        start.PerformClick(); cancel.PerformClick();
        await Task.Delay(3400);
        if (TextState("operation_status") != "cancelled") throw new Exception("Cancellation failed");
        using var preview = new Bitmap(Width, Height);
        DrawToBitmap(preview, new Rectangle(0, 0, Width, Height));
        preview.Save(Path.Combine(Path.GetDirectoryName(args["ready"])!, "widget-preview.png"),
                     System.Drawing.Imaging.ImageFormat.Png);
        Close();
    }
}

static class Program
{
    [STAThread]
    static void Main(string[] input)
    {
        var args = new Dictionary<string, string>(); var selfTest = false;
        for (var i = 0; i < input.Length; i++) {
            if (input[i] == "--self-test") { selfTest = true; continue; }
            args[input[i][2..]] = input[++i];
        }
        ApplicationConfiguration.Initialize();
        Application.SetUnhandledExceptionMode(UnhandledExceptionMode.ThrowException);
        Application.Run(new Fixture(args, selfTest));
    }
}
