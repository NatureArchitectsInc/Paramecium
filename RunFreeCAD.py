import sys
import os
import re
import json
from datetime import datetime

# GH側はstdout/stderrをUTF-8として読むので、日本語メッセージが化けないよう揃える
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# ログはファイルに書かず、stdoutでGHへ渡す（GH側でLog出力になる）。
# FreeCADのGUIはsys.stdoutをレポートビューへ差し替えるので、元のstdoutを先に確保しておく
_log_stream = sys.stdout

# この接頭辞で始まるstderrの行は、GH側でWarning（オレンジ）として表示される
GH_WARNING_PREFIX = "[Paramecium:Warning] "

# GUIで作業コピーが保存されたことをGHへ伝えるstdoutの行（GH側で元のFCStdへ反映する）
GH_EDIT_SAVED_MARKER = "[Paramecium:EditSaved]"


# FreeCAD同梱のpython.exe（setting.jsonのpython_path）と同じbinにあるFreeCADモジュールを読み込む
sys.path.append(os.path.dirname(sys.executable))
import FreeCAD
# import Part

def init(input_fcstd_path, json_path):
    log("")
    log("init()")

    # JSONファイルの存在チェック
    if not os.path.exists(json_path):  
        log(f"Error: JSON file '{json_path}' not found.")
        # return

    # FreeCADファイルの準備
    if os.path.exists(input_fcstd_path):
        doc = FreeCAD.open(input_fcstd_path)
        log("Opened existing file.")
    else:
        doc = FreeCAD.newDocument("NewDocument") 
        doc.saveAs(input_fcstd_path)  # 作成したドキュメントを保存
        log("Created new file")

    # Body を取得または作成
    body = doc.getObject("Body")
    if body is None:
        body = doc.addObject("PartDesign::Body", "Body")
        log("Created new Body")

    # 'VarSet' オブジェクトを取得または作成または作成
    obj = doc.getObject('ghVarSet')
    if obj is None:
        log("Created new ghVarset")
        obj = doc.addObject("App::FeaturePython", "ghVarSet")  # 新規作成

    # モデルを更新
    doc.recompute()

    return(doc,body)

def update_fcstd(json_path, doc, body, base_step_path, ref_step_path):
    log("")
    log("update_fcstd()")

    variables = {}
    if os.path.exists(json_path):
        try:
            with open(json_path, "r") as f:
                variables = json.load(f)
            log("Loaded json file")
        except Exception as e:
            log(f"Failed to load JSON: {e}")
    else:
        log(f"JSON file not found at {json_path}. Skipping variable update.")

    # 🔹 JSONの変数をVarSetオブジェクトに追加（あれば）
    if variables:
        obj = doc.getObject('ghVarSet')
        if obj is None:
            obj = doc.addObject("App::FeaturePython", "ghVarSet")
            log("Created ghVarSet")

        for key, value in variables.items():
            value = float(value)  # GH側のJSONでは 5.0 が 5 と書かれるので揃える
            if not hasattr(obj, key):  # まだ存在しないキーなら作成
                obj.addProperty("App::PropertyFloat", key, "Variables")
                setattr(obj, key, value)
                log(f"New var: {key} = {value}")
            else:
                old_value = getattr(obj, key)
                setattr(obj, key, value)
                log(f"Updated var: {key} = {old_value} → {value}")
    else:
        log("No variables to update.")

    """ FreeCADドキュメントを更新し、必要ならばSTEPファイルをインポート """
    # 🔹 インポートするSTEPのオブジェクト名を設定
    part_name = "ImportedBasePart"
    part_name_ref = "ImportedRefPart"

    # RefGeoのShapeBinderとしての追加
    if os.path.exists(ref_step_path):

        # STEP形状を読み込み
        imported_shape_ref = Part.read(ref_step_path)

        # 既存のPartオブジェクトがあるなら削除せず使い回し、なければ新規作成
        step_obj_ref = doc.getObject(part_name_ref)
        if step_obj_ref is None:
            step_obj_ref = doc.addObject("Part::Feature", part_name_ref)
            log(f"Created new Part::Feature: {part_name_ref}")

        # 参照先を壊さず、形状だけ更新
        step_obj_ref.Shape = imported_shape_ref

        # ShapeBinderの作成または更新
        binder = doc.getObject("RefBinder")
        if binder is None:
            binder = doc.addObject("PartDesign::SubShapeBinder", "RefBinder")
            body.addObject(binder)
            log("Created new ShapeBinder (RefBinder)")

        binder.Support = [(step_obj_ref, "")]
        log("Updated binder support.")
    else:
        log(f"ref_step_path does not exist. No Binder created.")

    # BaseGeoのインポート
    if os.path.exists(base_step_path):
        # STEP形状を読み込み
        imported_shape = Part.read(base_step_path)

        # 既存のPartオブジェクトがあるなら削除せず使い回し、なければ新規作成
        step_obj = doc.getObject(part_name)
        if step_obj is None:
            step_obj = doc.addObject("Part::Feature", part_name)
            log(f"Created new Part::Feature: {part_name}")

        # 参照先を壊さず、形状だけ更新
        step_obj.Shape = imported_shape

        # `BaseFeature` を取得または再利用
        base_feature = doc.getObject('BaseFeature')

        if base_feature is None:
            base_feature = doc.getObject('BaseFeature2')

        if base_feature is None:
            base_feature = doc.addObject("PartDesign::FeatureBase", "BaseFeature")
            body.addObject(base_feature)  # Body に追加
            log("Created new BaseFeature")

        # すでに設定されている参照が異なる場合のみ更新
        if base_feature.BaseFeature != step_obj:
            base_feature.BaseFeature = step_obj
        
        log(f"Updated BaseFeature to refer to: {step_obj.Name}")

    # ドキュメント更新
    doc.recompute(None,True,True)

    report_invalid_objects(doc)

    log("update_fcstd completed.")


