# 1. 基础镜像
FROM nvidia/cuda:12.1.1-devel-ubuntu20.04

# 2. 设置环境变量，设置非交互模式（避免apt安装时弹出交互窗口）
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai

# 3. RUN-Step 1: 安装基础系统和Python工具
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    wget \
    nano \
    python3-pip \
    python3-venv \
    python3-rosdep \
    python3-rosinstall \
    python3-rosinstall-generator \
    python3-wstool \
    && rm -rf /var/lib/apt/lists/*

# 4. RUN-Step 2: 安装catkin_tools
RUN python3 -m pip install --no-cache-dir catkin_tools

# 5. RUN-Step 3: 设置ROS软件源
RUN curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg
RUN echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros/ubuntu $(lsb_release -sc) main" | tee /etc/apt/sources.list.d/ros.list > /dev/null

# 6. RUN-Step 4: 初始化rosdep
RUN rm -f /etc/ros/rosdep/sources.list.d/20-default.list
RUN rosdep init && rosdep update

# 7. RUN-Step 5: 强制安装冲突的包
RUN apt-get update && \
    apt-get download python3-catkin-pkg-modules python3-rospkg-modules python3-rosdistro-modules && \
    dpkg -i --force-overwrite ./*.deb && \
    rm ./*.deb && \
    apt-get -f install -y

# 8. RUN-Step 6: 安装ROS Desktop
RUN apt-get install -y ros-noetic-desktop

# 9. 下载并安装Anaconda# 将Anaconda安装到/opt/conda目录下，并将其添加到系统PATH中
ENV PATH="/opt/conda/bin:$PATH"
RUN wget --quiet https://repo.anaconda.com/archive/Anaconda3-2023.09-0-Linux-x86_64.sh -O ~/anaconda.sh && \
    /bin/bash ~/anaconda.sh -b -p /opt/conda && \
    rm ~/anaconda.sh && \
    conda clean -afy

# 10. 初始化conda，让bash学会conda activate
RUN conda init bash

# 11. 创建专用的Conda环境# 我们创建一个名为ml_env，使用Python 3.10的环境
RUN conda create -n ml_env python=3.8 -y

# 12. 在Conda环境中安装Python库
COPY requirements.txt .
# 使用 conda run 在ml_env环境中执行pip安装
RUN conda run -n ml_env pip install --no-cache-dir -r requirements.txt

# 13. RUN-Step 10: 在Conda环境中单独安装与CUDA/PyTorch版本匹配的mmcv
RUN conda run -n ml_env pip install mmcv==2.2.0 -f https://download.openmmlab.com/mmcv/dist/cu121/torch2.4/index.html

# 14. 设置最终环境# 将ROS的环境和Conda环境的自动激活都添加到bash启动脚本中
RUN echo "source /opt/ros/noetic/setup.bash" >> ~/.bashrc && \
    echo "conda activate ml_env" >> ~/.bashrc
WORKDIR /workspace

# 15. 下载初始功能包，Arial.ttf, yolo11n.pt, olo11s-seg.pt, resnet50-19c8e357.pth, resnet18-5c106cde.pth, spynet_20210409-c6c1bd09.pth
RUN mkdir -p /tmp/Ultralytics
RUN mkdir -p /root/.cache/torch/hub/checkpoints
RUN wget -O /tmp/Ultralytics/Arial.ttf https://ultralytics.com/assets/Arial.ttf
RUN wget -O /root/.cache/torch/hub/checkpoints/resnet50-19c8e357.pth https://download.pytorch.org/models/resnet50-19c8e357.pth
RUN wget -O /root/.cache/torch/hub/checkpoints/resnet18-5c106cde.pth https://download.pytorch.org/models/resnet18-5c106cde.pth
RUN wget -O /root/.cache/torch/hub/checkpoints/spynet_20210409-c6c1bd09.pth https://download.openmmlab.com/mmediting/restorers/basicvsr/spynet_20210409-c6c1bd09.pth

# 16. 默认命令
CMD ["/bin/bash"]
