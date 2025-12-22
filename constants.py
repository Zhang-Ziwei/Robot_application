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

# 后部暂存区默认配置
DEFAULT_BACK_TEMP_STORAGE = {
    "glass_bottle_1000": [0, 0, 0, 0],
    "glass_bottle_500": [0, 0, 0, 0, 0, 0, 0, 0],
    "glass_bottle_250": [0, 0, 0, 0]
}

# 存储状态文件路径
STORAGE_STATE_FILE = "storage_state.json"
