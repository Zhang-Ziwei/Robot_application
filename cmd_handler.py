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
from constants import HTTP_SERVER_PORT
from task_state_machine import get_task_state_machine, TaskStep

logger = get_error_logger()

class CmdHandler:
    """命令处理器"""
    
    def __init__(self, robot_a: RobotController, robot_b: RobotController):
        self.robot_a = robot_a
        self.robot_b = robot_b
        self.bottle_manager = get_bottle_manager()
        self.task_optimizer = get_task_optimizer()
        # 等待SCAN_QRCODE_ENTER_ID的事件和数据
        self.scan_enter_id_event = threading.Event()
        self.scan_enter_id_data = None
        # 等待START_WORKING命令的事件
        self.start_working_event = threading.Event()
        # 状态机
        self.task_state_machine = get_task_state_machine()
    
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
            "PICK_UP": self.handle_pickup,
            "PUT_TO": self.handle_put_to,
            "TAKE_BOTTOL_FROM_SP_TO_SP": self.handle_transfer,
            "SCAN_QRCODE": self.handle_scan_qrcode,
            "SCAN_QRCODE_ENTER_ID": self.handle_scan_qrcode_enter_id,
            "GET_TASK_STATE": self.handle_get_task_state,
            "ENTER_ID": self.handle_enter_id,
            "BOTTLE_GET": self.handle_bottle_get,
        }
        
        handler = handlers.get(cmd_type)
        if not handler:
            error_msg = f"未知的命令类型: {cmd_type}"
            logger.error("命令处理器", error_msg)
            return {
                "cmd_id": cmd_id,
                "success": False,
                "message": error_msg
            }
        
        try:
            result = handler(cmd_data)
            result["cmd_id"] = cmd_id
            return result
        except Exception as e:
            logger.exception_occurred("命令处理器", f"处理命令{cmd_type}", e)
            return {
                "cmd_id": cmd_id,
                "success": False,
                "message": f"命令执行异常: {str(e)}"
            }
    
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
            self.robot_a.send_service_request("/navigation_status", "waiting_navigation_status")
            
            # 导航到目标点位
            result = self.robot_a.send_service_request(
                "/navigation_status", 
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
                    "/get_strawberry_service",
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
                    "/get_strawberry_service",
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
                    "/get_strawberry_service",
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
                    "/get_strawberry_service",
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
            self.robot_a.send_service_request("/navigation_status", "waiting_navigation_status")
            
            # 导航到目标点位
            result = self.robot_a.send_service_request(
                "/navigation_status",
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
                    "/get_strawberry_service",
                    "turn_waist",
                    extra_params={
                        "angle": "180",
                        "obstacle_avoidance": True
                    }
                )
                
                # 2. 从后部平台抓取
                grab_result = self.robot_a.send_service_request(
                    "/get_strawberry_service",
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
                    "/get_strawberry_service",
                    "turn_waist",
                    extra_params={
                        "angle": "0",
                        "obstacle_avoidance": True
                    }
                )
                
                # 4. 放置到目标点位
                put_result = self.robot_a.send_service_request(
                    "/get_strawberry_service",
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
                self.robot_a.send_service_request("/navigation_status", "waiting_navigation_status")
                self.robot_a.send_service_request(
                    "/navigation_status",
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
                self.robot_a.send_service_request("/navigation_status", "waiting_navigation_status")
                self.robot_a.send_service_request(
                    "/navigation_status",
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
        """
        # 生成唯一任务ID
        task_id = f"SCAN_QRCODE_{uuid.uuid4().hex[:8]}"
        cmd_id = cmd_data.get("cmd_id")
        logger.info("命令处理器", f"开始SCAN_QRCODE任务: {cmd_id}")
        
        # 初始化状态机
        self.task_state_machine.start_task(cmd_id)
        
        # 在后台线程执行扫码任务
        scan_thread = threading.Thread(
            target=self._execute_scan_qrcode_async,
            args=(cmd_id,),
            daemon=True,
            name=f"ScanThread-{cmd_id}"
        )
        scan_thread.start()
        
        # 立即返回任务ID
        return {
            "success": True,
            "message": "SCAN_QRCODE任务已启动",
            "cmd_id": cmd_id,
            "note": "使用 GET_TASK_STATE 命令查询任务状态"
        }
    
    def _execute_scan_qrcode_async(self, task_id: str):
        """异步执行SCAN_QRCODE任务（后台线程）"""
        try:
            logger.info("命令处理器", f"任务 {task_id} 开始执行")
            
            #input("press enter to continue...")
            # 订阅导航状态topic
            logger.info("命令处理器", "订阅 /navigation_status topic")
            subscribe_success = self.robot_a.subscribe_topic(
                topic_name="/navigation_status",
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
            #input("press enter to continue...")
            
            navigation_to_pose_result = self.robot_a.send_service_request(
                "/get_strawberry_service",
                "navigation_to_pose",
                extra_params={"position": "scan_table"}
            )
            # 导航到最后一段会断网
            # 导航后检查导航状态（支持连接断开重连）
            
            waiting_navigation_status_result = self._wait_for_navigation_finished()
            #print("waiting_navigation_status_result: ", waiting_navigation_status_result)
            if not waiting_navigation_status_result:
                logger.error("命令处理器", "导航到扫描台失败")
                print("导航到扫描台失败")
                return
            # 步骤2: 抓取扫描枪（如果需要）
            # self.task_state_machine.update_step(TaskStep.GRAB_SCAN_GUN, "抓取扫描枪")
            # ... 抓取扫描枪的代码 ...
            '''grab_object_result = self.robot_a.send_service_request(
                "/get_strawberry_service",
                "grab_object",
                extra_params={
                    "type": "scan_gun",
                    "target_pose": "scan_gun",
                    "hand": "right"
                }
            )'''
            
            # 循环处理瓶子
            #input("press enter to continue...")
            while True:
                # 检查暂存区是否全部已满
                if check_storage_is_full(back_temp_storage):
                    self.task_state_machine.set_error("暂存区已满")
                    logger.error("命令处理器", "暂存区已满")
                    return
                
                # 步骤3: CV检测
                self.task_state_machine.update_step(TaskStep.CV_DETECTING, "视觉检测瓶子")
                cv_detect_result, object_pose, object_type = self.robot_a.send_service_request(
                    "/get_strawberry_service",
                    "cv_detect"
                )
                
                if not cv_detect_result:
                    logger.info("命令处理器", "检测不到更多瓶子，扫码任务完成")
                    self.task_state_machine.update_step(TaskStep.CV_DETECTING_EMPTY, "视觉检测瓶子已抓完")
                    break
                else:
                    self.task_state_machine.update_step(TaskStep.CV_DETECTING_SUCCESS, "视觉检测瓶子成功")
                    logger.info("命令处理器", "视觉检测瓶子成功")
                #input("press enter to continue...")
                
                # 检查对应类型暂存区是否已满
                empty_storage_index = storage_mgr.get_empty_slot_index(object_type)
                if empty_storage_index is None:
                    self.task_state_machine.set_error(f"{object_type}暂存区已满")
                    logger.error("命令处理器", f"{object_type}暂存区已满")
                    return
                
                # 步骤4: 抓取瓶子
                self.task_state_machine.update_step(TaskStep.GRABBING_BOTTLE, f"抓取瓶子 ({object_type})")
                grab_result = self.robot_a.send_service_request(
                    "/get_strawberry_service",
                    "grab_object_scan_table",
                    extra_params={
                        "type": object_type,
                        "target_pose": object_pose,
                    }
                )
                
                if not grab_result:
                    self.task_state_machine.set_error("抓取瓶子失败")
                    logger.error("命令处理器", "抓取失败")
                    return
                
                #input("press enter to continue...")
                


                # 步骤5: 把瓶子放在旋转平台上,并按下按钮让平台旋转
                self.task_state_machine.update_step(TaskStep.PUT_TO_SCAN_MACHINE, "放置到扫描转盘")
                scan_result = self.robot_a.send_service_request(
                    "/get_strawberry_service",
                    "scan"
                )
                if not scan_result:
                    self.task_state_machine.set_error("扫描二维码失败")
                    logger.error("命令处理器", "扫描失败")
                    return
                # 步骤6: 按下按钮让平台旋转（异步）+ 等待ID录入（并行执行）
                #input("press enter to continue...")
                
                # 清除之前的事件和数据
                self.scan_enter_id_event.clear()
                self.scan_enter_id_data = None
                
                # 用于存储按钮结果的变量
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
                    # 检查对应类型暂存区是否已满
                    empty_storage_index = storage_mgr.get_empty_slot_index(object_type)
                    if empty_storage_index is None:
                        self.task_state_machine.set_error(f"{object_type}暂存区已满")
                        logger.error("命令处理器", f"{object_type}暂存区已满")
                        return
                
                # 步骤7: 把瓶子从旋转平台拿回来
                scan_back_result = self.robot_a.send_service_request(
                    "/get_strawberry_service",
                    "pick_scan_back",
                )
                if not scan_back_result:
                    self.task_state_machine.set_error("扫描后退失败")
                    logger.error("命令处理器", "扫描后退失败")
                    return
                #input("press enter to continue...")
                
                # 步骤8: 放置到后部平台
                self.task_state_machine.update_step(TaskStep.PUTTING_TO_BACK, f"放置到后部平台 slot_{empty_storage_index}")
                put_result = self.robot_a.send_service_request(
                    "/get_strawberry_service",
                    "put_object_back",
                    extra_params={
                        "type": object_type,
                        "target_pose": "point_" + str(empty_storage_index)
                    }
                )
                
                if not put_result:
                    self.task_state_machine.set_error("放置到后部平台失败")
                    logger.error("命令处理器", "放置失败")
                    return
                else:
                    # 更新暂存区状态
                    storage_mgr.update_slot(object_type, empty_storage_index, bottle_id)
                    # 记录已扫描的瓶子
                    self.task_state_machine.add_scanned_bottle(bottle_id, object_type, empty_storage_index, task)
                    logger.info("命令处理器", f"瓶子 {bottle_id} 已放置到 {object_type}[{empty_storage_index}]")
                
                #input("press enter to continue...")
                
                # 步骤9: 转回正面
                self.task_state_machine.update_step(TaskStep.TURNING_BACK_FRONT, "转回正面")
                turn_waist_result = self.robot_a.send_service_request(
                    "/get_strawberry_service",
                    "back_to_front",
                )
                if not turn_waist_result:
                    self.task_state_machine.set_error("转回正面失败")
                    logger.error("命令处理器", "转腰失败")
                    return
                # 继续下一个瓶子（循环）
            #input("press enter to continue...")
            
            # 步骤10: 导航到分液台
            self.task_state_machine.update_step(TaskStep.NAVIGATING_TO_SPLIT, "导航到分液台")
            navigation_to_pose_result = self.robot_a.send_service_request(
                "/get_strawberry_service",
                "navigation_to_pose",
                extra_params={"position": "split_table"}
            )

            waiting_navigation_status_result = self._wait_for_navigation_finished()
            if not waiting_navigation_status_result:
                return
            #input("press enter to continue...")
            
            # 步骤11: 放下暂存区所有瓶子到分液台
            self.task_state_machine.update_step(TaskStep.PUTTING_DOWN, "放下暂存区所有瓶子到分液台")
            
            # 获取暂存区所有瓶子
            all_bottles_in_storage = []
            for bottle_type, slots in back_temp_storage.items():
                for slot_index, bottle_id in enumerate(slots):
                    if bottle_id != 0:  # 不为空的槽位
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
                put_down_result = self.robot_a.send_service_request(
                    "/get_strawberry_service",
                    "put_down_split_table",
                    extra_params={
                        "target_pose": "pick_point_" + str(slot_index)
                    }
                )
                if not put_down_result:
                    error_msg = f"放置瓶子 {bottle_id} 失败"
                    logger.error("命令处理器", error_msg)
                    self.task_state_machine.set_error(error_msg)
                    return
                else:
                    # 更新暂存区状态
                    storage_mgr.update_slot(bottle_type, slot_index, 0)
                    
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
            self.robot_a.unsubscribe_topic("/navigation_status")
    
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
            return {
                "success": False,
                "message": state.get("message"),
                "current_task_id": state.get("current_task_id")
            }
        
        return {
            "success": True,
            "message": "状态查询成功",
            "data": state
        }
    
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
            return {
                "success": False,
                "message": "缺少bottle_id或type参数"
            }
        
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
    
    def _execute_pickup_sequence(self, bottle: Any) -> bool:
        """执行拾取序列（内部辅助方法）"""
        # 抓取
        grab_result = self.robot_a.send_service_request(
            "/get_strawberry_service",
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
            "/get_strawberry_service",
            "turn_waist",
            extra_params={"angle": "180", "obstacle_avoidance": True}
        )
        
        # 放置到后部平台
        back_pose = f"back_temp_{bottle.object_type.split('_')[-1]}_001"
        put_result = self.robot_a.send_service_request(
            "/get_strawberry_service",
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
            "/get_strawberry_service",
            "turn_waist",
            extra_params={"angle": "0", "obstacle_avoidance": True}
        )
        
        if put_result:
            self.bottle_manager.place_bottle(bottle.bottle_id, back_pose)
        
        return put_result
    
    def _wait_for_navigation_finished(self) -> bool:
        """等待导航完成"""
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
            nav_status = self._wait_for_topic_message("/navigation_status", timeout=10, retry_on_disconnect=True)
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
        '''status_code=1
        while status_code == 1:
            nav_status = self._wait_for_topic_message("/navigation_status", timeout=10, retry_on_disconnect=True)
            if nav_status:
                status_code = nav_status.get("state", 0).get("value", 0)
            time.sleep(1)

        while status_code != 1:
            nav_status = self._wait_for_topic_message("/navigation_status", timeout=10, retry_on_disconnect=True)
            if nav_status:
                # NavigationStatus状态码映射
                status_code = nav_status.get("state", 0).get("value", 0)
                status_name = status_names.get(status_code, f"UNKNOWN({status_code})")
                logger.info("命令处理器", f"导航状态: {status_code} ({status_name})")
                print(f"✓ 导航状态已更新: {status_code} - {status_name}")
                # 检查是否导航失败
                if status_code == 6:  # FAILURE
                    logger.error("命令处理器", "导航失败")
                    self.task_state_machine.set_error("导航失败")
                    return False
                elif status_code == 1:
                    self.task_state_machine.update_step(TaskStep.NAVIGATING_TO_SCAN, status_name)
                    return True
                time.sleep(1)
            else:
                logger.warning("命令处理器", "未收到导航状态消息")
                print("⚠️  未收到导航状态消息")
                return False'''


    def _wait_for_topic_message(self, topic_name: str, timeout: float = 60.0, retry_on_disconnect: bool = True) -> Optional[Dict]:
        """
        等待并获取topic消息（支持连接断开重连）
        
        参数:
            topic_name: topic名称
            timeout: 超时时间（秒）
            retry_on_disconnect: 连接断开时是否等待重连
        
        返回:
            dict: topic消息，如果超时返回None
        """
        import time
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            # 检查连接状态
            if not self.robot_a.is_connected():
                if retry_on_disconnect:
                    logger.warning("命令处理器", f"检测到连接断开，开始重连...")
                    print("⚠️  机器人连接已断开，开始重连...")
                    
                    # 主动触发重连
                    reconnect_success = self.robot_a.connect()
                    
                    if reconnect_success:
                        logger.info("命令处理器", "重连成功")
                        print("✓ 机器人重连成功")
                        
                        # 等待连接稳定
                        time.sleep(2)
                        
                        # 重新订阅topic
                        logger.info("命令处理器", f"重新订阅topic: {topic_name}")
                        print(f"[DEBUG] 开始重新订阅topic: {topic_name}")
                        
                        subscribe_success = self.robot_a.subscribe_topic(
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
            msg = self.robot_a.get_topic_message(topic_name)
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
            "/get_strawberry_service",
            "turn_waist",
            extra_params={"angle": "180", "obstacle_avoidance": True}
        )
        
        # 从后部平台抓取
        grab_result = self.robot_a.send_service_request(
            "/get_strawberry_service",
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
            "/get_strawberry_service",
            "turn_waist",
            extra_params={"angle": "0", "obstacle_avoidance": True}
        )
        
        # 放置到目标点位
        put_result = self.robot_a.send_service_request(
            "/get_strawberry_service",
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


# 全局命令处理器实例
_cmd_handler = None

def init_cmd_handler(robot_a: RobotController, robot_b: RobotController):
    """初始化命令处理器"""
    global _cmd_handler
    _cmd_handler = CmdHandler(robot_a, robot_b)
    logger.info("命令处理器", "命令处理器初始化完成")

def get_cmd_handler():
    """获取命令处理器实例"""
    global _cmd_handler
    if _cmd_handler is None:
        raise RuntimeError("命令处理器未初始化，请先调用init_cmd_handler")
    return _cmd_handler

def get_empty_storage_index(storage: Dict[str, List], object_type: str) -> Optional[int]:
    """
    获取指定类型暂存区中第一个空位的索引
    注意：此函数保留是为了兼容性，建议使用 storage_manager.get_empty_slot_index()
    """
    storage_mgr = get_storage_manager()
    return storage_mgr.get_empty_slot_index(object_type)

def check_storage_is_full(storage: Dict[str, List]) -> bool:
    """
    检查所有暂存区是否已满
    注意：此函数保留是为了兼容性，建议使用 storage_manager.is_full()
    """
    storage_mgr = get_storage_manager()
    return storage_mgr.is_full()