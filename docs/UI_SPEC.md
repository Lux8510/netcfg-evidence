# 界面设计规范对照

这份文档把「极简、科技、产品感」拆成可检查的条目，逐条对应实现位置。
不是设计宣言，是验收清单：每条都能在代码里指到具体类名或组件。

## 1. 布局与结构

| 要求 | 实现 | 位置 |
|---|---|---|
| Tailwind CSS 响应式布局 | 12 列栅格 + `sm/md/lg/xl` 断点 | `views/Dashboard.tsx` |
| 三栏工作台 | 3/6/3 栅格：左栏版本与采集，中栏证据画布，右栏拓扑与审计；底部通栏审批链 | `Dashboard` 主栅格 |
| Bento 感 | 卡片尺寸按任务分级，不做等宽平铺；窄屏按 1 → 2 → 3 列堆叠 | `StaggerItem` 的 `col-span` / `order` |
| 信息层级清晰 | 标题 14px / 正文 12–13px / 辅助 11px，三级字号 | `CardHeader`、`.label` |
| 留白充足 | 卡片内边距 20px，卡片间距 16px，页面最大宽 1440px | `Card`、`main` |
| 证据脊柱（签名元素） | 漂移/规则条目左侧常驻行号槽，悬停联动、点击打开原文 | `LineSpine` |

### 版式：三栏工作台

```
┌──────────┬──────────────────────┬──────────────┐
│ 版本      │ 漂移（主画布）         │ 拓扑          │
│ SSH 采集  │ 规则与证据            │ 审计          │
├──────────┴──────────────────────┴──────────────┤
│ 变更单 + 审批链 + 解释（通栏）                    │
└────────────────────────────────────────────────┘
```

窄屏顺序是「漂移/规则 → 版本/采集 → 拓扑/审计 → 审批链」，保证第一屏先看到证据。

### 签名元素：证据脊柱

普通后台把行号藏在弹窗里；这里把行号放在每条证据的左侧，成为版式的一部分。

- 常驻：不用打开弹窗就能看到这条 Finding 对应哪几行
- 联动：悬停行号，另一张卡里同一接口或同一行的条目同时高亮
- 可追溯：点击行号，直接打开该版本的原文并定位
- 无障碍：行号是按钮，可 Tab 聚焦，`aria-label` 为「第 N 行」

这不是装饰，是把「结论必须能指回某一行」变成可操作的界面。

## 2. 视觉与质感

| 要求 | 实现 | 位置 |
|---|---|---|
| 细边框 | 1px `--color-line`，深浅主题各一套 | `.glass` |
| 轻阴影 | 只有一层 ambient 阴影 + 一条内高光，无多层堆叠 | `.glass` |
| backdrop-blur | 卡片 `backdrop-blur-xl`，弹窗背景 `backdrop-blur-sm` | `.glass`、`Dialog` |
| 噪点纹理 | 内联 SVG `feTurbulence`，透明度 0.35，浅色主题降到 0.18 | `.noise::after` |
| 渐变网格 | 一层固定 `.mesh`（radial ×2 + conic），透明度 ≤ 0.09，无动画 | `index.css`、`App.tsx` |

## 3. 动效

| 要求 | 实现 | 位置 |
|---|---|---|
| hover feedback | 边框提亮 + 上移 1px，300ms | `.glass-hover` |
| scroll reveal | `whileInView` + `once`，位移 14px | `Stagger` / `Reveal` |
| stagger animation | 子项间隔 55ms，容器只触发一次 | `staggerParent` |
| smooth transition | 统一 `cubic-bezier(.22,1,.36,1)`，450ms | `staggerChild` |
| 克制、有呼吸感 | 无循环动画（除加载点）、无弹跳、减少动效时全部关闭 | `useReducedMotion` |

## 4. 3D 与空间感

| 要求 | 实现 | 位置 |
|---|---|---|
| Three.js / R3F 背景 | 900 点粒子场 + 线框二十面体 + 细环 | `components/Scene.tsx` |
| 作为氛围 | 固定背景、`-z-10`、透明度 0.7/浅色 0.32、不接收指针 | `.scene` |
| 不抢主体 | 窄屏（<900px）和 `prefers-reduced-motion` 时不加载 | `App.tsx` |

## 5. 组件与产品感

| 组件 | 实现 | 位置 |
|---|---|---|
| Button | primary / outline / ghost / danger，两档尺寸 | `ui.tsx` |
| Card | 统一玻璃容器 + 标题栏 | `Card`、`CardHeader` |
| Input / Select / Textarea | 同一套边框、聚焦环、错误态 | `fieldClass` |
| Modal | 焦点陷阱、Esc 关闭、焦点归还、弹簧入场 | `Dialog` |
| Toast | 右下堆叠、4.2s 自动消失、`aria-live` | `ToastProvider` |
| Empty State | 虚线边框 + 图标 + 说明 | `EmptyState` |

### 审批链

变更单卡片顶部有一条横向链：创建 → 提交 → 批准 → 执行，已完成实心、待处理空心、驳回断链并显示原因。链尾是变更单 ID 的短哈希，方便口头引用。

## 6. 整体风格

- 定位：工具型产品，不是营销页；首屏就是可操作的工作台。
- 重点：漂移和规则结果占最大面积，版本与审计为辅助列。
- 动效：只在进入、悬停、弹窗出现时发生，不做装饰性循环。

## 7. 禁止项自查

| 禁止 | 现状 |
|---|---|
| 过度渐变 | 只有一层低透明度 mesh，无卡片内渐变 |
| 廉价发光 | 圆点光晕已从 18px/0.9 降到 10px/0.45；按钮阴影只用于主操作 |
| 大圆角堆叠 | 卡片 16px、输入 8px、徽章全圆，层级不叠加 |
| 信息过满 | 每张卡只放一个任务；原始配置进弹窗，不铺在首屏 |

## 验证方式

```bash
cd frontend && npm run build      # 产物必须与 web/ 一致
make e2e                          # 15 项真实浏览器测试
```

截图：`docs/ui-dashboard.png`（深色）、`docs/ui-dashboard-light.png`（浅色）、`docs/ui-topology.png`、`docs/ui-evidence-spine.png`（行号联动）、`docs/ui-custody-chain.png`（审批链）。
