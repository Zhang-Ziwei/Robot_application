import asyncio
import websockets
import json
import threading
import time
from constants import RobotType

class RobotController:
    def __init__(self, host, port, robot_type, max_retry_attempts=None, retry_interval=5):
        self.host = host
        self.port = port
        self.robot_type = robot_type
        self.robot_name = "Robot A" if robot_type == RobotType.ROBOT_A else "Robot B"
        self.connected = False
        self.websocket = None
        self.mutex = threading.Lock()
        self.loop = None
        self.thread = None
        # 重连配置
        self.max_retry_attempts = max_retry_attempts  # None表示无限重试
        self.retry_interval = retry_interval  # 重试间隔（秒）
        self.retry_count = 0  # 当前重试次数
        
    def connect(self):
        """连接到机器人WebSocket服务，支持自动重试"""
        attempt = 0
        
        while True:
            with self.mutex:
                if self.connected:
                    print(f"✓ {self.robot_name} 已连接")
                    self.retry_count = 0  # 重置重试计数
                    return True
                
                attempt += 1
                self.retry_count = attempt
                
                # 检查是否超过最大重试次数
                if self.max_retry_attempts is not None and attempt > self.max_retry_attempts:
                    print(f"✗ {self.robot_name} 连接失败：已达到最大重试次数 ({self.max_retry_attempts})")
                    return False
                
                # 显示重试信息
                if attempt == 1:
                    print(f"\n{'='*60}")
                    print(f"正在连接 {self.robot_name} ({self.host}:{self.port})...")
                    if self.max_retry_attempts is None:
                        print(f"重试策略：无限重试，间隔 {self.retry_interval} 秒")
                    else:
                        print(f"重试策略：最多 {self.max_retry_attempts} 次，间隔 {self.retry_interval} 秒")
                    print(f"{'='*60}\n")
                else:
                    print(f"\n[重试 {attempt}/{self.max_retry_attempts if self.max_retry_attempts else '∞'}] 尝试连接 {self.robot_name}...")
                
                # 清理之前的连接
                if self.loop and self.loop.is_running():
                    self.loop.call_soon_threadsafe(self.loop.stop())
                if self.thread and self.thread.is_alive():
                    self.thread.join(timeout=2)
                
                # 创建新的事件循环并在单独的线程中运行
                self.loop = asyncio.new_event_loop()
                self.thread = threading.Thread(target=self._run_event_loop, daemon=True)
                self.thread.start()
                
                # 等待连接完成
                start_time = time.time()
                timeout = 10  # 10秒超时
                while not self.connected and (time.time() - start_time) < timeout:
                    time.sleep(0.1)
                
                # 检查是否连接成功
                if self.connected:
                    print(f"✓ {self.robot_name} 连接成功！")
                    self.retry_count = 0
                    return True
            
            # 连接失败，等待后重试
            print(f"✗ {self.robot_name} 连接失败")
            if self.max_retry_attempts is None or attempt < self.max_retry_attempts:
                print(f"⏳ 等待 {self.retry_interval} 秒后重试...")
                time.sleep(self.retry_interval)
            else:
                return False
    
    def _run_event_loop(self):
        """在单独线程中运行事件循环"""
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._async_connect())
        # 保持事件循环运行以处理后续的异步操作
        self.loop.run_forever()
    
    async def _async_connect(self):
        """异步连接到WebSocket服务器"""
        import socket
        
        # 先进行网络诊断
        print(f"\n=== {self.robot_name} 网络诊断 ===")
        print(f"目标地址: {self.host}:{self.port}")
        
        # 1. 检查 DNS 解析（如果是域名）
        try:
            ip_addr = socket.gethostbyname(self.host)
            print(f"✓ DNS 解析成功: {self.host} -> {ip_addr}")
        except socket.gaierror as e:
            print(f"✗ DNS 解析失败: {e}")
        
        # 2. 检查 TCP 连接
        print(f"正在测试 TCP 连接到 {self.host}:{self.port}...")
        tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_socket.settimeout(5)
        try:
            tcp_socket.connect((self.host, int(self.port)))
            print(f"✓ TCP 连接成功")
            tcp_socket.close()
        except socket.timeout:
            print(f"✗ TCP 连接超时 - 可能被防火墙阻止或服务未运行")
            self.connected = False
            return
        except ConnectionRefusedError:
            print(f"✗ 连接被拒绝 - 端口 {self.port} 上没有服务监听")
            self.connected = False
            return
        except Exception as e:
            print(f"✗ TCP 连接失败: {type(e).__name__}: {e}")
            self.connected = False
            return
        
        # 3. 尝试 WebSocket 连接
        print(f"正在建立 WebSocket 连接...")
        try:
            uri = f"ws://{self.host}:{self.port}/"
            print(f"WebSocket URI: {uri}")
            
            # 尝试带 rosbridge 协议
            try:
                self.websocket = await websockets.connect(
                    uri,
                    subprotocols=['rosbridge_v2'],
                    ping_timeout=20,
                    close_timeout=10
                )
                print(f"✓ WebSocket 连接成功 (使用 rosbridge_v2 协议)")
            except Exception as e1:
                # 如果 rosbridge 协议失败，尝试不带协议
                print(f"rosbridge_v2 协议失败，尝试标准 WebSocket...")
                self.websocket = await websockets.connect(
                    uri,
                    ping_timeout=20,
                    close_timeout=10
                )
                print(f"✓ WebSocket 连接成功 (标准协议)")
            
            self.connected = True
            print(f"✓ {self.robot_name} 已成功连接到 {self.host}:{self.port}")
            
        except Exception as e:
            print(f"✗ WebSocket 连接失败:")
            print(f"   错误类型: {type(e).__name__}")
            print(f"   错误信息: {str(e)}")
            import traceback
            traceback.print_exc()
            self.connected = False
    
    def is_connected(self):
        """检查连接状态"""
        return self.connected
    
    def send_service_request(self, service, action, type=-1, maxtime=120, extra_params=None):
        """发送服务请求到机器人，支持自动重连"""
        
        # 如果连接已断开，先尝试重连
        if not self.connected:
            print(f"⚠ {self.robot_name} 连接已断开，尝试重新连接...")
            if not self.connect():
                print(f"✗ {self.robot_name} 重连失败")
                return False
        
        with self.mutex:
            # 详细的连接状态检查
            if not self.websocket:
                print(f"✗ {self.robot_name} WebSocket 对象为空，尝试重连...")
                self.connected = False
                # 释放锁后重连
                self.mutex.release()
                result = self.connect()
                self.mutex.acquire()
                if not result:
                    return False
            
            if not self.loop:
                print(f"✗ {self.robot_name} 事件循环未初始化，尝试重连...")
                self.connected = False
                self.mutex.release()
                result = self.connect()
                self.mutex.acquire()
                if not result:
                    return False
            
            if not self.loop.is_running():
                print(f"✗ {self.robot_name} 事件循环未运行，尝试重连...")
                self.connected = False
                self.mutex.release()
                result = self.connect()
                self.mutex.acquire()
                if not result:
                    return False
            
            print(f"✓ {self.robot_name} 连接状态正常，准备发送请求")

            try:
                # 构建请求
                request = {
                    "op": "call_service",
                    "service": service,
                    "args": {"action": action}
                }

                if type != -1:
                    request["args"]["strawberry"] = {"type": type}

                if extra_params:
                    for key, value in extra_params.items():
                        request["args"][key] = value

                request_str = json.dumps(request, indent=4)
                print(f"{self.robot_name} sending request:\n{request_str}")

                # 在事件循环中执行异步发送和接收
                print(f"[DEBUG] 提交异步任务到事件循环...")
                future = asyncio.run_coroutine_threadsafe(
                    self._async_send_and_receive(request_str, maxtime),
                    self.loop
                )

                # 等待结果，设置超时
                print(f"[DEBUG] 等待响应（超时{maxtime}秒）...")
                result = future.result(maxtime)
                print(f"[DEBUG] 收到响应结果: {result}")
                return result
                
            except Exception as e:
                print(f"✗ {self.robot_name} 通信错误: {str(e)}")
                # 发生错误时标记为断开并尝试重连
                self.connected = False
                print(f"⚠ {self.robot_name} 检测到通信异常，尝试重连...")
                self.mutex.release()
                reconnect_result = self.connect()
                self.mutex.acquire()
                
                if reconnect_result:
                    print(f"✓ {self.robot_name} 重连成功，请重试发送请求")
                else:
                    print(f"✗ {self.robot_name} 重连失败")
                
                return False

    async def _async_send_and_receive(self, request_str, maxtime=60):
        """异步发送请求并等待响应"""
        try:
            print(f"[DEBUG] 发送消息到机器人...")
            await self.websocket.send(request_str)
            print(f"[DEBUG] 消息已发送，等待机器人响应（最长{maxtime}秒）...")
            
            # 超时时间应该与外层的 future.result() 超时一致
            response_str = await asyncio.wait_for(self.websocket.recv(), timeout=maxtime)
            print(f"✓ {self.robot_name} 收到响应:\n{response_str}")
            
            response = json.loads(response_str)
            print(f"[DEBUG] 解析后的响应: {response}")
            
            # 检查响应格式
            if "values" not in response:
                print(f"⚠ {self.robot_name} 响应缺少 'values' 字段")
                print(f"   完整响应结构: {list(response.keys())}")
                
                # 有些 rosbridge 响应可能直接包含 result
                if "result" in response:
                    print(f"[DEBUG] 检测到直接的 result 字段: {response['result']}")
                    return response["result"]
                return False
            
            values = response["values"]
            print(f"[DEBUG] values 内容: {values}")
            
            # 检查 result 字段
            has_result = "result" in response
            result_value = response.get("result", False)
            print(f"[DEBUG] result 字段存在: {has_result}, 值: {result_value}")
            
            # 检查 finish 字段
            has_finish = "finish" in values
            finish_value = values.get("finish", False)
            print(f"[DEBUG] finish 字段存在: {has_finish}, 值: {finish_value}")
            
            # 判断操作是否成功
            operation_success = (result_value and finish_value)
            
            if operation_success:
                print(f"✓ {self.robot_name} 操作成功完成")
                return True
            else:
                print(f"✗ {self.robot_name} 操作未完成")
                if "remaining" in values:
                    print(f"   剩余项: {values['remaining']}")
                print(f"   result={result_value}, finish={finish_value}")
                return False
                
        except asyncio.TimeoutError:
            print(f"✗ {self.robot_name} 读取超时（{maxtime}秒）")
            self.connected = False  # 标记为断开
            return False
        except websockets.exceptions.ConnectionClosed as e:
            print(f"✗ {self.robot_name} WebSocket连接已关闭: {e}")
            self.connected = False  # 标记为断开
            return False
        except Exception as e:
            print(f"✗ {self.robot_name} 异步通信错误: {type(e).__name__}: {str(e)}")
            self.connected = False  # 标记为断开
            return False
    
    def close(self):
        """关闭与机器人的连接"""
        with self.mutex:
            if self.connected and self.websocket and self.loop:
                try:
                    # 异步关闭连接
                    future = asyncio.run_coroutine_threadsafe(
                        self.websocket.close(), 
                        self.loop
                    )
                    future.result(5)  # 5秒超时
                    print(f"{self.robot_name} disconnected from {self.host}:{self.port}")
                except Exception as e:
                    print(f"{self.robot_name} close error: {str(e)}")
                
                self.connected = False
            
            # 停止事件循环
            if self.loop and self.loop.is_running():
                self.loop.call_soon_threadsafe(self.loop.stop())
            
            # 等待线程结束
            if self.thread and self.thread.is_alive():
                self.thread.join()
