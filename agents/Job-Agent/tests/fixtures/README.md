# 测试样例

`synthetic-jobs-NOT-LIVE.json` 是唯一保留的静态岗位导入样例，
覆盖目标城市的可谈薪资/经验差距，以及异地岗位的硬条件排除。
数据完全合成，不代表真实公司、岗位、来源链接或招聘库存。
真实服务测试也复用该文件，避免保存多份相同输入。

可在独立测试会话中导入，不访问 BOSS，不调用模型：

```bash
uv run ja jobs import tests/fixtures/synthetic-jobs-NOT-LIVE.json --session example-import
uv run ja sessions delete example-import
```

简历文本、空白/损坏文件、浏览器 HTML、Cookie 和提示词注入等样例，
由各测试在 pytest 临时目录中生成，不需要保留运行副本。
真实简历与登录凭证不是公开测试样例，不放在此目录。
