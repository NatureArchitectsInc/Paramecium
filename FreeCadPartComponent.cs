using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using Grasshopper.Kernel;
using Rhino;
using Rhino.Geometry;
using System.Text.Json;
using System.Linq;
using Grasshopper.Kernel.Types;
using Grasshopper.GUI.Canvas;
using Grasshopper.GUI;
using Grasshopper.Kernel.Attributes;

using System.Windows.Forms;
using GH_IO.Serialization;
using System.Reflection;
using System.Reflection.Metadata.Ecma335;

namespace Paramecium
{
    public class FreeCadPartComponent : GH_Component
    {
        internal FreecadRunContext _lastContext; // ダブルクリックの処理の際に使う 
        private byte[] embeddedFcstdBytes; // バイト配列フィールドの宣言
        private string FcstdPath { get; set; } // FreeCADファイルのパス
        private Guid RuntimewiseInstanceId { get; } = Guid.NewGuid(); // コンポーネントのインスタンスに固有のIDを生成
        private bool isUserProvidedPath = false; // フィールド追加

        // このインスタンス専用の一時フォルダ（埋め込みFCStdと、実行ごとの作業フォルダを置く）
        private string InstanceTempDir => Path.Combine(Path.GetTempPath(), "Paramecium", RuntimewiseInstanceId.ToString());

        // Gui編集後の出力に関するフィールド
        internal string _pendingPythonOutput;
        internal string _pendingPythonError;
        internal bool _hasPendingOutput = false;

        /// <summary>
        /// Each implementation of GH_Component must provide a public 
        /// constructor without any arguments.
        /// Category represents the Tab in which the component will appear, 
        /// Subcategory the panel. If you use non-existing tab or panel names, 
        /// new tabs/panels will automatically be created.
        /// </summary>
        public FreeCadPartComponent()
          : base("FreeCAD Part", "FCPart",
            "Edit a parametric part in FreeCAD. Double-click to open the FreeCAD UI.",
            "Paramecium", "Paramecium")
        {
        }

        /// <summary>
        /// Registers all the input parameters for this component.
        /// </summary>
        protected override void RegisterInputParams(GH_Component.GH_InputParamManager pManager)
        {
            pManager.AddGeometryParameter("Geometry", "BaseGeom", "Base Geometry", GH_ParamAccess.list); 
            pManager.AddGeometryParameter("Referece Geometry", "RefGeo", "Referece Geometry", GH_ParamAccess.list);
            pManager.AddTextParameter("Variable Names", "VarNames", "List of variable names", GH_ParamAccess.list);
            pManager.AddNumberParameter("Variable Values", "VarValues", "List of variable values", GH_ParamAccess.list);
            pManager.AddTextParameter("FCStdPath", "Path", "FreeCAD file path", GH_ParamAccess.item);
            pManager[0].Optional = true;
            pManager[1].Optional = true;
            pManager[2].Optional = true;
            pManager[3].Optional = true;
            pManager[4].Optional = true;
        }

        /// <summary>
        /// Registers all the output parameters for this component.
        /// </summary>
        protected override void RegisterOutputParams(GH_Component.GH_OutputParamManager pManager)
        {
            pManager.AddGeometryParameter("Geometries", "Geoms", "Importer Geometry from FreeCAD", GH_ParamAccess.list);
            pManager.AddTextParameter("FCStdPath", "Path", "FreeCAD file path", GH_ParamAccess.item);
            pManager.AddTextParameter("Python Log", "Log", "Python process's log", GH_ParamAccess.item);
        }

