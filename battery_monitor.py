"""
电池监控模块
自动检测机器人电池电量并在低电量时触发充电

功能：
- 定期检测所有已连接机器人的电池电量
- 电量低于阈值时，等待当前任务完成后前往充电
- 电量恢复后继续接收新任务

复用功能：
- robot_controller.py: subscribe_topic, get_topic_message, send_service_request
- task_state_machine.py: 获取任务状态
"""

import threading
import time
from typing import Dict, Optional, Callable
from constants import (
    ENABLE_AUTO_CHARGING,
    REQUIRE_BATTERY_INFO_ON_STARTUP,
    BATTERY_INFO_WAIT_TIMEOUT,
    BATTERY_CHECK_INTERVAL,
    BATTERY_LOW_THRESHOLD,
    BATTERY_FULL_THRESHOLD,
    BATTERY_TOPIC,
    CHARGING_STATION_POSE,
    ROSService
)
from error_logger import get_error_logger

# 尝试从外部配置加载自动充电参数
def _get_charging_config():
    """获取充电配置（优先使用外部配置）"""
    try:
        from config_loader import get_auto_charging_config
        external_config = get_auto_charging_config()
        if external_config:
            return {
                "enabled": external_config.get("enabled", ENABLE_AUTO_CHARGING),
                "require_battery_on_startup": external_config.get("require_battery_on_startup", REQUIRE_BATTERY_INFO_ON_STARTUP),
                "battery_wait_timeout": external_config.get("battery_wait_timeout", BATTERY_INFO_WAIT_TIMEOUT),
                "check_interval": external_config.get("check_interval", BATTERY_CHECK_INTERVAL),
                "low_threshold": external_config.get("low_threshold", BATTERY_LOW_THRESHOLD),
                "full_threshold": external_config.get("full_threshold", BATTERY_FULL_THRESHOLD),
            }
    except ImportError:
        pass
    except Exception:
        pass
    
    # 回退到默认常量
    return {
        "enabled": ENABLE_AUTO_CHARGING,
        "require_battery_on_startup": REQUIRE_BATTERY_INFO_ON_STARTUP,
        "battery_wait_timeout": BATTERY_INFO_WAIT_TIMEOUT,
        "check_interval": BATTERY_CHECK_INTERVAL,
        "low_threshold": BATTERY_LOW_THRESHOLD,
        "full_threshold": BATTERY_FULL_THRESHOLD,
    }

logger = get_error_logger()


class RobotBatteryState:
    """单个机器人的电池状态"""
    PENDING = "pending"         # 等待获取电量信息
    NORMAL = "normal"           # 正常工作
    LOW_BATTERY = "low_battery" # 低电量，等待任务完成
    CHARGING = "charging"       # 充电中
    
    def __init__(self, robot_id: str):
        self.robot_id = robot_id
        self.state = self.PENDING  # 初始状态为等待电量信息
        self.percentage = None  # 电池百分比，None表示未获取
        self.last_check_time = 0
        self.subscribed = False
        self.battery_info_received = False  # 是否已收到电量信息


