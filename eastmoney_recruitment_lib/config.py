"""配置常量。"""

from __future__ import annotations

import re


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

STRONG_RECRUITMENT_KEYWORDS = (
    "招聘",
    "招贤",
    "招贤纳士",
    "招募",
    "社会招聘",
    "招聘信息",
    "人才招聘",
    "热点招聘",
    "加入我们",
    "诚聘",
    "岗位",
    "职位",
    "社招",
    "careers",
    "career",
    "jobs",
    "job",
    "join us",
    "join-us",
    "talent",
    "hiring",
    "recruit",
)

WEAK_RECRUITMENT_KEYWORDS = (
    "加入",
    "人力",
    "人才",
    "人力资源",
    "岗位",
)

SOCIAL_RECRUITMENT_POSITIVE_KEYWORDS = (
    "社会招聘",
    "社招",
    "诚聘",
    "招贤纳士",
    "招聘信息",
    "人才招聘",
    "热点招聘",
    "招聘岗位",
    "招聘职位",
    "招聘人数",
    "工作职责",
    "岗位职责",
    "岗位要求",
    "任职要求",
    "职位描述",
    "岗位描述",
    "工作地点",
    "工作地址",
    "加入",
    "加入我们",
    "人力",
    "人力资源",
    "岗位",
    "社会人才",
    "社会精英",
    "career",
    "careers",
    "job",
    "jobs",
    "social recruitment",
    "experienced hire",
)

SOCIAL_RECRUITMENT_NEGATIVE_KEYWORDS = (
    "校园招聘",
    "校招",
    "应届生",
    "毕业生",
    "宣讲会",
    "双选会",
    "实习",
    "管培生",
    "储备生",
    "人才发展大会",
    "人才大会",
    "人才政策",
    "人才战略",
    "人力资源管理",
    "新闻中心",
    "公司新闻",
    "新闻动态",
    "媒体报道",
    "公告",
    "公示",
    "党建",
)

FOLLOW_HINT_KEYWORDS = STRONG_RECRUITMENT_KEYWORDS + WEAK_RECRUITMENT_KEYWORDS + (
    "招聘信息",
    "人才招聘",
    "招贤纳士",
    "热点招聘",
    "招聘岗位",
    "招聘职位",
    "招聘人数",
    "工作职责",
    "岗位要求",
    "关于我们",
    "联系我们",
    "公司新闻",
    "新闻中心",
    "公告",
    "contact",
    "about",
    "news",
    "notice",
    "hr",
)

EXTERNAL_RECRUITMENT_HOST_KEYWORDS = (
    "mokahr.com",
    "moka.cn",
    "zhaopin.com",
    "51job.com",
    "liepin.com",
    "lagou.com",
    "bosszhipin.com",
    "zhipin.com",
    "kanzhun.com",
    "jobui.com",
)

BINARY_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".pdf",
    ".zip",
    ".rar",
    ".7z",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".mp4",
    ".mp3",
    ".avi",
    ".wmv",
    ".apk",
    ".exe",
}

DATE_PATTERNS = (
    re.compile(r"(20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)"),
    re.compile(r"(20\d{2}\d{2}\d{2})"),
)

POSITION_PATTERNS = (
    re.compile(r"(?:招聘岗位|招聘职位|岗位名称|职位名称|招聘[:：]?)\s*([^\s|,，;；:：]{2,40})"),
    re.compile(r"(?:诚聘|招募|招聘)\s*([^\s|,，;；:：]{2,40})"),
    re.compile(r"(?:岗位|职位)[:：]\s*([^\n|,，;；]{2,40})"),
)

LOCATION_PATTERNS = (
    re.compile(r"(?:工作地点|工作地址|工作城市|工作地区|上班地点|办公地点|location)[:：]?\s*([^\n|;；]{2,60})", re.IGNORECASE),
    re.compile(r"(?:base in|located in)\s*([A-Za-z\u4e00-\u9fff\s,-]{2,60})", re.IGNORECASE),
)

DESCRIPTION_PATTERNS = (
    re.compile(r"(?:岗位描述|职位描述|工作内容|岗位职责|职责描述)[:：]?\s*(.{20,240})"),
    re.compile(r"(?:任职要求|岗位要求)[:：]?\s*(.{20,240})"),
)
