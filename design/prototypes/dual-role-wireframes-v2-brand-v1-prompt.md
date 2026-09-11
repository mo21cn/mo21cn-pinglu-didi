# V2 首屏品牌替换

内置 image_gen 编辑：首屏底图替换为用户提供的河道航拍照片，保留浅薰衣草淡化处理；标题替换为用户提供的船好多 logo，标语改为“船好货多赚好多”。原 V2 保留，业务开发继续暂停。

输出：dual-role-wireframes-v2-brand-v1.png

完整提示词：

```text
Perform a precise localized image edit. Image 1 is the existing six-screen Chinese UI storyboard and is the EDIT TARGET. Image 2 is the replacement river aerial PHOTO. Image 3 is the exact provided LOGO asset, with stylized Chinese “船好多”, navy and orange ship graphic. Output the same full six-screen storyboard at the same proportions and resolution, no cropping.

Modify ONLY the interior upper branding/background region of the TOP LEFT phone “01 身份选择”:
1. Replace the old illustrated harbor/mountain background with the actual aerial river/bridges landscape from image 2. Use the same pale low-opacity lavender/periwinkle treatment as the original: desaturate and wash with translucent light lavender/white overlay, preserve visible aerial river curves, forests and bridges, fade smoothly into the pale lavender bottom background. Do not leave the original illustrated ships/crane backdrop. Keep photo subtle enough for dark logo and slogan to read clearly. Do not show the saturated green original unprocessed.
2. Replace the text “平陆滴滴打船” INSIDE that phone with the provided image 3 logo, faithfully preserve its distinctive stylized glyph shapes, ship silhouette and original navy/orange colors. Place the whole horizontal logo centered in the same branding position, fitting inside phone width with comfortable margins, maintain aspect ratio without cropping. Remove the white rectangular source background so the logo sits cleanly on the pale photo. Do not type a substitute wordmark, do not distort/redesign/recolor the logo.
3. Replace the slogan “让水运更简单” below that logo with EXACT Simplified Chinese “船好货多赚好多”. Exactly seven characters: 船 好 货 多 赚 好 多. One centered line, modest tracking, same typographic scale and dark color as original slogan.

STRICT PRESERVATION:
Keep identity modal, its location, two option cards, all modal wording/icons, status bar and WeChat capsule unchanged. Keep ALL other five phone screens visually unchanged, including every field, label, example datum, card, button, tab, typography, spacing, colors and layout. Keep outside storyboard titles/captions/footer unchanged, including board title V2 (do not rename global title to logo; only the in-phone branding is replaced). No extra screens, no new UI elements. Maintain the established soft white-card lavender UI style. Crisp legible Chinese text. Only the three requested localized changes.
```
