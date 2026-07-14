# SPDX-License-Identifier: GPL-3.0-or-later
"""Small, explicit English/Japanese UI dictionary for Quick SDF Studio."""

from __future__ import annotations

import bpy


TRANSLATION_KEY = "quick_sdf_blender"

TRANSLATIONS = {
    "ja_JP": {
        ("*", "Quick SDF Studio"): "Quick SDF Studio",
        ("*", "Quick SDF Paint"): "Quick SDF ペイント",
        ("*", "Quick SDF Timeline"): "Quick SDF タイムライン",
        ("*", "Create & Edit"): "作成して編集",
        ("*", "Select the face mesh"): "顔のメッシュを選択",
        ("*", "Then create a paint-ready face shadow."): "すぐに塗れる顔影を作成します",
        ("*", "A material will be created"): "マテリアルを自動作成します",
        ("*", "Auto-bakes the current pose, then opens Studio."): "現在のポーズを自動ベイクしてStudioを開きます",
        ("*", "Open Quick SDF Studio"): "Quick SDF Studioを開く",
        ("*", "Exit Quick SDF"): "Quick SDFを終了",
        ("*", "Light"): "明部",
        ("*", "Shadow"): "影",
        ("*", "Light / Shadow"): "明部 / 影",
        ("*", "Mirror On"): "左右対称 ON",
        ("*", "Paint"): "ペイント",
        ("*", "Paint Overlay"): "ペイント重ね表示",
        ("*", "Preview"): "プレビュー",
        ("*", "Mask"): "マスク",
        ("*", "Toon Result"): "トゥーン結果",
        ("*", "Export Face Shadow Texture"): "顔影テクスチャを書き出す",
        ("*", "Choose an angle · choose Light or Shadow · paint"): "角度を選ぶ・明部か影を選ぶ・塗る",
        ("*", "A light-sweep guide from rear oblique to full light is ready. Paint only the areas you want to adjust."): "斜め後ろから全体へ広がる影ガイドを作成しました。気になる部分だけ修正してください。",
        ("*", "A light-sweep guide from rear oblique to full light is ready. Paint only the areas you want to adjust. Paint between keys to add that angle."): "斜め後ろから全体へ広がる影ガイドを作成しました。気になる部分だけ修正してください。キーの間で塗ると、その角度が自動で追加されます。",
        ("*", "Preparing this angle"): "この角度を準備中",
        ("*", "Preparing angle"): "角度を準備中",
        ("*", "Cancel Angle Preparation"): "角度の準備をキャンセル",
        ("*", "Paint here to add this angle"): "ここで塗ると、この角度を追加します",
        ("*", "Maximum 16 keys · select or delete a key"): "最大16キーです。近いキーを選ぶか不要なキーを削除してください",
        ("*", "No paint landed · move back or press Numpad 5"): "ペイントが届きませんでした。少し引くかNumpad 5を押してください",
        ("*", "Light Starts"): "光り始め",
        ("*", "Side"): "真横",
        ("*", "Full Light"): "全体が明部",
        ("*", "Open Quick SDF Studio to paint"): "Quick SDF Studioを開いてペイントしてください",
        ("*", "Paint Light or Shadow and keep all light angles consistent"): "明部または影を塗り、全角度の整合性を自動で保ちます",
        ("*", "Advanced"): "詳細設定",
        ("*", "Editing %s"): "%sを編集中",
        ("*", "Choose the preview that matches the face UV"): "顔のUVに合うプレビューを選択してください",
        ("*", "Whole Texture"): "テクスチャ全体",
        ("*", "Paired Islands"): "対応UVアイランド",
        ("*", "Shared UV"): "共有UV",
        ("*", "Rebake Base"): "ベースを再ベイク",
        ("*", "Base needs update"): "ベースの更新が必要です",
        ("*", "Onion"): "オニオン表示",
        ("*", "Add Key"): "キーを追加",
        ("*", "Duplicate Key"): "キーを複製",
        ("*", "Delete Key"): "キーを削除",
        ("*", "Angle Keys"): "角度キー",
        ("*", "Angle"): "角度",
        ("*", "Add at Seek"): "現在角度に追加",
        ("*", "Duplicate to Seek"): "現在角度へ複製",
        ("*", "Add Angle…"): "角度を追加…",
        ("*", "Duplicate Angle…"): "角度を複製…",
        ("*", "Move / Retime"): "角度を移動",
        ("*", "Delete"): "削除",
        ("*", "Break Mirror"): "左右対称を解除",
        ("*", "Auto Mirror"): "自動左右対称",
        ("*", "Right"): "右",
        ("*", "Left"): "左",
        ("*", "Cancel"): "キャンセル",
        ("*", "Character Axes"): "キャラクター軸",
        ("*", "Set Forward from View"): "現在のビューを正面に設定",
        ("*", "Use This View as Front"): "このビューを正面にする",
        ("*", "Adjust Shadow Guide"): "影ガイドを調整",
        ("*", "Shadow Amount"): "影の量",
        ("*", "Update Shadow Guide"): "影ガイドを更新",
        ("*", "The guide is nearly uniform; confirm which way the face points"): "影ガイドがほぼ一色です。顔の正面方向を確認してください",
        ("*", "Mirror"): "左右対称",
        ("*", "Layout"): "UV配置",
        ("*", "Paint Side"): "編集する側",
        ("*", "Review / Recovery"): "確認 / 復旧",
        ("*", "Export Review Masks"): "確認用マスクを書き出す",
        ("*", "Restore Materials"): "マテリアルを復元",
        ("*", "Remove Quick SDF Project"): "Quick SDFプロジェクトを削除",
        ("*", "Quick SDF Studio requires an interactive Blender window"): "Quick SDF StudioにはBlenderの操作可能なウィンドウが必要です",
        ("*", "Exit the current Quick SDF Studio before opening another project"): "別のプロジェクトを開く前に現在のQuick SDF Studioを終了してください",
        ("*", "Retry Export"): "書き出しを再試行",
        ("*", "Adjusted angle continuity and exported"): "角度のつながりを自動調整して書き出しました",
        ("*", "Review Export Adjustments"): "書き出し時の自動調整を確認",
        ("*", "%s authored pixels needed angle adjustment."): "%sか所の編集位置で角度のつながりを調整しました。",
        ("*", "%s angle samples changed"): "%s個の角度サンプルを調整",
        ("*", "%s edited pixels required adjustment."): "%sか所の手編集部分を調整しました。",
        ("*", "Export adjustments are shown in the Image Editor; choose an angle to return"): "書き出し時の調整を画像エディターに表示しました。角度を選ぶとペイントへ戻ります。",
    }
}

