# 双身份 UI 线框原型 V1

本次仅生成 UI 原型图片，修复与业务开发按用户要求暂停。本原型用于布局与交互讨论，不代表相关能力已经实现，也不自动更新开发范围。

工具：内置 image_gen。参考图：用户提供的“平陆滴滴打船.jpg”。

输出：dual-role-wireframes-v1.png。六屏顺序：身份浮窗、货主找船、船东找货、发布货物、发布空船、我的订单。

港口与我的仅保留底栏占位，不设计二级页面。搜索、AI、客服只在两端首页出现。发布页面采用返回导航和固定提交栏。订单示例为货主操作，船东端可沿用结构调整操作。

以下为本次完整生成提示词：

```text
Use case: ui-mockup.
Create ONE coherent high-resolution Chinese mobile WeChat mini-program UI wireframe storyboard for “平陆滴滴打船”. Six full-length phone screens in an orderly 3-column by 2-row grid on a light neutral canvas, equal scale, no cropped screens. Large readable Simplified Chinese typography. Portrait overall canvas with ample room for six screens, ideally 3072x3840. This is a medium-fidelity colored WIREFRAME for reviewing layout and interaction, not an advertisement. White rounded cards, restrained blue-to-purple gradients (#3878F6 to #8256E8) on headers and primary actions, thin slate icon strokes, pale blue-gray page backgrounds. No orange theme, truck icons, stock photography, marketing banners, 3D phones, perspective, or excessive decorations.

Input image 1 is REFERENCE ONLY: use upper-left card-based dispatch layout for shipper, upper-right freight hall for shipowner, lower-left identity overlay pattern, lower-right order overview. Do NOT copy its branding, automotive labels, identity cancellation text, or orange styling. Follow the user specifications below.

Board title “平陆滴滴打船 · UI 原型 V1”, subtitle “双身份入口 / 卡片式首页 / 发布二级页 / 订单”. Screen captions OUTSIDE each phone:
01 身份选择
02 货主端 · 找船
03 船东端 · 找货
04 发布货物
05 发布空船
06 我的订单

Global navigation: NO separate generic 首页/landing page. Choosing an identity immediately opens its own home screen. Only TWO identity options 货主 and 船东. 港口 is not an identity option. Persistent bottom nav on the two home screens and orders screen has exactly five items:
shipper: 找船 | 订单 | prominent raised central + 发布货物 | 港口 | 我的
shipowner: 找货 | 订单 | prominent raised central + 发布空船 | 港口 | 我的
港口 and 我的 are muted placeholder entries only, do not draw their destination screens. For release forms use back navigation and a fixed submit bar INSTEAD of bottom tabs.
Search/AI/customer service control ONLY appears on the two role home screens. Above it at top left place circular USER AVATAR and GPS pin with “南宁 · 平塘港” location. A small role pill at right reads “货主 ▾” or “船东 ▾”. Unified search pill contains search icon, “搜索船源、航线” or “搜索货源、航线”, then clearly separated “AI” and “客服” affordances on its right. Do not put this search/AI/service bar on forms or orders. Include restrained WeChat capsule at top right and mobile status bar.

01: First-launch identity picker as a large white floating modal over a dimmed neutral blue-purple launch background (NOT a homepage). Tiny platform name behind; title “请选择您的身份”, subtitle “选择后直接进入专属工作台”. Exactly two large tappable cards stacked: “我是货主” / “发货找船，查看推荐航线” with parcel outline icon and right arrow; “我是船东” / “寻找货源，发布空船” with ship outline icon and right arrow. Small footer “后续可切换身份”. No 港口, no 经纪人, no cancellation/account registration mechanics, no generic homepage buttons.

02: Home title “找船”. Avatar/GPS and unified search in blue-purple header as above. Main WHITE card “我要发货”: origin row “装货港  南宁 · 平塘港”, destination row “卸货港  请选择目的港”, divided fields “货物资料  品类 / 吨数” and “用船需求  船型 / 装货日期”. Two compact options “整船运输” and “拼船运输” as concept choices, selected first; primary button “查看匹配船源”. Below “推荐船源” with compact 2 cards: “桂航 008” / “散货船 · 2000 吨” / “南宁可装 · 查看详情”; second “西江 016” / “散货船 · 3000 吨”. Below “推荐航线” in 2-column mini cards “南宁 → 贵港”, “南宁 → 钦州”, modest subtitle “查看沿线船源”. Clear white space, no invented prices. Five bottom tabs as specified with 找船 active and central 发布货物.

03: Home title “找货”. Same header hierarchy, avatar/GPS, role, unified search. Section “货源大厅”. A filter card row “装货港 ▾  卸货港 ▾  船型 ▾”, second small sort choices “最新发布  距离优先  筛选”. Three substantial cards with cargo and route details. Example 1 “南宁 → 贵港”, “水泥熟料 · 1200 吨”, “9月15日装货 · 散货”, “运费面议”, small avatar “陈先生” and outline CTA “查看货源”. Example 2 “贵港 → 梧州”, “钢材 · 800 吨”, “9月18日装货”, “运费面议”, CTA “查看货源”. Example 3 “钦州 → 南宁”, “设备 · 200 吨”, “9月20日装货”. No actual call, bidding, or new secondary detail screens. Five bottom tabs with 找货 active, center 发布空船.

04: Proposed second-level form for central 发布货物 action. Top back arrow and title “发布货物”; no home search/AI/customer service. Four numbered grouped white cards fitting the screen:
“01 运输路线” fields “装货港” / “卸货港” with example 南宁/贵港.
“02 货物信息” fields “货物名称”, “货物类型”, “重量（吨）”, “包装方式”.
“03 用船与时间” fields “期望装货日期”, “所需船型” and note “按货物条件匹配”.
“04 运费与备注” segmented “面议 / 一口价”, optional amount line and short textarea “补充说明（选填）”.
Bottom concise checkbox “已确认货物信息真实准确”.
Fixed bottom action strip: secondary “保存草稿” and large blue-purple “确认发布”. Field labels legible, choices shown as wireframe controls. No new AI control.

05: Proposed second-level form for central 发布空船 action. Back arrow “发布空船”; no search/AI/service and no tabs.
White card “01 选择船舶” shows select row “桂航 008 ▾”, small existing vessel summary “散货船 · 载重 2000 吨 · 已认证”.
“02 当前运力” fields “当前停靠港”, “可用载重（吨）”, “空船可用日期”.
“03 期望航线” fields “出发港”, “目的港（可选）” with compact chip “不限目的港”.
“04 报价与备注” selector “运费面议 / 参考报价”, short textarea “补充说明（选填）”.
Fixed bottom action strip secondary “保存草稿”, primary “发布空船信息”. These are UX concepts, not an assertion of implemented backend functionality.

06: Order overview inspired by lower-right reference, retain nautical semantics and current simple statuses. Back arrow and title “我的订单” at top, optional small notification bell. NO search/AI/service bar. Upper WHITE card has 5 evenly spaced icon shortcuts with counts and captions “全部”, “待承运”, “运输中”, “已完成”, “已撤销”. Below white “待处理事项” panel with two rows “待支付订单  1 笔  ›” and “待确认收货  1 笔  ›”. Below “最近订单” card shows “南宁 → 贵港”, “水泥熟料 · 1200 吨”, status badge “待承运”, outline “查看合同” and gradient “去支付”. Second compact order “贵港 → 梧州” / “运输中”. Bottom five tabs use shipper variant with 订单 active; add small OUTSIDE-screen annotation “订单结构两端共用，操作随身份变化”. Do not draw 港口 or 我的 pages.

Add tiny board footer outside phones: “交互原型 · 示例数据 ｜ 港口、我的仅占位”. Prioritize precise Chinese labels and component hierarchy. No source-code, implementation jargon, APIs, feature numbering, or previous audit details in the UI. Draw all six full screens in one unified storyboard with no extraneous seventh screen.
```
