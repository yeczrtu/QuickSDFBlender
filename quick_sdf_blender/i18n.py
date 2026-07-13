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
        ("*", "A normal-based shadow guide is ready. Paint only the areas you want to adjust."): "法線から影ガイドを作成しました。気になる部分だけ修正してください。",
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
        ("*", "Add at Seek"): "現在角度に追加",
        ("*", "Duplicate to Seek"): "現在角度へ複製",
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
        ("*", "Create Normal Shadow Guide"): "法線から影ガイドを作成",
        ("*", "Start with a shadow guide from the model"): "モデルから影の下描きを作成できます",
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
    }
}

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
