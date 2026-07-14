"use client";

import { useEffect, useState } from "react";

type Language = "ja" | "en";
type LegendItem = readonly [string, string];

const media = "/QuickSDFBlender/media/";

const copy = {
  ja: {
    skip: "本文へ移動",
    docs: "操作ガイド",
    version: "v0.6.1",
    navLabel: "ページ内ナビゲーション",
    languageLabel: "表示言語",
    openFullSize: "画像を原寸で開く",
    nav: [
      ["インストール", "#install"],
      ["使い方", "#workflow"],
      ["影の変化を確認", "#step-4"],
      ["書き出し", "#step-5"],
      ["困ったとき", "#help"],
    ],
    download: "Windows x64版をダウンロード",
    github: "GitHub",
    guideTitle: "Quick SDF Studio 操作ガイド",
    guideBody: "Quick SDF Studioは、Blenderで角度別の顔影マスクを編集し、lilToon／liltoonUE向けの16-bit RGBA PNGを書き出すアドオンです。このページでは、インストール、編集、確認、書き出しの手順を説明します。",
    startGuide: "操作手順を見る",
    supported: "対応環境",
    requirements: ["Blender 5.1", "Windows x64", "単一メッシュ", "単一マテリアルスロット", "0–1 UVマップ"],
    beforeTitle: "始める前に確認するもの",
    beforeItems: ["顔を含むメッシュ", "顔に使用しているマテリアルスロット", "0–1範囲に収まったUVマップ"],
    basicsTitle: "基本操作",
    basics: [
      ["Light／Shadow", "Lightは白、Shadowは黒を塗ります。"],
      ["0° → 90°", "ライトの実角度ではありません。0°のLight Startsから、45°のSideを通り、90°のFull Lightへ明るい部分が広がります。"],
      ["1回のペイント", "選択中の角度へ反映され、0.5境界を越えた画素はLightなら90°側、Shadowなら0°側へ反映されます。"],
      ["書き出し", "8枚のつながりは自動で整えられます。塗った画像そのものは変更されません。"],
    ],
    installTitle: "インストール",
    installLead: "ダウンロードしたZIPは展開せず、そのままBlenderへインストールします。",
    installSteps: [
      "GitHub ReleasesからWindows x64版のZIPをダウンロードします。",
      "Blenderで 編集 → プリファレンス → エクステンションを入手 を開きます。",
      "右上のメニューから ディスクからインストール を選びます。",
      "ZIPを選択します。インストール後、3DビューでNキーを押すとQuick SDFタブが表示されます。",
    ],
    workflowTitle: "操作手順",
    workflowLead: "顔メッシュを選択し、角度キーごとのマスクを修正して、テクスチャを書き出します。",
    step1Label: "STEP 1",
    step1Title: "顔のメッシュを選び、Studioを開く",
    step1Body: "Object Modeで顔を含むメッシュを選択します。Quick SDFパネルで、顔に使用しているマテリアルスロットとUVマップを確認してから、Create & Editを押します。",
    step1Actions: [
      "顔を含むメッシュをアクティブにする",
      "3DビューでNキー → Quick SDFを開く",
      "Material SlotとUV Mapを確認する",
      "Create & Editを押す",
    ],
    step1Result: "Create & Editを押すと、現在のポーズと法線から8枚の影ガイドが作られ、38.6°のキーを選んだ状態でStudioが開きます。まずはResolution 1024、Initialize: Light Sweepのままで構いません。既存の作業を続けるときはOpen Quick SDF Studioを押します。左右のUVを自動判定できない場合だけ、Studioを開く前に3つの候補から見た目の合うものを選びます。",
    step1Legend: [
      ["1", "顔を含むメッシュとマテリアルスロットを確認"],
      ["2", "Create & Editを押して8枚の下描きを作成"],
      ["3", "解像度や初期化方法は、必要な場合だけ変更"],
    ] as LegendItem[],
    step2Label: "STEP 2",
    step2Title: "作業画面を確認する",
    step2Body: "Studioは、左の2Dキャンバス、右の3Dビュー、下のライトスイープで構成されています。2Dキャンバスと3Dビューは同じ影マスクを編集します。",
    step2Note: "主な操作は、Light、Shadow、ライトスイープ、Export Face Shadow Textureです。",
    step2Legend: [
      ["1", "2Dキャンバス：白黒マスクの細部を修正"],
      ["2", "3Dビュー：顔に出る影を確認しながら修正"],
      ["3", "Light／Shadow：次のストロークで塗る値を選択"],
      ["4", "Export Face Shadow Texture：完成したテクスチャを書き出す"],
      ["5", "ライトスイープ：8枚の下描きを選び、影の変化を確認"],
    ] as LegendItem[],
    step3Label: "STEP 3",
    step3Title: "影ガイドを修正する",
    step3Body: "白に変更する部分にはLight、黒に変更する部分にはShadowを選択してペイントします。",
    paintChoices: [
      ["Light", "白にする"],
      ["Shadow", "黒にする"],
      ["2Dキャンバス", "細かな輪郭や3Dで届きにくい場所を修正する"],
      ["3Dビュー", "顔への見え方を確認しながら修正する"],
    ],
    step3Note: "Mirror Onが有効な場合、一方の側への変更は設定されたミラー方式で反対側へ反映されます。ブラシサイズ、強さ、筆圧、UndoはBlender標準のまま使えます。3D上の暖色と寒色は確認用の表示で、書き出すテクスチャには入りません。",
    step3Legend: [
      ["1", "2Dキャンバス上の白黒マスクを直接修正"],
      ["2", "同じ修正を3DのPaint Overlayで確認"],
      ["3", "青枠で現在編集しているキーを確認"],
    ] as LegendItem[],
    step4Label: "STEP 4",
    step4Title: "影の移り変わりを確認する",
    step4Body: "画面下のサムネイルが編集可能な角度キーです。サムネイルをクリックすると、そのキーが編集対象になります。プレイヘッドをドラッグすると、キーの間を含む影の変化を連続して確認できます。",
    step4Actions: [
      "プレイヘッドを左右へドラッグする",
      "2Dと3Dで、明るい部分が広がる様子を確認する",
      "編集する位置でマウスを離す",
      "Step 3と同じ方法で修正する",
    ],
    step4Note: "初期キーは0.0、12.9、25.7、38.6、51.4、64.3、77.1、90.0°の8つです。既存キーから2°以内ではそのキーへ吸着します。それ以外では一時Canvasが表示され、実際に筆跡が付いた時だけ新しい角度キーが作成されます。スクラブだけではデータは増えません。45°のSideは基準点で、追加の画像ではありません。",
    angleTerms: [
      ["0° → 90°", "ライトの実角度ではありません。0°はLight Starts、45°はSideの目印、90°はFull Lightです。"],
      ["サムネイル／プレイヘッド", "サムネイルは編集可能な角度キー、プレイヘッドはキーの選択と角度間の確認に使用します。"],
    ],
    gifAlt: "プレイヘッドをLight StartsからFull Lightへ動かすと、2Dマスクと3Dの顔影が変化するQuick SDF Studioの画面",
    step4Legend: [
      ["1", "ドラッグ中：キーの間を含めて影の変化を確認"],
      ["2", "マウスを離したあと：既存キーまたは角度間の一時Canvasを編集"],
    ] as LegendItem[],
    step5Label: "STEP 5",
    step5Title: "Face Shadow Textureを書き出す",
    step5Body: "修正が終わったら、画面上部のExport Face Shadow Textureを押します。必要な確認と調整はQuick SDFが自動で行い、lilToon／liltoonUE用の16-bit RGBA PNGを1枚書き出します。",
    step5Actions: [
      "Export Face Shadow Textureを押す",
      "初回だけ保存先を選ぶ",
      "Exportedと表示されたら完了",
      "次回からは同じ保存先を使用し、上書き時だけ確認する",
    ],
    step5Note: "キー同士のつながりに矛盾があっても、書き出し用のデータだけが自動で整えられます。Studioで塗った画像は変わりません。『角度のつながりを自動調整して書き出しました』と表示された場合も、書き出しは正常に完了しています。",
    step5Legend: [
      ["1", "書き出される16-bit PNG"],
      ["2", "Export Face Shadow Textureで書き出しを開始"],
      ["3", "完了メッセージを確認"],
      ["4", "自動調整の詳細は、必要な場合だけ確認"],
    ] as LegendItem[],
    outputTitle: "書き出されるファイル",
    outputBody: "完成するのは、lilToon／liltoonUE用の16-bit RGBA PNGが1枚です。既定ではR/Gに左右の切替角、Bに通常法線へ戻す領域、Aに影強度を格納します。UnityやUnreal Engineへ読み込むときはsRGBを無効にし、色ではなくデータとして扱います。",
    outputSpecTitle: "出力テクスチャの仕様を見る",
    outputRows: [
      ["形式", "16-bit RGBA PNG、1枚"],
      ["R", "キャラクターの右側から光が当たるとき、Lightへ変わるタイミング"],
      ["G", "キャラクターの左側から光が当たるとき、Lightへ変わるタイミング"],
      ["B", "SDF Area：通常法線へ戻す領域"],
      ["A", "Shadow Strength：影の強度"],
      ["R／Gの値", "大きいほど早い段階でLightへ切り替わります。0は最後までShadow、65535は最初からLightです。"],
    ],
    advancedTitle: "影ガイドとミラーの設定",
    advancedLead: "Advancedには、正面方向、Shadow Amount、再ベイク、角度キー、ミラー方式、復旧の設定があります。",
    advancedCases: [
      ["ライトスイープの向きを直したい", "正面からモデルを見てUse This View as Front"],
      ["下描きの影を増減したい", "Shadow Amountを変更して影ガイドを更新"],
      ["ポーズやメッシュ変更を反映したい", "Rebake Base"],
      ["編集するキーを増やしたい", "Add Angle…"],
      ["左右を別々に描きたい", "Break Mirror"],
      ["特殊な左右UVへ合わせたい", "MirrorのLayoutとPaint Sideを変更"],
      ["出力チャンネルを変更したい", "Output Packingで割り当てと反転を変更"],
      ["角度に依存しない領域や強度を編集したい", "Additional Masksでマスクを選択"],
    ],
    advancedNote: "影ガイドを更新または再ベイクしても、手描きした部分は保持されます。",
    advancedLegend: [
      ["1", "顔の正面方向を設定"],
      ["2", "影ガイドの量を調整"],
      ["3", "ミラー方式と編集する側を変更"],
      ["4", "角度キーの追加、複製、移動、削除、再ベイク"],
      ["5", "書き出し時の自動調整確認とマテリアル復元"],
    ] as LegendItem[],
    helpTitle: "困ったとき",
    helpLead: "症状に近い項目を開いて、最初の対処から順に確認してください。",
    troubles: [
      ["Create & Editが表示されない", "Object Modeで編集可能なメッシュをアクティブにします。0–1 UVマップと有効なマテリアルスロットも必要です。"],
      ["3Dビューで塗れない", "モデルから少し離れるか、Numpad 5で平行投影へ切り替えます。2Dキャンバスなら表示距離に関係なく同じ場所を編集できます。"],
      ["ペイントしても変化が見えない", "すでに同じ色になっている可能性があります。LightとShadowを切り替えて確認します。Studio内でQuick SDF Paintツールが選ばれていることも確認してください。"],
      ["2Dでは変わるが3Dでは見えない", "モデルを回転し、塗った面が視点の反対側にないか確認します。表示方法がPaint Overlayになっていることも確認してください。"],
      ["2Dキャンバスに余分な線が見える", "Image EditorのUV Overlayを非表示にします。書き出し対象は、選択したマテリアルスロットの面だけです。"],
      ["ライトスイープの向きが逆", "モデルを正面から見て、AdvancedのUse This View as Frontを押します。"],
      ["書き出しが完了しない", "画像やUVが失われていないか、保存先へ書き込めるか、ディスク容量が足りているか確認します。原因を直したあとRetry Exportを押します。"],
      ["元のマテリアルに戻らない", "Advanced → Review / Recovery → Restore Materialsを押します。"],
    ],
    referenceTitle: "参考情報",
    referenceLead: "対応範囲、ソースコード、不具合報告はこちらです。",
    limitations: ["単一オブジェクト", "単一マテリアルスロット", "単一0–1 UV", "Windows x64", "Blender 5.1"],
    issue: "GitHub Issueを作成",
    attribution: "画面キャプチャにはKipfel ©もち山金魚を使用しています。モデルデータはQuick SDF Studioに含まれません。",
    footer: "Quick SDF Studio v0.6.1 — Blender 5.1／Windows x64",
    imageAlts: {
      create: "顔のメッシュとマテリアルスロットを選び、Create & Editを押すQuick SDFパネル",
      studio: "左に2Dキャンバス、右にKipfelの3Dビュー、下に8段階のライトスイープがあるQuick SDF Studio",
      paint: "Kipfelの影ガイドをLightまたはShadowで修正している画面",
      angle: "プレイヘッドを動かしてライトスイープによる顔影の変化を確認するQuick SDF Studio",
      snap: "プレイヘッドのドラッグ中と、最も近い制作キーを選んだあとの比較",
      export: "Export Face Shadow Textureと、書き出し完了メッセージを表示した画面",
      texture: "Quick SDF Studioから書き出したlilToon／liltoonUE向け16-bit RGBA顔影テクスチャ",
      advanced: "影ガイド、ミラー、角度、復旧設定をまとめたAdvancedパネル",
    },
  },
  en: {
    skip: "Skip to content",
    docs: "User Guide",
    version: "v0.6.1",
    navLabel: "Page navigation",
    languageLabel: "Display language",
    openFullSize: "Open image at full size",
    nav: [["Install", "#install"], ["Workflow", "#workflow"], ["Preview the Sweep", "#step-4"], ["Export", "#step-5"], ["Troubleshooting", "#help"]],
    download: "Download for Windows x64",
    github: "GitHub",
    guideTitle: "Quick SDF Studio User Guide",
    guideBody: "Quick SDF Studio is a Blender add-on for editing angle-based face-shadow masks and exporting a 16-bit RGBA PNG for lilToon or liltoonUE. This page describes installation, editing, review, and export.",
    startGuide: "View instructions",
    supported: "Requirements",
    requirements: ["Blender 5.1", "Windows x64", "One mesh", "One material slot", "One 0–1 UV map"],
    beforeTitle: "Before you begin",
    beforeItems: ["A mesh containing the face", "The material slot used by the face", "A UV map contained within the 0–1 range"],
    basicsTitle: "Basic controls",
    basics: [["Light / Shadow", "Light paints white; Shadow paints black."], ["0° → 90°", "This is not the light’s physical angle. The lit area grows from Light Starts at 0°, through the Side reference at 45°, to Full Light at 90°."], ["One stroke", "The stroke is applied at the selected angle. Pixels crossing the 0.5 boundary are carried toward 90° for Light or toward 0° for Shadow."], ["Export", "Quick SDF fixes continuity across all eight guides without changing the images you painted."]],
    installTitle: "Installation",
    installLead: "Install the downloaded ZIP directly in Blender without extracting it.",
    installSteps: ["Download the Windows x64 ZIP from GitHub Releases.", "In Blender, open Edit → Preferences → Get Extensions.", "Open the top-right menu and choose Install from Disk.", "Select the ZIP. After installation, press N in the 3D Viewport to find the Quick SDF tab."],
    workflowTitle: "Procedure",
    workflowLead: "Select the face mesh, edit the mask at each angle key, and export the texture.",
    step1Label: "STEP 1",
    step1Title: "Select the face mesh and open Studio",
    step1Body: "In Object Mode, select the mesh containing the face. In the Quick SDF panel, confirm the material slot and UV map used by the face, then choose Create & Edit.",
    step1Actions: ["Make the mesh containing the face active", "Press N in the 3D Viewport and open Quick SDF", "Confirm Material Slot and UV Map", "Choose Create & Edit"],
    step1Result: "Create & Edit builds eight shadow guides from the current pose and normals, then opens Studio with the 38.6° key selected. Start with Resolution 1024 and Initialize: Light Sweep. Choose Open Quick SDF Studio to continue an existing project. Only when Quick SDF cannot identify the left/right UV layout will it ask you to choose the matching preview before Studio opens.",
    step1Legend: [["1", "Confirm the face mesh and material slot"], ["2", "Choose Create & Edit to build the eight guide images"], ["3", "Change the resolution or starting method only when needed"]] as LegendItem[],
    step2Label: "STEP 2",
    step2Title: "Identify the working areas",
    step2Body: "Studio contains a 2D Canvas on the left, a 3D Viewport on the right, and the light sweep along the bottom. The 2D Canvas and 3D Viewport edit the same shadow mask.",
    step2Note: "The main controls are Light, Shadow, the light sweep, and Export Face Shadow Texture.",
    step2Legend: [["1", "2D Canvas: refine details in the black-and-white mask"], ["2", "3D Viewport: edit while checking the shadow on the face"], ["3", "Light / Shadow: choose the value painted by the next stroke"], ["4", "Export Face Shadow Texture: write the finished texture"], ["5", "Light sweep: choose one of the eight guides or preview the transition"]] as LegendItem[],
    step3Label: "STEP 3",
    step3Title: "Edit the shadow guide",
    step3Body: "Choose Light to paint an area white or Shadow to paint it black.",
    paintChoices: [["Light", "Paint white"], ["Shadow", "Paint black"], ["2D Canvas", "Refine edges and areas that are difficult to reach in 3D"], ["3D Viewport", "Edit while judging the result on the face"]],
    step3Note: "When Mirror On is enabled, edits on one side are applied to the other side using the selected mirror mode. Brush size, strength, pressure, and Undo keep Blender’s standard behavior. The warm and cool colors in the 3D overlay are only a preview; they are not written to the exported texture.",
    step3Legend: [["1", "Edit the black-and-white mask in the 2D Canvas"], ["2", "Check the same edit in the 3D Paint Overlay"], ["3", "The blue outline shows the key currently being edited"]] as LegendItem[],
    step4Label: "STEP 4",
    step4Title: "Check how the shadow moves",
    step4Body: "The thumbnails along the bottom are editable angle keys. Click a thumbnail to edit that key. Drag the playhead to preview the continuous transition between keys in both 2D and 3D.",
    step4Actions: ["Drag the playhead left or right", "Watch the lit area spread in 2D and 3D", "Release the mouse at the angle to edit", "Paint as in Step 3"],
    step4Note: "The eight initial keys are 0.0, 12.9, 25.7, 38.6, 51.4, 64.3, 77.1, and 90.0°. The playhead snaps within 2° of an existing key. At other angles, a temporary Canvas is shown and a new key is created only after a stroke changes pixels. Scrubbing alone does not add data. The Side marker at 45° is a reference point, not another image.",
    angleTerms: [["0° → 90°", "This is not the light’s physical angle. 0° is Light Starts, 45° marks Side, and 90° is Full Light."], ["Thumbnails / playhead", "Thumbnails are editable angle keys. The playhead selects a key or previews the result between keys."]],
    gifAlt: "The Quick SDF playhead moves from Light Starts to Full Light while the 2D mask and 3D face shadow change",
    step4Legend: [["1", "While dragging: preview the transition between keys"], ["2", "After release: edit an existing key or a temporary Canvas between keys"]] as LegendItem[],
    step5Label: "STEP 5",
    step5Title: "Export the Face Shadow Texture",
    step5Body: "When the corrections are finished, choose Export Face Shadow Texture in the top bar. Quick SDF performs the required checks and adjustments, then writes one 16-bit RGBA PNG for lilToon or liltoonUE.",
    step5Actions: ["Choose Export Face Shadow Texture", "Select a save location on the first export", "The export is complete when Exported appears", "Later exports reuse the same location and ask only before overwriting"],
    step5Note: "If the keys contain a conflicting transition, Quick SDF fixes only a temporary copy used for export. The images you painted in Studio stay unchanged. The automatic-adjustment message still means the export succeeded.",
    step5Legend: [["1", "The exported 16-bit PNG"], ["2", "Choose Export Face Shadow Texture to begin"], ["3", "Check the completion message"], ["4", "Inspect automatic adjustments only when needed"]] as LegendItem[],
    outputTitle: "Exported file",
    outputBody: "The finished asset is one 16-bit RGBA PNG for lilToon or liltoonUE. By default, R/G store the right and left transition angles, B stores the area that returns to the regular normal, and A stores shadow strength. Disable sRGB when importing it into Unity or Unreal Engine so it is treated as data rather than color.",
    outputSpecTitle: "View the output texture specification",
    outputRows: [["Format", "One 16-bit RGBA PNG"], ["R", "When light comes from the character’s right, the point where the pixel becomes Light"], ["G", "When light comes from the character’s left, the point where the pixel becomes Light"], ["B", "SDF Area: the region that returns to the regular normal"], ["A", "Shadow Strength"], ["R / G values", "Larger values become Light earlier. 0 stays Shadow through the end; 65535 is Light from the start."]],
    advancedTitle: "Shadow guide and mirror settings",
    advancedLead: "Advanced contains settings for front direction, Shadow Amount, rebaking, angle keys, mirror modes, and recovery.",
    advancedCases: [["Correct the direction of the light sweep", "View the model from the front and choose Use This View as Front"], ["Increase or decrease the guide shadow", "Change Shadow Amount and update the guide"], ["Apply pose or mesh changes", "Rebake Base"], ["Add another editable key", "Add Angle…"], ["Paint left and right independently", "Break Mirror"], ["Match a special left/right UV layout", "Change Mirror Layout and Paint Side"], ["Change output channels", "Configure assignments and inversion under Output Packing"], ["Edit angle-independent regions or strength", "Select a mask under Additional Masks"]],
    advancedNote: "Updating or rebaking the guide preserves painted corrections.",
    advancedLegend: [["1", "Set the character’s front direction"], ["2", "Adjust the amount of shadow in the guide"], ["3", "Configure mirroring and the painted side"], ["4", "Add, duplicate, retime, delete, or rebake angle keys"], ["5", "Review export adjustments or restore materials"]] as LegendItem[],
    helpTitle: "Troubleshooting",
    helpLead: "Open the item that matches the symptom and try the first action before reading further.",
    troubles: [["Create & Edit is unavailable", "Make an editable mesh active in Object Mode. It also needs a 0–1 UV map and a valid material slot."], ["Painting does not work in the 3D Viewport", "Move the view slightly away from the model or press Numpad 5 for Orthographic view. The 2D Canvas edits the same texels at any viewing distance."], ["Painting appears to do nothing", "The area may already contain the selected value. Toggle Light and Shadow, and confirm that the Quick SDF Paint tool is selected in Studio."], ["The 2D Canvas changes but the 3D Viewport does not", "Rotate the model to check whether the painted face is on the far side. Also confirm that the display mode is Paint Overlay."], ["Extra lines appear in the 2D Canvas", "Hide the Image Editor’s UV Overlay. Only faces in the selected material slot are exported."], ["The light sweep moves in the wrong direction", "View the model from the front and choose Advanced → Use This View as Front."], ["Export does not finish", "Check for missing images or UVs, write access to the destination, and available disk space. Fix the reported issue, then choose Retry Export."], ["The original material was not restored", "Choose Advanced → Review / Recovery → Restore Materials."]],
    referenceTitle: "Reference",
    referenceLead: "Supported scope, source code, and issue reporting.",
    limitations: ["One object", "One material slot", "One 0–1 UV map", "Windows x64", "Blender 5.1"],
    issue: "Open a GitHub Issue",
    attribution: "Screenshots feature Kipfel ©もち山金魚. Character model files are not included with Quick SDF Studio.",
    footer: "Quick SDF Studio v0.6.1 — Blender 5.1 / Windows x64",
    imageAlts: {
      create: "Quick SDF panel with the face mesh and material slot selected before choosing Create and Edit",
      studio: "Quick SDF Studio with the 2D Canvas, Kipfel in the 3D Viewport, and the eight-stage light sweep",
      paint: "Editing the shadow guide on Kipfel with Light or Shadow",
      angle: "Quick SDF Studio previewing how the face shadow changes across the light sweep",
      snap: "Comparison between dragging the playhead and selecting the nearest editable production key",
      export: "Export Face Shadow Texture and the export completion message",
      texture: "16-bit RGBA lilToon and liltoonUE face-shadow texture exported by Quick SDF Studio",
      advanced: "Advanced controls for the shadow guide, mirroring, angles, and recovery",
    },
  },
} as const;

