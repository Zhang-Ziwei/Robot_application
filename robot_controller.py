import asyncio
import websockets
import json
import threading
import time
from constants import RobotType
from error_logger import get_error_logger

class RobotController:
    def __init__(self, host, port=None, robot_type=None, max_retry_attempts=None, retry_interval=5):
        self.host = host
        self.port = port  # None表示不使用端口（WiFi连接时）
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
        # Topic订阅相关
        self.subscribed_topics = {}  # {topic_name: msg_type}
        self.topic_messages = {}  # {topic_name: latest_message}
        self._topic_listener_started = False  # 监听器是否已启动
        # 服务请求响应队列
        self.service_response_future = None  # 用于存储等待的服务响应
    
    def _get_address_str(self):
        """获取地址字符串（用于显示）"""
        if self.port:
            return f"{self.host}:{self.port}"
        else:
            return self.host
    
    def _get_uri(self):
        """获取WebSocket URI"""
        if self.port:
            return f"ws://{self.host}:{self.port}/"
        else:
            return f"ws://{self.host}/"
        
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
                    error_msg = f"连接失败：已达到最大重试次数 ({self.max_retry_attempts})"
                    print(f"✗ {self.robot_name} {error_msg}")
                    # 记录错误日志
                    get_error_logger().connection_failed(
                        self.robot_name, self.host, self.port, error_msg
                    )
                    return False
                
                # 显示重试信息
                if attempt == 1:
                    print(f"\n{'='*60}")
                    print(f"正在连接 {self.robot_name} ({self._get_address_str()})...")
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
                
                # 重置topic监听器标志（重连时需要重新启动）
                self._topic_listener_started = False
                
                # 清空之前的topic订阅记录和消息缓存
                self.subscribed_topics.clear()
                self.topic_messages.clear()
                
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
                    # 记录连接成功
                    get_error_logger().connection_success(
                        self.robot_name, self.host, self.port, attempt
                    )
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
        print(f"目标地址: {self._get_address_str()}")
        
        # 1. 检查 DNS 解析（如果是域名）
        try:
            ip_addr = socket.gethostbyname(self.host)
            print(f"✓ DNS 解析成功: {self.host} -> {ip_addr}")
        except socket.gaierror as e:
            print(f"✗ DNS 解析失败: {e}")
            get_error_logger().connection_failed(
                self.robot_name, self.host, self.port, f"DNS解析失败: {e}"
            )
        
        # 2. 检查 TCP 连接（仅在指定端口时）
        if self.port:
            print(f"正在测试 TCP 连接到 {self._get_address_str()}...")
            tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            tcp_socket.settimeout(5)
            try:
                tcp_socket.connect((self.host, int(self.port)))
                print(f"✓ TCP 连接成功")
                tcp_socket.close()
            except socket.timeout:
                error_msg = "TCP 连接超时 - 可能被防火墙阻止或服务未运行"
                print(f"✗ {error_msg}")
                get_error_logger().connection_failed(
                    self.robot_name, self.host, self.port, error_msg
                )
                self.connected = False
                return
            except ConnectionRefusedError:
                error_msg = f"连接被拒绝 - 端口 {self.port} 上没有服务监听"
                print(f"✗ {error_msg}")
                get_error_logger().connection_failed(
                    self.robot_name, self.host, self.port, error_msg
                )
                self.connected = False
                return
            except Exception as e:
                error_msg = f"TCP 连接失败: {type(e).__name__}: {e}"
                print(f"✗ {error_msg}")
                get_error_logger().exception_occurred(
                    self.robot_name, "TCP连接", e
                )
                self.connected = False
                return
        else:
            print(f"跳过 TCP 连接测试（WiFi模式，无端口号）")
        
        # 3. 尝试 WebSocket 连接
        print(f"正在建立 WebSocket 连接...")
        try:
            uri = self._get_uri()
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
            print(f"✓ {self.robot_name} 已成功连接到 {self._get_address_str()}")
            
        except Exception as e:
            print(f"✗ WebSocket 连接失败:")
            print(f"   错误类型: {type(e).__name__}")
            print(f"   错误信息: {str(e)}")
            import traceback
            traceback.print_exc()
            # 记录WebSocket连接失败
            get_error_logger().exception_occurred(
                self.robot_name, "WebSocket连接", e
            )
            self.connected = False
    
    def is_connected(self):
        """检查连接状态"""
        return self.connected
    
    def send_service_request(self, service, action, type=-1, maxtime=600, extra_params=None):
        """发送服务请求到机器人，支持自动重连"""
        
        # 如果连接已断开，先尝试重连
        if not self.connected:
            print(f"⚠ {self.robot_name} 连接已断开，尝试重新连接...")
            get_error_logger().warning(self.robot_name, "发送请求前检测到连接断开，尝试重连")
            if not self.connect():
                print(f"✗ {self.robot_name} 重连失败")
                get_error_logger().error(self.robot_name, "重连失败，无法发送请求")
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
                        request["args"]["strawberry"] = {key: value}

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
                
                # 记录请求结果
                if result:
                    get_error_logger().request_success(self.robot_name, service, action)
                else:
                    get_error_logger().request_failed(self.robot_name, service, action, "机器人返回失败")
                
                return result
                
            except Exception as e:
                print(f"✗ {self.robot_name} 通信错误: {str(e)}")
                # 记录通信异常
                get_error_logger().exception_occurred(self.robot_name, "发送请求", e)
                
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

    async def _async_send_and_receive(self, request_str, maxtime=120):
        """异步发送请求并等待响应"""
        try:
            # 创建一个Future用于接收响应
            self.service_response_future = asyncio.Future()
            
            print(f"[DEBUG] 发送消息到机器人...")
            await self.websocket.send(request_str)
            print(f"[DEBUG] 消息已发送，等待机器人响应（最长{maxtime}秒）...")
            
            # 通过Future等待统一监听器传递的响应
            response_str = await asyncio.wait_for(self.service_response_future, timeout=maxtime)
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
            # 检查cv_detect字段
            has_target_pose_return = "target_pose_return" in values
            target_pose_return_value = values.get("target_pose_return", False)
            has_type_return = "type_return" in values
            type_return_value = values.get("type_return", False)
            

            # 判断操作是否成功
            operation_success = (result_value and finish_value)

            if target_pose_return_value!=None and type_return_value!=None and operation_success:
                return True, target_pose_return_value, type_return_value
            elif target_pose_return_value!=None and type_return_value!=None and not operation_success:
                return False, None, None
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
            error_msg = f"读取超时（{maxtime}秒）"
            print(f"✗ {self.robot_name} {error_msg}")
            get_error_logger().error(self.robot_name, error_msg)
            self.connected = False  # 标记为断开
            return False
        except websockets.exceptions.ConnectionClosed as e:
            error_msg = f"WebSocket连接已关闭: {e}"
            print(f"✗ {self.robot_name} {error_msg}")
            get_error_logger().error(self.robot_name, error_msg)
            self.connected = False  # 标记为断开
            return False
        except Exception as e:
            error_msg = f"异步通信错误: {type(e).__name__}: {str(e)}"
            print(f"✗ {self.robot_name} {error_msg}")
            get_error_logger().exception_occurred(self.robot_name, "异步通信", e)
            self.connected = False  # 标记为断开
            return False
    
    def subscribe_topic(self, topic_name, msg_type="std_msgs/String", throttle_rate=0, queue_length=1):
        """
        订阅ROS topic
        
        参数:
            topic_name: topic名称，如 "/navigation_status"
            msg_type: 消息类型，如 "std_msgs/String" 或 "NavigationStatus"
            throttle_rate: 节流速率（毫秒），0表示不节流
            queue_length: 队列长度
        
        返回:
            bool: 订阅是否成功
        """
        if not self.connected:
            print(f"✗ {self.robot_name} 未连接，无法订阅topic")
            return False
        
        try:
            # 构建订阅请求
            subscribe_request = {
                "op": "subscribe",
                "topic": topic_name,
                "type": msg_type,
                "throttle_rate": throttle_rate,
                "queue_length": queue_length
            }
            
            request_str = json.dumps(subscribe_request)
            print(f"{self.robot_name} 订阅topic: {topic_name}")
            print(f"订阅请求: {request_str}")
            
            # 发送订阅请求
            future = asyncio.run_coroutine_threadsafe(
                self.websocket.send(request_str),
                self.loop
            )
            future.result(5)  # 5秒超时
            
            # 记录订阅
            self.subscribed_topics[topic_name] = msg_type
            self.topic_messages[topic_name] = None
            
            # 启动统一消息接收循环（如果尚未启动）
            if not hasattr(self, '_topic_listener_started') or not self._topic_listener_started:
                self._topic_listener_started = True
                print(f"[DEBUG] {self.robot_name} 启动统一消息监听器")
                asyncio.run_coroutine_threadsafe(
                    self._unified_message_listener(),
                    self.loop
                )
            else:
                print(f"[DEBUG] {self.robot_name} 消息监听器已在运行")
            
            print(f"✓ {self.robot_name} 成功订阅topic: {topic_name}")
            return True
            
        except Exception as e:
            print(f"✗ {self.robot_name} 订阅topic失败: {str(e)}")
            get_error_logger().exception_occurred(self.robot_name, f"订阅topic {topic_name}", e)
            return False
    
    def unsubscribe_topic(self, topic_name):
        """
        取消订阅ROS topic
        
        参数:
            topic_name: topic名称
        
        返回:
            bool: 取消订阅是否成功
        """
        if not self.connected:
            print(f"✗ {self.robot_name} 未连接")
            return False
        
        try:
            # 构建取消订阅请求
            unsubscribe_request = {
                "op": "unsubscribe",
                "topic": topic_name
            }
            
            request_str = json.dumps(unsubscribe_request)
            print(f"{self.robot_name} 取消订阅topic: {topic_name}")
            
            # 发送取消订阅请求
            future = asyncio.run_coroutine_threadsafe(
                self.websocket.send(request_str),
                self.loop
            )
            future.result(5)  # 5秒超时
            
            # 从记录中移除
            if topic_name in self.subscribed_topics:
                del self.subscribed_topics[topic_name]
            if topic_name in self.topic_messages:
                del self.topic_messages[topic_name]
            
            print(f"✓ {self.robot_name} 成功取消订阅topic: {topic_name}")
            return True
            
        except Exception as e:
            print(f"✗ {self.robot_name} 取消订阅topic失败: {str(e)}")
            get_error_logger().exception_occurred(self.robot_name, f"取消订阅topic {topic_name}", e)
            return False
    
    async def _unified_message_listener(self):
        """
        统一消息监听器（异步）
        处理所有websocket消息：topic消息和服务响应
        """
        print(f"[DEBUG] {self.robot_name} 启动统一消息监听器")
        
        try:
            while self.connected and self.websocket:
                try:
                    # 接收消息
                    message_str = await asyncio.wait_for(self.websocket.recv(), timeout=1.0)
                    message = json.loads(message_str)
                    
                    # 检查消息类型
                    op = message.get("op")
                    
                    if op == "publish":
                        # Topic消息
                        topic_name = message.get("topic")
                        msg_data = message.get("msg")
                        
                        if topic_name in self.subscribed_topics:
                            # 存储最新消息
                            self.topic_messages[topic_name] = msg_data
                    
                    else:
                        # 服务响应或其他消息
                        if self.service_response_future and not self.service_response_future.done():
                            # 将响应传递给等待的协程
                            self.service_response_future.set_result(message_str)
                    
                except asyncio.TimeoutError:
                    # 超时是正常的，继续循环
                    continue
                except Exception as e:
                    print(f"[DEBUG] {self.robot_name} 消息监听器错误: {e}")
                    # 如果有等待的future，设置异常
                    if self.service_response_future and not self.service_response_future.done():
                        self.service_response_future.set_exception(e)
                    break
                    
        except Exception as e:
            print(f"✗ {self.robot_name} 消息监听器异常退出: {e}")
        finally:
            print(f"[DEBUG] {self.robot_name} 统一消息监听器已停止")
            self._topic_listener_started = False
    
    def get_topic_message(self, topic_name):
        """
        获取topic的最新消息
        
        参数:
            topic_name: topic名称
        
        返回:
            dict: 最新的消息数据，如果没有则返回None
        """
        msg = self.topic_messages.get(topic_name)
        if msg is None:
            # 检查是否已订阅
            if topic_name not in self.subscribed_topics:
                print(f"[DEBUG] {self.robot_name} topic {topic_name} 未订阅")
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
                print(f"[DEBUG] {self.robot_name} topic {topic_name} 已订阅但没有收到消息")
        return msg
    
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