        /// <summary>
        /// This is the method that actually does the work.
        /// </summary>
        /// <param name="DA">The DA object can be used to retrieve data from input parameters and 
        /// to store data in output parameters.</param>
        protected override void SolveInstance(IGH_DataAccess DA)
        {

            // 共通の出力変数
            string pyout = null;
            string pyerr = null;

            // コンテキスト（Run用の入力情報）を保持
            FreecadRunContext ctx = null;

            // ダブルクリック直後の処理
            if (_hasPendingOutput)
            {
                pyout = _pendingPythonOutput;
                pyerr = _pendingPythonError;
                _hasPendingOutput = false;
                ctx = _lastContext;
            }
            else
            {
                if (!TryGetInputs(DA, out ctx))
                    return;

                _lastContext = ctx;
                RunFreecadProcess(ctx, out pyout, out pyerr);
            }

            // 共通の出力処理
            DA.SetData(2, pyout);
            DA.SetData(1, ctx.FreecadFilePath);

            if (!string.IsNullOrWhiteSpace(pyerr))
            {
                AddPythonErrorMessages(pyerr);
            }

            // 通常時はジオメトリを読み込む（ダブルクリック後は再読込されるが副作用なし）
            if (ctx != null && (File.Exists(ctx.StepFilePath) || string.IsNullOrWhiteSpace(pyerr)))
            {
                ImportStepIntoRhino(ctx.StepFilePath, DA);
            }

            // 作業フォルダのクリーンアップ（このコンポーネントが作ったフォルダだけを消す）
            CleanupWorkDir(ctx);

        }

        private void CleanupWorkDir(FreecadRunContext ctx)
        {
            if (ctx == null || ctx.KeepWorkDir || string.IsNullOrEmpty(ctx.WorkDir) || !Directory.Exists(ctx.WorkDir))
                return;

            try
            {
                Directory.Delete(ctx.WorkDir, true);
            }
            catch (Exception ex)
            {
                AddRuntimeMessage(GH_RuntimeMessageLevel.Warning, $"作業フォルダの削除失敗: {ctx.WorkDir} → {ex.Message}");
            }
        }

        /// <summary>
        /// Provides an Icon for every component that will be visible in the User Interface.
        /// Icons need to be 24x24 pixels.
        /// You can add image files to your project resources and access them like this:
        /// return Resources.IconForThisComponent;
        /// </summary>
        protected override System.Drawing.Bitmap Icon => Paramecium.Properties.Resource.freecad_part_24;

        /// <summary>
        /// Each component must have a unique Guid to identify it. 
        /// It is vital this Guid doesn't change otherwise old ghx files 
        /// that use the old ID will partially fail during loading.
        /// </summary>
        public override Guid ComponentGuid => new Guid("c58b96f2-1cd7-4344-934d-182285c48020");

        public override bool Write(GH_IWriter writer)
        {
            base.Write(writer);

            if (!isUserProvidedPath && embeddedFcstdBytes != null && embeddedFcstdBytes.Length > 0)
            {
                writer.SetByteArray("fcstd", embeddedFcstdBytes);
            }

            return true;
        }

        public override bool Read(GH_IReader reader)
        {
            base.Read(reader);

            if (reader.ItemExists("fcstd"))
            {
                embeddedFcstdBytes = reader.GetByteArray("fcstd");

                //  Read時に自分で一時保存先を生成
                Directory.CreateDirectory(InstanceTempDir);
                FcstdPath = Path.Combine(InstanceTempDir, "model.FCStd");

                File.WriteAllBytes(FcstdPath, embeddedFcstdBytes);
            }

            return true;
        }

        public override void CreateAttributes()

        {
            m_attributes = new CustomAttributes(this);
        }

        public override IEnumerable<string> Keywords // 検索性向上
        {
            get
            {
                return new string[]
                {
            "freecad", "parametric", "paramecium"
                };
            }
        }

        internal class FreecadRunContext
        {
            public string FreecadFilePath;
            public string StepFilePath;
            public string JsonPath;
            public string BaseStepFilePath;
            public string RefStepFilePath;
            public string EditFilePath; // GUI編集用の作業コピー
            public string WorkDir; // 実行ごとの作業フォルダ（FCStd以外の中間ファイルはすべてここに置く）
            public bool KeepWorkDir; // 編集内容を反映できなかったときは作業フォルダを残す
            public bool EnableGui;
            public Dictionary<string, double> Variables;
            public List<GeometryBase> BaseGeometry;
            public List<GeometryBase> RefGeometry;
        }

