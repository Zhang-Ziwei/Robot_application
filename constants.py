# PLC保持寄存器地址映射
class PLCHoldingRegisters:
    OPEN_LID_STATE = 0       # 4001: 0-未就绪,1-准备就绪,2-工作中,3-工作完成
    CLEAN_STATE = 1          # 4002: 0-未就绪,1-准备就绪,2-工作中,3-工作完成
    DETECT_STATE = 2         # 4003: 0-未就绪,1-准备就绪,2-放料位移完成,3-工作中,4-请求取走第一个样品,5-请求取走第二个样品
    CLOSE_LID_STATE = 3      # 4004: 0-未就绪,1-准备就绪,2-工作中,3-工作完成
    HOLDING_REG_COUNT = 4

# PLC线圈地址映射
class PLCCoils:
    OPEN_START = 0           # 1: 开盖启动（状态1置位→状态2复位）
    OPEN_FINISH = 1          # 2: 开盖完成后取瓶（状态3置位→状态1复位）
    CLOSE_START = 2          # 3: 关盖启动（状态1置位→状态2复位）
    CLOSE_FINISH = 3         # 4: 关盖完成后取瓶（状态3置位→状态1复位）
    DETECT_DISPENSE = 4      # 5: 检测放料位移（状态1置位→状态2复位）
    DETECT_START = 5         # 6: 检测启动（状态2置位→状态3复位）
    DETECT_PICK = 6          # 7: 检测取料位移（状态4置位→状态5复位）
    DETECT_FINISH = 7        # 8: 检测取料完成（状态5置位→状态1复位）
    CLEAN_START = 8          # 9: 清洗启动（状态1置位→状态2复位）
    CLEAN_FINISH = 9         # 10: 清洗取料完成（状态3置位→状态1复位）
    COIL_COUNT = 16

# 机器人类型
class RobotType:
    ROBOT_A = "robot_a"
    ROBOT_B = "robot_b"
    ROBOT_C = "robot_c"

# ROS服务名称
class ROSService:
    # 机器人A主服务
    STRAWBERRY_SERVICE = "/get_strawberry_service"
    # 机器人B主服务
    CHEM_PROJECT_SERVICE = "/chem_project_service"
    # 导航状态（所有机器人通用）
    NAVIGATION_STATUS = "/navigation_status"

# 机器人B ROS服务名称（固定不变的特殊服务）
class ROSServiceRobotB:
    HALFBODY_CHEMICAL_SERVICE = "/get_halfbodychemical_service"

# 机器人C ROS服务名称（本地部署示例）
class ROSServiceRobotC:
    MAIN_SERVICE = "/robot_c_main_service"

# 机器人类型与主ROS服务的映射关系
# - robot_a 使用 STRAWBERRY_SERVICE
# - robot_b 使用 CHEM_PROJECT_SERVICE
# - 导航状态 NAVIGATION_STATUS 所有机器人通用
ROBOT_MAIN_SERVICE_MAP = {
    RobotType.ROBOT_A: ROSService.STRAWBERRY_SERVICE,
    RobotType.ROBOT_B: ROSService.CHEM_PROJECT_SERVICE,
    "robot_a": ROSService.STRAWBERRY_SERVICE,
    "robot_b": ROSService.CHEM_PROJECT_SERVICE,
}

def get_main_ros_service(robot_id: str) -> str:
    """
    根据机器人ID获取对应的主ROS服务名称
    
    参数:
        robot_id: 机器人ID (如 "robot_a", "robot_b")
    
    返回:
        对应的ROS服务名称
        
    示例:
        get_main_ros_service("robot_a") -> "/get_strawberry_service"
        get_main_ros_service("robot_b") -> "/chem_project_service"
    """
    return ROBOT_MAIN_SERVICE_MAP.get(robot_id, ROSService.STRAWBERRY_SERVICE)
    
