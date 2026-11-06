"""Synthetic layout stress cases; contains no candidate or live recruitment data."""
import argparse
import json
from pathlib import Path

from javis_tools.documents import render_resume


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    text = (
        "合成版式测试项目：使用 Python、React、LangChain 构建检索结果界面，"
        "负责 API、错误提示、分页及来源引用，核对请求日志并记录未验证范围。"
        "业务收益未知，不编造性能数字。"
    )
    cases = {}
    for name, count in (("short", 3), ("long", 30)):
        cases[name] = render_resume({
            "id": "synthetic-layout-" + name,
            "sections": [
                {"heading": "项目经历",
                 "lines": [{"text": f"第 {i + 1} 个版式测试条目。{text}"} for i in range(count)]},
                {"heading": "专业技能", "lines": [{
                    "text": "Python / TypeScript / React / LangChain / SQL / Git / HTTP",
                }]},
            ],
        }, {
            "name": "合成版式验收样本 非真实简历",
            "email": "synthetic.layout@example.invalid", "city": "测试城市",
            "link": "https://example.invalid/a-very-long-public-portfolio-address-for-layout-testing",
        }, args.output / name, preview=True)
    print(json.dumps(cases, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
