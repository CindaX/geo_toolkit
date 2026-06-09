# Shopify App + GEO API 进展笔记

> 这是「Shopify 插件」这条线的交接笔记,记录做到哪、下次从哪继续。
> 配合项目宪法一起看。最后更新: 2026-06-09

---

## 一句话现状

GEO 逻辑已包装成 API 并部署上公网,可被任何程序调用。**下一步是让 Shopify app 调这个 API + 用 Polaris 做界面**(阶段5)。

---

## 已完成 ✅

### Shopify app 脚手架
- 项目位置: `~/Desktop/geo-shopify-app/geo-toolkit` (Remix + TypeScript)
- 已能在测试店安装运行 (空壳,还没接入 GEO 功能)
- 测试店: Cinda Geo (cinda-geo.myshopify.com)
- Partner 组织: AUKSON INC
- 启动方式: 在项目目录跑 `shopify app dev` (会建隧道 + 在测试店预览)
- 已接入 Shopify Dev MCP 到 Claude Code (项目级)

### GEO Audit API (核心成果)
- **公网地址: https://geo-audit-api-6vel.onrender.com**
- 部署平台: Render (free 档, Blueprint managed, 服务名 geo-audit-api)
- 代码位置: `geo_toolkit` (Python 项目) 的 `api/` 目录
  - `api/main.py` — FastAPI: POST /audit + GET /health
  - `api/protections.py` — 防刷三件套
  - `api/requirements-api.txt` — 精简依赖
  - `render.yaml` (仓库根) — Render 部署配置
- 已验证: **同步返回完整 audit,Render 扛得住 100 秒长请求 → 异步化暂不需要**
- 鉴权: 请求需带 header `X-API-Key` = GEO_API_KEY
- 防护: 限流 (5/分, 50/天) + 每日成本熔断 ($20) + URL结果缓存 (24h)
- 钱包隔离: API 用独立 OpenRouter key (不连累 Streamlit 付费用户)

### 架构决策 (路1)
```
Python GEO 逻辑 (run_audit, 干净纯函数)
  → 包装成 FastAPI (api/)
  → 部署 Render
  → Shopify app (Remix) 调用它  ← 下一步
```
关键: run_audit() 零 Streamlit 耦合,直接复用,没重写逻辑。

---

## 下一步: 阶段5 — Shopify app 接 API ⬜

在 **Shopify 项目** (`~/Desktop/geo-shopify-app/geo-toolkit`) 里做:
1. app 内做一个页面: 获取当前店铺 URL → 调 GEO API → 展示结果
2. 用 Polaris 组件把 8 维度 audit 结果渲染成 Shopify 原生 UI
3. API 调用要带 X-API-Key (GEO_API_KEY)
4. 注意: 调用 https://geo-audit-api-6vel.onrender.com/audit

⚠️ 用 Shopify 项目里那个接了 Dev MCP 的 Claude Code 来写。

---

## 之后还有 ⬜
- 阶段6: listed app 上架审核 (Polaris合规/性能/隐私政策/"数据给第三方"合规)
- 品牌名 (上架用,不能含"Shopify"/不能太通用,需查重)
- app 内的付费墙设计 (怎么收费)

---

## 已知技术债 / 待办
- emails 表 RLS 关闭 (UNRESTRICTED 状态) — Supabase 安全债,待收紧
- Render free 档会休眠 (首请求慢~50秒) + 内存防护重启清零 — 上线前考虑升 $7/mo 常驻
- 异步化: 当前同步可用,若以后并发量大或换平台再考虑

---

## 两个易混项目 (别搞错!)
```
geo_toolkit  (下划线) = Python/Streamlit + API  → /Users/cindrax/Desktop/AI 软件创业/geo_toolkit
geo-toolkit  (连字符) = Shopify app (Remix)      → /Users/cindrax/Desktop/geo-shopify-app/geo-toolkit
```

---

## 关键账号/位置速查
- Render 控制台: dashboard.render.com (服务 geo-audit-api, ID srv-d8ju7h48aovs73dfn0f0)
- API 公网: https://geo-audit-api-6vel.onrender.com
- GitHub: github.com/CindaX/geo_toolkit
- Shopify Partner: AUKSON INC / 测试店 cinda-geo.myshopify.com
- key 都在各自 .env / Render 环境变量里 (不在代码/git)