# Keep this safety-critical review state understandable even in source trees
# created under a non-UTF-8 Windows console.
TRANSLATIONS["ja_JP"][(
    "*",
    "Export adjustments are shown read-only; choose an angle to return",
)] = "\u66f8\u304d\u51fa\u3057\u8abf\u6574\u3092\u8aad\u307f\u53d6\u308a\u5c02\u7528\u3067\u8868\u793a\u3057\u307e\u3057\u305f\u3002\u89d2\u5ea6\u3092\u9078\u3076\u3068\u30da\u30a4\u30f3\u30c8\u306b\u623b\u308a\u307e\u3059\u3002"

# These messages are assembled at runtime so artists get an actionable reason
# for a no-op stroke instead of a misleading projection-paint failure.
TRANSLATIONS["ja_JP"].update({
    ("*", "No visible change"): "\u898b\u305f\u76ee\u306e\u5909\u5316\u306f\u3042\u308a\u307e\u305b\u3093",
    ("*", "this area may already be Light. Try Shadow"): "\u3053\u306e\u5834\u6240\u306f\u3059\u3067\u306bLight\u306e\u3088\u3046\u3067\u3059\u3002Shadow\u3092\u8a66\u3057\u3066\u304f\u3060\u3055\u3044",
    ("*", "this area may already be Shadow. Try Light"): "\u3053\u306e\u5834\u6240\u306f\u3059\u3067\u306bShadow\u306e\u3088\u3046\u3067\u3059\u3002Light\u3092\u8a66\u3057\u3066\u304f\u3060\u3055\u3044",
    ("*", "if the brush misses, move back or press Numpad 5"): "\u30d6\u30e9\u30b7\u304c\u5c4a\u304b\u306a\u3044\u5834\u5408\u306f\u5c11\u3057\u5f15\u304f\u304bNumpad 5\u3092\u62bc\u3057\u3066\u304f\u3060\u3055\u3044",
})