def report_invalid_objects(doc):
    """失敗フィーチャーの状態チェック（警告は出すが止めない）"""
    invalid_states = ["Invalid", "Failed"]
    for obj in doc.Objects:
        if hasattr(obj, "State") and any(s in obj.State for s in invalid_states):
            msg = f"[FreeCAD Warning] Object '{obj.Name}' has state: {obj.State}\n"
            log(msg.strip())
            sys.stderr.write(msg)  # ← ここで GH の pyerr に表示される（赤い警告）


def redirect_stderr_to_file(path):
    """C++側を含むstderr（fd 2）をファイルに切り替える。戻り値はrestore_stderr()に渡す。
    パイプ＋読み取りスレッドにすると、GILを握ったままのC++の出力でパイプが詰まりデッドロックするのでファイルにする。
    pathはGH側が用意した作業フォルダ内（作業フォルダごとGH側で削除される）"""
    sys.stderr.flush()
    saved_fd = os.dup(2)
    f = open(path, "w", encoding="utf-8")
    os.dup2(f.fileno(), 2)
    return saved_fd, f, path


def restore_stderr(state):
    """stderrを元に戻し、退避中の出力はログに移す"""
    saved_fd, f, path = state
    sys.stderr.flush()
    os.dup2(saved_fd, 2)
    os.close(saved_fd)
    f.close()

    with open(path, "r", encoding="utf-8", errors="replace") as captured:
        for line in captured.read().splitlines():
            if line.strip():
                log(f"[GUI stderr] {line}")


