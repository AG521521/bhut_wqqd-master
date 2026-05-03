# -*- coding: utf-8 -*-
# @Author：Spance
# @Email: wqqd@spance.xyz
# @Version：v1.8
# @Desc:大学考勤系统自动签到


import base64
import json
from datetime import datetime, timezone, timedelta
import hashlib
import logging
import random
import sys
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import aiohttp
import asyncio


"""
                更新日志
2026年3月26日 14:32:38：
  修复了学校于2026年3月24日更新导致无法签到的错误，目前项目已可以正常签到
  但为保证签到接口不因为频繁操作导致失败，上调了签到前的等待时间，并加锁限制
  若您为多名用户进行签到，请自行调整重试次数及等待时间
  
2026年3月17日 21:39:52:
  修复了没有在程序结束之前关闭各用户的session，若持久化运行会导致内存泄漏的问题

2026年3月14日 21:57:27:
  添加了异步并发限制，现在无需担心并发太多导致学校服务器压力过大。
  修复了获取经纬度时保存的数据为str类型，导致无法偏置
  
2026年3月14日:
  整体修改成异步执行，在多用户情况下比多线程更快，更合适。
  经过实际测试，异步条件下无限制并发量单次执行不超过15s即可完成百人签到
  添加了获取系统设置的宿舍楼信息接口，创建用户对象时无需设置经纬度。
  为签到的位置添加了随机数，模拟定位偏差。
  每个用户使用专属session持久化上下文调用接口，减少爬虫特征。
  
2026年3月5日:
  更正了签到成功后重复签到会按照签到失败处理的bug。
  
2026年1月3日：
  修改用户类可以直接传入md5加密之后的密码，通过is_encrypted属性选择。
  将generate_sign更新为学校现行版本。
  添加了多个随机UA及访问各接口前添加随机延时，模拟人工操作。
"""


@dataclass
class User:
    # 学号(必须填写)
    student_Id: int
    # 姓名(无需填写，获取token时可自动获取)
    username: str = ''
    # 密码(若未修改考勤系统密码可以留空)
    password: str = "Ahgydx@920"
    # 纬度(无需填写，实时获取)
    latitude: float = 0
    # 经度(无需填写，实时获取)
    longitude: float = 0
    # 用户专属token(无需填写，实时获取)
    token: str = None
    # 签到任务的内部Id(无需填写，实时获取)
    taskId: int = None
    # 宿舍床位编号(无需填写，自动获取)
    room_id:str = ""
    # 当前提供的密码是否为加密之后的
    is_encrypted: int = 0
    # 内部持有的session
    _session = None
    # 对外展示的session
    @property
    def session(self):
        if self._session is None:
            session = aiohttp.ClientSession(headers = {
                'User-Agent': random.choice(UA_LIST),
                'authorization': "Basic Zmx5c291cmNlX3dpc2VfYXBwOkRBNzg4YXNkVURqbmFzZF9mbHlzb3VyY2VfZHNkYWREQUlVaXV3cWU=",
                'Content-Type': "application/json;charset=UTF-8",
                'X-Requested-With': "com.tencent.mm",
                'Origin': "https://xskq.ahut.edu.cn",
                'Referer': f"https://xskq.ahut.edu.cn/wise/pages/ssgl/dormsign?&userId={self.student_Id}"
            })
            self._session = session
        else:
            if self.token: self._session.headers["flysource-auth"] = f"bearer {self.token}"
        return self._session

    async def close(self):
        if self._session:
            await self._session.close()


## *------------------------------------------------------* ##
##             请在此处完成您的配置 ([]内的为可选列表)             ##
## *------------------------------------------------------* ##
# log输出的等级 (logging.[DEBUG,INFO,WARNING,ERROR,CRITICAL])
LOG_GRADE = logging.INFO  # 测试时建议用INFO，避免日志过多
## *------------------------------------------------------* ##
##             可视化报表配置                               ##
## *------------------------------------------------------* ##

# 可视化报表配置
VISUALIZATION_CONFIG = {
    'enable': True,  # 是否启用可视化报表
    'show_heatmap': True,  # 显示热力图
    'show_rank': True,  # 显示排行榜
    'show_trend': True,  # 显示趋势图
    'show_location': True,  # 显示地理位置
}

# 数据存储文件
DATA_FILES = {
    'rank': 'sign_rank.json',  # 排行榜数据
    'history': 'sign_history.json',  # 历史数据
    'locations': 'sign_locations.json',  # 位置数据
}
# 成功学号保存文件
SUCCESS_STUDENTS_FILE = 'success_students.json'

# 签到模式：'all'=签到所有学号，'success_only'=只签到成功过的学号，'random'=随机抽取签到
SIGN_MODE = 'random'  # 改为 'random'

# 随机抽取设置（仅当 SIGN_MODE='random' 时生效）
RANDOM_SIGN_CONFIG = {
    'enable': True,  # 是否启用随机抽取
    'sample_size': 27,  # 抽取人数
    'random_seed': None,  # 随机种子（None表示每次不同，设置数字可固定结果）
}

# 测试模式配置
TEST_MODE = {
    'enable': True,  # 是否启用测试模式（跳过时间检查）
    'test_range': (229144003, 229144199),  # 要测试的学号范围
    'test_delay': 2,  # 每个学号测试间隔（秒），避免请求过快
    'max_concurrent_test': 5,  # 测试时的并发数
}

# 成功学号保存文件
SUCCESS_STUDENTS_FILE = 'success_students.json'

# 测试模式：是否合并新旧结果（True=合并，False=覆盖）
TEST_MERGE_MODE = True  # 新增：合并模式，保留历史成功学号并添加新测试成功的学号

# 如果SIGN_MODE为'all'，需要签到的学号范围
SIGN_RANGE = (229084240, 229084319)  # 要签到的学号范围

# 生成连续学号列表的函数
def generate_continuous_students(start_id, end_id):
    """生成连续学号列表"""
    return [User(student_id) for student_id in range(start_id, end_id + 1)]

# 根据SIGN_MODE自动生成用户列表
if SIGN_MODE == 'all':
    # 签到所有学号
    USER_LIST = generate_continuous_students(SIGN_RANGE[0], SIGN_RANGE[1])
    print(f"签到模式：全部学号，共 {len(USER_LIST)} 人")
elif SIGN_MODE == 'success_only':
    # 只签到成功学号（稍后从文件加载）
    USER_LIST = []
    print("签到模式：仅成功学号，将从文件加载")
else:
    USER_LIST = []
    print("未知签到模式")

#USER_LIST = [
    # 使用参考
    # User(259000000),
    # User(259000001, "诸天神佛"),
    # User(259000003, "保我代码", "password"),
    # 批量添加 229044001 到 229044056
 #   *generate_continuous_students(229084280, 229084319),
    
 #   *generate_continuous_students(229144049, 229144053),
    # 此处使用随机学号进行调试，实际情况请使用需要签到学生的学号
    #User(random.randint(259024000,259025000)) for _ in range(20)
#]
# 单次尝试签到最大尝试次数
MAX_RETRIES = 4
# 单次尝试签到因TOKEN失效最大额外尝试次数
MAX_TOKEN_RETRIES = 3
# 异步并发数限制
MAX_CONCURRENT = 15
# 签到请求锁
SIGN_IN_LOCK = asyncio.Lock()

# 邮箱提醒配置
EMAIL_CONFIG = {
    'enable': True,  # 是否启用邮箱提醒
    'smtp_server': 'smtp.qq.com',  # SMTP服务器地址（QQ邮箱）
    'smtp_port': 465,  # SMTP端口（QQ邮箱SSL端口）
    'sender_email': 'ag985211ag@qq.com',  # 发送者邮箱
    'sender_password': 'vtcbdwqqwoxfddji',  # 邮箱授权码（不是登录密码）
    'receiver_email': '3454443243@qq.com',  # 接收者邮箱（可以多个，用逗号分隔）
    # 'receiver_emails': ['email1@qq.com', 'email2@qq.com'],  # 或者使用列表形式
}

# 可选：使用多个接收邮箱
# EMAIL_CONFIG['receiver_emails'] = ['email1@qq.com', 'email2@qq.com']
## *------------------------------------------------------* ##


## *------------------------------------------------------* ##
##                         日志设置区                         ##
## *------------------------------------------------------* ##

# 日志格式设定
formatter = logging.Formatter(
    fmt='%(levelname)s [%(name)s] (%(asctime)s): %(message)s (Line: %(lineno)d [%(filename)s])',
    datefmt='%Y/%m/%d %H:%M:%S'
)

# 获取日志记录器，并设定显示等级
logger = logging.getLogger()
logger.setLevel(LOG_GRADE)

# 添加控制台handler以输出日志
console_handler = logging.StreamHandler(stream=sys.stdout)
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.DEBUG)
logger.addHandler(console_handler)

# 屏蔽第三方库的logging日志
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("dbutils").setLevel(logging.WARNING)
logging.getLogger("yagmail").setLevel(logging.WARNING)
## *------------------------------------------------------* ##


## *------------------------------------------------------* ##
##                         常量声明区                         ##
## *------------------------------------------------------* ##

# 学校考勤系统api_url
API_BASE_URL = "https://xskq.ahut.edu.cn/api"