        private bool TryGetInputs(IGH_DataAccess DA, out FreecadRunContext ctx)
        {
            ctx = new FreecadRunContext();

            string fcstdPath = default;

            // fcstdパスの生成
            if (!DA.GetData(4, ref fcstdPath)) // もしDirが入力されなかったらTempディレクトリを使う
            {
                isUserProvidedPath = false;
                fcstdPath = Path.Combine(InstanceTempDir, "model.FCStd");

                if (!Directory.Exists(InstanceTempDir))
                {
                    Directory.CreateDirectory(InstanceTempDir);
                }
            }
            else
            {
                isUserProvidedPath = true;
            }

            // FCStd以外のファイルパスは、実行のたびにRunFreecadProcessで作業フォルダ内に決める
            ctx.FreecadFilePath = fcstdPath;

            // 変数名・値
            List<string> variableNames = new List<string>() ;
            List<double> variableValues = new List<double>() ;
            DA.GetDataList(2, variableNames);
            DA.GetDataList(3, variableValues);

            if (variableNames.Count != variableValues.Count)
            {
                AddRuntimeMessage(GH_RuntimeMessageLevel.Error, "変数名と値の数が一致していません。");
                return false;
            }

            // NaN・無限大はJSONに書けず、FreeCADの寸法としても意味を持たないので止める
            for (int i = 0; i < variableValues.Count; i++)
            {
                if (!double.IsFinite(variableValues[i]))
                {
                    AddRuntimeMessage(GH_RuntimeMessageLevel.Error,
                        $"変数 '{variableNames[i]}' の値 ({variableValues[i]}) は使用できません。有限の数値を入力してください。");
                    return false;
                }
            }

            ctx.Variables = variableNames.Zip(variableValues, (k, v) => new { k, v }).ToDictionary(x => x.k, x => x.v);

            foreach (string name in variableNames)
            {
                if (!System.Text.RegularExpressions.Regex.IsMatch(name, @"^[A-Za-z_][A-Za-z0-9_]*$"))
                {
                    AddRuntimeMessage(GH_RuntimeMessageLevel.Error,
                        $"変数名 '{name}' は使用できません。使用不可の「-」が含まれているかもしれません。");
                }
            }

            // GUIフラグ
            ctx.EnableGui = false;

            // BaseGeo
            List<GeometryBase> geometries = new List<GeometryBase>();
            if (!DA.GetDataList(0, geometries) || geometries.Count == 0)
            {
                ctx.BaseGeometry = new List<GeometryBase>() ;
            }
            ctx.BaseGeometry = geometries;

            // RefGeo
            List<GeometryBase> refGeometries = new List<GeometryBase>();
            if (!DA.GetDataList(1, refGeometries))
            {
                refGeometries = new List<GeometryBase>() ;
            }
            ctx.RefGeometry = refGeometries;

            return true;
        }

        internal void RunFreecadProcess(FreecadRunContext ctx, out string pyout, out string pyerr)
        {

            string thisAssemblyDir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            string pyScriptPath = Path.Combine(thisAssemblyDir, "RunFreeCAD.py");

            // 中間ファイルは利用者のフォルダに置かず、実行ごとに新しく作る作業フォルダに置く。
            // 同名の既存ファイルを上書き・削除しないため
            ctx.WorkDir = Path.Combine(InstanceTempDir, "run-" + Guid.NewGuid().ToString("N"));
            ctx.KeepWorkDir = false;
            Directory.CreateDirectory(ctx.WorkDir);
            ctx.StepFilePath = Path.Combine(ctx.WorkDir, "result.step");
            ctx.JsonPath = Path.Combine(ctx.WorkDir, "variables.json");
            ctx.BaseStepFilePath = Path.Combine(ctx.WorkDir, "base.step");
            ctx.RefStepFilePath = Path.Combine(ctx.WorkDir, "ref.step");
            ctx.EditFilePath = Path.Combine(ctx.WorkDir, "edit.FCStd");

            bool fcstdExistedBefore = File.Exists(ctx.FreecadFilePath);

            ExportGeometryToStep(ctx.BaseGeometry, ctx.BaseStepFilePath);

            ExportGeometryToStep(ctx.RefGeometry, ctx.RefStepFilePath);

            File.WriteAllText(ctx.JsonPath, JsonSerializer.Serialize(ctx.Variables, new JsonSerializerOptions { WriteIndented = true }));

            RunPythonScript(ctx, pyScriptPath, ctx.EnableGui, out pyout, out pyerr, out bool editSaved);

            // GUIで保存された作業コピーを元のFCStdへ反映する
            if (editSaved)
            {
                ApplySavedEdit(ctx, fcstdExistedBefore, ref pyout, ref pyerr);
            }

            // 入力パスが指定されていなかったときだけfcstdBytesに保存
            if (!isUserProvidedPath && File.Exists(ctx.FreecadFilePath))
            {
                embeddedFcstdBytes = File.ReadAllBytes(ctx.FreecadFilePath);
            }
        }