function ScreenFigure({
  src,
  alt,
  fullSizeLabel,
  caption,
  legend,
  width = 2048,
  height = 1080,
  className = "",
}: {
  src: string;
  alt: string;
  fullSizeLabel: string;
  caption?: string;
  legend?: readonly LegendItem[];
  width?: number;
  height?: number;
  className?: string;
}) {
  return (
    <figure className={`screen-figure ${className}`.trim()}>
      <a href={src} target="_blank" rel="noreferrer" aria-label={`${alt} — ${fullSizeLabel}`}>
        <img src={src} alt={alt} width={width} height={height} loading="lazy" />
      </a>
      {caption || legend ? (
        <figcaption>
          {caption ? <p>{caption}</p> : null}
          {legend ? (
            <ol className="screen-legend">
              {legend.map(([number, text]) => (
                <li key={`${number}-${text}`}><span>{number}</span><p>{text}</p></li>
              ))}
            </ol>
          ) : null}
        </figcaption>
      ) : null}
    </figure>
  );
}

function StepHeading({ label, title, body }: { label: string; title: string; body: string }) {
  return (
    <header className="step-heading">
      <span>{label}</span>
      <div><h2>{title}</h2><p>{body}</p></div>
    </header>
  );
}

export default function Home() {
  const [language, setLanguage] = useState<Language>("ja");
  const t = copy[language];

  useEffect(() => { document.documentElement.lang = language; }, [language]);

  return (
    <>
      <a className="skip-link" href="#content">{t.skip}</a>
      <header className="site-header">
        <a className="brand" href="#top" aria-label="Quick SDF Studio"><span className="brand-mark" aria-hidden="true" /><span>Quick SDF</span><small>{t.docs}</small></a>
        <nav className="main-nav" aria-label={t.navLabel}>{t.nav.map(([label, href]) => <a href={href} key={href}>{label}</a>)}</nav>
        <div className="header-actions">
          <span className="version-pill">{t.version}</span>
          <div className="language-switch" aria-label={t.languageLabel}>
            <button type="button" className={language === "ja" ? "active" : ""} onClick={() => setLanguage("ja")} aria-pressed={language === "ja"}>JA</button>
            <button type="button" className={language === "en" ? "active" : ""} onClick={() => setLanguage("en")} aria-pressed={language === "en"}>EN</button>
          </div>
          <a className="header-download" href="https://github.com/yeczrtu/QuickSDFBlender/releases/latest">{t.download}</a>
        </div>
      </header>

      <main id="content">
        <section className="guide-intro page-shell" id="top">
          <div className="guide-intro-copy">
            <p className="product-version">{t.docs} {t.version}</p>
            <h1>{t.guideTitle}</h1>
            <p className="guide-description">{t.guideBody}</p>
            <div className="intro-actions">
              <a className="primary-action" href="#step-1">{t.startGuide}</a>
              <a className="secondary-action" href="https://github.com/yeczrtu/QuickSDFBlender/releases/latest">{t.download}</a>
            </div>
          </div>
          <aside className="before-card">
            <h2>{t.beforeTitle}</h2>
            <ul>{t.beforeItems.map((item) => <li key={item}>{item}</li>)}</ul>
            <strong>{t.supported}</strong>
            <ul className="requirements-list">{t.requirements.map((item) => <li key={item}>{item}</li>)}</ul>
          </aside>
        </section>

        <section className="basics-section page-shell" aria-labelledby="basics-title">
          <h2 id="basics-title">{t.basicsTitle}</h2>
          <dl>{t.basics.map(([term, description]) => <div key={term}><dt>{term}</dt><dd>{description}</dd></div>)}</dl>
        </section>

        <section className="manual-section install-section" id="install">
          <div className="page-shell">
            <header className="section-title"><h2>{t.installTitle}</h2><p>{t.installLead}</p></header>
            <ol className="install-steps">{t.installSteps.map((step, index) => <li key={step}><span>{index + 1}</span><p>{step}</p></li>)}</ol>
          </div>
        </section>

        <section className="workflow-section" id="workflow">
          <div className="page-shell workflow-heading"><h2>{t.workflowTitle}</h2><p>{t.workflowLead}</p></div>

          <article className="manual-step page-shell" id="step-1">
            <StepHeading label={t.step1Label} title={t.step1Title} body={t.step1Body} />
            <div className="step-grid">
              <div className="instruction-card">
                <ol>{t.step1Actions.map((item) => <li key={item}>{item}</li>)}</ol>
                <p className="result-note">{t.step1Result}</p>
              </div>
              <ScreenFigure src={`${media}quick-sdf-create-and-edit.png`} alt={t.imageAlts.create} fullSizeLabel={t.openFullSize} legend={t.step1Legend} />
            </div>
          </article>

          <article className="manual-step page-shell" id="step-2">
            <StepHeading label={t.step2Label} title={t.step2Title} body={t.step2Body} />
            <ScreenFigure src={`${media}quick-sdf-studio-overview.png`} alt={t.imageAlts.studio} fullSizeLabel={t.openFullSize} legend={t.step2Legend} className="wide-figure" />
            <p className="plain-note">{t.step2Note}</p>
          </article>

          <article className="manual-step page-shell" id="step-3">
            <StepHeading label={t.step3Label} title={t.step3Title} body={t.step3Body} />
            <div className="step-grid reverse-grid">
              <ScreenFigure src={`${media}quick-sdf-normal-guide-and-paint.png`} alt={t.imageAlts.paint} fullSizeLabel={t.openFullSize} legend={t.step3Legend} />
              <div>
                <dl className="choice-list">{t.paintChoices.map(([term, description]) => <div key={term}><dt>{term}</dt><dd>{description}</dd></div>)}</dl>
                <p className="plain-note">{t.step3Note}</p>
              </div>
            </div>
          </article>

          <article className="manual-step page-shell" id="step-4">
            <StepHeading label={t.step4Label} title={t.step4Title} body={t.step4Body} />
            <div className="instruction-row">
              <ol className="compact-actions">{t.step4Actions.map((item) => <li key={item}>{item}</li>)}</ol>
              <p className="plain-note">{t.step4Note}</p>
            </div>
            <figure className="screen-figure motion-figure">
              <a href={`${media}quick-sdf-angle-seek.gif`} target="_blank" rel="noreferrer" aria-label={`${t.imageAlts.angle} — ${t.openFullSize}`}>
                <img className="motion-live" src={`${media}quick-sdf-angle-seek.gif`} alt={t.gifAlt} width="960" height="540" loading="lazy" />
                <img className="motion-still" src={`${media}quick-sdf-angle-seek-poster.png`} alt={t.imageAlts.angle} width="1280" height="720" loading="lazy" />
              </a>
            </figure>
            <div className="angle-explanation">
              <dl className="term-grid">{t.angleTerms.map(([term, description]) => <div key={term}><dt>{term}</dt><dd>{description}</dd></div>)}</dl>
              <ScreenFigure src={`${media}quick-sdf-single-playhead.png`} alt={t.imageAlts.snap} fullSizeLabel={t.openFullSize} legend={t.step4Legend} width={2048} height={540} />
            </div>
          </article>

          <article className="manual-step page-shell" id="step-5">
            <StepHeading label={t.step5Label} title={t.step5Title} body={t.step5Body} />
            <div className="instruction-row">
              <ol className="compact-actions">{t.step5Actions.map((item) => <li key={item}>{item}</li>)}</ol>
              <p className="plain-note">{t.step5Note}</p>
            </div>
            <ScreenFigure src={`${media}quick-sdf-export.png`} alt={t.imageAlts.export} fullSizeLabel={t.openFullSize} legend={t.step5Legend} className="wide-figure" />
            <div className="output-panel">
              <figure><img src={`${media}quick-sdf-threshold-example.png`} alt={t.imageAlts.texture} width="1024" height="1024" loading="lazy" /></figure>
              <div>
                <h3>{t.outputTitle}</h3>
                <p>{t.outputBody}</p>
                <details className="reference-details">
                  <summary>{t.outputSpecTitle}<span aria-hidden="true">＋</span></summary>
                  <dl>{t.outputRows.map(([term, description]) => <div key={term}><dt>{term}</dt><dd>{description}</dd></div>)}</dl>
                </details>
              </div>
            </div>
          </article>
        </section>

        <section className="manual-section advanced-section" id="advanced">
          <div className="page-shell">
            <header className="section-title"><h2>{t.advancedTitle}</h2><p>{t.advancedLead}</p></header>
            <div className="advanced-grid">
              <ScreenFigure src={`${media}quick-sdf-advanced.png`} alt={t.imageAlts.advanced} fullSizeLabel={t.openFullSize} legend={t.advancedLegend} />
              <div>
                <dl className="case-list">{t.advancedCases.map(([need, action]) => <div key={need}><dt>{need}</dt><dd>{action}</dd></div>)}</dl>
                <p className="plain-note">{t.advancedNote}</p>
              </div>
            </div>
          </div>
        </section>

        <section className="manual-section help-section" id="help">
          <div className="page-shell help-grid">
            <header className="section-title"><h2>{t.helpTitle}</h2><p>{t.helpLead}</p></header>
            <div className="trouble-list">{t.troubles.map(([title, body], index) => <details key={title} open={index === 0}><summary>{title}<span aria-hidden="true">＋</span></summary><p>{body}</p></details>)}</div>
          </div>
        </section>

        <section className="manual-section reference-section">
          <div className="page-shell reference-grid">
            <div><h2>{t.referenceTitle}</h2><p>{t.referenceLead}</p><ul>{t.limitations.map((item) => <li key={item}>{item}</li>)}</ul></div>
            <div className="reference-links"><a href="https://github.com/yeczrtu/QuickSDFBlender" target="_blank" rel="noreferrer">{t.github}<span aria-hidden="true">↗</span></a><a href="https://github.com/yeczrtu/QuickSDFBlender/issues" target="_blank" rel="noreferrer">{t.issue}<span aria-hidden="true">↗</span></a><a href="https://mochiyama.com/kipfel_manual_jp" target="_blank" rel="noreferrer">{t.attribution}<span aria-hidden="true">↗</span></a></div>
          </div>
        </section>
      </main>

      <footer className="site-footer"><div className="page-shell"><span>{t.footer}</span><span>© 2026 Hoshino · GPL-3.0-or-later</span></div></footer>
    </>
  );
}
