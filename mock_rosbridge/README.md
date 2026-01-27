# Mock ROS Bridge Server

模拟 ROS Bridge WebSocket 服务器，用于测试控制系统与机器人的通信。

## 文件结构

```
mock_rosbridge/
├── mock_rosbridge_server.py  # 主程序
├── Dockerfile_arm            # ARM架构Docker镜像
├── requirements.txt          # Python依赖
├── run.sh                    # 本地启动脚本
└── README.md                 # 使用说明
```

---

## 方式一：Docker部署（推荐）

### 1. 构建镜像

```bash
# 在mock_rosbridge目录下执行
docker build -f Dockerfile_arm -t mock-rosbridge:arm .
```

docker load -i /home/ubuntu/robot_image.tar
### 2. 运行容器

```bash
# 基础运行
docker run -d -p 9091:9091 --name rosbridge mock-rosbridge:arm

# 带日志输出运行
docker run -it -p 9091:9091 --name rosbridge mock-rosbridge:arm

# 后台运行并自动重启
docker run -d -p 9091:9091 --restart=always --name rosbridge mock-rosbridge:arm
```

### 3. 管理容器

```bash
# 查看日志
docker logs -f rosbridge

# 停止容器
docker stop rosbridge

# 启动容器
docker start rosbridge

# 删除容器
docker rm -f rosbridge
```

### 4. 修改端口

如需使用其他端口：

```bash
# 映射到主机的9092端口
docker run -d -p 9092:9091 --name rosbridge mock-rosbridge:arm
```

---

## 方式二：直接运行

### 1. 安装依赖

```bash
pip3 install websockets
```

### 2. 运行程序

```bash
python3 mock_rosbridge_server.py
```

或使用启动脚本：

```bash
./run.sh
```

---

## 配置说明

### 修改监听端口

编辑 `mock_rosbridge_server.py` 文件开头：

```python
HOST = "0.0.0.0"  # 监听地址
PORT = 9091       # 监听端口
```

---

## 支持的功能

| 操作类型 | 说明 |
|---------|------|
| `call_service` | 服务调用，返回模拟响应 |
| `subscribe` | 订阅topic |
| `unsubscribe` | 取消订阅topic |

### 模拟的服务响应

| Action | 响应 |
|--------|------|
| `cv_detect` | 75%概率检测到瓶子，返回类型和位置 |
| `navigation_to_pose` | 启动导航状态模拟 |
| `grab_object` | 返回成功 |
| `put_object` | 返回成功 |
| `scan` | 返回成功 |
| `press_button` | 返回成功 |
| 其他 | 返回成功 |

### 模拟的Topic

| Topic | 说明 |
|-------|------|
| `/navigation_status` | 导航状态，2Hz发布频率 |

---

## 验证连接

控制系统连接配置：

```python
# main.py 中的机器人配置
ROBOT_CONFIGS = {
    "robot_a": {
        "host": "<ARM设备IP>",  # 运行Docker的设备IP
        "port": "9091",
        ...
    }
}
```

---

## 常见问题

### Q: 端口被占用

```bash
# 查看占用端口的进程
sudo lsof -i :9091

# 或使用其他端口
docker run -d -p 9092:9091 --name rosbridge mock-rosbridge:arm
```

### Q: 容器无法启动

```bash
# 查看错误日志
docker logs rosbridge

# 检查镜像是否构建成功
docker images | grep mock-rosbridge
```

### Q: 控制系统无法连接

1. 确认Docker容器正在运行：`docker ps`
2. 确认端口映射正确：`docker port rosbridge`
3. 确认防火墙允许端口：`sudo ufw allow 9091`
4. 测试连接：`telnet <IP> 9091`

---

## 输出示例

```
==================================================
Mock ROS Bridge Server
==================================================
监听: ws://0.0.0.0:9091
支持的操作:
  - call_service: 服务调用
  - subscribe: 订阅topic
  - unsubscribe: 取消订阅
==================================================
等待连接...

[连接] 客户端已连接: ('192.168.1.100', 54321)
[收到] op=call_service
  数据: {"op": "call_service", "service": "/get_strawberry_service", ...}
[服务] /get_strawberry_service -> navigation_to_pose
[响应] {"op": "service_response", "result": true, ...}
```

