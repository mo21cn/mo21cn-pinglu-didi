# UI 原型 V2 样式调整

使用内置 image_gen 图像编辑功能，以 dual-role-wireframes-v1.png 为内容与布局基准，以用户提供的 97a9b29e5b7573ca95aeff4d4c5becfd.webp 为配色和构件样式参考。

输出：dual-role-wireframes-v2.png。保留 V1 原图。调整为浅薰衣草背景、柔白圆角卡片、细描边、圆形返回按钮和纵向蓝紫渐变主按钮。业务开发继续暂停。

完整提示词：

```text
Edit image 1 (the six-screen Chinese 平陆滴滴打船 UI storyboard). Image 2 is STYLE REFERENCE ONLY. Restyle all six screens to closely match image 2's palette and component design. Keep ALL original content, field labels, sample values, section order, screen order, navigation, interactions and number of components from image 1. Do not introduce any reference-image booking, truck, payment-method or English content.

Output one high-resolution portrait 3-column x 2-row six-phone storyboard, same composition as image 1, fully visible screens, crisp readable Chinese. Change only the board version label to “UI 原型 V2”. Captions remain: 01 身份选择; 02 货主端·找船; 03 船东端·找货; 04 发布货物; 05 发布空船; 06 我的订单.

STYLE MUST FOLLOW IMAGE 2:
- Whole screen backgrounds are very pale desaturated lavender/periwinkle (#E8E7F8, #DDDFF5) softly fading toward off-white at the bottom. Remove saturated royal-blue/purple header blocks entirely.
- Canvas light cool gray (#E7E8EB). Very restrained diffuse shadows, thin white phone outlines and larger soft rounded screen corners.
- Cards creamy white/translucent white on pale lavender, generous 20–28px-style corner radius; very faint gray dividers; selected cards have a hairline muted indigo outline, not thick colored borders.
- Capsule primary buttons with a VERTICAL gradient from soft lavender at top (#B6A0ED) to rich cobalt-blue at bottom (#3C60DC), white type. Subtle smooth highlight like reference, no neon and no heavy glow. Secondary buttons pill-shaped with thin muted indigo or gray outlines.
- Circle white back-button containers, small restrained charcoal/slate thin line icons, black or near-black headings, medium-gray explanatory text, restrained indigo links. Small green status/location accents permitted where matching existing semantic elements, but do not replace text.
- Field groups use soft recessed/off-white rounded controls and subtle outlines, rather than many harsh blue rectangles.
- Identity selection screen should also adopt the soft lavender look: keep the existing port/boat backdrop very faint and desaturated behind the same floating identity modal. Preserve platform name, existing subtitle, two identity choices and footer. Do not add a third identity.
- Existing ship icons can be restyled as restrained miniature ship illustrations in white rounded icon containers, never trucks or cars.
- Keep avatar/GPS ABOVE unified 搜索/AI/客服 bar on ONLY screens 02 and 03. No search/AI/service controls on screens 04,05,06.
- Keep exactly five tab items on screens 02,03,06, with 港口 and 我的 placeholders. Center + action stays prominent but takes the reference's lavender-to-cobalt gradient. Screen02 找船/订单/发布货物/港口/我的; Screen03 找货/订单/发布空船/港口/我的; Screen06 uses shipper tabs with 订单 active. No tab bar on the two release forms.
- Preserve every form field, save draft and submit button, order statuses and task list, recommended ships and routes, cargo cards, dates and example counts exactly from image 1.
Preserve structural density and usability, don't shrink text excessively or omit content to create whitespace. This is a style-only revision, not a new design or feature proposal. Do not add external map lines or decorative pins from image2's board. No extra panels.
```