        /// <summary>
        /// GUIで保存された作業コピーで元のFCStdを置き換える。
        /// 利用者指定のFCStdが元からあった場合は、置き換える前に同じフォルダへバックアップを新規作成する。
        /// </summary>
        private void ApplySavedEdit(FreecadRunContext ctx, bool fcstdExistedBefore, ref string pyout, ref string pyerr)
        {
            try
            {
                if (isUserProvidedPath && fcstdExistedBefore && File.Exists(ctx.FreecadFilePath))
                {
                    string backupPath = MakeBackupPath(ctx.FreecadFilePath);
                    File.Copy(ctx.FreecadFilePath, backupPath, false);
                    pyout += $"\nBackup of the original file: {backupPath}";
                }

                File.Copy(ctx.EditFilePath, ctx.FreecadFilePath, true);
                pyout += $"\nApplied the edited file to: {ctx.FreecadFilePath}";
            }
            catch (Exception ex)
            {
                // 編集内容を失わないよう、作業フォルダは消さずに残す
                ctx.KeepWorkDir = true;
                pyerr += $"\n編集内容を {ctx.FreecadFilePath} に反映できませんでした: {ex.Message}"
                    + $"\n編集後のファイルは {ctx.EditFilePath} に残してあります。";
            }
        }

        // 例: bracket.FCStd → bracket.20260925-153000.FCBak（FreeCADの日付付きバックアップと同じ形式）
        private static string MakeBackupPath(string fcstdPath)
        {
            string dir = Path.GetDirectoryName(fcstdPath);
            string name = Path.GetFileNameWithoutExtension(fcstdPath);
            string stamp = DateTime.Now.ToString("yyyyMMdd-HHmmss");

            string path = Path.Combine(dir, $"{name}.{stamp}.FCBak");
            for (int i = 1; File.Exists(path); i++)
            {
                path = Path.Combine(dir, $"{name}.{stamp}-{i}.FCBak");
            }
            return path;
        }

        private bool ExportGeometryToStep(List<GeometryBase> geometries, string stepFile)
        {
            // 入力形状が空の場合はエクスポートをスキップ
            if (geometries == null || geometries.Count == 0)
            {
                // AddRuntimeMessage(GH_RuntimeMessageLevel.Remark, "入力形状が空なのでStepファイルはエクスポートされません。");
                return true;
            }

            try
            {
                using (var hlDoc = RhinoDoc.CreateHeadless(null))
                {
                    if (hlDoc == null)
                    {
                        AddRuntimeMessage(GH_RuntimeMessageLevel.Error, "Failed to create a headless Rhino document.");
                        return false;
                    }

                    // ===【修正③】ここで `IGH_GeometricGoo` に変換する ===
                    foreach (var geom in geometries)
                    {
                        IGH_GeometricGoo goo = null;

                        if (geom is Brep brep) goo = new GH_Brep(brep); // `Brep` → `GH_Brep`
                        else if (geom is Curve curve) goo = new GH_Curve(curve); // `Curve` → `GH_Curve`
                        else if (geom is Mesh mesh) goo = new GH_Mesh(mesh); // `Mesh` → `GH_Mesh`

                        if (goo != null)
                        {
                            // `IGH_GeometricGoo` から `GeometryBase` に変換して追加
                            var prop = goo.GetType().GetProperty("Value");
                            if (prop != null)
                            {
                                GeometryBase convertedGeom = prop.GetValue(goo) as GeometryBase;
                                if (convertedGeom != null)
                                {
                                    hlDoc.Objects.Add(convertedGeom);
                                }
                            }
                        }
                    }

                    // STEP ファイルにエクスポート
                    bool success = hlDoc.Export(stepFile);
                    if (!success)
                    {
                        AddRuntimeMessage(GH_RuntimeMessageLevel.Error, $"Failed to export to STEP file: {stepFile}");
                        return false;
                    }

                    return true;
                }
            }
            catch (Exception ex)
            {
                AddRuntimeMessage(GH_RuntimeMessageLevel.Error, $"Export error: {ex.Message}");
                return false;
            }
        }