# 机器人配置
# 
# 配置优先级：
# 1. 外部配置文件 /config/robot_config.json (Docker挂载目录，最高优先级)
# 2. 外部配置文件 ./robot_config.json (当前目录)
# 3. 以下默认配置 DEFAULT_ROBOT_CONFIGS (最低优先级)
#
# 使用方法：
#   from constants import get_robot_configs
#   configs = get_robot_configs()  # 自动加载外部配置或使用默认配置
#
# 默认配置（当没有外部配置文件时使用）
DEFAULT_ROBOT_CONFIGS = {
    "robot_b": {
        "host": "0.0.0.0",
        "port": "9091",
        "robot_type": RobotType.ROBOT_B
    }
}

def get_robot_configs():
    """
    获取机器人配置（优先使用外部配置文件）
    
    返回:
        机器人配置字典
    """
    try:
        from config_loader import get_robot_configs as load_external_configs
        external_configs = load_external_configs()
        if external_configs:
            # 将外部配置转换为内部格式（添加robot_type对象）
            result = {}
            for robot_id, config in external_configs.items():
                robot_type_str = config.get("robot_type", robot_id)
                # 映射robot_type字符串到RobotType类属性
                robot_type = getattr(RobotType, robot_type_str.upper(), robot_type_str)
                result[robot_id] = {
                    "host": config.get("host"),
                    "port": config.get("port"),
                    "robot_type": robot_type
                }
            return result
    except ImportError:
        pass
    except Exception:
        pass
    
    return DEFAULT_ROBOT_CONFIGS

# 兼容旧代码：直接使用ROBOT_CONFIGS变量（静态默认配置）
ROBOT_CONFIGS = DEFAULT_ROBOT_CONFIGS

# 模块名称映射
MODULE_NAMES = [
    "Open Lid Module", 
    "Clean Module", 
    "Detect Module", 
    "Close Lid Module"
]

# Modbus配置
MODBUS_PORT = 502  # 使用非特权端口避免权限问题

# HTTP服务器配置
HTTP_SERVER_PORT = 8090  # HTTP服务器端口（注意：8081被Docker容器占用）

# 机器人B单独动作接口开关
# 设置为True时，可通过HTTP接口单独调用机器人B的各个动作
ENABLE_ROBOT_B_ACTIONS = True

# 自动充电功能开关
ENABLE_AUTO_CHARGING = True
# 启动时等待电量信息功能开关
# 开启后，系统启动时会等待获取到所有机器人的电量信息后才允许执行任务
REQUIRE_BATTERY_INFO_ON_STARTUP = True
# 启动时等待电量信息的超时时间（秒），0表示无限等待
BATTERY_INFO_WAIT_TIMEOUT = 60
# 电池电量检测间隔（秒）
BATTERY_CHECK_INTERVAL = 600  # 10分钟
# 低电量阈值（低于此值触发充电）
BATTERY_LOW_THRESHOLD = 0.30  # 30%
# 充电完成阈值（高于此值恢复工作）
BATTERY_FULL_THRESHOLD = 0.80  # 80%
# 电池状态 topic
BATTERY_TOPIC = "/zj_humanoid/robot/battery_info"
# 充电桩导航点位
CHARGING_STATION_POSE = "charging_station"

# 后部暂存区默认配置
# 槽位格式: 0 表示空, {"bottle_id": "xxx", "bottle_state": "..."} 表示有瓶子
# 每个机器人的储位配置可能不同

# 机器人A的储位配置
ROBOT_A_BACK_TEMP_STORAGE = {
    "glass_bottle_1000": [0, 0, 0, 0],
    "glass_bottle_500": [0, 0, 0, 0, 0, 0, 0, 0],
    "glass_bottle_250": [0, 0, 0, 0]
}

# 机器人B的储位配置
ROBOT_B_BACK_TEMP_STORAGE = {
    "glass_bottle_1000": [0],
    "glass_bottle_500": [0, 0, 0, 0],
    "glass_bottle_250": [0, 0, 0, 0]
}

# 机器人C的储位配置（示例）
ROBOT_C_BACK_TEMP_STORAGE = {
    "glass_bottle_1000": [0, 0, 0, 0],
    "glass_bottle_500": [0, 0, 0, 0, 0, 0],
    "glass_bottle_250": [0, 0, 0, 0]
}