def fix_error(doc):
    log("")
    log("fix_error()")
    
    # doc = FreeCAD.open(input_fcstd_path)
    
    while True:  # 🔹 エラーがなくなるまで繰り返し
        error_objects = []

        # 1. フィレットなどの参照エラーを修正
        for obj in doc.Objects:
            try:
                if hasattr(obj, "Base"):  # Baseプロパティを持っているか確認
                    base = obj.Base
                    if isinstance(base, tuple) and len(base) == 2:
                        base_obj, face_refs = base

                        # ?付きの参照を修正
                        fixed_faces = [ref[1:] if isinstance(ref, str) and ref.startswith("?") else ref for ref in face_refs]

                        # 修正後の参照リストが異なる場合のみ適用
                        if fixed_faces != face_refs:
                            obj.Base = (base_obj, fixed_faces)
                            log(f"修正: {obj.Name} - {face_refs} → {fixed_faces}")
                            error_objects.append(obj)

            except Exception as e:
                log(f"Errors found: {obj.Name}: {e}")

        # 2. スケッチのアタッチメントサポートのエラーを修正
        for obj in doc.Objects:
            try:
                # Sketcherオブジェクトかつ AttachmentSupport を持っている場合のみ処理
                if obj.TypeId == "Sketcher::SketchObject" and hasattr(obj, "AttachmentSupport") and obj.AttachmentSupport:
                    attachment = obj.AttachmentSupport

                    fixed_attachment = []
                    modified = False

                    for support in attachment:
                        if isinstance(support, tuple) and len(support) == 2:
                            base_obj, face_ref = support

                            # face_ref がタプル ('?FaceX',) になっている場合も考慮
                            if isinstance(face_ref, tuple) and len(face_ref) == 1:
                                face_ref = face_ref[0]  # タプルを展開

                            if isinstance(face_ref, str) and face_ref.startswith("?"):
                                fixed_attachment.append((base_obj, (face_ref[1:],)))  # ?を削除し、再びタプルにする
                                modified = True
                            else:
                                fixed_attachment.append(support)
                        else:
                            fixed_attachment.append(support)

                    # 修正があった場合のみ適用
                    if modified:
                        obj.AttachmentSupport = fixed_attachment
                        log(f"Fix: Support of {obj.Name} - {attachment} → {fixed_attachment}")
                        error_objects.append(obj)

            except Exception as e:
                log(f"Error: {obj.Name}: {e}")

        # 3. 修正したオブジェクトがある場合は FreeCAD を再計算
        if error_objects:
            log("Fixed objects:")
            for obj in error_objects:
                log(f"- {obj.Name}")
            doc.recompute()  # 🔹 再計算を実行
            log("Fixed all errors")
            log(len(error_objects))
        else:
            log("No error found.")
            break  # 🔹 エラーがなくなったらループを抜ける

    doc.recompute()  # 🔹 再計算を実行
    # doc.save()  # 🔹 最終的な修正を保存
    # FreeCAD.closeDocument(doc.Name)  # 🔹 ドキュメントを閉じる


def qt_exec(obj):
    """PySide6ではexec_()が非推奨なのでexec()を使う。PySide2にはexec()が無いのでexec_()"""
    return obj.exec() if hasattr(obj, "exec") else obj.exec_()


def major_minor(version_text):
    """'1.0R39109 (Git)' のような文字列から (1, 0) を取り出す。読めなければ None"""
    m = re.match(r"\s*(\d+)\.(\d+)", version_text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def check_version(doc):
    """FCStdを保存したFreeCADと実行中のFreeCADのバージョン（メジャー.マイナー）を比較する。
    異なれば (保存時, 実行中) の表示用文字列を、同じか判定できなければ None を返す"""
    saved = major_minor(doc.getProgramVersion())
    running = major_minor(".".join(FreeCAD.Version()[:2]))
    log(f"FCStd saved by {saved}, running {running}")
    if saved is None or running is None or saved == running:
        return None
    return ("%d.%d" % saved, "%d.%d" % running)


def warn_version_mismatch(saved, running):
    msg = (f"このコンポーネントでの処理を定義している FCStd ファイルは FreeCAD {saved} で保存されています（使用中：FreeCAD {running}）。"
           "バージョンが異なるため、正しく再計算されない可能性があります。")
    log(msg)
    sys.stderr.write(GH_WARNING_PREFIX + msg + "\n")


def confirm_version_mismatch(saved, running):
    """GUI編集の前に、上書き保存のリスクを伝えて続行するかを選ばせる"""
    from PySide.QtWidgets import QApplication, QMessageBox
    from PySide.QtCore import Qt

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    if major_minor(saved) > major_minor(running):
        risk = (f"FreeCAD {running} では正しく読み込めていない可能性があり、"
                f"保存すると FreeCAD {saved} で追加された機能の情報が失われる可能性があります。")
    else:
        risk = (f"保存すると FreeCAD {running} 形式で上書きされ、"
                f"FreeCAD {saved} では一部が正しく扱えなくなる可能性があります。")

    box = QMessageBox()
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Paramecium - FreeCAD のバージョン確認")
    box.setText(f"このコンポーネントでの処理を定義している FCStd ファイルは FreeCAD {saved} で保存されています（使用中：FreeCAD {running}）。")
    box.setInformativeText(risk + "\n\n編集を続けますか？")
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.No)
    box.setWindowFlags(box.windowFlags() | Qt.WindowStaysOnTopHint)  # Rhinoの裏に隠れないように
    answer = qt_exec(box)

    accepted = answer == QMessageBox.Yes
    log(f"Version mismatch dialog: {'continue' if accepted else 'cancelled'}")
    return accepted