# 执行完整签到流程所涉及的url
WEB_DICT = {
    # 获取用户token
    "token_api": f"{API_BASE_URL}/flySource-auth/oauth/token",
    # 获取当前签到taskId
    "task_id_api": f"{API_BASE_URL}/flySource-yxgl/dormSignTask/getStudentTaskPage?userDataType=student&current=1&size=15",
    # 获取微信接口配置，确保考勤系统记录中用户是通过微信尝试签到
    "auth_check_api": f"{API_BASE_URL}/flySource-base/wechat/getWechatMpConfig"
                      "?configUrl=https://xskq.ahut.edu.cn/wise/pages/ssgl/dormsign"
                      "?taskId={TASK_ID}&autoSign=1&scanSign=0&userId={STUDENT_ID}",
    # 开启签到的时间窗口
    "apiLog_api": f"{API_BASE_URL}/flySource-base/apiLog/save?menuTitle=%E6%99%9A%E5%AF%9D%E7%AD%BE%E5%88%B0",
    # 获取签到位置
    'get_location_api': f"{API_BASE_URL}/flySource-yxgl/dormSignTask/getTaskByIdForApp"
                        "?taskId={TASK_ID}&signDate={date_str}",
    # 进行晚寝签到(已更新)
    "sign_in_api": f"{API_BASE_URL}/flySource-yxgl/dormSignRecord/stuSign",
    # 获取未签到列表
    "sign_in_result_api": f"{API_BASE_URL}/flySource-yxgl/dormSignStu/getWqdStudentPage"
                          "?taskId={TASK_ID}&xhOrXm=&nowDate={date_str}&userDataType=student&current=1&size=100",
}

# 可选的UA头列表
UA_LIST = [
    "Mozilla/5.0 (Linux; Android 15; MIX Fold 4 Build/TKQ1.240502.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/128.0.6613.137 Mobile Safari/537.36 MicroMessenger/8.0.61.2660(0x28003D37) WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64",
    "Mozilla/5.0 (Linux; Android 15; LYA-AL10 Build/HUAWEILYA-AL10; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/128.0.6613.137 Mobile Safari/537.36 MicroMessenger/8.0.61.2660(0x28003D37) WeChat/arm64 Weixin NetType/5G Language/zh_CN ABI/arm64",
    "Mozilla/5.0 (Linux; Android 15; SM-S938B Build/TP1A.240205.004; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/128.0.6613.137 Mobile Safari/537.36 MicroMessenger/8.0.61.2660(0x28003D37) WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.61(0x18003D29) NetType/WIFI Language/zh_CN",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.61(0x18003D29) NetType/5G Language/zh_CN",
]
## *------------------------------------------------------* ##
## *------------------------------------------------------* ##
##                         功能方法区                         ##
## *------------------------------------------------------* ##

## *------------------------------------------------------* ##
##                  数据统计与可视化类                        ##
## *------------------------------------------------------* ##

import json
import os
from datetime import datetime, timedelta
from collections import defaultdict

