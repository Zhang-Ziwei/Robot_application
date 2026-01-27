#!/usr/bin/env python3
"""
模拟 ROS Bridge WebSocket 服务器
接收来自控制系统的命令并返回模拟响应
"""

import asyncio
import websockets
import json
import random
import time

# 服务器配置
HOST = "0.0.0.0"
PORT = 9091

# 导航状态模拟
nav_status = {"state": {"value": 1}, "taskstate": {"value": 0}}  # STANDBY


class MockRosBridge:
    def __init__(self):
        self.clients = set()
        self.subscribed_topics = {}  # {client: [topics]}
        
    async def handle_client(self, websocket):
        """处理客户端连接"""
        self.clients.add(websocket)
        client_addr = websocket.remote_address
        print(f"[连接] 客户端已连接: {client_addr}")
        
        try:
            async for message in websocket:
                await self.process_message(websocket, message)
        except websockets.exceptions.ConnectionClosed:
            print(f"[断开] 客户端已断开: {client_addr}")
        finally:
            self.clients.discard(websocket)
            if websocket in self.subscribed_topics:
                del self.subscribed_topics[websocket]
    
    async def process_message(self, websocket, message):
        """处理收到的消息"""
        try:
            data = json.loads(message)
            op = data.get("op")
            
            print(f"\n[收到] op={op}")
            print(f"  数据: {json.dumps(data, ensure_ascii=False, indent=2)}")
            
            if op == "call_service":
                await self.handle_service_call(websocket, data)
            elif op == "subscribe":
                await self.handle_subscribe(websocket, data)
            elif op == "unsubscribe":
                await self.handle_unsubscribe(websocket, data)
            else:
                print(f"[警告] 未知操作: {op}")
                
        except json.JSONDecodeError:
            print(f"[错误] JSON解析失败: {message}")
    
    async def handle_service_call(self, websocket, data):
        """处理服务调用"""
        service = data.get("service", "")
        args = data.get("args", {})
        action = args.get("action", "")
        
        print(f"[服务] {service} -> {action}")
        
        # 模拟处理时间
        await asyncio.sleep(0.5)
        
        # 根据不同的action返回不同的响应
        response = self.generate_response(service, action, args)
        
        await websocket.send(json.dumps(response))
        print(f"[响应] {json.dumps(response, ensure_ascii=False)}")
    
    def generate_response(self, service, action, args):
        """生成服务响应"""
        base_response = {
            "op": "service_response",
            "service": service,
            "result": True
        }
        
        # 根据action类型生成特定响应
        if action == "cv_detect":
            # 模拟CV检测结果
            detected = random.choice([True, True, True, False])  # 75%概率检测到
            if detected:
                base_response["values"] = {
                    "success": True,
                    "object_pose": f"pose_{random.randint(0, 5)}",
                    "object_type": random.choice(["glass_bottle_500", "plastic_bottle_350", "can_330"])
                }
            else:
                base_response["values"] = {"success": False}
                
        elif action == "navigation_to_pose":
            # 导航请求 - 启动导航状态模拟
            asyncio.create_task(self.simulate_navigation())
            base_response["values"] = {"success": True, "message": "导航已启动"}
            
        elif action in ["grab_object", "grab_object_scan_table", "put_object", "put_object_back", 
                        "scan", "press_button", "pick_scan_back", "back_to_front", 
                        "put_down_split_table", "turn_waist"]:
            base_response["values"] = {"success": True}
            
        else:
            base_response["values"] = {"success": True, "message": f"执行: {action}"}
        
        return base_response
    
    async def simulate_navigation(self):
        """模拟导航状态变化"""
        global nav_status
        
        # 规划中
        nav_status = {"state": {"value": 2}, "taskstate": {"value": 1}}
        await asyncio.sleep(1)
        
        # 运行中
        nav_status = {"state": {"value": 3}, "taskstate": {"value": 1}}
        await asyncio.sleep(3)
        
        # 完成
        nav_status = {"state": {"value": 5}, "taskstate": {"value": 2}}
    
    async def handle_subscribe(self, websocket, data):
        """处理topic订阅"""
        topic = data.get("topic", "")
        print(f"[订阅] {topic}")
        
        if websocket not in self.subscribed_topics:
            self.subscribed_topics[websocket] = []
        
        if topic not in self.subscribed_topics[websocket]:
            self.subscribed_topics[websocket].append(topic)
            
            # 启动topic发布任务
            if topic == "/navigation_status":
                asyncio.create_task(self.publish_navigation_status(websocket, topic))
    
    async def handle_unsubscribe(self, websocket, data):
        """处理取消订阅"""
        topic = data.get("topic", "")
        print(f"[取消订阅] {topic}")
        
        if websocket in self.subscribed_topics and topic in self.subscribed_topics[websocket]:
            self.subscribed_topics[websocket].remove(topic)
    
    async def publish_navigation_status(self, websocket, topic):
        """持续发布导航状态"""
        print(f"[发布] 开始发布 {topic}")
        
        while websocket in self.clients:
            if websocket in self.subscribed_topics and topic in self.subscribed_topics[websocket]:
                try:
                    msg = {
                        "op": "publish",
                        "topic": topic,
                        "msg": nav_status
                    }
                    await websocket.send(json.dumps(msg))
                except:
                    break
            else:
                break
            await asyncio.sleep(0.5)  # 2Hz发布频率
        
        print(f"[发布] 停止发布 {topic}")


async def main():
    server = MockRosBridge()
    
    print("="*50)
    print("Mock ROS Bridge Server")
    print("="*50)
    print(f"监听: ws://{HOST}:{PORT}")
    print("支持的操作:")
    print("  - call_service: 服务调用")
    print("  - subscribe: 订阅topic")
    print("  - unsubscribe: 取消订阅")
    print("="*50)
    print("等待连接...\n")
    
    async with websockets.serve(
        server.handle_client, 
        HOST, 
        PORT,
        subprotocols=['rosbridge_v2']
    ):
        await asyncio.Future()  # 永久运行


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n服务器已停止")