def edit_in_gui(edit_path, input_fcstd_path):
    """作業コピー（edit_path）をGUIで編集させる。利用者が作業コピーを保存したらTrueを返す。
    元のFCStdへの反映はGH側で行う（上書き前にバックアップを作るため）"""
    log("")
    log("edit_in_gui()")

    import FreeCADGui
    # FreeCAD同梱の互換モジュール。1.0ではPySide2、1.1以降ではPySide6に振り分けられる
    from PySide.QtWidgets import QApplication

    # QApplicationのインスタンスを作成
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    FreeCADGui.showMainWindow()
    FreeCADGui.updateGui()

    # 保存されたかどうかは、GUI終了後に作業コピーの更新日時とサイズが変わったかで判定する
    # （GUI側のドキュメントオブザーバーには保存完了の通知がないため）
    before = file_signature(edit_path)

    # Open the document（保存先は作業コピーのまま。表示名だけ元のFCStdに合わせる）
    doc = FreeCAD.open(edit_path)
    doc.Label = os.path.splitext(os.path.basename(input_fcstd_path))[0]

    # オブジェクトを表示

    doc.getObject("Body").Visibility = True
    doc.Objects[-1].Visibility = True

    for obj in doc.Objects:
        if obj.TypeId == "Sketcher::SketchObject":
            obj.ViewObject.Visibility = False

    ref_binder = doc.getObject("RefBinder")
    if ref_binder is not None:
        ref_binder.Visibility = True
        if hasattr(ref_binder, "ViewObject") and hasattr(ref_binder, "Shape"):
            num_faces = len(ref_binder.Shape.Faces)
            yellow = (1.0, 1.0, 0.0)
            ref_binder.ViewObject.DiffuseColor = [yellow] * num_faces
            ref_binder.ViewObject.Transparency = 70
            log(f"Set RefBinder to yellow with transparency on {num_faces} faces")

    # ビューの調整
    view = FreeCADGui.ActiveDocument.ActiveView # 🔹 GUIビューを取得
    view.viewIsometric() # 🔹 アイソメトリックビューに変更
    view.fitAll() # 🔹 ズームをオブジェクトにフィット
    FreeCADGui.updateGui() # 🔹 GUIを更新

    # Set up an observer to detect when the user saves or closes the document
    class DocObserver:
        def slotDeletedDocument(self, doc):
            log("Document closed. Exiting script.")
            FreeCADGui.removeDocumentObserver(self)  # 🔹 監視を解除
            QApplication.instance().quit()

    observer = DocObserver()
    FreeCADGui.addDocumentObserver(observer)

    log("FreeCAD GUI is now open. You can edit the model.")
    try:
        qt_exec(app)
    except Exception as e:
        log(f"Error in event loop: {e}")

    # 「名前を付けて保存」で別の場所に保存された場合は作業コピーが変わらないので、元のFCStdへは反映しない
    saved = file_signature(edit_path) != before
    log(f"Working copy {'was saved' if saved else 'was not saved'}: {edit_path}")
    return saved