        // RunFreeCAD.py の GH_EDIT_SAVED_MARKER と揃える。GUIで作業コピーが保存されたことを表す
        private const string PythonEditSavedMarker = "[Paramecium:EditSaved]";

        private void RunPythonScript(FreecadRunContext ctx, string pyScriptPath, bool gui, out string pyout, out string pyerr, out bool editSaved)
        {
            editSaved = false;

            string pythonPath = GetPythonPathFromSettings(out string settingsError);
            if (pythonPath == null)
            {
                // ダブルクリック経由でも表示されるよう、pyerr として返す
                pyout = null;
                pyerr = settingsError;
                return;
            }

            var process = new Process();
            process.StartInfo = new ProcessStartInfo()
            {
                FileName = pythonPath,
                // 引数は1つずつ渡す（パスに " が含まれていても、後ろの引数がずれないように）
                ArgumentList = { pyScriptPath, ctx.FreecadFilePath, ctx.JsonPath, gui.ToString(), ctx.BaseStepFilePath, ctx.RefStepFilePath, ctx.StepFilePath, ctx.EditFilePath },
                WorkingDirectory = Path.GetDirectoryName(pyScriptPath),
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = System.Text.Encoding.UTF8,
                StandardErrorEncoding = System.Text.Encoding.UTF8,
                UseShellExecute = false,
                CreateNoWindow = true
            };

            // ログはstdout、エラーはstderrで受け取る。
            // 出力が多いとパイプが詰まって止まるので、終了を待つ間も非同期で読み続ける
            var outLines = new List<string>();
            var errLines = new List<string>();
            bool saved = false;
            process.OutputDataReceived += (s, e) =>
            {
                if (e.Data == null) return;
                lock (outLines)
                {
                    if (e.Data.Trim() == PythonEditSavedMarker)
                        saved = true;
                    else
                        outLines.Add(e.Data);
                }
            };
            process.ErrorDataReceived += (s, e) =>
            {
                if (e.Data == null) return;
                lock (errLines) errLines.Add(e.Data);
            };

            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            process.WaitForExit(); // 引数なしのWaitForExitは非同期読み取りの完了まで待つ

            editSaved = saved;
            pyerr = string.Join("\n", errLines);
            pyout = outLines.Count > 0 ? string.Join("\n", outLines) : "(ログ出力がありません)";
        }

        private void ImportStepIntoRhino(string stepFilePath, IGH_DataAccess DA)
        {
            using (var hlDoc = RhinoDoc.CreateHeadless(null))
            {
                if (!hlDoc.Import(stepFilePath))
                {
                    AddRuntimeMessage(GH_RuntimeMessageLevel.Warning, $"Failed to import \"{stepFilePath}\".");
                    return;
                }

                if (hlDoc.Objects == null || hlDoc.Objects.Count == 0)
                {
                    AddRuntimeMessage(GH_RuntimeMessageLevel.Remark, "No objects were imported from the STEP file.");
                    return;
                }

                var inputGeometries = new List<GeometryBase>();
                foreach (var obj in hlDoc.Objects)
                {
                    if (obj.Geometry != null)
                        inputGeometries.Add(obj.Geometry.Duplicate());
                }

                DA.SetDataList(0, inputGeometries);
            }
        }