# 机器人储位配置映射
ROBOT_STORAGE_CONFIGS = {
    RobotType.ROBOT_A: ROBOT_A_BACK_TEMP_STORAGE,
    RobotType.ROBOT_B: ROBOT_B_BACK_TEMP_STORAGE,
    RobotType.ROBOT_C: ROBOT_C_BACK_TEMP_STORAGE,
    "robot_a": ROBOT_A_BACK_TEMP_STORAGE,
    "robot_b": ROBOT_B_BACK_TEMP_STORAGE,
    "robot_c": ROBOT_C_BACK_TEMP_STORAGE,
}

# 默认储位配置（兼容旧代码）
DEFAULT_BACK_TEMP_STORAGE = ROBOT_A_BACK_TEMP_STORAGE

def get_robot_storage_config(robot_id: str) -> dict:
    """
    获取指定机器人的储位配置
    
    参数:
        robot_id: 机器人ID (如 "robot_a", "robot_b")
    
    返回:
        储位配置字典
    """
    import copy
    config = ROBOT_STORAGE_CONFIGS.get(robot_id, DEFAULT_BACK_TEMP_STORAGE)
    return copy.deepcopy(config)

# 瓶子状态
class BottleState:
    NOT_SPLIT = "未分液"
    SPLIT_DONE = "已分液"

# 暂存区区域名称常量
class StationArea:
    WAITING_SPLIT_AREA = "waiting_split_area"           # 分液台待分液区
    SPLIT_DONE_250ML_AREA = "split_done_250ml_area"     # 250ml分液完成暂存区
    SPLIT_DONE_500ML_AREA = "split_done_500ml_area"     # 500ml分液完成暂存区
    EMPTY_BOTTLE_AREA = "empty_bottle_area"             # 空瓶区

# 导航点位常量
class NavigationPose:
    SCAN_TABLE = "scan_table"                                   # 扫描台
    WAITING_SPLIT_AREA_TRANSFER = "waiting_split_area_transfer"                   # 分液台待分液区（转运任务点位）
    SPLIT_DONE_250ML_AREA_TRANSFER = "250ml_split_done_area_transfer"             # 250ml分液完成暂存区（转运任务点位）
    SPLIT_DONE_500ML_AREA_TRANSFER = "500ml_split_done_area_transfer"             # 500ml分液完成暂存区（转运任务点位）
    WAITING_SPLIT_AREA_SPLIT = "waiting_split_area_split"                   # 分液台待分液区（分液任务点位）
    EMPTY_BOTTLE_AREA_SPLIT = "empty_bottle_area_split"                     # 空瓶区（分液任务点位）
    SPLIT_DONE_250ML_AREA_SPLIT = "250ml_split_done_area_split"             # 250ml分液完成暂存区（分液任务点位）
    SPLIT_DONE_500ML_AREA_SPLIT = "500ml_split_done_area_split"             # 500ml分液完成暂存区（分液任务点位）
    CHROMATOGRAPH = "chromatograph"                             # 色谱仪

# 存储状态文件路径
STORAGE_STATE_FILE = "storage_state.json"

# ============================================================
# HTTP API 错误码定义
# ============================================================
# 错误码格式说明：
# - 0: 成功
# - 1xxx: 请求错误 (Request Errors)
# - 2xxx: 机器人相关错误 (Robot Errors)
# - 3xxx: 任务相关错误 (Task Errors)
# - 4xxx: 系统错误 (System Errors)
# - 5xxx: 资源错误 (Resource Errors)