# Channel packing stays out of the daily Studio UI, but its Advanced labels
# still need to read as artist-facing concepts rather than implementation data.
TRANSLATIONS["ja_JP"].update({
    ("*", "Output Packing"): "出力パッキング",
    ("*", "Packing: lilToon"): "パッキング：lilToon",
    ("*", "Packing: Custom — This Project"): "パッキング：カスタム（このプロジェクト）",
    ("*", "Customize"): "カスタマイズ",
    ("*", "Reset to lilToon"): "lilToonに戻す",
    ("*", "Direct"): "そのまま",
    ("*", "Invert"): "反転",
    ("*", "Right Threshold"): "右側スレッショルド",
    ("*", "Left Threshold"): "左側スレッショルド",
    ("*", "SDF Area"): "SDF領域",
    ("*", "Shadow Strength"): "影の強度",
    ("*", "Custom Mask"): "カスタムマスク",
    ("*", "No Custom Mask Selected"): "カスタムマスク未選択",
    ("*", "Use Selected Mask"): "選択中のマスクを使用",
    ("*", "Constant"): "定数",
    ("*", "Constant output: %s"): "出力値：%s",
    ("*", "Black: %s · White: %s"): "黒：%s・白：%s",
    ("*", "lights late"): "遅く明るくなる",
    ("*", "lights early"): "早く明るくなる",
    ("*", "normal shading"): "通常法線シェーディング",
    ("*", "face SDF"): "顔SDF",
    ("*", "shadow off"): "影なし",
    ("*", "full shadow"): "影強度100%",
    ("*", "mask off"): "マスク0",
    ("*", "mask on"): "マスク1",
    ("*", "%s: Missing channel mapping"): "%s：チャンネル割り当てがありません",
    ("*", "Packing data is not available for this project."): "このプロジェクトにはパッキングデータがありません。",
    ("*", "Add a Custom Mask first."): "先にカスタムマスクを追加してください。",
    ("*", "Additional Masks"): "追加マスク",
    ("*", "Additional Mask"): "追加マスク",
    ("*", "These masks are shared by every angle."): "このマスクはすべての角度で共通です。",
    ("*", "Editing Mask: %s"): "マスクを編集中：%s",
    ("*", "Back to Face Shadow"): "顔影の編集に戻る",
    ("*", "Edit Mask"): "マスクを編集",
    ("*", "Import from Image"): "画像から読み込み",
    ("*", "Fill White"): "白で塗りつぶす",
    ("*", "Fill Black"): "黒で塗りつぶす",
    ("*", "Reset SDF Area from UV"): "UVからSDF領域を作り直す",
    ("*", "Add Custom Mask"): "カスタムマスクを追加",
    ("*", "Delete Mask"): "マスクを削除",
    ("*", "No additional masks."): "追加マスクはありません。",
    ("*", "Additional mask data is not available for this project."): "このプロジェクトには追加マスクデータがありません。",
    ("*", "Image: %s"): "画像：%s",
    ("*", "White"): "白",
    ("*", "Black"): "黒",
    ("*", "This mask is shared by every light angle"): "このマスクはすべてのライト角度で共通です",
    ("*", "Preview Packed RGB"): "パッキング済みRGBをプレビュー",
})

TRANSLATIONS["ja_JP"].update({
    ("*", "%s: Unknown source"): "%s：不明なソースです",
    ("*", "%s: Required mask is missing"): "%s：必要なマスクがありません",
    ("*", "%s: Select a Custom Mask"): "%s：カスタムマスクを選択してください",
    ("*", "%s: Mask image is missing"): "%s：マスク画像がありません",
    ("*", "%s: Mask resolution does not match the project"): "%s：マスクの解像度がプロジェクトと一致しません",
    ("*", "%s: Duplicate channel mapping"): "%s：チャンネル割り当てが重複しています",
})

_REGISTERED = False


def tr(message: str, context: str = "*") -> str:
    return bpy.app.translations.pgettext_iface(message, context)


def register_translations() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    try:
        bpy.app.translations.unregister(TRANSLATION_KEY)
    except RuntimeError:
        pass
    bpy.app.translations.register(TRANSLATION_KEY, TRANSLATIONS)
    _REGISTERED = True


def unregister_translations() -> None:
    global _REGISTERED
    try:
        bpy.app.translations.unregister(TRANSLATION_KEY)
    except RuntimeError:
        pass
    _REGISTERED = False


CLASSES: tuple[type, ...] = ()


__all__ = [
    "CLASSES", "TRANSLATIONS", "TRANSLATION_KEY", "register_translations", "tr",
    "unregister_translations",
]
