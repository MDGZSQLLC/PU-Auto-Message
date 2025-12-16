import os
import requests
import json
import datetime
import time
import base64
import random
import re
from typing import Dict, List, Any, Optional, Set, Tuple

# ==============================================================================
# 1. 基础配置与鉴权 (Basic Config & Auth)
# ==============================================================================
# 用户 Token (需定期更新)
AUTHORIZATION = "Bearer er45616e5fb1eb6541865er1brg5vdv5d:4865165151515"

# HTTP 请求头
HEADERS = {
    "Authorization": AUTHORIZATION,
    "Accept": "application/json",
    "Content-Type": "application/json; charset=utf-8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
}

# 基础域名
_BASE_URL = "https://apis.pocketuni.net/apis"
# 活动相关
URL_ACTIVITY_LIST = f"{_BASE_URL}/activity/list"      # 获取全局活动列表
URL_ACTIVITY_INFO = f"{_BASE_URL}/activity/info"      # 获取活动详情
URL_MY_JOINED     = f"{_BASE_URL}/activity/myList"    # 获取我已报名的活动
# 社团/组织相关
URL_MY_TRIBE      = f"{_BASE_URL}/tribe/myList"       # 获取我加入的社团/组织
URL_TRIBE_EVENT   = f"{_BASE_URL}/tribe/eventList"    # 获取特定社团内部活动

# 允许的年级 ID (必需参数)
ALLOW_YEARS = [123456789101112]
# 目标学院 ID (非此学院的公共活动将被过滤)
TARGET_COLLEGE_ID = 123456789101112
# 标题过滤关键词 (包含这些词的活动直接忽略)
FILTER_KEYWORDS = []

LARGE_ACT_CAPACITY_LIMIT = 700   # 大型活动判定：人数上限
LARGE_ACT_DURATION_DAYS = 10     # 大型活动判定：持续天数
MAX_LARGE_DETAIL_COUNT = 3       # 大型活动：详细通知上限次数
LARGE_NOTIFY_BATCH = 80          # 大型活动：简略通知积攒人数阈值

# 数据存储路径 (指定绝对路径)
DATA_FILE = "./pu_monitor_cache.json"

# 【建议添加】自动确保目录存在，防止报错
_dir = os.path.dirname(DATA_FILE)
if not os.path.exists(_dir):
    try:
        os.makedirs(_dir)
        print(f"📁 自动创建日志目录: {_dir}")
    except Exception as e:
        print(f"❌ 创建目录失败: {e}")

# 消息推送接口 (发送 Base64 编码的 Markdown)
DIFF_LOG_URL = "http://127.0.0.1/message.php"

# ==============================================================================
# 5. 调度与时间策略 (Scheduling & Timing)
# ==============================================================================
# 全量数据刷新间隔 (秒) -> 30分钟
REFRESH_INTERVAL_SEC = 1800
# 紧急提醒时间窗口 (分钟) -> 活动开始前多少分钟内提醒
REMIND_WINDOW_MIN = 30
# 网络请求超时时间 (秒)
REQUEST_TIMEOUT = 8
# 网络请求最大重试次数
MAX_RETRIES = 2

# ==============================================================================
# 9. 数据清洗配置 (Data Cleaning Config)
# ==============================================================================

# 只保留这些字段 (根据你的要求定义的白名单)
REQUIRED_FIELDS = [
    "id",                   # 活动ID
    "name",                 # 活动主题
    "description",          # 活动介绍
    "joinStartTime",        # 报名开始
    "joinEndTime",          # 报名结束
    "allowUserCount",       # 报名人数上限
    "joinUserCount",        # 已报名人数
    "signInUserCount",      # 已签到人数
    "startTime",            # 活动开始
    "endTime",              # 活动结束
    "signStartTime",        # 签到时间
    "signOutStartTime",     # 签退时间
    "credit",               # 学分
    "tag",                  # 某些环境下是单个字符串
    "tags",                 # 大多是列表[{id,name}]
    "puAmount",             # PU银豆
    "allowTribe",           # 限定社团（列表）
    "attachName",           # 附件URL
    "attachTitle",          # 附件标题
    "status",               # 活动状态码
    "statusName",           # 活动状态
    "creatorName",          # 创建人/主办者
]

# 初始化全局 Session (复用 TCP 连接)
_session = requests.Session()
_session.headers.update(HEADERS)

