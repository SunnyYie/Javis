# 验收记录

日期：2026-11-05，Asia/Shanghai。

清理说明：应用户要求，已删除重复模型、临时会话、简历衍生文件、
验收运行数据库、导出与终端转录。下文历史命令仍保留以便复现，
不代表所有历史中间文件仍在磁盘；当前保留项见文末“测试数据清理”。

## 当前结论

终端 Agent、简历文本提取、本地中文 OCR、结构化职业信息、导入、匹配、缓存、
导出、会话恢复和会话清理已完成对应验证。

**BOSS 登录后实时岗位搜索尚未通过验收。**
真实浏览器探针收到首页 HTTP 200 并进入 `/beijing/`，随后页面变为 `about:blank`。
项目附带 Chromium 和普通 Chrome 的独立上下文均记录到该现象；
没有确认其根因，不将其归因于验证码、账号登录失败或某种具体风控机制。
本轮没有获取实时岗位、登录账号、发送招聘消息、投递或上传简历。

## 自动检查

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| `pytest -q`（清理后） | 105 passed，11 skipped | `data/acceptance/unit-final-junit.xml` |
| `ruff check src tests scripts` | 通过 | 最终代码运行结果 |
| `python -m compileall -q src tests scripts` | 通过 | 最终代码运行结果 |
| `JA_BROWSER_FIXTURES=1 pytest -q tests/test_browser_contract.py` | 4 passed | `data/acceptance/browser-contract-junit.xml` |

普通测试覆盖工资格式及未知周期、城市与经验差距、无默认公司排除、
模型失败与无效引用、条件变更与缓存隔离、会话确认与路径授权、
失败/中断记录、版本固定、导出公式注入和资料中的伪造授权。
当前 11 项默认跳过为 6 项真实服务和 5 项浏览器测试，并非“忽略失败”。
表中浏览器 JUnit 是早期 4 项验证记录，后续单独运行的 5 项结果见下文。

浏览器合同测试确实启动 Chromium，但页面是明确标注的合成 HTML。
它验证当前提取代码如何处理该页面，不证明当前 BOSS 线上选择器兼容。

## 真实服务

运行时使用独立数据目录、真实远程 `glm-5.3-flashx`、本地 RapidOCR 3.10、
ONNX Runtime 1.31.1 和 `127.0.0.1:16381` 的独立 Redis。
模型配置仅通过授权的知识助手 `.env` 中的模型字段获取。

完整命令：

```bash
JA_LIVE=1 \
JA_RESUME=/Users/sunyi/Documents/Codex/2026-11-04/new-chat-2/outputs/resume.pdf \
JA_EVIDENCE_DIR="$PWD/data/acceptance/live-2026-11-05-r3" \
  .venv/bin/pytest -q tests/test_live.py \
  --junitxml=data/acceptance/live-2026-11-05-r3/junit.xml
```

结果：**6 passed**。终端测试出现一次 Python `forkpty` 多线程警告，
实际子进程正常退出；后续独立运行的终端回归未出现该警告。

- 文本层及强制 OCR 两种方式分别核对学校、专业、学历、两段工作日期、
  四个项目、React/TypeScript/Node.js 等关键内容。
- OCR 平均置信度约 0.986；本轮可核对的已完成工作月份为 27，
  不把项目时间与教育年限叠加到工作经验。
- 真实图片、扫描 PDF 及文本/扫描混合 PDF 的页级路由通过。
- 对发往模型的简历提取 payload 检查电话、邮箱及证件号模式，
  未发现这些模式。此检查不构成对所有身份信息的完整脱敏保证。
- **岗位输入为明确标注的合成 JD，不是真实招聘库存。**
  真实模型匹配、可谈薪资/经验差距、异地排除、Redis 命中、
  提高目标薪资后的重匹配及三种格式导出通过。
- 实际 `create_agent` 调用改变条件，恢复 SQLite 会话和终端 PTY 对话通过。
- 真实 Redis 按会话删除通过，其他会话缓存与原始简历文件保持不变。

清理前证据目录曾含 `resume-checks.json`、`image-scan-checks.json`、
`synthetic-jd-checks.json`、`agent-restore-checks.json`、
`terminal-transcript.txt`、`deletion-checks.json` 和 JUnit。
这些文件及测试生成的简历衍生物均属于私有运行数据，不是公开样例。
清理后只保留 JUnit、最终页级路由及删除检查记录，删除包含职业内容的
解析/匹配结果、对话及重复输入；不再将已删除文件作为现存证据。