class ErrorCode:
    """HTTP API 错误码"""
    
    # 成功
    SUCCESS = 0
    
    # 1xxx: 请求错误
    INVALID_JSON = 1001              # JSON解析错误
    MISSING_PARAMS = 1002            # 缺少必要参数
    INVALID_PARAMS = 1003            # 参数格式错误
    UNKNOWN_CMD_TYPE = 1004          # 未知命令类型
    
    # 2xxx: 机器人相关错误
    ROBOT_NOT_FOUND = 2001           # 机器人不存在
    ROBOT_NOT_CONNECTED = 2002       # 机器人未连接
    ROBOT_BUSY = 2003                # 机器人正忙
    ROBOT_ACTION_DISABLED = 2004     # 机器人动作接口未启用
    ROBOT_ACTION_FAILED = 2005       # 机器人动作执行失败
    ROBOT_SERVICE_ERROR = 2006       # 机器人服务调用失败
    ROBOT_BATTERY_INFO_PENDING = 2007  # 电量信息未获取
    ROBOT_LOW_BATTERY = 2008         # 机器人低电量
    
    # 3xxx: 任务相关错误
    TASK_NOT_FOUND = 3001            # 任务不存在
    TASK_ID_MISMATCH = 3002          # 任务ID不匹配
    TASK_QUEUE_DISABLED = 3003       # 任务队列未启用
    TASK_TIMEOUT = 3004              # 任务超时
    TASK_CANCELLED = 3005            # 任务已取消
    TASK_FAILED = 3006               # 任务执行失败
    
    # 4xxx: 系统错误
    HANDLER_NOT_INIT = 4001          # 命令处理器未初始化
    SYSTEM_RESET_FAILED = 4002       # 系统重置失败
    CMD_EXECUTION_ERROR = 4003       # 命令执行异常
    INTERNAL_ERROR = 4004            # 内部错误
    
    # 5xxx: 资源错误
    RESOURCE_INSUFFICIENT = 5001     # 资源不足（如瓶子不足）
    STORAGE_FULL = 5002              # 存储区已满
    RESOURCE_NOT_FOUND = 5003        # 资源未找到


# 错误码描述映射
ERROR_MESSAGES = {
    ErrorCode.SUCCESS: "成功",
    
    # 请求错误
    ErrorCode.INVALID_JSON: "JSON格式错误",
    ErrorCode.MISSING_PARAMS: "缺少必要参数",
    ErrorCode.INVALID_PARAMS: "参数格式错误",
    ErrorCode.UNKNOWN_CMD_TYPE: "未知命令类型",
    
    # 机器人错误
    ErrorCode.ROBOT_NOT_FOUND: "机器人不存在",
    ErrorCode.ROBOT_NOT_CONNECTED: "机器人未连接",
    ErrorCode.ROBOT_BUSY: "机器人正忙",
    ErrorCode.ROBOT_ACTION_DISABLED: "机器人动作接口未启用",
    ErrorCode.ROBOT_ACTION_FAILED: "机器人动作执行失败",
    ErrorCode.ROBOT_SERVICE_ERROR: "机器人服务调用失败",
    ErrorCode.ROBOT_BATTERY_INFO_PENDING: "机器人电量信息未获取，请等待",
    ErrorCode.ROBOT_LOW_BATTERY: "机器人电量低，正在充电",
    
    # 任务错误
    ErrorCode.TASK_NOT_FOUND: "任务不存在",
    ErrorCode.TASK_ID_MISMATCH: "任务ID不匹配",
    ErrorCode.TASK_QUEUE_DISABLED: "任务队列未启用",
    ErrorCode.TASK_TIMEOUT: "任务超时",
    ErrorCode.TASK_CANCELLED: "任务已取消",
    ErrorCode.TASK_FAILED: "任务执行失败",
    
    # 系统错误
    ErrorCode.HANDLER_NOT_INIT: "命令处理器未初始化",
    ErrorCode.SYSTEM_RESET_FAILED: "系统重置失败",
    ErrorCode.CMD_EXECUTION_ERROR: "命令执行异常",
    ErrorCode.INTERNAL_ERROR: "内部错误",
    
    # 资源错误
    ErrorCode.RESOURCE_INSUFFICIENT: "资源不足",
    ErrorCode.STORAGE_FULL: "存储区已满",
    ErrorCode.RESOURCE_NOT_FOUND: "资源未找到",
}