def log(message):
    """简易日志输出"""
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{current_time}] {message}")
def safe_post_request(url: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    带重试机制的通用 POST 请求函数
    :param url: 请求地址
    :param payload: JSON 数据
    :return: 成功返回 JSON 字典，失败返回 None
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # 使用全局 session 发送请求
            response = _session.post(url, json=payload, timeout=REQUEST_TIMEOUT)

            # 200 OK
            if response.status_code == 200:
                return response.json()

            # 401/403 鉴权失败 (通常不需要重试，直接返回)
            elif response.status_code in [401, 403]:
                log(f"❌ 鉴权失败 ({response.status_code}): Token 可能已过期或无效")
                return None

            # 其他错误码 (500, 502 等)，进行重试
            else:
                log(f"⚠️ 请求异常 (Code: {response.status_code}) - {url} - 重试 {attempt}/{MAX_RETRIES}")

        except requests.exceptions.RequestException as e:
            # 捕获网络层面的异常 (超时、DNS 错误等)
            log(f"⚠️ 网络错误: {e} - 重试 {attempt}/{MAX_RETRIES}")

        # 指数退避策略：每次失败后随机等待 1~2 秒，避免请求过于频繁
        if attempt < MAX_RETRIES:
            time.sleep(random.uniform(1, 2))

    log(f"❌ 请求最终失败: {url}")
    return None
def send_messages(messages: List[str]):
    """
    发送消息逻辑：
    1. Check: 如果 DIFF_LOG_URL 为空 -> 直接在控制台打印 (本地模式)
    2. Post:  如果配置了 URL -> Base64编码并 POST 发送 (远程模式)
    """
    if not messages:
        return

    # ================= 分支 A: 本地打印模式 =================
    # 如果 URL 是空字符串、None 或未设置
    if not DIFF_LOG_URL:
        log(f"⚠️ 未配置推送地址 (DIFF_LOG_URL为空)，切换为控制台直接输出 ({len(messages)} 条):")

        for i, msg in enumerate(messages):
            print(msg)
            print("-" * 30)
        return

    # ================= 分支 B: 网络推送模式 =================
    log(f"📨 正在打包 {len(messages)} 条消息进行远程推送...")

    # 1. 合并消息 (添加分隔符，方便阅读)
    separator = "\n\n" + "-" * 30 + "\n\n"
    full_content = separator.join(messages)

    # 2. Base64 编码
    try:
        b64_data = base64.b64encode(full_content.encode('utf-8')).decode('utf-8')
    except Exception as e:
        log(f"❌ Base64 编码失败: {e}")
        return

    # 3. 构造并发送 POST 请求
    try:
        # 处理 URL: 去掉可能的查询参数 (如 ?msg=)，只保留脚本路径
        target_url = DIFF_LOG_URL.split("?")[0] if "?" in DIFF_LOG_URL else DIFF_LOG_URL

        # 发送请求 (设置5秒超时)
        response = requests.post(target_url, data={"msg": b64_data}, timeout=5)

        if response.status_code == 200:
            log("✅ 消息推送成功")
        else:
            log(f"⚠️ 推送失败，服务器返回: {response.status_code}")

    except requests.exceptions.RequestException as e:
        log(f"❌ 推送网络错误: {e}")
def clean_activity_descriptions(data_list: List[Dict]) -> List[Dict]:
    """
    清洗功能函数：
    遍历活动列表，去除 description 中的换行符(\n)、回车符(\r)、制表符(\t)等控制字符。
    将多行文本合并为单行，并去除首尾空白。
    """
    for item in data_list:
        desc = item.get("description")

        # 确保 description 存在且是字符串
        if desc and isinstance(desc, str):
            # 核心逻辑：
            # r'[\r\n\t]+' : 匹配一个或多个回车、换行、制表符
            # ' ' : 替换为空格 (避免 "Hello\nWorld" 变成 "HelloWorld" 导致粘连)
            # .strip() : 去除字符串两端的空格
            cleaned_desc = re.sub(r'[\r\n\t]+', ' ', desc).strip()

            # 更新回字典
            item["description"] = cleaned_desc

    return data_list
def filter_by_keywords(activity_list: List[Dict]) -> List[Dict]:
    """
    根据全局配置 FILTER_KEYWORDS 过滤活动标题
    如果标题包含任一关键词，则直接剔除
    """
    # 如果没有配置关键词，直接返回原列表，省去循环
    if not FILTER_KEYWORDS:
        return activity_list

    valid_list = []
    dropped_count = 0

    for item in activity_list:
        name = item.get("name", "")

        # 核心逻辑：检查 name 是否包含 FILTER_KEYWORDS 中的任意一个词
        # 只要命中一个，就视为包含
        if any(keyword in name for keyword in FILTER_KEYWORDS):
            dropped_count += 1
            # log(f"   🚫 屏蔽关键词活动: {name}") # 调试时可开启
            continue

        valid_list.append(item)

    if dropped_count > 0:
        log(f"   ✂️ [关键词过滤] 移除了 {dropped_count} 条标题包含屏蔽词的活动")

    return valid_list

def load_data() -> Dict[str, Any]:
    """
    读取数据文件
    结构: {
        "last_run_time": "yyyy-mm-dd HH:MM:SS",
        "tribe": { activity_id: { ...完整数据..., "_state": {...} } },
        "public": { activity_id: { ...完整数据..., "_state": {...} } }
    }
    """
    if not os.path.exists(DATA_FILE):
        return {
            "last_run_time": "未运行",
            "tribe": {},
            "public": {}
        }
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ 数据文件损坏，重置数据: {e}")
        return {"last_run_time": "未运行", "tribe": {}, "public": {}}
def save_data(data: Dict[str, Any]):
    """保存完整数据到硬盘"""
    # 更新最后运行时间
    data["last_run_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"❌ 保存数据失败: {e}")

def fetch_global_activity_list(limit: int = 25) -> List[Dict[str, Any]]:
    """
    获取全局活动列表（初始大池子）
    :param limit: 获取数量，默认为 25 条，尽量覆盖近期活动
    :return: 活动列表 list，如果获取失败返回空列表 []
    """
    # 构造 Payload
    # sort: 0 (通常是默认排序)
    # puType: 0 (普通活动)
    # allowYears: 年级限制
    payload = {
        "sort": 0,
        "page": 1,
        "limit": limit,
        "puType": 0,
        "allowYears": ALLOW_YEARS
    }

    log(f"📡 正在获取全局活动列表 (Limit: {limit})...")
    data = safe_post_request(URL_ACTIVITY_LIST, payload)

    if data and "data" in data and "list" in data["data"]:
        activity_list = data["data"]["list"]
        log(f"✅ 获取成功，原始数据共 {len(activity_list)} 条")
        return activity_list
    else:
        log("⚠️ 全局列表获取失败或数据为空")
        return []
def fetch_ended_activity_list(limit: int = 25) -> List[Dict[str, Any]]:
    """
    获取已结束的活动列表 (Status=3)
    用于后续做减法，剔除无效活动
    """
    payload = {
        "sort": 0,
        "page": 1,
        "limit": limit,
        "puType": 0,
        "status": 3,  # 关键参数：3 代表已结束
        "allowYears": ALLOW_YEARS
    }

    log(f"📡 正在获取已结束活动列表 (Limit: {limit})...")
    data = safe_post_request(URL_ACTIVITY_LIST, payload)

    if data and "data" in data and "list" in data["data"]:
        return data["data"]["list"]
    return []
def filter_effective_activities(all_activities: List[Dict[str, Any]],ended_activities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    集合减法：从全部活动中剔除已结束的活动
    :param all_activities: 全局活动列表 (大池子)
    :param ended_activities: 已结束活动列表 (黑名单)
    :return: 剩余的有效活动列表
    """
    # 1. 提取黑名单 ID 集合 (使用 set 查找速度更快)
    ended_ids = {item["id"] for item in ended_activities if "id" in item}

    effective_list = []

    # 2. 遍历大池子进行筛选
    for item in all_activities:
        act_id = item.get("id")
        name = item.get("name", "")

        # 排除 ID 在已结束列表中的
        if act_id in ended_ids:
            continue

        # (可选双重保障) 排除状态名直接显示为“已结束/已完结”的
        # 虽然通过ID减法已经处理了，但防止漏网之鱼
        status_name = item.get("statusName", "")
        if status_name in ["已结束", "已完结","完结待审核"]:
            continue

        # 排除 ID 为空的数据
        if not act_id:
            continue

        effective_list.append(item)

    log(f"📉 数据清洗: 原始 {len(all_activities)} 条 - 已结束 {len(ended_ids)} 条 = 有效 {len(effective_list)} 条")
    return effective_list
def fetch_my_tribes(limit: int = 5) -> List[Dict[str, Any]]:
    """
    获取我加入的社团/组织列表
    :param limit: 获取社团的数量上限
    """
    payload = {
        "page": 1,
        "limit": limit,
        "type": 2  # type=2 通常指“我加入的”
    }

    log(f"📡 正在获取我的社团列表...")
    data = safe_post_request(URL_MY_TRIBE, payload)

    if data and "data" in data and "list" in data["data"]:
        tribes = data["data"]["list"]
        log(f"✅ 获取到 {len(tribes)} 个社团/组织")
        return tribes
    return []
def fetch_valid_tribe_activities(tribe_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    遍历社团列表，获取每个社团的有效活动
    逻辑：请求活动 -> 剔除 '已结束'/'已完结' -> 汇总
    """
    valid_tribe_events = []

    # 定义无效状态集合
    INVALID_STATUS = ["已结束", "已完结","完结待审核"]

    for tribe in tribe_list:
        tid = tribe.get("id")
        tname = tribe.get("name", "未知社团")

        # 构造请求获取该社团的活动
        payload = {
            "tribeID": tid,
            "page": 1,
            "limit": 4  # 每个社团只看最近的5个活动
        }

        # log(f"🔍 正在检查社团: {tname} ...")
        # (注释掉以免日志太多，只在发现有效活动时输出)

        data = safe_post_request(URL_TRIBE_EVENT, payload)

        if data and "data" in data and "list" in data["data"]:
            events = data["data"]["list"]

            for event in events:
                status = event.get("statusName", "")

                # 核心过滤逻辑
                if status not in INVALID_STATUS:
                    # 给活动打上标记，方便后续知道这是社团活动
                    event["_source_type"] = "社团"
                    event["_source_name"] = tname

                    valid_tribe_events.append(event)
                    log(f"   🌟 发现社团有效活动: [{tname}] {event.get('name')}")

    log(f"✅ 社团活动扫描完成，共发现 {len(valid_tribe_events)} 个有效活动")
    return valid_tribe_events
def get_non_tribe_valid_activities(global_valid: List[Dict],tribe_valid: List[Dict]) -> List[Dict]:
    """
    逻辑分离：获取 [全局有效] 中除去 [社团有效] 之外的活动
    即：公共/其他类型的有效活动
    """
    # 1. 获取社团活动的 ID 集合
    tribe_ids = {item["id"] for item in tribe_valid if "id" in item}

    other_activities = []

    # 2. 遍历全局有效活动，如果不在社团ID集合中，则归为“其他”
    for item in global_valid:
        if item["id"] not in tribe_ids:
            other_activities.append(item)

    log(f"✂️ 分离完成: 社团活动 {len(tribe_ids)} 个，其他公共活动 {len(other_activities)} 个")
    return other_activities
def fetch_and_clean_data(activity_list: List[Dict], filter_tribe_limit: bool = True) -> List[Dict]:
    """
    核心清洗函数 (最终完整版)：
    1. 请求 '/activity/info' 获取详情。
    2. 安全解析 baseInfo，防御空数据。
    3. 【过滤】根据 filter_tribe_limit 决定是否过滤有社团限制的活动。
    4. 【过滤】过滤非本学院 (allowCollege) 的活动。
    5. 【过滤】过滤非本年级 (allowYears) 的活动。
    6. 【修复】强制回填 ID，防止详情接口缺少 ID 字段。

    :param activity_list: 待处理的活动列表
    :param filter_tribe_limit:
           - True (默认): 用于公共列表清洗。发现有社团限制则丢弃（视为别人的社团）。
           - False: 用于"我的社团"列表清洗。保留社团限制（视为我自己的社团）。
    """
    cleaned_data_list = []
    total = len(activity_list)

    # 统计计数器
    skipped_tribe = 0  # 因社团限制被踢
    skipped_college = 0  # 因学院限制被踢
    skipped_year = 0  # 因年级限制被踢

    log(f"🧹 开始清洗 {total} 个活动 (社团限制过滤: {'开启' if filter_tribe_limit else '关闭'})...")

    for index, item in enumerate(activity_list):
        # 优先使用列表中的 ID，这是最可靠的
        act_id = item.get("id")
        if not act_id: continue

        # 1. 请求详情
        resp = safe_post_request(URL_ACTIVITY_INFO, {"id": act_id})

        # 2. 空值防御：确保 resp 和 data 都不为空
        if not resp or not resp.get("data"):
            continue

        # 兼容部分接口直接返回 dict 或嵌套在 baseInfo 中
        raw_data = resp["data"]
        full_info = raw_data.get("baseInfo", raw_data)

        # 若 baseInfo 解析失败，跳过
        if not full_info:
            continue

        # =================== 过滤逻辑 A: 社团 (受 filter_tribe_limit 控制) ===================
        if filter_tribe_limit:
            allow_tribe = full_info.get("allowTribe")
            # 如果有社团限制，且列表不为空 -> 视为其他社团的内部活动 -> 丢弃
            if allow_tribe and isinstance(allow_tribe, list) and len(allow_tribe) > 0:
                skipped_tribe += 1
                continue

                # =================== 过滤逻辑 B: 学院 ===================
        allow_college = full_info.get("allowCollege")
        if allow_college and isinstance(allow_college, list) and len(allow_college) > 0:
            allowed_college_ids = [c.get('id') for c in allow_college if c.get('id')]
            # 如果有限制，且我的学院ID不在允许列表中 -> 丢弃
            if TARGET_COLLEGE_ID not in allowed_college_ids:
                skipped_college += 1
                continue

        # =================== 过滤逻辑 C: 年级 ===================
        allow_years_info = full_info.get("allowYears")
        if allow_years_info and isinstance(allow_years_info, list) and len(allow_years_info) > 0:
            allowed_year_ids = [y.get('id') for y in allow_years_info if y.get('id')]
            # 集合求交集：如果 (我的年级) 与 (允许年级) 无交集 -> 丢弃
            if not (set(ALLOW_YEARS) & set(allowed_year_ids)):
                skipped_year += 1
                continue

        # =================== 数据提取与 ID 修复 ===================
        clean_item = {}

        # 提取白名单字段
        for field in REQUIRED_FIELDS:
            clean_item[field] = full_info.get(field, None)

        # 【关键】强制覆盖 ID，防止详情接口返回 null
        clean_item["id"] = act_id

        # 补充来源标记 (如果原始列表中有)
        if "_source_type" in item:
            clean_item["_source_type"] = item["_source_type"]
        if "_source_name" in item:
            clean_item["_source_name"] = item["_source_name"]

        cleaned_data_list.append(clean_item)

        # 进度日志
        if (index + 1) % 5 == 0:
            log(f"   ...已处理 {index + 1}/{total} (当前有效: {len(cleaned_data_list)})")

    log(f"✨ 清洗报告: 输入{total} -> 社团剔除{skipped_tribe} -> 学院剔除{skipped_college} -> 年级剔除{skipped_year} -> 输出{len(cleaned_data_list)}")
    return cleaned_data_list


def fetch_target_activities_by_mode(enable_tribe: bool = False,enable_public: bool = False) -> Tuple[List[Dict], List[Dict]]:
    """
    按需调度中心：根据开关获取社团或公共活动
    优点：不执行的任务完全不发送网络请求，降低封号风险。

    :param enable_tribe: 是否执行社团活动获取
    :param enable_public: 是否执行公共活动获取
    :return: (final_tribe_data, final_public_data)
    """
    final_tribe_data = []
    final_public_data = []

    # ================= 任务分支 A: 社团活动 =================
    if enable_tribe:
        log("🚀 [任务启动] 开始获取“我的社团”活动...")

        # 1. 获取我的社团
        my_tribes = fetch_my_tribes(limit=10)

        # 2. 获取社团内部列表
        raw_tribe_activities = fetch_valid_tribe_activities(my_tribes)
        
        # 3. 关键词过滤 (在请求详情前执行，节省流量)
        raw_tribe_activities = filter_by_keywords(raw_tribe_activities)
        
        # 4. 深度清洗 (filter_tribe_limit=False, 保留社团限制)
        if raw_tribe_activities:
            final_tribe_data = fetch_and_clean_data(raw_tribe_activities, filter_tribe_limit=False)
            # 去除描述中的换行符
            final_tribe_data = clean_activity_descriptions(final_tribe_data)
        else:
            log("社团暂无有效活动")

    # ================= 任务分支 B: 公共活动 =================
    if enable_public:
        log("🚀 [任务启动] 开始获取“公共”活动...")

        # 1. 获取全局列表
        raw_global_list = fetch_global_activity_list(limit=30)

        # 2. 获取已结束列表 (用于去重)
        raw_ended_list = fetch_ended_activity_list(limit=30)

        # 3. 初步清洗 (剔除已结束)
        effective_global = filter_effective_activities(raw_global_list, raw_ended_list)
        
         # 4. 关键词过滤
        effective_global = filter_by_keywords(effective_global)
        
        # 5. 深度清洗 (filter_tribe_limit=True, 剔除有社团限制的活动)
        # 注意：这里不需要再做"集合减法"，因为 fetch_and_clean_data 内部会检查 allowTribe。
        # 如果一个活动在全局列表里，但它是社团专属，filter_tribe_limit=True 会把它过滤掉。
        if effective_global:
            final_public_data = fetch_and_clean_data(effective_global, filter_tribe_limit=True)
            # 去除描述中的换行符
            final_public_data = clean_activity_descriptions(final_public_data)
        else:
            log("全局暂无有效活动")

    # 汇总报告
    total_tribe = len(final_tribe_data)
    total_public = len(final_public_data)
    log(f"📊 本次获取结果: 社团 {total_tribe} 个 | 公共 {total_public} 个")

    return final_tribe_data, final_public_data

def _format_date_mmddhm(ts: Any) -> str:
    """
    [内部辅助] 统一格式化时间为 'MM-DD HH:mm'
    兼容:
    1. 时间戳 (1734220800 或 1734220800000)
    2. 字符串 ("2026-01-01 18:00:00")
    """
    if not ts: return "-"

    try:
        # 情况1: 如果是整数或浮点数，当做时间戳处理
        if isinstance(ts, (int, float)):
            val = int(ts)
            # 兼容13位毫秒级时间戳
            if val > 10000000000: val = val / 1000
            return datetime.datetime.fromtimestamp(val).strftime("%m-%d %H:%M")

        # 情况2: 如果是字符串 "2026-01-01 18:00:00"
        ts_str = str(ts).strip()
        # 简单高效的处理方式：如果是标准格式，直接截取字符串
        # 原始: "2026-01-01 18:00:00" -> 索引5到16 -> "01-01 18:00"
        if len(ts_str) >= 16 and "-" in ts_str and ":" in ts_str:
            return ts_str[5:16]

        return ts_str
    except:
        return str(ts)
def _get_days_diff(start_str: Any, end_str: Any) -> float:
    """计算两个时间字符串/时间戳相差的天数"""
    def _to_ts(t):
        if not t: return 0
        try:
            if isinstance(t, str) and "-" in t and ":" in t:
                return datetime.datetime.strptime(str(t), "%Y-%m-%d %H:%M:%S").timestamp()
            val = float(t)
            return val / 1000.0 if val > 10000000000 else val
        except:
            return 0
    return (_to_ts(end_str) - _to_ts(start_str)) / 86400.0
def _is_large_public_activity(activity: Dict[str, Any]) -> bool:
    """判断是否为【大型公共活动】"""
    try:
        capacity = int(activity.get("allowUserCount", 0))
    except:
        capacity = 0
    if capacity <= LARGE_ACT_CAPACITY_LIMIT:
        return False
    act_days = _get_days_diff(activity.get("startTime"), activity.get("endTime"))
    join_days = _get_days_diff(activity.get("joinStartTime"), activity.get("joinEndTime"))
    return (act_days > LARGE_ACT_DURATION_DAYS) or (join_days > LARGE_ACT_DURATION_DAYS)

def format_activity_markdown(a: Dict[str, Any], show_detail: bool = True) -> str:
    """
    构建 Markdown 格式的活动信息
    :param a: 单个活动数据字典
    :param show_detail: True=显示完整详情, False=显示简略卡片
    """

    # --- 1. 基础信息构建 ---
    name = a.get("name", "无标题")
    source = f"【{a.get('_source_type', '活动')}】" if a.get('_source_type') else ""
    title_line = f"### {source}{name}"

    # 报名人数信息
    join_info = f"上限 {a.get('allowUserCount', '-')} | 已报名 {a.get('joinUserCount', '-')} | 已签到 {a.get('signInUserCount', '-')}"

    # 学分/银豆信息
    credit_info = f"{a.get('credit', '-')} / {a.get('puAmount', '-')}"

    # --- 2. 简略模式 ---
    if not show_detail:
        desc_raw = a.get('description') or ""
        short_desc = desc_raw[:10] + "......" if desc_raw else "无介绍......"

        return (
            f"{title_line}\n"
            f"> {short_desc}\n"
            f"*报名人数：* {join_info}\n"
            f"*学分 / PU银豆：* {credit_info}"
        )

    # --- 3. 详细模式 ---

    # 主办方逻辑
    tribes = a.get("allowTribe") or []
    tribe_names = [t.get("name", "") for t in tribes if isinstance(t, dict)]
    tribe_str = ", ".join([x for x in tribe_names if x]).strip()

    tags = a.get("tags") or []
    if isinstance(tags, list):
        tag_names = [t.get("name", "") for t in tags if isinstance(t, dict)]
    else:
        tag_names = [str(tags)] if tags else []
    tag_str = ", ".join([x for x in tag_names if x]).strip()

    creator = a.get("creatorName") or ""

    if tribe_str:
        org_info = f"{tribe_str} / {creator}" if creator else tribe_str
    elif tag_str:
        org_info = f"{tag_str} / {creator}" if creator else tag_str
    else:
        org_info = creator or "-"

    # 附件处理
    attach_title = a.get("attachTitle")
    attach_name = a.get("attachName")
    attach_line = ""
    if attach_title or attach_name:
        title = attach_title or "附件下载"
        url = str(attach_name) if attach_name else ""
        attach_line = f"\n*附件：* [{title}]({url})" if url else f"\n*附件：* {title}"

    # --- 4. 详细输出 (严格按照指定格式) ---
    detailed_md = (
        f"{title_line}\n"
        f"{a.get('description', '无详细介绍')}\n\n"
        f"*报名时间：* {_format_date_mmddhm(a.get('joinStartTime'))} ~ {_format_date_mmddhm(a.get('joinEndTime'))}\n"
        f"*报名人数：* {join_info}\n"
        f"*活动时间：* {_format_date_mmddhm(a.get('startTime'))} ~ {_format_date_mmddhm(a.get('endTime'))}\n"
        f"*状态：* {a.get('statusName', '-')}\n"
        f"*主办/所属：* {org_info}\n"
        f"*学分 / PU银豆：* {credit_info}"
        f"{attach_line}"
    )

    return detailed_md

def process_tribe_activities(new_tribe_list: List[Dict],old_tribe_data: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    """
    社团活动核心处理器
    逻辑：我的社团活动非常重要，不做限流，不做简略。
    只要有变动，全部详细通知。
    """
    messages = []
    updated_tribe_group = {}

    for act in new_tribe_list:
        act_id = str(act.get("id"))
        current_joined = int(act.get("joinUserCount", 0))

        # --- 读取旧状态 ---
        old_record = old_tribe_data.get(act_id, {})
        old_state = old_record.get("_state", {})
        last_joined = old_state.get("last_joined", 0)

        # 判断是否为新活动 (不在旧缓存中)
        is_new = act_id not in old_tribe_data

        # 计算增量
        delta = current_joined - last_joined

        should_notify = False
        header = ""

        # --- 决策逻辑 ---
        if is_new:
            # 全新社团活动
            should_notify = True
            header = f"🆕 **发现我的社团新活动**"

        elif delta > 0:
            # 人数增加
            should_notify = True
            header = f"📈 **社团活动动态 (新增 +{delta}人)**"

        # --- 生成消息 (强制详细模式) ---
        if should_notify:
            md = format_activity_markdown(act, show_detail=True)
            messages.append(f"{header}\n{md}")

        # --- 注入状态并保存 ---
        # 社团活动状态很简单，只需要记录上次人数和时间
        act["_state"] = {
            "last_joined": current_joined,
            "update_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        updated_tribe_group[act_id] = act

    return messages, updated_tribe_group
def process_public_activities(new_public_list: List[Dict],old_public_data: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    """
    公共活动核心处理器 (最终版)

    参数:
    - new_public_list: 从 API 获取的最新公共活动列表
    - old_public_data: 从本地缓存读取的旧公共活动数据

    返回:
    - (messages, updated_public_data): 待发送消息列表, 更新后的完整数据
    """
    messages = []
    updated_public_group = {}

    for act in new_public_list:
        act_id = str(act.get("id"))
        current_joined = int(act.get("joinUserCount", 0))

        # --- 读取旧状态 (Old Data) ---
        old_record = old_public_data.get(act_id, {})
        old_state = old_record.get("_state", {})

        last_joined = old_state.get("last_joined", 0)
        detail_count = old_state.get("detail_count", 0)  # 已发详细次数
        acc_increase = old_state.get("acc_increase", 0)  # 积攒人数

        # 计算增量 (如果是新活动，last_joined为0，delta即为当前总人数)
        delta = current_joined - last_joined

        # --- 判断活动类型 ---
        is_large = _is_large_public_activity(act)

        # --- 准备新的状态变量 (默认继承旧值) ---
        new_detail_count = detail_count
        new_acc_increase = acc_increase

        # --- 决策变量 ---
        should_notify = False  # 是否发送
        show_detail = True  # True=详细, False=简略
        notify_num = delta  # 消息中显示的新增人数

        # 只有人数增加(或新活动)才处理
        if delta > 0:
            if not is_large:
                # [A] 普通公共活动 -> 总是详细通知，不限流
                should_notify = True
                show_detail = True
                notify_num = delta
                new_acc_increase = 0  # 确保清理积攒

            else:
                # [B] 大型公共活动 -> 状态机限流逻辑
                # 将本次增量加入积攒池
                current_acc = acc_increase + delta

                if detail_count < MAX_LARGE_DETAIL_COUNT:
                    # [阶段1: 详细通知期] 名额(3次)没用完 -> 详细通知
                    should_notify = True
                    show_detail = True
                    notify_num = current_acc

                    new_detail_count += 1  # 消耗1次详细机会
                    new_acc_increase = 0  # 清空积攒

                else:
                    # [阶段2: 简略通知期] 名额用完了 -> 积攒够80才简略通知
                    if current_acc >= LARGE_NOTIFY_BATCH:
                        should_notify = True
                        show_detail = False  # 简略模式
                        notify_num = current_acc

                        new_acc_increase = 0  # 清空积攒
                    else:
                        # 没攒够 -> 静默，只更新积攒数
                        should_notify = False
                        new_acc_increase = current_acc

        # --- 生成消息 ---
        if should_notify:
            # 统一的消息头
            header = f"🔥 **火热报名中 (新增 +{notify_num}人)**"

            # 调用 Markdown 生成函数 (根据 show_detail 决定繁简)
            md = format_activity_markdown(act, show_detail=show_detail)
            messages.append(f"{header}\n{md}")

        # --- 注入状态并保存 (构建 updated_public_data) ---
        act["_state"] = {
            "last_joined": current_joined,
            "detail_count": new_detail_count,
            "acc_increase": new_acc_increase,
            "is_large": is_large,
            "update_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        updated_public_group[act_id] = act

    return messages, updated_public_group

def check_run_conditions(cache_data: Dict[str, Any]) -> Tuple[bool, bool]:
    """
    调度检查器

    功能：
    1. 【硬性门槛】检查当前时间是否在 07:30 ~ 22:00 之间。
    2. 【社团频率】检查距离上次社团刷新是否超过 20 分钟。
    3. 【公共频率】检查距离上次公共刷新是否超过 30 分钟。

    返回: (run_tribe, run_public)
    """
    now = datetime.datetime.now()
    current_time = now.time()

    # === 1. 全局时间窗口检查 (07:30 ~ 22:00) ===
    start_time = datetime.time(7, 30)
    end_time = datetime.time(22, 0)

    if not (start_time <= current_time <= end_time):
        log(f"💤 当前时间 {current_time.strftime('%H:%M')} 不在运行窗口 (07:30-22:00)，脚本休眠。")
        return False, False

    # === 2. 获取上次运行时间 ===
    # 默认值为很久以前，确保第一次运行能通过检查
    default_past = "2000-01-01 00:00:00"

    last_tribe_str = cache_data.get("tribe_last_run", default_past)
    last_public_str = cache_data.get("public_last_run", default_past)

    # 辅助：字符串转datetime
    def str_to_dt(s):
        try:
            return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except:
            return datetime.datetime.strptime(default_past, "%Y-%m-%d %H:%M:%S")

    last_tribe_dt = str_to_dt(last_tribe_str)
    last_public_dt = str_to_dt(last_public_str)

    # === 3. 计算时间差 (分钟) ===
    # total_seconds() / 60
    tribe_diff_min = (now - last_tribe_dt).total_seconds() / 60
    public_diff_min = (now - last_public_dt).total_seconds() / 60

    # === 4. 判定是否执行 ===
    # 阈值：社团 20分钟，公共 30分钟
    run_tribe = tribe_diff_min >= 20
    run_public = public_diff_min >= 30

    # 日志输出当前状态
    log(f"⏱️ 调度检查: 社团间隔 {int(tribe_diff_min)}分 (阈值20) -> {'执行' if run_tribe else '跳过'} | "
        f"公共间隔 {int(public_diff_min)}分 (阈值30) -> {'执行' if run_public else '跳过'}")

    return run_tribe, run_public

if __name__ == "__main__":
    try:
        # ---------------- Step 1: 读取本地缓存 ----------------
        full_cache_data = load_data()

        old_tribe_data = full_cache_data.get("tribe", {})
        old_public_data = full_cache_data.get("public", {})

        # ---------------- Step 2: 调度检查 (决定跑什么) ----------------
        do_run_tribe, do_run_public = check_run_conditions(full_cache_data)

        # 如果全都不需要跑，直接退出，极致省流
        if not do_run_tribe and not do_run_public:
            print("💤 所有任务均未达到执行间隔，脚本结束。")
            exit(0)

        # ---------------- Step 3: 按需请求数据 ----------------
        # 只请求需要执行的部分，减少封号风险
        new_tribe_acts, new_public_acts = fetch_target_activities_by_mode(
            enable_tribe=do_run_tribe,
            enable_public=do_run_public
        )

        # 准备收集的消息列表 (这是要发给客户端的干货)
        all_messages = []

        # 准备用于保存的数据 (默认为旧数据)
        final_tribe_data = old_tribe_data
        final_public_data = old_public_data

        # 获取当前时间
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # ---------------- Step 4: 执行业务逻辑 ----------------

        # === A. 处理社团活动 ===
        if do_run_tribe:
            print(f"\n⚡ 分析社团数据变动...")
            # 这里的 process 函数只会返回 mkdown 数据，不含 log
            t_msgs, final_tribe_data = process_tribe_activities(new_tribe_acts, old_tribe_data)
            all_messages.extend(t_msgs)

            # 更新运行时间
            full_cache_data["tribe_last_run"] = now_str

        # === B. 处理公共活动 ===
        if do_run_public:
            print(f"\n⚡ 分析公共数据变动...")
            p_msgs, final_public_data = process_public_activities(new_public_acts, old_public_data)
            all_messages.extend(p_msgs)

            # 更新运行时间
            full_cache_data["public_last_run"] = now_str

        # ---------------- Step 5: 保存数据 ----------------
        # 先保存状态，防止发送消息出错导致数据回滚
        data_to_save = {
            "tribe_last_run": full_cache_data.get("tribe_last_run", ""),
            "public_last_run": full_cache_data.get("public_last_run", ""),
            "tribe": final_tribe_data,
            "public": final_public_data
        }

        save_data(data_to_save)
        print("\n✅ 数据状态已保存")

        # ---------------- Step 6: 批量发送消息 ----------------
        # 只有当有实际变动消息时，才调用发送接口
        if all_messages:
            # 调用刚才写好的 POST 发送函数
            send_messages(all_messages)

            # (本地调试用，可以看到发了什么，实际运行在服务器上看log即可)
            print("-" * 30)
            print(f"共推送 {len(all_messages)} 条内容")
        else:
            print("\n💤 本次执行无重要变动，不发送推送")

    except Exception as e:
        import traceback

        print(f"❌ 脚本运行发生异常: {e}")
        traceback.print_exc()