按任务固定岗位版本的修正之后，用最终代码补跑以下 4 项真实服务回归：
合成 JD 匹配、Agent 条件更新及恢复、真实终端、真实 Redis 清理。
结果 **4 passed**，证据为同目录的 `post-fix-junit.xml`；
该轮包含正常缓存复用，不宣称每次匹配都重新调用了模型。

## 真实网站探针

证据目录：

- `data/acceptance/boss-2026-11-05T17-52-08`：Chromium，`source_blank`。
- `data/acceptance/boss-2026-11-05T17-57-23`：普通 Chrome，`source_blank`。

目录中保留 `report.json`、最后页面截图及 `last-error.json`，
后者记录响应状态、跳转地址、页面标题和文字摘要。
没有使用 CloakBrowser、反检测、已有登录态或验证码绕过。

待完成：在页面可正常加载的目标环境，由用户以真实求职条件确认并手动登录，
验证实时列表、详情、限流/登录失效处理以及岗位匹配和导出。
不能仅凭当前探针，宣称实时采集和全部网站故障分支已经验收。

## 保留的失败

没有覆盖原始失败报告：

1. `data/acceptance/live-2026-11-05/junit.xml`：4 passed、1 failed。
   前一场景已将薪资改为 40K，后续场景却要求“提高到 30K”，
   模型选择追问而非改值。修正测试独立初始值为 25K，保留 30K 断言不变。
2. `data/acceptance/live-2026-11-05-r2/junit.xml`：4 passed、1 failed。
   发现不同运行目录中同名会话共享旧缓存，导致隔离环境未真正调用模型。
   已修复为包含绝对数据目录命名空间的 v2 缓存键，增加隔离回归，未放松真实调用断言。
3. 早期浏览器探针在沙箱内无法启动；提升授权后启动成功。
   最初把后续空白页误判为等待登录，检查截图后已修复为 `source_blank`。
   旧探针目录保留，不作为登录页成功显示的证据。

## 使用边界

首次执行需明确同意远程模型数据范围；首次搜索需确认摘要和条件。
本轮的城市、薪资及合成 JD 仅为验收输入，没有写成用户默认偏好。
姓名和其他身份信息的本地规则不是通用 PII 检测器；
复杂版式请先使用人工确认的脱敏简历副本。
同一 PDF 页内若只有少量文本覆盖大幅扫描图，文本质量启发式仍可能漏判，
可使用 `--force-ocr`；本轮混合 PDF 测试验证的是不同页的文本/扫描组合。

## 用户提供 Cookie 的登录验证（2026-11-05）

新增人类控制面命令 `ja auth import-cookies` 和 `ja auth check`，
不增加模型可调用的凭证工具。只接受用户明确提供的 BOSS Cookie 文件，
不扫描其他浏览器或项目。导入丢弃 localStorage，原文件保持不变；
登录态原子保存，权限为 `0600`，不发送给远程模型或 Redis。

实际导入 15 项 Cookie，过期跳过 0 项；导出文件的空 `sameSite` 字段
按浏览器默认的 `Lax` 处理。会话为 `boss-cookie-20261105`，
没有设置求职偏好、远程发送同意或搜索确认。

真实可见浏览器只访问 BOSS 首页，没有请求岗位列表、详情或发送消息：

- 首次探针返回 `browser_error`，诊断文件未成功写出；
  这是诊断容错缺陷，不能据此判断登录失效。
- 修正截图失败时仍写诊断、并单独分类加载超时后，再次探针返回
  `login_verified: false`、`issue: source_timeout`、`jobs_collected: 0`。
- 当时页面 URL 为 `https://www.zhipin.com/`，标题仍为加载北京首页，
  正文为空；未看到明确登录标识。截图未成功生成，不能声称有截图证据。
- 私有证据为会话内 `auth-check.json`、`browser-evidence/last-error.json`，
  这些路径全部位于忽略的 `data/` 内。没有导出或公开凭证值。

结论：**Cookie 已导入，但真实登录有效性与实时岗位链路未验证通过**。
页面超时不能证明 Cookie 无效，也不能冒充“没有岗位”。
未采取反检测、验证码绕过或不限次数重试。

