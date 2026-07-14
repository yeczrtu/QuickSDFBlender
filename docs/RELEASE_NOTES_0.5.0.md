# Quick SDF Studio 0.5.0

0.5.0は、アーティスト向けの顔影編集フローを維持したまま、シェーダーごとのRGBA構成へ対応する更新です。通常はlilToon設定のまま、従来どおり1ボタンで書き出せます。

## 主な変更

- `Advanced > Output Packing`へ、Project専用のRGBA 4行マッパーを追加しました。
- 各チャンネルへRight／Left Threshold、SDF Area、Shadow Strength、Custom Mask、Constantを割り当て、Direct／Invertを選べます。
- R/G/B/AのSolo PreviewとRGB合成Previewを追加しました。
- 全角度共通の`SDF Area`、`Shadow Strength`、任意のCustom Maskを3D／2Dでペイントできます。
- Blender ImageのR/G/B/A/Luminanceから補助マスクを読み込めます。元Imageは変更しません。
- 出力は引き続き1枚の16-bit RGBA PNGです。数式、ノードグラフ、複数Texture出力は追加していません。

## lilToon既定パッキング

| Channel | 内容 |
|---|---|
| R | 右光スレッショルド |
| G | 左光スレッショルド |
| B | SDF Areaを反転。顔SDF領域は0、通常法線領域は65535 |
| A | Shadow Strength。通常は65535、影を無効化する領域は0 |

R/Gのライトスイープ値は0.4.0と同じです。

## 互換性

- Project schemaは5です。以前のProjectを移行する処理はありません。
- Extension versionは0.5.0です。
- Native core ABIは5のままです。
- Blender 5.1、Windows x64、単一Object／Material Slot／0–1 UVという対応範囲は変わりません。
