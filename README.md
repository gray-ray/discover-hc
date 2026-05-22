# 上市公司招聘洞察

这个工具按行业板块做三段式抓取：

1. 获取行业板块和成分股。
2. 提取公司官网等基础信息。
3. 访问官网，优先抓取包含 `招聘 / 人才 / 加入我们 / career / jobs` 等关键词的页面，并输出招聘相关信息。
4. 默认过滤掉非社会招聘内容，例如校园招聘、宣讲会、人才活动、新闻公告等噪音页面。

## 命令行运行

```bash
python eastmoney_recruitment.py 白酒
```

默认会把结果写入当前目录下的 `output.json`。
运行过程中会默认输出进度提示，方便判断当前是在获取行业、查询公司资料还是抓取社会招聘页面。

常用参数：

```bash
python eastmoney_recruitment.py 白酒 \
  --company-limit 20 \
  --page-limit 20 \
  --result-limit 5
```

如果你想查看 SSL 兼容回退提示，可以额外加上：

```bash
python eastmoney_recruitment.py 白酒 --show-ssl-warning
```

如果你不想看运行进度，可以加上：

```bash
python eastmoney_recruitment.py 白酒 --quiet
```

## HTML 控制台

启动本地页面控制台：

```bash
python recruitment_console.py
```

或者：

```bash
python eastmoney_recruitment.py serve
```

默认地址：

```text
http://127.0.0.1:8765/discover.html
```

页面顶层现在只有 2 个菜单：

- `/discover.html`
  公司筛选：只拉全行业上市公司清单，输出公司基础信息和官网。
- `/discover.html#crawl`
  招聘获取：支持正常抓取、继续执行、重试失败三种方式。

页面功能包括：

- 每个菜单页顶部都有独立的查询配置区
- 触发任务
- 通过 SSE 查看实时过程日志
- 查看本地 discover / crawl 文件
- 预览任务结果列表
- 支持文件多选、全选和批量删除
- 通过前端二次确认按钮关闭整个本地服务并释放端口
- 通过“使用说明”按钮弹窗查看菜单用途和文件说明

## 输出内容

`output.json` 结构示例：

```json
[
  {
    "company": {
      "stock_code": "600519",
      "stock_name": "贵州茅台",
      "company_name": "贵州茅台酒股份有限公司",
      "industry": "白酒Ⅲ",
      "website": "https://www.moutaichina.com/"
    },
    "recruitment_info": [
      {
        "publish_date": "2024-10-01",
        "position": "招商主管",
        "job_description": "负责渠道拓展与客户维护……",
        "work_location": "贵州遵义",
        "source_title": "社会招聘",
        "source_url": "https://example.com/jobs/1"
      }
    ],
    "error": null
  }
]
```

## 代码结构

当前实现已经从单文件拆分为多模块：

- `eastmoney_recruitment.py`
  保留原命令入口，作为薄封装调用。
- `eastmoney_recruitment_lib/config.py`
  关键词、正则、常量配置。
- `eastmoney_recruitment_lib/models.py`
  数据模型和异常定义。
- `eastmoney_recruitment_lib/runtime.py`
  运行时开关和进度输出。
- `eastmoney_recruitment_lib/helpers.py`
  文本、URL、域名等通用工具函数。
- `eastmoney_recruitment_lib/html_parser.py`
  HTML 链接和正文提取。
- `eastmoney_recruitment_lib/http_client.py`
  HTTP 请求、编码识别、SSL 回退。
- `eastmoney_recruitment_lib/pipeline.py`
  行业与公司清单获取、官网抓取、招聘提取主流程。
- `eastmoney_recruitment_lib/cli.py`
  命令行参数解析和程序主入口。
- `eastmoney_recruitment_lib/service.py`
  discover / crawl / resume / refresh-failed 任务服务、持久化和断点续跑。
- `eastmoney_recruitment_lib/web_server.py`
  本地 HTTP API、SSE 事件流和 HTML 控制台服务。
- `web/discover.html`
  主控制台页面，顶部 2 个菜单切换。
- `web/assets/styles.css`
  共享 Bootstrap 补充样式。
- `web/assets/app.js`
  共享前端逻辑、SSE、任务触发、文件管理。

## 说明

- 脚本只依赖 Python 标准库，不需要额外安装第三方包。
- 默认会在 SSL 证书校验失败时静默回退到兼容模式，不把警告混入结果输出；只有传入 `--show-ssl-warning` 才会显示。
- 默认总是写入 `output.json`，也可以用 `--json-out` 覆盖输出路径。
- 默认会输出运行进度；如果只想保留最终结果，可以使用 `--quiet`。
- HTML 控制台会自动管理本地任务状态和结果文件。
- HTML 控制台的任务列表、日志和文件列表现在通过 SSE 实时推送更新，不再依赖定时轮询。
- 官网招聘页抓取采用轻量站内爬取策略。部分官网如果完全依赖前端脚本渲染，或者招聘入口藏在外部招聘平台里，命中率会下降。
- 现在会额外识别菜单点击后新开标签页的招聘入口，例如 `onclick/window.open/data-url`，也会跟进 `mokahr`、`51job`、`zhaopin`、`boss` 等常见招聘外链。
- 当前结果默认只保留更接近社会招聘的信息，会尽量过滤校招、新闻、人才活动等内容。
- 如果某一家公司的资料接口或官网返回异常，脚本会把错误记录到该公司的 `error` 字段，并继续处理后续公司，不会整批退出。
- 如果官网页面信息不完整，`岗位`、`岗位描述`、`工作地址` 等字段可能为空。
- 如果资料源没有提供 `ORG_WEB` 官网字段，脚本会直接标记该公司未提供官网地址。
