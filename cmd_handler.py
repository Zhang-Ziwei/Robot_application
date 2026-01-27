"""
命令处理器模块
处理各种CMD_TYPES的具体执行逻辑
"""
import time
import threading
import uuid
from typing import Dict, List, Any, Optional
from robot_controller import RobotController
from bottle_manager import get_bottle_manager
from task_optimizer import get_task_optimizer
from error_logger import get_error_logger
from storage_manager import get_storage_manager, save_back_temp_storage
from constants import (
    HTTP_SERVER_PORT, BottleState, ROSService, ENABLE_ROBOT_B_ACTIONS, 
    get_main_ros_service, StationArea, NavigationPose,
    ErrorCode, make_error_response, make_success_response
)
from task_state_machine import get_task_state_machine, TaskStep, TaskStatus
from robot_actions import handle_robot_action_command, is_robot_actions_enabled, get_available_actions
from station_counter import get_station_counter, StationCounter
from battery_monitor import get_battery_monitor, is_robot_available_for_task

logger = get_error_logger()

class CmdHandler:
    """命令处理器"""
    
    def __init__(self, robots: Dict[str, RobotController] = None):
        """
        初始化命令处理器
        
        参数:
            robots: 机器人字典，key为robot_id，value为RobotController实例
        """
        self.robots = robots or {}
        # 为了向后兼容，保留robot_a和robot_b引用
        self.robot_a = self.robots.get("robot_a")
        self.robot_b = self.robots.get("robot_b")
        self.bottle_manager = get_bottle_manager()
        self.task_optimizer = get_task_optimizer()
        # 等待SCAN_QRCODE_ENTER_ID的事件和数据
        self.scan_enter_id_event = threading.Event()
        self.scan_enter_id_data = None
        # 等待START_WORKING命令的事件
        self.start_working_event = threading.Event()
        # 系统重置事件（用于通知main.py停止当前操作）
        self.reset_system_event = threading.Event()
        # 等待PICK_FROM_BOTTLE_OPENER_500命令的事件和数据
        self.pick_from_opener_500_event = threading.Event()
        self.pick_from_opener_500_data = None
        # 等待PICK_FROM_BOTTLE_OPENER_250命令的事件和数据
        self.pick_from_opener_250_event = threading.Event()
        self.pick_from_opener_250_data = None
        # 状态机
        self.task_state_machine = get_task_state_machine()
    
    def get_robot(self, robot_id: str) -> Optional[RobotController]:
        """
        根据robot_id获取机器人实例
        
        参数:
            robot_id: 机器人ID (如 "robot_a", "robot_b")
        
        返回:
            RobotController实例，如果不存在则返回None
        """
        return self.robots.get(robot_id)
    
    def get_ros_service(self, robot_id: str) -> str:
        """
        根据机器人ID获取对应的主ROS服务名称
        
        参数:
            robot_id: 机器人ID (如 "robot_a", "robot_b")
        
        返回:
            对应的ROS服务名称
            - robot_a -> STRAWBERRY_SERVICE
            - robot_b -> CHEM_PROJECT_SERVICE
        """
        return get_main_ros_service(robot_id)
    
    def is_robot_busy(self, robot_id: str = None) -> bool:
        """
        检查机器人是否正在执行任务
        
        参数:
            robot_id: 机器人ID，如果为None则检查当前任务状态
        
        返回:
            True: 机器人正忙（正在执行任务）
            False: 机器人空闲
        """
        state = self.task_state_machine.get_state()
        current_status = state.get("status")
        current_robot = state.get("robot_id")
        
        # 如果指定了robot_id，只检查该机器人
        if robot_id and current_robot and current_robot != robot_id:
            return False  # 当前任务不是这个机器人在执行
        
        # 检查是否处于运行中或等待中状态
        return current_status in [TaskStatus.RUNNING.value, TaskStatus.WAITING.value]
    
    def check_battery_availability(self, robot_id: str) -> dict:
        """
        检查机器人电量状态是否允许执行任务
        
        参数:
            robot_id: 机器人ID
        
        返回:
            dict: 如果可用返回None，否则返回错误响应
        """
        available, reason = is_robot_available_for_task(robot_id)
        
        if available:
            return None
        
        battery_monitor = get_battery_monitor()
        battery_status = battery_monitor.get_battery_status(robot_id) if battery_monitor else None
        
        if reason == "battery_info_pending":
            return make_error_response(
                ErrorCode.ROBOT_BATTERY_INFO_PENDING,
                f"机器人 {robot_id} 电量信息未获取，请等待系统初始化完成",
                robot_id=robot_id,
                battery_status=battery_status
            )
        elif reason == "low_battery":
            percentage = battery_status.get("percentage", 0) if battery_status else 0
            return make_error_response(
                ErrorCode.ROBOT_LOW_BATTERY,
                f"机器人 {robot_id} 电量低 ({percentage*100:.1f}%)，等待充电完成",
                robot_id=robot_id,
                battery_status=battery_status
            )
        elif reason == "charging":
            percentage = battery_status.get("percentage", 0) if battery_status else 0
            return make_error_response(
                ErrorCode.ROBOT_LOW_BATTERY,
                f"机器人 {robot_id} 正在充电 ({percentage*100:.1f}%)，请等待充电完成",
                robot_id=robot_id,
                battery_status=battery_status
            )
        
        return None
    
    def handle_command(self, cmd_data: Dict) -> Dict:
        """
        处理命令
        
        参数:
            cmd_data: HTTP JSON消息
        
        返回:
            处理结果
        """
        cmd_type = cmd_data.get("cmd_type")
        cmd_id = cmd_data.get("cmd_id")
        
        # GET_TASK_STATE 命令不打印日志，避免刷屏
        if cmd_type != "GET_TASK_STATE":
            logger.info("命令处理器", f"收到命令: {cmd_type} (ID: {cmd_id})")
        
        # 根据cmd_type分发到对应的处理函数
        handlers = {
            "START_WORKING": self.handle_start_working,
            "RESET_SYSTEM": self.handle_reset_system,
            "PICK_UP": self.handle_pickup,
            "PUT_TO": self.handle_put_to,
            "TAKE_BOTTOL_FROM_SP_TO_SP": self.handle_transfer,
            "SCAN_QRCODE": self.handle_scan_qrcode,
            "SCAN_QRCODE_ENTER_ID": self.handle_scan_qrcode_enter_id,
            "GET_TASK_STATE": self.handle_get_task_state,
            "ENTER_ID": self.handle_enter_id,
            "BOTTLE_GET": self.handle_bottle_get,
            "SPLIT_LIQUID": self.handle_split_liquid,
            "PICK_FROM_BOTTLE_OPENER_500": self.handle_pick_from_bottle_opener_500,
            "PICK_FROM_BOTTLE_OPENER_250": self.handle_pick_from_bottle_opener_250,
            "ROBOT_ACTION": self.handle_robot_b_action,
            "REFILL_EMPTY_BOTTLES": self.handle_refill_empty_bottles,
            "GET_STATION_COUNTER": self.handle_get_station_counter,
            "TRANSFER_TO_CHROMATOGRAPH": self.handle_transfer_to_chromatograph,
        }
        
        handler = handlers.get(cmd_type)
        if not handler:
            error_msg = f"未知的命令类型: {cmd_type}"
            logger.error("命令处理器", error_msg)
            return make_error_response(ErrorCode.UNKNOWN_CMD_TYPE, error_msg, cmd_id=cmd_id)
        
        try:
            result = handler(cmd_data)
            result["cmd_id"] = cmd_id
            # 确保所有响应都包含code字段
            if "code" not in result:
                result["code"] = ErrorCode.SUCCESS if result.get("success") else ErrorCode.INTERNAL_ERROR
            return result
        except Exception as e:
            logger.exception_occurred("命令处理器", f"处理命令{cmd_type}", e)
            return make_error_response(ErrorCode.CMD_EXECUTION_ERROR, f"命令执行异常: {str(e)}", cmd_id=cmd_id)
    
    def handle_pickup(self, cmd_data: Dict) -> Dict:
        """
        处理PICK_UP命令
        拿取东西到平台
        """
        params = cmd_data.get("params", {})
        target_params = params.get("target_params", [])
        timeout = params.get("timeout", 10.0)
        
        # 提取bottle_id列表
        bottle_ids = [item["bottle_id"] for item in target_params]
        logger.info("命令处理器", f"PICK_UP - 瓶子数量: {len(bottle_ids)}")
        
        # 任务优化
        task_list, failed_bottles = self.task_optimizer.optimize_pickup_task(bottle_ids)
        
        if failed_bottles:
            logger.warning("命令处理器", f"以下瓶子无法拾取: {failed_bottles}")
        
        # 执行任务
        success_count = 0
        for nav_pose, bottles in task_list.items():
            # 等待导航状态
            self.robot_a.send_service_request(ROSService.NAVIGATION_STATUS, "waiting_navigation_status")
            
            # 导航到目标点位
            result = self.robot_a.send_service_request(
                ROSService.NAVIGATION_STATUS, 
                "navigation_to_pose",
                extra_params={"navigation_pose": nav_pose}
            )
            
            if not result:
                logger.error("命令处理器", f"导航失败: {nav_pose}")
                continue
            
            # 对每个瓶子执行拾取操作
            for bottle_id in bottles:
                bottle = self.bottle_manager.get_bottle(bottle_id)
                if not bottle:
                    continue
                
                # 1. 抓取物体
                grab_result = self.robot_a.send_service_request(
                    self.robot_a.get_robot_service(),
                    "grab_object",
                    extra_params={
                        "strawberry": {
                            "type": bottle.object_type,
                            "target_pose": bottle.target_pose,
                            "hand": bottle.hand
                        }
                    }
                )
                
                if not grab_result:
                    logger.error("命令处理器", f"抓取失败: {bottle_id}")
                    continue
                
                # 2. 转腰到背面
                self.robot_a.send_service_request(
                    self.robot_a.get_robot_service(),
                    "turn_waist",
                    extra_params={
                        "angle": "180",
                        "obstacle_avoidance": True
                    }
                )
                
                # 3. 放置到后部平台
                # 确定后部平台的放置点位
                back_pose = f"back_temp_{bottle.object_type.split('_')[-1]}_00{success_count + 1}"
                
                put_result = self.robot_a.send_service_request(
                    self.robot_a.get_robot_service(),
                    "put_object",
                    extra_params={
                        "strawberry": {
                            "type": bottle.object_type,
                            "target_pose": back_pose,
                            "hand": bottle.hand,
                            "safe_pose": "preset"
                        }
                    }
                )
                
                if put_result:
                    # 更新瓶子位置
                    self.bottle_manager.place_bottle(bottle_id, back_pose)
                    success_count += 1
                
                # 4. 转回正面
                self.robot_a.send_service_request(
                    self.robot_a.get_robot_service(),
                    "turn_waist",
                    extra_params={
                        "angle": "0",
                        "obstacle_avoidance": True
                    }
                )
        
        return {
            "success": True,
            "message": f"PICK_UP完成",
            "success_count": success_count,
            "failed_bottles": failed_bottles,
            "total": len(bottle_ids)
        }
    
    def handle_put_to(self, cmd_data: Dict) -> Dict:
        """
        处理PUT_TO命令
        放下东西到某个地方
        """
        params = cmd_data.get("params", {})
        release_params = params.get("release_params", [])
        
        logger.info("命令处理器", f"PUT_TO - 数量: {len(release_params)}")
        
        # 任务优化
        task_list, failed_bottles = self.task_optimizer.optimize_put_task(release_params)
        
        # 执行任务
        success_count = 0
        for nav_pose, items in task_list.items():
            # 等待导航状态
            self.robot_a.send_service_request(ROSService.NAVIGATION_STATUS, "waiting_navigation_status")
            
            # 导航到目标点位
            result = self.robot_a.send_service_request(
                ROSService.NAVIGATION_STATUS,
                "navigation_to_pose",
                extra_params={"navigation_pose": nav_pose}
            )
            
            if not result:
                logger.error("命令处理器", f"导航失败: {nav_pose}")
                continue
            
            # 对每个瓶子执行放置操作
            for bottle_id, release_pose in items:
                bottle = self.bottle_manager.get_bottle(bottle_id)
                if not bottle:
                    continue
                
                # 1. 转腰到背面
                self.robot_a.send_service_request(
                    self.robot_a.get_robot_service(),
                    "turn_waist",
                    extra_params={
                        "angle": "180",
                        "obstacle_avoidance": True
                    }
                )
                
                # 2. 从后部平台抓取
                grab_result = self.robot_a.send_service_request(
                    self.robot_a.get_robot_service(),
                    "grab_object",
                    extra_params={
                        "strawberry": {
                            "type": bottle.object_type,
                            "target_pose": bottle.location or f"back_temp_{bottle.object_type.split('_')[-1]}_001",
                            "hand": bottle.hand
                        }
                    }
                )
                
                if not grab_result:
                    logger.error("命令处理器", f"抓取失败: {bottle_id}")
                    continue
                
                # 3. 转回正面
                self.robot_a.send_service_request(
                    self.robot_a.get_robot_service(),
                    "turn_waist",
                    extra_params={
                        "angle": "0",
                        "obstacle_avoidance": True
                    }
                )
                
                # 4. 放置到目标点位
                put_result = self.robot_a.send_service_request(
                    self.robot_a.get_robot_service(),
                    "put_object",
                    extra_params={
                        "strawberry": {
                            "type": bottle.object_type,
                            "target_pose": release_pose,
                            "hand": bottle.hand,
                            "safe_pose": "preset"
                        }
                    }
                )
                
                if put_result:
                    # 更新瓶子位置
                    self.bottle_manager.remove_bottle_from_pose(bottle_id, bottle.location)
                    self.bottle_manager.place_bottle(bottle_id, release_pose)
                    success_count += 1
        
        return {
            "success": True,
            "message": "PUT_TO完成",
            "success_count": success_count,
            "failed_bottles": failed_bottles,
            "total": len(release_params)
        }
    
    def handle_transfer(self, cmd_data: Dict) -> Dict:
        """
        处理TAKE_BOTTLE_FROM_SP_TO_SP命令
        把样品瓶从某处拿到某处
        """
        params = cmd_data.get("params", {})
        target_params = params.get("target_params", [])
        release_params = params.get("release_params", [])
        
        logger.info("命令处理器", 
                   f"TRANSFER - 拾取: {len(target_params)}, 放置: {len(release_params)}")
        
        # 任务优化
        task_list2, failed_bottles = self.task_optimizer.optimize_transfer_task(
            target_params, release_params
        )
        
        # 执行任务
        total_success = 0
        for batch in task_list2:
            pick_tasks = batch["pick"]
            put_tasks = batch["put"]
            
            # 执行拾取任务
            for nav_pose, bottles in pick_tasks.items():
                self.robot_a.send_service_request(ROSService.NAVIGATION_STATUS, "waiting_navigation_status")
                self.robot_a.send_service_request(
                    ROSService.NAVIGATION_STATUS,
                    "navigation_to_pose",
                    extra_params={"navigation_pose": nav_pose}
                )
                
                for bottle_id in bottles:
                    bottle = self.bottle_manager.get_bottle(bottle_id)
                    if not bottle:
                        continue
                    
                    # 抓取、转腰、放置到后部平台的流程
                    self._execute_pickup_sequence(bottle)
            
            # 执行放置任务
            for nav_pose, items in put_tasks.items():
                self.robot_a.send_service_request(ROSService.NAVIGATION_STATUS, "waiting_navigation_status")
                self.robot_a.send_service_request(
                    ROSService.NAVIGATION_STATUS,
                    "navigation_to_pose",
                    extra_params={"navigation_pose": nav_pose}
                )
                
                for bottle_id, release_pose in items:
                    bottle = self.bottle_manager.get_bottle(bottle_id)
                    if not bottle:
                        continue
                    
                    # 从后部平台抓取、转腰、放置到目标点位的流程
                    if self._execute_putdown_sequence(bottle, release_pose):
                        total_success += 1
        
        return {
            "success": True,
            "message": "TRANSFER完成",
            "success_count": total_success,
            "failed_bottles": failed_bottles,
            "total": len(target_params)
        }

    def handle_scan_qrcode(self, cmd_data: Dict) -> Dict:
        """
        处理SCAN_QRCODE命令（异步模式）
        立即返回task_id，后台线程执行任务
        
        params中可指定robot_id来选择执行任务的机器人
        """
        # 生成唯一任务ID
        task_id = f"SCAN_QRCODE_{uuid.uuid4().hex[:8]}"
        cmd_id = cmd_data.get("cmd_id")
        params = cmd_data.get("params", {})
        
        # 从params中获取robot_id，默认使用robot_a
        robot_id = params.get("robot_id", "robot_a")
        
        # 验证机器人是否存在
        robot = self.get_robot(robot_id)
        if robot is None:
            available_robots = list(self.robots.keys()) if self.robots else []
            return make_error_response(
                ErrorCode.ROBOT_NOT_FOUND,
                f"指定的机器人 {robot_id} 不存在",
                available_robots=available_robots
            )
        
        # 检查机器人电量状态
        battery_error = self.check_battery_availability(robot_id)
        if battery_error:
            return battery_error
        
        # 检查机器人是否正忙
        if self.is_robot_busy(robot_id):
            state = self.task_state_machine.get_state()
            return make_error_response(
                ErrorCode.ROBOT_BUSY,
                f"机器人 {robot_id} 正忙，无法执行新任务",
                current_task_id=state.get("cmd_id"),
                current_status=state.get("status"),
                current_step=state.get("current_step", {}).get("description")
            )
        
        logger.info("命令处理器", f"开始SCAN_QRCODE任务: {cmd_id}, 使用机器人: {robot_id}")
        
        # 初始化状态机（传入robot_id）
        self.task_state_machine.start_task(cmd_id, robot_id)
        
        # 在后台线程执行扫码任务
        scan_thread = threading.Thread(
            target=self._execute_scan_qrcode_async,
            args=(cmd_id, robot_id),
            daemon=True,
            name=f"ScanThread-{cmd_id}-{robot_id}"
        )
        scan_thread.start()
        
        # 立即返回任务ID
        return make_success_response(
            "SCAN_QRCODE任务已启动",
            cmd_id=cmd_id,
            robot_id=robot_id,
            note="使用 GET_TASK_STATE 命令查询任务状态"
        )

    def _execute_scan_qrcode_async(self, task_id: str, robot_id: str = "robot_a"):
        """
        异步执行SCAN_QRCODE任务（后台线程）
        
        参数:
            task_id: 任务ID
            robot_id: 执行任务的机器人ID
        """
        # 获取对应的机器人实例
        robot = self.get_robot(robot_id)
        if robot is None:
            self.task_state_machine.set_error(f"机器人 {robot_id} 不存在")
            logger.error("命令处理器", f"机器人 {robot_id} 不存在")
            return
        
        try:
            logger.info("命令处理器", f"任务 {task_id} 开始执行 (机器人: {robot_id})")
            
            #input("press enter to continue...")
            # 订阅导航状态topic
            logger.info("命令处理器", "订阅 /navigation_status topic")
            subscribe_success = robot.subscribe_topic(
                topic_name=ROSService.NAVIGATION_STATUS,
                msg_type="navi_types/NavigationStatus",
                throttle_rate=0,
                queue_length=1
            )
            if not subscribe_success:
                self.task_state_machine.set_error("订阅导航状态失败")
                logger.error("命令处理器", "订阅导航状态失败")

            # 获取存储管理器
            storage_mgr = get_storage_manager()
            back_temp_storage = storage_mgr.get_storage()

            # 步骤1: 导航到扫描台
            self.task_state_machine.update_step(TaskStep.NAVIGATING_TO_SCAN, "开始导航到扫描台")
            # 通过action触发导航        
            '''navigation_to_pose_result = robot.send_service_request(
                robot.get_robot_service(),
                "navigation_to_pose",
                extra_params={"position": "scan_table"}'''
            # 通过发布topic消息触发导航（类似rospy.Publisher）
            navigation_publish_result = robot.publish_topic(
                topic_name="/navigation_control",
                msg_type="std_msgs/String",
                msg_data={"data": NavigationPose.SCAN_TABLE}  # 导航目标位置
            )
            if not navigation_publish_result:
                logger.error("命令处理器", "发布导航命令失败")
                print("发布导航命令失败")
                self.task_state_machine.set_error("发布导航命令失败")
                return
            # 导航到最后一段会断网
            # 导航后检查导航状态（支持连接断开重连）
            waiting_navigation_status_result = self._wait_for_navigation_finished(robot)
            if not waiting_navigation_status_result:
                logger.error("命令处理器", "导航到扫描台失败")
                print("导航到扫描台失败")
                return
            # 步骤2: 抓取扫描枪（如果需要）
            # self.task_state_machine.update_step(TaskStep.GRAB_SCAN_GUN, "抓取扫描枪")
            # ... 抓取扫描枪的代码 ...
            '''grab_object_result = self.robot_a.send_service_request(
                ROSService.STRAWBERRY_SERVICE,
                "grab_object",
                extra_params={
                    "type": "scan_gun",
                    "target_pose": "scan_gun",
                    "hand": "right"
                }
            )'''
            
            # 循环处理瓶子：扫码并放置到后部暂存区
            scan_store_result = self._scan_and_store_bottles_loop_press_button(robot, storage_mgr, None, robot_id)
            if not scan_store_result:
                return  # 出错时已在内部设置错误状态
            #input("press enter to continue...")
            
            # 步骤10: 导航到分液台待分液区（转运任务点位）
            self.task_state_machine.update_step(TaskStep.NAVIGATING_TO_WAITING_SPLIT_AREA_TRANSFER, "导航到分液台待分液区（转运任务点位）")
            '''navigation_to_pose_result = robot.send_service_request(
                robot.get_robot_service(),
                "navigation_to_pose",
                extra_params={"position": "split_table"}
            )'''
            navigation_publish_result = robot.publish_topic(
                topic_name="/navigation_control",
                msg_type="std_msgs/String",
                msg_data={"data": NavigationPose.WAITING_SPLIT_AREA_TRANSFER}
            )
            if not navigation_publish_result:
                logger.error("命令处理器", "发布导航命令失败")
                print("发布导航命令失败")
                self.task_state_machine.set_error("发布导航命令失败")
                return
            # 导航到最后一段会断网
            # 导航后检查导航状态（支持连接断开重连）
            waiting_navigation_status_result = self._wait_for_navigation_finished(robot)

            waiting_navigation_status_result = self._wait_for_navigation_finished(robot)
            if not waiting_navigation_status_result:
                return
            #input("press enter to continue...")
            
            # 步骤11: 放下暂存区所有瓶子到分液台
            self.task_state_machine.update_step(TaskStep.PUTTING_DOWN, "放下暂存区所有瓶子到分液台")
            
            # 获取暂存区所有瓶子
            all_bottles_in_storage = []
            for bottle_type, slots in back_temp_storage.items():
                for slot_index, slot in enumerate(slots):
                    if not storage_mgr.is_slot_empty(slot):  # 不为空的槽位
                        bottle_id = storage_mgr.get_bottle_id(slot)
                        all_bottles_in_storage.append({
                            "bottle_type": bottle_type,
                            "bottle_id": bottle_id,
                            "slot_index": slot_index
                        })
            
            logger.info("命令处理器", f"暂存区共有 {len(all_bottles_in_storage)} 个瓶子需要放置")
            print(f"\n✓ 暂存区共有 {len(all_bottles_in_storage)} 个瓶子")
            
            # 遍历所有瓶子，执行放置动作
            for i, bottle_info in enumerate(all_bottles_in_storage):
                #input("press enter to continue...")
                bottle_id = bottle_info["bottle_id"]
                slot_index = bottle_info["slot_index"]
                bottle_type = bottle_info["bottle_type"]
                
                print(f"\n处理第 {i+1}/{len(all_bottles_in_storage)} 个瓶子:")
                print(f"  瓶子ID: {bottle_id}")
                print(f"  类型: {bottle_type}")
                print(f"  槽位: {slot_index}")
                
                self.task_state_machine.update_step(
                    TaskStep.PUTTING_DOWN_BOTTLE, 
                    f"放下瓶子 {i+1}/{len(all_bottles_in_storage)}: {bottle_id}"
                )
                
                # 执行放置动作
                put_down_result = robot.send_service_request(
                    robot.get_robot_service(),
                    "put_down_split_table",
                    extra_params={
                        "target_pose": "back_temp_500_" + str(slot_index)
                    }
                )
                if not put_down_result:
                    error_msg = f"放置瓶子 {bottle_id} 失败"
                    logger.error("命令处理器", error_msg)
                    self.task_state_machine.set_error(error_msg)
                    return
                else:
                    # 更新暂存区状态
                    storage_mgr.update_slot(robot_id, bottle_type, slot_index, 0)
                    # 分液台待分液区增加一瓶
                    station_counter = get_station_counter()
                    station_counter.increment(StationCounter.WAITING_SPLIT_AREA)
                    
                print(f"✓ 瓶子 {bottle_id} 放置完成")
                logger.info("命令处理器", f"瓶子 {bottle_id} 放置完成")
            
            print(f"\n✓ 所有瓶子放置完成，共 {len(all_bottles_in_storage)} 个")
            logger.info("命令处理器", f"所有瓶子放置完成，共 {len(all_bottles_in_storage)} 个")
            self.task_state_machine.update_step(TaskStep.COMPLETED, "任务完成")
            self.task_state_machine.complete_task(True, "流程结束")
                
                
            
        except Exception as e:
            logger.exception_occurred("命令处理器", f"任务 {task_id} 执行异常", e)
            self.task_state_machine.set_error(f"执行异常: {str(e)}")
        finally:
            # 任务结束，取消订阅
            logger.info("命令处理器", "取消订阅 /navigation_status topic")
            if robot:
                robot.unsubscribe_topic(ROSService.NAVIGATION_STATUS)

    

    
    def handle_split_liquid(self, cmd_data: Dict) -> Dict:
        """
        处理SPLIT_LIQUID命令（异步模式）
        立即返回task_id，后台线程执行任务
        
        params中可指定robot_id来选择执行任务的机器人
        """
        # 生成唯一任务ID
        task_id = f"SPLIT_LIQUID_{uuid.uuid4().hex[:8]}"
        cmd_id = cmd_data.get("cmd_id")
        params = cmd_data.get("params", {})
        
        # 从params中获取robot_id，默认使用robot_a
        robot_id = params.get("robot_id", "robot_a")
        
        # 验证机器人是否存在
        robot = self.get_robot(robot_id)
        if robot is None:
            available_robots = list(self.robots.keys()) if self.robots else []
            return make_error_response(
                ErrorCode.ROBOT_NOT_FOUND,
                f"指定的机器人 {robot_id} 不存在",
                available_robots=available_robots
            )
        
        # 检查机器人电量状态
        battery_error = self.check_battery_availability(robot_id)
        if battery_error:
            return battery_error
        
        # 检查机器人是否正忙
        if self.is_robot_busy(robot_id):
            state = self.task_state_machine.get_state()
            return make_error_response(
                ErrorCode.ROBOT_BUSY,
                f"机器人 {robot_id} 正忙，无法执行新任务",
                current_task_id=state.get("cmd_id"),
                current_status=state.get("status"),
                current_step=state.get("current_step", {}).get("description")
            )
        
        logger.info("命令处理器", f"开始SPLIT_LIQUID任务: {cmd_id}, 使用机器人: {robot_id}")
        
        # 初始化状态机（传入robot_id）
        self.task_state_machine.start_task(cmd_id, robot_id)
        
        # 在后台线程执行分液任务
        split_thread = threading.Thread(
            target=self._execute_split_liquid_async,
            args=(cmd_id, robot_id),
            daemon=True,
            name=f"SplitThread-{cmd_id}-{robot_id}"
        )
        split_thread.start()
        
        # 立即返回任务ID
        return make_success_response(
            "SPLIT_LIQUID任务已启动",
            cmd_id=cmd_id,
            robot_id=robot_id,
            note="使用 GET_TASK_STATE 命令查询任务状态"
        )
    
    def _execute_split_liquid_async(self, task_id: str, robot_id: str = "robot_a"):
        """
        异步执行SPLIT_LIQUID任务（后台线程）
        
        参数:
            task_id: 任务ID
            robot_id: 执行任务的机器人ID
        """
        # 获取对应的机器人实例
        robot = self.get_robot(robot_id)
        if robot is None:
            self.task_state_machine.set_error(f"机器人 {robot_id} 不存在")
            logger.error("命令处理器", f"机器人 {robot_id} 不存在")
            return
        
        try:
            logger.info("命令处理器", f"任务 {task_id} 开始执行 (机器人: {robot_id})")

            # 获取存储管理器
            storage_mgr = get_storage_manager()
            back_temp_storage = storage_mgr.get_storage(robot_id)
            
            # 调试代码，模拟新增空瓶子
            storage_mgr.update_slot(robot_id, "glass_bottle_500", 1, 22222)
            storage_mgr.set_bottle_state(robot_id, "glass_bottle_500", 1, BottleState.NOT_SPLIT)
            storage_mgr.update_slot(robot_id, "glass_bottle_500", 2, 33333)
            storage_mgr.set_bottle_state(robot_id, "glass_bottle_500", 0, BottleState.NOT_SPLIT)
            storage_mgr.update_slot(robot_id, "glass_bottle_500", 3, 44444)
            storage_mgr.set_bottle_state(robot_id, "glass_bottle_500", 1, BottleState.NOT_SPLIT)

            # 订阅导航状态topic
            robot.subscribe_topic(
                topic_name="/navigation_status",
                msg_type="NavigationStatus"
            )

            # 大步骤：在待分液区抓取瓶子
            # 导航到待分液区（分液任务点位）
            input("navigating to waiting split area split...")
            self.task_state_machine.update_step(TaskStep.NAVIGATING_TO_WAITING_SPLIT_AREA_SPLIT, "导航到待分液区（分液任务点位）")
            # 通过topic触发导航
            # 导航准备位置
            navigation_prepare_result = robot.send_service_request(
                robot.get_robot_service(),
                "navigation_prepare"
            )
            if not navigation_prepare_result:
                self.task_state_machine.set_error("导航准备位置失败")
                logger.error("命令处理器", "导航准备位置失败")
                return
            navigation_publish_result = robot.publish_topic(
                topic_name="/navigation_control",
                msg_type="std_msgs/String",
                msg_data={"data": NavigationPose.WAITING_SPLIT_AREA_SPLIT}
            )
            if not navigation_publish_result:
                self.task_state_machine.set_error("发布导航命令失败")
                logger.error("命令处理器", "发布导航命令失败")
                return
            # 等待导航完成
            '''waiting_navigation_status_result = self._wait_for_navigation_finished(robot)
            if not waiting_navigation_status_result:
                self.task_state_machine.set_error("导航到待分液区（分液任务点位）失败")
                logger.error("命令处理器", "导航到待分液区（分液任务点位）失败")
                return'''

            # 封装技能：在待分液区抓取瓶子放到后部暂存区
            self._scan_and_store_bottles_loop(robot, storage_mgr, StationCounter.WAITING_SPLIT_AREA, robot_id)
            
            # 获取暂存区所有未分液的瓶子
            glass_bottle_250_in_storage_split = []
            glass_bottle_500_in_storage_split = []
            for bottle_type, slots in back_temp_storage.items():
                for slot_index, slot in enumerate(slots):
                    if not storage_mgr.is_slot_empty(slot):  # 不为空的槽位
                        bottle_info = storage_mgr.get_bottle_info(robot_id, bottle_type, slot_index)
                        # 只处理未分液的瓶子
                        if bottle_info and bottle_info.get("bottle_state") == BottleState.NOT_SPLIT:
                            if bottle_type == "glass_bottle_250":
                                glass_bottle_250_in_storage_split.append({
                                    "bottle_type": bottle_type,
                                    "bottle_id": bottle_info.get("bottle_id"),
                                    "slot_index": slot_index
                                })
                            elif bottle_type == "glass_bottle_500":
                                glass_bottle_500_in_storage_split.append({
                                    "bottle_type": bottle_type,
                                    "bottle_id": bottle_info.get("bottle_id"),
                                    "slot_index": slot_index
                                })
            
            logger.info("命令处理器", f"暂存区共有 {len(glass_bottle_250_in_storage_split)} 个250ml未分液瓶子和 {len(glass_bottle_500_in_storage_split)} 个500ml未分液瓶子")
            print(f"\n✓ 暂存区共有 {len(glass_bottle_250_in_storage_split)} 个250ml未分液瓶子和 {len(glass_bottle_500_in_storage_split)} 个500ml未分液瓶子")

            # 如果空瓶子不足，补满空瓶
            if len(glass_bottle_250_in_storage_split) < len(glass_bottle_500_in_storage_split):
                # 大步骤：在空瓶区抓取瓶子
                # 导航到空瓶区（分液任务点位）
                input("navigating to empty bottle area split...")
                self.task_state_machine.update_step(TaskStep.NAVIGATING_TO_EMPTY_BOTTLE_AREA_SPLIT, "导航到空瓶区（分液任务点位）")
                # 通过topic触发导航
                # 导航准备位置
                navigation_prepare_result = robot.send_service_request(
                    robot.get_robot_service(),
                    "navigation_prepare"
                )
                if not navigation_prepare_result:
                    self.task_state_machine.set_error("导航准备位置失败")
                    logger.error("命令处理器", "导航准备位置失败")
                    return
                '''navigation_publish_result = robot.publish_topic(
                    topic_name="/navigation_control",
                    msg_type="std_msgs/String",
                    msg_data={"data": NavigationPose.EMPTY_BOTTLE_AREA_SPLIT}
                )
                if not navigation_publish_result:
                    self.task_state_machine.set_error("发布导航命令失败")
                    logger.error("命令处理器", "发布导航命令失败")
                    return
                # 等待导航完成
                waiting_navigation_status_result = self._wait_for_navigation_finished(robot)
                if not waiting_navigation_status_result:
                    self.task_state_machine.set_error("导航到空瓶区（分液任务点位）失败")
                    logger.error("命令处理器", "导航到空瓶区（分液任务点位）失败")
                    return'''
                # 调试代码，模拟新增空瓶子
                storage_mgr.update_slot(robot_id, "glass_bottle_250", 1, 66666)
                storage_mgr.set_bottle_state(robot_id, "glass_bottle_250", 1, BottleState.NOT_SPLIT)
                storage_mgr.update_slot(robot_id, "glass_bottle_250", 2, 77777)
                storage_mgr.set_bottle_state(robot_id, "glass_bottle_250", 0, BottleState.NOT_SPLIT)
                storage_mgr.update_slot(robot_id, "glass_bottle_250", 3, 88888)
                storage_mgr.set_bottle_state(robot_id, "glass_bottle_250", 1, BottleState.NOT_SPLIT)
                # 封装技能：在空瓶区抓取瓶子放到后部暂存区
                self._scan_and_store_bottles_loop(robot, storage_mgr, StationArea.EMPTY_BOTTLE_AREA, robot_id)

                # 获取暂存区所有未分液的瓶子
                glass_bottle_250_in_storage_split = []
                glass_bottle_500_in_storage_split = []
                for bottle_type, slots in back_temp_storage.items():
                    for slot_index, slot in enumerate(slots):
                        if not storage_mgr.is_slot_empty(slot):  # 不为空的槽位
                            bottle_info = storage_mgr.get_bottle_info(robot_id, bottle_type, slot_index)
                            # 只处理未分液的瓶子
                            if bottle_info and bottle_info.get("bottle_state") == BottleState.NOT_SPLIT:
                                if bottle_type == "glass_bottle_250":
                                    glass_bottle_250_in_storage_split.append({
                                        "bottle_type": bottle_type,
                                        "bottle_id": bottle_info.get("bottle_id"),
                                        "slot_index": slot_index
                                    })
                                elif bottle_type == "glass_bottle_500":
                                    glass_bottle_500_in_storage_split.append({
                                        "bottle_type": bottle_type,
                                        "bottle_id": bottle_info.get("bottle_id"),
                                        "slot_index": slot_index
                                    })
                
                logger.info("命令处理器", f"暂存区共有 {len(glass_bottle_250_in_storage_split)} 个250ml未分液瓶子和 {len(glass_bottle_500_in_storage_split)} 个500ml未分液瓶子")
                print(f"\n✓ 暂存区共有 {len(glass_bottle_250_in_storage_split)} 个250ml未分液瓶子和 {len(glass_bottle_500_in_storage_split)} 个500ml未分液瓶子")

            # 遍历所有瓶子，执行放置动作
            # 循环次数按照最少的瓶子数量来决定（万一空瓶区数量没了，要区分开来）
            input("placing bottles to bottle opener split...")
            both_hands_operation = True
            max_loop_count = min(len(glass_bottle_250_in_storage_split), len(glass_bottle_500_in_storage_split))
            for i in range(max_loop_count):
                glass_bottle_250_info = glass_bottle_250_in_storage_split[i]
                glass_bottle_500_info = glass_bottle_500_in_storage_split[i]
                # 双手分开操作
                if not both_hands_operation:
                    # 步骤1: 从后部暂存区拿起瓶子,并放下瓶子到500ml开瓶器
                    pick_up_result = robot.send_service_request(
                        robot.get_robot_service(),
                        "pick_from_back_temp",
                        extra_params={
                            "type": glass_bottle_500_info["bottle_type"],
                            "target_pose": "bottle_opener_500",
                            "end_pose": glass_bottle_500_info["slot_index"]
                        }
                    )
                    if not pick_up_result:
                        logger.error("命令处理器", "拿起瓶子到500ml开瓶器失败")
                        return
                    print(f"✓ 步骤1完成: 从后部暂存区拿起瓶子成功")
                    logger.info("命令处理器", f"瓶子 {glass_bottle_500_info['bottle_id']} 已从后部暂存区拿起成功")

                    print(f"放下瓶子 {glass_bottle_500_info['bottle_id']} 到500ml开瓶器")
                    put_down_result = robot.send_service_request(
                        robot.get_robot_service(),
                        "put_down",
                        extra_params={
                            "type": glass_bottle_500_info["bottle_type"],
                            "target_pose": "bottle_opener_500"
                        }
                    )
                    if not put_down_result:
                        self.task_state_machine.set_error("放下瓶子到500ml开瓶器失败")
                        logger.error("命令处理器", "放下瓶子到500ml开瓶器失败")
                        return
                    print(f"✓ 步骤2完成: 放下瓶子到500ml开瓶器成功")
                    logger.info("命令处理器", f"瓶子 {glass_bottle_500_info['bottle_id']} 已放下到500ml开瓶器")
                    self.task_state_machine.update_step(TaskStep.PUTTING_DOWN_TO_BOTTLE_OPENER_500_SUCCESS, "放下瓶子到500ml开瓶器成功")

                    # ========== 步骤3和步骤4并行执行 ==========
                    # 清除之前的事件状态
                    self.pick_from_opener_500_event.clear()
                    self.pick_from_opener_250_event.clear()
                    self.pick_from_opener_500_data = None
                    self.pick_from_opener_250_data = None
                    
                    # 用于存储步骤3的结果
                    step3_result_holder = {"success": False, "completed": False}
                    # 用于存储步骤4等待结果
                    step4_result_holder = {"success": False}
                    
                    def execute_step3_and_step4():
                        """在线程中执行步骤3和步骤4"""
                        # 步骤3: 从后部暂存区抓取瓶子，并放下瓶子到250ml开瓶器
                        pick_up_result = robot.send_service_request(
                            robot.get_robot_service(),
                            "pick_from_back_temp",
                            extra_params={
                                "type": glass_bottle_250_info["bottle_type"],
                                "target_pose": "back_temp_250_" + str(glass_bottle_250_info["slot_index"])
                            }
                        )
                        if not pick_up_result:
                            logger.error("命令处理器", f"[并行] 从后部暂存区抓取瓶子失败: {glass_bottle_250_info['bottle_id']}")
                            return
                        print(f"✓ [并行] 从后部暂存区抓取瓶子成功: {glass_bottle_250_info['bottle_id']}")
                        logger.info("命令处理器", f"[并行] 瓶子 {glass_bottle_250_info['bottle_id']} 已从后部暂存区抓取成功")

                        logger.info("命令处理器", f"[并行] 放下瓶子 {glass_bottle_250_info['bottle_id']} 到250ml开瓶器")
                        print(f"[并行] 放下瓶子 {glass_bottle_250_info['bottle_id']} 到250ml开瓶器")
                        put_down_250_result = robot.send_service_request(
                            robot.get_robot_service(),
                            "put_down",
                            extra_params={
                                "type": glass_bottle_250_info["bottle_type"],
                                "target_pose": "bottle_opener_250",
                            }
                        )
                        self.task_state_machine.update_step(TaskStep.PUTTING_DOWN_TO_BOTTLE_OPENER_250_SUCCESS, "放下瓶子到250ml开瓶器成功")
                        if put_down_250_result:
                            step3_result_holder["success"] = True
                            self.task_state_machine.update_step(TaskStep.PUTTING_DOWN_TO_BOTTLE_OPENER_250_SUCCESS, "放下瓶子到250ml开瓶器成功")
                            logger.info("命令处理器", f"[并行] 瓶子 {glass_bottle_250_info['bottle_id']} 已放下到250ml开瓶器")
                            print(f"✓ [并行] 瓶子 {glass_bottle_250_info['bottle_id']} 已放下到250ml开瓶器")
                            
                            # 步骤4: 等待250ml开瓶器完成开瓶
                            print(f"\n======================================================================")
                            print(f"【步骤4】等待250ml开瓶器完成开瓶...")
                            print(f"请使用以下命令发送完成信号:")
                            print(f"curl -X POST http://localhost:8848 -d @test_commands/PICK_FROM_BOTTLE_OPENER_250_command.json")
                            print(f"======================================================================\n")
                            
                            # 等待HTTP命令
                            if self.pick_from_opener_250_event.wait(timeout=600):  # 10分钟超时
                                if self.pick_from_opener_250_data and self.pick_from_opener_250_data.get("success"):
                                    step4_result_holder["success"] = True
                                    logger.info("命令处理器", "[并行] 250ml开瓶器完成信号已接收")
                                    print(f"✓ [并行] 250ml开瓶器完成信号已接收")
                                else:
                                    logger.error("命令处理器", "[并行] 250ml开瓶器完成信号为失败")
                                    print(f"✗ [并行] 250ml开瓶器完成信号为失败")
                            else:
                                logger.error("命令处理器", "[并行] 等待250ml开瓶器完成信号超时")
                                print(f"✗ [并行] 等待250ml开瓶器完成信号超时")
                        else:
                            logger.error("命令处理器", "[并行] 放下瓶子到250ml开瓶器失败")
                            print(f"✗ [并行] 放下瓶子到250ml开瓶器失败")
                        step3_result_holder["completed"] = True
                    
                    # 启动步骤3和步骤4的线程
                    step3_4_thread = threading.Thread(target=execute_step3_and_step4, daemon=True)
                    step3_4_thread.start()
                    
                    # 步骤2: 等待500ml开瓶器完成开瓶（主线程）
                    print(f"\n======================================================================")
                    print(f"【步骤2】等待500ml开瓶器完成开瓶...")
                    print(f"【步骤3】同时执行: 放下瓶子到250ml开瓶器")
                    print(f"请使用以下命令发送500ml完成信号:")
                    print(f"curl -X POST http://localhost:8848 -d @test_commands/PICK_FROM_BOTTLE_OPENER_500_command.json")
                    print(f"======================================================================\n")
                    
                    step2_success = False
                    if self.pick_from_opener_500_event.wait(timeout=600):  # 10分钟超时
                        if self.pick_from_opener_500_data and self.pick_from_opener_500_data.get("success"):
                            step2_success = True
                            logger.info("命令处理器", "500ml开瓶器完成信号已接收")
                            print(f"✓ 500ml开瓶器完成信号已接收")
                        else:
                            self.task_state_machine.set_error("500ml开瓶器完成信号为失败")
                            logger.error("命令处理器", "500ml开瓶器完成信号为失败")
                            return
                    else:
                        self.task_state_machine.set_error("等待500ml开瓶器完成信号超时")
                        logger.error("命令处理器", "等待500ml开瓶器完成信号超时")
                        return
                    
                    # 等待步骤3完成
                    step3_4_thread.join(timeout=10)  # 等待步骤3完x成，最多10秒
                    
                    # 检查步骤2成功 AND 步骤3成功
                    if not (step2_success and step3_result_holder["success"]):
                        self.task_state_machine.set_error("步骤2或步骤3执行失败")
                        logger.error("命令处理器", f"步骤2或步骤3执行失败: step2={step2_success}, step3={step3_result_holder['success']}")
                        return
                    
                    print(f"✓ 步骤2和步骤3都已成功完成，继续执行步骤5")

                    # 步骤5: 从500ml开瓶器抓取
                    self.task_state_machine.update_step(TaskStep.GRABBING_FROM_BOTTLE_OPENER_500, "从500ml开瓶器抓取瓶子")
                    logger.info("命令处理器", "从500ml开瓶器抓取瓶子")
                    print(f"步骤5: 从500ml开瓶器抓取瓶子")
                    grab_result_500 = robot.send_service_request(
                        robot.get_robot_service(),
                        "grab_from_bottle_opener",
                        extra_params={
                            "target_pose": "bottle_opener_500"
                        }
                    )
                    if not grab_result_500:
                        self.task_state_machine.set_error("从500ml开瓶器抓取失败")
                        logger.error("命令处理器", "从500ml开瓶器抓取失败")
                        return
                    print(f"✓ 步骤5完成: 从500ml开瓶器抓取成功")
                    
                    # 等待步骤4完成（步骤4在步骤3之后已经开始等待）
                    # 步骤3-4线程应该已经在执行步骤4的等待了
                    step3_4_thread.join(timeout=600)  # 等待步骤4完成
                    
                    # 检查步骤4是否成功
                    if not step4_result_holder["success"]:
                        self.task_state_machine.set_error("步骤4执行失败: 250ml开瓶器完成信号未收到或失败")
                        logger.error("命令处理器", "步骤4执行失败")
                        return
                    
                    print(f"✓ 步骤4已成功完成，继续执行步骤6")
                    
                    # 步骤6: 从250ml开瓶器抓取
                    self.task_state_machine.update_step(TaskStep.GRABBING_FROM_BOTTLE_OPENER_250, "从250ml开瓶器抓取瓶子")
                    logger.info("命令处理器", "从250ml开瓶器抓取瓶子")
                    print(f"步骤6: 从250ml开瓶器抓取瓶子")
                    grab_result_250 = robot.send_service_request(
                        robot.get_robot_service(),
                        "grab_from_bottle_opener",
                        extra_params={
                            "target_pose": "bottle_opener_250"
                        }
                    )
                    if not grab_result_250:
                        self.task_state_machine.set_error("从250ml开瓶器抓取失败")
                        logger.error("命令处理器", "从250ml开瓶器抓取失败")
                        return
                    print(f"✓ 步骤6完成: 从250ml开瓶器抓取成功")
                    
                    # 步骤7: 分液
                    self.task_state_machine.update_step(TaskStep.POURING_WATER, "分液")
                    pull_water_result = robot.send_service_request(
                        robot.get_robot_service(),
                        "pouring_water",
                        extra_params={
                            "volumn": 200
                        }
                    )
                    if not pull_water_result:
                        self.task_state_machine.set_error("抽水失败")
                        logger.error("命令处理器", "抽水失败")
                        return
                    
                    # 步骤8: 放置到500ml开瓶器
                    self.task_state_machine.update_step(TaskStep.PUTTING_TO_BOTTLE_OPENER_500, "放置到500ml开瓶器")
                    put_to_result = robot.send_service_request(
                        robot.get_robot_service(),
                        "put_to_bottle_opener",
                        extra_params={
                            "target_pose": "bottle_opener_500"
                        }
                    )
                    if not put_to_result:
                        self.task_state_machine.set_error("放置到开瓶器失败")
                        logger.error("命令处理器", "放置到开瓶器失败")
                        return
                    
                    # ========== 步骤9和步骤10并行执行 ==========
                    # 清除之前的事件状态（复用步骤2-6的事件）
                    self.pick_from_opener_500_event.clear()
                    self.pick_from_opener_250_event.clear()
                    self.pick_from_opener_500_data = None
                    self.pick_from_opener_250_data = None
                    
                    # 用于存储步骤10的结果
                    step10_result_holder = {"success": False, "completed": False}
                    # 用于存储步骤11等待结果
                    step11_result_holder = {"success": False}
                    
                    def execute_step10_and_step11():
                        """在线程中执行步骤10和步骤11"""
                        # 步骤10: 放置到250ml开瓶器
                        self.task_state_machine.update_step(TaskStep.PUTTING_TO_BOTTLE_OPENER_250, "放置到250ml开瓶器")
                        logger.info("命令处理器", "[并行] 放置到250ml开瓶器")
                        print(f"[并行] 步骤10: 放置到250ml开瓶器")
                        put_to_result_250 = robot.send_service_request(
                            robot.get_robot_service(),
                            "put_to_bottle_opener",
                            extra_params={
                                "target_pose": "bottle_opener_250"
                            }
                        )
                        if put_to_result_250:
                            step10_result_holder["success"] = True
                            logger.info("命令处理器", "[并行] 放置到250ml开瓶器成功")
                            print(f"✓ [并行] 步骤10完成: 放置到250ml开瓶器成功")
                            
                            # 步骤11: 等待250ml开瓶器完成关瓶
                            print(f"\n======================================================================")
                            print(f"【步骤11】等待250ml开瓶器完成关瓶...")
                            print(f"请使用以下命令发送完成信号:")
                            print(f"curl -X POST http://localhost:8848 -d @test_commands/PICK_FROM_BOTTLE_OPENER_250_command.json")
                            print(f"======================================================================\n")
                            
                            # 等待HTTP命令
                            if self.pick_from_opener_250_event.wait(timeout=600):  # 10分钟超时
                                if self.pick_from_opener_250_data and self.pick_from_opener_250_data.get("success"):
                                    step11_result_holder["success"] = True
                                    logger.info("命令处理器", "[并行] 250ml开瓶器关瓶完成信号已接收")
                                    print(f"✓ [并行] 250ml开瓶器关瓶完成信号已接收")
                                else:
                                    logger.error("命令处理器", "[并行] 250ml开瓶器关瓶完成信号为失败")
                                    print(f"✗ [并行] 250ml开瓶器关瓶完成信号为失败")
                            else:
                                logger.error("命令处理器", "[并行] 等待250ml开瓶器关瓶完成信号超时")
                                print(f"✗ [并行] 等待250ml开瓶器关瓶完成信号超时")
                        else:
                            logger.error("命令处理器", "[并行] 放置到250ml开瓶器失败")
                            print(f"✗ [并行] 放置到250ml开瓶器失败")
                        step10_result_holder["completed"] = True
                    
                    # 启动步骤10和步骤11的线程
                    step10_11_thread = threading.Thread(target=execute_step10_and_step11, daemon=True)
                    step10_11_thread.start()
                    
                    # 步骤9: 等待500ml开瓶器完成关瓶（主线程）
                    print(f"\n======================================================================")
                    print(f"【步骤9】等待500ml开瓶器完成关瓶...")
                    print(f"【步骤10】同时执行: 放置到250ml开瓶器")
                    print(f"请使用以下命令发送500ml完成信号:")
                    print(f"curl -X POST http://localhost:8848 -d @test_commands/PICK_FROM_BOTTLE_OPENER_500_command.json")
                    print(f"======================================================================\n")
                    
                    step9_success = False
                    if self.pick_from_opener_500_event.wait(timeout=600):  # 10分钟超时
                        if self.pick_from_opener_500_data and self.pick_from_opener_500_data.get("success"):
                            step9_success = True
                            logger.info("命令处理器", "500ml开瓶器关瓶完成信号已接收")
                            print(f"✓ 500ml开瓶器关瓶完成信号已接收")
                        else:
                            self.task_state_machine.set_error("500ml开瓶器关瓶完成信号为失败")
                            logger.error("命令处理器", "500ml开瓶器关瓶完成信号为失败")
                            return
                    else:
                        self.task_state_machine.set_error("等待500ml开瓶器关瓶完成信号超时")
                        logger.error("命令处理器", "等待500ml开瓶器关瓶完成信号超时")
                        return
                    
                    # 等待步骤10完成
                    step10_11_thread.join(timeout=10)  # 等待步骤10完成，最多10秒
                    
                    # 检查步骤9成功 AND 步骤10成功
                    if not (step9_success and step10_result_holder["success"]):
                        self.task_state_machine.set_error("步骤9或步骤10执行失败")
                        logger.error("命令处理器", f"步骤9或步骤10执行失败: step9={step9_success}, step10={step10_result_holder['success']}")
                        return
                    
                    print(f"✓ 步骤9和步骤10都已成功完成，继续执行步骤12")

                    # 步骤12: 从500ml开瓶器取回放到后部暂存区
                    self.task_state_machine.update_step(TaskStep.PICK_FROM_BOTTLE_OPENER_500_TO_BACK_TEMP, "从500ml开瓶器取回放到后部暂存区")
                    logger.info("命令处理器", "从500ml开瓶器取回放到后部暂存区")
                    print(f"步骤12: 从500ml开瓶器取回放到后部暂存区")
                    pick_back_result_500 = robot.send_service_request(
                        robot.get_robot_service(),
                        "pick_from_bottle_opener_to_back_temp",
                        extra_params={
                            "target_pose": "back_temp_500_0"
                        }
                    )
                    if not pick_back_result_500:
                        self.task_state_machine.set_error("从500ml开瓶器取回失败")
                        logger.error("命令处理器", "从500ml开瓶器取回失败")
                        return
                    print(f"✓ 步骤12完成: 从500ml开瓶器取回成功")
                    
                    # 等待步骤11完成（步骤11在步骤10之后已经开始等待）
                    step10_11_thread.join(timeout=600)  # 等待步骤11完成
                    
                    # 检查步骤11是否成功
                    if not step11_result_holder["success"]:
                        self.task_state_machine.set_error("步骤11执行失败: 250ml开瓶器关瓶完成信号未收到或失败")
                        logger.error("命令处理器", "步骤11执行失败")
                        return
                    
                    print(f"✓ 步骤11已成功完成，继续执行步骤13")
                    
                    # 步骤13: 从250ml开瓶器取回放到后部暂存区
                    self.task_state_machine.update_step(TaskStep.PICK_FROM_BOTTLE_OPENER_250_TO_BACK_TEMP, "从250ml开瓶器取回放到后部暂存区")
                    logger.info("命令处理器", "从250ml开瓶器取回放到后部暂存区")
                    print(f"步骤13: 从250ml开瓶器取回放到后部暂存区")
                    pick_back_result_250 = robot.send_service_request(
                        robot.get_robot_service(),
                        "pick_from_bottle_opener_to_back_temp",
                        extra_params={
                            "target_pose": "back_temp_250_0"
                        }
                    )
                    if not pick_back_result_250:
                        self.task_state_machine.set_error("从250ml开瓶器取回失败")
                        logger.error("命令处理器", "从250ml开瓶器取回失败")
                        return
                    print(f"✓ 步骤13完成: 从250ml开瓶器取回成功")
                    
                    # 标记瓶子为已分液
                    storage_mgr.set_bottle_state(robot_id, glass_bottle_500_info["bottle_type"], glass_bottle_500_info["slot_index"], BottleState.SPLIT_DONE)
                    storage_mgr.set_bottle_state(robot_id, glass_bottle_250_info["bottle_type"], glass_bottle_250_info["slot_index"], BottleState.SPLIT_DONE)


                    logger.info("命令处理器", f"瓶子 {glass_bottle_500_info['bottle_id']} 和 {glass_bottle_250_info['bottle_id']} 已标记为已分液")
                # 双手操作
                else:
                    # 步骤1: 从后部暂存区拿取指定位置瓶子
                    input("picking bottle from back storage...")
                    print(f"从后部暂存区拿取指定位置瓶子 {glass_bottle_250_info['bottle_id']} 和 {glass_bottle_500_info['bottle_id']}")
                    pick_up_result = robot.send_service_request(
                        robot.get_robot_service(),
                        "pick_from_back_temp_bothhand",
                        extra_params={
                            "right_hand": {
                                "type": glass_bottle_250_info["bottle_type"],
                                "target_pose": "back_temp_250_" + str(glass_bottle_250_info["slot_index"])
                            },
                            "left_hand": {
                                "type": glass_bottle_500_info["bottle_type"],
                                "target_pose": "back_temp_500_" + str(glass_bottle_500_info["slot_index"])
                            }
                        }
                    )
                    print(f"pick_up_result: {pick_up_result}")
                    if not pick_up_result:
                        self.task_state_machine.set_error("从后部暂存区拿取指定位置瓶子失败")
                        logger.error("命令处理器", "从后部暂存区拿取指定位置瓶子失败")
                        return
                    print(f"✓ 步骤1完成: 从后部暂存区拿取指定位置瓶子成功")
                    logger.info("命令处理器", f"瓶子 {glass_bottle_250_info['bottle_id']} 和 {glass_bottle_500_info['bottle_id']} 已从后部暂存区拿取成功")

                    # 步骤2：双手放下手中瓶子到开瓶器
                    input("put_down_bothhand")
                    put_down_result = robot.send_service_request(
                        robot.get_robot_service(),
                        "put_down_bothhand",
                        extra_params={
                            "right_hand": {
                                "type": glass_bottle_250_info["bottle_type"],
                                "target_pose": "bottle_opener_250"
                            },
                            "left_hand": {
                                "type": glass_bottle_500_info["bottle_type"],
                                "target_pose": "bottle_opener_500"
                            }
                        }
                    )
                    if not put_down_result:
                        self.task_state_machine.set_error("双手放下手中瓶子到开瓶器失败")
                        logger.error("命令处理器", "双手放下手中瓶子到开瓶器失败")
                        return
                    print(f"✓ 步骤2完成: 双手放下手中瓶子到开瓶器成功")
                    self.task_state_machine.update_step(TaskStep.PUTTING_DOWN_TO_BOTTLE_OPENER_500_AND_250_SUCCESS, "放下瓶子到500ml和250ml开瓶器成功")
                    logger.info("命令处理器", f"瓶子 {glass_bottle_250_info['bottle_id']} 和 {glass_bottle_500_info['bottle_id']} 已双手放下手中瓶子到开瓶器成功")

                    # 步骤3，4：等待开瓶完成命令（并行执行）
                    print(f"\n======================================================================")
                    print(f"【步骤2】等待500ml开瓶器完成开瓶...")
                    print(f"【步骤3】等待250ml开瓶器完成开瓶...")
                    print(f"请使用以下命令发送500ml完成信号:")
                    print(f"curl -X POST http://localhost:8848 -d @test_commands/PICK_FROM_BOTTLE_OPENER_500_command.json")
                    print(f"======================================================================\n")
                    # 清除之前的事件状态
                    self.pick_from_opener_500_event.clear()
                    self.pick_from_opener_250_event.clear()
                    self.pick_from_opener_500_data = None
                    self.pick_from_opener_250_data = None
                    
                    # 用于存储步骤3等待结果
                    step3_result_holder = {"success": False}
                    # 用于存储步骤4等待结果
                    step4_result_holder = {"success": False}
                    def execute_step3():
                        """在线程中执行步骤3"""
                        # 步骤3: 等待500ml开瓶器完成开瓶
                        if self.pick_from_opener_500_event.wait(timeout=600):  # 10分钟超时
                            if self.pick_from_opener_500_data and self.pick_from_opener_500_data.get("success"):
                                step3_result_holder["success"] = True
                                logger.info("命令处理器", "500ml开瓶器完成开瓶信号已接收")
                                print(f"✓ 500ml开瓶器完成开瓶信号已接收")
                            else:
                                self.task_state_machine.set_error("500ml开瓶器完成开瓶信号为失败")
                                logger.error("命令处理器", "500ml开瓶器完成开瓶信号为失败")
                                return
                        else:
                            self.task_state_machine.set_error("等待500ml开瓶器完成开瓶信号超时")
                            logger.error("命令处理器", "等待500ml开瓶器完成开瓶信号超时")
                            return
                        step3_result_holder["success"] = True
                    
                    # 启动步骤3线程
                    step3_thread = threading.Thread(target=execute_step3, daemon=True)
                    step3_thread.start()

                    # 步骤4: 等待250ml开瓶器完成开瓶
                    if self.pick_from_opener_250_event.wait(timeout=600):  # 10分钟超时
                        if self.pick_from_opener_250_data and self.pick_from_opener_250_data.get("success"):
                            step4_result_holder["success"] = True
                            logger.info("命令处理器", "250ml开瓶器完成开瓶信号已接收")
                            print(f"✓ 250ml开瓶器完成开瓶信号已接收")
                        else:
                            self.task_state_machine.set_error("250ml开瓶器完成开瓶信号为失败")
                            logger.error("命令处理器", "250ml开瓶器完成开瓶信号为失败")
                            return
                    else:
                        self.task_state_machine.set_error("等待250ml开瓶器完成开瓶信号超时")
                        logger.error("命令处理器", "等待250ml开瓶器完成开瓶信号超时")
                        return
                    step4_result_holder["success"] = True

                    # 等待步骤3完成
                    step3_thread.join(timeout=10)  # 等待步骤3完成，最多10秒
                    
                    # 检查步骤3是否成功
                    if not step3_result_holder["success"]:
                        self.task_state_machine.set_error("步骤3执行失败")
                        logger.error("命令处理器", "步骤3执行失败")
                        return
                    
                    # 步骤5：双手从开瓶器抓取瓶子(握住状态)
                    input("grab_from_bottle_opener_bothhand")
                    self.task_state_machine.update_step(TaskStep.GRABBING_FROM_BOTTLE_OPENER_500_AND_250, "双手从开瓶器抓取瓶子")
                    logger.info("命令处理器", "双手从开瓶器抓取瓶子")
                    print(f"步骤5: 双手从开瓶器抓取瓶子")
                    grab_result = robot.send_service_request(
                        robot.get_robot_service(),
                        "grab_from_bottle_opener_bothhand",
                        extra_params={
                            "right_hand": {
                                "type": glass_bottle_250_info["bottle_type"],
                                "target_pose": "bottle_opener_250"
                            },
                            "left_hand": {
                                "type": glass_bottle_500_info["bottle_type"],
                                "target_pose": "bottle_opener_500"
                            }
                        }
                    )
                    if not grab_result:
                        self.task_state_machine.set_error("双手从开瓶器抓取瓶子失败")
                        logger.error("命令处理器", "双手从开瓶器抓取瓶子失败")
                        return
                    print(f"✓ 步骤5完成: 双手从开瓶器抓取瓶子成功")
                    logger.info("命令处理器", f"瓶子 {glass_bottle_250_info['bottle_id']} 和 {glass_bottle_500_info['bottle_id']} 已双手从开瓶器抓取瓶子成功")

                    # 步骤6：分液
                    input("pouring_water")
                    self.task_state_machine.update_step(TaskStep.POURING_WATER, "分液")
                    logger.info("命令处理器", "分液")
                    print(f"步骤6: 分液")
                    pour_result = robot.send_service_request(
                        robot.get_robot_service(),
                        "pouring_water",
                        extra_params={
                            "volume": "250"
                        }
                    )
                    if not pour_result:
                        self.task_state_machine.set_error("分液失败")
                        logger.error("命令处理器", "分液失败")
                        return
                    print(f"✓ 步骤6完成: 分液成功")
                    logger.info("命令处理器", "分液成功")
                    
                    # 步骤7：双手把手上的瓶子放到目标开瓶器上（握住状态）
                    input("put_to_bottle_opener_bothhand")
                    self.task_state_machine.update_step(TaskStep.PUTTING_TO_BOTTLE_OPENER_500_AND_250, "双手把手上的瓶子放到目标开瓶器上")
                    logger.info("命令处理器", "双手把手上的瓶子放到目标开瓶器上")
                    print(f"步骤7: 双手把手上的瓶子放到目标开瓶器上")
                    put_to_result = robot.send_service_request(
                        robot.get_robot_service(),
                        "put_to_bottle_opener_bothhand",
                        extra_params={
                            "right_hand": {
                                "type": glass_bottle_250_info["bottle_type"],
                                "target_pose": "bottle_opener_250"
                            },
                            "left_hand": {
                                "type": glass_bottle_500_info["bottle_type"],
                                "target_pose": "bottle_opener_500"
                            }
                        }
                    )
                    if not put_to_result:
                        self.task_state_machine.set_error("双手把手上的瓶子放到目标开瓶器上失败")
                        logger.error("命令处理器", "双手把手上的瓶子放到目标开瓶器上失败")
                        return
                    print(f"✓ 步骤7完成: 双手把手上的瓶子放到目标开瓶器上成功")
                    self.task_state_machine.update_step(TaskStep.PUTTING_TO_BOTTLE_OPENER_500_AND_250_SUCCESS, "双手把手上的瓶子放到目标开瓶器上成功")
                    logger.info("命令处理器", f"瓶子 {glass_bottle_250_info['bottle_id']} 和 {glass_bottle_500_info['bottle_id']} 已双手把手上的瓶子放到目标开瓶器上成功")
                    
                    # 步骤8,9：等待关瓶完成命令（并行执行）
                    print(f"\n======================================================================")
                    print(f"【步骤8】等待500ml开瓶器完成关瓶...")
                    print(f"【步骤9】等待250ml开瓶器完成关瓶...")
                    print(f"请使用以下命令发送500ml完成信号:")
                    print(f"curl -X POST http://localhost:8848 -d @test_commands/PICK_FROM_BOTTLE_OPENER_500_command.json")
                    print(f"======================================================================\n")
                    # 清除之前的事件状态
                    self.pick_from_opener_500_event.clear()
                    self.pick_from_opener_250_event.clear()
                    self.pick_from_opener_500_data = None
                    self.pick_from_opener_250_data = None
                    
                    # 用于存储步骤8等待结果
                    step8_result_holder = {"success": False}
                    # 用于存储步骤9等待结果
                    step9_result_holder = {"success": False}
                    def execute_step8():
                        """在线程中执行步骤8"""
                        # 步骤8: 等待500ml开瓶器完成关瓶
                        if self.pick_from_opener_500_event.wait(timeout=600):  # 10分钟超时
                            if self.pick_from_opener_500_data and self.pick_from_opener_500_data.get("success"):
                                step8_result_holder["success"] = True
                                logger.info("命令处理器", "500ml开瓶器完成关瓶信号已接收")
                                print(f"✓ 500ml开瓶器完成关瓶信号已接收")
                            else:
                                self.task_state_machine.set_error("500ml开瓶器完成关瓶信号为失败")
                                logger.error("命令处理器", "500ml开瓶器完成关瓶信号为失败")
                                return
                        else:
                            self.task_state_machine.set_error("等待500ml开瓶器完成关瓶信号超时")
                            logger.error("命令处理器", "等待500ml开瓶器完成关瓶信号超时")
                            return
                        step8_result_holder["success"] = True
                    
                    # 启动步骤8线程
                    step8_thread = threading.Thread(target=execute_step8, daemon=True)
                    step8_thread.start()

                    # 步骤9: 等待250ml开瓶器完成关瓶
                    if self.pick_from_opener_250_event.wait(timeout=600):  # 10分钟超时
                        if self.pick_from_opener_250_data and self.pick_from_opener_250_data.get("success"):
                            step9_result_holder["success"] = True
                            logger.info("命令处理器", "250ml开瓶器完成关瓶信号已接收")
                            print(f"✓ 250ml开瓶器完成关瓶信号已接收")
                        else:
                            self.task_state_machine.set_error("250ml开瓶器完成关瓶信号为失败")
                            logger.error("命令处理器", "250ml开瓶器完成关瓶信号为失败")
                            return
                    else:
                        self.task_state_machine.set_error("等待250ml开瓶器完成关瓶信号超时")
                        logger.error("命令处理器", "等待250ml开瓶器完成关瓶信号超时")
                        return
                    step9_result_holder["success"] = True

                    # 等待步骤8完成
                    step8_thread.join(timeout=10)  # 等待步骤8完成，最多10秒
                    
                    # 检查步骤8是否成功
                    if not step8_result_holder["success"]:
                        self.task_state_machine.set_error("步骤8执行失败")
                        logger.error("命令处理器", "步骤8执行失败")
                        return
                    
                    # 步骤10：双手从开瓶器提起瓶子
                    input("raising_from_bottle_opener_bothhand")
                    self.task_state_machine.update_step(TaskStep.RAISING_FROM_BOTTLE_OPENER_500_AND_250, "双手从开瓶器提起瓶子")
                    logger.info("命令处理器", "双手从开瓶器提起瓶子")
                    print(f"步骤10: 双手从开瓶器提起瓶子")
                    raise_result = robot.send_service_request(
                        robot.get_robot_service(),
                        "pick_from_bottle_opener_bothhand",
                        extra_params={
                            "right_hand": {
                                "type": glass_bottle_250_info["bottle_type"],
                                "target_pose": "bottle_opener_250"
                            },
                            "left_hand": {
                                "type": glass_bottle_500_info["bottle_type"],
                                "target_pose": "bottle_opener_500"
                            }
                        }
                    )
                    if not raise_result:
                        self.task_state_machine.set_error("双手从开瓶器提起瓶子失败")
                        logger.error("命令处理器", "双手从开瓶器提起瓶子失败")
                        return
                    print(f"✓ 步骤10完成: 双手从开瓶器提起瓶子成功")
                    logger.info("命令处理器", f"瓶子 {glass_bottle_250_info['bottle_id']} 和 {glass_bottle_500_info['bottle_id']} 已双手从开瓶器提起瓶子成功")

                    # 步骤11：双手把手上的瓶子放到后部暂存区
                    input("put_down_back_temp_bothhand")
                    self.task_state_machine.update_step(TaskStep.PUTTING_DOWN_TO_BACK_TEMP_500_AND_250, "双手把手上的瓶子放到后部暂存区")
                    logger.info("命令处理器", "双手把手上的瓶子放到后部暂存区")
                    print(f"步骤11: 双手把手上的瓶子放到后部暂存区")
                    put_down_result = robot.send_service_request(
                        robot.get_robot_service(),
                        "put_down_back_temp_bothhand",
                        extra_params={
                            "right_hand": {
                                "type": glass_bottle_250_info["bottle_type"],
                                "target_pose": "back_temp_250_" + str(glass_bottle_250_info["slot_index"])
                            },
                            "left_hand": {
                                "type": glass_bottle_500_info["bottle_type"],
                                "target_pose": "back_temp_500_" + str(glass_bottle_500_info["slot_index"])
                            }
                        }
                    )
                    if not put_down_result:
                        self.task_state_machine.set_error("双手把手上的瓶子放到后部暂存区失败")
                        logger.error("命令处理器", "双手把手上的瓶子放到后部暂存区失败")
                        return
                    print(f"✓ 步骤11完成: 双手把手上的瓶子放到后部暂存区成功")
                    logger.info("命令处理器", f"瓶子 {glass_bottle_250_info['bottle_id']} 和 {glass_bottle_500_info['bottle_id']} 已双手把手上的瓶子放到后部暂存区成功")
                    # 标记瓶子为已分液
                    storage_mgr.set_bottle_state(robot_id, glass_bottle_500_info["bottle_type"], glass_bottle_500_info["slot_index"], BottleState.SPLIT_DONE)
                    storage_mgr.set_bottle_state(robot_id, glass_bottle_250_info["bottle_type"], glass_bottle_250_info["slot_index"], BottleState.SPLIT_DONE)
                    logger.info("命令处理器", f"瓶子 {glass_bottle_500_info['bottle_id']} 和 {glass_bottle_250_info['bottle_id']} 已标记为已分液")

                    
                # 步骤14： 导航到250ml分液完成暂存区（分液任务点位）
                # 使用topic触发导航
                input("navigating to 250ml split done area split...")
                # 导航准备位置
                navigation_prepare_result = robot.send_service_request(
                    robot.get_robot_service(),
                    "navigation_prepare"
                )
                if not navigation_prepare_result:
                    self.task_state_machine.set_error("导航准备位置失败")
                    logger.error("命令处理器", "导航准备位置失败")
                    return
                '''navigation_publish_result = robot.publish_topic(
                    topic_name="/navigation_control",
                    msg_type="std_msgs/String",
                    msg_data={"data": NavigationPose.SPLIT_DONE_250ML_AREA_SPLIT}
                )
                if not navigation_publish_result:
                    self.task_state_machine.set_error("发布导航命令失败")
                    logger.error("命令处理器", "发布导航命令失败")
                    return
                # 等待导航完成
                waiting_navigation_status_result = self._wait_for_navigation_finished(robot)
                if not waiting_navigation_status_result:
                    self.task_state_machine.set_error("导航到250ml分液完成暂存区（分液任务点位）失败")
                    logger.error("命令处理器", "导航到250ml分液完成暂存区（分液任务点位）失败")
                    return'''
                
                # 步骤15： 把后部暂存区所有分液完成的250ml瓶子放到250ml分液完成暂存区
                input("putting down 250ml split done area...")
                self.task_state_machine.update_step(TaskStep.PUTTING_DOWN_TO_250ML_SPLIT_DONE_AREA, "把后部暂存区所有分液完成的250ml瓶子放到250ml分液完成暂存区")
                logger.info("命令处理器", "把后部暂存区所有分液完成的250ml瓶子放到250ml分液完成暂存区")
                print(f"步骤15: 把后部暂存区所有分液完成的250ml瓶子放到250ml分液完成暂存区")
                # 遍历所有已分液的250ml瓶子
                all_bottles_in_storage = []
                for slot_index, slot in enumerate(self.storage_mgr.get_storage(robot_id)["glass_bottle_250"]):
                    if self.storage_mgr.get_bottle_info(robot_id, "glass_bottle_250", slot_index)["bottle_state"] == BottleState.SPLIT_DONE:
                        bottle_info = self.storage_mgr.get_bottle_info(robot_id, "glass_bottle_250", slot_index)
                        all_bottles_in_storage.append(bottle_info)
                self._put_bottles_loop(robot, self.storage_mgr, "250ml_split_done_area", all_bottles_in_storage, robot_id)

                # 步骤16： 导航到500ml分液完成暂存区（分液任务点位）
                # 使用topic触发导航
                input("navigating to 500ml split done area split...")
                # 导航准备位置
                navigation_prepare_result = robot.send_service_request(
                    robot.get_robot_service(),
                    "navigation_prepare"
                )
                if not navigation_prepare_result:
                    self.task_state_machine.set_error("导航准备位置失败")
                    logger.error("命令处理器", "导航准备位置失败")
                    return
                '''navigation_publish_result = robot.publish_topic(
                    topic_name="/navigation_control",
                    msg_type="std_msgs/String",
                    msg_data={"data": NavigationPose.SPLIT_DONE_500ML_AREA_SPLIT}
                )
                if not navigation_publish_result:
                    self.task_state_machine.set_error("发布导航命令失败")
                    logger.error("命令处理器", "发布导航命令失败")
                    return
                # 等待导航完成
                waiting_navigation_status_result = self._wait_for_navigation_finished(robot)
                if not waiting_navigation_status_result:
                    self.task_state_machine.set_error("导航到500ml分液完成暂存区（分液任务点位）失败")
                    logger.error("命令处理器", "导航到500ml分液完成暂存区（分液任务点位）失败")
                    return'''
                
                # 步骤17： 把后部暂存区所有分液完成的500ml瓶子放到500ml分液完成暂存区
                input("putting down 500ml split done area...")
                self.task_state_machine.update_step(TaskStep.PUTTING_DOWN_TO_500ML_SPLIT_DONE_AREA, "把后部暂存区所有分液完成的500ml瓶子放到500ml分液完成暂存区")
                logger.info("命令处理器", "把后部暂存区所有分液完成的500ml瓶子放到500ml分液完成暂存区")
                print(f"步骤17: 把后部暂存区所有分液完成的500ml瓶子放到500ml分液完成暂存区")
                # 遍历所有已分液的500ml瓶子
                all_bottles_in_storage = []
                for slot_index, slot in enumerate(self.storage_mgr.get_storage(robot_id)["glass_bottle_500"]):
                    if self.storage_mgr.get_bottle_info(robot_id, "glass_bottle_500", slot_index)["bottle_state"] == BottleState.SPLIT_DONE:
                        bottle_info = self.storage_mgr.get_bottle_info(robot_id, "glass_bottle_500", slot_index)
                        all_bottles_in_storage.append(bottle_info)
                self._put_bottles_loop(robot, self.storage_mgr, "500ml_split_done_area", all_bottles_in_storage, robot_id)
            
            # 任务完成
            self.task_state_machine.update_step(TaskStep.COMPLETED, "分液任务完成")
            self.task_state_machine.complete_task(True, "分液流程结束")
            logger.info("命令处理器", f"任务 {task_id} 执行完成")
            
        except Exception as e:
            logger.exception_occurred("命令处理器", f"任务 {task_id} 执行异常", e)
            self.task_state_machine.set_error(f"执行异常: {str(e)}")
    
    def handle_get_task_state(self, cmd_data: Dict) -> Dict:
        """
        处理GET_TASK_STATE命令
        查询任务的执行状态
        
        功能：
        - 如果params中有target_cmd_id，则查询指定任务的执行状态
        - 如果没有target_cmd_id，则查询机器人当前正在执行的任务状态
        """
        params = cmd_data.get("params", {})
        target_cmd_id = params.get("target_cmd_id")
        
        if target_cmd_id:
            state = self.task_state_machine.get_state(target_cmd_id)
        else:
            state = self.task_state_machine.get_state()
        
        # 检查是否找到任务
        if state.get("status") == "未找到":
            return make_error_response(
                ErrorCode.TASK_NOT_FOUND,
                state.get("message"),
                current_task_id=state.get("current_task_id")
            )
        
        return make_success_response(
            "状态查询成功",
            data=state
        )
    
    def handle_enter_id(self, cmd_data: Dict) -> Dict:
        """处理ENTER_ID命令"""
        params = cmd_data.get("params", {})
        bottle_id = params.get("bottle_id")
        object_type = params.get("type")
        
        logger.info("命令处理器", f"ENTER_ID - {bottle_id}")
        
        # 标记瓶子已扫码
        self.bottle_manager.mark_scanned(bottle_id)
        
        return {
            "success": True,
            "message": "ID录入成功",
            "bottle_id": bottle_id
        }
    
    def handle_scan_qrcode_enter_id(self, cmd_data: Dict) -> Dict:
        """
        处理SCAN_QRCODE_ENTER_ID命令
        接收HTTP发送的瓶子ID信息，触发等待事件
        """
        params = cmd_data.get("params", {})
        success = params.get("success")
        qrcode_id = params.get("qrcode_id")
        object_type = params.get("type")
        bottle_id = str(object_type) + "_" + str(qrcode_id)
        task = params.get("task")
        
        logger.info("命令处理器", f"SCAN_QRCODE_ENTER_ID - bottle_id: {bottle_id}, type: {object_type}, task: {task}")
        
        if not bottle_id or not object_type:
            logger.error("命令处理器", "SCAN_QRCODE_ENTER_ID缺少必要参数")
            return make_error_response(
                ErrorCode.MISSING_PARAMS,
                "缺少bottle_id或type参数"
            )
        
        # 保存数据并触发事件
        self.scan_enter_id_data = {
            "success": success,
            "bottle_id": bottle_id,
            "type": object_type,
            "task": task
        }
        self.scan_enter_id_event.set()
        
        logger.info("命令处理器", f"瓶子ID已录入并触发事件: {bottle_id}")
        
        return {
            "success": success,
            "message": "瓶子ID已录入，SCAN_QRCODE流程继续",
            "bottle_id": bottle_id,
            "qrcode_id": qrcode_id,
            "type": object_type
        }
    
    def handle_pick_from_bottle_opener_500(self, cmd_data: Dict) -> Dict:
        """
        处理PICK_FROM_BOTTLE_OPENER_500命令
        接收HTTP发送的500ml开瓶器完成信号
        """
        params = cmd_data.get("params", {})
        success = params.get("success", False)
        
        logger.info("命令处理器", f"PICK_FROM_BOTTLE_OPENER_500 - success: {success}")
        
        # 保存数据并触发事件
        self.pick_from_opener_500_data = {"success": success}
        self.pick_from_opener_500_event.set()
        
        return {
            "success": True,
            "message": "500ml开瓶器完成信号已接收"
        }
    
    def handle_pick_from_bottle_opener_250(self, cmd_data: Dict) -> Dict:
        """
        处理PICK_FROM_BOTTLE_OPENER_250命令
        接收HTTP发送的250ml开瓶器完成信号
        """
        params = cmd_data.get("params", {})
        success = params.get("success", False)
        
        logger.info("命令处理器", f"PICK_FROM_BOTTLE_OPENER_250 - success: {success}")
        
        # 保存数据并触发事件
        self.pick_from_opener_250_data = {"success": success}
        self.pick_from_opener_250_event.set()
        
        return {
            "success": True,
            "message": "250ml开瓶器完成信号已接收"
        }
    
    def handle_robot_b_action(self, cmd_data: Dict) -> Dict:
        """
        处理ROBOT_ACTION命令
        执行机器人的单独动作
        
        请求参数:
            action_type: 动作类型，如 "B_STEP_1", "B_STEP_2" 等
            robot_id: 机器人ID (可选，默认为 "robot_b")
        
        可用动作:
            B_STEP_1: 抓取瓶子后倒液并将试管1放到转盘上 (pure_water, 1)
            B_STEP_2: 抓取瓶子后倒液并将试管2放到转盘上 (pure_water, 2)
            B_STEP_3: 把样品瓶放回原位并归位 (place_reagent_bottle)
            B_STEP_5: 将试管1从清洗设备上拿到试管架上 (take_tube_rack, 1)
            B_STEP_7: 将试管2从清洗设备上拿到试管架上 (take_tube_rack, 2)
        """
        params = cmd_data.get("params", {})
        action_type = params.get("action_type")
        robot_id = params.get("robot_id", "robot_b")
        
        timeout = params.get("timeout", 600)
        
        logger.info("命令处理器", f"ROBOT_ACTION - action_type: {action_type}, robot_id: {robot_id}, timeout: {timeout}")
        
        # 检查功能是否启用
        if not is_robot_actions_enabled():
            return make_error_response(
                ErrorCode.ROBOT_ACTION_DISABLED,
                "机器人动作接口未启用，请在constants.py中设置ENABLE_ROBOT_B_ACTIONS=True",
                available_actions=get_available_actions()
            )
        
        # 调用处理函数（复用 robot_actions 模块，内部调用 robot_controller.send_service_request）
        result = handle_robot_action_command(
            {"action_type": action_type, "robot_id": robot_id, "timeout": timeout},
            self.robots
        )
        
        return result
    
    def handle_refill_empty_bottles(self, cmd_data: Dict) -> Dict:
        """
        处理REFILL_EMPTY_BOTTLES命令
        导航到空瓶区抓取空瓶并放置到后部暂存区
        
        请求参数:
            robot_id: 机器人ID (可选，默认为 "robot_a")
            timeout: 超时时间 (可选，默认60秒)
        """
        params = cmd_data.get("params", {})
        robot_id = params.get("robot_id", "robot_a")
        timeout = params.get("timeout", 60.0)
        task_id = cmd_data.get("cmd_id")
        
        logger.info("命令处理器", f"REFILL_EMPTY_BOTTLES - robot_id: {robot_id}")
        
        # 获取机器人
        robot = self.robots.get(robot_id)
        if not robot:
            return make_error_response(
                ErrorCode.ROBOT_NOT_CONNECTED,
                f"机器人 {robot_id} 未连接"
            )
        
        # 检查机器人电量状态
        battery_error = self.check_battery_availability(robot_id)
        if battery_error:
            return battery_error
        
        # 检查机器人是否正忙
        if self.is_robot_busy(robot_id):
            state = self.task_state_machine.get_state()
            return make_error_response(
                ErrorCode.ROBOT_BUSY,
                f"机器人 {robot_id} 正忙，无法执行新任务",
                current_task_id=state.get("cmd_id"),
                current_status=state.get("status"),
                current_step=state.get("current_step", {}).get("description")
            )
        
        # 生成任务ID
        #task_id = str(uuid.uuid4())[:8]
        
        # 初始化任务状态
        self.task_state_machine.start_task(task_id, robot_id)
        
        # 异步执行
        thread = threading.Thread(
            target=self._execute_refill_empty_bottles_async,
            args=(task_id, robot_id, timeout),
            daemon=True
        )
        thread.start()
        
        return {
            "success": True,
            "message": "补充空瓶任务已启动",
            "task_id": task_id
        }
    
    def _execute_refill_empty_bottles_async(self, task_id: str, robot_id: str, timeout: float):
        """
        异步执行补充空瓶任务
        
        流程：
        1. 导航到空瓶区
        2. 抓取空瓶并放置到后部暂存区（循环直到无更多瓶子或暂存区满）
        """
        robot = self.robots.get(robot_id)
        if not robot:
            self.task_state_machine.set_error(f"机器人 {robot_id} 未连接")
            return
        
        try:
            logger.info("命令处理器", f"任务 {task_id} 开始执行 (机器人: {robot_id})")
            
            # 获取存储管理器
            storage_mgr = get_storage_manager()
            
            # 通过topic触发导航
            navigation_publish_result = robot.publish_topic(
                topic_name="/navigation_control",
                msg_type="std_msgs/String",
                msg_data={"data": NavigationPose.EMPTY_BOTTLE_AREA_SPLIT}
            )
            if not navigation_publish_result:
                self.task_state_machine.set_error("发布导航命令失败")
                logger.error("命令处理器", "发布导航命令失败")
                return
            
            # 等待导航完成
            waiting_navigation_status_result = self._wait_for_navigation_finished(robot)
            if not waiting_navigation_status_result:
                self.task_state_machine.set_error("导航到空瓶区（分液任务点位）失败")
                logger.error("命令处理器", "导航到空瓶区（分液任务点位）失败")
                return
            
            print("✓ 已到达空瓶区")
            logger.info("命令处理器", "已到达空瓶区")
            
            # 步骤2: 抓取空瓶并放置到后部暂存区
            print("\n" + "="*70)
            print("【步骤2】抓取空瓶并放置到后部暂存区...")
            print("="*70)
            
            scan_store_result = self._scan_and_store_bottles_loop(robot, storage_mgr, StationArea.EMPTY_BOTTLE_AREA, robot_id)
            if not scan_store_result:
                # 错误已在内部设置
                return
            
            # 任务完成
            self.task_state_machine.update_step(TaskStep.COMPLETED, "补充空瓶任务完成")
            self.task_state_machine.complete_task(True, "补充空瓶流程结束")
            logger.info("命令处理器", f"任务 {task_id} 执行完成")
            print("\n" + "="*70)
            print("✅ 补充空瓶任务完成")
            print("="*70)
            
        except Exception as e:
            logger.exception_occurred("命令处理器", f"任务 {task_id} 执行异常", e)
            self.task_state_machine.set_error(f"执行异常: {str(e)}")
    
    def handle_bottle_get(self, cmd_data: Dict) -> Dict:
        """处理BOTTLE_GET命令 - 获取样品瓶信息"""
        params = cmd_data.get("params", {})
        bottle_id = params.get("bottle_id")
        pose_name = params.get("pose_name")
        detail_params = params.get("detail_params", True)
        
        logger.info("命令处理器", f"BOTTLE_GET - bottle_id: {bottle_id}, pose: {pose_name}")
        
        result_data = {}
        
        if bottle_id:
            # 查询指定瓶子
            if detail_params:
                result_data = self.bottle_manager.get_bottle_detail(bottle_id)
            else:
                result_data = {"bottle_id": bottle_id}
        
        elif pose_name:
            # 查询指定点位的所有瓶子
            bottles = self.bottle_manager.get_bottles_by_pose(pose_name)
            if detail_params:
                result_data = {
                    "pose_name": pose_name,
                    "bottles": [b.to_dict() for b in bottles]
                }
            else:
                result_data = {
                    "pose_name": pose_name,
                    "bottle_ids": [b.bottle_id for b in bottles]
                }
        
        else:
            # 查询所有瓶子
            all_bottles = self.bottle_manager.get_all_bottles()
            if detail_params:
                result_data = {
                    "total_count": len(all_bottles),
                    "bottles": [b.to_dict() for b in all_bottles.values()]
                }
            else:
                result_data = {
                    "total_count": len(all_bottles),
                    "bottle_ids": list(all_bottles.keys())
                }
        
        return {
            "success": True,
            "message": "查询成功",
            "data": result_data
        }
    
    def handle_get_station_counter(self, cmd_data: Dict) -> Dict:
        """
        处理GET_STATION_COUNTER命令 - 查询暂存区瓶子数量
        
        可查询的暂存区：
        - waiting_split_area: 分液台待分液区
        - split_done_250ml_area: 250ml分液完成暂存区
        - split_done_500ml_area: 500ml分液完成暂存区
        
        注意：暂存区与机器人无关，是独立的物理位置
        
        请求参数:
            area: 暂存区名称 (可选，不提供则返回所有暂存区数据)
            action: 操作类型 (可选，"reset"可重置计数器)
        """
        params = cmd_data.get("params", {})
        area = params.get("area")
        action = params.get("action")
        
        station_counter = get_station_counter()
        
        # 如果是重置操作
        if action == "reset":
            station_counter.reset(area)
            return {
                "success": True,
                "message": f"计数器已重置" + (f" (area={area})" if area else ""),
                "data": station_counter.get_all_counts()
            }
        
        # 查询操作
        if area:
            # 查询指定区域
            count = station_counter.get_count(area)
            return {
                "success": True,
                "message": "查询成功",
                "data": {
                    "area": area,
                    "count": count
                }
            }
        else:
            # 查询所有区域数据
            counts = station_counter.get_all_counts()
            return {
                "success": True,
                "message": "查询成功",
                "data": counts
            }
    
    def handle_transfer_to_chromatograph(self, cmd_data: Dict) -> Dict:
        """
        处理TRANSFER_TO_CHROMATOGRAPH命令
        从250ml分液完成暂存区拿取瓶子，运到色谱仪暂存位
        
        请求参数:
            robot_id: 机器人ID (可选，默认为 "robot_a")
        """
        params = cmd_data.get("params", {})
        robot_id = params.get("robot_id", "robot_a")
        task_id = cmd_data.get("cmd_id")
        
        logger.info("命令处理器", f"TRANSFER_TO_CHROMATOGRAPH - robot_id: {robot_id}")
        
        # 获取机器人
        robot = self.robots.get(robot_id)
        if not robot:
            return make_error_response(
                ErrorCode.ROBOT_NOT_CONNECTED,
                f"机器人 {robot_id} 未连接"
            )
        
        # 检查机器人电量状态
        battery_error = self.check_battery_availability(robot_id)
        if battery_error:
            return battery_error
        
        # 检查机器人是否正忙
        if self.is_robot_busy(robot_id):
            state = self.task_state_machine.get_state()
            return make_error_response(
                ErrorCode.ROBOT_BUSY,
                f"机器人 {robot_id} 正忙，无法执行新任务",
                current_task_id=state.get("cmd_id"),
                current_status=state.get("status")
            )
        # 测试参数
        input("press enter to continue_0...")
        pick_up_result = robot.send_service_request(
            robot.get_robot_service(),
            "put_down_back_temp_bothhand",
            extra_params={
                "right_hand": {
                    "type": "glass_bottle_250",
                    "target_pose": "back_temp_250_0"
                },
                "left_hand": {
                    "type": "glass_bottle_500",
                    "target_pose": "back_temp_500_0"
                }
            }
        )
        input("press enter to continue_1...")

        station_counter = get_station_counter()
        station_counter.increment(StationCounter.WAITING_SPLIT_AREA)
        self._execute_split_liquid_async(task_id, robot_id)

        input("press enter to continue_2...")

        # 检查250ml分液完成暂存区是否有瓶子
        station_counter = get_station_counter()
        available_count = station_counter.get_count(StationCounter.SPLIT_DONE_250ML_AREA)
        if available_count == 0:
            return make_error_response(
                ErrorCode.RESOURCE_INSUFFICIENT,
                f"250ml分液完成暂存区瓶子不足，当前: {available_count}"
            )

        # 启动任务
        self.task_state_machine.start_task(task_id, robot_id)
        
        # 启动异步执行线程
        thread = threading.Thread(
            target=self._execute_transfer_to_chromatograph_async,
            args=(task_id, robot_id),
            daemon=True
        )
        thread.start()
        
        return make_success_response(
            "TRANSFER_TO_CHROMATOGRAPH任务已启动",
            robot_id=robot_id,
            note="使用 GET_TASK_STATE 命令查询任务状态"
        )
    
    def _execute_transfer_to_chromatograph_async(self, task_id: str, robot_id: str):
        """
        异步执行TRANSFER_TO_CHROMATOGRAPH任务
        
        流程：
        1. 导航到250ml分液完成暂存区
        2. 抓取瓶子
        3. 导航到色谱仪
        4. 放置瓶子到色谱仪暂存位
        """
        robot = self.get_robot(robot_id)
        if robot is None:
            self.task_state_machine.set_error(f"机器人 {robot_id} 不存在")
            return
        
        try:
            logger.info("命令处理器", f"任务 {task_id} 开始执行 (机器人: {robot_id})")
            # 后部暂存区参数
            back_temp_storage = self.storage_mgr.get_storage(robot_id)
                
            # 步骤1: 导航到250ml分液完成暂存区（转运任务点位）
            self.task_state_machine.update_step(TaskStep.NAVIGATING_TO_250ML_SPLIT_DONE_AREA_TRANSFER, "导航到250ml分液完成暂存区（转运任务点位）")
            navigation_publish_result = robot.publish_topic(
                topic_name="/navigation_control",
                msg_type="std_msgs/String",
                msg_data={"data": NavigationPose.SPLIT_DONE_250ML_AREA_TRANSFER}
            )
            if not navigation_publish_result:
                self.task_state_machine.set_error("发布导航命令失败")
                logger.error("命令处理器", "发布导航命令失败")
                return
            # 等待导航完成
            waiting_navigation_status_result = self._wait_for_navigation_finished(robot)
            if not waiting_navigation_status_result:
                self.task_state_machine.set_error("导航到250ml分液完成暂存区（转运任务点位）失败")
                logger.error("命令处理器", "导航到250ml分液完成暂存区（转运任务点位）失败")
                return
            print(f"✓ 步骤1完成: 导航到250ml分液完成暂存区（转运任务点位）")
            
            # 步骤2: 大步骤：从250ml分液完成暂存区抓取瓶子
            bottle_msg = {
                "bottle_id": "bottle_to_chromatograph",
                "object_type": "glass_bottle_250",
                "task": "transfer_to_chromatograph"
            }
            self._store_bottles_loop(robot, back_temp_storage, robot_id, StationCounter.SPLIT_DONE_250ML_AREA, bottle_msg)
            
            # 步骤3: 导航到色谱仪
            self.task_state_machine.update_step(TaskStep.NAVIGATING_TO_CHROMATOGRAPH, "导航到色谱仪")
            navigation_publish_result = robot.publish_topic(
                topic_name="/navigation_control",
                msg_type="std_msgs/String",
                msg_data={"data": NavigationPose.CHROMATOGRAPH}
            )
            if not navigation_publish_result:
                self.task_state_machine.set_error("发布导航命令失败")
                logger.error("命令处理器", "发布导航命令失败")
                return
            
            # 等待导航完成
            waiting_navigation_status_result = self._wait_for_navigation_finished(robot)
            if not waiting_navigation_status_result:
                self.task_state_machine.set_error("导航到色谱仪失败")
                logger.error("命令处理器", "导航到色谱仪失败")
                return
            print(f"✓ 步骤3完成: 导航到色谱仪")
            
            # 步骤4: 把后部暂存区瓶子放到色谱仪暂存位
            # 遍历所有已分液的250ml瓶子
            all_bottles_in_storage = []
            for bottle_type, slots in self.storage_mgr.get_storage(robot_id).items():
                if bottle_type == "glass_bottle_250":
                    for slot_index, slot in enumerate(slots):
                        if self.storage_mgr.get_bottle_info(robot_id, bottle_type, slot_index)["bottle_state"] == BottleState.SPLIT_DONE:
                            bottle_info = self.storage_mgr.get_bottle_info(robot_id, bottle_type, slot_index)
                            all_bottles_in_storage.append(bottle_info)
            self._put_bottles_loop(robot, back_temp_storage, "chromatograph", all_bottles_in_storage, robot_id)
            
            # 任务完成
            self.task_state_machine.update_step(TaskStep.COMPLETED, "转移到色谱仪任务完成")
            self.task_state_machine.complete_task(True, f"成功转移 {bottle_count} 个瓶子到色谱仪")
            logger.info("命令处理器", f"任务 {task_id} 执行完成")
            
        except Exception as e:
            logger.exception_occurred("命令处理器", f"任务 {task_id} 执行异常", e)
            self.task_state_machine.set_error(f"执行异常: {str(e)}")
    
    def handle_start_working(self, cmd_data: Dict) -> Dict:
        """
        处理START_WORKING命令
        激活程序，开始正常工作
        """
        logger.info("命令处理器", "收到START_WORKING命令，程序激活")
        
        # 触发启动事件
        self.start_working_event.set()
        
        return {
            "success": True,
            "message": "已接收START_WORKING命令，程序开始工作"
        }
    
    def handle_reset_system(self, cmd_data: Dict) -> Dict:
        """
        处理RESET_SYSTEM命令
        重置系统到初始休眠状态，可被START_WORKING重新激活
        """
        logger.info("命令处理器", "收到RESET_SYSTEM命令，开始重置系统...")
        
        try:
            # 1. 触发重置事件（通知main.py停止当前操作）
            self.reset_system_event.set()
            
            # 2. 停止所有机器人的重连尝试
            for robot_id, robot in self.robots.items():
                if robot:
                    robot.stop_reconnect()
                    # 关闭现有连接
                    try:
                        if robot.is_connected():
                            robot.close()
                    except:
                        pass  # 忽略关闭时的错误
                    logger.info("命令处理器", f"已停止 {robot_id} 的重连")
            
            # 3. 清除所有等待事件
            self.start_working_event.clear()
            self.scan_enter_id_event.clear()
            self.pick_from_opener_500_event.clear()
            self.pick_from_opener_250_event.clear()
            
            # 4. 清除事件数据
            self.scan_enter_id_data = None
            self.pick_from_opener_500_data = None
            self.pick_from_opener_250_data = None
            
            # 5. 重置状态机
            self.task_state_machine.reset()
            
            # 6. 清空机器人字典（下次START_WORKING时重新创建）
            self.robots.clear()
            self.robot_a = None
            self.robot_b = None
            
            logger.info("命令处理器", "系统重置完成，已进入休眠状态，等待START_WORKING命令激活")
            
            return {
                "success": True,
                "message": "系统已重置到休眠状态，发送START_WORKING命令可重新激活"
            }
        except Exception as e:
            logger.exception_occurred("命令处理器", "重置系统", e)
            return make_error_response(
                ErrorCode.SYSTEM_RESET_FAILED,
                f"系统重置失败: {str(e)}"
            )
    
    def _execute_pickup_sequence(self, bottle: Any) -> bool:
        """执行拾取序列（内部辅助方法）"""
        # 抓取
        grab_result = self.robot_a.send_service_request(
            self.robot_a.get_robot_service(),
            "grab_object",
            extra_params={
                "strawberry": {
                    "type": bottle.object_type,
                    "target_pose": bottle.target_pose,
                    "hand": bottle.hand
                }
            }
        )
        
        if not grab_result:
            return False
        
        # 转腰
        self.robot_a.send_service_request(
            self.robot_a.get_robot_service(),
            "turn_waist",
            extra_params={"angle": "180", "obstacle_avoidance": True}
        )
        
        # 放置到后部平台
        back_pose = f"back_temp_{bottle.object_type.split('_')[-1]}_001"
        put_result = self.robot_a.send_service_request(
            self.robot_a.get_robot_service(),
            "put_object",
            extra_params={
                "strawberry": {
                    "type": bottle.object_type,
                    "target_pose": back_pose,
                    "hand": bottle.hand,
                    "safe_pose": "preset"
                }
            }
        )
        
        # 转回
        self.robot_a.send_service_request(
            self.robot_a.get_robot_service(),
            "turn_waist",
            extra_params={"angle": "0", "obstacle_avoidance": True}
        )
        
        if put_result:
            self.bottle_manager.place_bottle(bottle.bottle_id, back_pose)
        
        return put_result
    
    def _wait_for_navigation_finished(self, robot: RobotController = None) -> bool:
        """
        等待导航完成
        
        参数:
            robot: 机器人实例，如果为None则使用self.robot_a（向后兼容）
        """
        if robot is None:
            robot = self.robot_a
        status_names = {
            0: "NONE",
            1: "STANDBY，导航待机中",
            2: "PLANNING，导航规划中",
            3: "RUNNING，导航运行中",
            4: "STOPPING，导航停止中",
            5: "FINISHED，导航完成",
            6: "FAILURE，导航失败"
        }
        taskstatus_names = {
            0: "NONE",
            1: "RUNNING，运行中",
            2: "SUCCESS，运行成功",
            3: "FAILED，运行失败"
        }
        taskstatus_code_temp = 0
        while True:
            nav_status = self._wait_for_topic_message(ROSService.NAVIGATION_STATUS, timeout=10, retry_on_disconnect=True, robot=robot)
            if nav_status:
                taskstatus_code = nav_status.get("taskstate", 0).get("value", 0)
                taskstatus_name = taskstatus_names.get(taskstatus_code, f"UNKNOWN({taskstatus_code})")
                if taskstatus_code_temp != taskstatus_code:
                    logger.info("命令处理器", f"导航状态已更新: {taskstatus_code} ({taskstatus_name})")
                    print(f"✓ 导航状态已更新: {taskstatus_code} - {taskstatus_name}")
                    taskstatus_code_temp = taskstatus_code
                if taskstatus_code == 2:
                    return True
                elif taskstatus_code == 3:
                    logger.error("命令处理器", "导航失败")
                    self.task_state_machine.set_error("导航失败")
                    return False


    def _wait_for_topic_message(self, topic_name: str, timeout: float = 60.0, retry_on_disconnect: bool = True, robot: RobotController = None) -> Optional[Dict]:
        """
        等待并获取topic消息（支持连接断开重连）
        
        参数:
            topic_name: topic名称
            timeout: 超时时间（秒）
            retry_on_disconnect: 连接断开时是否等待重连
            robot: 机器人实例，如果为None则使用self.robot_a（向后兼容）
        
        返回:
            dict: topic消息，如果超时返回None
        """
        if robot is None:
            robot = self.robot_a
        
        import time
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            # 检查连接状态
            if not robot.is_connected():
                if retry_on_disconnect:
                    logger.warning("命令处理器", f"检测到连接断开，开始重连...")
                    print("⚠️  机器人连接已断开，开始重连...")
                    
                    # 主动触发重连
                    reconnect_success = robot.connect()
                    
                    if reconnect_success:
                        logger.info("命令处理器", "重连成功")
                        print("✓ 机器人重连成功")
                        
                        # 等待连接稳定
                        time.sleep(2)
                        
                        # 重新订阅topic
                        logger.info("命令处理器", f"重新订阅topic: {topic_name}")
                        print(f"[DEBUG] 开始重新订阅topic: {topic_name}")
                        
                        subscribe_success = robot.subscribe_topic(
                            topic_name=topic_name,
                            msg_type="navi_types/NavigationStatus",
                            throttle_rate=0,
                            queue_length=1
                        )
                        
                        if subscribe_success:
                            logger.info("命令处理器", "重新订阅成功")
                            print("✓ Topic重新订阅成功")
                            print("[DEBUG] 等待3秒让topic消息开始传输...")
                            time.sleep(3)  # 等待订阅生效并接收第一条消息
                        else:
                            logger.error("命令处理器", "重新订阅失败")
                            print("✗ Topic重新订阅失败")
                            return None
                    else:
                        logger.error("命令处理器", "重连失败")
                        print("✗ 机器人重连失败")
                        return None
                else:
                    logger.error("命令处理器", "连接断开，放弃等待")
                    return None
            
            # 尝试获取消息
            msg = robot.get_topic_message(topic_name)
            if msg:
                #print(f"[DEBUG] 成功获取topic消息: {topic_name}")
                return msg
            
            # 短暂等待后重试
            time.sleep(0.5)
        
        # 超时
        logger.warning("命令处理器", f"等待topic消息超时: {topic_name}")
        return None
    
    def _execute_putdown_sequence(self, bottle: Any, release_pose: str) -> bool:
        """执行放下序列（内部辅助方法）"""
        # 转腰到背面
        self.robot_a.send_service_request(
            self.robot_a.get_robot_service(),
            "turn_waist",
            extra_params={"angle": "180", "obstacle_avoidance": True}
        )
        
        # 从后部平台抓取
        grab_result = self.robot_a.send_service_request(
            self.robot_a.get_robot_service(),
            "grab_object",
            extra_params={
                "strawberry": {
                    "type": bottle.object_type,
                    "target_pose": bottle.location,
                    "hand": bottle.hand
                }
            }
        )
        
        if not grab_result:
            return False
        
        # 转回正面
        self.robot_a.send_service_request(
            self.robot_a.get_robot_service(),
            "turn_waist",
            extra_params={"angle": "0", "obstacle_avoidance": True}
        )
        
        # 放置到目标点位
        put_result = self.robot_a.send_service_request(
            self.robot_a.get_robot_service(),
            "put_object",
            extra_params={
                "strawberry": {
                    "type": bottle.object_type,
                    "target_pose": release_pose,
                    "hand": bottle.hand,
                    "safe_pose": "preset"
                }
            }
        )
        
        if put_result:
            self.bottle_manager.remove_bottle_from_pose(bottle.bottle_id, bottle.location)
            self.bottle_manager.place_bottle(bottle.bottle_id, release_pose)
        
        return put_result

    def _scan_and_store_bottles_loop(self, robot, storage_mgr, target_area: str, robot_id: str) -> bool:
        """
        扫码并放置到后部暂存区流程（循环处理所有瓶子）
        
        该方法封装了完整的瓶子扫码和存储流程，包括：
        1. CV检测瓶子
        2. 抓取瓶子
        3. 放置到扫描转盘
        4. 等待ID录入
        5. 从扫描转盘取回瓶子
        6. 放置到后部暂存区
        7. 转回正面
        
        参数:
            robot: 机器人控制器实例
            storage_mgr: 存储管理器实例
            robot_id: 机器人ID（用于区分不同机器人的暂存区）
            from_waiting_split_area: 是否从待分液区抓取（如果是则减少待分液区计数）
        
        返回:
            bool: 流程是否成功完成（True=成功/无更多瓶子，False=出错）
        """
        back_temp_storage = storage_mgr.get_storage(robot_id)
        station_counter = get_station_counter()
        
        while True:
            # 检查暂存区是否全部已满
            if check_storage_is_full(back_temp_storage):
                self.task_state_machine.set_error("暂存区已满")
                logger.error("命令处理器", "暂存区已满")
                return False
            
            # 瓶子类型依照瓶子区域决定
            if target_area == StationArea.WAITING_SPLIT_AREA:
                object_type = "glass_bottle_500"
            elif target_area == StationArea.EMPTY_BOTTLE_AREA:
                object_type = "glass_bottle_250"
            elif target_area == StationArea.SPLIT_DONE_500ML_AREA:
                object_type = "glass_bottle_500"
            elif target_area == StationArea.SPLIT_DONE_250ML_AREA:
                object_type = "glass_bottle_250"
            else:
                self.task_state_machine.set_error("未知目标区域")
                logger.error("命令处理器", "未知目标区域")
                return False
            
            # 检查对应类型暂存区是否已满
            empty_storage_index = storage_mgr.get_empty_slot_index(robot_id, object_type)
            if empty_storage_index is None:
                self.task_state_machine.set_error(f"{object_type}暂存区已满")
                logger.error("命令处理器", f"{object_type}暂存区已满")
                return False
            
            # 视觉检测瓶子
            input("cv detecting bottle...")
            self.task_state_machine.update_step(TaskStep.CV_DETECTING, "视觉检测瓶子")
            cv_detect_result = robot.send_service_request(
                robot.get_robot_service(),
                "cv_detect"
            )
            if not cv_detect_result:
                self.task_state_machine.set_error("视觉检测瓶子失败")
                logger.error("命令处理器", "视觉检测瓶子失败")
                return False
            else:
                self.task_state_machine.update_step(TaskStep.CV_DETECTING_SUCCESS, "视觉检测瓶子成功")
                logger.info("命令处理器", "视觉检测瓶子成功")
            
            # 步骤: 抓取瓶子
            input("pick bottle from scan table...")
            self.task_state_machine.update_step(TaskStep.GRABBING_BOTTLE, f"抓取瓶子 ({object_type})")
            grab_result = robot.send_service_request(
                robot.get_robot_service(),
                "pick_object",
                extra_params={
                    "type": object_type,
                }
            )
            
            if not grab_result:
                self.task_state_machine.set_error("抓取瓶子失败")
                logger.error("命令处理器", "抓取失败")
                return False
            
            
            # 步骤: 把瓶子放在旋转平台上
            input("putting bottle to scan table...")
            self.task_state_machine.update_step(TaskStep.PUT_TO_SCAN_MACHINE, "放置到扫描转盘")
            scan_result = robot.send_service_request(
                robot.get_robot_service(),
                "scan",
                extra_params={
                    "type": object_type,
                }
            )
            if not scan_result:
                self.task_state_machine.set_error("放置到扫描转盘失败")
                logger.error("命令处理器", "放置到扫描转盘失败")
                return False
            
            # 步骤: 等待ID录入
            self.scan_enter_id_event.clear()
            self.scan_enter_id_data = None
            
            print("\n" + "="*70)
            print("【等待ID录入】")
            print("请使用以下命令发送:")
            print(f"curl -X POST http://localhost:{HTTP_SERVER_PORT} -d @test_commands/SCAN_QRCODE_ENTER_ID_command.json")
            print("="*70 + "\n")
            
            logger.info("命令处理器", "等待SCAN_QRCODE_ENTER_ID消息...")
            self.task_state_machine.update_step(TaskStep.WAITING_ID_INPUT, "等待ID录入")
            
            if self.scan_enter_id_event.wait(timeout=150):
                if self.scan_enter_id_data:
                    scan_qrcode_enter_id_result = self.scan_enter_id_data.get("success")
                    bottle_id = self.scan_enter_id_data.get("bottle_id")
                    object_type_scan = self.scan_enter_id_data.get("type")
                    task = self.scan_enter_id_data.get("task") # 获取到任务是，能否解析这个任务，然后安排机器人接下来做的事情，比如“这个瓶子需要先去分液，然后再去色谱仪检测，等待检测结果出来后会有新的指令”，这个任务能被解析成一系列任务代码。
                    self.task_state_machine.update_step(TaskStep.ID_INPUT_SUCCESS, "ID录入成功")
                    logger.info("命令处理器", f"接收到瓶子信息: {bottle_id}, 类型: {object_type_scan}, 任务: {task}")
                    print(f"✓ 已接收到瓶子ID: {bottle_id}")
                else:
                    self.task_state_machine.set_error("接收数据异常")
                    logger.error("命令处理器", "接收到事件但数据为空")
                    return False
            else:
                self.task_state_machine.set_error("等待扫码ID录入超时")
                logger.error("命令处理器", "等待SCAN_QRCODE_ENTER_ID消息超时")
                return False
            
            if not scan_qrcode_enter_id_result:
                self.task_state_machine.set_error("扫描二维码ID录入失败")
                logger.error("命令处理器", "扫描二维码ID录入失败")
                return False
            
            print("✓ ID录入完成")

            # 机器人识别瓶子类型出错，需要更新暂存区位置
            if object_type != object_type_scan:
                object_type = object_type_scan
                empty_storage_index = storage_mgr.get_empty_slot_index(robot_id, object_type)
                if empty_storage_index is None:
                    self.task_state_machine.set_error(f"{object_type}暂存区已满")
                    logger.error("命令处理器", f"{object_type}暂存区已满")
                    return False

            # 步骤: 把瓶子从旋转平台拿回来
            input("picking bottle from scan table...")
            scan_back_result = robot.send_service_request(
                robot.get_robot_service(),
                "pick_scan_back",
                extra_params={
                    "type": object_type,
                }

            )
            if not scan_back_result:
                self.task_state_machine.set_error("从扫描转盘取回失败")
                logger.error("命令处理器", "从扫描转盘取回失败")
                return False
            
            # 步骤: 放置到后部暂存区
            if object_type == "glass_bottle_250":
                target_pose = f"back_temp_250_" + str(empty_storage_index)
            elif object_type == "glass_bottle_500":
                target_pose = f"back_temp_500_" + str(empty_storage_index)
            else:
                print(f"未知瓶子类型: {object_type}")
                logger.error("命令处理器", f"未知瓶子类型: {object_type}")
                return False
            self.task_state_machine.update_step(TaskStep.PUTTING_TO_BACK, f"放置到后部暂存区 slot_{empty_storage_index}")
            input("putting bottle to back storage...")
            put_result = robot.send_service_request(
                robot.get_robot_service(),
                "put_object_back",
                extra_params={
                    "type": object_type,
                    "target_pose": target_pose
                }
            )
            
            if not put_result:
                self.task_state_machine.set_error("放置到后部暂存区失败")
                logger.error("命令处理器", "放置失败")
                return False
            else:
                # 更新暂存区状态
                storage_mgr.update_slot(robot_id, object_type, empty_storage_index, bottle_id)
                storage_mgr.set_bottle_state(robot_id, object_type, empty_storage_index, BottleState.NOT_SPLIT)
                # 抓取完成，目标计数区域减少一瓶
                station_counter.decrement(target_area)
                # 记录已扫描的瓶子
                self.task_state_machine.add_scanned_bottle(bottle_id, object_type, empty_storage_index, task)
                logger.info("命令处理器", f"瓶子 {bottle_id} 已放置到 {object_type}[{empty_storage_index}]")
            
            # 步骤: 转回正面
            input("turning back front...")
            self.task_state_machine.update_step(TaskStep.TURNING_BACK_FRONT, "转回正面")
            turn_waist_result = robot.send_service_request(
                robot.get_robot_service(),
                "back_to_front",
            )
            if not turn_waist_result:
                self.task_state_machine.set_error("转回正面失败")
                logger.error("命令处理器", "转腰失败")
                return False
            
            # 继续下一个瓶子（循环）

    def _scan_and_store_bottles_loop_press_button(self, robot, storage_mgr, target_area: str, robot_id: str) -> bool:
        """
        扫码并放置到后部暂存区流程（循环处理所有瓶子）
        
        该方法封装了完整的瓶子扫码和存储流程，包括：
        1. CV检测瓶子
        2. 抓取瓶子
        3. 放置到扫描转盘
        4. 按下按钮并等待ID录入
        5. 从扫描转盘取回瓶子
        6. 放置到后部暂存区
        7. 转回正面
        
        参数:
            robot: 机器人控制器实例
            storage_mgr: 存储管理器实例
            robot_id: 机器人ID（用于区分不同机器人的暂存区）
        
        返回:
            bool: 流程是否成功完成（True=成功/无更多瓶子，False=出错）
        """
        back_temp_storage = storage_mgr.get_storage(robot_id)
        station_counter = get_station_counter()
        while True:
            # 检查暂存区是否全部已满
            if check_storage_is_full(back_temp_storage):
                self.task_state_machine.set_error("暂存区已满")
                logger.error("命令处理器", "暂存区已满")
                return False
            
            # 步骤: CV检测
            self.task_state_machine.update_step(TaskStep.CV_DETECTING, "视觉检测瓶子")
            cv_detect_result, object_pose, object_type = robot.send_service_request(
                robot.get_robot_service(),
                "cv_detect"
            )
            
            if not cv_detect_result:
                logger.info("命令处理器", "检测不到更多瓶子，扫码任务完成")
                self.task_state_machine.update_step(TaskStep.CV_DETECTING_EMPTY, "视觉检测瓶子已抓完")
                return True  # 没有更多瓶子，成功完成
            else:
                self.task_state_machine.update_step(TaskStep.CV_DETECTING_SUCCESS, "视觉检测瓶子成功")
                logger.info("命令处理器", "视觉检测瓶子成功")
            
            # 检查对应类型暂存区是否已满
            empty_storage_index = storage_mgr.get_empty_slot_index(robot_id, object_type)
            if empty_storage_index is None:
                self.task_state_machine.set_error(f"{object_type}暂存区已满")
                logger.error("命令处理器", f"{object_type}暂存区已满")
                return False
            
            # 步骤: 抓取瓶子
            self.task_state_machine.update_step(TaskStep.GRABBING_BOTTLE, f"抓取瓶子 ({object_type})")
            grab_result = robot.send_service_request(
                robot.get_robot_service(),
                "grab_object_scan_table",
                extra_params={
                    "type": object_type,
                    "target_pose": object_pose,
                }
            )
            
            if not grab_result:
                self.task_state_machine.set_error("抓取瓶子失败")
                logger.error("命令处理器", "抓取失败")
                return False
            
            # 步骤: 把瓶子放在旋转平台上
            self.task_state_machine.update_step(TaskStep.PUT_TO_SCAN_MACHINE, "放置到扫描转盘")
            scan_result = robot.send_service_request(
                robot.get_robot_service(),
                "scan"
            )
            if not scan_result:
                self.task_state_machine.set_error("放置到扫描转盘失败")
                logger.error("命令处理器", "放置到扫描转盘失败")
                return False
            
            # 步骤: 按下按钮让平台旋转（异步）+ 等待ID录入（并行执行）
            self.scan_enter_id_event.clear()
            self.scan_enter_id_data = None
            # 储存按钮变量结果
            press_button_result_holder = {"result": None, "completed": False}
                
            def press_button_async():
                """异步执行按下按钮动作"""
                try:
                    logger.info("命令处理器", "开始按下按钮让平台旋转...")
                    result = self.robot_a.send_service_request(
                        "/get_strawberry_service",
                        "press_button"
                    )
                    press_button_result_holder["result"] = result
                    press_button_result_holder["completed"] = True
                    if result:
                        logger.info("命令处理器", "按下按钮成功，平台开始旋转")
                        print("✓ 按下按钮成功，平台开始旋转")
                    else:
                        logger.error("命令处理器", "按下按钮失败")
                        print("✗ 按下按钮失败")
                except Exception as e:
                    logger.exception_occurred("命令处理器", "按下按钮异常", e)
                    press_button_result_holder["result"] = False
                    press_button_result_holder["completed"] = True
            
            # 启动按钮线程
            press_button_thread = threading.Thread(target=press_button_async, daemon=True)
            press_button_thread.start()
            logger.info("命令处理器", "按下按钮线程已启动，同时开始等待ID录入")

            print("\n" + "="*70)
            print("【并行执行中】")
            print("  1. 平台旋转中...")
            print("  2. 等待HTTP发送SCAN_QRCODE_ENTER_ID消息...")
            print("请使用以下命令发送:")
            print(f"curl -X POST http://localhost:{HTTP_SERVER_PORT} -d @test_commands/SCAN_QRCODE_ENTER_ID_command.json")
            print("="*70 + "\n")
            
            # 等待ID录入事件（与按钮动作并行）
            logger.info("命令处理器", "等待SCAN_QRCODE_ENTER_ID消息...")
            self.task_state_machine.update_step(TaskStep.WAITING_ID_INPUT, "按下按钮+等待ID录入")
            
            if self.scan_enter_id_event.wait(timeout=150):
                if self.scan_enter_id_data:
                    scan_qrcode_enter_id_result = self.scan_enter_id_data.get("success")
                    bottle_id = self.scan_enter_id_data.get("bottle_id")
                    object_type_scan = self.scan_enter_id_data.get("type")
                    task = self.scan_enter_id_data.get("task")
                    self.task_state_machine.update_step(TaskStep.ID_INPUT_SUCCESS, "按下按钮+ID录入成功")
                    logger.info("命令处理器", f"接收到瓶子信息: {bottle_id}, 类型: {object_type_scan}, 任务: {task}")
                    print(f"✓ 已接收到瓶子ID: {bottle_id}")
                else:
                    self.task_state_machine.set_error("接收数据异常")
                    logger.error("命令处理器", "接收到事件但数据为空")
                    return
            else:
                self.task_state_machine.set_error("等待扫码ID录入超时")
                logger.error("命令处理器", "等待SCAN_QRCODE_ENTER_ID消息超时")
                return
            
            # 等待按钮线程完成（如果还没完成的话）
            if press_button_thread.is_alive():
                logger.info("命令处理器", "ID已录入，等待按钮动作完成...")
                print("等待平台旋转完成...")
                press_button_thread.join(timeout=60)  # 最多等60秒
            
            # 检查按钮结果
            if not press_button_result_holder["completed"]:
                self.task_state_machine.set_error("按压按钮超时")
                logger.error("命令处理器", "按压按钮超时")
                return
                
            if not press_button_result_holder["result"]:
                self.task_state_machine.set_error("按压按钮失败")
                logger.error("命令处理器", "按压按钮失败")
                return
            
            if not scan_qrcode_enter_id_result:
                self.task_state_machine.set_error("扫描二维码ID录入失败")
                logger.error("命令处理器", "扫描二维码ID录入失败")
                bottle_id = "unknown"
                object_type_scan = "unknown"
                task = "unknown"
                return
            
            print("✓ 按钮动作和ID录入都已完成")

            # 机器人识别瓶子类型出错，需要更新暂存区位置
            if object_type != object_type_scan:
                object_type = object_type_scan
                empty_storage_index = storage_mgr.get_empty_slot_index(robot_id, object_type)
                if empty_storage_index is None:
                    self.task_state_machine.set_error(f"{object_type}暂存区已满")
                    logger.error("命令处理器", f"{object_type}暂存区已满")
                    return False
            
            # 步骤: 把瓶子从旋转平台拿回来
            scan_back_result = robot.send_service_request(
                robot.get_robot_service(),
                "pick_scan_back",
            )
            if not scan_back_result:
                self.task_state_machine.set_error("从扫描转盘取回失败")
                logger.error("命令处理器", "从扫描转盘取回失败")
                return False
            
            # 步骤: 放置到后部暂存区
            if object_type == "glass_bottle_250":
                target_pose = f"back_temp_250_" + str(empty_storage_index)
            elif object_type == "glass_bottle_500":
                target_pose = f"back_temp_500_" + str(empty_storage_index)
            else:
                print(f"未知瓶子类型: {object_type}")
                logger.error("命令处理器", f"未知瓶子类型: {object_type}")
                return False
            self.task_state_machine.update_step(TaskStep.PUTTING_TO_BACK, f"放置到后部暂存区 slot_{empty_storage_index}")
            put_result = robot.send_service_request(
                robot.get_robot_service(),
                "put_object_back",
                extra_params={
                    "type": object_type,
                    "target_pose": target_pose
                }
            )
            
            if not put_result:
                self.task_state_machine.set_error("放置到后部暂存区失败")
                logger.error("命令处理器", "放置失败")
                return False
            else:
                # 更新暂存区状态
                storage_mgr.update_slot(robot_id, object_type, empty_storage_index, bottle_id)
                storage_mgr.set_bottle_state(robot_id, object_type, empty_storage_index, BottleState.NOT_SPLIT)
                # 抓取完成，目标计数区域减少一瓶
                station_counter.decrement(target_area)
                # 记录已扫描的瓶子
                self.task_state_machine.add_scanned_bottle(bottle_id, object_type, empty_storage_index, task)
                logger.info("命令处理器", f"瓶子 {bottle_id} 已放置到 {object_type}[{empty_storage_index}]")
            
            # 步骤: 转回正面
            self.task_state_machine.update_step(TaskStep.TURNING_BACK_FRONT, "转回正面")
            turn_waist_result = robot.send_service_request(
                robot.get_robot_service(),
                "back_to_front",
            )
            if not turn_waist_result:
                self.task_state_machine.set_error("转回正面失败")
                logger.error("命令处理器", "转腰失败")
                return False
            
            # 继续下一个瓶子（循环）

    def store_bottles_loop(self, robot, storage_mgr, robot_id: str, target_area: str, bottle_msg: Dict) -> bool:
        """
        不扫码直接放置到后部暂存区流程（循环处理所有瓶子）
        
        该方法封装了完整的瓶子扫码和存储流程，包括：
        1. CV检测瓶子
        2. 抓取瓶子
        3. 放置到后部暂存区
        4. 转回正面
        
        参数:
            robot: 机器人控制器实例
            storage_mgr: 存储管理器实例
            robot_id: 机器人ID（用于区分不同机器人的暂存区）
            from_waiting_split_area: 是否从待分液区抓取（如果是则减少待分液区计数）
        
        返回:
            bool: 流程是否成功完成（True=成功/无更多瓶子，False=出错）
        """
        back_temp_storage = storage_mgr.get_storage(robot_id)
        station_counter = get_station_counter()
        
        while True:
            # 检查暂存区是否全部已满
            if check_storage_is_full(back_temp_storage):
                self.task_state_machine.set_error("暂存区已满")
                logger.error("命令处理器", "暂存区已满")
                return True
            
            # 步骤: CV检测
            self.task_state_machine.update_step(TaskStep.CV_DETECTING, "视觉检测瓶子")
            cv_detect_result, object_pose, object_type = robot.send_service_request(
                robot.get_robot_service(),
                "cv_detect"
            )
            
            if not cv_detect_result:
                logger.info("命令处理器", "检测不到更多瓶子，扫码任务完成")
                self.task_state_machine.update_step(TaskStep.CV_DETECTING_EMPTY, "视觉检测瓶子已抓完")
                return True  # 没有更多瓶子，成功完成
            else:
                self.task_state_machine.update_step(TaskStep.CV_DETECTING_SUCCESS, "视觉检测瓶子成功")
                logger.info("命令处理器", "视觉检测瓶子成功")
            
            # 检查视觉识别结果是否和指定结果一样
            if object_type != bottle_msg.get("object_type"):
                self.task_state_machine.set_error(f"视觉识别结果和指定结果不一样，识别结果: {object_type}，指定结果: {bottle_msg.get('object_type')}")
                logger.error("命令处理器", f"视觉识别结果和指定结果不一样，识别结果: {object_type}，指定结果: {bottle_msg.get('object_type')}")
                return False
            
            # 模拟瓶子ID和类型
            bottle_id = bottle_msg.get("bottle_id")
            object_type = bottle_msg.get("object_type")
            task = bottle_msg.get("task")

            # 检查对应类型暂存区是否已满
            empty_storage_index = storage_mgr.get_empty_slot_index(robot_id, object_type)
            if empty_storage_index is None:
                self.task_state_machine.set_error(f"{object_type}暂存区已满")
                logger.error("命令处理器", f"{object_type}暂存区已满")
                return True
            
            # 步骤: 抓取瓶子
            self.task_state_machine.update_step(TaskStep.GRABBING_BOTTLE, f"抓取瓶子 ({object_type})")
            grab_result = robot.send_service_request(
                robot.get_robot_service(),
                "grab_object_scan_table",
                extra_params={
                    "type": object_type,
                    "target_pose": object_pose,
                }
            )
            
            if not grab_result:
                self.task_state_machine.set_error("抓取瓶子失败")
                logger.error("命令处理器", "抓取失败")
                return False
            
            # 步骤: 放置到后部暂存区
            if object_type == "glass_bottle_250":
                target_pose = f"back_temp_250_" + str(empty_storage_index)
            elif object_type == "glass_bottle_500":
                target_pose = f"back_temp_500_" + str(empty_storage_index)
            else:
                print(f"未知瓶子类型: {object_type}")
                logger.error("命令处理器", f"未知瓶子类型: {object_type}")
                return False
            
            self.task_state_machine.update_step(TaskStep.PUTTING_TO_BACK, f"放置到后部暂存区 slot_{empty_storage_index}")
            put_result = robot.send_service_request(
                robot.get_robot_service(),
                "put_object_back",
                extra_params={
                    "type": object_type,
                    "target_pose": target_pose
                }
            )
            
            if not put_result:
                self.task_state_machine.set_error("放置到后部暂存区失败")
                logger.error("命令处理器", "放置失败")
                return False
            else:
                # 更新暂存区状态
                storage_mgr.update_slot(robot_id, object_type, empty_storage_index, bottle_id)
                storage_mgr.set_bottle_state(robot_id, object_type, empty_storage_index, BottleState.NOT_SPLIT)
                # 抓取完成，目标计数区域减少一瓶
                station_counter.decrement(target_area)
                # 记录已扫描的瓶子
                self.task_state_machine.add_scanned_bottle(bottle_id, object_type, empty_storage_index, task)
                logger.info("命令处理器", f"瓶子 {bottle_id} 已放置到 {object_type}[{empty_storage_index}]")
            
            # 步骤: 转回正面
            self.task_state_machine.update_step(TaskStep.TURNING_BACK_FRONT, "转回正面")
            turn_waist_result = robot.send_service_request(
                robot.get_robot_service(),
                "back_to_front",
            )
            if not turn_waist_result:
                self.task_state_machine.set_error("转回正面失败")
                logger.error("命令处理器", "转腰失败")
                return False
            
            # 继续下一个瓶子（循环）

    def _put_bottles_loop(self, robot, storage_mgr, target_area: str, all_bottles_in_storage: List[Dict], robot_id: str) -> bool:
        """
        把后部暂存区瓶子放到指定位置流程（循环处理所有瓶子）(这里特定为已分液完成的瓶子)
        
        参数:
            robot: 机器人控制器实例
            storage_mgr: 存储管理器实例
            target_area: 目标计数区域
            robot_id: 机器人ID
        """        
        # 遍历所有瓶子，执行放置动作
        for i, bottle_info in enumerate(all_bottles_in_storage):
            #input("press enter to continue...")
            bottle_id = bottle_info["bottle_id"]
            slot_index = bottle_info["slot_index"]
            bottle_type = bottle_info["bottle_type"]
            
            print(f"\n处理第 {i+1}/{len(all_bottles_in_storage)} 个瓶子:")
            print(f"  瓶子ID: {bottle_id}")
            print(f"  类型: {bottle_type}")
            print(f"  槽位: {slot_index}")
            
            self.task_state_machine.update_step(
                TaskStep.PUTTING_DOWN_BOTTLE, 
                f"放下瓶子 {i+1}/{len(all_bottles_in_storage)}: {bottle_id}"
            )
            
            # 执行放置动作
            input("putting down bottle to split table..." + f"back_temp_{bottle_type}_" + str(slot_index))
            put_down_result = robot.send_service_request(
                robot.get_robot_service(),
                "put_down_split_table",
                extra_params={
                    "target_pose": f"back_temp_{bottle_type}_" + str(slot_index)
                }
            )
            if not put_down_result:
                error_msg = f"放置瓶子 {bottle_id} 失败"
                logger.error("命令处理器", error_msg)
                self.task_state_machine.set_error(error_msg)
                return
            else:
                # 更新暂存区状态
                storage_mgr.update_slot(robot_id, bottle_type, slot_index, 0)
                # 分液台待分液区增加一瓶
                # 如果target_area值在StationCounter中存在，则增加计数
                if target_area in StationCounter.__dict__:
                    station_counter = get_station_counter()
                    station_counter.increment(target_area)
                
            print(f"✓ 瓶子 {bottle_id} 放置完成")
            logger.info("命令处理器", f"瓶子 {bottle_id} 放置完成")