# 业务错误码到HTTP状态码的映射
# HTTP状态码说明：
# - 200 OK: 请求成功
# - 400 Bad Request: 请求参数错误
# - 404 Not Found: 资源未找到
# - 409 Conflict: 资源冲突（如机器人正忙）
# - 422 Unprocessable Entity: 请求格式正确但无法处理（如资源不足）
# - 500 Internal Server Error: 服务器内部错误
# - 503 Service Unavailable: 服务不可用
ERROR_CODE_TO_HTTP_STATUS = {
    ErrorCode.SUCCESS: 200,
    
    # 1xxx 请求错误 -> 400 Bad Request
    ErrorCode.INVALID_JSON: 400,
    ErrorCode.MISSING_PARAMS: 400,
    ErrorCode.INVALID_PARAMS: 400,
    ErrorCode.UNKNOWN_CMD_TYPE: 400,
    
    # 2xxx 机器人错误
    ErrorCode.ROBOT_NOT_FOUND: 404,           # 404 Not Found
    ErrorCode.ROBOT_NOT_CONNECTED: 503,       # 503 Service Unavailable
    ErrorCode.ROBOT_BUSY: 409,                # 409 Conflict
    ErrorCode.ROBOT_ACTION_DISABLED: 503,     # 503 Service Unavailable
    ErrorCode.ROBOT_ACTION_FAILED: 500,       # 500 Internal Server Error
    ErrorCode.ROBOT_SERVICE_ERROR: 502,       # 502 Bad Gateway
    ErrorCode.ROBOT_BATTERY_INFO_PENDING: 503,  # 503 Service Unavailable
    ErrorCode.ROBOT_LOW_BATTERY: 503,         # 503 Service Unavailable
    
    # 3xxx 任务错误
    ErrorCode.TASK_NOT_FOUND: 404,            # 404 Not Found
    ErrorCode.TASK_ID_MISMATCH: 404,          # 404 Not Found
    ErrorCode.TASK_QUEUE_DISABLED: 503,       # 503 Service Unavailable
    ErrorCode.TASK_TIMEOUT: 408,              # 408 Request Timeout
    ErrorCode.TASK_CANCELLED: 410,            # 410 Gone
    ErrorCode.TASK_FAILED: 500,               # 500 Internal Server Error
    
    # 4xxx 系统错误 -> 500 Internal Server Error
    ErrorCode.HANDLER_NOT_INIT: 503,          # 503 Service Unavailable
    ErrorCode.SYSTEM_RESET_FAILED: 500,
    ErrorCode.CMD_EXECUTION_ERROR: 500,
    ErrorCode.INTERNAL_ERROR: 500,
    
    # 5xxx 资源错误
    ErrorCode.RESOURCE_INSUFFICIENT: 422,     # 422 Unprocessable Entity
    ErrorCode.STORAGE_FULL: 422,              # 422 Unprocessable Entity
    ErrorCode.RESOURCE_NOT_FOUND: 404,        # 404 Not Found
}


def get_http_status(error_code: int) -> int:
    """
    根据业务错误码获取对应的HTTP状态码
    
    参数:
        error_code: 业务错误码
    
    返回:
        HTTP状态码
    """
    return ERROR_CODE_TO_HTTP_STATUS.get(error_code, 500)


def make_error_response(error_code: int, message: str = None, cmd_id: str = None, **extra_data) -> dict:
    """
    生成标准错误响应
    
    参数:
        error_code: 错误码
        message: 错误消息（可选，不提供则使用默认消息）
        cmd_id: 命令ID（可选）
        **extra_data: 额外数据字段
    
    返回:
        标准格式的响应字典（包含 _http_status 字段用于HTTP服务器设置状态码）
    """
    response = {
        "success": error_code == ErrorCode.SUCCESS,
        "code": error_code,
        "message": message or ERROR_MESSAGES.get(error_code, "未知错误"),
        "_http_status": get_http_status(error_code)  # 内部字段，用于HTTP服务器
    }
    if cmd_id:
        response["cmd_id"] = cmd_id
    response.update(extra_data)
    return response


def make_success_response(message: str = "操作成功", cmd_id: str = None, **extra_data) -> dict:
    """
    生成标准成功响应
    
    参数:
        message: 成功消息
        cmd_id: 命令ID（可选）
        **extra_data: 额外数据字段
    
    返回:
        标准格式的响应字典
    """
    response = {
        "success": True,
        "code": ErrorCode.SUCCESS,
        "message": message,
        "_http_status": 200
    }
    if cmd_id:
        response["cmd_id"] = cmd_id
    response.update(extra_data)
    return response
