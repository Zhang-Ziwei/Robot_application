"""
模拟机器人控制器
用于在没有实际机器人连接的情况下测试信息传递
"""

import json
from typing import Dict, Any
from error_logger import get_error_logger

logger = get_error_logger()


class MockRobotController:
    """模拟机器人控制器 - 用于测试"""
    
    def __init__(self, host: str, port: str, robot_type: str, 
                 max_retry_attempts=None, retry_interval=5):
        self.host = host
        self.port = port
        self.robot_type = robot_type
        self.robot_name = f"Mock Robot ({host}:{port})"
        self.connected = True  # 模拟模式始终显示已连接
        self.request_log = []  # 记录所有请求
        
        logger.info("模拟机器人", f"初始化模拟机器人: {self.robot_name}")
        print(f"\n{'='*70}")
        print(f"🤖 模拟机器人初始化")
        print(f"{'='*70}")
        print(f"机器人名称: {self.robot_name}")
        print(f"地址: {host}:{port}")
        print(f"类型: {robot_type}")
        print(f"模式: 模拟测试模式（不需要实际连接）")
        print(f"{'='*70}\n")
    
    def connect(self):
        """模拟连接"""
        print(f"\n{'='*70}")
        print(f"🔗 {self.robot_name} - 模拟连接")
        print(f"{'='*70}")
        print(f"✓ 跳过实际连接（模拟模式）")
        print(f"✓ 模拟连接成功")
        print(f"{'='*70}\n")
        
        logger.info("模拟机器人", f"{self.robot_name} 模拟连接成功")
        self.connected = True
        return True
    
    def is_connected(self):
        """检查连接状态"""
        return self.connected
    
    def send_service_request(self, service: str, action: str, 
                            type: int = -1, maxtime: int = 60, 
                            extra_params: Dict = None) -> bool:
        """
        模拟发送服务请求
        记录并显示所有会发送给机器人的信息
        """
        # 构建完整的请求
        request = {
            "op": "call_service",
            "service": service,
            "args": {
                "action": action
            }
        }
        
        if type != -1:
            request["args"]["strawberry"] = {"type": type}
        
        if extra_params:
            for key, value in extra_params.items():
                request["args"][key] = value
        
        # 记录请求
        self.request_log.append(request)
        
        # 显示请求信息
        print(f"\n{'─'*70}")
        print(f"📤 {self.robot_name} - 发送请求")
        print(f"{'─'*70}")
        print(f"服务: {service}")
        print(f"动作: {action}")
        
        if type != -1:
            print(f"类型: {type}")
        
        if extra_params:
            print(f"\n额外参数:")
            for key, value in extra_params.items():
                if isinstance(value, dict):
                    print(f"  {key}:")
                    for k, v in value.items():
                        print(f"    - {k}: {v}")
                else:
                    print(f"  {key}: {value}")
        
        print(f"\n完整JSON请求:")
        print(json.dumps(request, indent=2, ensure_ascii=False))
        print(f"{'─'*70}")
        
        # 模拟响应
        print(f"✓ 模拟执行成功")
        print(f"✓ 返回结果: True")
        print(f"{'─'*70}\n")
        
        logger.info("模拟机器人", 
                   f"{self.robot_name} 模拟请求 - service={service}, action={action}")
        
        return True
    
    def close(self):
        """关闭连接"""
        print(f"\n{'='*70}")
        print(f"🔌 {self.robot_name} - 断开连接")
        print(f"{'='*70}")
        print(f"✓ 模拟断开连接")
        print(f"总共发送了 {len(self.request_log)} 个请求")
        print(f"{'='*70}\n")
        
        logger.info("模拟机器人", f"{self.robot_name} 模拟断开连接")
        self.connected = False
    
    def get_request_log(self):
        """获取所有请求日志"""
        return self.request_log
    
    def print_request_summary(self):
        """打印请求摘要"""
        print(f"\n{'='*70}")
        print(f"📊 {self.robot_name} - 请求统计")
        print(f"{'='*70}")
        print(f"总请求数: {len(self.request_log)}")
        
        # 按服务统计
        service_count = {}
        action_count = {}
        
        for req in self.request_log:
            service = req.get("service", "unknown")
            action = req["args"].get("action", "unknown")
            
            service_count[service] = service_count.get(service, 0) + 1
            action_count[action] = action_count.get(action, 0) + 1
        
        print(f"\n按服务统计:")
        for service, count in service_count.items():
            print(f"  - {service}: {count}次")
        
        print(f"\n按动作统计:")
        for action, count in action_count.items():
            print(f"  - {action}: {count}次")
        
        print(f"{'='*70}\n")
    
    def save_requests_to_file(self, filename: str = "mock_requests_log.json"):
        """保存所有请求到文件"""
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(self.request_log, f, indent=2, ensure_ascii=False)
        
        print(f"✓ 请求日志已保存到: {filename}")
        logger.info("模拟机器人", f"请求日志已保存: {filename}")