class BatteryMonitor:
    """
    电池监控器
    
    监控所有机器人的电池状态，在低电量时触发充电流程
    
    配置优先级：
    1. 外部配置文件 robot_config.json 的 auto_charging 部分
    2. constants.py 中的默认常量
    """
    
    def __init__(self):
        self.robots: Dict = {}  # {robot_id: RobotController}
        self.battery_states: Dict[str, RobotBatteryState] = {}  # {robot_id: RobotBatteryState}
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._task_state_callback: Optional[Callable] = None  # 获取任务状态的回调
        self._battery_info_ready_event = threading.Event()  # 电量信息就绪事件
        
        # 加载配置（优先使用外部配置文件）
        self._config = _get_charging_config()
        self._enabled = self._config["enabled"]
        self._require_battery_on_startup = self._config["require_battery_on_startup"]
        self._battery_wait_timeout = self._config["battery_wait_timeout"]
        self._check_interval = self._config["check_interval"]
        self._low_threshold = self._config["low_threshold"]
        self._full_threshold = self._config["full_threshold"]
        
    def set_robots(self, robots: Dict):
        """设置要监控的机器人"""
        self.robots = robots
        # 为每个机器人创建电池状态
        for robot_id in robots.keys():
            if robot_id not in self.battery_states:
                self.battery_states[robot_id] = RobotBatteryState(robot_id)
                logger.info("电池监控", f"添加机器人 {robot_id} 到监控列表")
    
    def set_task_state_callback(self, callback: Callable):
        """设置获取任务状态的回调函数"""
        self._task_state_callback = callback
    
    def start(self):
        """启动电池监控"""
        if not self._enabled:
            logger.info("电池监控", "自动充电功能已禁用")
            print("⚡ 自动充电功能已禁用（可在robot_config.json或constants.py中启用）")
            # 即使禁用自动充电，也标记电量信息已就绪（跳过等待）
            self._battery_info_ready_event.set()
            return
        
        if self._running:
            logger.warning("电池监控", "监控器已在运行")
            return
        
        self._running = True
        self._stop_event.clear()
        self._battery_info_ready_event.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        
        logger.info("电池监控", "电池监控器已启动")
        print(f"⚡ 电池监控器已启动")
        print(f"   检测间隔: {self._check_interval}秒")
        print(f"   低电量阈值: {self._low_threshold*100:.0f}%")
        print(f"   充电完成阈值: {self._full_threshold*100:.0f}%")
        
        # 如果需要在启动时等待电量信息
        if self._require_battery_on_startup:
            print(f"⏳ 等待获取机器人电量信息...")
            self._wait_for_initial_battery_info()
    
    def stop(self):
        """停止电池监控"""
        if not self._running:
            return
        
        self._running = False
        self._stop_event.set()
        
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5)
        
        # 取消所有电池topic订阅
        for robot_id, robot in self.robots.items():
            if robot_id in self.battery_states and self.battery_states[robot_id].subscribed:
                try:
                    robot.unsubscribe_topic(BATTERY_TOPIC)
                except:
                    pass
        
        logger.info("电池监控", "电池监控器已停止")
        print("⚡ 电池监控器已停止")
    
    def _wait_for_initial_battery_info(self):
        """等待获取所有机器人的初始电量信息"""
        if not self._require_battery_on_startup:
            self._battery_info_ready_event.set()
            return
        
        logger.info("电池监控", "开始等待机器人电量信息...")
        
        timeout = self._battery_wait_timeout if self._battery_wait_timeout > 0 else None
        start_time = time.time()
        
        # 等待电量信息就绪事件
        result = self._battery_info_ready_event.wait(timeout=timeout)
        
        elapsed = time.time() - start_time
        
        if result:
            logger.info("电池监控", f"电量信息获取完成，耗时 {elapsed:.1f} 秒")
        else:
            # 超时，但仍然允许继续（标记为就绪）
            logger.warning("电池监控", f"等待电量信息超时 ({timeout}秒)，继续运行")
            print(f"⚠️ 等待电量信息超时，部分机器人可能无法获取电量")
            self._battery_info_ready_event.set()
    
    def is_battery_info_ready(self, robot_id: str = None) -> bool:
        """
        检查电量信息是否已就绪
        
        参数:
            robot_id: 指定机器人ID，为None则检查所有机器人
        
        返回:
            bool: 电量信息是否已就绪
        """
        if not self._enabled or not self._require_battery_on_startup:
            return True
        
        if robot_id:
            state = self.battery_states.get(robot_id)
            if not state:
                return True  # 未配置的机器人默认就绪
            return state.battery_info_received
        
        return self._battery_info_ready_event.is_set()
    
    def wait_for_battery_info(self, timeout: float = None) -> bool:
        """
        等待电量信息就绪
        
        参数:
            timeout: 超时时间（秒），None表示无限等待
        
        返回:
            bool: 是否成功获取到电量信息
        """
        if not self._enabled or not self._require_battery_on_startup:
            return True
        return self._battery_info_ready_event.wait(timeout=timeout)
    
    def _monitor_loop(self):
        """监控循环"""
        logger.info("电池监控", "开始监控循环")
        
        # 首次启动时订阅所有机器人的电池topic
        self._subscribe_all_battery_topics()
        
        # 如果需要等待电量信息，先快速检测几次
        if self._require_battery_on_startup and not self._battery_info_ready_event.is_set():
            logger.info("电池监控", "快速检测电量信息...")
            for _ in range(30):  # 最多检测30次，每次1秒
                if self._stop_event.is_set():
                    break
                try:
                    self._check_all_batteries()
                except Exception as e:
                    logger.exception_occurred("电池监控", "检测电池状态", e)
                
                if self._battery_info_ready_event.is_set():
                    break
                time.sleep(1)
        
        while self._running and not self._stop_event.is_set():
            try:
                self._check_all_batteries()
            except Exception as e:
                logger.exception_occurred("电池监控", "检测电池状态", e)
            
            # 等待下一次检测
            self._stop_event.wait(timeout=self._check_interval)
        
        logger.info("电池监控", "监控循环已结束")
    
    def _subscribe_all_battery_topics(self):
        """订阅所有机器人的电池topic"""
        for robot_id, robot in self.robots.items():
            if robot_id not in self.battery_states:
                self.battery_states[robot_id] = RobotBatteryState(robot_id)
            
            state = self.battery_states[robot_id]
            if not state.subscribed:
                try:
                    success = robot.subscribe_topic(
                        topic_name=BATTERY_TOPIC,
                        msg_type="sensor_msgs/BatteryState",  # 标准电池消息类型
                        throttle_rate=0,
                        queue_length=1
                    )
                    if success:
                        state.subscribed = True
                        logger.info("电池监控", f"{robot_id} 已订阅电池状态topic")
                    else:
                        logger.warning("电池监控", f"{robot_id} 订阅电池状态topic失败")
                except Exception as e:
                    logger.exception_occurred("电池监控", f"{robot_id} 订阅电池topic", e)
    
    def _check_all_batteries(self):
        """检测所有机器人的电池状态"""
        current_time = time.time()
        all_battery_received = True
        
        for robot_id, robot in self.robots.items():
            if not robot or not robot.is_connected():
                all_battery_received = False
                continue
            
            state = self.battery_states.get(robot_id)
            if not state:
                all_battery_received = False
                continue
            
            # 获取电池状态
            battery_info = robot.get_topic_message(BATTERY_TOPIC)
            if battery_info:
                # 解析电池百分比
                percentage = battery_info.get("percentage", 1.0)
                state.percentage = percentage
                state.last_check_time = current_time
                
                # 首次收到电量信息
                if not state.battery_info_received:
                    state.battery_info_received = True
                    logger.info("电池监控", f"{robot_id} 首次获取到电量信息: {percentage*100:.1f}%")
                    print(f"✅ {robot_id} 电量信息已获取: {percentage*100:.1f}%")
                else:
                    logger.info("电池监控", f"{robot_id} 电量: {percentage*100:.1f}%")
                    print(f"⚡ {robot_id} 电量: {percentage*100:.1f}%")
                
                # 根据电量状态进行处理
                self._handle_battery_state(robot_id, robot, state)
            else:
                if not state.battery_info_received:
                    all_battery_received = False
                logger.warning("电池监控", f"{robot_id} 无法获取电池状态")
        
        # 检查是否所有机器人都已获取到电量信息
        if all_battery_received and not self._battery_info_ready_event.is_set():
            self._battery_info_ready_event.set()
            logger.info("电池监控", "所有机器人电量信息已获取")
            print("✅ 所有机器人电量信息已获取，系统就绪")
    
    def _handle_battery_state(self, robot_id: str, robot, state: RobotBatteryState):
        """处理电池状态"""
        percentage = state.percentage
        
        if state.state == RobotBatteryState.PENDING:
            # 等待电量信息状态 -> 收到电量信息后判断是否需要充电
            if percentage < self._low_threshold:
                logger.warning("电池监控", f"{robot_id} 启动时电量低 ({percentage*100:.1f}%)，需要先充电")
                print(f"⚠️ {robot_id} 启动时电量低 ({percentage*100:.1f}%)，需要先充电")
                state.state = RobotBatteryState.LOW_BATTERY
                # 尝试触发充电
                self._try_start_charging(robot_id, robot, state)
            else:
                logger.info("电池监控", f"{robot_id} 电量正常 ({percentage*100:.1f}%)，可以工作")
                print(f"✅ {robot_id} 电量正常 ({percentage*100:.1f}%)，可以工作")
                state.state = RobotBatteryState.NORMAL
        
        elif state.state == RobotBatteryState.NORMAL:
            # 正常状态下检测是否低电量
            if percentage < self._low_threshold:
                logger.warning("电池监控", f"{robot_id} 电量低 ({percentage*100:.1f}%)，准备充电")
                print(f"⚠️ {robot_id} 电量低 ({percentage*100:.1f}%)，等待当前任务完成后前往充电")
                state.state = RobotBatteryState.LOW_BATTERY
                # 注意：不立即触发充电，等待当前任务完成
        
        elif state.state == RobotBatteryState.LOW_BATTERY:
            # 低电量状态，等待任务完成后再充电
            self._try_start_charging(robot_id, robot, state)
        
        elif state.state == RobotBatteryState.CHARGING:
            # 充电中，检测是否充满
            if percentage >= self._full_threshold:
                logger.info("电池监控", f"{robot_id} 充电完成 ({percentage*100:.1f}%)")
                print(f"✅ {robot_id} 充电完成 ({percentage*100:.1f}%)，恢复正常工作")
                state.state = RobotBatteryState.NORMAL
    
    def _try_start_charging(self, robot_id: str, robot, state: RobotBatteryState):
        """尝试开始充电"""
        # 检查当前是否有任务在执行
        if self._task_state_callback:
            task_state = self._task_state_callback()
            if task_state and task_state.get("is_running", False):
                logger.info("电池监控", f"{robot_id} 当前有任务执行中，等待完成")
                return
        
        # 任务已完成或无任务，前往充电
        logger.info("电池监控", f"{robot_id} 开始前往充电桩")
        print(f"🔋 {robot_id} 前往充电桩...")
        
        # 导航到充电桩
        try:
            # 使用topic发布导航命令
            result = robot.publish_topic(
                topic_name="/navigation_control",
                msg_type="std_msgs/String",
                msg_data={"data": CHARGING_STATION_POSE}
            )
            
            if result:
                state.state = RobotBatteryState.CHARGING
                logger.info("电池监控", f"{robot_id} 已到达充电桩，开始充电")
                print(f"🔋 {robot_id} 已到达充电桩，开始充电")
            else:
                logger.error("电池监控", f"{robot_id} 导航到充电桩失败")
                print(f"❌ {robot_id} 导航到充电桩失败")
        except Exception as e:
            logger.exception_occurred("电池监控", f"{robot_id} 导航到充电桩", e)
    
    def is_robot_available(self, robot_id: str) -> tuple:
        """
        检查机器人是否可用于接收新任务
        
        返回:
            tuple: (is_available, reason)
            - is_available: bool, 是否可用
            - reason: str, 不可用的原因（如果可用则为None）
        """
        if not self._enabled:
            return True, None
        
        state = self.battery_states.get(robot_id)
        if not state:
            return True, None
        
        # 检查是否还在等待电量信息
        if state.state == RobotBatteryState.PENDING:
            return False, "battery_info_pending"
        
        # 检查是否低电量或充电中
        if state.state == RobotBatteryState.LOW_BATTERY:
            return False, "low_battery"
        
        if state.state == RobotBatteryState.CHARGING:
            return False, "charging"
        
        return True, None
    
    def get_battery_status(self, robot_id: str = None) -> Dict:
        """获取电池状态"""
        if robot_id:
            state = self.battery_states.get(robot_id)
            if state:
                available, reason = self.is_robot_available(robot_id)
                return {
                    "robot_id": robot_id,
                    "percentage": state.percentage,
                    "state": state.state,
                    "battery_info_received": state.battery_info_received,
                    "available": available,
                    "unavailable_reason": reason
                }
            return None
        
        # 返回所有机器人的电池状态
        result = {}
        for robot_id, state in self.battery_states.items():
            available, reason = self.is_robot_available(robot_id)
            result[robot_id] = {
                "percentage": state.percentage,
                "state": state.state,
                "battery_info_received": state.battery_info_received,
                "available": available,
                "unavailable_reason": reason
            }
        return result


# 全局电池监控器实例
_battery_monitor: Optional[BatteryMonitor] = None


def init_battery_monitor() -> BatteryMonitor:
    """初始化电池监控器"""
    global _battery_monitor
    _battery_monitor = BatteryMonitor()
    return _battery_monitor


def get_battery_monitor() -> Optional[BatteryMonitor]:
    """获取电池监控器实例"""
    return _battery_monitor


def is_robot_available_for_task(robot_id: str) -> tuple:
    """
    检查机器人是否可用于接收新任务
    
    返回:
        tuple: (is_available, reason)
    """
    if _battery_monitor:
        return _battery_monitor.is_robot_available(robot_id)
    return True, None


def is_battery_info_ready(robot_id: str = None) -> bool:
    """
    检查电量信息是否已就绪
    
    参数:
        robot_id: 指定机器人ID，为None则检查所有机器人
    """
    if _battery_monitor:
        return _battery_monitor.is_battery_info_ready(robot_id)
    return True


def wait_for_battery_info(timeout: float = None) -> bool:
    """
    等待电量信息就绪
    
    参数:
        timeout: 超时时间（秒），None表示无限等待
    """
    if _battery_monitor:
        return _battery_monitor.wait_for_battery_info(timeout)
    return True