最终回归：

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
JA_BROWSER_FIXTURES=1 .venv/bin/pytest -q tests/test_browser_contract.py
```

结果分别为 **100 passed, 11 skipped**、静态检查通过、**5 passed**。
11 项跳过为 6 项真实模型/OCR/Redis 测试与 5 项默认关闭的浏览器测试；
浏览器 5 项已单独实际运行。新增 Cookie 和超时测试使用合成凭证；
本轮未重跑真实模型/OCR/Redis，不将旧记录宣称为本轮新验收。

## 继续实时验收：网络诊断（2026-11-05 18:34）

用户要求继续 BOSS 实时搜索及真实岗位获取。本轮先检查已授权的
`boss-cookie-20261105`：没有简历、岗位、城市、目标月薪及搜索确认，
远程模型同意也未开启。未把旧测试条件写成用户偏好，已请求用户补充条件。
尚未执行岗位搜索，不能把连接检查当作实时搜索验收。

新增 `scripts/diagnose_boss.py`，仅复用该会话的独立浏览器上下文访问首页。
不打印凭证，不记录请求头、查询参数或响应正文。

首次诊断在 `page.evaluate` 读取 DOM 时持续等待，经显式中断后进程以
130 退出，没有生成报告。移除该无超时读取，增加 60 秒总时限及
超时/中断持久记录后，只补做一次真实网络诊断：

`data/acceptance/boss-diagnostic-2026-11-05T18-34-07/report.json`

- `www.zhipin.com`：12 个 HTTP 200 响应。
- `static.zhipin.com`：16 个 HTTP 200 响应。
- `img.bosszhipin.com`：53 个 HTTP 200 响应。
- `apm-fe.zhipin.com`：1 个 HTTP 200 响应。
- `/beijing/` 导航请求出现 1 次 `net::ERR_ABORTED`。
- 导航等待抛出 `TimeoutError`，报告为 `navigation_incomplete`；
  正文读取失败，截图也未生成，未看到明确已登录标识。
- `login_verified: false`、`jobs_collected: 0`。

这证明部分网络请求已成功返回，**不证明浏览器页面可用、Cookie 无效、
账号已登录或岗位列表已获取**。根因仍未确认，没有绕过安全验证、
修改浏览器反检测参数或反复无限重试。下一步需要用户明确求职条件，
并在页面能正常显示的环境完成登录与真实搜索。

最终自动回归：`.venv/bin/pytest -q` 为 **104 passed, 11 skipped**；
`.venv/bin/ruff check src tests scripts` 通过。新增 4 项测试覆盖诊断元数据
脱敏与错误/超时/中断报告，均为合成替身，不是真实岗位证据。
本轮没有重跑真实模型/OCR/Redis 或合成 Chromium 页面套件。

## 测试数据清理

按用户请求执行，仅限本项目：

- 保留 `tests/` 测试代码，合成岗位输入统一到
  `tests/fixtures/synthetic-jobs-NOT-LIVE.json`，真实服务测试复用同一文件。
  其他合成输入在 pytest 临时目录按需生成，不保留重复静态副本。
- 删除 `resume-text-check`、`resume-ocr-check`、`acceptance-text`、
  `acceptance-ocr`、`agent-build-probe` 五个临时会话及对应 Redis 缓存。
- 删除三轮 `live-*` 目录下的隔离运行库、重复 OCR 模型、输入副本、
  导出、简历图片/扫描 PDF/混合 PDF、终端转录及含职业内容的中间结果。
  同步清理这三套运行目录的 `real-services` 会话缓存。
- 保留失败 JUnit、最终真实服务 JUnit、最终页级 OCR 路由与删除检查、
  体积很小的 BOSS 失败报告及现有截图，避免丢失失败结论。
- 删除空的中断诊断目录、过时单元测试 JUnit、pytest/ruff 缓存、
  本项目源码/测试/脚本的 `__pycache__`。
- 保留运行所需的一份 OCR 模型、虚拟环境和锁文件；未删除 Docker 卷，
  未触及其他项目的文件或缓存。
- 保留正式 `boss-cookie-20261105` 会话、项目配置和原始简历；
  对配置、原始简历、BOSS 登录态和登录检查记录做了清理前后哈希校验，
  全部未变化。主库当前只剩这个正式会话。

清理测试数据共释放 **103,846,597 字节（约 99 MiB）**，不含后续删除的
字节码及工具缓存。明细为私有 `data/acceptance/cleanup-2026-11-05.json`。
清理后自动回归 **105 passed, 11 skipped**，ruff 通过；
本轮没有访问 BOSS、调用远程模型或重跑 OCR。