def file_signature(path):
    """更新日時とサイズの組。ファイルが無ければ None"""
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def export_step(doc, output_step_path):
    log("")
    log("export_step()")

    # 除外する名前リスト
    excluded_names = ["ImportedStepPart", "ImportedBasePart", "ImportedRefPart"]

    # 出力対象オブジェクトを抽出
    valid_objects = []
    for obj in doc.Objects:
        if not obj.isDerivedFrom("Part::Feature"):
            continue
        if obj.Name in excluded_names:
            continue
        valid_objects.append(obj)

    # 'return' をラベルに含むオブジェクトを優先的に抽出（大文字小文字は無視）
    return_objects = []
    for obj in valid_objects:
        if "return" in obj.Label.lower():
            return_objects.append(obj)

    # 出力対象の決定
    if return_objects:
        target_objects = return_objects
        log(f"Found {len(return_objects)} object(s) with label 'return'.")
    elif valid_objects:
        target_objects = [valid_objects[-1]]
        log(f"No 'return'-labelled object found. Falling back to last object: {target_objects[0].Name}")
    else:
        target_objects = []
        log("Error: No valid solid objects found in the file.")

    # エクスポート
    if target_objects:
        Part.export(target_objects, output_step_path)
        exported_names = ", ".join([obj.Name for obj in target_objects])
        log(f"Exported object(s): {exported_names}")
        log(f"Export successful: {output_step_path}")

    # ドキュメントを閉じる
    FreeCAD.closeDocument(doc.Name)


def log(msg):
    now = datetime.now()
    ts = now.strftime("[%H:%M:%S.%f]")
    _log_stream.write(f"{ts} (PID {os.getpid()}) {msg}\n")
    _log_stream.flush()

if __name__ == "__main__":
    if len(sys.argv) < 8:
        print("Usage: python RunFreeCAD.py <path_to_fcstd> <path_to_json> <true/false> <path_to_base_step> <path_to_ref_step> <path_to_output_step> <path_to_edit_fcstd>")
    else:
        # 引数を変数に格納
        input_fcstd_path = sys.argv[1]
        json_path = sys.argv[2]
        enable_gui = sys.argv[3].lower() == "true"
        base_step_path = sys.argv[4]
        ref_step_path = sys.argv[5]
        output_step_path = sys.argv[6]  # FCStd以外の出力はGH側が用意した作業フォルダ内
        edit_path = sys.argv[7]

        log("main()")
        log(f"input_fcstd_path = {input_fcstd_path}")
        log(f"json_path = {json_path}")
        log(f"enable_gui = {enable_gui}")
        log(f"base_step_path = {base_step_path}")
        log(f"ref_step_path = {ref_step_path}")
        log(f"output_step_path = {output_step_path}")
        log(f"edit_path = {edit_path}")

        #処理
        doc, body = init(input_fcstd_path, json_path)

        # 保存時と実行中のFreeCADのバージョンが異なる場合
        # 再計算（上書きしない）は警告のみ、GUI編集（上書きしうる）は続行するか確認する
        mismatch = check_version(doc)
        if mismatch:
            if not enable_gui:
                warn_version_mismatch(*mismatch)
            elif not confirm_version_mismatch(*mismatch):
                enable_gui = False  # 編集せず、既存ファイルのまま形状だけ返す
                warn_version_mismatch(*mismatch)

        if not enable_gui:
            update_fcstd(json_path, doc, body, base_step_path, ref_step_path)
        else:
            # GUI編集では途中経過のエラーではなく、最終的なモデルの状態だけをGHに返す。
            # 編集前〜GUI終了までのstderrはログに退避する
            # 利用者には作業コピーを編集させ、元のFCStdには直接保存させない
            saved = False
            stderr_state = redirect_stderr_to_file(
                os.path.join(os.path.dirname(edit_path), "gui_stderr.txt"))
            try:
                update_fcstd(json_path, doc, body, base_step_path, ref_step_path)
                doc.saveAs(edit_path)
                FreeCAD.closeDocument(doc.Name)
                saved = edit_in_gui(edit_path, input_fcstd_path)
                # 作業コピーには変数とBase/Ref形状が反映済み。保存されていれば編集後、
                # されていなければ通常の再計算と同じ状態になる
                doc = FreeCAD.open(edit_path)
            finally:
                restore_stderr(stderr_state)

            if saved:
                _log_stream.write(GH_EDIT_SAVED_MARKER + "\n")
                _log_stream.flush()
            elif mismatch:
                # 保存されなかった＝元のFCStdは古いバージョンのままなので、警告を出し続ける
                warn_version_mismatch(*mismatch)

            # 保存された最終状態を、通常の再計算と同じ手順で確かめる
            log("")
            log("Checking final state after GUI editing")
            doc.recompute(None, True, True)
            report_invalid_objects(doc)
        # fix_error(doc) 不要かもしれなのでコメントアウトして様子見
        export_step(doc, output_step_path)