# 全局命令处理器实例
_cmd_handler = None

def init_cmd_handler(robots: Dict[str, RobotController] = None):
    """
    初始化命令处理器
    
    参数:
        robots: 机器人字典，key为robot_id，value为RobotController实例
    """
    global _cmd_handler
    _cmd_handler = CmdHandler(robots)
    robot_count = len(robots) if robots else 0
    logger.info("命令处理器", f"命令处理器初始化完成，管理 {robot_count} 台机器人")

def get_cmd_handler():
    """获取命令处理器实例"""
    global _cmd_handler
    if _cmd_handler is None:
        raise RuntimeError("命令处理器未初始化，请先调用init_cmd_handler")
    return _cmd_handler

def get_empty_storage_index(storage: Dict[str, List], object_type: str, robot_id: str = "robot_a") -> Optional[int]:
    """
    获取指定类型暂存区中第一个空位的索引
    注意：此函数保留是为了兼容性，建议使用 storage_manager.get_empty_slot_index()
    """
    storage_mgr = get_storage_manager()
    return storage_mgr.get_empty_slot_index(robot_id, object_type)

def check_storage_is_full(storage: Dict[str, List]) -> bool:
    """
    检查暂存区是否已满（兼容函数）
    注意：此函数直接检查传入的storage字典，不使用robot_id
    """
    for bottle_type, slots in storage.items():
        for slot in slots:
            if slot == 0:
                return False
    return True