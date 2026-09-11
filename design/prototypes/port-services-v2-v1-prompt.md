# 港口二级页原型

工具：内置 image_gen。新增第 07 屏，既有六屏原型保留。参考用户“港口页面.png”左上水域地图、左下功能清单和右侧地图加浮层布局，沿用 UI 原型 V2 样式。

输出：port-services-v2-v1.png。

模块清单：3 个快捷入口；船闸服务 8 项（预约报闸、待闸查询、待闸队列、船闸水位、过闸费用、船闸信息、过闸记录、运行方案）；港口服务 3 项（业务办理、港口信息、船期表查询）；航道服务 4 项（水情气象、拖船租赁、燃料补给、船舶维修）。快捷入口复用服务项目，不另算新功能。

以上均为视觉占位。地图为参考图示意，标记不代表真实港口/船闸坐标。本轮不实现接口、不恢复业务开发。两身份共用港口页，底栏中央按钮按货主/船东显示发布货物/发布空船。

完整提示词：

```text
Use case: ui-mockup. Generate ONE new high-resolution portrait mobile UI prototype for the Chinese shipping mini-program 船好多, specifically the “港口” destination reached from the existing bottom navigation. ONE full front-facing phone screen only, large and readable on a pale cool-gray presentation canvas. Caption outside phone “07 港口服务 · UI 原型 V2”. No redesign of the previous six screens, no multi-screen collage.

Input image 1 has THREE roles: its UPPER LEFT waterway terrain map is the replacement map source; its LOWER LEFT module screenshot is the exact function inventory; its RIGHT phone is the structural layout reference (large upper map with controls over it, overlapping rounded bottom information panel). Input image 2 is the STYLE AUTHORITY (current six-screen UI V2). Do not import English, drone, parcel, truck, dollar prices, orange route, or delivery tracking data from image1's right reference.

Layout: tall phone ratio approximately 1:2.25, generous readability. Status bar 9:41. Map extends edge to edge behind top header, occupying upper 30–34% of screen. Floating round white back control left; centered dark navy title “港口”; WeChat capsule at top right. Use the TERRAIN AND WATERWAY MAP from the upper-left area of image1, NOT the urban map on the right. Preserve its winding broad cyan water, branched reservoir, pale green relief hills and thin roads, soften with a very light lavender overlay to harmonize with V2, keep map recognizable and labels subtle. No aerial photo. Add two unobtrusive outlined map pins labeled “港口” and “船闸” as schematic preview markers, with NO fabricated queue counts, live positions or numeric water levels. Small white round location button on lower right of map and a small pill “地图示意” at lower left. NO homepage search/AI/customer-service bar on this secondary page.

Bottom content is a large rounded-top soft-white/lavender scrollable sheet overlapping the map bottom, like the right-hand reference composition. Small central gray drag handle. At sheet top bold “港航服务”, restrained badge “功能预览”, and short gray helper “服务入口已预留，后续开放”. All modules are PLACEHOLDERS: no fake working reservations, payments or navigation, no fake statuses.

Arrange the complete inventory below in a compact but clearly readable layout, all visible in the full-screen prototype:
1. Three equal prominent rounded shortcut tiles in one row, each small line icon above exact label:
“预约报闸”  “过闸费用”  “待闸队列”.
Use pale periwinkle/lavender tile fills, indigo-blue icons, subtle borders; not orange/yellow multicolor blocks.
2. Rounded white group card heading “船闸服务”, trailing “即将开放” and small collapse chevron. Inside exactly EIGHT icon+label entries in a four-column, two-row grid:
row1 “预约报闸” / “待闸查询” / “待闸队列” / “船闸水位”
row2 “过闸费用” / “船闸信息” / “过闸记录” / “运行方案”.
Icons respectively sluice/ship, query-calendar, clock/ship, level gauge, receipt, document/waves, history/ship, operations wheel. NO omission or substitute labels.
3. Rounded white group card heading “港口服务”, trailing “即将开放” and chevron. Exactly THREE evenly spaced entries:
“业务办理” / “港口信息” / “船期表查询”.
4. Rounded white group card heading “航道服务”, trailing “即将开放” and chevron. Exactly FOUR entries:
“水情气象” / “拖船租赁” / “燃料补给” / “船舶维修”.
No invented extra service groups or new screens. All groups expanded in prototype so full inventory is reviewable. Use reasonable compact icon sizes and spacing; do not crop last row or put content beneath bottom bar.

Fixed bottom navigation exactly matching V2 shipper variant: “找船” | “订单” | raised center gradient + and “发布货物” | “港口” | “我的”. 港口 is the ACTIVE item in indigo; others inactive, central publish remains lavender-to-cobalt. User role does not change when entering 港口. Outside-phone small note “两端共用 · 船东端中央按钮为发布空船”. This is the port-service secondary content, not a third login identity. No added portal-specific identity selection. My remains placeholder.

STYLE: exactly the soft V2 design of input image2: pale desaturated lavender/periwinkle backgrounds (#E8E7F8), creamy translucent white cards with 20–28px-style radius, delicate gray dividers and indigo hairline outlines, soft diffuse shadows, dark navy Chinese headings, medium-gray helper text. Circle white navigation buttons. Only prominent primary/central action uses a VERTICAL gradient light lavender top to cobalt bottom, NOT highly saturated full header or orange. Consistent thin nautical line icons with small pale rounded icon backing tiles. Typography large, accurate Simplified Chinese, no English labels. White phone outline, flat straight-on UI screenshot, no tilted mockup or physical hardware thickness.
Final board footer outside phone: “港口二级页 · 功能模块占位 · 地图示意”. Prioritize faithful module completeness and a clean map-plus-panel hierarchy.
```
