# Rental Team

只读租房决策团队，唯一房源来源是唯果与自如，按唯果优先顺序串行查询。
没有联系房东、预约、二维码、支付、签约、第三平台搜索或后台任务。
当前真实来源验收和未完成项见 [实施记录](../../docs/Javis实施与验收.md)。

## 安装

```bash
cd Brain-Leader
uv sync --extra test --locked
uv run javis doctor
uv run javis chat
```

配置不要求远程模型即可执行结构化终端命令。
凭据从 `RENTAL_VIGO_API_KEY`、`RENTAL_AMAP_API_KEY` 或本项目忽略提交的
`credentials.json` 读取，显式配置优先。使用以下命令隐藏输入，权限 `0600`，
原子更新指定服务的 Key，保留其他服务凭据：

```bash
uv run python -m rental_team.configure --provider vigo
uv run python -m rental_team.configure --provider amap
```

不要把 Key 粘贴到命令参数、测试样例或问题文本。`doctor` 不请求网络，不消耗配额。

自如独立环境，固定第三方提交与已验证依赖组合，不复制其业务实现：

```bash
uv venv ../agents/Rental-Team/.venv-ziroom --python .venv/bin/python
uv pip install --python ../agents/Rental-Team/.venv-ziroom/bin/python \
  -r ../agents/Rental-Team/ziroom-requirements.txt
```

Windows 将 `bin/python` 替换为 `Scripts/python.exe`。
macOS 指定 `RENTAL_CHROME_PATH`，默认 Chrome 应用路径。
服务通过标准 MCP stdio 独立启动；域名请求、重定向和子资源均经过项目包装层，
仅允许 HTTPS `ziroom.com` 及其子域，独立新浏览器上下文，不读取个人用户目录或 Cookie。
未持久化登录身份；需要登录/验证码/限流时停止该来源，不绕过限制。
上游仓库在固定提交未发现许可证文件，未将其源码再分发进 Javis；
独立安装不代表已获再分发授权。

## 确认与搜索

以下仅为接口格式示例，不是用户的实际租房条件：

```text
/rental requirements {"city":"北京","rent_max":5000,"budget_kind":"rent","rent_type":"entire","move_in":"2026-12-01"}
/rental confirm 1
/rental search
/rental results
```

`rent_type` 为 `entire/shared`；`budget_kind` 为仅租金 `rent` 或已知固定月支出
`known_monthly`。可加 `rent_min/months/location/bedrooms/hard_conditions`。
`sources` 默认双来源；只有明确指定 `["vigolive"]` 或 `["ziroom"]` 才缩为单来源。
条件变更产生新版本，旧确认、位置授权和当前结果失效，必须重新确认。
未知入住时间、宠物条件等会列入待核验，不能仅因平台筛选通过就声称全部满足。

自然语言仅在 `/javis consent` 或 `--allow-remote` 后发送给配置模型，
提取待确认草稿；模型不能执行确认、跨域共享或地图授权。

## 通勤与成本

目的地不是必填。按需设置 `destination/max_commute_minutes/commute_mode`，
精确路线还需要用户确认的 `destination_gcj02`、房源位置以及单独
`/rental authorize-location 版本` 和项目专用高德 Key（本地凭据或 `RENTAL_AMAP_API_KEY`）。
缺少任一前提显示 `not_queried`，不编造分钟数。
唯果坐标未有坐标系证据，不自动当 GCJ-02 使用。
位置消歧不自动选择地点。`/rental geocode "地址"` 返回候选，由用户把确认的目的地
GCJ-02 坐标加入新需求版本并重新确认、授权。
`/rental route vigolive 房源ID 经度,纬度` 使用用户本次明确确认的房源坐标，
无需重复消耗房源搜索额度；路线独立成新任务，不改写原房源快照。
骑行路由尚未接入。

程序分开计算月度固定支出、一次性不可退费用、可退押金和首次现金。
缺失费用保留 `null`，结果称“已知部分成本”；水电使用费不伪造定额。
多个自如押付方案没有用户选择前不合成一个价格。

## 额度与数据

唯果默认每任务最多 5 次业务调用、每来源最多 10 条列表和 3 条详情。
每日固定 SQLite 台账上限 30，进程重启、切换会话和子任务不会重置。
握手/通知/工具发现额外保守预留 3 次本地额度，失败请求也不退回。
实际平台更低额度优先；收到配额耗尽将本地当天余量封顶。
外部客户端已消耗的额度无法本地获知，不宣称本地计数就是平台余额。
来源串行调用至少间隔 3 秒，不自动重试业务请求。

租房状态在本项目 `data/`，主控任务在 `BL_DATA_DIR/javis`，
不读取职业简历、薪资、聊天和记忆。结果字段白名单保存来源、房源 ID、采集时间和证据；
历史结果显示 `historical`，不保证仍可出租。启动时清理超过 7 天的有时间戳任务与快照，
位置授权过期时移除精确目的地坐标并使确认失效；没有后台服务。
可通过 `RENTAL_RETENTION_DAYS` 调整为 1–90 天。
使用 `javis sessions delete` 显式删除对应域数据，配额台账不随会话删除。
私有目录权限不等于加密；外部备份和命令输出副本不在删除范围。

## 验证

```bash
cd Brain-Leader
uv run pytest -q -m 'not live' -p no:cacheprovider
# 明确 opt-in，真实模型及最多一次双来源搜索，有实际调用费用：
uv run python -B scripts/javis_acceptance.py data/acceptance/全新目录 \
  --live-model --live-sources
# 高德独立真实验证，仅公共地点，最多两次地图请求，不访问唯果/自如：
uv run python -B scripts/amap_acceptance.py data/acceptance/全新地图目录 --live-map
```

普通测试是离线契约测试，不能替代真实来源验收。
上述 live 脚本不以模拟房源或放宽断言通过，自如/唯果详情缺失会记录失败。