        private string GetPythonPathFromSettings(out string error)
        {
            string thisAssemblyDir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            string settingsPath = Path.Combine(thisAssemblyDir, "setting.json");
            string hint = $"{settingsPath} の python_path に、FreeCAD 同梱の python.exe のパスを設定してください。"
                + "（例: C:\\Program Files\\FreeCAD 1.1\\bin\\python.exe）";

            error = null;

            if (!File.Exists(settingsPath))
            {
                error = $"setting.json が見つかりません。\n{hint}";
                return null;
            }

            try
            {
                var json = File.ReadAllText(settingsPath);

                // 手で編集されることを想定し、コメントと末尾のカンマは許容する。python_path 以外のキーは型を問わず無視する
                var options = new JsonDocumentOptions { CommentHandling = JsonCommentHandling.Skip, AllowTrailingCommas = true };
                string path = null;
                using (var settings = JsonDocument.Parse(json, options))
                {
                    if (settings.RootElement.ValueKind == JsonValueKind.Object
                        && settings.RootElement.TryGetProperty("python_path", out var value)
                        && value.ValueKind == JsonValueKind.String)
                    {
                        path = value.GetString();
                    }
                }

                if (string.IsNullOrWhiteSpace(path))
                {
                    error = $"setting.json に python_path が設定されていません。\n{hint}";
                    return null;
                }

                if (!File.Exists(path))
                {
                    error = $"python.exe が見つかりません: {path}\n{hint}";
                    return null;
                }

                return path;
            }
            catch (Exception ex)
            {
                error = $"setting.json の読み込みに失敗しました: {ex.Message}\n{hint}";
                return null;
            }
        }

        // RunFreeCAD.py の GH_WARNING_PREFIX と揃える
        private const string PythonWarningPrefix = "[Paramecium:Warning] ";

        // FreeCAD 1.1 の GUI がドキュメントを開く際に stderr へ直接出す、動作に影響しない出力
        private static readonly string[] IgnoredPythonErrorPatterns =
        {
            "Requested non-existent style parameter token",
            "on geo element version change",
        };

        /// <summary>
        /// Python の stderr を、接頭辞付きの行は Warning、それ以外は Error として表示する。
        /// </summary>
        private void AddPythonErrorMessages(string pyerr)
        {
            var errorLines = new List<string>();
            foreach (var line in pyerr.Split(new[] { "\r\n", "\n" }, StringSplitOptions.None))
            {
                if (line.StartsWith(PythonWarningPrefix))
                    AddRuntimeMessage(GH_RuntimeMessageLevel.Warning, line.Substring(PythonWarningPrefix.Length).Trim());
                else if (!IgnoredPythonErrorPatterns.Any(line.Contains))
                    errorLines.Add(line);
            }

            string error = string.Join("\n", errorLines).Trim();
            if (error.Length > 0)
                AddRuntimeMessage(GH_RuntimeMessageLevel.Error, error);
        }

        private static bool _pythonInitialized = false;

    }
    internal class CustomAttributes : GH_ComponentAttributes
    {
        public CustomAttributes(IGH_Component component) : base(component) { }

        public override GH_ObjectResponse RespondToMouseDoubleClick(GH_Canvas sender, GH_CanvasMouseEvent e)
        {
            if (Owner is FreeCadPartComponent comp)
            {
                var ctx = comp._lastContext;

                if (ctx == null)
                {
                    MessageBox.Show("先にコンポーネントに入力を与えてください。");
                    return GH_ObjectResponse.Handled;
                }

                ctx.EnableGui = true; // ダブルクリックではGUIモードに強制設定

                comp.RunFreecadProcess(ctx, out string pyout, out string pyerr);

                //  出力を一時保存（フィールドに）
                comp._pendingPythonOutput = pyout;
                comp._pendingPythonError = pyerr;
                comp._hasPendingOutput = true;

                comp.ExpireSolution(true); // FreecadファイルGUI編集後にコンポーネントを再計算

            }

            return GH_ObjectResponse.Handled;
        }

    }

}