"""
任务状态机管理模块
管理任务的执行状态和步骤追踪
"""

import threading
import time
from typing import Dict, List, Optional, Any
from datetime import datetime
from enum import Enum
from error_logger import get_error_logger

logger = get_error_logger()


class TaskStep(Enum):
    """任务流程步骤定义"""
    IDLE = "空闲"
    NAVIGATING_TO_SCAN = "导航到扫描台"
    GRAB_SCAN_GUN = "抓取扫描枪"
    CV_DETECTING = "视觉检测瓶子"
    GRABBING_BOTTLE = "抓取瓶子"
    SCANNING = "扫描二维码"
    WAITING_ID_INPUT = "等待ID录入"
    PUTTING_TO_BACK = "放置到后部平台"
    TURNING_BACK_FRONT = "转回正面"
    NAVIGATING_TO_SPLIT = "导航到分液台"
    PUTTING_DOWN = "放下瓶子"
    COMPLETED = "完成"
    ERROR = "错误"


class TaskStatus(Enum):
    """任务状态"""
    NOT_STARTED = "未开始"
    RUNNING = "运行中"
    WAITING = "等待中"
    COMPLETED = "已完成"
    ERROR = "错误"
    CANCELLED = "已取消"


class TaskStateMachine:
    """任务状态机"""
    
    def __init__(self):
        self.task_id: Optional[str] = None
        self.status: TaskStatus = TaskStatus.NOT_STARTED
        self.current_step: TaskStep = TaskStep.IDLE
        self.completed_steps: List[Dict[str, Any]] = []
        self.error_message: Optional[str] = None
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        
        # 扫描结果
        self.scanned_bottles: List[Dict[str, Any]] = []
        self.current_bottle_info: Optional[Dict[str, Any]] = None
        
        # 线程锁
        self.lock = threading.Lock()
        
        logger.info("状态机", "任务状态机已初始化")
    
    def start_task(self, task_id: str):
        """开始新任务"""
        with self.lock:
            self.task_id = task_id
            self.status = TaskStatus.RUNNING
            self.current_step = TaskStep.IDLE
            self.completed_steps = []
            self.error_message = None
            self.start_time = time.time()
            self.end_time = None
            self.scanned_bottles = []
            self.current_bottle_info = None
            
            logger.info("状态机", f"任务开始: {task_id}")
    
    def update_step(self, step: TaskStep, message: str = ""):
        """更新当前步骤"""
        with self.lock:
            # 记录上一步完成
            if self.current_step != TaskStep.IDLE:
                self.completed_steps.append({
                    "step": self.current_step.name,
                    "step_name": self.current_step.value,
                    "message": message,
                    "timestamp": datetime.now().isoformat(),
                    "duration": time.time() - self.start_time if self.start_time else 0
                })
            
            # 更新当前步骤
            self.current_step = step
            
            # 更新状态
            if step == TaskStep.WAITING_ID_INPUT:
                self.status = TaskStatus.WAITING
            elif step == TaskStep.COMPLETED:
                self.status = TaskStatus.COMPLETED
                self.end_time = time.time()
            elif step == TaskStep.ERROR:
                self.status = TaskStatus.ERROR
                self.end_time = time.time()
            else:
                self.status = TaskStatus.RUNNING
            
            logger.info("状态机", f"步骤更新: {step.value} - {message}")
    
    def set_waiting_id(self, bottle_info: Dict[str, Any]):
        """设置等待ID录入状态"""
        with self.lock:
            self.current_bottle_info = bottle_info
            self.update_step(TaskStep.WAITING_ID_INPUT, f"等待录入瓶子ID (类型: {bottle_info.get('type', 'unknown')})")
    
    def add_scanned_bottle(self, bottle_id: str, bottle_type: str, slot_index: int):
        """添加已扫描的瓶子"""
        with self.lock:
            self.scanned_bottles.append({
                "bottle_id": bottle_id,
                "type": bottle_type,
                "slot_index": slot_index,
                "timestamp": datetime.now().isoformat()
            })
            logger.info("状态机", f"瓶子已扫描: {bottle_id}")
    
    def set_error(self, error_msg: str):
        """设置错误状态"""
        with self.lock:
            self.error_message = error_msg
            self.status = TaskStatus.ERROR
            self.current_step = TaskStep.ERROR
            self.end_time = time.time()
            
            logger.error("状态机", f"任务错误: {error_msg}")
    
    def complete_task(self, success: bool = True, message: str = ""):
        """完成任务"""
        with self.lock:
            if success:
                self.status = TaskStatus.COMPLETED
                self.current_step = TaskStep.COMPLETED
            else:
                self.status = TaskStatus.ERROR
                self.current_step = TaskStep.ERROR
                self.error_message = message
            
            self.end_time = time.time()
            
            logger.info("状态机", f"任务完成: success={success}, message={message}")
    
    def get_state(self, query_task_id: Optional[str] = None) -> Dict[str, Any]:
        """
        获取任务状态
        
        参数:
            query_task_id: 要查询的任务ID，如果为None则返回当前任务状态
        
        返回:
            任务状态字典
        """
        with self.lock:
            # 如果指定了task_id但与当前任务不匹配，返回未找到
            if query_task_id and query_task_id != self.task_id:
                return {
                    "cmd_id": query_task_id,
                    "status": "未找到",
                    "message": f"任务ID不匹配或任务不存在: {query_task_id}",
                    "current_task_id": self.task_id
                }
            
            # 返回当前任务状态（或指定ID匹配的任务状态）
            duration = None
            if self.start_time:
                duration = (self.end_time or time.time()) - self.start_time
            
            return {
                "cmd_id": self.task_id,
                "status": self.status.value,
                "current_step": {
                    "name": self.current_step.name,
                    "description": self.current_step.value
                },
                "completed_steps": self.completed_steps.copy(),
                "scanned_bottles": self.scanned_bottles.copy(),
                "current_bottle_info": self.current_bottle_info.copy() if self.current_bottle_info else None,
                "error_message": self.error_message,
                "start_time": datetime.fromtimestamp(self.start_time).isoformat() if self.start_time else None,
                "end_time": datetime.fromtimestamp(self.end_time).isoformat() if self.end_time else None,
                "duration_seconds": round(duration, 2) if duration else None,
                "scanned_count": len(self.scanned_bottles)
            }
    
    def reset(self):
        """重置状态机"""
        with self.lock:
            self.task_id = None
            self.status = TaskStatus.NOT_STARTED
            self.current_step = TaskStep.IDLE
            self.completed_steps = []
            self.error_message = None
            self.start_time = None
            self.end_time = None
            self.scanned_bottles = []
            self.current_bottle_info = None
            
            logger.info("状态机", "状态机已重置")


# 全局状态机实例
_task_state_machine = None

def get_task_state_machine() -> TaskStateMachine:
    """获取状态机实例（单例）"""
    global _task_state_machine
    if _task_state_machine is None:
        _task_state_machine = TaskStateMachine()
    return _task_state_machine

