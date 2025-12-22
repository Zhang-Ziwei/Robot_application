# 使用官方的Miniconda3镜像作为基础镜像
FROM continuumio/miniconda3:latest

# 设置工作目录
WORKDIR /app

# 复制环境配置文件（如果有）
COPY environment.yml .
COPY requirement.txt* .

# 创建并激活conda环境
RUN conda env create -f environment.yml && conda clean -afy

# 确保conda环境在容器启动时被激活
SHELL ["conda", "run", "-n", "robot_connect", "/bin/bash", "-c"]

# 如果使用pip安装额外依赖
RUN if [ -f "requirement.txt" ]; then pip install --no-cache-dir -r requirement.txt; fi

# 复制项目源代码
COPY . .