class SignStatistics:
    """签到数据统计分析类"""
    
    def __init__(self):
        self.rank_file = DATA_FILES['rank']
        self.history_file = DATA_FILES['history']
        self.locations_file = DATA_FILES['locations']
        
        self.rank_data = self.load_data(self.rank_file, {})
        self.history_data = self.load_data(self.history_file, [])
        self.locations_data = self.load_data(self.locations_file, [])
    
    def load_data(self, filename, default):
        """加载JSON数据"""
        if os.path.exists(filename):
            try:
                with open(filename, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return default
        return default
    
    def save_data(self, filename, data):
        """保存JSON数据"""
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存数据失败: {e}")
    
    def update_rank(self, student_id, username, success):
        """更新排行榜"""
        sid = str(student_id)
        if sid not in self.rank_data:
            self.rank_data[sid] = {
                'student_id': student_id,
                'username': username,
                'total': 0,
                'success': 0,
                'failed': 0,
                'last_sign': None
            }
        
        self.rank_data[sid]['total'] += 1
        if success:
            self.rank_data[sid]['success'] += 1
        else:
            self.rank_data[sid]['failed'] += 1
        self.rank_data[sid]['last_sign'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 更新用户名（如果有）
        if username:
            self.rank_data[sid]['username'] = username
        
        self.save_data(self.rank_file, self.rank_data)
    
    def update_history(self, total, success, failed, duration, mode_info):
        """更新历史记录"""
        self.history_data.append({
            'date': datetime.now().strftime('%Y-%m-%d'),
            'time': datetime.now().strftime('%H:%M:%S'),
            'total': total,
            'success': success,
            'failed': failed,
            'success_rate': success/total*100 if total > 0 else 0,
            'duration': duration,
            'mode': mode_info.get('mode', 'unknown'),
            'sample_size': mode_info.get('sample_size', total)
        })
        
        # 只保留最近30天
        if len(self.history_data) > 30:
            self.history_data = self.history_data[-30:]
        
        self.save_data(self.history_file, self.history_data)
    
    def update_location(self, student_id, latitude, longitude, success):
        """更新位置数据"""
        self.locations_data.append({
            'student_id': student_id,
            'latitude': latitude,
            'longitude': longitude,
            'success': success,
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        
        # 只保留最近1000条
        if len(self.locations_data) > 1000:
            self.locations_data = self.locations_data[-1000:]
        
        self.save_data(self.locations_file, self.locations_data)
    
    def get_top_rankers(self, limit=10):
        """获取排行榜前N名"""
        sorted_rank = sorted(self.rank_data.items(), 
                            key=lambda x: x[1]['success'], 
                            reverse=True)
        
        top_list = []
        for i, (sid, data) in enumerate(sorted_rank[:limit], 1):
            success_rate = data['success']/data['total']*100 if data['total'] > 0 else 0
            top_list.append({
                'rank': i,
                'student_id': data['student_id'],
                'username': data.get('username', ''),
                'success_count': data['success'],
                'total_count': data['total'],
                'success_rate': success_rate,
                'last_sign': data.get('last_sign', '从未')
            })
        
        return top_list
    
    def get_trend_data(self, days=7):
        """获取趋势数据（修复日期匹配问题）"""
        trend = []
        today = datetime.now()
        
        for i in range(days - 1, -1, -1):  # 从最早的日期开始
            target_date = (today - timedelta(days=i))
            target_date_full = target_date.strftime('%Y-%m-%d')
            target_date_short = target_date.strftime('%m-%d')
            
            # 查找匹配的历史记录
            day_data = None
            for record in self.history_data:
                record_date = record.get('date', '')
                # 同时支持完整日期和短日期格式
                if record_date == target_date_full or record_date == target_date_short:
                    day_data = record
                    break
            
            if day_data:
                trend.append({
                    'date': target_date_short,
                    'success_rate': day_data['success_rate'],
                    'total': day_data['total'],
                    'success': day_data['success'],
                    'has_data': True
                })
                logger.debug(f"趋势数据: {target_date_short} - 成功率 {day_data['success_rate']}%")
            else:
                trend.append({
                    'date': target_date_short,
                    'success_rate': 0,
                    'total': 0,
                    'success': 0,
                    'has_data': False
                })
                logger.debug(f"趋势数据: {target_date_short} - 无数据")
        
        return trend
    
    def get_heatmap_data(self):
        """获取热力图数据（按小时分布）"""
        heatmap = defaultdict(int)
        for record in self.history_data:
            if 'time' in record:
                hour = record['time'].split(':')[0]
                heatmap[hour] += record['success']
        
        return dict(heatmap)

## *------------------------------------------------------* ##
##                  随机抽取签到功能区                       ##
## *------------------------------------------------------* ##

import random

def random_select_students(success_students, sample_size):
    """
    从成功学号中随机抽取指定数量的学生
    
    :param success_students: 成功学号列表
    :param sample_size: 要抽取的人数
    :return: 抽取的学生列表
    """
    if not success_students:
        logger.warning("成功学号列表为空，无法抽取")
        return []
    
    # 设置随机种子（如果需要固定结果）
    if RANDOM_SIGN_CONFIG.get('random_seed') is not None:
        random.seed(RANDOM_SIGN_CONFIG['random_seed'])
    
    # 如果抽取人数大于总人数，则返回全部
    if sample_size >= len(success_students):
        logger.warning(f"抽取人数({sample_size})大于等于总人数({len(success_students)})，将签到全部学号")
        return success_students
    
    # 随机抽取
    selected = random.sample(success_students, sample_size)
    
    # 按学号排序（可选）
    selected.sort(key=lambda x: x['student_id'])
    
    return selected

def generate_random_sign_user_list():
    """
    从成功学号中随机生成签到用户列表
    
    :return: User对象列表
    """
    success_students = load_success_students()
    
    if not success_students:
        logger.warning("没有找到成功学号，请先运行测试模式")
        return []
    
    total_success = len(success_students)
    sample_size = RANDOM_SIGN_CONFIG.get('sample_size', 20)
    
    # 随机抽取
    selected_students = random_select_students(success_students, sample_size)
    
    # 生成用户列表
    user_list = []
    for student in selected_students:
        student_id = student['student_id'] if isinstance(student, dict) else student
        user_list.append(User(student_id))
    
    # 输出抽取信息
    logger.info("=" * 50)
    logger.info(f"🎲 随机抽取签到模式")
    logger.info(f"成功学号总数: {total_success} 人")
    logger.info(f"本次抽取人数: {len(selected_students)} 人")
    logger.info(f"抽取比例: {len(selected_students)/total_success*100:.1f}%")
    
    # 显示抽取的学号（前10个）
    display_count = min(10, len(selected_students))
    logger.info(f"抽取学号示例: {', '.join([str(s['student_id']) for s in selected_students[:display_count]])}")
    if len(selected_students) > display_count:
        logger.info(f"  ... 共{len(selected_students)}个")
    logger.info("=" * 50)
    
    return user_list

## *------------------------------------------------------* ##
##                  成功学号测试与保存功能区                     ##
## *------------------------------------------------------* ##

import json
import os

async def test_student_single(user: User, semaphore) -> dict:
    """
    测试单个学号是否能成功签到
    
    :param user: 要测试的用户对象
    :param semaphore: 并发控制信号量
    :return: 测试结果字典
    """
    async with semaphore:
        logger.info(f"测试学号 {user.student_Id}...")
        result = await sign_in(user, debug=True)  # 使用debug模式跳过时间检查
        await asyncio.sleep(TEST_MODE.get('test_delay', 1))  # 延迟避免请求过快
        
        return {
            'student_id': user.student_Id,
            'username': user.username,
            'success': result['success'],
            'error': result['data'] if not result['success'] else None  # result['data'] 已经是列表
        }

async def test_and_save_success_students():
    """
    测试学号范围内哪些能成功签到，并保存到文件
    """
    """
    测试学号范围内哪些能成功签到，并保存到文件
    """
    start_id, end_id = TEST_MODE['test_range']
    total_students = end_id - start_id + 1
    
    logger.info("=" * 60)
    logger.info(f"开始测试学号范围: {start_id} - {end_id} (共 {total_students} 个学号)")
    
    # 显示合并模式状态
    if TEST_MERGE_MODE and os.path.exists(SUCCESS_STUDENTS_FILE):
        logger.info(f"📌 合并模式已启用，将合并新旧测试结果")
    else:
        logger.info(f"📌 覆盖模式，将覆盖旧结果")
    
    logger.info("=" * 60)

    # 创建测试用户列表
    test_users = [User(student_id) for student_id in range(start_id, end_id + 1)]
    
    # 控制并发数
    semaphore = asyncio.Semaphore(TEST_MODE.get('max_concurrent_test', 5))
    
    # 执行测试
    start_time = time.time()
    results = await asyncio.gather(
        *(test_student_single(user, semaphore) for user in test_users)
    )
    end_time = time.time()
    
    # 统计结果
    success_students = []
    failed_students = []
    
    for result in results:
        if result['success']:
            success_students.append({
                'student_id': result['student_id'],
                'username': result['username']
            })
        else:
            failed_students.append({
                'student_id': result['student_id'],
                'error': result['error']
            })
    
    # 输出统计信息
    logger.info("=" * 60)
    logger.info(f"测试完成！总耗时: {end_time - start_time:.2f} 秒")
    logger.info(f"测试总数: {total_students}")
    logger.info(f"成功学号: {len(success_students)} 个")
    logger.info(f"失败学号: {len(failed_students)} 个")
    if total_students > 0:
        logger.info(f"成功率: {len(success_students)/total_students*100:.2f}%")
    logger.info("=" * 60)
    
    # 输出成功学号列表（只显示前20个）
    if success_students:
        logger.info("\n✅ 成功学号列表:")
        display_count = min(20, len(success_students))
        for i, student in enumerate(success_students[:display_count], 1):
            logger.info(f"  {i}. {student['student_id']} ({student['username']})")
        if len(success_students) > display_count:
            logger.info(f"  ... 还有 {len(success_students) - display_count} 个成功学号")
    
    # 输出失败学号及原因（只显示前20个）
    if failed_students:
        logger.info("\n❌ 失败学号列表:")
        display_count = min(20, len(failed_students))
        for i, student in enumerate(failed_students[:display_count], 1):
            # 处理不同类型的错误信息
            error_msg = '未知错误'
            if student['error']:
                if isinstance(student['error'], (list, tuple)):
                    error_msg = student['error'][0] if len(student['error']) > 0 else '未知错误'
                elif isinstance(student['error'], set):
                    error_msg = next(iter(student['error'])) if student['error'] else '未知错误'
                else:
                    error_msg = str(student['error'])
            logger.info(f"  {i}. {student['student_id']}: {error_msg}")
        if len(failed_students) > display_count:
            logger.info(f"  ... 还有 {len(failed_students) - display_count} 个失败学号")
    
    # 保存成功学号到文件
    save_success_students(success_students)
    
    # 关闭所有session
    await asyncio.gather(*[user.close() for user in test_users])
    
    return success_students, failed_students

def save_success_students(success_students):
    """
    保存成功学号到JSON文件（支持合并模式）
    
    :param success_students: 成功学号列表
    """
    # 如果启用合并模式且旧文件存在，则合并
    if TEST_MERGE_MODE and os.path.exists(SUCCESS_STUDENTS_FILE):
        try:
            # 读取旧数据
            with open(SUCCESS_STUDENTS_FILE, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
            
            # 获取旧的学号列表
            old_students = old_data.get('students', [])
            old_student_ids = {s['student_id'] for s in old_students}
            
            # 合并新学号（去重）
            new_students = []
            for student in success_students:
                if student['student_id'] not in old_student_ids:
                    new_students.append(student)
            
            # 合并所有学号
            all_students = old_students + new_students
            
            # 按学号排序
            all_students.sort(key=lambda x: x['student_id'])
            
            logger.info(f"合并模式：旧文件有 {len(old_students)} 个学号")
            logger.info(f"本次测试新增 {len(new_students)} 个成功学号")
            logger.info(f"合并后共 {len(all_students)} 个成功学号")
            
            # 保存合并后的数据
            data = {
                'test_time': get_time()['full'],
                'last_test_range': TEST_MODE['test_range'],
                'test_history': old_data.get('test_history', []) + [{
                    'time': get_time()['full'],
                    'range': TEST_MODE['test_range'],
                    'new_count': len(new_students)
                }],
                'total_success': len(all_students),
                'students': all_students
            }
            
        except Exception as e:
            logger.error(f"读取旧文件失败: {e}，将覆盖保存")
            data = {
                'test_time': get_time()['full'],
                'test_range': TEST_MODE['test_range'],
                'total_success': len(success_students),
                'students': success_students
            }
    else:
        # 覆盖模式
        data = {
            'test_time': get_time()['full'],
            'test_range': TEST_MODE['test_range'],
            'total_success': len(success_students),
            'students': success_students
        }
    
    try:
        with open(SUCCESS_STUDENTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"\n💾 成功学号已保存到: {SUCCESS_STUDENTS_FILE}")
        logger.info(f"   共保存 {len(data['students'])} 个学号")
    except Exception as e:
        logger.error(f"保存成功学号失败: {e}")

def load_success_students():
    """
    从文件加载成功学号列表
    
    :return: 成功学号列表
    """
    if not os.path.exists(SUCCESS_STUDENTS_FILE):
        logger.warning(f"成功学号文件不存在: {SUCCESS_STUDENTS_FILE}")
        return []
    
    try:
        with open(SUCCESS_STUDENTS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        logger.info(f"从文件加载成功学号: {SUCCESS_STUDENTS_FILE}")
        logger.info(f"测试时间: {data.get('test_time', '未知')}")
        logger.info(f"成功学号数量: {len(data.get('students', []))}")
        
        return data.get('students', [])
    except Exception as e:
        logger.error(f"加载成功学号失败: {e}")
        return []

def generate_user_list_from_success():
    """
    从成功学号文件生成用户列表
    
    :return: User对象列表
    """
    success_students = load_success_students()
    
    if not success_students:
        logger.warning("没有找到成功学号，将使用空列表")
        return []
    
    user_list = []
    for student in success_students:
        student_id = student['student_id'] if isinstance(student, dict) else student
        user_list.append(User(student_id))
    
    logger.info(f"已生成 {len(user_list)} 个签到用户")
    return user_list


def password_md5(pwd: str) -> str:
    """
    使用 MD5 算法对用户密码进行加密。

    :param pwd: 需加密的明文字段
    :return: 加密后的字符串
    """
    return hashlib.md5(pwd.encode('utf-8')).hexdigest()


def generate_sign(url, token) -> str:
    """
    实时生成指定用户访问指定网页的访问令牌。

    :param url: 所需访问的url
    :param token: user所持有的令牌token
    :return: 指定的网页令牌
    """
    if not token:
        return ''
    parsed_url = urlparse(url)
    api = parsed_url.path + "?sign="
    timestamp = int(time.time() * 1000)
    inner = f"{timestamp}{token}"
    inner_hash = hashlib.md5(inner.encode("utf-8")).hexdigest()
    raw = f"{api}{inner_hash}"
    final_hash = hashlib.md5(raw.encode("utf-8")).hexdigest()
    encoded_time = base64.b64encode(str(timestamp).encode("utf-8")).decode("utf-8")
    return f"{final_hash}1.{encoded_time}"


def get_time() -> dict:
    """
    获取当前时间，并以结构化格式返回。

    :return: 格式化后的时间
    """
    now = time.localtime()
    date = time.strftime("%Y-%m-%d", now)
    current_time = time.strftime("%H:%M:%S", now)
    full_datetime = time.strftime("%Y年%m月%d日 %H:%M:%S", now)
    week_list = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = week_list[now.tm_wday]
    return {
        "date": date,
        "time": current_time,
        "weekday": weekday,
        "full": full_datetime
    }


def generate_header(user: User, url: str = None) -> dict:
    """
    为user访问指定url生成对应的请求头，建议一段时间后更新UA

    :param user: User对象
    :param url: 所需访问的url
    :return: 访问所需的header
    """
    header = {}
    if user.token:
        header['flysource-auth'] = f"bearer {user.token}"
        if url:
            header['flysource-sign'] = generate_sign(url, user.token)
    return header


def generate_params(user: User):
    """
    为user生成获取token时必须的查询参数

    :param user: User对象
    :return: 所需的查询参数字典
    """
    return {
        'tenantId': '000000',
        'username': user.student_Id,
        'password': user.password if user.is_encrypted else password_md5(user.password),
        'type': 'account',
        'grant_type': 'password',
        'scope': 'all'
    }

# 签到接口新参数signCode生成方法
def generate_signCode(timestamp_ms):
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc) + timedelta(hours=8)

    week = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    w = week[dt.weekday()]
    m = month[dt.month - 1]
    tz = "GMT+0800 (中国标准时间)"
    time_str = f"{w} {m} {dt.day:02d} {dt.year} {dt.strftime('%H:%M:%S')} {tz}"
    return hashlib.md5(time_str.encode()).hexdigest()

# 签到接口新参数stuTaskId生成方法
def generate_stuTaskId(lat, lng, acc, date, taskId, fileId=""):
    data = {
        "latitude": str(lat),
        "longitude": str(lng),
        "locationAccuracy": str(acc),
        "signDate": date,
        "taskId": taskId,
        "fileId": fileId
    }
    json_str = json.dumps(data, separators=(',', ':'))
    return hashlib.md5(json_str.encode()).hexdigest()

# 签到接口新的提交表单
def generate_data(user: User) -> dict:
    """
    为user生成对应的data用于签到请求时发送

    :param user: User对象
    :return: 规范后的data字典
    """
    signLat = user.latitude + round(random.uniform(-0.01, 0.01), 6)
    signLng = user.longitude + round(random.uniform(-0.01, 0.01), 6)
    locationAccuracy = round(random.uniform(25, 35), 2)
    return {
        "signType": 0,
        "taskId": user.taskId,
        "signLat": signLat,
        "signLng": signLng,
        "locationAccuracy": locationAccuracy,
        "stuTaskId": generate_stuTaskId(signLat,signLng,locationAccuracy,get_time()['date'],user.taskId),
        "scanCode": "",
        "scanType": "",
        "roomId": user.room_id,
        "signKey": user.room_id,
        "signCode": generate_signCode(int(time.time())),
    }


## *------------------------------------------------------* ##
##                         邮箱提醒功能区                       ##
## *------------------------------------------------------* ##
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

def send_sign_summary_email(results_dict, total_users, success_count, failed_count, duration, mode_info):                           
    """
    发送签到结果统计邮件（完整优化版）
    
    :param results_dict: 签到结果字典 {学号: {'success': bool, 'data': list}}
    :param total_users: 总人数
    :param success_count: 成功人数
    :param failed_count: 失败人数
    :param duration: 执行耗时（秒）
    """
    if not EMAIL_CONFIG.get('enable', False):
        logger.info("邮箱提醒功能未启用")
        return
    
    # 计算成功率
    success_rate = (success_count / total_users * 100) if total_users > 0 else 0
    
    # 获取当前时间
    current_time = get_time()
    
    # 收集失败学生信息（带学号和姓名）
    failed_students = []
    global USER_LIST
    
    for student_id, result in results_dict.items():
        if not result['success']:
            # 尝试获取姓名（如果有的话）
            username = ''
            for user in USER_LIST:
                if user.student_Id == student_id:
                    username = user.username
                    break
            failed_students.append({
                'student_id': student_id,
                'username': username,
                'error': ', '.join(result['data']) if result['data'] else '未知错误'
            })
    
    # ✅ 修复：不要覆盖 mode_info，只补充缺失的字段
    if 'description' not in mode_info:
        mode_info['description'] = get_mode_description()
    
    # 如果是随机模式，添加额外信息
    if SIGN_MODE == 'random':
        mode_info['sample_size'] = total_users
        mode_info['total_success'] = len(load_success_students())
        mode_info['sample_ratio'] = f"{total_users/len(load_success_students())*100:.1f}%" if load_success_students() else "0%"
    
    # 生成邮件内容（HTML格式，更美观）
    html_content = generate_html_email(
        current_time, total_users, success_count, failed_count, 
        success_rate, duration, failed_students, mode_info
    )
    
    # 生成纯文本内容（备用）
    text_content = generate_text_email(
        current_time, total_users, success_count, failed_count, 
        success_rate, duration, failed_students, mode_info
    )
    
    # 发送邮件
    try:
        msg = MIMEMultipart('alternative')
        
        # 邮件主题（根据成功率显示不同表情）
        if success_rate == 100:
            subject_emoji = "🎉"
            subject_status = "完美签到"
        elif success_rate >= 90:
            subject_emoji = "👍"
            subject_status = "签到成功"
        elif success_rate >= 70:
            subject_emoji = "⚠️"
            subject_status = "部分失败"
        else:
            subject_emoji = "❌"
            subject_status = "大量失败"
        
        # 添加模式标识到主题
        mode_prefix = ""
        if SIGN_MODE == 'random':
            mode_prefix = f"[随机{total_users}人] "
        elif SIGN_MODE == 'success_only':
            mode_prefix = "[仅成功] "
        elif SIGN_MODE == 'all':
            mode_prefix = "[全员] "
        
        msg['Subject'] = Header(
            f"{subject_emoji} {mode_prefix}考勤签到报告 - {current_time['date']} - {subject_status}({success_rate:.1f}%)", 
            'utf-8'
        )
        msg['From'] = EMAIL_CONFIG['sender_email']
        
        # 处理接收邮箱
        receivers = EMAIL_CONFIG.get('receiver_emails', [])
        if not receivers and EMAIL_CONFIG.get('receiver_email'):
            receivers = [EMAIL_CONFIG['receiver_email']]
        
        if not receivers:
            logger.warning("未配置接收邮箱，无法发送邮件")
            return
            
        msg['To'] = ', '.join(receivers)
        
        # 添加邮件内容
        msg.attach(MIMEText(text_content, 'plain', 'utf-8'))
        msg.attach(MIMEText(html_content, 'html', 'utf-8'))
        
        # 发送邮件
        server = smtplib.SMTP_SSL(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port'])
        server.login(EMAIL_CONFIG['sender_email'], EMAIL_CONFIG['sender_password'])
        server.sendmail(EMAIL_CONFIG['sender_email'], receivers, msg.as_string())
        server.quit()
        
        logger.info(f"签到统计邮件已发送至 {', '.join(receivers)}")
        
    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"邮件认证失败，请检查邮箱和授权码: {e}")
    except smtplib.SMTPException as e:
        logger.error(f"邮件发送失败: {e}")
    except Exception as e:
        logger.error(f"发送邮件时发生未知错误: {e}")

def get_mode_description():
    """获取签到模式的描述"""
    if SIGN_MODE == 'all':
        return "全员签到模式"
    elif SIGN_MODE == 'success_only':
        return "成功学号签到模式"
    elif SIGN_MODE == 'random':
        return "随机抽取签到模式"
    else:
        return "未知模式"

def generate_html_email(current_time, total_users, success_count, failed_count, 
                        success_rate, duration, failed_students, mode_info):
    """生成HTML格式的邮件内容（包含可视化）"""
    
    # 初始化统计类
    stats = SignStatistics()
    
    # 获取数据
    top_rankers = stats.get_top_rankers(10)
    trend_data = stats.get_trend_data(7)
    heatmap_data = stats.get_heatmap_data()
    
    # 根据成功率设置颜色
    if success_rate == 100:
        status_color = "#4CAF50"
        status_icon = "✅"
        status_text = "完美签到"
    elif success_rate >= 90:
        status_color = "#8BC34A"
        status_icon = "👍"
        status_text = "签到成功"
    elif success_rate >= 70:
        status_color = "#FFC107"
        status_icon = "⚠️"
        status_text = "部分失败"
    else:
        status_color = "#F44336"
        status_icon = "❌"
        status_text = "需要关注"
    
    # 模式信息HTML
    mode_html = ""
    if mode_info['mode'] == 'random':
        mode_html = f"""
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                        border-radius: 8px; padding: 12px; margin: 15px 0; text-align: center;">
                <div style="font-size: 24px; margin-bottom: 5px;">🎲</div>
                <div style="font-size: 14px; font-weight: bold;">随机抽取签到模式</div>
                <div style="font-size: 12px; margin-top: 5px;">
                    从 {mode_info.get('total_success', 0)} 个成功学号中随机抽取 {mode_info.get('sample_size', 0)} 人<br>
                    抽取比例: {mode_info.get('sample_ratio', '0%')}
                </div>
            </div>
        """
    
    # 生成排行榜HTML
    rank_html = generate_rank_html(top_rankers)
    
    # 生成趋势图HTML
    trend_html = generate_trend_chart_html(trend_data)
    
    # 生成热力图HTML
    heatmap_html = generate_heatmap_html(heatmap_data)
    
    # 生成地理位置HTML
    location_html = generate_location_html(stats.locations_data)
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                line-height: 1.6;
                color: #333;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                margin: 0;
                padding: 20px;
            }}
            .container {{
                max-width: 800px;
                margin: 0 auto;
                background: white;
                border-radius: 20px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                overflow: hidden;
            }}
            .header {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 40px;
                text-align: center;
            }}
            .header h1 {{
                margin: 0;
                font-size: 28px;
                font-weight: 600;
            }}
            .header .time {{
                margin-top: 10px;
                font-size: 14px;
                opacity: 0.9;
            }}
            .status-badge {{
                display: inline-block;
                background: {status_color};
                color: white;
                padding: 6px 16px;
                border-radius: 25px;
                font-size: 14px;
                font-weight: 500;
                margin-top: 15px;
            }}
            .stats-grid {{
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 1px;
                background: #e0e0e0;
                margin: 20px;
                border-radius: 12px;
                overflow: hidden;
            }}
            .stat-card {{
                background: white;
                padding: 20px;
                text-align: center;
                transition: transform 0.2s;
            }}
            .stat-card:hover {{
                transform: translateY(-2px);
            }}
            .stat-number {{
                font-size: 32px;
                font-weight: bold;
                margin-bottom: 8px;
            }}
            .stat-label {{
                font-size: 12px;
                color: #666;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }}
            .success {{
                color: #4CAF50;
            }}
            .failed {{
                color: #F44336;
            }}
            .section {{
                margin: 30px 20px;
                padding: 20px;
                background: #f8f9fa;
                border-radius: 12px;
            }}
            .section-title {{
                font-size: 18px;
                font-weight: 600;
                margin-bottom: 15px;
                padding-bottom: 10px;
                border-bottom: 2px solid #667eea;
                display: inline-block;
            }}
            .rank-table {{
                width: 100%;
                border-collapse: collapse;
            }}
            .rank-table th {{
                text-align: left;
                padding: 12px;
                background: #e9ecef;
                font-weight: 600;
            }}
            .rank-table td {{
                padding: 10px 12px;
                border-bottom: 1px solid #dee2e6;
            }}
            .rank-table tr:hover {{
                background: #f1f3f5;
            }}
            .medal-1 {{ color: #FFD700; font-weight: bold; }}
            .medal-2 {{ color: #C0C0C0; font-weight: bold; }}
            .medal-3 {{ color: #CD7F32; font-weight: bold; }}
            .chart-container {{
                height: 300px;
                margin: 20px 0;
            }}
            .heatmap-grid {{
                display: grid;
                grid-template-columns: repeat(24, 1fr);
                gap: 2px;
                margin: 20px 0;
            }}
            .heatmap-cell {{
                aspect-ratio: 1;
                background: #e9ecef;
                border-radius: 4px;
                transition: all 0.2s;
                position: relative;
            }}
            .heatmap-cell:hover {{
                transform: scale(1.1);
                z-index: 10;
            }}
            .location-badge {{
                display: inline-block;
                background: #4CAF50;
                color: white;
                padding: 8px 16px;
                border-radius: 8px;
                margin: 5px;
                font-size: 12px;
            }}
            .failed-list {{
                max-height: 400px;
                overflow-y: auto;
            }}
            .student-item {{
                background: #fff5f5;
                border-left: 3px solid #F44336;
                padding: 12px;
                margin-bottom: 8px;
                border-radius: 6px;
            }}
            .footer {{
                background: #f8f9fa;
                padding: 20px;
                text-align: center;
                font-size: 12px;
                color: #999;
            }}
            @media (max-width: 600px) {{
                .stats-grid {{
                    grid-template-columns: repeat(2, 1fr);
                }}
                .heatmap-grid {{
                    grid-template-columns: repeat(12, 1fr);
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>{status_icon} 南京大学考勤签到报告</h1>
                <div class="time">{current_time['full']}</div>
                <div class="status-badge">{status_text}</div>
            </div>
            
            {mode_html}
            
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-number">{total_users}</div>
                    <div class="stat-label">总人数</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number success">{success_count}</div>
                    <div class="stat-label">成功签到</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number failed">{failed_count}</div>
                    <div class="stat-label">签到失败</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number" style="color: {status_color};">{success_rate:.1f}%</div>
                    <div class="stat-label">成功率</div>
                </div>
            </div>
            
            <div style="text-align: center; margin: -10px 0 20px;">
                <span style="color: #666;">⏱️ 总耗时: {duration:.2f} 秒</span>
                <span style="margin: 0 10px;">|</span>
                <span style="color: #666;">⚡ 平均: {duration/total_users:.2f} 秒/人</span>
            </div>
    """
    
    # 添加排行榜
    if VISUALIZATION_CONFIG.get('show_rank', True):
        html += rank_html
    
    # 添加趋势图
    if VISUALIZATION_CONFIG.get('show_trend', True):
        html += trend_html
    
    # 添加热力图
    if VISUALIZATION_CONFIG.get('show_heatmap', True):
        html += heatmap_html
    
    # 添加地理位置
    if VISUALIZATION_CONFIG.get('show_location', True):
        html += location_html
    
    # 添加失败列表
    if failed_students:
        html += generate_failed_list_html(failed_students)
    
    html += """
            <div class="footer">
                <div>📊 数据统计基于最近30天签到记录</div>
                <div style="margin-top: 8px;">本邮件由南京大学考勤系统自动发送 | 版本 v2.0</div>
                <div style="margin-top: 8px;">💡 提示：排行榜根据累计成功次数排名</div>
            </div>
        </div>
    </body>
    </html>
    """
    
    return html

def generate_rank_html(top_rankers):
    """生成排行榜HTML（修复空数据问题）"""
    
    if not top_rankers:
        return f"""
        <div class="section">
            <div class="section-title">🏆 签到排行榜</div>
            <div style="background: #f8f9fa; padding: 40px; text-align: center; border-radius: 12px;">
                <div style="font-size: 48px; margin-bottom: 15px;">🏆</div>
                <div style="font-size: 16px; color: #666; margin-bottom: 10px;">
                    暂无排行榜数据
                </div>
                <div style="font-size: 14px; color: #999;">
                    开始签到后，这里将显示签到次数最多的同学
                </div>
                <div style="margin-top: 20px; padding: 10px; background: #e3f2fd; border-radius: 8px; font-size: 12px;">
                    💪 第一个完成签到的同学将登上榜首！
                </div>
            </div>
        </div>
        """
    
    html = """
        <div class="section">
            <div class="section-title">🏆 签到排行榜 TOP 10</div>
            <table class="rank-table">
                <thead>
                    <tr>
                        <th>排名</th>
                        <th>学号</th>
                        <th>姓名</th>
                        <th>成功次数</th>
                        <th>总次数</th>
                        <th>成功率</th>
                    </tr>
                </thead>
                <tbody>
    """
    
    for ranker in top_rankers:
        medal_class = ""
        if ranker['rank'] == 1:
            medal_class = "medal-1"
        elif ranker['rank'] == 2:
            medal_class = "medal-2"
        elif ranker['rank'] == 3:
            medal_class = "medal-3"
        
        rank_display = f"<span class='{medal_class}'>#{ranker['rank']}</span>"
        if ranker['rank'] <= 3:
            medals = {1: "🥇", 2: "🥈", 3: "🥉"}
            rank_display = f"{medals[ranker['rank']]}"
        
        html += f"""
                    <tr>
                        <td>{rank_display}</td>
                        <td>{ranker['student_id']}</td>
                        <td>{ranker['username'] or '未设置'}</td>
                        <td>{ranker['success_count']}</td>
                        <td>{ranker['total_count']}</td>
                        <td>{ranker['success_rate']:.1f}%</td>
                    </tr>
        """
    
    html += """
                </tbody>
            </table>
            <div style="margin-top: 10px; font-size: 12px; color: #999; text-align: center;">
                📊 排行榜根据累计成功签到次数排序，每天更新
            </div>
        </div>
    """
    
    return html

def generate_trend_chart_html(trend_data):
    """生成趋势图HTML（修复空白问题）"""
    
    # 检查是否有足够的数据
    valid_data = [d for d in trend_data if d['success_rate'] > 0 or d['total'] > 0]
    
    if not valid_data or len(valid_data) < 1:
        # 数据不足时显示友好提示
        return f"""
        <div class="section">
            <div class="section-title">📈 签到趋势图（最近7天）</div>
            <div style="background: #f8f9fa; padding: 40px; text-align: center; border-radius: 12px;">
                <div style="font-size: 48px; margin-bottom: 15px;">📊</div>
                <div style="font-size: 16px; color: #666; margin-bottom: 10px;">
                    暂无足够的签到数据
                </div>
                <div style="font-size: 14px; color: #999;">
                    需要至少1天的签到记录才能生成趋势图<br>
                    继续签到，数据将自动积累
                </div>
                <div style="margin-top: 20px; padding: 10px; background: #e3f2fd; border-radius: 8px; font-size: 12px;">
                    💡 提示：趋势图将显示最近7天的签到成功率变化
                </div>
            </div>
        </div>
        """
    
    # 准备数据，补充缺失的日期
    import datetime
    current_date = datetime.datetime.now()
    complete_trend_data = []
    
    # 生成最近7天的完整数据
    for i in range(6, -1, -1):
        date = (current_date - datetime.timedelta(days=i)).strftime('%m-%d')
        # 查找是否有该日期的数据
        day_data = next((d for d in trend_data if d['date'] == date), None)
        if day_data:
            complete_trend_data.append(day_data)
        else:
            # 没有数据的日期，添加空数据
            complete_trend_data.append({
                'date': date,
                'success_rate': 0,
                'total': 0,
                'success': 0,
                'has_data': False
            })
    
    dates = [d['date'] for d in complete_trend_data]
    rates = [d['success_rate'] for d in complete_trend_data]
    success_counts = [d['success'] for d in complete_trend_data]
    has_data = [d.get('has_data', True) for d in complete_trend_data]
    
    # 检查是否所有数据都是0
    if sum(rates) == 0:
        return f"""
        <div class="section">
            <div class="section-title">📈 签到趋势图（最近7天）</div>
            <div style="background: #f8f9fa; padding: 40px; text-align: center; border-radius: 12px;">
                <div style="font-size: 48px; margin-bottom: 15px;">📈</div>
                <div style="font-size: 16px; color: #666; margin-bottom: 10px;">
                    签到数据积累中...
                </div>
                <div style="font-size: 14px; color: #999;">
                    目前还没有签到记录，开始签到后趋势图将自动显示
                </div>
                <div style="margin-top: 20px; padding: 10px; background: #fff3e0; border-radius: 8px; font-size: 12px;">
                    🎯 首次签到后，明天的报告中就会显示趋势图啦！
                </div>
            </div>
        </div>
        """
    
    # 生成带标注的图表
    html = f"""
        <div class="section">
            <div class="section-title">📈 签到趋势图（最近7天）</div>
            <div id="trendChart" class="chart-container"></div>
            <script>
                var trendChart = echarts.init(document.getElementById('trendChart'));
                var option = {{
                    tooltip: {{
                        trigger: 'axis',
                        axisPointer: {{ type: 'shadow' }},
                        formatter: function(params) {{
                            var result = params[0].axisValue + '<br/>';
                            for (var i = 0; i < params.length; i++) {{
                                var data = params[i];
                                if (data.value === 0 && data.seriesName === '成功率(%)') {{
                                    result += data.marker + ' ' + data.seriesName + ': 暂无数据<br/>';
                                }} else {{
                                    result += data.marker + ' ' + data.seriesName + ': ' + data.value;
                                    if (data.seriesName === '成功率(%)') result += '%';
                                    result += '<br/>';
                                }}
                            }}
                            return result;
                        }}
                    }},
                    legend: {{
                        data: ['成功率(%)', '成功人数']
                    }},
                    xAxis: {{
                        type: 'category',
                        data: {dates},
                        axisLabel: {{
                            rotate: 0,
                            interval: 0
                        }}
                    }},
                    yAxis: [
                        {{
                            type: 'value',
                            name: '成功率(%)',
                            min: 0,
                            max: 100,
                            axisLabel: {{ formatter: '{{value}}%' }}
                        }},
                        {{
                            type: 'value',
                            name: '人数',
                            min: 0
                        }}
                    ],
                    series: [
                        {{
                            name: '成功率(%)',
                            type: 'line',
                            data: {rates},
                            smooth: true,
                            lineStyle: {{ width: 3, color: '#667eea' }},
                            areaStyle: {{ opacity: 0.3, color: '#667eea' }},
                            symbol: 'circle',
                            symbolSize: 8,
                            connectNulls: false,
                            itemStyle: {{
                                color: function(params) {{
                                    if (params.value === 0 && {has_data}[params.dataIndex] === false) {{
                                        return '#ccc';
                                    }}
                                    return '#667eea';
                                }}
                            }}
                        }},
                        {{
                            name: '成功人数',
                            type: 'bar',
                            yAxisIndex: 1,
                            data: {success_counts},
                            itemStyle: {{
                                borderRadius: [5, 5, 0, 0],
                                color: '#4CAF50',
                                opacity: function(params) {{
                                    if (params.value === 0 && {has_data}[params.dataIndex] === false) {{
                                        return 0.3;
                                    }}
                                    return 0.8;
                                }}
                            }}
                        }}
                    ]],
                    graphic: {{
                        type: 'text',
                        left: 'center',
                        top: 'middle',
                        style: {{
                            text: {f"'{'暂无数据' if sum(rates) == 0 else ''}'"},
                            fill: '#999',
                            fontSize: 14,
                            fontWeight: 'bold'
                        }},
                        invisible: {sum(rates) > 0}
                    }}
                }};
                trendChart.setOption(option);
                
                // 窗口大小改变时重绘
                window.addEventListener('resize', function() {{
                    trendChart.resize();
                }});
            </script>
            <div style="margin-top: 10px; font-size: 12px; color: #999; text-align: center;">
                💡 提示：图表显示最近7天的签到情况，灰色区域表示暂无数据
            </div>
        </div>
    """
    
    return html

def generate_heatmap_html(heatmap_data):
    """生成热力图HTML（修复空白问题）"""
    
    if not heatmap_data or sum(heatmap_data.values()) == 0:
        return f"""
        <div class="section">
            <div class="section-title">🔥 签到热力图（按小时分布）</div>
            <div style="background: #f8f9fa; padding: 40px; text-align: center; border-radius: 12px;">
                <div style="font-size: 48px; margin-bottom: 15px;">🔥</div>
                <div style="font-size: 16px; color: #666; margin-bottom: 10px;">
                    暂无签到时间数据
                </div>
                <div style="font-size: 14px; color: #999;">
                    开始签到后，热力图将自动记录每天的签到时间分布
                </div>
                <div style="margin-top: 20px; padding: 10px; background: #fff3e0; border-radius: 8px; font-size: 12px;">
                    ⏰ 热力图会显示每个小时段的签到人数，帮助了解签到高峰时间
                </div>
            </div>
        </div>
        """
    
    # 创建24小时的颜色映射
    hours = []
    colors = []
    max_count = max(heatmap_data.values()) if heatmap_data else 1
    
    for hour in range(24):
        hour_str = f"{hour:02d}"
        count = heatmap_data.get(hour_str, 0)
        hours.append(hour_str)
        
        # 根据签到数量设置颜色强度
        if max_count == 0:
            intensity = 0
        else:
            intensity = count / max_count
        
        if count == 0:
            color = "#e9ecef"
        elif intensity < 0.2:
            color = "#c3e6cb"
        elif intensity < 0.4:
            color = "#9ecf9e"
        elif intensity < 0.6:
            color = "#6fbf4c"
        elif intensity < 0.8:
            color = "#ffc107"
        else:
            color = "#dc3545"
        
        colors.append(color)
    
    # 添加统计信息
    total_signs = sum(heatmap_data.values())
    peak_hour = max(heatmap_data.items(), key=lambda x: x[1]) if heatmap_data else None
    
    html = f"""
        <div class="section">
            <div class="section-title">🔥 签到热力图（按小时分布）</div>
            <div class="heatmap-grid">
    """
    
    for i, (hour, color) in enumerate(zip(hours, colors)):
        count = heatmap_data.get(hour, 0)
        html += f"""
                <div class="heatmap-cell" style="background: {color};" 
                     title="{hour}:00 - 签到{count}次">
                    <div style="position: absolute; bottom: 2px; right: 4px; font-size: 9px; color: #666;">
                        {hour}
                    </div>
                </div>
        """
    
    html += """
            </div>
            <div style="display: flex; justify-content: space-between; margin-top: 15px; font-size: 12px;">
                <span>📊 颜色越深表示签到人数越多</span>
    """
    
    if peak_hour:
        html += f"""
                <span>🔥 高峰时段: {peak_hour[0]}:00 ({peak_hour[1]}次)</span>
        """
    
    html += f"""
                <span>📈 总签到: {total_signs}次</span>
            </div>
        </div>
    """
    
    return html

def generate_location_html(locations_data):
    """生成地理位置HTML"""
    if not locations_data:
        return '<div class="section"><div class="section-title">📍 签到地理位置</div><p>暂无位置数据</p></div>'
    
    # 统计最近的位置
    recent_locations = locations_data[-50:]  # 最近50条
    
    # 统计位置频率
    location_count = defaultdict(int)
    for loc in recent_locations:
        if loc['success']:
            key = f"{loc['latitude']:.2f},{loc['longitude']:.2f}"
            location_count[key] += 1
    
    # 获取最常签到位置
    top_location = max(location_count.items(), key=lambda x: x[1]) if location_count else None
    
    html = """
        <div class="section">
            <div class="section-title">📍 签到地理位置分析</div>
    """
    
    if top_location:
        html += f"""
            <div style="background: #e3f2fd; padding: 15px; border-radius: 8px; margin: 15px 0;">
                <div style="font-size: 14px; font-weight: bold; margin-bottom: 8px;">📍 最常签到位置</div>
                <div>坐标: {top_location[0]}</div>
                <div>签到次数: {top_location[1]} 次</div>
                <div style="margin-top: 8px;">
                    <a href="https://www.amap.com/search?query={top_location[0]}" 
                       target="_blank" style="color: #667eea;">
                        🔍 查看地图详情
                    </a>
                </div>
            </div>
        """
    
    html += f"""
            <div style="background: #f5f5f5; padding: 12px; border-radius: 8px;">
                <div>📊 统计信息</div>
                <div>• 最近50次签到位置记录</div>
                <div>• 共有 {len(location_count)} 个不同位置</div>
                <div>• 位置集中在宿舍区域附近</div>
            </div>
        </div>
    """
    
    return html

def generate_failed_list_html(failed_students):
    """生成失败列表HTML"""
    display_limit = 50
    display_count = min(display_limit, len(failed_students))
    
    html = f"""
        <div class="section">
            <div class="section-title">❌ 签到失败详情 ({len(failed_students)}人)</div>
            <div class="failed-list">
    """
    
    for student in failed_students[:display_count]:
        name_display = f"({student['username']})" if student['username'] else ""
        html += f"""
                <div class="student-item">
                    <div>
                        <span style="font-weight: bold; color: #d32f2f;">{student['student_id']}</span>
                        <span style="color: #666; margin-left: 8px;">{name_display}</span>
                    </div>
                    <div style="color: #999; font-size: 12px; margin-top: 5px;">
                        ⚠️ {student['error']}
                    </div>
                </div>
        """
    
    if len(failed_students) > display_limit:
        html += f"""
                <div style="background: #fff3e0; padding: 12px; border-radius: 6px; margin-top: 10px; text-align: center;">
                    📊 还有 {len(failed_students) - display_limit} 个失败未显示，请查看控制台日志
                </div>
        """
    
    html += """
            </div>
            <div style="background: #e3f2fd; padding: 12px; border-radius: 8px; margin-top: 15px;">
                💡 提示：失败的学号可能因为密码错误、网络问题或未到签到时间导致
            </div>
        </div>
    """
    
    return html



def generate_text_email(current_time, total_users, success_count, failed_count, success_rate, duration, failed_students, mode_info):
    """生成纯文本格式的邮件内容（完整版）"""
    
    # 根据成功率添加表情
    if success_rate == 100:
        status_emoji = "🎉"
        status_text = "完美签到"
    elif success_rate >= 90:
        status_emoji = "👍"
        status_text = "签到成功"
    elif success_rate >= 70:
        status_emoji = "⚠️"
        status_text = "部分失败"
    else:
        status_emoji = "❌"
        status_text = "需要关注"
    
    # 模式信息
    mode_text = ""
    if mode_info['mode'] == 'random':
        mode_text = f"""
签到模式：随机抽取签到
  从 {mode_info.get('total_success', 0)} 个成功学号中随机抽取 {mode_info.get('sample_size', 0)} 人
  抽取比例: {mode_info.get('sample_ratio', '0%')}
"""
    elif mode_info['mode'] == 'success_only':
        mode_text = """
签到模式：成功学号签到
  仅签到已验证成功的学号
"""
    elif mode_info['mode'] == 'all':
        mode_text = """
签到模式：全员签到
  签到指定范围内的所有学号
"""
    
    text = f"""
╔═══════════════════════════════════════════════════════════╗
║          {status_emoji} 大学考勤签到报告 {status_emoji}          ║
╚═══════════════════════════════════════════════════════════╝

签到时间：{current_time['full']}
签到状态：{status_text}
{mode_text}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 统计信息
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  总人数：{total_users} 人
  ✅ 成功：{success_count} 人
  ❌ 失败：{failed_count} 人
  📈 成功率：{success_rate:.2f}%
  ⏱️  总耗时：{duration:.2f} 秒
  ⚡ 平均耗时：{duration/total_users:.2f} 秒/人
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    if failed_students:
        # 限制显示数量
        display_limit = 30
        display_count = min(display_limit, len(failed_students))
        
        text += f"""
❌ 签到失败详情 ({len(failed_students)}人)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for student in failed_students[:display_count]:
            name_info = f" ({student['username']})" if student['username'] else ""
            text += f"""
  学号：{student['student_id']}{name_info}
  原因：{student['error']}
  ─────────────────────────────────────────────────────────
"""
        
        if len(failed_students) > display_limit:
            text += f"""
  ... 还有 {len(failed_students) - display_limit} 个失败未显示，请查看控制台日志
  ─────────────────────────────────────────────────────────
"""
        
        text += """
💡 提示：失败的学号可能因为以下原因：
   • 密码错误或账号异常
   • 网络连接问题
   • 未到签到时间
   • 系统临时故障
建议手动检查或稍后重试。
"""
    else:
        text += """
🎉 全员签到成功！所有同学均已成功签到，继续保持！
"""
    
    # 添加性能建议
    if duration > 300:
        text += """
⚠️ 性能提示：本次签到耗时较长，建议：
   • 检查网络连接
   • 适当增加并发数（MAX_CONCURRENT）
   • 考虑使用随机抽取模式
"""
    elif duration > 120:
        text += """
📊 性能提示：签到耗时正常，可保持当前配置。
"""
    
    text += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
本邮件由大学考勤系统自动发送
如有疑问请联系管理员
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    return text    
## *------------------------------------------------------* ##





## *------------------------------------------------------* ##
##                       主要功能实现区                       ##
## *------------------------------------------------------* ##

async def sign_in_by_step(user: User, step: int, debug: bool = False) -> dict:
    """
    为指定user执行step步的签到过程，旨在实现错误重试

    :param user: 执行晚寝签到的User对象
    :param step: 当前需要执行的步骤数
    :param debug: 是否处于debug模式
    :return: {success:当前步骤是否完成, msg:错误信息, step:下一次将要进行的步骤}
    """
    # 签到前时间检验
    if not debug:
        now_time = get_time()['time']
        if now_time < '21:20:00':
            logger.error(f'当前时间 {now_time} 未到签到时间，不进行签到')
            return {'success': False,'msg':"未到签到时间",'step': -1}

    # 获取token
    if step == 0:
        logger.info(f"开始为 {user.student_Id} 获取token")
        async with user.session.post(
                url=WEB_DICT["token_api"],
                params=generate_params(user),
                headers=generate_header(user)
        ) as resp:
            token_result = await resp.json()
        logger.debug(f'{user.student_Id} 获取token返回信息 {token_result}')
        if 'refresh_token' in token_result:
            user.token = token_result['refresh_token']
            user.username = token_result['userName']
            logger.info(f"成功为 {user.username}({user.student_Id}) 获取到token")
            logger.debug(f"{user.username}({user.student_Id}) 的token为 {user.token}")
            return {'success': True, 'msg':'','step':step+1}
        else:
            error_desc = token_result.get('error_description','未知错误')
            if "Bad credentials" in error_desc or "用户名或密码错误" in error_desc: error_desc = "密码错误"
            logger.error(f"为 {user.student_Id} 获取token时，出现错误：{error_desc}")
            return {'success': False, 'msg': error_desc, 'step': -1}
    # 获取taskId
    if step == 1:
        logger.info(f"开始为 {user.username}({user.student_Id}) 获取当前签到taskId")
        async with user.session.get(
                url=WEB_DICT['task_id_api'],
                headers=generate_header(user,WEB_DICT['task_id_api'])
        ) as resp:
            task_result = await resp.json()
        logger.debug(f"{user.username}({user.student_Id}) 获取taskId返回信息 {task_result}")
        if task_result['code'] == 200:
            if task_result.get('data', {}).get('records', [{}])[0].get("taskId"):
                user.taskId = task_result.get('data').get('records')[0].get('taskId')
                logger.info(f"为 {user.username}({user.student_Id}) 获取到当前签到的taskId：{user.taskId}")
                return {'success': True, 'msg': '', 'step': step+1}
            else:
                logger.error(f"{user.username}({user.student_Id}) 获取taskId时未在返回信息中解析到taskId字段，请检查{task_result}")
                return {'success': False, 'msg': '未在返回信息中解析到taskId字段', 'step': step}
        else:
            if (("请求未授权" in task_result.get('msg'))
                    or ("缺失身份信息" in task_result.get('msg'))
                    or ('鉴权失败' in task_result.get('msg'))):
                logger.warning(f"{user.username}({user.student_Id}) Token失效或未授权，将重试获取Token。")
                user.token = ''
                return {'success': False, 'msg': 'token失效', 'step': 0}
            else:
                logger.warning(f"{user.username}({user.student_Id}) 获取taskId时出现问题：{task_result.get('msg')}")
                return {'success': False, 'msg': task_result.get('msg'), 'step': step}
    # 获取微信接口配置
    if step == 2:
        logger.info(f"开始为 {user.username}({user.student_Id}) 获取微信接口配置")
        url =WEB_DICT['auth_check_api'].format(TASK_ID=user.taskId,STUDENT_ID=user.student_Id)
        async with user.session.get(
                url=url,
                headers=generate_header(user,url)
        ) as resp:
            auth_result = await resp.json()
        logger.debug(f"{user.username}({user.student_Id}) 获取微信接口配置返回信息 {auth_result}")
        if auth_result['code'] == 200:
            logger.info(f"为 {user.username}({user.student_Id}) 获取微信接口配置信息成功")
            return {'success': True, 'msg': '', 'step': step+1}
        else:
            if (("请求未授权" in auth_result.get('msg'))
                    or ("缺失身份信息" in auth_result.get('msg'))
                    or ('鉴权失败' in auth_result.get('msg'))):
                logger.warning(f"{user.username}({user.student_Id}) Token失效或未授权，将重试获取Token。")
                user.token = ''
                return {'success': False, 'msg': 'token失效', 'step': 0}
            else:
                logger.warning(
                    f"{user.username}({user.student_Id}) 获取微信接口配置信息时出现问题：{auth_result.get('msg')}")
                return {'success': False, 'msg': auth_result.get('msg'), 'step': step}
    # 开启时间窗口
    if step == 3:
        logger.info(f"开始为 {user.username}({user.student_Id}) 开启签到时间窗口")
        async with user.session.post(
                url=WEB_DICT["apiLog_api"],
                headers=generate_header(user, WEB_DICT['apiLog_api'])
        ) as resp:
            apiLog_result = resp
            apiLog_text = await apiLog_result.text()
        logger.debug(f"{user.username}({user.student_Id}) 开启签到时间窗口返回信息 {apiLog_text}")
        if apiLog_result.status == 200:
            logger.info(f"为 {user.username}({user.student_Id}) 开启签到时间窗口成功")
            return {'success': True, 'msg': '', 'step': step + 1}
        else:
            logger.warning(
                f"{user.username}({user.student_Id}) 开启签到时间窗口时出现问题")
            return {'success': False, 'msg': "开启签到时间窗口时出现问题", 'step': step}
    # 获取签到的指定位置
    if step == 4:
        logger.info(f"开始获取 {user.username}({user.student_Id}) 签到位置")
        url = WEB_DICT['get_location_api'].format(TASK_ID=user.taskId,date_str=datetime.now().strftime('%Y-%m-%d'))
        async with user.session.get(
                url,
                headers=generate_header(user, url)
        ) as resp:
            location_result = await resp.json()
        logger.debug(f"{user.username}({user.student_Id}) 获取签到位置返回信息 {location_result}")
        if location_result['code'] == 200:
            # 安全地获取经纬度，处理可能为None的情况
            latitude_value = location_result['data'].get('dormitoryRegisterVO', {}).get('locationLat')
            longitude_value = location_result['data'].get('dormitoryRegisterVO', {}).get('locationLng')
            
            # 检查经纬度是否为None或空字符串
            if latitude_value is not None and longitude_value is not None and latitude_value != '' and longitude_value != '':
                try:
                    user.latitude = float(latitude_value)
                    user.longitude = float(longitude_value)
                    user.room_id = location_result['data'].get('dormitoryRegisterVO', {}).get("roomId", "")
                    logger.info(f"为 {user.username}({user.student_Id}) 获取签到位置成功")
                    return {'success': True, 'msg': '', 'step': step+1}
                except (ValueError, TypeError) as e:
                    logger.warning(f"为 {user.username}({user.student_Id}) 转换经纬度失败: {e}")
                    # 使用默认位置
                    user.longitude = 118.227
                    user.latitude = 31.668
                    user.room_id = "DEFAULT"
                    logger.warning(f"已为 {user.username}({user.student_Id}) 使用默认位置")
                    return {"success": True, "msg": "", "step": step+1}
            else:
                # 经纬度为空，使用默认位置
                user.longitude = 118.227
                user.latitude = 31.668
                user.room_id = location_result['data'].get('dormitoryRegisterVO', {}).get("roomId", "DEFAULT")
                logger.warning(f"未能为 {user.username}({user.student_Id}) 获取有效签到位置，已使用默认位置托底")
                return {"success": True, "msg": "", "step": step+1}
        else:
            if (("请求未授权" in location_result.get('msg'))
                    or ("缺失身份信息" in location_result.get('msg'))
                    or ('鉴权失败' in location_result.get('msg'))):
                logger.warning(f"{user.username}({user.student_Id}) Token失效或未授权，将重试获取Token。")
                user.token = ''
                return {'success': False, 'msg': 'token失效', 'step': 0}
            else:
                # API返回错误，使用默认位置
                user.longitude = 118.227
                user.latitude = 31.668
                user.room_id = "DEFAULT"
                logger.warning(f"未能为 {user.username}({user.student_Id}) 获取签到位置，API返回错误: {location_result.get('msg')}，已使用默认位置托底")
                return {"success": True, "msg": "", "step": step+1}
    # 进行晚寝签到
    if step == 5:
        async with SIGN_IN_LOCK:
            logger.info(f"开始为 {user.username}({user.student_Id}) 晚寝签到")
            sleep_time = round(random.uniform(4,10))
            logger.debug(f"等待时间:{sleep_time}")
            await asyncio.sleep(sleep_time)
            async with user.session.post(
                    url=WEB_DICT["sign_in_api"],
                    json=generate_data(user),
                    headers=generate_header(user,WEB_DICT['sign_in_api'])
            ) as resp:
                sign_in_result = await resp.json()
            logger.debug(f"{user.username}({user.student_Id}) 晚寝签到返回信息 {sign_in_result}")
            if sign_in_result['code'] == 200 or '您今天已完成签到' in sign_in_result['msg']:
                logger.info(f"为 {user.username}({user.student_Id}) 晚寝签到成功")
                return {'success': True, 'msg': '', 'step': step + 1}
            else:
                if (("请求未授权" in sign_in_result.get('msg'))
                        or ("缺失身份信息" in sign_in_result.get('msg'))
                        or ('鉴权失败' in sign_in_result.get('msg'))):
                    logger.warning(f"{user.username}({user.student_Id}) Token失效或未授权，将重试获取Token。")
                    user.token = ''
                    return {'success': False, 'msg': 'token失效', 'step': 0}
                else:
                    if '未到签到时间！' in sign_in_result.get('msg'):
                        logger.warning(
                            f"因当前时间{get_time()['time']}未到签到时间，{user.username}({user.student_Id}) 签到失败")
                        return {'success': False, 'msg': sign_in_result.get('msg'), 'step': -1}
                    logger.warning(
                        f"{user.username}({user.student_Id}) 晚寝签到时出现问题：{sign_in_result.get('msg')}")
                    return {'success': False, 'msg': sign_in_result.get('msg'), 'step': step}

    # 未知情况或传入的step错误
    else:
        logger.debug(f"出现未知错误，当前参数为：user={user.student_Id},step={step}")
        return {'success': False, 'msg': '', 'step': -1}


async def sign_in(user: User, debug: bool = False):
    """
    为单人进行晚寝签到尝试

    :param user: 尝试晚寝签到的User对象
    :param debug: 是否为debug模式，此模式下忽略签到时间限制
    :return: {success:签到结果, data:签到过程中出现的错误}
    """
    logger.info(f"为 {user.username}({user.student_Id}) 尝试执行签到")
    step, retries, token_retries = 0, 0, 0
    error_history = set()

    while retries < MAX_RETRIES and 0 <= step < 6:
        result = await sign_in_by_step(user, step, debug)
        step = result['step']
        if not result['success']:
            error_history.add(result['msg'])
            if step == 0 and token_retries < MAX_TOKEN_RETRIES:
                token_retries += 1
            else:
                retries += 1
        # 添加随机延时，模拟手动操作
        await asyncio.sleep(round(random.uniform(0.5,2),2))

    if step == 6:
        return {'success': True, 'data': error_history}
    else:
        return {'success': False, 'data': error_history}


# 异步执行
async def main():
    """主函数，根据配置执行签到"""
    global USER_LIST
    
    # 初始化统计类
    stats = SignStatistics()
    
    # 根据签到模式生成用户列表
    if SIGN_MODE == 'all':
        USER_LIST = generate_continuous_students(SIGN_RANGE[0], SIGN_RANGE[1])
        logger.info(f"签到模式：全部学号，共 {len(USER_LIST)} 人")
        
    elif SIGN_MODE == 'success_only':
        USER_LIST = generate_user_list_from_success()
        if not USER_LIST:
            logger.warning("没有找到成功学号，请先运行测试模式")
            return {}
        logger.info(f"签到模式：仅成功学号，共 {len(USER_LIST)} 人")
        
    elif SIGN_MODE == 'random':
        USER_LIST = generate_random_sign_user_list()
        if not USER_LIST:
            logger.warning("没有找到成功学号，请先运行测试模式")
            return {}
    
    if not USER_LIST:
        logger.warning("用户列表为空，请检查配置")
        return {}
    
    start_time = time.time()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def limited_sign_in(user):
        async with semaphore:
            result = await sign_in(user, debug=False)
            
            # 更新统计
            stats.update_rank(user.student_Id, user.username, result['success'])
            stats.update_location(user.student_Id, user.latitude, user.longitude, result['success'])
            
            return result

    async_results = await asyncio.gather(
        *(limited_sign_in(u) for u in USER_LIST))
    
    await asyncio.gather(*[user.close() for user in USER_LIST])
    
    end_time = time.time()
    duration = end_time - start_time
    
    # 统计结果
    total_users = len(USER_LIST)
    results_dict = {}
    success_count = 0
    failed_count = 0
    
    for user, result in zip(USER_LIST, async_results):
        results_dict[user.student_Id] = result
        if result['success']:
            success_count += 1
        else:
            failed_count += 1
    
    # 获取模式信息
    mode_info = {
        'mode': SIGN_MODE,
        'description': get_mode_description()
    }
    
    if SIGN_MODE == 'random':
        mode_info['sample_size'] = total_users
        mode_info['total_success'] = len(load_success_students())
        mode_info['sample_ratio'] = f"{total_users/len(load_success_students())*100:.1f}%" if load_success_students() else "0%"
    
    # 更新历史记录
    stats.update_history(total_users, success_count, failed_count, duration, mode_info)
    
    # 控制台输出统计信息
    logger.info("=" * 50)
    logger.info(f"签到统计报告")
    logger.info(f"签到模式: {SIGN_MODE}")
    logger.info(f"总人数：{total_users} 人")
    logger.info(f"成功人数：{success_count} 人")
    logger.info(f"失败人数：{failed_count} 人")
    
    if total_users > 0:
        logger.info(f"成功率：{success_count/total_users*100:.2f}%")
        logger.info(f"总耗时：{duration:.2f} 秒")
    
    logger.info("=" * 50)
    
    # 显示排行榜
    top_rankers = stats.get_top_rankers(10)
    if top_rankers:
        logger.info("\n🏆 签到排行榜 TOP 10:")
        for ranker in top_rankers[:5]:
            logger.info(f"  {ranker['rank']}. {ranker['student_id']} - 成功{ranker['success_count']}次")
    
    # 发送邮件（包含所有可视化内容）
    if total_users > 0:
        try:
            send_sign_summary_email(results_dict, total_users, success_count, 
                                   failed_count, duration, mode_info)
        except Exception as e:
            logger.error(f"发送邮件失败: {e}")
    
    return results_dict

# 异步阻塞执行(串行执行)
# async def main():
#     results = {}
#     for u in USER_LIST:
#         result = await sign_in(u,debug=False) # 如需在非签到时间内测试可传入参数debug=True
#         results[u.student_Id] = result
#     return results

## *------------------------------------------------------* ##



if __name__ == '__main__':
    import sys
    
    # 检查命令行参数
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        # 测试模式：测试并保存成功学号
        print("进入测试模式...")
        asyncio.run(test_and_save_success_students())
    else:
        # 正常签到模式
        start_time = time.time()
        results = asyncio.run(main())
        end_time = time.time()
        
        if results:
            success_count = sum(1 for r in results.values() if r['success'])
            print(f"\n本次为 {len(results)} 人尝试进行签到，成功人数：{success_count}，"
                  f"本次任务总耗时 {end_time - start_time:.2f} 秒。")
            
            # 显示失败详情
            failed_students = [k for k, v in results.items() if not v['success']]
            if failed_students:
                print(f"\n签到失败的学号 ({len(failed_students)}个):")
                for student_id in failed_students[:10]:  # 只显示前10个
                    print(f"  {student_id}")
                if len(failed_students) > 10:
                    print(f"  ... 共{len(failed_students)}个失败")