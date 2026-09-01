<div align="center">

# reversedd-skill

> 「报个公司名。回车。一份签字前该看的避坑报告。」
> *"Name a company. Hit enter. A red-flag report you read before you sign."*

**反向背调 · 求职者避坑。**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Agent-Agnostic](https://img.shields.io/badge/Agent-Agnostic-blueviolet)](https://skills.sh)
[![No API Key](https://img.shields.io/badge/API%20Key-不需要-blue)](https://github.com/bbruoyu/reversedd-skill)

<br>

**在你的 agent 里报个公司名，拿回一份签字前该看的避坑报告。**

<br>

平时都是公司背调你，这次反过来。你会拿到：一份七维风险雷达与红黄绿灯结论、一张从母品牌穿透到关联壳 / 子公司 / 分公司的关系图、一份按你求职阶段定制的面试反问清单，和一句「去 / 不去」的决策建议。

不是「小红书避雷帖聚合器」那类二手情绪——是裁判文书网的劳动争议、失信被执行记录、行政处罚、年报参保人数趋势。给 skill 你刷到的那条避雷帖，它拿去硬源交叉验证是真还是假；一条都不给，纯公开检索 + 政府公开站也能跑出完整报告，**零后端、零 API key、零登录**。

你在这篇 README 顶部看到的那张风险雷达，就是 reversedd-skill 自己渲的。不是付费企查查会员，不是爬虫面板，就是一句话 + skill 跑通，纯标准库离线出图。下次拿到 offer 心里没底？现在你自己就能查。

[能做什么](#它能做什么) · [装上就能用](#快速开始) · [设计原则](#设计原则) · [目录结构](#目录结构)

</div>

---

<p align="center"><sub>
  👉 渲染你自己的报告：<code>python scripts/render_report.py 你的报告.md -o 你的报告.html</code>
</sub></p>

## 它能做什么

- 跨公开平台聚合口碑与风险信号（看准 / 职友集 / 牛客 / 黑猫 / **知乎（口碑主力）** / 贴吧 / 微博 / 新闻）；小红书无法主动检索，其真实员工口碑由知乎 + 贴吧 + 微博补强
- **硬风险源核查**（杀手锏）：裁判文书网劳动争议、失信被执行、行政处罚、年报参保人数趋势
- 把用户从小红书看到的避雷帖拿去官方硬源交叉验证（辨真伪、找硬证据）
- 研判创始人 / 实控人言行与战略变化：画饼话术、业务线收缩 / 关停、套现离场信号（言论可公开检索、工商变更属硬信号）
- 横向对比薪资市场基准：把你的 offer 与同岗位同城市市场区间比对，冷门岗诚实标「无法评估」
- 输出结构化「红黄绿灯报告」+ 面试反问清单 + 决策建议
- **可选可视化**：渲染出自包含 HTML 报告，含风险雷达图 + 关系穿透图（离线可用、零依赖）

## 目录结构

```
reversedd-skill/
├── SKILL.md                       # skill 入口（描述 + 工作流 + 引用）
├── LICENSE                       # MIT
├── README.md
├── references/
│   ├── system_prompt.md          # 系统提示词（粘贴到任意 AI 平台）
│   ├── knowledge_base.md         # 风险源知识库
│   ├── import_guide.md           # 元器 / Coze / Dify 导入指引
│   ├── decision_framework.md     # 红绿灯评分维度 + 报告模板
│   ├── interview_questions.md    # 面试反问清单 + 黑话解码
│   ├── risk_sources.md           # 硬风险源核查清单
│   └── search_playbook.md        # 多源检索话术
├── examples/
│   └── example_report.md         # 输出样例报告（含 relationship-graph 块，可直接渲染）
└── scripts/
    ├── report_renderer.py        # 渲染器（仅标准库，离线）
    ├── render_report.py          # CLI：Markdown -> HTML（支持 --financials）
    └── financial_fetcher.py      # 上市公司财报抓取（东方财富公开 API，无密钥）
```

## 快速开始

### 1. 接入 agent（核心能力，无需代码）

把 `references/system_prompt.md` 的完整内容粘贴到任意 AI 平台的「系统提示词 / System Prompt」框，
并把 `references/knowledge_base.md` 作为知识库上传（或拼接在提示词末尾）即可。

支持：腾讯元器 / 扣子 Coze / Dify / 自建后端 / 本机 WorkBuddy。

#### 在 WorkBuddy 中安装（本机推荐）

把整个 `reversedd-skill/` 文件夹放进 WorkBuddy 的技能目录即可，零配置：

```bash
cp -r reversedd-skill ~/.workbuddy/skills/reversedd-skill
```

刷新 / 重启 WorkBuddy 后，在对话里说「反向背调 XX 公司」「查一下 XX 公司」即可触发。
（也可在 WorkBuddy 的 Skills 面板用「从本地目录导入」加载。）

### 2. 可选：渲染可视化 HTML 报告

需要 Python 3.8+（仅标准库，无网络、无 API key）：

```bash
cd scripts
python render_report.py 报告.md            # 输出 报告.html（同目录）
python render_report.py 报告.md -o out.html
```

LLM 输出的 Markdown 只要含 `> 核查主体：…` 头部与 `relationship-graph` 代码块，即可被自动解析并绘图。

想先看效果？直接用仓库自带的样例：

```bash
cd scripts
python render_report.py ../examples/example_report.md -o example_report.html
```

### 3. 可选：叠加上市公司「财务快照」卡

仅对上市公司生效。实体对齐拿到股票代码后，用东方财富公开 API（无需密钥）抓财报摘要：

```bash
cd scripts
python financial_fetcher.py --company "浦发银行" --code 600000 -o fin.json
python render_report.py 报告.md --financials fin.json   # 报告中叠加「财务快照」卡
```

非上市公司无公开年报，跳过此步，「成长发展」维度据工商 / 招聘信号推断并标注。

## 设计原则

- **七维研判框架**：硬风险 / 薪酬真实度 / 强度与边界 / 管理文化 / 成长发展 / 口碑一致性 / 治理与决策权
- **来源层级标注**：`[L1 硬数据]` / `[L3 口碑]` / `[L4 用户情报]`，不伪造、不夸大
- **实体对齐**：品牌 ≠ 法律主体，先核实实际雇主全称与统一社会信用代码
- **关系穿透**：从母品牌穿透到关联壳 / 子公司 / 分公司并逐个查诉讼
- **只读不写**：全程检索分析，绝不发帖 / 加好友 / 私信
- **零密钥**：不要求任何 API key 也能跑（走公开检索）

## 开源协议

[MIT](./LICENSE) —— 可自由使用、修改、再分发。
